"""The MCP tools beyond SQL browsing, by tier: read (default), --write, --destructive."""
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from mcpkit import Client, Fixture

from omatableplus import session as session_module
from omatableplus.mcpschema import CELL_LIMIT, MAX_ROWS

READ_TOOLS = {"redis_scan", "redis_get", "redis_keyspace", "mongo_find", "mongo_count", "mongo_indexes",
              "mongo_aggregate"}
WRITE_TOOLS = {"commit_changes"}
DESTRUCTIVE_TOOLS = set()
NEVER_AN_ARGUMENT = {"confirmed", "password", "tag", "readonly", "overwrite", "path", "id"}


class FakeAdapter:
    """Stands in for a driver: remembers what it was asked, answers with the shapes the real adapters give."""
    calls = []

    def __init__(self, conn):
        self.conn = conn
        self.database = conn.get("database") or "0"

    def record(self, name, *args):
        FakeAdapter.calls.append((name, *args))

    def databases(self):
        return ["0", "1"]

    def version(self):
        return "fake 1"

    def use(self, database):
        self.record("use", database)
        self.database = str(database)

    def close(self):
        pass

    def cancel(self):
        pass


class FakeRedis(FakeAdapter):
    engine = "redis"

    def scan(self, pattern="*", cursor="0", count=500, type=None):  # noqa: A002
        self.record("scan", pattern, cursor, count, type)
        return {"keys": [{"key": "a", "type": "string", "ttl": -1}], "cursor": "0", "total": 1}

    def get(self, key, limit=100):
        self.record("get", key)
        return {"type": "string", "value": "v" * 5000, "ttl": -1, "size": 5000, "truncated": False}

    def keyspace(self):
        self.record("keyspace")
        return {"0": 2}


class FakeMongo(FakeAdapter):
    engine = "mongodb"

    def find(self, collection, filter="", sort="", projection="", skip=0, limit=100):  # noqa: A002
        self.record("find", collection, filter, sort, projection, skip, limit)
        return {"columns": ["_id"], "rows": [["1"]], "truncated": False}

    def count(self, collection, filter=""):  # noqa: A002
        self.record("count", collection, filter)
        return {"count": 3, "estimated": False}

    def indexes(self, collection):
        self.record("indexes", collection)
        return [{"name": "_id_", "keys": "{_id: 1}", "unique": True}]

    def aggregate(self, collection, pipeline, limit=100):
        self.record("aggregate", collection, pipeline, limit)
        return {"columns": ["n"], "rows": [[1]], "truncated": False}


FAKES = {"redis": FakeRedis, "mongodb": FakeMongo}


class Faked(Fixture):
    """A config that also holds a MongoDB connection; the Redis and MongoDB ones are served by the fakes above."""

    def setUp(self):
        super().setUp()
        FakeAdapter.calls = []
        real = session_module.connect
        patch = mock.patch.object(session_module, "connect", lambda conn: (FAKES[conn["engine"]](conn), None)
                                  if conn["engine"] in FAKES else real(conn))
        patch.start()
        self.addCleanup(patch.stop)
        self.store.save({"name": "Docs", "engine": "mongodb", "host": "localhost", "tag": "local"})

    def called(self, name):
        return [c for c in FakeAdapter.calls if c[0] == name]


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


class NosqlTest(Faked):
    def setUp(self):
        super().setUp()
        bson_free = types.ModuleType("omatableplus.mongojs")     # the real one needs the drivers' bson
        bson_free.parse = lambda text, what: [{"$match": {}}, {"$group": {"_id": "$city"}}]
        patch = mock.patch.dict(sys.modules, {"omatableplus.mongojs": bson_free})
        patch.start()
        self.addCleanup(patch.stop)

    def test_redis_tools_read_through_the_adapter(self):
        _, scan = self.client.call("redis_scan", connection="Cache", pattern="user:*", count=10, type="string")
        self.assertEqual(scan["keys"][0]["key"], "a")
        self.assertEqual(self.called("scan"), [("scan", "user:*", "0", 10, "string")])
        self.assertEqual(self.client.call("redis_keyspace", connection="Cache")[1], {"0": 2})

    def test_redis_scan_count_is_capped(self):
        self.client.call("redis_scan", connection="Cache", count=100000)
        self.assertEqual(self.called("scan")[0][3], MAX_ROWS)

    def test_a_long_redis_value_is_cut(self):
        _, payload = self.client.call("redis_get", connection="Cache", key="big")
        self.assertTrue(payload["value"].startswith("v" * CELL_LIMIT))
        self.assertLess(len(payload["value"]), CELL_LIMIT + 50)

    def test_the_database_argument_switches_the_redis_database(self):
        self.client.call("redis_keyspace", connection="Cache", database="1")
        self.assertEqual(self.called("use"), [("use", "1")])

    def test_a_call_without_database_goes_back_to_the_default_one_of_the_connection(self):
        self.client.call("redis_keyspace", connection="Cache", database="1")
        self.client.call("redis_keyspace", connection="Cache")
        self.client.call("redis_keyspace", connection="Cache")
        self.assertEqual(self.called("use"), [("use", "1"), ("use", "0")])

    def test_mongo_tools_read_through_the_adapter(self):
        self.client.call("mongo_find", connection="Docs", collection="orders", filter="{status: 'open'}",
                         sort="{_id: -1}", limit=500, database="shop")
        self.assertEqual(self.called("find"), [("find", "orders", "{status: 'open'}", "{_id: -1}", "", 0, MAX_ROWS)])
        self.assertEqual(self.client.call("mongo_count", connection="Docs", collection="orders")[1]["count"], 3)
        self.assertEqual(self.client.call("mongo_indexes", connection="Docs", collection="orders")[1]["indexes"][0]["name"], "_id_")

    def test_mongo_aggregate_runs_a_pipeline_that_reads(self):
        is_error, _ = self.client.call("mongo_aggregate", connection="Docs", collection="orders",
                                       pipeline="[{$match: {status: 'open'}}, {$group: {_id: '$city'}}]")
        self.assertFalse(is_error)
        self.assertEqual(len(self.called("aggregate")), 1)

    def test_mongo_aggregate_only_refuses_the_stages_that_write_not_the_names_that_start_like_them(self):
        for stage in ("{$project: {m: {$mergeObjects: ['$a', '$b']}}}", "{$match: {kind: '$outcome'}}",
                      "{$group: {_id: '$out_of_stock', merged: {$sum: '$merge_count'}}}"):
            is_error, payload = self.client.call("mongo_aggregate", connection="Docs", collection="orders",
                                                 pipeline=f"[{stage}]")
            self.assertFalse(is_error, (stage, payload))
        self.assertEqual(len(self.called("aggregate")), 3)

    def test_mongo_aggregate_refuses_a_pipeline_that_writes(self):
        for stage in ("{$out: 'copy'}", "{$merge: {into: 'copy'}}", "{'$out': 'copy'}", '{"$merge" : {into: "copy"}}'):
            is_error, payload = self.client.call("mongo_aggregate", connection="Docs", collection="orders",
                                                 pipeline=f"[{{$match: {{}}}}, {stage}]")
            self.assertTrue(is_error, stage)
            self.assertEqual(payload["error"]["code"], "invalid", stage)
        self.assertEqual(self.called("aggregate"), [])

    def test_the_session_is_read_only_whatever_the_mode(self):
        loud = Client(self.helper, destructive=True)
        loud.call("redis_keyspace", connection="Cache")
        loud.call("mongo_count", connection="Docs", collection="c", database="shop")
        self.assertEqual(len(self.helper.sessions.items), 2)
        self.assertTrue(all(s.conn.get("readonly") for s in self.helper.sessions.items.values()))

    def test_engines_do_not_mix(self):
        for tool, args, ref in (("redis_keyspace", {}, "Docs"), ("mongo_count", {"collection": "c"}, "Cache"),
                                ("redis_keyspace", {}, "Notes"), ("mongo_count", {"collection": "c"}, "Notes")):
            is_error, payload = self.client.call(tool, connection=ref, **args)
            self.assertTrue(is_error, (tool, ref))
            self.assertEqual(payload["error"]["code"], "unsupported", (tool, ref))

    def test_list_connections_says_which_tools_fit_each_engine(self):
        _, payload = self.client.call("list_connections")
        family = {c["name"]: c["family"] for c in payload["connections"]}
        self.assertEqual((family["Notes"], family["Cache"], family["Docs"]), ("sql", "redis", "mongodb"))
        self.assertTrue(all(c["supported"] for c in payload["connections"]))


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
