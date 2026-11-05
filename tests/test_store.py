import json
import os
import stat
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omatableplus.protocol import Error
from omatableplus.store import Keyring, Store


class MemoryKeyring:
    def __init__(self, works=True):
        self.works = works
        self.items = {}

    def available(self):
        return self.works

    def set(self, conn_id, kind, secret):
        if not self.works:
            return False
        self.items[(conn_id, kind)] = secret
        return True

    def get(self, conn_id, kind):
        return self.items.get((conn_id, kind))

    def clear(self, conn_id, kind):
        self.items.pop((conn_id, kind), None)


PG = {"name": "Local PG", "engine": "postgresql", "host": "127.0.0.1", "port": 5432,
      "user": "postgres", "database": "postgres", "tag": "local", "color": "#336791"}


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "omatableplus")
        self.ring = MemoryKeyring()
        self.store = Store(self.dir, self.ring)

    def tearDown(self):
        self.tmp.cleanup()

    def raw(self):
        with open(os.path.join(self.dir, "connections.json")) as f:
            return json.load(f)

    def test_save_assigns_id_and_lists_without_secrets(self):
        saved = self.store.save({**PG, "password": "s3cret"})
        self.assertRegex(saved["id"], r"^[0-9a-f]{12}$")
        self.assertNotIn("password", saved)
        self.assertTrue(saved["has_password"])
        self.assertEqual(saved["password_storage"], "keyring")
        self.assertEqual(self.store.list(), [saved])
        self.assertNotIn("s3cret", json.dumps(self.raw()))
        self.assertEqual(self.ring.items[(saved["id"], "db")], "s3cret")

    def test_tls_and_mongo_auth_source_are_kept(self):
        saved = self.store.save({**PG, "tls": "require", "auth_source": "admin"})
        self.assertEqual(saved["tls"], "require")
        self.assertNotIn("auth_source", saved)
        mongo = self.store.save({"name": "M", "engine": "mongodb", "host": "h", "auth_source": "admin"})
        self.assertEqual(mongo["auth_source"], "admin")
        mongo = self.store.save({"name": "M", "engine": "mongodb", "host": "h", "id": mongo["id"]})
        self.assertEqual(mongo["auth_source"], "")

    def test_file_permissions(self):
        self.store.save(PG)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, "connections.json")).st_mode), 0o600)

    def test_password_unchanged_cleared_and_replaced(self):
        cid = self.store.save({**PG, "password": "one"})["id"]
        self.store.save({**PG, "id": cid, "name": "Renamed"})
        self.assertEqual(self.store.get(cid, secrets=True)["password"], "one")
        self.store.save({**PG, "id": cid, "password": "two"})
        self.assertEqual(self.store.get(cid, secrets=True)["password"], "two")
        saved = self.store.save({**PG, "id": cid, "password": ""})
        self.assertFalse(saved["has_password"])
        self.assertEqual(saved["password_storage"], "none")
        self.assertEqual(self.store.get(cid, secrets=True)["password"], "")

    def test_file_fallback_when_keyring_unavailable(self):
        store = Store(self.dir, MemoryKeyring(works=False))
        saved = store.save({**PG, "password": "plain"})
        self.assertEqual(saved["password_storage"], "file")
        self.assertEqual(store.get(saved["id"], secrets=True)["password"], "plain")
        self.assertEqual(self.raw()[0]["secrets"]["db"], "plain")

    def test_ssh_password_and_settings(self):
        ssh = {"enabled": True, "host": "bastion", "port": 22, "user": "me", "auth": "password", "key_path": ""}
        saved = self.store.save({**PG, "ssh": ssh, "ssh_password": "tunnelpw"})
        self.assertTrue(saved["has_ssh_password"])
        self.assertEqual(saved["ssh"], ssh)
        self.assertEqual(self.store.get(saved["id"], secrets=True)["ssh_password"], "tunnelpw")

    def test_delete_clears_secrets(self):
        cid = self.store.save({**PG, "password": "x", "ssh_password": "y",
                               "ssh": {"enabled": True, "host": "h", "port": 22, "user": "u", "auth": "password"}})["id"]
        self.store.delete(cid)
        self.assertEqual(self.store.list(), [])
        self.assertEqual(self.ring.items, {})
        with self.assertRaises(Error) as e:
            self.store.get(cid)
        self.assertEqual(e.exception.code, "notfound")

    def test_duplicate_copies_secrets(self):
        cid = self.store.save({**PG, "password": "dup"})["id"]
        copy = self.store.duplicate(cid)
        self.assertNotEqual(copy["id"], cid)
        self.assertEqual(copy["name"], "Local PG copy")
        self.assertEqual(self.store.get(copy["id"], secrets=True)["password"], "dup")
        self.assertEqual(len(self.store.list()), 2)

    def test_sqlite_needs_path_not_host(self):
        saved = self.store.save({"name": "Notes", "engine": "sqlite", "path": "/tmp/notes.db"})
        self.assertEqual(saved["path"], "/tmp/notes.db")
        with self.assertRaises(Error):
            self.store.save({"name": "Bad", "engine": "sqlite"})

    def test_defaults(self):
        saved = self.store.save({"name": "R", "engine": "redis", "host": "localhost"})
        self.assertEqual(saved["port"], 6379)
        self.assertEqual(saved["tag"], "local")
        self.assertRegex(saved["color"], r"^#[0-9a-f]{6}$")

    def test_validation(self):
        bad = [
            {**PG, "name": ""},
            {**PG, "name": "x" * 65},
            {**PG, "engine": "oracle"},
            {**PG, "port": 0},
            {**PG, "port": "abc"},
            {**PG, "host": ""},
            {**PG, "tag": "prod"},
            {**PG, "color": "red"},
            {**PG, "ssh": {"enabled": True, "host": "", "port": 22, "user": "u", "auth": "agent"}},
            {**PG, "ssh": {"enabled": True, "host": "h", "port": 22, "user": "u", "auth": "magic"}},
            {**PG, "id": "../../etc"},
        ]
        for conn in bad:
            with self.subTest(conn=conn):
                with self.assertRaises(Error) as e:
                    self.store.save(conn)
                self.assertEqual(e.exception.code, "invalid")

    def test_save_unknown_id_is_notfound(self):
        with self.assertRaises(Error) as e:
            self.store.save({**PG, "id": "abcdefabcdef"})
        self.assertEqual(e.exception.code, "notfound")


@unittest.skipUnless(Keyring().available(), "no Secret Service")
class RealKeyringTest(unittest.TestCase):
    def test_round_trip(self):
        ring = Keyring()
        cid = "test" + uuid.uuid4().hex[:8]
        try:
            self.assertTrue(ring.set(cid, "db", "pa ss'\"$word"))
            self.assertEqual(ring.get(cid, "db"), "pa ss'\"$word")
        finally:
            ring.clear(cid, "db")
        self.assertIsNone(ring.get(cid, "db"))

    def test_moves_a_password_saved_before_the_rename(self):
        ring = Keyring()
        cid = "test" + uuid.uuid4().hex[:8]
        legacy = ["app", "omatable", "conn", cid, "kind", "db"]
        try:
            ring._run(["store", "--label", "OmaTable test", *legacy], "old secret")
            self.assertEqual(ring.get(cid, "db"), "old secret")
            r = ring._run(["lookup", *legacy])
            self.assertNotEqual(r.returncode, 0)                         # gone from the old name
            r = ring._run(["lookup", "app", "omatableplus", "conn", cid, "kind", "db"])
            self.assertEqual(r.stdout, "old secret")                     # and kept under the new one
        finally:
            ring.clear(cid, "db")
        self.assertIsNone(ring.get(cid, "db"))

    def test_disabled(self):
        self.assertFalse(Keyring(enabled=False).available())


if __name__ == "__main__":
    unittest.main()
