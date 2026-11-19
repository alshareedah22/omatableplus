import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omatableplus.__main__ import build
from omatableplus.protocol import Error
from omatableplus.store import Keyring, Store
from tests.test_tunnel import SSHD, Sshd

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
LIVE = os.environ.get("OMATABLEPLUS_TEST_DB") == "1"


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["OMATABLEPLUS_DBCTL"] = os.path.join(self.tmp.name, "none")
        self.path = os.path.join(self.tmp.name, "app.db")
        db = sqlite3.connect(self.path)
        db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        db.executemany("INSERT INTO notes (body) VALUES (?)", [("one",), ("two",), ("three",)])
        db.commit()
        db.close()
        self.store = Store(os.path.join(self.tmp.name, "cfg"), Keyring(False))
        self.server = build(self.store)
        self.m = self.server.methods
        self.conn = self.store.save({"name": "Notes", "engine": "sqlite", "path": self.path, "tag": "local"})

    def tearDown(self):
        self.server.sessions.close_all()
        os.environ.pop("OMATABLEPLUS_DBCTL", None)
        self.tmp.cleanup()

    def test_read_only_at_connect_time(self):
        sid = self.m["session.open"](id=self.conn["id"], readonly=True)["session"]
        with self.assertRaises(Error) as e:
            self.m["query.run"](session=sid, text="DELETE FROM notes")
        self.assertEqual(e.exception.code, "readonly")

    def test_read_only_connection(self):
        self.store.save({**self.conn, "readonly": True, "folder": "Work"})
        listed = self.store.list()[0]
        self.assertEqual((listed["readonly"], listed["folder"]), (True, "Work"))
        sid = self.m["session.open"](id=self.conn["id"])["session"]
        self.assertEqual(len(self.m["query.run"](session=sid, text="SELECT * FROM notes")["results"][0]["rows"]), 3)
        writes = [("query.run", {"text": "DELETE FROM notes"}),
                  ("table.commit", {"schema": "main", "table": "notes",
                                    "changes": {"updates": [{"key": {"id": 1}, "values": {"body": "x"}}]}}),
                  ("table.drop", {"schema": "main", "table": "notes"}),
                  ("table.truncate", {"schema": "main", "table": "notes", "confirmed": True})]
        for method, params in writes:
            with self.assertRaises(Error) as e:
                self.m[method](session=sid, **params)
            self.assertEqual(e.exception.code, "readonly", method)
        self.assertEqual(len(self.m["query.run"](session=sid, text="SELECT * FROM notes")["results"][0]["rows"]), 3)

    def test_open_browse_commit_close(self):
        info = self.m["session.open"](id=self.conn["id"])
        self.assertEqual(info["engine"], "sqlite")
        self.assertEqual(info["tag"], "local")
        self.assertEqual(info["databases"], ["main"])
        sid = info["session"]
        tree = self.m["schema.tree"](session=sid)
        self.assertEqual(tree["schemas"][0]["tables"], ["notes"])
        rows = self.m["table.rows"](session=sid, schema="main", table="notes", sort=[{"column": "id", "desc": True}])
        self.assertEqual(rows["rows"][0], [3, "three"])
        changes = {"updates": [{"key": {"id": 1}, "values": {"body": "uno"}}]}
        self.assertIn("'uno'", self.m["table.preview"](session=sid, schema="main", table="notes",
                                                       changes=changes)["statements"][0])
        self.assertEqual(self.m["table.commit"](session=sid, schema="main", table="notes", changes=changes),
                         {"affected": 1})
        self.assertEqual(self.m["table.count"](session=sid, schema="main", table="notes",
                                               filters=[{"column": "body", "op": "=", "value": "uno"}]), {"count": 1})
        self.m["session.close"](session=sid)
        with self.assertRaises(Error) as e:
            self.m["schema.tree"](session=sid)
        self.assertEqual(e.exception.code, "notfound")

    def test_a_page_comes_with_its_total_and_leaves_no_transaction_open(self):
        sid = self.m["session.open"](id=self.conn["id"])["session"]
        page = self.m["table.page"](session=sid, schema="main", table="notes", limit=2,
                                    filters=[{"column": "body", "op": "contains", "value": "t"}])
        self.assertEqual((len(page["rows"]), page["matched"]), (2, 2))
        changes = {"updates": [{"key": {"id": 1}, "values": {"body": "uno"}}]}
        self.assertEqual(self.m["table.commit"](session=sid, schema="main", table="notes", changes=changes),
                         {"affected": 1})

    def test_query_run_current_and_all(self):
        sid = self.m["session.open"](id=self.conn["id"])["session"]
        text = "SELECT body FROM notes WHERE id = 1;\n\nSELECT count(*) FROM notes;\n"
        r = self.m["query.run"](session=sid, text=text, cursor=text.index("count"))
        self.assertEqual([x["rows"] for x in r["results"]], [[[3]]])
        self.assertEqual(r["range"], [text.index("SELECT count"), len(text) - 1])
        self.assertFalse(r["ddl"])
        r = self.m["query.run"](session=sid, text=text)
        self.assertEqual([x["rows"] for x in r["results"]], [[["one"]], [[3]]])
        with self.assertRaises(Error):
            self.m["query.run"](session=sid, text="  -- nothing")

    def test_query_offsets_are_utf16(self):
        sid = self.m["session.open"](id=self.conn["id"])["session"]
        text = "SELECT '\U0001F600';SELECT 2"
        cursor = len(text.encode("utf-16-le")) // 2 - 1      # inside "SELECT 2", in QML units
        r = self.m["query.run"](session=sid, text=text, cursor=cursor)
        self.assertEqual(r["results"][0]["rows"], [[2]])
        self.assertEqual(r["range"], [12, 20])

    def test_query_ddl_history_and_errors(self):
        sid = self.m["session.open"](id=self.conn["id"])["session"]
        r = self.m["query.run"](session=sid, text="CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT)")
        self.assertTrue(r["ddl"])
        self.assertIn("tags", self.m["schema.tree"](session=sid)["schemas"][0]["tables"])
        r = self.m["query.run"](session=sid, text="SELECT 1; SELECT nope FROM notes; SELECT 3")
        self.assertEqual(len(r["results"]), 2)
        self.assertEqual(r["results"][1]["error"]["code"], "sql")
        items = self.m["query.history"](conn=self.conn["id"])["items"]
        self.assertEqual(items[0]["sql"], "SELECT 1; SELECT nope FROM notes; SELECT 3")
        self.assertFalse(items[0]["ok"])
        self.assertIn("nope", items[0]["error"])
        self.assertTrue(items[1]["ok"])
        self.assertEqual(self.m["query.history"](search="tags")["items"][0]["statements"], 1)
        self.m["query.history_clear"](conn=self.conn["id"])
        self.assertEqual(self.m["query.history"]()["items"], [])

    def test_query_cancel(self):
        sid = self.m["session.open"](id=self.conn["id"])["session"]
        slow = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c"
        threading.Timer(0.5, lambda: self.m["query.cancel"](session=sid)).start()
        start = time.monotonic()
        r = self.m["query.run"](session=sid, text=slow)
        self.assertLess(time.monotonic() - start, 8)
        self.assertEqual(r["results"][0]["error"]["code"], "cancelled")
        self.assertEqual(self.m["query.run"](session=sid, text="SELECT 5")["results"][0]["rows"], [[5]])

    def test_saved_queries(self):
        q = self.m["query.save"](name="Count", sql="SELECT count(*) FROM notes")
        self.assertEqual([x["name"] for x in self.m["query.saved"]()["items"]], ["Count"])
        self.m["query.delete_saved"](id=q["id"])
        self.assertEqual(self.m["query.saved"]()["items"], [])

    def test_structure_methods(self):
        sid = self.m["session.open"](id=self.conn["id"])["session"]
        st = self.m["struct.get"](session=sid, schema="main", table="notes")
        self.assertEqual([c["name"] for c in st["columns"]], ["id", "body"])
        changes = {"columns": [{"op": "add", "name": "tag", "type": "TEXT"}]}
        self.assertEqual(self.m["struct.preview"](session=sid, schema="main", table="notes", changes=changes),
                         {"statements": ['ALTER TABLE "main"."notes" ADD COLUMN "tag" TEXT;']})
        self.m["struct.apply"](session=sid, schema="main", table="notes", changes=changes)
        cols = self.m["table.rows"](session=sid, schema="main", table="notes")["columns"]
        self.assertEqual([c["name"] for c in cols], ["id", "body", "tag"])

    def test_export_import_drop_truncate(self):
        sid = self.m["session.open"](id=self.conn["id"])["session"]
        path = os.path.join(self.tmp.name, "notes.csv")
        r = self.m["export.run"](session=sid, target={"schema": "main", "table": "notes"}, format="csv", path=path)
        self.assertEqual(r["rows"], 3)
        p = self.m["import.preview"](path=path)
        self.assertEqual((p["header"], p["count"]), (["id", "body"], 3))
        self.m["table.truncate"](session=sid, schema="main", table="notes")
        self.assertEqual(self.m["table.count"](session=sid, schema="main", table="notes")["count"], 0)
        r = self.m["import.run"](session=sid, schema="main", table="notes", path=path, mapping=["id", "body"])
        self.assertEqual(r, {"inserted": 3})
        self.m["table.drop"](session=sid, schema="main", table="notes")
        self.assertEqual(self.m["schema.tree"](session=sid)["schemas"][0]["tables"], [])
        self.assertIn("home", self.m["app.paths"]())

    def test_palette(self):
        path = os.path.join(self.tmp.name, "colors.toml")
        with open(path, "w") as f:
            f.write('magenta = "#cba6f7"\ngreen = "#a6e3a1"\ncolor3 = "#f9e2af"\ncyan = "bad"\n')
        os.environ["OMATABLEPLUS_THEME_COLORS"] = path
        try:
            self.assertEqual(self.m["app.palette"](), {"keyword": "#cba6f7", "string": "#a6e3a1", "number": "#f9e2af"})
            os.environ["OMATABLEPLUS_THEME_COLORS"] = path + ".missing"
            self.assertEqual(self.m["app.palette"](), {})
        finally:
            os.environ.pop("OMATABLEPLUS_THEME_COLORS", None)

    def test_conn_test_with_form_data(self):
        ok = self.m["conn.test"](conn={"name": "x", "engine": "sqlite", "path": self.path})
        self.assertTrue(ok["ok"])
        self.assertTrue(ok["version"].startswith("SQLite"))
        with self.assertRaises(Error) as e:
            self.m["conn.test"](conn={"name": "x", "engine": "sqlite", "path": self.path + "x"})
        self.assertEqual(e.exception.code, "notfound")
        with self.assertRaises(Error) as e:
            self.m["conn.test"](conn={"name": "", "engine": "sqlite"})
        self.assertEqual(e.exception.code, "invalid")

    def test_safe_mode(self):
        prod = self.store.save({"name": "Prod", "engine": "sqlite", "path": self.path, "tag": "production"})
        sid = self.m["session.open"](id=prod["id"])["session"]
        # reads run straight away
        self.assertEqual(self.m["query.run"](session=sid, text="SELECT count(*) FROM notes")["results"][0]["rows"], [[3]])
        changes = {"updates": [{"key": {"id": 1}, "values": {"body": "uno"}}]}
        calls = [
            ("table.commit", {"schema": "main", "table": "notes", "changes": changes}, "UPDATE"),
            ("query.run", {"text": "SELECT 1; DELETE FROM notes WHERE id = 3"}, "DELETE FROM notes WHERE id = 3;"),
            ("struct.apply", {"schema": "main", "table": "notes",
                              "changes": {"columns": [{"op": "add", "name": "n", "type": "INTEGER"}]}}, "ADD COLUMN"),
            ("table.truncate", {"schema": "main", "table": "notes"}, None),
        ]
        for method, params, detail in calls:
            with self.assertRaises(Error) as e:
                self.m[method](session=sid, **params)
            self.assertEqual(e.exception.code, "confirm", method)
            if detail:
                self.assertIn(detail, e.exception.detail)
        # nothing ran
        self.assertEqual(self.m["query.run"](session=sid, text="SELECT body FROM notes ORDER BY id")["results"][0]["rows"],
                         [["one"], ["two"], ["three"]])
        self.assertEqual(self.m["table.commit"](session=sid, schema="main", table="notes", changes=changes, confirmed=True),
                         {"affected": 1})
        out = self.m["query.run"](session=sid, text="DELETE FROM notes WHERE id = 3", confirmed=True)
        self.assertEqual(out["results"][0]["affected"], 1)

    def test_confirm_error_carries_detail_over_the_wire(self):
        import io
        from omatableplus.protocol import Server
        out = io.StringIO()
        server = Server(out)

        @server.method("x")
        def x():
            raise Error("confirm", "Sure?", "DROP TABLE t;")

        server.handle(json.dumps({"id": 1, "method": "x"}))
        server.close()
        self.assertEqual(json.loads(out.getvalue())["error"], {"code": "confirm", "message": "Sure?", "detail": "DROP TABLE t;"})

    def test_wrong_kind_of_session(self):
        sid = self.m["session.open"](id=self.conn["id"])["session"]
        for method, params in (("redis.get", {"key": "k"}), ("mongo.find", {"collection": "c"})):
            with self.assertRaises(Error) as e:
                self.m[method](session=sid, **params)
            self.assertEqual(e.exception.code, "invalid")

    def test_sigterm_closes_cleanly(self):
        env = {**os.environ, "PYTHONPATH": ROOT, "OMATABLEPLUS_CONFIG": os.path.join(self.tmp.name, "cfg"),
               "OMATABLEPLUS_NO_KEYRING": "1"}
        proc = subprocess.Popen([sys.executable, "-m", "omatableplus"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                text=True, env=env)
        self.assertEqual(json.loads(proc.stdout.readline())["event"], "ready")
        proc.stdin.write(json.dumps({"id": 1, "method": "session.open", "params": {"id": self.conn["id"]}}) + "\n")
        proc.stdin.flush()
        self.assertIn("session", json.loads(proc.stdout.readline())["result"])
        proc.send_signal(signal.SIGTERM)
        self.assertEqual(proc.wait(timeout=10), 0)
        proc.stdin.close()
        proc.stdout.close()


@unittest.skipUnless(LIVE, "set OMATABLEPLUS_TEST_DB=1 with zt-vk and zt-mg running")
class KeyValueSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "cfg"), Keyring(False))
        self.server = build(self.store)
        self.m = self.server.methods

    def tearDown(self):
        self.server.sessions.close_all()
        self.tmp.cleanup()

    def test_redis_methods(self):
        info = self.m["session.open"](id="omadb:zt-vk")
        self.assertEqual((info["engine"], info["database"]), ("valkey", "0"))
        self.assertTrue(info["version"].startswith("Valkey"))
        sid = info["session"]
        self.m["session.use"](session=sid, database="9")
        self.m["redis.cmd"](session=sid, text="FLUSHDB")
        self.m["redis.create"](session=sid, key="greet", type="hash", value=[["en", "hi"]], ttl=60)
        self.m["redis.edit"](session=sid, key="greet", type="hash", action="set", item="ar", value="مرحبا")
        got = self.m["redis.get"](session=sid, key="greet")
        self.assertEqual(sorted(got["value"]), [["ar", "مرحبا"], ["en", "hi"]])
        self.assertEqual(self.m["redis.scan"](session=sid, pattern="gr*")["keys"][0]["key"], "greet")
        self.assertEqual(self.m["redis.keyspace"](session=sid)["9"], 1)
        self.assertEqual(self.m["redis.ttl"](session=sid, key="greet", ttl=None)["ttl"], -1)
        self.m["redis.rename"](session=sid, key="greet", new="hello")
        out = self.m["redis.cmd"](session=sid, text="HGET hello en")
        self.assertEqual(out["reply"], "hi")
        hist = self.m["query.history"](conn="omadb:zt-vk")["items"]
        self.assertEqual(hist[0]["sql"], "HGET hello en")
        self.assertEqual(self.m["redis.del"](session=sid, keys=["hello"])["deleted"], 1)
        self.m["query.history_clear"](conn="omadb:zt-vk")

    def production(self, name, **extra):
        from omatableplus import omadb
        src = omadb.server(name)
        conn = self.store.save({"name": "prod " + name, "engine": src["engine"], "host": "127.0.0.1", "port": src["port"],
                                "user": src["user"], "password": src["password"], "tag": "production", **extra})
        return self.m["session.open"](id=conn["id"])["session"]

    def confirm_needed(self, method, **params):
        with self.assertRaises(Error) as e:
            self.m[method](**params)
        self.assertEqual(e.exception.code, "confirm", (method, params))
        return e.exception

    def test_redis_safe_mode(self):
        sid = self.production("zt-vk", database="9")
        self.m["redis.cmd"](session=sid, text="FLUSHDB", confirmed=True)
        for text in ("GET a", "SCAN 0", "INFO memory", "TTL a", "CONFIG GET maxmemory", "SELECT 9"):
            self.m["redis.cmd"](session=sid, text=text)
        for text in ("SET a 1", "DEL a", "FLUSHALL", "CONFIG SET maxmemory 0", "EVAL 'return 1' 0", "EXPIRE a 5"):
            self.assertEqual(self.confirm_needed("redis.cmd", session=sid, text=text).detail, text)
        self.confirm_needed("redis.create", session=sid, key="k", type="string", value="v")
        self.m["redis.create"](session=sid, key="k", type="string", value="v", confirmed=True)
        self.confirm_needed("redis.edit", session=sid, key="k", type="string", action="set", value="w")
        self.confirm_needed("redis.del", session=sid, keys=["k"])
        self.confirm_needed("redis.rename", session=sid, key="k", new="k2")
        self.confirm_needed("redis.ttl", session=sid, key="k", ttl=5)
        self.assertEqual(self.m["redis.get"](session=sid, key="k")["value"], "v")
        self.m["redis.cmd"](session=sid, text="FLUSHDB", confirmed=True)

    def test_mongo_safe_mode(self):
        sid = self.production("zt-mg", database="zt_safe")
        try:
            self.m["mongo.create_collection"](session=sid, name="c", confirmed=True)
            self.m["mongo.find"](session=sid, collection="c")
            self.m["mongo.aggregate"](session=sid, collection="c", pipeline="[{$match: {}}]")
            self.m["mongo.run"](session=sid, text="{dbStats: 1}")
            self.confirm_needed("mongo.insert", session=sid, collection="c", text="{a: 1}")
            self.confirm_needed("mongo.aggregate", session=sid, collection="c", pipeline="[{$out: 'copy'}]")
            self.confirm_needed("mongo.run", session=sid, text="{drop: 'c'}")
            self.confirm_needed("mongo.drop_collection", session=sid, name="c")
            self.confirm_needed("mongo.create_index", session=sid, collection="c", keys="{a: 1}")
            self.assertIn("c", self.m["schema.tree"](session=sid)["schemas"][0]["tables"])
        finally:
            s = self.server.sessions.get(sid)
            s.run(s.adapter.client.drop_database, "zt_safe")
        self.m["query.history_clear"](conn=None)

    def test_mongo_methods(self):
        info = self.m["session.open"](id="omadb:zt-mg")
        sid = info["session"]
        self.m["session.use"](session=sid, database="zt_session")
        try:
            self.m["mongo.create_collection"](session=sid, name="notes")
            self.assertEqual(self.m["schema.tree"](session=sid)["schemas"][0]["tables"], ["notes"])
            self.m["mongo.insert"](session=sid, collection="notes", text="[{n: 1}, {n: 2}]")
            found = self.m["mongo.find"](session=sid, collection="notes", sort="{n: -1}")
            self.assertEqual([r[1] for r in found["rows"]], [2, 1])
            self.m["mongo.update"](session=sid, collection="notes", id=found["ids"][0], text="{n: 20}")
            self.assertEqual(self.m["mongo.count"](session=sid, collection="notes", filter="{n: 20}")["count"], 1)
            self.assertEqual(self.m["mongo.delete"](session=sid, collection="notes", ids=found["ids"][1:])["deleted"], 1)
            self.m["mongo.create_index"](session=sid, collection="notes", keys="{n: 1}")
            self.assertEqual(len(self.m["mongo.indexes"](session=sid, collection="notes")["indexes"]), 2)
            self.m["mongo.drop_index"](session=sid, collection="notes", name="n_1")
            agg = self.m["mongo.aggregate"](session=sid, collection="notes", pipeline="[{$project: {_id: 0}}]")
            self.assertEqual(agg["rows"], [[20]])
            self.assertIn('"ok": 1.0', self.m["mongo.run"](session=sid, text="{ping: 1}")["reply"])
            self.m["mongo.drop_collection"](session=sid, name="notes")
        finally:
            s = self.server.sessions.get(sid)
            s.run(s.adapter.client.drop_database, "zt_session")
        self.m["query.history_clear"](conn="omadb:zt-mg")


@unittest.skipUnless(LIVE and os.access(SSHD, os.X_OK), "set OMATABLEPLUS_TEST_DB=1 with zt-pg running")
class TunnelSessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["OMATABLEPLUS_SSH_KNOWN_HOSTS"] = os.path.join(self.tmp.name, "kh")
        self.sshd = Sshd()
        self.store = Store(os.path.join(self.tmp.name, "cfg"), Keyring(False))
        self.server = build(self.store)

    def tearDown(self):
        self.server.sessions.close_all()
        self.sshd.close()
        os.environ.pop("OMATABLEPLUS_SSH_KNOWN_HOSTS", None)
        self.tmp.cleanup()

    def test_postgres_over_ssh(self):
        from omatableplus import omadb
        pg = omadb.server("zt-pg")
        conn = self.store.save({"name": "via ssh", "engine": "postgresql", "host": "127.0.0.1", "port": pg["port"],
                                "user": pg["user"], "password": pg["password"], "database": "postgres",
                                "ssh": {"enabled": True, "host": "127.0.0.1", "port": self.sshd.port,
                                        "user": os.environ["USER"], "auth": "key", "key_path": self.sshd.key}})
        info = self.server.methods["session.open"](id=conn["id"])
        self.assertTrue(info["ssh"])
        self.assertTrue(info["version"].startswith("PostgreSQL"))
        session = self.server.sessions.get(info["session"])
        tunnel = session.tunnel
        self.assertTrue(tunnel.alive())
        self.server.methods["session.close"](session=info["session"])
        time.sleep(0.2)
        self.assertFalse(tunnel.alive())

    def test_valkey_over_ssh(self):
        from omatableplus import omadb
        vk = omadb.server("zt-vk")
        conn = self.store.save({"name": "kv via ssh", "engine": "valkey", "host": "127.0.0.1", "port": vk["port"],
                                "user": vk["user"], "password": vk["password"], "database": "9",
                                "ssh": {"enabled": True, "host": "127.0.0.1", "port": self.sshd.port,
                                        "user": os.environ["USER"], "auth": "key", "key_path": self.sshd.key}})
        info = self.server.methods["session.open"](id=conn["id"])
        self.assertTrue(info["ssh"])
        self.assertEqual(self.server.methods["redis.cmd"](session=info["session"], text="PING")["reply"], "PONG")


if __name__ == "__main__":
    unittest.main()
