"""Shared by the MCP tests: a client that drives McpServer message by message, and a throw-away SQLite config."""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omatableplus.__main__ import build
from omatableplus.mcp import McpServer
from omatableplus.store import Keyring, Store


class Client:
    """Drives an McpServer one JSON-RPC message at a time and reads back what it wrote."""

    def __init__(self, helper, write=False, destructive=False):
        self.out = io.StringIO()
        self.server = McpServer(helper, write=write, destructive=destructive, out=self.out)
        self.helper = helper
        self.seen = 0
        self.next_id = 0

    def replies(self):
        lines = self.out.getvalue().splitlines()[self.seen:]
        self.seen += len(lines)
        return [json.loads(line) for line in lines]

    def raw(self, line):
        self.server.handle(line)
        return self.replies()

    def send(self, method, params=None):
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        replies = self.raw(json.dumps(message))
        self.assertion_one(replies)
        return replies[0]

    def notify(self, method):
        return self.raw(json.dumps({"jsonrpc": "2.0", "method": method}))

    @staticmethod
    def assertion_one(replies):
        assert len(replies) == 1, replies

    def tool_names(self):
        return [t["name"] for t in self.send("tools/list")["result"]["tools"]]

    def call(self, tool, **arguments):
        """(is_error, payload) of a tools/call, the payload being the JSON the tool wrote."""
        reply = self.send("tools/call", {"name": tool, "arguments": arguments})
        result = reply["result"]
        return result["isError"], json.loads(result["content"][0]["text"])


class Fixture(unittest.TestCase):
    write = False
    destructive = False

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["OMATABLEPLUS_DBCTL"] = os.path.join(self.tmp.name, "none")
        self.path = os.path.join(self.tmp.name, "app.db")
        db = sqlite3.connect(self.path)
        db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        db.executemany("INSERT INTO notes (body) VALUES (?)", [("one",), ("two",), ("three",)])
        db.execute("CREATE TABLE big (id INTEGER PRIMARY KEY, label TEXT)")
        db.executemany("INSERT INTO big (label) VALUES (?)", [(f"row {i}",) for i in range(300)])
        db.execute("CREATE TABLE blobs (id INTEGER PRIMARY KEY, payload BLOB, story TEXT)")
        db.execute("INSERT INTO blobs (payload, story) VALUES (?, ?)", (b"\x00\x01\x02", "y" * 5000))
        db.commit()
        db.close()
        self.store = Store(os.path.join(self.tmp.name, "cfg"), Keyring(False))
        self.helper = build(self.store)
        self.local = self.store.save({"name": "Notes", "engine": "sqlite", "path": self.path, "tag": "local"})
        self.prod = self.store.save({"name": "Prod", "engine": "sqlite", "path": self.path, "tag": "production"})
        self.frozen = self.store.save({"name": "Frozen", "engine": "sqlite", "path": self.path, "tag": "local",
                                       "readonly": True})
        self.store.save({"name": "Legacy MySQL", "engine": "mysql", "host": "db.example", "port": 3306,
                         "user": "app", "password": "s3cret", "tag": "staging"})
        self.store.save({"name": "Cache", "engine": "redis", "host": "localhost", "tag": "local"})
        self.client = Client(self.helper, write=self.write, destructive=self.destructive)

    def tearDown(self):
        self.client.server.close()
        os.environ.pop("OMATABLEPLUS_DBCTL", None)
        self.tmp.cleanup()

    def scalar(self, sql):
        db = sqlite3.connect(self.path)
        try:
            return db.execute(sql).fetchone()[0]
        finally:
            db.close()
