"""The MCP tools beyond SQL browsing, by tier: read (default), --write, --destructive."""
import csv
import os
import sqlite3
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from mcpkit import Client, Fixture

from omatableplus import omadb
from omatableplus import session as session_module
from omatableplus import sqlgen
from omatableplus.mcpschema import CELL_LIMIT, MAX_ROWS

READ_TOOLS = {"list_relations", "list_saved_queries", "list_processes", "list_users", "get_user_grants",
              "preview_user_change", "preview_create_table", "preview_alter_table", "preview_import", "list_files",
              "redis_scan", "redis_get", "redis_keyspace", "mongo_find", "mongo_count", "mongo_indexes",
              "mongo_aggregate"}
WRITE_TOOLS = {"commit_changes", "create_table", "alter_table", "save_query", "import_csv", "export_data",
               "backup_database", "create_database", "create_connection", "duplicate_connection"}
DESTRUCTIVE_TOOLS = {"drop_object", "truncate_table", "drop_database", "restore_database", "kill_process",
                     "change_user", "delete_saved_query", "delete_connection"}
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


class FakeServer(FakeAdapter):
    engine = "mysql"
    d = sqlgen.DIALECTS["mysql"]

    def processes(self):
        return [{"id": 7, "user": "app", "state": "Query"}]

    def kill(self, pid, hard=False):
        self.record("kill", pid, hard)
        return {"ok": True}

    def users(self):
        return [{"name": "app", "host": "%"}]

    def user_grants(self, spec):
        self.record("grants", spec)
        return ["GRANT SELECT ON *.* TO `app`@`%`"]

    def admin_run(self, statements):
        self.record("admin", statements)

    def drop_database(self, name):
        self.record("drop_database", name)

    def create_database(self, name):
        self.record("create_database", name)

    def truncate(self, schema, table):
        self.record("truncate", schema, table)
        return {"ok": True}

    def drop(self, schema, table, kind="table"):
        self.record("drop", schema, table, kind)
        return {"ok": True}

    def drop_object(self, schema, name, kind, table=None, signature=None):
        self.record("drop_object", schema, name, kind, table, signature)
        return {"ok": True}


FAKES = {"redis": FakeRedis, "mongodb": FakeMongo, "mysql": FakeServer}


class Faked(Fixture):
    """A config that also holds a Redis, a MongoDB and two MySQL connections served by the fakes above."""

    def setUp(self):
        super().setUp()
        FakeAdapter.calls = []
        real = session_module.connect
        patch = mock.patch.object(session_module, "connect", lambda conn: (FAKES[conn["engine"]](conn), None)
                                  if conn["engine"] in FAKES else real(conn))
        patch.start()
        self.addCleanup(patch.stop)
        self.store.save({"name": "Docs", "engine": "mongodb", "host": "localhost", "tag": "local"})
        self.store.save({"name": "Server", "engine": "mysql", "host": "db.example", "tag": "local"})
        self.store.save({"name": "Server prod", "engine": "mysql", "host": "db.example", "tag": "production"})

    def called(self, name):
        return [c for c in FakeAdapter.calls if c[0] == name]


class ToolListingTest(Fixture):
    def names(self, **modes):
        return set(Client(self.helper, files=self.files, **modes).tool_names())

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
        client = Client(self.helper, destructive=True, files=self.files)
        for tool in client.send("tools/list")["result"]["tools"]:
            self.assertFalse(set(tool["inputSchema"]["properties"]) & NEVER_AN_ARGUMENT, tool["name"])
            self.assertIs(tool["inputSchema"]["additionalProperties"], False, tool["name"])

    def test_a_tool_of_a_higher_tier_is_unknown_not_refused(self):
        for tool in ("drop_object", "create_table"):
            self.assertEqual(self.client.send("tools/call", {"name": tool, "arguments": {}})["error"]["code"], -32602)

    def test_instructions_name_the_mode(self):
        said = {mode: Client(self.helper, files=self.files, **flags).send("initialize", {})["result"]["instructions"]
                for mode, flags in (("read", {}), ("write", {"write": True}), ("destructive", {"destructive": True}))}
        self.assertIn("read-only", said["read"])
        self.assertNotIn("destructive", said["write"].replace("not destructive", ""))
        self.assertIn("destructive", said["destructive"])
        self.assertIn("MCP files folder", said["write"])


class ReadToolsTest(Fixture):
    def test_list_relations_shows_foreign_keys(self):
        db = sqlite3.connect(self.path)
        db.execute("CREATE TABLE comments (id INTEGER PRIMARY KEY, note_id INTEGER REFERENCES notes(id))")
        db.commit()
        db.close()
        is_error, payload = self.client.call("list_relations", connection="Notes")
        self.assertFalse(is_error)
        self.assertIn("comments", str(payload))
        self.assertIn("note_id", str(payload))

    def test_saved_queries_are_listed_and_cut_to_a_readable_size(self):
        self.helper.methods["query.save"](name="long", sql="SELECT '" + "x" * 5000 + "'")
        _, payload = self.client.call("list_saved_queries")
        item = payload["items"][0]
        self.assertEqual(item["name"], "long")
        self.assertLess(len(item["sql"]), CELL_LIMIT + 50)

    def test_preview_create_table_shows_the_sql_and_creates_nothing(self):
        is_error, payload = self.client.call(
            "preview_create_table", connection="Notes", schema="main", table="tags",
            columns=[{"name": "id", "type": "INTEGER", "pk": True}, {"name": "label", "type": "TEXT"}])
        self.assertFalse(is_error)
        self.assertIn("CREATE TABLE", payload["statements"][0])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM sqlite_master WHERE name = 'tags'"), 0)

    def test_preview_alter_table_shows_the_sql_and_changes_nothing(self):
        is_error, payload = self.client.call(
            "preview_alter_table", connection="Notes", schema="main", table="notes",
            changes={"columns": [{"op": "add", "name": "extra", "type": "TEXT"}]})
        self.assertFalse(is_error)
        self.assertIn("ADD COLUMN", payload["statements"][0])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM pragma_table_info('notes')"), 2)

    def test_preview_import_reads_a_file_of_the_folder_only(self):
        os.makedirs(self.files)
        with open(os.path.join(self.files, "in.csv"), "w") as f:
            f.write("body\nalpha\nbeta\n")
        is_error, payload = self.client.call("preview_import", file="in.csv")
        self.assertFalse(is_error)
        self.assertEqual((payload["header"], payload["count"]), (["body"], 2))
        self.assertNotIn(self.files, str(payload))
        for name in ("../in.csv", "/etc/passwd"):
            self.assertEqual(self.client.call("preview_import", file=name)[1]["error"]["code"], "invalid", name)

    def test_list_files_shows_the_folder_content(self):
        self.assertEqual(self.client.call("list_files")[1], {"files": []})
        os.makedirs(self.files)
        with open(os.path.join(self.files, "a.csv"), "w") as f:
            f.write("x")
        self.assertEqual(self.client.call("list_files")[1], {"files": [{"name": "a.csv", "bytes": 1}]})

    def test_a_sql_engine_without_server_features_says_so(self):
        for tool, args in (("list_processes", {}), ("list_users", {})):
            is_error, payload = self.client.call(tool, connection="Notes", **args)
            self.assertTrue(is_error, tool)
            self.assertEqual(payload["error"]["code"], "unsupported", tool)


class AdminReadTest(Faked):
    def test_processes_users_and_grants(self):
        self.assertEqual(self.client.call("list_processes", connection="Server")[1]["processes"][0]["id"], 7)
        self.assertEqual(self.client.call("list_users", connection="Server")[1]["users"], [{"name": "app", "host": "%"}])
        _, payload = self.client.call("get_user_grants", connection="Server", name="app", host="%")
        self.assertEqual(payload["grants"], ["GRANT SELECT ON *.* TO `app`@`%`"])

    def test_preview_user_change_masks_the_statement_and_runs_nothing(self):
        is_error, payload = self.client.call("preview_user_change", connection="Server", op="grant",
                                             spec={"name": "app", "host": "%", "scope": "global",
                                                   "privileges": ["SELECT"]})
        self.assertFalse(is_error, payload)
        self.assertIn("GRANT SELECT", payload["statements"][0])
        self.assertEqual(self.called("admin"), [])

    def test_a_password_never_goes_through_the_model(self):
        for tool in ("preview_user_change",):
            is_error, payload = self.client.call(tool, connection="Server", op="create",
                                                 spec={"name": "x", "password": "hunter2"})
            self.assertTrue(is_error)
            self.assertEqual(payload["error"]["code"], "invalid")
            self.assertNotIn("hunter2", self.client.out.getvalue())


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

    def test_mongo_aggregate_refuses_a_pipeline_that_writes(self):
        for stage in ("{$out: 'copy'}", "{$merge: {into: 'copy'}}"):
            is_error, payload = self.client.call("mongo_aggregate", connection="Docs", collection="orders",
                                                 pipeline=f"[{{$match: {{}}}}, {stage}]")
            self.assertTrue(is_error, stage)
            self.assertEqual(payload["error"]["code"], "invalid", stage)
        self.assertEqual(self.called("aggregate"), [])

    def test_the_session_is_read_only_whatever_the_mode(self):
        loud = Client(self.helper, destructive=True, files=self.files)
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

    def test_a_refused_batch_runs_none_of_its_statements(self):
        self.client.call("execute_sql", connection="Notes", sql="DELETE FROM notes WHERE id = 1; DROP TABLE big")
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)

    def test_ordinary_writes_still_run(self):
        is_error, _ = self.client.call("execute_sql", connection="Notes", sql="DELETE FROM notes WHERE id = 3")
        self.assertFalse(is_error)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 2)

    def test_dropping_through_alter_table_is_refused_too(self):
        for key, item in (("columns", {"op": "drop", "name": "body"}), ("indexes", {"op": "drop", "name": "x"}),
                          ("foreign_keys", {"op": "drop", "name": "x"})):
            is_error, payload = self.client.call("alter_table", connection="Notes", schema="main", table="notes",
                                                 changes={key: [item]})
            self.assertTrue(is_error, key)
            self.assertEqual(payload["error"]["code"], "destructive", key)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM pragma_table_info('notes')"), 2)


class SqlFileEffectsTest(Fixture):
    """Raw SQL must not reach files outside the MCP files folder, whatever the tier or the connection."""

    def setUp(self):
        super().setUp()
        self.outside = os.path.join(self.tmp.name, "outside")
        os.makedirs(self.outside)
        self.tiers = [Client(self.helper, files=self.files, **flags) for flags in ({}, {"write": True}, {"destructive": True})]

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

    def test_words_that_only_look_like_those_statements_still_run(self):
        write = self.tiers[1]
        for sql in ("SELECT 'VACUUM INTO x, ATTACH, COPY, OUTFILE' AS words", "SELECT 1 /* ATTACH */", "VACUUM"):
            self.assertFalse(write.call("execute_sql", connection="Notes", sql=sql)[0], sql)

    def test_export_runs_one_query_that_reads_and_nothing_else(self):
        write = self.tiers[1]
        for sql in (f"VACUUM INTO '{self.outside}/e.db'", "DELETE FROM notes", "SELECT 1; SELECT 2",
                    "PRAGMA user_version(9)", "CREATE TABLE t (x)", "WITH d AS (DELETE FROM notes RETURNING id) SELECT * FROM d",
                    "SELECT * FROM notes INTO OUTFILE '/tmp/x'"):
            for ref in ("Notes", "Prod", "Frozen"):
                is_error, payload = write.call("export_data", connection=ref, sql=sql, format="csv", file="x.csv")
                self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"), (ref, sql))
        self.assertEqual((os.listdir(self.outside), self.scalar("SELECT COUNT(*) FROM notes"),
                          self.scalar("PRAGMA user_version")), ([], 3, 0))
        self.assertFalse(os.path.exists(os.path.join(self.files, "x.csv")))

    def test_export_still_takes_selects_with_and_values(self):
        write = self.tiers[1]
        for n, sql in enumerate(("SELECT body FROM notes", "WITH x AS (SELECT 1 AS n) SELECT n FROM x", "VALUES (1), (2)",
                                 "  -- all\nselect * from notes;")):
            is_error, payload = write.call("export_data", connection="Prod", sql=sql, format="json", file=f"ok{n}.json")
            self.assertFalse(is_error, (sql, payload))


class WriteToolsTest(Fixture):
    write = True

    def put(self, name, text):
        os.makedirs(self.files, exist_ok=True)
        with open(os.path.join(self.files, name), "w", newline="") as f:
            f.write(text)

    def test_create_table(self):
        is_error, _ = self.client.call(
            "create_table", connection="Notes", schema="main", table="tags",
            columns=[{"name": "id", "type": "INTEGER", "pk": True}, {"name": "label", "type": "TEXT"}])
        self.assertFalse(is_error)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM pragma_table_info('tags')"), 2)

    def test_alter_table_adds_a_column(self):
        is_error, _ = self.client.call("alter_table", connection="Notes", schema="main", table="notes",
                                       changes={"columns": [{"op": "add", "name": "extra", "type": "TEXT"}]})
        self.assertFalse(is_error)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM pragma_table_info('notes')"), 3)

    def test_schema_changes_are_refused_on_production_and_read_only_connections(self):
        columns = [{"name": "id", "type": "INTEGER", "pk": True}]
        for ref, code in (("Prod", "confirm"), ("Frozen", "readonly")):
            is_error, payload = self.client.call("create_table", connection=ref, schema="main", table="tags",
                                                 columns=columns)
            self.assertTrue(is_error, ref)
            self.assertEqual(payload["error"]["code"], code, ref)
            is_error, payload = self.client.call("alter_table", connection=ref, schema="main", table="notes",
                                                 changes={"columns": [{"op": "add", "name": "x", "type": "TEXT"}]})
            self.assertEqual(payload["error"]["code"], code, ref)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM sqlite_master WHERE name = 'tags'"), 0)

    def test_save_query_creates_a_new_saved_query_and_cannot_overwrite_one(self):
        self.assertFalse(self.client.call("save_query", name="count notes", sql="SELECT COUNT(*) FROM notes")[0])
        _, listed = self.client.call("list_saved_queries")
        self.assertEqual([i["name"] for i in listed["items"]], ["count notes"])
        is_error, payload = self.client.call("save_query", name="x", sql="SELECT 1", id=listed["items"][0]["id"])
        self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"))

    def test_import_csv_inserts_rows_from_a_file_of_the_folder(self):
        self.put("in.csv", "body\nalpha\nbeta\n")
        is_error, payload = self.client.call("import_csv", connection="Notes", schema="main", table="notes",
                                             file="in.csv", mapping=["body"])
        self.assertFalse(is_error, payload)
        self.assertEqual(payload["inserted"], 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 5)

    def test_import_csv_is_refused_on_production_and_cannot_leave_the_folder(self):
        self.put("in.csv", "body\nalpha\n")
        is_error, payload = self.client.call("import_csv", connection="Prod", schema="main", table="notes",
                                             file="in.csv", mapping=["body"])
        self.assertEqual((is_error, payload["error"]["code"]), (True, "confirm"))
        is_error, payload = self.client.call("import_csv", connection="Notes", schema="main", table="notes",
                                             file="../in.csv", mapping=["body"])
        self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)

    def test_export_a_table_to_a_new_file_of_the_folder(self):
        is_error, payload = self.client.call("export_data", connection="Notes", schema="main", table="notes",
                                             format="csv", file="notes.csv")
        self.assertFalse(is_error, payload)
        self.assertEqual(payload["rows"], 3)
        self.assertEqual(payload["file"], "notes.csv")
        with open(os.path.join(self.files, "notes.csv"), newline="") as f:
            self.assertEqual([r[1] for r in csv.reader(f)], ["body", "one", "two", "three"])

    def test_export_a_query_and_never_overwrite(self):
        args = {"connection": "Notes", "format": "json", "file": "q.json", "sql": "SELECT body FROM notes"}
        self.assertFalse(self.client.call("export_data", **args)[0])
        is_error, payload = self.client.call("export_data", **args)
        self.assertEqual((is_error, payload["error"]["code"]), (True, "exists"))

    def test_export_is_a_read_so_production_may_do_it_but_cannot_use_it_to_write(self):
        self.assertFalse(self.client.call("export_data", connection="Prod", schema="main", table="notes",
                                          format="csv", file="p.csv")[0])
        is_error, payload = self.client.call("export_data", connection="Notes", format="csv", file="w.csv",
                                             sql="DELETE FROM notes")
        self.assertTrue(is_error)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)
        self.assertFalse(os.path.exists(os.path.join(self.files, "w.csv")))

    def test_export_needs_exactly_one_source_and_a_known_format(self):
        base = {"connection": "Notes", "file": "x.csv", "format": "csv"}
        for extra in ({}, {"sql": "SELECT 1", "table": "notes", "schema": "main"}, {"table": "notes"}):
            is_error, payload = self.client.call("export_data", **base, **extra)
            self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"), extra)
        is_error, payload = self.client.call("export_data", **{**base, "format": "xlsx"}, sql="SELECT 1")
        self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"))

    def test_export_and_backup_cannot_leave_the_folder(self):
        for tool, args in (("export_data", {"schema": "main", "table": "notes", "format": "csv"}),
                           ("backup_database", {})):
            for name in ("../x", "/tmp/x", "a/b", ".hidden"):
                is_error, payload = self.client.call(tool, connection="Notes", file=name, **args)
                self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"), (tool, name))

    def test_backup_database_writes_a_new_file_of_the_folder(self):
        is_error, payload = self.client.call("backup_database", connection="Notes", file="app.bak")
        self.assertFalse(is_error, payload)
        self.assertEqual(payload["file"], "app.bak")
        self.assertGreater(os.path.getsize(os.path.join(self.files, "app.bak")), 0)
        self.assertEqual(self.client.call("backup_database", connection="Notes", file="app.bak")[1]["error"]["code"],
                         "exists")

    def test_create_database_on_a_file_engine_is_unsupported(self):
        is_error, payload = self.client.call("create_database", connection="Notes", name="other")
        self.assertEqual((is_error, payload["error"]["code"]), (True, "unsupported"))

    def test_create_connection_is_read_only_production_and_has_no_password(self):
        is_error, payload = self.client.call("create_connection", name="New", engine="postgresql", host="pg.example",
                                             user="reader", database="app")
        self.assertFalse(is_error, payload)
        saved = self.store.get(payload["id"], secrets=True)
        self.assertEqual((saved["readonly"], saved["tag"], saved["password"]), (True, "production", ""))
        self.assertNotIn("password", payload)

    def test_create_connection_only_for_server_engines_and_with_no_guard_switch(self):
        is_error, payload = self.client.call("create_connection", name="File", engine="sqlite", host="x")
        self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"))
        for extra in ({"tag": "local"}, {"readonly": False}, {"password": "p"}, {"path": "/etc/passwd"}):
            is_error, payload = self.client.call("create_connection", name="N", engine="mysql", host="h", **extra)
            self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"), extra)
        self.assertEqual([c["name"] for c in self.store.list() if c["name"] in ("File", "N")], [])

    def test_duplicate_connection_keeps_the_guards_of_the_source(self):
        is_error, payload = self.client.call("duplicate_connection", connection="Prod")
        self.assertFalse(is_error, payload)
        copy = self.store.get(payload["id"], secrets=True)
        self.assertEqual((copy["tag"], copy["name"]), ("production", "Prod copy"))
        frozen = self.client.call("duplicate_connection", connection="Frozen")[1]
        self.assertTrue(self.store.get(frozen["id"], secrets=True)["readonly"])

    def test_nothing_is_written_outside_the_folder(self):
        self.client.call("export_data", connection="Notes", schema="main", table="notes", format="csv", file="a.csv")
        self.client.call("backup_database", connection="Notes", file="a.bak")
        leftovers = [n for n in os.listdir(self.files) if n.startswith(".") and n != ".backups.json"]
        self.assertEqual((sorted(n for n in os.listdir(self.files) if n != ".backups.json"), leftovers),
                         (["a.bak", "a.csv"], []))


class DestructiveToolsTest(Fixture):
    write = True
    destructive = True

    def test_execute_sql_may_drop(self):
        self.assertFalse(self.client.call("execute_sql", connection="Notes", sql="DROP TABLE big")[0])
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM sqlite_master WHERE name = 'big'"), 0)

    def test_drop_object_drops_a_table_or_a_view(self):
        self.client.call("execute_sql", connection="Notes", sql="CREATE VIEW v AS SELECT * FROM notes")
        for name, kind in (("big", "table"), ("v", "view")):
            is_error, payload = self.client.call("drop_object", connection="Notes", schema="main", name=name, kind=kind)
            self.assertFalse(is_error, payload)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('big', 'v')"), 0)

    def test_truncate_table_empties_it(self):
        is_error, payload = self.client.call("truncate_table", connection="Notes", schema="main", table="big")
        self.assertFalse(is_error, payload)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM big"), 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)

    def test_production_and_read_only_connections_survive_every_destructive_tool(self):
        for ref, code in (("Prod", "confirm"), ("Frozen", "readonly")):
            for tool, args in (("drop_object", {"schema": "main", "name": "notes", "kind": "table"}),
                               ("truncate_table", {"schema": "main", "table": "notes"}),
                               ("drop_database", {"name": "main"}),
                               ("kill_process", {"process_id": 1})):
                is_error, payload = self.client.call(tool, connection=ref, **args)
                self.assertTrue(is_error, (ref, tool))
                self.assertIn(payload["error"]["code"], (code, "unsupported"), (ref, tool))
            self.assertEqual(self.client.call("drop_object", connection=ref, schema="main", name="notes",
                                              kind="table")[1]["error"]["code"], code)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM notes"), 3)

    def test_restore_database_reaches_the_helper_with_a_file_of_the_folder(self):
        # the helper restores PostgreSQL, MySQL and MongoDB dumps; for a SQLite file it says so, which proves the
        # tool got as far as the helper with a resolved path (a real restore needs the drivers' dump tools)
        self.client.call("backup_database", connection="Notes", file="app.bak")
        is_error, payload = self.client.call("restore_database", connection="Notes", file="app.bak")
        self.assertEqual((is_error, payload["error"]["code"]), (True, "unsupported"))
        self.assertEqual(self.client.call("restore_database", connection="Notes", file="nope.bak")[1]["error"]["code"],
                         "notfound")

    def test_restore_only_loads_what_backup_database_wrote(self):
        # a file the model shaped itself would run with the server's rights: psql executes `\! command`,
        # and an export puts a column alias of the model's choosing in the first line of a CSV
        loaded = []
        spy = lambda **kwargs: loaded.append(os.path.basename(kwargs["path"])) or {"ms": 0, "database": "main"}  # noqa: E731
        self.assertFalse(self.client.call("backup_database", connection="Notes", file="app.bak")[0])
        self.assertFalse(self.client.call("export_data", connection="Notes", format="csv", file="evil.sql",
                                          sql='SELECT 1 AS "a \\! touch /tmp/pwned"')[0])
        with open(os.path.join(self.files, "by-hand.sql"), "w") as f:
            f.write("SELECT 1;")
        with mock.patch.dict(self.helper.methods, {"db.restore": spy}):
            for name in ("evil.sql", "by-hand.sql"):
                is_error, payload = self.client.call("restore_database", connection="Notes", file=name)
                self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"), name)
                self.assertIn("backup_database", payload["error"]["message"])
            self.assertFalse(self.client.call("restore_database", connection="Notes", file="app.bak")[0])
            with open(os.path.join(self.files, "app.bak"), "ab") as f:
                f.write(b"\n\\! touch /tmp/pwned\n")
            self.assertEqual(self.client.call("restore_database", connection="Notes", file="app.bak")[1]["error"]["code"],
                             "invalid")
        self.assertEqual(loaded, ["app.bak"])

    def test_restore_cannot_read_outside_the_folder(self):
        is_error, payload = self.client.call("restore_database", connection="Notes", file="../app.db")
        self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"))

    def test_delete_saved_query(self):
        item = self.helper.methods["query.save"](name="old", sql="SELECT 1")
        self.assertFalse(self.client.call("delete_saved_query", query_id=item["id"])[0])
        self.assertEqual(self.client.call("list_saved_queries")[1]["items"], [])

    def test_delete_connection_closes_its_sessions_and_forgets_it(self):
        self.client.call("execute_sql", connection="Notes", sql="SELECT 1")
        self.assertFalse(self.client.call("delete_connection", connection="Notes")[0])
        self.assertNotIn(self.local["id"], [c["id"] for c in self.store.list()])
        self.assertEqual(self.helper.sessions.items, {})

    def test_a_production_or_read_only_connection_is_not_deleted_like_it_is_not_changed(self):
        for ref, code in (("Prod", "confirm"), ("Frozen", "readonly")):
            is_error, payload = self.client.call("delete_connection", connection=ref)
            self.assertEqual((is_error, payload["error"]["code"]), (True, code), ref)
        self.assertTrue({self.prod["id"], self.frozen["id"]} <= {c["id"] for c in self.store.list()})

    def test_copy_and_delete_leave_omadb_servers_alone(self):
        server = {"id": "omadb:fake", "name": "Fake", "engine": "mysql", "host": "127.0.0.1", "port": 3306}
        with mock.patch.object(omadb, "servers", lambda: [server]):
            self.assertFalse(self.client.call("list_connections")[0])
            for tool in ("delete_connection", "duplicate_connection"):
                is_error, payload = self.client.call(tool, connection="Fake")
                self.assertEqual((is_error, payload["error"]["code"]), (True, "notfound"), tool)
                self.assertIn("saved connection", payload["error"]["message"], tool)


class ServerAdminTest(Faked):
    write = True
    destructive = True

    def test_kill_process(self):
        self.assertFalse(self.client.call("kill_process", connection="Server", process_id=7, hard=True)[0])
        self.assertEqual(self.called("kill"), [("kill", 7, True)])

    def test_drop_and_create_database(self):
        self.assertFalse(self.client.call("drop_database", connection="Server", name="old")[0])
        self.assertFalse(self.client.call("create_database", connection="Server", name="fresh")[0])
        self.assertEqual((self.called("drop_database"), self.called("create_database")),
                         ([("drop_database", "old")], [("create_database", "fresh")]))

    def test_drop_object_routes_functions_and_triggers_to_their_own_method(self):
        self.client.call("drop_object", connection="Server", schema="app", name="f", kind="function", signature="a int")
        self.client.call("drop_object", connection="Server", schema="app", name="t", kind="trigger", table="orders")
        self.client.call("drop_object", connection="Server", schema="app", name="orders", kind="table")
        self.assertEqual(self.called("drop_object"), [("drop_object", "app", "f", "function", None, "a int"),
                                                      ("drop_object", "app", "t", "trigger", "orders", None)])
        self.assertEqual(self.called("drop"), [("drop", "app", "orders", "table")])

    def test_truncate_table(self):
        self.client.call("truncate_table", connection="Server", schema="app", table="orders")
        self.assertEqual(self.called("truncate"), [("truncate", "app", "orders")])

    def test_change_user_runs_the_statements(self):
        is_error, payload = self.client.call("change_user", connection="Server", op="drop",
                                             spec={"name": "app", "host": "%"})
        self.assertFalse(is_error, payload)
        self.assertEqual(self.called("admin"), [("admin", ["DROP USER 'app'@'%'"])])

    def test_change_user_refuses_a_password(self):
        is_error, payload = self.client.call("change_user", connection="Server", op="alter",
                                             spec={"name": "app", "password": "hunter2"})
        self.assertEqual((is_error, payload["error"]["code"]), (True, "invalid"))
        self.assertEqual(self.called("admin"), [])

    def test_production_refuses_each_server_tool(self):
        calls = (("kill_process", {"process_id": 7}), ("drop_database", {"name": "old"}), ("create_database", {"name": "x"}),
                 ("truncate_table", {"schema": "a", "table": "t"}),
                 ("drop_object", {"schema": "a", "name": "t", "kind": "table"}),
                 ("change_user", {"op": "drop", "spec": {"name": "app"}}))
        for tool, args in calls:
            is_error, payload = self.client.call(tool, connection="Server prod", **args)
            self.assertEqual((is_error, payload["error"]["code"]), (True, "confirm"), tool)
        self.assertEqual([c[0] for c in FakeAdapter.calls if c[0] != "use"], [])


if __name__ == "__main__":
    unittest.main()
