"""The MCP tiers, read (default), --write and --destructive: what each lists and what the SQL guards refuse."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from mcpkit import Client, Fixture

READ_TOOLS = {"list_connections", "list_databases", "list_objects", "describe_object", "search_schema", "search_rows",
              "execute_sql", "explain_sql", "recent_queries", "preview_changes"}
WRITE_TOOLS = {"commit_changes"}
DESTRUCTIVE_TOOLS = set()
NEVER_AN_ARGUMENT = {"confirmed", "password", "tag", "readonly", "overwrite", "path", "id"}


class ToolListingTest(Fixture):
    def names(self, **modes):
        return set(Client(self.helper, **modes).tool_names())

    def test_read_mode_offers_every_read_tool_and_nothing_that_changes_anything(self):
        names = self.names()
        self.assertTrue(READ_TOOLS <= names, READ_TOOLS - names)
        self.assertFalse(names & (WRITE_TOOLS | DESTRUCTIVE_TOOLS), names & (WRITE_TOOLS | DESTRUCTIVE_TOOLS))

    def test_write_mode_adds_the_write_tools_but_not_the_destructive_ones(self):
        names = self.names(write=True)
        self.assertTrue((READ_TOOLS | WRITE_TOOLS) <= names)
        self.assertFalse(names & DESTRUCTIVE_TOOLS)

    def test_destructive_mode_offers_everything_and_implies_write(self):
        self.assertTrue((READ_TOOLS | WRITE_TOOLS | DESTRUCTIVE_TOOLS) <= self.names(destructive=True))

    def test_no_tool_takes_a_secret_a_guard_switch_or_a_free_path(self):
        client = Client(self.helper, destructive=True)
        for tool in client.send("tools/list")["result"]["tools"]:
            self.assertFalse(set(tool["inputSchema"]["properties"]) & NEVER_AN_ARGUMENT, tool["name"])
            self.assertIs(tool["inputSchema"]["additionalProperties"], False, tool["name"])

    def test_a_tool_of_a_higher_tier_is_unknown_not_refused(self):
        for tool in ("commit_changes",):
            self.assertEqual(self.client.send("tools/call", {"name": tool, "arguments": {}})["error"]["code"], -32602)

    def test_instructions_name_the_mode(self):
        said = {mode: Client(self.helper, **flags).send("initialize", {})["result"]["instructions"]
                for mode, flags in (("read", {}), ("write", {"write": True}), ("destructive", {"destructive": True}))}
        self.assertIn("read-only", said["read"])
        self.assertNotIn("destructive", said["write"].replace("not destructive", ""))
        self.assertIn("destructive", said["destructive"])


class SqlGuardTest(Fixture):
    write = True

    def test_write_mode_refuses_sql_that_drops_truncates_or_changes_rights(self):
        for sql in ("DROP TABLE notes", "TRUNCATE TABLE notes", "GRANT ALL ON *.* TO x", "REVOKE ALL ON *.* FROM x",
                    "SELECT 1; DROP TABLE notes", "  -- hi\n drop table notes",
                    "ALTER TABLE notes DROP COLUMN body"):
            is_error, payload = self.client.call("execute_sql", connection="Notes", sql=sql)
            self.assertTrue(is_error, sql)
            self.assertEqual(payload["error"]["code"], "destructive", sql)
            self.assertIn("--destructive", payload["error"]["hint"])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM sqlite_master WHERE name = 'notes'"), 1)

    def test_write_mode_refuses_the_ways_round_the_first_keyword(self):
        # each one drops or deletes behind a statement that does not start with DROP
        for ref, sql in (("Legacy MySQL", "PREPARE s FROM 'DROP TABLE t'"), ("Legacy MySQL", "EXECUTE s"),
                         ("Legacy MySQL", "SELECT 1; prepare s from 'DROP TABLE t'"),
                         ("Notes", "DO $$ BEGIN DROP TABLE notes; END $$"),
                         ("Notes", "PRAGMA writable_schema = 1; DELETE FROM sqlite_master WHERE name = 'notes'"),
                         ("Notes", "PRAGMA writable_schema(1)")):
            is_error, payload = self.client.call("execute_sql", connection=ref, sql=sql)
            self.assertEqual((is_error, payload["error"]["code"]), (True, "destructive"), sql)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM sqlite_master WHERE name = 'notes'"), 1)

    def test_a_refused_batch_runs_none_of_its_statements(self):
        self.client.call("execute_sql", connection="Notes", sql="DELETE FROM notes WHERE id = 1; DROP TABLE big")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)

    def test_ordinary_writes_still_run(self):
        is_error, _ = self.client.call("execute_sql", connection="Notes", sql="DELETE FROM notes WHERE id = 3")
        self.assertFalse(is_error)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 2)

class SessionFollowsTheConnectionTest(Fixture):
    write = True

    def retag(self, **changes):
        self.store.save({**self.store.get(self.local["id"], secrets=True), **changes})

    def insert(self):
        is_error, payload = self.client.call("execute_sql", connection="Notes", sql="INSERT INTO notes (body) VALUES ('x')")
        return payload["error"]["code"] if is_error else "ok"

    def test_a_session_already_open_obeys_a_connection_that_became_production_or_read_only(self):
        self.assertEqual(self.insert(), "ok")
        self.retag(tag="production")
        self.assertEqual(self.insert(), "confirm")
        self.retag(tag="local", readonly=True)
        self.assertEqual(self.insert(), "readonly")
        self.retag(tag="local", readonly=False)
        self.assertEqual(self.insert(), "ok")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 5)

    def test_a_connection_that_did_not_change_keeps_its_session(self):
        self.client.call("execute_sql", connection="Notes", sql="SELECT 1")
        first = set(self.helper.sessions.items)
        self.client.call("execute_sql", connection="Notes", sql="SELECT 2")
        self.assertEqual(set(self.helper.sessions.items), first)


class SqlFileEffectsTest(Fixture):
    """Raw SQL must not reach files outside the MCP files folder, whatever the tier or the connection."""

    def setUp(self):
        super().setUp()
        self.outside = os.path.join(self.tmp.name, "outside")
        os.makedirs(self.outside)
        self.tiers = [Client(self.helper, **flags) for flags in ({}, {"write": True}, {"destructive": True})]

    def tearDown(self):
        for client in self.tiers:
            client.server.close()
        super().tearDown()

    def refused(self, sql, code, connection="Notes", tools=("execute_sql", "explain_sql")):
        for client in self.tiers:
            for tool in tools:
                is_error, payload = client.call(tool, connection=connection, sql=sql)
                self.assertEqual((is_error, payload["error"]["code"]), (True, code), (tool, sql))

    def test_sql_cannot_create_or_open_a_file_outside_the_folder(self):
        for sql in (f"VACUUM INTO '{self.outside}/v.db'", f"vacuum main into '{self.outside}/m.db'",
                    f"ATTACH DATABASE '{self.path}' AS other", f"/* x */ ATTACH '{self.outside}/a.db' AS a",
                    f"SELECT 1; VACUUM INTO '{self.outside}/s.db'", "COPY notes TO '/tmp/x'",
                    "COPY notes FROM PROGRAM 'id'", "LOAD DATA INFILE '/etc/passwd' INTO TABLE notes"):
            self.refused(sql, "files")
        self.assertEqual(os.listdir(self.outside), [])

    def test_mysql_file_clauses_and_comments_that_run_are_refused_before_any_connection(self):
        for sql, code in (("SELECT * FROM t INTO OUTFILE '/tmp/x'", "files"), ("SELECT 1 INTO DUMPFILE '/tmp/x'", "files"),
                          ("SELECT 1 /*!50000 INTO OUTFILE '/tmp/x' */", "invalid"),
                          ("SELECT 1 /*M!100000 INTO OUTFILE '/tmp/x' */", "invalid")):
            self.refused(sql, code, connection="Legacy MySQL")

    def test_postgresql_calls_that_lift_the_read_only_session_are_refused_before_any_connection(self):
        # verified on a real server: each of these, called from a SELECT, turns default_transaction_read_only off
        # for the session, after which a function that writes (nextval, your own) runs
        self.store.save({"name": "Legacy PG", "engine": "postgresql", "host": "db.example", "tag": "local"})
        for sql in ("SELECT set_config('default_transaction_read_only', 'off', false)",
                    "SELECT pg_catalog.set_config('transaction_read_only', 'off', false)",
                    "SELECT query_to_xml('SELECT 1', true, true, '')",
                    "SELECT query_to_xml_and_xmlschema('SELECT 1', true, true, '')",
                    "SELECT query_to_xmlschema('SELECT 1', true, true, '')",
                    "SELECT cursor_to_xml('c', 1, true, true, '')",
                    "SELECT * FROM ts_stat('SELECT 1::tsvector')",
                    "SELECT dblink_exec('dbname=x', 'DELETE FROM notes')", "SELECT 1; SELECT SET_CONFIG('a', 'b', false)"):
            self.refused(sql, "invalid", connection="Legacy PG", tools=("execute_sql", "explain_sql"))

    def test_those_names_stay_harmless_elsewhere_and_as_plain_text(self):
        write = self.tiers[1]
        for sql in ("SELECT 'set_config query_to_xml ts_stat' AS words", "SELECT 1 AS set_config_note"):
            self.assertFalse(write.call("execute_sql", connection="Notes", sql=sql)[0], sql)

    def test_words_that_only_look_like_those_statements_still_run(self):
        write = self.tiers[1]
        for sql in ("SELECT 'VACUUM INTO x, ATTACH, COPY, OUTFILE' AS words", "SELECT 1 /* ATTACH */", "VACUUM"):
            self.assertFalse(write.call("execute_sql", connection="Notes", sql=sql)[0], sql)


class DestructiveToolsTest(Fixture):
    write = True
    destructive = True

    def test_execute_sql_may_drop(self):
        self.assertFalse(self.client.call("execute_sql", connection="Notes", sql="DROP TABLE big")[0])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM sqlite_master WHERE name = 'big'"), 0)


if __name__ == "__main__":
    unittest.main()
