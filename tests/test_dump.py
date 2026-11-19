"""Backup and restore. SQLite and the tool lookup always run; PostgreSQL and
MariaDB need the OmaDBngin servers zt-pg and zt-my (OMATABLEPLUS_TEST_DB=1).
"""

import os
import sqlite3
import stat
import sys
import tempfile
import threading
import types
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omatableplus import adapters, dump, omadb
from omatableplus.protocol import Error

LIVE = os.environ.get("OMATABLEPLUS_TEST_DB") == "1"


def live_conn(name, database):
    return {**omadb.server(name), "database": database}


class ToolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def fake(self, engine, version, name):
        d = os.path.join(self.tmp.name, "engines", engine, version, "bin")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        with open(path, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(path, 0o755)
        return path

    def test_find_tool_prefers_the_server_major(self):
        p16 = self.fake("postgresql", "16.15.1", "pg_dump")
        p18 = self.fake("postgresql", "18.6.1", "pg_dump")
        with mock.patch.object(dump, "ENGINE_DIRS", (os.path.join(self.tmp.name, "engines"),)), \
                mock.patch("shutil.which", return_value=None):
            self.assertEqual(dump.find_tool(["pg_dump"], (16, 4)), p16)
            self.assertEqual(dump.find_tool(["pg_dump"], (17, 0)), p18)   # no 17: the newest
            self.assertEqual(dump.find_tool(["pg_dump"]), p18)
            with self.assertRaises(Error) as e:
                dump.find_tool(["mariadb-dump"])
            self.assertEqual(e.exception.code, "missing")

    def test_job_reports_the_last_error_line_and_cancels(self):
        job = dump.Job()
        with self.assertRaises(Error) as e:
            job.run(["sh", "-c", "echo noise >&2; echo 'real problem' >&2; exit 3"])
        self.assertEqual(e.exception.code, "tool")
        self.assertIn("real problem", e.exception.message)
        job = dump.Job()
        threading.Timer(0.3, job.cancel).start()
        start = time.monotonic()
        with self.assertRaises(Error) as e:
            job.run(["sleep", "20"])
        self.assertEqual(e.exception.code, "cancelled")
        self.assertLess(time.monotonic() - start, 5)

    def test_pg_database_name_is_never_a_connection_string(self):
        # A database name comes from whoever created it; as `-d` libpq would read this one as a
        # connection string and send the saved password to evil.example.
        crafted = "host=evil.example port=1 dbname=postgres"
        tool = self.fake("postgresql", "17.2.1", "pg_dump")
        record = os.path.join(self.tmp.name, "record")
        with open(tool, "w") as f:
            f.write('#!/bin/sh\nprintf "%s\\n" "$@" > "$REC.args"\nenv | grep ^PG > "$REC.env"\n'
                    'while [ $# -gt 0 ]; do [ "$1" = -f ] && : > "$2"; shift; done\n')
        adapter = types.SimpleNamespace(engine="postgresql", database="app", version=lambda: "PostgreSQL 17.2",
                                        conn={"engine": "postgresql", "host": "db.local", "port": 5433,
                                              "user": "u", "password": "pw"})
        with mock.patch.object(dump, "find_tool", return_value=tool), \
                mock.patch.dict(os.environ, {"REC": record, "PGHOSTADDR": "10.9.9.9", "PGSERVICE": "x"}):
            dump.backup(adapter, dump.Job(), os.path.join(self.tmp.name, "out.sql"), database=crafted)
        with open(record + ".args") as f:
            args = f.read().splitlines()
        with open(record + ".env") as f:
            env = dict(line.split("=", 1) for line in f.read().splitlines())
        self.assertNotIn(crafted, args)
        for flag in ("-d", "-h", "-U", "-p", "--dbname", "--host"):
            self.assertNotIn(flag, args)
        self.assertEqual(env["PGDATABASE"], crafted)
        self.assertEqual((env["PGHOST"], env["PGPORT"], env["PGUSER"]), ("db.local", "5433", "u"))
        self.assertNotIn("PGHOSTADDR", env)   # an inherited one would win over PGHOST
        self.assertNotIn("PGSERVICE", env)

    def test_sqlite_backup(self):
        src = os.path.join(self.tmp.name, "a.db")
        with sqlite3.connect(src) as db:
            db.execute("CREATE TABLE t (x)")
            db.execute("INSERT INTO t VALUES (1), (2)")
        a = adapters.create({"engine": "sqlite", "path": src})
        a.connect()
        self.addCleanup(a.close)
        out = os.path.join(self.tmp.name, "b.db")
        r = dump.backup(a, dump.Job(), out)
        self.assertEqual(r["path"], out)
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        with sqlite3.connect(out) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM t").fetchone()[0], 2)
        with self.assertRaises(Error) as e:
            dump.backup(a, dump.Job(), out)
        self.assertEqual(e.exception.code, "exists")
        with self.assertRaises(Error):
            dump.restore(a, dump.Job(), out)
        self.assertEqual([f for f in os.listdir(self.tmp.name) if f.startswith(".omatableplus-")], [])


class LiveMixin:
    def round_trip(self, a, admin, create, drop):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        out = os.path.join(tmp.name, "zt.sql")
        r = dump.backup(a, dump.Job(), out)
        self.assertGreater(r["bytes"], 100)
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        with open(out) as f:
            self.assertIn("people", f.read())
        admin.query(drop)
        admin.query(create)
        r = dump.restore(a, dump.Job(), out, "zt_restored")
        self.assertEqual(r["database"], "zt_restored")
        return out


@unittest.skipUnless(LIVE, "set OMATABLEPLUS_TEST_DB=1 with zt-pg running")
class PostgresDumpTest(LiveMixin, unittest.TestCase):
    def test_round_trip(self):
        admin = adapters.create(live_conn("zt-pg", "postgres"))
        admin.connect()
        self.addCleanup(admin.close)
        for db in ("zt_dump", "zt_restored"):
            admin.query(f"DROP DATABASE IF EXISTS {db}")
        admin.query("CREATE DATABASE zt_dump")
        a = adapters.create(live_conn("zt-pg", "zt_dump"))
        a.connect()
        self.addCleanup(a.close)
        a.query("CREATE TABLE people (id serial PRIMARY KEY, name text)")
        a.query("INSERT INTO people (name) VALUES ('ann'), ('bob')")
        self.round_trip(a, admin, "CREATE DATABASE zt_restored", "DROP DATABASE IF EXISTS zt_restored")
        b = adapters.create(live_conn("zt-pg", "zt_restored"))
        b.connect()
        self.assertEqual(b.query("SELECT count(*) FROM people")[1][0][0], 2)
        b.close()
        # a failing restore changes nothing: the dump's tables already exist
        with self.assertRaises(Error) as e:
            dump.restore(a, dump.Job(), self._again(a), "zt_restored")
        self.assertEqual(e.exception.code, "tool")
        a.close()
        for db in ("zt_dump", "zt_restored"):
            admin.query(f"DROP DATABASE IF EXISTS {db}")

    def _again(self, a):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return dump.backup(a, dump.Job(), os.path.join(tmp.name, "again.sql"))["path"]


@unittest.skipUnless(LIVE, "set OMATABLEPLUS_TEST_DB=1 with zt-my running")
class MariaDBDumpTest(LiveMixin, unittest.TestCase):
    def test_round_trip(self):
        admin = adapters.create(live_conn("zt-my", ""))
        admin.connect()
        self.addCleanup(admin.close)
        for db in ("zt_dump", "zt_restored"):
            admin.query(f"DROP DATABASE IF EXISTS {db}")
        admin.query("CREATE DATABASE zt_dump")
        a = adapters.create(live_conn("zt-my", "zt_dump"))
        a.connect()
        self.addCleanup(a.close)
        a.query("CREATE TABLE people (id int AUTO_INCREMENT PRIMARY KEY, name varchar(20))")
        a.query("INSERT INTO people (name) VALUES ('ann'), ('bob')")
        self.round_trip(a, admin, "CREATE DATABASE zt_restored", "DROP DATABASE IF EXISTS zt_restored")
        self.assertEqual(admin.query("SELECT count(*) FROM zt_restored.people")[1][0][0], 2)
        for db in ("zt_dump", "zt_restored"):
            admin.query(f"DROP DATABASE IF EXISTS {db}")




class MySqlOptionFile(unittest.TestCase):
    def test_password_is_double_quoted_with_escapes(self):
        from omatableplus import dump
        self.assertEqual(dump._my_quote("plain"), '"plain"')
        self.assertEqual(dump._my_quote('a #b'), '"a #b"')
        self.assertEqual(dump._my_quote('q"uote'), '"q\\"uote"')
        self.assertEqual(dump._my_quote("back\\slash"), '"back\\\\slash"')
        self.assertEqual(dump._my_quote(" lead and trail "), '" lead and trail "')
        self.assertEqual(dump._my_quote("no\r\nnewline"), '"nonewline"')

    def test_the_option_file_holds_the_quoted_password_and_is_private(self):
        import os
        import stat
        from omatableplus import dump
        with dump._my_options({"host": "h", "user": "u", "password": "p #1"}, "mysql") as path:
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertIn('password="p #1"', open(path).read())
        self.assertFalse(os.path.exists(path))


if __name__ == "__main__":
    unittest.main()