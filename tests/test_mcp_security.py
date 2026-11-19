"""The hardening of the MCP server: guards, redaction, limits and ambient credentials. No live server is needed."""
import os
import sqlite3
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omatableplus import mcp
from omatableplus.adapters.mysql import MySQL
from omatableplus.adapters.postgres import Postgres
from omatableplus.adapters.sqlite import SQLite
from omatableplus.mcp_admin import AdminTools
from omatableplus.mcpfiles import Sandbox, glance, runs_commands, shown
from omatableplus.mcpschema import DATA_NOTE, export_limits, statement_timeout
from omatableplus.mcpsql import (redact_all, redact_secrets, refuse_destructive, refuse_file_effects,
                                 refuse_server_changes, refuse_server_control, refuse_unless_read)
from omatableplus.protocol import Error
from tests.mcpkit import Fixture
from tests.test_mcp_tiers import Faked


def code(guard, sql, engine):
    try:
        guard(sql, engine)
    except Error as caught:
        return caught.code
    return None


class ProductionIsReadOnlyAtTheDatabaseTest(Fixture):
    write = True

    def flags(self):
        return {s.conn["name"]: (bool(s.conn.get("readonly")), bool(s.conn.get("db_readonly")))
                for s in self.helper.sessions.items.values()}

    def test_production_is_read_only_at_the_database_in_the_write_tier_and_local_is_not(self):
        for ref in ("Notes", "Prod", "Frozen"):
            self.client.call("execute_sql", connection=ref, sql="SELECT 1")
        self.assertEqual(self.flags(), {"Notes": (False, False), "Prod": (False, True), "Frozen": (True, False)})

    def test_a_production_write_is_still_answered_with_confirm_and_the_file_is_not_changed(self):
        is_error, payload = self.client.call("execute_sql", connection="Prod", sql="INSERT INTO notes (body) VALUES ('x')")
        self.assertEqual((is_error, payload["error"]["code"]), (True, "confirm"))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)

    def test_the_session_reaches_the_adapter_as_read_only(self):
        self.client.call("execute_sql", connection="Prod", sql="SELECT 1")
        session = next(s for s in self.helper.sessions.items.values() if s.conn["name"] == "Prod")
        with self.assertRaises(sqlite3.OperationalError):
            session.adapter.db.execute("INSERT INTO notes (body) VALUES ('y')")

    def test_a_session_gets_the_statement_timeout_of_the_server(self):
        self.client.server.timeout = 7
        self.client.call("execute_sql", connection="Notes", sql="SELECT 1")
        session = next(iter(self.helper.sessions.items.values()))
        self.assertEqual(session.conn["statement_timeout"], 7)

    def test_no_timeout_means_none_in_the_session(self):
        self.client.server.timeout = 0
        self.client.call("execute_sql", connection="Notes", sql="SELECT 1")
        self.assertNotIn("statement_timeout", next(iter(self.helper.sessions.items.values())).conn)


class AdapterOptionsTest(unittest.TestCase):
    def test_postgres_options(self):
        self.assertIsNone(Postgres({"engine": "postgresql"}).options())
        self.assertEqual(Postgres({"engine": "postgresql", "readonly": True}).options(),
                         "-c default_transaction_read_only=on")
        self.assertEqual(Postgres({"engine": "postgresql", "db_readonly": True, "statement_timeout": 60}).options(),
                         "-c default_transaction_read_only=on -c statement_timeout=60000")

    def test_a_connection_made_over_mcp_takes_no_ambient_password(self):
        plain = Postgres({"engine": "postgresql", "host": "h", "user": "u"}).params("db")
        self.assertIsNone(plain["password"])
        self.assertNotIn("passfile", plain)
        made = Postgres({"engine": "postgresql", "host": "h", "user": "u", "mcp_created": True}).params("db")
        self.assertEqual((made["password"], made["passfile"]), ("", os.devnull))

    def test_mysql_limits_try_mysql_then_mariadb_and_are_empty_without_a_timeout(self):
        self.assertEqual(MySQL({"engine": "mysql"}).limit_statements(), [])
        self.assertEqual(MySQL({"engine": "mysql", "statement_timeout": 3}).limit_statements(),
                         ["SET SESSION max_execution_time = 3000", "SET SESSION max_statement_time = 3"])

    def test_mysql_connection_made_over_mcp_reads_no_option_file(self):
        params = MySQL({"engine": "mysql", "host": "h", "user": "u", "mcp_created": True}).params()
        self.assertIsNone(params["read_default_file"])
        self.assertEqual(params["password"], "")

    def test_the_sqlite_deadline_stops_a_long_statement_and_only_that_one(self):
        db = sqlite3.connect(":memory:")
        db.set_progress_handler(SQLite.deadline(0.2), 1000)
        with self.assertRaises(sqlite3.OperationalError):
            db.execute("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT count(*) FROM n").fetchall()
        time.sleep(1.1)     # a pause, then a short statement runs normally
        self.assertEqual(db.execute("SELECT 1").fetchone(), (1,))
        db.close()


class LimitsFromTheEnvironmentTest(unittest.TestCase):
    def test_timeout_default_clamp_off_and_junk(self):
        key = "OMATABLEPLUS_MCP_TIMEOUT"
        self.assertEqual(statement_timeout({}), 60)
        self.assertEqual(statement_timeout({key: "5"}), 5)
        self.assertEqual(statement_timeout({key: "0"}), 0)
        self.assertEqual(statement_timeout({key: "99999"}), 3600)
        self.assertEqual(statement_timeout({key: "-4"}), 1)
        self.assertEqual(statement_timeout({key: "soon"}), 60)

    def test_export_limits(self):
        self.assertEqual(export_limits({}), (1_000_000, 512 << 20))
        self.assertEqual(export_limits({"OMATABLEPLUS_MCP_EXPORT_ROWS": "10", "OMATABLEPLUS_MCP_EXPORT_MB": "1"}),
                         (10, 1 << 20))
        self.assertEqual(export_limits({"OMATABLEPLUS_MCP_EXPORT_ROWS": "0", "OMATABLEPLUS_MCP_EXPORT_MB": "0"}), (0, 0))


class ExportCapTest(Fixture):
    write = True

    def test_an_export_over_the_row_cap_is_stopped_and_leaves_no_file(self):
        self.client.server.export_rows = 100
        is_error, payload = self.client.call("export_data", connection="Notes", schema="main", table="big",
                                             format="csv", file="big.csv")
        self.assertEqual((is_error, payload["error"]["code"]), (True, "limit"))
        self.assertEqual(os.listdir(self.files), [])

    def test_an_export_over_the_byte_cap_is_stopped(self):
        self.client.server.export_rows, self.client.server.export_bytes = 0, 100
        is_error, payload = self.client.call("export_data", connection="Notes", schema="main", table="big",
                                             format="csv", file="big.csv")
        self.assertEqual((is_error, payload["error"]["code"]), (True, "limit"))
        self.assertEqual(os.listdir(self.files), [])

    def test_an_export_under_the_caps_works(self):
        self.client.server.export_rows = 1000
        self.assertFalse(self.client.call("export_data", connection="Notes", schema="main", table="notes",
                                          format="csv", file="n.csv")[0])


class ServerChangesTest(unittest.TestCase):
    REFUSED = (
        ("postgresql", "CREATE USER eve"), ("postgresql", "create role eve login superuser"),
        ("postgresql", "ALTER ROLE eve WITH PASSWORD 'x'"), ("postgresql", "ALTER SYSTEM SET fsync = off"),
        ("postgresql", "ALTER DEFAULT PRIVILEGES IN SCHEMA s GRANT ALL ON TABLES TO eve"),
        ("postgresql", "CREATE OR REPLACE FUNCTION f() RETURNS void AS $$ BEGIN END $$ LANGUAGE plpgsql"),
        ("postgresql", "CREATE PROCEDURE p() LANGUAGE sql AS 'DROP TABLE t'"),
        ("postgresql", "CREATE CONSTRAINT TRIGGER t AFTER INSERT ON x FOR EACH ROW EXECUTE FUNCTION f()"),
        ("postgresql", "CREATE EVENT TRIGGER t ON ddl_command_start EXECUTE FUNCTION f()"),
        ("postgresql", "CREATE EXTENSION dblink"), ("postgresql", "CREATE TRUSTED PROCEDURAL LANGUAGE x"),
        ("postgresql", "SET ROLE postgres"), ("postgresql", "SET SESSION AUTHORIZATION postgres"),
        ("postgresql", "SET LOCAL ROLE postgres"), ("postgresql", "SELECT 1; set role admin"),
        ("mysql", "CREATE USER 'a'@'%' IDENTIFIED BY 'x'"), ("mysql", "RENAME USER a TO b"),
        ("mysql", "CREATE DEFINER = root@localhost FUNCTION f() RETURNS int RETURN 1"),
        ("mysql", "CREATE DEFINER=`root`@`%` TRIGGER t BEFORE INSERT ON x FOR EACH ROW SET NEW.a = 1"),
        ("mysql", "CREATE EVENT e ON SCHEDULE EVERY 1 DAY DO DROP TABLE t"), ("mysql", "ALTER EVENT e DISABLE"),
        ("mysql", "SET PASSWORD FOR 'a'@'h' = 'x'"), ("mysql", "SET GLOBAL max_connections = 1"),
        ("mysql", "set persist sql_mode = ''"), ("mysql", "SET PERSIST_ONLY x = 1"),
        ("mysql", "SET @@global.sql_mode = ''"), ("mysql", "SET DEFAULT ROLE ALL TO a"),
        ("sqlite", "CREATE TRIGGER t AFTER INSERT ON a BEGIN DELETE FROM b; END"),
    )
    FINE = (
        ("postgresql", 'SELECT "role", "user" FROM t'), ("postgresql", "SELECT 'create user eve'"),
        ("postgresql", "SELECT 1 -- create role x\n"), ("postgresql", "/* ALTER SYSTEM */ SELECT 1"),
        ("postgresql", "CREATE TABLE t (role text, function int, trigger int, event text, language text)"),
        ("postgresql", "CREATE TABLE role (id int)"), ("postgresql", "CREATE UNIQUE INDEX i ON t (role)"),
        ("postgresql", "ALTER TABLE t ADD COLUMN role text"), ("postgresql", "ALTER TABLE t ADD CONSTRAINT c UNIQUE (a)"),
        ("postgresql", "INSERT INTO t (role) VALUES ('set role x')"), ("postgresql", "UPDATE t SET role = 'x'"),
        ("postgresql", "SET search_path = role"), ("postgresql", "SET statement_timeout = 5"),
        ("postgresql", "CREATE TEMP TABLE x AS SELECT 1"), ("postgresql", "CREATE VIEW v AS SELECT role FROM t"),
        ("mysql", "SET @x = 'global'"), ("mysql", "SET SESSION sql_mode = ''"), ("mysql", "SET NAMES utf8mb4"),
        ("mysql", "CREATE DEFINER = root@localhost VIEW v AS SELECT 1"), ("mysql", "UPDATE t SET `global` = 1"),
        ("mysql", "SELECT `password`, 'SET PASSWORD' FROM t"), ("sqlite", "CREATE INDEX i ON t (event)"),
    )

    def test_each_is_refused(self):
        for engine, sql in self.REFUSED:
            self.assertEqual(code(refuse_server_changes, sql, engine), "destructive", sql)
            self.assertEqual(code(refuse_destructive, sql, engine), "destructive", sql)

    def test_lookalikes_in_strings_comments_and_names_pass(self):
        for engine, sql in self.FINE:
            self.assertIsNone(code(refuse_server_changes, sql, engine), sql)
            self.assertIsNone(code(refuse_destructive, sql, engine), sql)


class ServerControlTest(unittest.TestCase):
    CALLS = ("SELECT pg_terminate_backend(123)", "select PG_CANCEL_BACKEND(1)", "SELECT pg_reload_conf()",
             "SELECT pg_rotate_logfile()", "SELECT pg_switch_wal()", "SELECT pg_create_restore_point('x')",
             "SELECT pg_promote()", "SELECT pg_wal_replay_pause()", "SELECT pg_drop_replication_slot('s')",
             "SELECT pg_advisory_lock(1)", "SELECT pg_try_advisory_lock(1)", "SELECT pg_advisory_xact_lock_shared(1)",
             'SELECT "pg_terminate_backend" /* x */ (1)', "SELECT pg_advisory_unlock_all()")

    def test_calls_are_refused(self):
        for sql in self.CALLS:
            self.assertEqual(code(refuse_server_control, sql, "postgresql"), "destructive", sql)
            self.assertEqual(code(refuse_unless_read, sql, "postgresql"), "destructive", sql)
        self.assertEqual(code(refuse_server_control, "SELECT GET_LOCK('a', 5)", "mysql"), "destructive")
        self.assertEqual(code(refuse_server_control, "SELECT release_all_locks()", "mariadb"), "destructive")

    def test_names_without_a_call_and_strings_pass(self):
        for sql in ("SELECT pg_terminate_backend FROM t", "SELECT 'pg_reload_conf()'", "SELECT pid FROM pg_stat_activity",
                    "SELECT /* pg_promote() */ 1", "SELECT pg_advisory_lock FROM t"):
            self.assertIsNone(code(refuse_server_control, sql, "postgresql"), sql)

    def test_more_file_functions_are_refused_with_the_files_code(self):
        for sql in ("SELECT pg_ls_logdir()", "SELECT * FROM pg_ls_waldir()", "SELECT pg_ls_tmpdir()",
                    "SELECT pg_ls_archive_status_dir()".replace("status_dir", "statusdir"),
                    "SELECT pg_ls_logicalmapdir()", "SELECT pg_ls_logicalsnapdir()", "SELECT pg_ls_replslotdir('s')",
                    "SELECT pg_current_logfile()", "SELECT pg_read_file('/etc/passwd')"):
            self.assertEqual(code(refuse_file_effects, sql, "postgresql"), "files", sql)


class ToolPlumbingTest(Fixture):
    def test_the_read_tier_refuses_account_and_setting_changes_with_the_destructive_code(self):
        for sql in ("CREATE USER x", "ALTER SYSTEM SET a = 1", "SET ROLE x"):
            is_error, payload = self.client.call("execute_sql", connection="Notes", sql=sql)
            self.assertEqual((is_error, payload["error"]["code"]), (True, "destructive"), sql)


class WriteTierPlumbingTest(Fixture):
    write = True

    def test_execute_sql_refuses_create_trigger_in_the_write_tier(self):
        is_error, payload = self.client.call("execute_sql", connection="Notes",
                                             sql="CREATE TRIGGER t AFTER INSERT ON notes BEGIN DELETE FROM big; END")
        self.assertEqual((is_error, payload["error"]["code"]), (True, "destructive"))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'"), 0)


class DestructiveTierTest(Fixture):
    write = destructive = True

    def test_the_destructive_tier_allows_what_the_lower_ones_refuse(self):
        self.assertFalse(self.client.call("execute_sql", connection="Notes",
                                          sql="CREATE TRIGGER t AFTER INSERT ON notes BEGIN SELECT 1; END")[0])


class ServerControlToolTest(Faked):
    def test_execute_and_explain_refuse_a_server_call_below_destructive(self):
        for tool in ("execute_sql", "explain_sql"):
            is_error, payload = self.client.call(tool, connection="Server", sql="SELECT GET_LOCK('a', 1)")
            self.assertEqual((is_error, payload["error"]["code"]), (True, "destructive"), tool)
        self.assertEqual(self.called("run"), [])


class RedactionTest(unittest.TestCase):
    CASES = (
        ("CREATE USER bob PASSWORD 'x1'", "CREATE USER bob PASSWORD '***'"),
        ("create role a with encrypted password 'p''q'", "create role a with encrypted password '***'"),
        ("ALTER USER a PASSWORD = \"zz\"", "ALTER USER a PASSWORD = '***'"),
        ("ALTER ROLE a PASSWORD E'a\\'b'", "ALTER ROLE a PASSWORD '***'"),
        ("ALTER ROLE a PASSWORD $t$se'c$t$ VALID UNTIL 'x'", "ALTER ROLE a PASSWORD '***' VALID UNTIL 'x'"),
        ("CREATE USER 'a'@'h' IDENTIFIED BY 's3'", "CREATE USER 'a'@'h' IDENTIFIED BY '***'"),
        ("CREATE USER a IDENTIFIED WITH mysql_native_password BY 's3'",
         "CREATE USER a IDENTIFIED WITH mysql_native_password BY '***'"),
        ("ALTER USER a IDENTIFIED WITH caching_sha2_password AS 'hash'",
         "ALTER USER a IDENTIFIED WITH caching_sha2_password AS '***'"),
        ("SET PASSWORD FOR 'a'@'h' = 'x'", "SET PASSWORD FOR 'a'@'h' = '***'"),
        ("SET PASSWORD = PASSWORD('x')", "SET PASSWORD = PASSWORD('***')"),
        ("SELECT PASSWORD(\"x\")", "SELECT PASSWORD('***')"),
        ("CHANGE MASTER TO MASTER_PASSWORD='mm'", "CHANGE MASTER TO MASTER_PASSWORD='***'"),
    )

    def test_each_password_literal_is_replaced(self):
        for raw, clean in self.CASES:
            self.assertEqual(redact_secrets(raw), clean, raw)

    def test_text_without_a_password_is_unchanged(self):
        for sql in ("SELECT * FROM users WHERE name = 'password'", "SELECT 1", "UPDATE t SET a = 'x'"):
            self.assertEqual(redact_secrets(sql), sql)

    def test_a_payload_of_any_shape_is_redacted_and_non_text_kept(self):
        out = redact_all({"processes": [{"id": 7, "query": "CREATE USER a IDENTIFIED BY 'pw'", "n": None}]})
        self.assertEqual(out, {"processes": [{"id": 7, "query": "CREATE USER a IDENTIFIED BY '***'", "n": None}]})


class RedactionInToolsTest(Fixture):
    write = True

    def test_recent_queries_and_saved_queries_hide_passwords(self):
        sql = "SELECT 1 AS \"PASSWORD 'hunter2'\""
        self.helper.methods["query.save"](name="mine", sql="ALTER USER a IDENTIFIED BY 'hunter2'")
        self.client.call("execute_sql", connection="Notes", sql=sql)
        text = self.client.call("recent_queries", connection="Notes")[1]
        self.assertNotIn("hunter2", str(text))
        self.assertNotIn("hunter2", str(self.client.call("list_saved_queries")[1]))
        self.assertIn("'***'", str(self.client.call("list_saved_queries")[1]))


class ProcessesRedactedTest(Faked):
    def test_list_processes_hides_passwords(self):
        from tests.test_mcp_tiers import FakeServer
        original = FakeServer.processes
        FakeServer.processes = lambda self: [{"id": 7, "query": "CREATE USER a IDENTIFIED BY 'hunter2'"}]
        self.addCleanup(setattr, FakeServer, "processes", original)
        payload = self.client.call("list_processes", connection="Server")[1]
        self.assertNotIn("hunter2", str(payload))
        self.assertIn("'***'", str(payload))


class RiskyUserChangeTest(unittest.TestCase):
    def refused(self, engine, op, spec):
        try:
            AdminTools.refuse_risky_user_change(engine, op, spec)
        except Error as caught:
            self.assertIn("OmaTablePlus window", caught.message)
            return True
        return False

    def test_refused(self):
        for engine, op, spec in (
                ("postgresql", "create", {"name": "x", "superuser": True}),
                ("postgresql", "alter", {"name": "x", "superuser": True}),
                ("postgresql", "alter", {"name": "x", "createrole": True}),
                ("postgresql", "create", {"name": "x", "createrole": True, "login": True}),
                ("postgresql", "create", {"name": "x"}),
                ("mysql", "grant", {"name": "x", "scope": "global", "privileges": ["SELECT"]}),
                ("mysql", "grant", {"name": "x", "scope": "database", "database": "d", "privileges": ["GRANT OPTION"]}),
                ("mysql", "grant", {"name": "x", "scope": "database", "database": "d", "privileges": ["ALL PRIVILEGES"],
                                    "grant_option": True}),
                ("postgresql", "grant", {"name": "x", "scope": "table", "admin_option": True}),
                ("mysql", "create", {"name": "x"}),
                ("mysql", "create", {"name": "x", "host": "%"}),
                ("mariadb", "create", {"name": "x", "host": "10.0.0.5"})):
            self.assertTrue(self.refused(engine, op, spec), (engine, op, spec))

    def test_allowed(self):
        for engine, op, spec in (
                ("mysql", "create", {"name": "x", "host": "localhost"}),
                ("mysql", "create", {"name": "x", "host": "127.0.0.1"}),
                ("mysql", "create", {"name": "x", "host": "::1"}),
                ("mysql", "create", {"name": "x", "host": "%", "locked": True}),
                ("postgresql", "create", {"name": "x", "login": False}),
                ("postgresql", "alter", {"name": "x", "createdb": True}),
                ("postgresql", "alter", {"name": "x", "createrole": True, "login": False}),
                ("mysql", "grant", {"name": "x", "scope": "database", "database": "d", "privileges": ["SELECT"]}),
                ("mysql", "revoke", {"name": "x", "scope": "global", "privileges": ["SELECT"]}),
                ("mysql", "drop", {"name": "x"})):
            self.assertFalse(self.refused(engine, op, spec), (engine, op, spec))


class ChangeUserToolTest(Faked):
    write = destructive = True

    def test_change_user_refuses_a_superuser_and_runs_nothing(self):
        is_error, payload = self.client.call("change_user", connection="Server", op="create",
                                             spec={"name": "x", "host": "%"})
        self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"))
        self.assertEqual(self.called("admin"), [])

    def test_change_user_still_works_for_a_safe_change(self):
        self.assertFalse(self.client.call("change_user", connection="Server", op="drop", spec={"name": "x"})[0])


class CreateConnectionTest(Faked):
    write = True

    def test_port_and_user_are_required_and_the_connection_is_marked(self):
        is_error, payload = self.client.call("create_connection", name="N", engine="postgresql", host="localhost")
        self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"))
        is_error, payload = self.client.call("create_connection", name="N", engine="postgresql", host="localhost",
                                             port=5432, user="  ")
        self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"))
        is_error, payload = self.client.call("create_connection", name="N", engine="postgresql", host="localhost",
                                             port=5432, user="reader")
        self.assertFalse(is_error, payload)
        self.assertTrue(self.store.get(payload["id"], secrets=True)["mcp_created"])
        self.assertNotIn("mcp_created", self.store.get(self.local["id"], secrets=True))


class CommandLinesTest(unittest.TestCase):
    def test_commands_at_the_start_or_in_the_middle_of_a_line(self):
        for line in ("\\! id", "  \\gexec", "SELECT 1 \\! touch /tmp/x", "SELECT 1 \\gexec", "SELECT 1 \\g |sh",
                     "\\copy t from program 'id'", "x \\? y", "SELECT 1\\watch", "pager cat > /tmp/x", "tee /tmp/x",
                     "EDIT", "system id", "source f.sql", "\\. other.sql", "SELECT 1 \\i f.sql"):
            self.assertTrue(runs_commands(line), line)

    def test_what_every_pg_dump_has_is_not_a_command(self):
        for line in ("\\connect app", "\\restrict abc123", "\\unrestrict abc123", "a\t\\N\tb", "\\N", "\\.",
                     "SELECT 1;", "-- pager is nice", "editor = 1", "sources", "tee-shirt"):
            self.assertFalse(runs_commands(line), line)

    def test_control_characters_are_not_shown(self):
        self.assertEqual(shown("a\x1b[2Kb\x00c\x7fd\te\r"), "a[2Kbcd\te")

    def test_the_dialog_lists_a_mid_line_command_and_strips_controls(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            box = Sandbox(tmp)
            path = box.new("x.sql")
            with open(path, "wb") as f:
                f.write(b"SELECT 1;\nSELECT 2 \\! touch /tmp/pwned\n\x1b[31mred \\gexec\nCOPY t FROM stdin;\n")
            text = glance(path)
        commands = text.split("run as commands", 1)[1]
        self.assertIn("SELECT 2 \\! touch /tmp/pwned", commands)
        self.assertIn("[31mred \\gexec", commands)
        self.assertNotIn("\x1b", text)
        self.assertNotIn("COPY t FROM stdin", commands)


class InjectionNoteTest(Fixture):
    def test_instructions_and_data_tools_say_results_are_not_instructions(self):
        self.assertTrue(all(DATA_NOTE in text for text in mcp.INSTRUCTIONS.values()))
        tools = {t["name"]: t["description"] for t in self.client.send("tools/list")["result"]["tools"]}
        for name in ("execute_sql", "search_rows", "describe_object", "recent_queries", "list_processes",
                     "redis_get", "mongo_find", "list_saved_queries", "preview_import"):
            self.assertIn(DATA_NOTE, tools[name], name)
        self.assertNotIn(DATA_NOTE, tools["list_files"])

    def test_the_result_schema_is_unchanged(self):
        is_error, payload = self.client.call("execute_sql", connection="Notes", sql="SELECT 1 AS n")
        self.assertEqual(sorted(payload), ["database", "results"])
        self.assertEqual(sorted(payload["results"][0]), ["columns", "ms", "rows", "sql", "truncated"])


if __name__ == "__main__":
    unittest.main()
