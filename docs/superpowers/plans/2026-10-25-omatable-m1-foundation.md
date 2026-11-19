# OmaTable M1: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working OmaTable plugin skeleton: bar icon opens a resizable window; a Python helper bootstraps pinned drivers, speaks the JSON-lines protocol, and manages saved connections (keyring-backed passwords) plus live OmaDB servers; the welcome window lists, creates, edits, duplicates and deletes connections.

**Architecture:** `tablectl` (stdlib-only bootstrap) creates/validates a venv from `requirements.lock`, then execs `python -m omatable` from that venv. The `omatable` package is a threaded JSON-lines server; `store.py` owns connections and secrets, `omadb.py` reads `dbctl state`. QML side: `Panel.qml` hosts a `FloatingWindow`, `qml/Helper.qml` wraps the helper `Process`, `qml/Welcome.qml` + `qml/ConnectionForm.qml` render the UI.

**Tech Stack:** Python 3.14 (Arch system python, stdlib `venv`, `unittest`), pip `--require-hashes`, `secret-tool` (libsecret), Quickshell QML (`Quickshell`, `Quickshell.Io`, `qs.Commons`, `qs.Ui`).

**Spec:** `docs/superpowers/specs/2026-10-25-omatable-design.md`

## Global Constraints

- Plugin id `blackcode.omatable`, name "OmaTable", version `0.1.0` for M1, author "BlackCode", license MIT.
- Plugin directory: `~/.config/omarchy/plugins/blackcode.omatable/`. Never edit `/usr/share/omarchy/`.
- Connections file: `~/.config/omatable/connections.json`, mode 600, directory mode 700. Override with `OMATABLE_CONFIG` (directory).
- Data directory: `~/.local/share/omatable/` (venv lives in `venv/`). Override with `OMATABLE_DATA`.
- Keyring attributes: `app=omatable conn=<id> kind=db|ssh`. `OMATABLE_NO_KEYRING=1` forces the file fallback.
- Drivers: `psycopg`, `psycopg-binary`, `PyMySQL`, `redis`, `pymongo`, `dnspython`, `typing-extensions`, installed only with `--require-hashes --no-deps --only-binary :all:`.
- Engines: `postgresql mysql mariadb sqlite redis valkey mongodb`. Tags: `local development staging production`.
- Protocol: one JSON object per line; request `{id, method, params}`, reply `{id, result}` or `{id, error: {code, message}}`, event `{event, ...}`. Error codes: `auth network tunnel sql notfound readonly drivers invalid protocol internal`.
- Passwords never appear in argv, environment, `conn.list` output, or logs.
- Commits: `git -c user.name=BlackCode -c user.email=aalsheraidah@gmail.com commit`, message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- After editing QML, reload with `omarchy restart shell` (hot reload is unreliable).

## File Structure

| File | Responsibility |
|---|---|
| `manifest.json` | plugin manifest: kinds `bar-widget` + `panel` |
| `Bar.qml` | bar icon; click toggles the window via `omarchy-shell shell toggle` |
| `Panel.qml` | panel lifecycle (`open/close/requestClose`), `FloatingWindow`, splash vs welcome |
| `qml/Helper.qml` | owns the helper `Process`; `request(method, params, cb)`; status + events |
| `qml/Welcome.qml` | connection list (search, groups, OmaDB) + form host |
| `qml/ConnectionForm.qml` | create/edit form with SSH section |
| `qml/Field.qml` | labelled text input used by the form |
| `tablectl` | stdlib bootstrap: venv check/build, then exec the server |
| `omatable/__init__.py` | version constant |
| `omatable/__main__.py` | wires methods into the server and serves stdin |
| `omatable/protocol.py` | `Server`, `Error`, JSON encoding |
| `omatable/store.py` | `Keyring`, `Store`: validation, persistence, secrets |
| `omatable/omadb.py` | `servers()` from `dbctl state` |
| `requirements.lock` | pinned driver wheels with sha256 hashes |
| `tools/update-requirements-lock` | regenerates the lock from PyPI |
| `tests/test_protocol.py`, `tests/test_store.py`, `tests/test_omadb.py`, `tests/test_bootstrap.py` | unittest suites |
| `README.md`, `LICENSE`, `.gitignore` | docs |

Run all unit tests with: `cd ~/.config/omarchy/plugins/blackcode.omatable && /usr/bin/python3 -m unittest discover -s tests -v`

---

### Task 1: Protocol server

**Files:**
- Create: `omatable/__init__.py`, `omatable/protocol.py`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Produces: `omatable.protocol.Error(code: str, message: str)`; `Server(out)` with `.method(name)` decorator (handler called as `fn(**params)`, returns JSON-able), `.send(obj)`, `.event(name, **fields)`, `.handle(line: str)`, `.serve(inp)`, `.close()`; `omatable.__version__ = "0.1.0"`.

- [ ] **Step 1: Write the failing test**

`tests/test_protocol.py`:
```python
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omatable.protocol import Error, Server


def run(lines, register):
    out = io.StringIO()
    server = Server(out)
    register(server)
    server.serve(io.StringIO("".join(json.dumps(l) + "\n" if not isinstance(l, str) else l for l in lines)))
    return [json.loads(x) for x in out.getvalue().splitlines()]


class ProtocolTest(unittest.TestCase):
    def test_result(self):
        def reg(s):
            s.method("math.add")(lambda a, b: a + b)
        replies = run([{"id": 1, "method": "math.add", "params": {"a": 2, "b": 3}}], reg)
        self.assertEqual(replies, [{"id": 1, "result": 5}])

    def test_typed_error(self):
        def reg(s):
            def boom():
                raise Error("invalid", "bad input")
            s.method("boom")(boom)
        replies = run([{"id": 2, "method": "boom"}], reg)
        self.assertEqual(replies, [{"id": 2, "error": {"code": "invalid", "message": "bad input"}}])

    def test_unexpected_exception_is_internal(self):
        def reg(s):
            s.method("crash")(lambda: 1 / 0)
        replies = run([{"id": 3, "method": "crash"}], reg)
        self.assertEqual(replies[0]["error"]["code"], "internal")
        self.assertIn("division", replies[0]["error"]["message"])

    def test_unknown_method(self):
        replies = run([{"id": 4, "method": "nope"}], lambda s: None)
        self.assertEqual(replies[0]["error"]["code"], "notfound")

    def test_bad_json_and_bad_params(self):
        def reg(s):
            s.method("one")(lambda a: a)
        replies = run(["not json\n", {"id": 5, "method": "one", "params": {"zzz": 1}}], reg)
        self.assertEqual(replies[0], {"id": None, "error": {"code": "protocol", "message": "invalid JSON"}})
        self.assertEqual(replies[1]["error"]["code"], "invalid")

    def test_blank_lines_ignored_and_concurrent_replies_all_arrive(self):
        def reg(s):
            s.method("echo")(lambda v: v)
        lines = ["\n"] + [{"id": i, "method": "echo", "params": {"v": i}} for i in range(50)]
        replies = run(lines, reg)
        self.assertEqual(sorted(r["result"] for r in replies), list(range(50)))

    def test_event(self):
        out = io.StringIO()
        Server(out).event("progress", text="hi", pct=10)
        self.assertEqual(json.loads(out.getvalue()), {"event": "progress", "text": "hi", "pct": 10})

    def test_bytes_and_dates_encode(self):
        import datetime
        import decimal
        out = io.StringIO()
        s = Server(out)
        s.send({"b": b"\x00\x01", "d": datetime.date(2026, 1, 2), "n": decimal.Decimal("1.50")})
        self.assertEqual(json.loads(out.getvalue()),
                         {"b": {"$t": "bytes", "v": "AAE="}, "d": "2026-01-02", "n": "1.50"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m unittest tests.test_protocol -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'omatable'`

- [ ] **Step 3: Write minimal implementation**

`omatable/__init__.py`:
```python
"""OmaTable helper: talks to databases for the OmaTable window."""

__version__ = "0.1.0"
```

`omatable/protocol.py`:
```python
"""JSON-lines protocol: one request, reply or event per line.

  request  {"id": 7, "method": "conn.list", "params": {...}}
  reply    {"id": 7, "result": ...}  or  {"id": 7, "error": {"code": ..., "message": ...}}
  event    {"event": "progress", ...}

Requests run on a thread pool so a slow query never blocks the others;
replies are written whole under a lock, so lines never interleave.
"""

import base64
import datetime
import decimal
import json
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor


class Error(Exception):
    """An error the window shows to the user: a stable code plus a message."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def encode(value):
    """JSON fallback for values databases hand back."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$t": "bytes", "v": base64.b64encode(bytes(value)).decode()}
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return str(value)
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    return str(value)


class Server:
    def __init__(self, out=None, workers=8):
        self.out = out or sys.stdout
        self.methods = {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=workers)

    def method(self, name):
        def register(fn):
            self.methods[name] = fn
            return fn
        return register

    def send(self, obj):
        line = json.dumps(obj, default=encode, ensure_ascii=False)
        with self.lock:
            self.out.write(line + "\n")
            self.out.flush()

    def event(self, name, **fields):
        self.send({"event": name, **fields})

    def reply_error(self, rid, code, message):
        self.send({"id": rid, "error": {"code": code, "message": message}})

    def handle(self, line):
        try:
            req = json.loads(line)
            if not isinstance(req, dict):
                raise ValueError
        except ValueError:
            self.reply_error(None, "protocol", "invalid JSON")
            return
        rid = req.get("id")
        fn = self.methods.get(req.get("method"))
        if fn is None:
            self.reply_error(rid, "notfound", f"unknown method {req.get('method')!r}")
            return
        params = req.get("params") or {}
        if not isinstance(params, dict):
            self.reply_error(rid, "invalid", "params must be an object")
            return
        self.pool.submit(self.call, rid, fn, params)

    def call(self, rid, fn, params):
        try:
            result = fn(**params)
        except Error as e:
            self.reply_error(rid, e.code, e.message)
        except TypeError as e:
            self.reply_error(rid, "invalid", str(e))
        except Exception as e:  # noqa: BLE001 - every failure must reach the window
            self.reply_error(rid, "internal", f"{type(e).__name__}: {e}")
        else:
            self.send({"id": rid, "result": result})

    def serve(self, inp=None):
        for line in inp or sys.stdin:
            if line.strip():
                self.handle(line)
        self.close()

    def close(self):
        self.pool.shutdown(wait=True)
```

Note: a `TypeError` raised *inside* a handler is also reported as `invalid`; handlers must raise `Error` for user-facing failures and let real bugs surface as other exception types.

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m unittest tests.test_protocol -v`
Expected: 8 tests, OK

- [ ] **Step 5: Commit**

```bash
git add omatable tests/test_protocol.py
git commit -m "Add the JSON-lines protocol server"
```

---

### Task 2: Connection store and keyring

**Files:**
- Create: `omatable/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `omatable.protocol.Error`.
- Produces:
  - `Keyring(enabled: bool = True)` with `.available() -> bool`, `.set(conn_id, kind, secret) -> bool`, `.get(conn_id, kind) -> str | None`, `.clear(conn_id, kind) -> None`.
  - `Store(directory: str, keyring: Keyring)` with `.list() -> list[dict]` (public, no secrets), `.get(conn_id, secrets=False) -> dict`, `.save(conn: dict) -> dict` (public), `.delete(conn_id) -> None`, `.duplicate(conn_id) -> dict` (public).
  - Public connection dict keys: `id name engine host port user database color tag path tls ssh` plus `has_password: bool`, `password_storage: "keyring" | "file" | "none"`, `has_ssh_password: bool`.
  - `.get(id, secrets=True)` adds `password` and `ssh_password` (str, possibly "").
  - Save semantics: key `password` absent → unchanged, `""` → cleared, string → set. Same for `ssh_password`.
  - Constants `ENGINES`, `TAGS`, `DEFAULT_PORTS`, `default_dir() -> str`.

- [ ] **Step 1: Write the failing test**

`tests/test_store.py`:
```python
import json
import os
import stat
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omatable.protocol import Error
from omatable.store import Keyring, Store


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
        self.dir = os.path.join(self.tmp.name, "omatable")
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

    def test_disabled(self):
        self.assertFalse(Keyring(enabled=False).available())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m unittest tests.test_store -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'omatable.store'`

- [ ] **Step 3: Write minimal implementation**

`omatable/store.py`:
```python
"""Saved connections: connections.json (mode 600) plus passwords in the keyring.

Passwords go to the Secret Service through `secret-tool`, fed on stdin so they
never show up in argv. When no Secret Service answers they are kept in the
same 600-mode file, and the window says so.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import uuid

from .protocol import Error

ENGINES = ("postgresql", "mysql", "mariadb", "sqlite", "redis", "valkey", "mongodb")
TAGS = ("local", "development", "staging", "production")
DEFAULT_PORTS = {"postgresql": 5432, "mysql": 3306, "mariadb": 3306, "redis": 6379,
                 "valkey": 6379, "mongodb": 27017}
ENGINE_COLORS = {"postgresql": "#336791", "mysql": "#00758f", "mariadb": "#c0765a", "sqlite": "#4b8bbe",
                 "redis": "#d82c20", "valkey": "#6983ff", "mongodb": "#4faa41"}
SSH_AUTH = ("agent", "key", "password")
KINDS = ("db", "ssh")
ID_RE = re.compile(r"^[0-9a-f]{12}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def default_dir():
    if os.environ.get("OMATABLE_CONFIG"):
        return os.environ["OMATABLE_CONFIG"]
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "omatable")


class Keyring:
    """The Secret Service through secret-tool; every call is best effort."""

    def __init__(self, enabled=True):
        self.enabled = bool(enabled) and os.environ.get("OMATABLE_NO_KEYRING") != "1" \
            and shutil.which("secret-tool") is not None
        self._available = None

    def _run(self, args, secret=None):
        try:
            return subprocess.run(["secret-tool", *args], input=secret, capture_output=True,
                                  text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None

    def available(self):
        if not self.enabled:
            return False
        if self._available is None:
            probe = ["app", "omatable", "conn", "probe", "kind", "probe"]
            ok = self._run(["store", "--label", "OmaTable probe", *probe], "probe")
            self._available = bool(ok and ok.returncode == 0)
            if self._available:
                self._run(["clear", *probe])
        return self._available

    def set(self, conn_id, kind, secret):
        if not self.available():
            return False
        r = self._run(["store", "--label", f"OmaTable {kind} password ({conn_id})",
                       "app", "omatable", "conn", conn_id, "kind", kind], secret)
        return bool(r and r.returncode == 0)

    def get(self, conn_id, kind):
        if not self.available():
            return None
        r = self._run(["lookup", "app", "omatable", "conn", conn_id, "kind", kind])
        return r.stdout if r and r.returncode == 0 else None

    def clear(self, conn_id, kind):
        if self.available():
            self._run(["clear", "app", "omatable", "conn", conn_id, "kind", kind])


def _str(conn, key, limit=255):
    value = conn.get(key)
    value = "" if value is None else str(value).strip()
    if len(value) > limit:
        raise Error("invalid", f"{key}: up to {limit} characters")
    return value


def _port(value, what="port"):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise Error("invalid", f"{what} must be a number") from None
    if not 1 <= port <= 65535:
        raise Error("invalid", f"{what} must be between 1 and 65535")
    return port


def validate(conn):
    """The stored (non-secret) fields of a connection, or Error('invalid')."""
    name = _str(conn, "name", 64)
    if not name:
        raise Error("invalid", "name is required")
    engine = conn.get("engine")
    if engine not in ENGINES:
        raise Error("invalid", f"engine must be one of {', '.join(ENGINES)}")
    tag = conn.get("tag") or "local"
    if tag not in TAGS:
        raise Error("invalid", f"tag must be one of {', '.join(TAGS)}")
    color = (conn.get("color") or ENGINE_COLORS[engine]).lower()
    if not COLOR_RE.match(color):
        raise Error("invalid", "color must look like #rrggbb")
    out = {"name": name, "engine": engine, "tag": tag, "color": color,
           "host": "", "port": 0, "user": _str(conn, "user", 128), "database": _str(conn, "database", 128),
           "path": "", "tls": conn.get("tls") or "prefer", "ssh": None}
    if out["tls"] not in ("disable", "prefer", "require"):
        raise Error("invalid", "tls must be disable, prefer or require")
    if engine == "sqlite":
        out["path"] = _str(conn, "path", 4096)
        if not out["path"]:
            raise Error("invalid", "SQLite needs a database file")
        return out
    out["host"] = _str(conn, "host")
    if not out["host"]:
        raise Error("invalid", "host is required")
    port = conn.get("port")
    out["port"] = _port(DEFAULT_PORTS[engine] if port in (None, "") else port)
    ssh = conn.get("ssh")
    if ssh and ssh.get("enabled"):
        tunnel = {"enabled": True, "host": _str(ssh, "host"), "port": _port(ssh.get("port") or 22, "SSH port"),
                  "user": _str(ssh, "user", 128), "auth": ssh.get("auth") or "agent",
                  "key_path": _str(ssh, "key_path", 4096)}
        if not tunnel["host"] or not tunnel["user"]:
            raise Error("invalid", "SSH needs a host and a user")
        if tunnel["auth"] not in SSH_AUTH:
            raise Error("invalid", f"SSH auth must be one of {', '.join(SSH_AUTH)}")
        if tunnel["auth"] == "key" and not tunnel["key_path"]:
            raise Error("invalid", "SSH key auth needs a key file")
        out["ssh"] = tunnel
    return out


class Store:
    def __init__(self, directory=None, keyring=None):
        self.dir = directory or default_dir()
        self.path = os.path.join(self.dir, "connections.json")
        self.ring = keyring if keyring is not None else Keyring()
        self.lock = threading.Lock()

    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return []
        except ValueError:
            raise Error("internal", f"{self.path} is not valid JSON") from None
        return data if isinstance(data, list) else []

    def _write(self, conns):
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        os.chmod(self.dir, 0o700)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(conns, f, indent=2)
        os.replace(tmp, self.path)

    def _secret(self, conn, kind):
        file_secret = (conn.get("secrets") or {}).get(kind)
        if file_secret is not None:
            return file_secret, "file"
        if (conn.get("in_keyring") or {}).get(kind):
            value = self.ring.get(conn["id"], kind)
            return (value or ""), ("keyring" if value else "none")
        return "", "none"

    def _put_secret(self, conn, kind, value):
        secrets = conn.setdefault("secrets", {})
        in_ring = conn.setdefault("in_keyring", {})
        secrets.pop(kind, None)
        if in_ring.pop(kind, None):
            self.ring.clear(conn["id"], kind)
        if not value:
            return
        if self.ring.set(conn["id"], kind, value):
            in_ring[kind] = True
        else:
            secrets[kind] = value

    def _public(self, conn):
        out = {k: v for k, v in conn.items() if k not in ("secrets", "in_keyring")}
        _, db_storage = self._secret(conn, "db")
        _, ssh_storage = self._secret(conn, "ssh")
        out["has_password"] = db_storage != "none"
        out["password_storage"] = db_storage
        out["has_ssh_password"] = ssh_storage != "none"
        return out

    @staticmethod
    def _find(conns, conn_id):
        for c in conns:
            if c["id"] == conn_id:
                return c
        raise Error("notfound", "connection not found")

    def list(self):
        with self.lock:
            return [self._public(c) for c in self._load()]

    def get(self, conn_id, secrets=False):
        with self.lock:
            conn = self._find(self._load(), conn_id)
            out = self._public(conn)
            if secrets:
                out["password"] = self._secret(conn, "db")[0]
                out["ssh_password"] = self._secret(conn, "ssh")[0]
            return out

    def save(self, conn):
        fields = validate(conn)
        conn_id = conn.get("id")
        if conn_id is not None and not ID_RE.match(str(conn_id)):
            raise Error("invalid", "bad connection id")
        with self.lock:
            conns = self._load()
            if conn_id:
                stored = self._find(conns, conn_id)
                stored.update(fields)
            else:
                stored = {"id": uuid.uuid4().hex[:12], **fields}
                conns.append(stored)
            if "password" in conn:
                self._put_secret(stored, "db", conn["password"])
            if "ssh_password" in conn:
                self._put_secret(stored, "ssh", conn["ssh_password"])
            self._write(conns)
            return self._public(stored)

    def delete(self, conn_id):
        with self.lock:
            conns = self._load()
            conn = self._find(conns, conn_id)
            for kind in KINDS:
                if (conn.get("in_keyring") or {}).get(kind):
                    self.ring.clear(conn_id, kind)
            self._write([c for c in conns if c["id"] != conn_id])

    def duplicate(self, conn_id):
        source = self.get(conn_id, secrets=True)
        copy = {k: v for k, v in source.items() if k != "id"}
        copy["name"] = (source["name"] + " copy")[:64]
        return self.save(copy)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m unittest tests.test_store -v`
Expected: all StoreTest tests OK; RealKeyringTest runs (Secret Service is active on this machine) and passes.

- [ ] **Step 5: Commit**

```bash
git add omatable/store.py tests/test_store.py
git commit -m "Store connections with passwords in the keyring"
```

---

### Task 3: OmaDB servers

**Files:**
- Create: `omatable/omadb.py`
- Test: `tests/test_omadb.py`

**Interfaces:**
- Consumes: `omatable.store.DEFAULT_PORTS`, `ENGINE_COLORS`.
- Produces: `omadb.dbctl_path() -> str | None`; `omadb.servers(secrets=False) -> list[dict]`, each with `id` (`"omadb:<name>"`), `name`, `engine`, `version`, `host` `"127.0.0.1"`, `port`, `user`, `database`, `tag` `"local"`, `color`, `mode`, `running`, `source` `"omadb"`, `has_password`; with `secrets=True` also `password`. `omadb.server(name) -> dict` (with secrets) raising `Error("notfound")`.

- [ ] **Step 1: Write the failing test**

`tests/test_omadb.py`:
```python
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omatable import omadb
from omatable.protocol import Error

STATE = {"services": [
    {"name": "app", "engine": "postgresql", "version": "17", "port": 5433, "mode": "native",
     "running": True, "user": "dev", "password": "pw"},
    {"name": "cache", "engine": "valkey", "version": "8", "port": 6380, "mode": "docker",
     "running": False, "user": "", "password": ""},
    {"name": "shop", "engine": "mysql", "version": "8.4", "port": 3307, "mode": "native",
     "running": True, "user": "root", "password": ""},
]}


class OmaDBTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctl = os.path.join(self.tmp.name, "dbctl")
        with open(self.ctl, "w") as f:
            f.write("#!/bin/sh\n[ \"$1\" = state ] && cat <<'EOF'\n" + json.dumps(STATE) + "\nEOF\n")
        os.chmod(self.ctl, 0o755)
        os.environ["OMATABLE_DBCTL"] = self.ctl

    def tearDown(self):
        os.environ.pop("OMATABLE_DBCTL", None)
        self.tmp.cleanup()

    def test_servers_public(self):
        servers = omadb.servers()
        self.assertEqual([s["id"] for s in servers], ["omadb:app", "omadb:cache", "omadb:shop"])
        app = servers[0]
        self.assertEqual(app["host"], "127.0.0.1")
        self.assertEqual(app["port"], 5433)
        self.assertEqual(app["database"], "postgres")
        self.assertEqual(app["tag"], "local")
        self.assertTrue(app["has_password"])
        self.assertNotIn("password", app)
        self.assertEqual(servers[1]["database"], "0")
        self.assertEqual(servers[2]["database"], "")

    def test_server_with_secret(self):
        self.assertEqual(omadb.server("app")["password"], "pw")
        with self.assertRaises(Error) as e:
            omadb.server("missing")
        self.assertEqual(e.exception.code, "notfound")

    def test_missing_dbctl_means_no_servers(self):
        os.environ["OMATABLE_DBCTL"] = os.path.join(self.tmp.name, "nope")
        self.assertEqual(omadb.servers(), [])

    def test_broken_output_means_no_servers(self):
        with open(self.ctl, "w") as f:
            f.write("#!/bin/sh\necho garbage\n")
        self.assertEqual(omadb.servers(), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m unittest tests.test_omadb -v`
Expected: FAIL with `ImportError: cannot import name 'omadb'`

- [ ] **Step 3: Write minimal implementation**

`omatable/omadb.py`:
```python
"""Servers run by OmaDB (blackcode.omadb), read live from `dbctl state`."""

import json
import os
import subprocess

from .protocol import Error
from .store import ENGINE_COLORS

DEFAULT_DATABASE = {"postgresql": "postgres", "redis": "0", "valkey": "0", "mongodb": "admin"}


def dbctl_path():
    path = os.environ.get("OMATABLE_DBCTL") or os.path.expanduser(
        "~/.config/omarchy/plugins/blackcode.omadb/dbctl")
    return path if os.access(path, os.X_OK) else None


def _state():
    ctl = dbctl_path()
    if not ctl:
        return []
    try:
        r = subprocess.run([ctl, "state"], capture_output=True, text=True, timeout=20)
        services = json.loads(r.stdout).get("services") or []
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return []
    return [s for s in services if isinstance(s, dict) and s.get("name")]


def _entry(s, secrets):
    engine = s.get("engine", "")
    out = {"id": "omadb:" + s["name"], "name": s["name"], "engine": engine,
           "version": str(s.get("version", "")), "host": "127.0.0.1", "port": int(s.get("port") or 0),
           "user": s.get("user") or "", "database": DEFAULT_DATABASE.get(engine, ""),
           "tag": "local", "color": ENGINE_COLORS.get(engine, "#888888"), "mode": s.get("mode", ""),
           "running": bool(s.get("running")), "source": "omadb", "path": "", "tls": "prefer",
           "ssh": None, "has_password": bool(s.get("password"))}
    if secrets:
        out["password"] = s.get("password") or ""
    return out


def servers(secrets=False):
    return [_entry(s, secrets) for s in _state()]


def server(name):
    for s in _state():
        if s["name"] == name:
            return _entry(s, True)
    raise Error("notfound", f"OmaDB server {name!r} not found")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m unittest tests.test_omadb -v`
Expected: 4 tests, OK

- [ ] **Step 5: Commit**

```bash
git add omatable/omadb.py tests/test_omadb.py
git commit -m "Read OmaDB servers from dbctl state"
```

---

### Task 4: Server entry point with connection methods

**Files:**
- Create: `omatable/__main__.py`
- Modify: `tests/test_protocol.py` (append an end-to-end class)

**Interfaces:**
- Consumes: `Server`, `Store`, `Keyring`, `omadb.servers`.
- Produces: `omatable.__main__.build(store) -> Server` registering `conn.list` → `{"connections": [...], "omadb": [...], "keyring": bool}`, `conn.save(conn)` → public dict, `conn.delete(id)` → `{"ok": true}`, `conn.duplicate(id)` → public dict, `app.version` → `{"version": "0.1.0"}`. `main()` sends `{"event": "ready", "version": ...}` then serves stdin. Running `python3 -m omatable` (with the plugin dir on `PYTHONPATH`) starts it.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_protocol.py` (before the `if __name__` block):
```python
import subprocess
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


class EndToEndTest(unittest.TestCase):
    def test_conn_methods_over_stdio(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "PYTHONPATH": ROOT, "OMATABLE_CONFIG": tmp, "OMATABLE_NO_KEYRING": "1",
                   "OMATABLE_DBCTL": os.path.join(tmp, "none")}
            requests = [
                {"id": 1, "method": "conn.save", "params": {"conn": {
                    "name": "PG", "engine": "postgresql", "host": "localhost", "password": "pw"}}},
            ]
            out = subprocess.run([sys.executable, "-m", "omatable"], input="".join(json.dumps(r) + "\n" for r in requests),
                                 capture_output=True, text=True, env=env, timeout=30).stdout.splitlines()
            msgs = [json.loads(l) for l in out]
            self.assertEqual(msgs[0]["event"], "ready")
            saved = msgs[1]["result"]
            self.assertEqual(saved["password_storage"], "file")
            requests = [
                {"id": 2, "method": "conn.duplicate", "params": {"id": saved["id"]}},
            ]
            out = subprocess.run([sys.executable, "-m", "omatable"], input="".join(json.dumps(r) + "\n" for r in requests),
                                 capture_output=True, text=True, env=env, timeout=30).stdout.splitlines()
            copy = json.loads(out[1])["result"]
            requests = [
                {"id": 3, "method": "conn.delete", "params": {"id": copy["id"]}},
            ]
            subprocess.run([sys.executable, "-m", "omatable"], input="".join(json.dumps(r) + "\n" for r in requests),
                           capture_output=True, text=True, env=env, timeout=30)
            out = subprocess.run([sys.executable, "-m", "omatable"], input=json.dumps({"id": 4, "method": "conn.list"}) + "\n",
                                 capture_output=True, text=True, env=env, timeout=30).stdout.splitlines()
            listing = json.loads(out[1])["result"]
            self.assertEqual([c["name"] for c in listing["connections"]], ["PG"])
            self.assertEqual(listing["omadb"], [])
            self.assertFalse(listing["keyring"])
            self.assertNotIn("pw", out[1])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/usr/bin/python3 -m unittest tests.test_protocol.EndToEndTest -v`
Expected: FAIL (`No module named omatable.__main__`; first line is not a ready event)

- [ ] **Step 3: Write minimal implementation**

`omatable/__main__.py`:
```python
"""Entry point: `python -m omatable` serves the protocol on stdin/stdout."""

from . import __version__, omadb
from .protocol import Server
from .store import Store


def build(store, server=None):
    server = server or Server()

    @server.method("app.version")
    def version():
        return {"version": __version__}

    @server.method("conn.list")
    def conn_list():
        return {"connections": store.list(), "omadb": omadb.servers(), "keyring": store.ring.available()}

    @server.method("conn.save")
    def conn_save(conn):
        return store.save(conn)

    @server.method("conn.delete")
    def conn_delete(id):  # noqa: A002 - protocol field name
        store.delete(id)
        return {"ok": True}

    @server.method("conn.duplicate")
    def conn_duplicate(id):  # noqa: A002
        return store.duplicate(id)

    return server


def main():
    server = build(Store())
    server.event("ready", version=__version__)
    server.serve()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/usr/bin/python3 -m unittest discover -s tests -v`
Expected: all tests OK

- [ ] **Step 5: Commit**

```bash
git add omatable/__main__.py tests/test_protocol.py
git commit -m "Serve connection methods over stdio"
```

---

### Task 5: Pinned drivers and the bootstrap

**Files:**
- Create: `tools/update-requirements-lock`, `requirements.lock` (generated), `tablectl`
- Test: `tests/test_bootstrap.py`

**Interfaces:**
- Consumes: `python -m omatable` from Task 4.
- Produces: executable `tablectl`. Env: `OMATABLE_DATA` (data dir), `OMATABLE_LOCK` (lock path, tests only). Emits `{"event": "progress", "text": ..., "pct": ...}` while installing; on failure emits `{"event": "fatal", "code": "drivers", "message": ...}` and exits 1 leaving no venv. Venv stamp file `venv/.omatable-stamp` contains `"<major>.<minor> <sha256 of lock>"`.

- [ ] **Step 1: Write the lock tool and generate the lock**

`tools/update-requirements-lock`:
```python
#!/usr/bin/python3
"""Regenerate requirements.lock: the newest release of every driver, pinned to
the sha256 of each wheel pip may pick on Linux x86_64 for CPython 3.13-3.15.

Maintainer tool only; the plugin never installs anything that is not in the
lock. Review the diff, then commit it.
"""

import json
import os
import re
import urllib.request

PACKAGES = ["psycopg", "psycopg-binary", "PyMySQL", "redis", "pymongo", "dnspython", "typing-extensions"]
WHEEL = re.compile(r"-(py3-none-any|py2\.py3-none-any|cp31[345]-cp31[345]-manylinux[^-]*x86_64)\.whl$")
LOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "requirements.lock")


def release(name):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=30) as r:
        data = json.load(r)
    version = data["info"]["version"]
    hashes = sorted(f["digests"]["sha256"] for f in data["urls"]
                    if f["packagetype"] == "bdist_wheel" and WHEEL.search(f["filename"]))
    if not hashes:
        raise SystemExit(f"{name} {version}: no usable wheel")
    return version, hashes


lines = ["# Generated by tools/update-requirements-lock; do not edit by hand.\n"]
for name in PACKAGES:
    version, hashes = release(name)
    print(f"{name} {version}: {len(hashes)} wheel(s)")
    lines.append(f"{name}=={version} \\\n" + " \\\n".join(f"    --hash=sha256:{h}" for h in hashes) + "\n")
with open(LOCK, "w") as f:
    f.writelines(lines)
print(f"Wrote {os.path.normpath(LOCK)}")
```

Run: `chmod +x tools/update-requirements-lock && tools/update-requirements-lock`
Expected: one line per package, then `Wrote .../requirements.lock`; `head -3 requirements.lock` shows `psycopg==3.3.6 \` followed by `--hash=sha256:` lines.

- [ ] **Step 2: Write the failing test**

`tests/test_bootstrap.py`:
```python
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CTL = os.path.join(ROOT, "tablectl")
NET = os.environ.get("OMATABLE_TEST_NET") == "1"


def run(env_extra, stdin=""):
    env = {**os.environ, "OMATABLE_NO_KEYRING": "1", **env_extra}
    r = subprocess.run([CTL], input=stdin, capture_output=True, text=True, env=env, timeout=600)
    return r.returncode, [json.loads(l) for l in r.stdout.splitlines() if l.startswith("{")], r.stderr


class LockTest(unittest.TestCase):
    def test_every_requirement_is_pinned_with_hashes(self):
        with open(os.path.join(ROOT, "requirements.lock")) as f:
            text = f.read()
        reqs = re.findall(r"^([A-Za-z0-9_.-]+)==([^ ]+) \\\n((?:    --hash=sha256:[0-9a-f]{64}(?: \\\n|\n))+)", text, re.M)
        names = {r[0] for r in reqs}
        self.assertEqual(names, {"psycopg", "psycopg-binary", "PyMySQL", "redis", "pymongo", "dnspython",
                                 "typing-extensions"})


@unittest.skipUnless(NET, "set OMATABLE_TEST_NET=1 to download drivers")
class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_install_then_reuse(self):
        data = os.path.join(self.tmp, "data")
        env = {"OMATABLE_DATA": data, "OMATABLE_CONFIG": os.path.join(self.tmp, "cfg")}
        code, msgs, err = run(env, json.dumps({"id": 1, "method": "app.version"}) + "\n")
        self.assertEqual(code, 0, err)
        self.assertIn("progress", [m.get("event") for m in msgs])
        self.assertEqual(msgs[-1], {"id": 1, "result": {"version": "0.1.0"}})
        py = os.path.join(data, "venv", "bin", "python")
        imports = "import psycopg, pymysql, redis, pymongo; print('ok')"
        self.assertEqual(subprocess.run([py, "-c", imports], capture_output=True, text=True).stdout.strip(), "ok")
        code, msgs, _ = run(env, "")
        self.assertEqual(code, 0)
        self.assertNotIn("progress", [m.get("event") for m in msgs])
        self.assertEqual(msgs[0]["event"], "ready")

    def test_tampered_lock_refuses_and_leaves_nothing(self):
        with open(os.path.join(ROOT, "requirements.lock")) as f:
            text = f.read()
        bad = re.sub(r"(PyMySQL==[^\n]+\n    --hash=sha256:)[0-9a-f]{64}", r"\g<1>" + "0" * 64, text)
        lock = os.path.join(self.tmp, "bad.lock")
        with open(lock, "w") as f:
            f.write(bad)
        data = os.path.join(self.tmp, "data")
        code, msgs, _ = run({"OMATABLE_DATA": data, "OMATABLE_LOCK": lock})
        self.assertEqual(code, 1)
        self.assertEqual(msgs[-1]["event"], "fatal")
        self.assertEqual(msgs[-1]["code"], "drivers")
        self.assertFalse(os.path.exists(os.path.join(data, "venv")))
        self.assertFalse(os.path.exists(os.path.join(data, "venv.new")))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `OMATABLE_TEST_NET=1 /usr/bin/python3 -m unittest tests.test_bootstrap -v`
Expected: LockTest passes; BootstrapTest FAILS with `FileNotFoundError` for `tablectl`.

- [ ] **Step 4: Write the bootstrap**

`tablectl`:
```python
#!/usr/bin/python3
"""OmaTable helper launcher.

Makes sure the database drivers in requirements.lock are installed, exactly
and verified, in ~/.local/share/omatable/venv, then runs the helper server
(`python -m omatable`) from that venv. Only the standard library is used here.

The venv is rebuilt when the system Python's minor version or the lock
changes; a failed install leaves nothing behind.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import venv

HERE = os.path.dirname(os.path.realpath(__file__))
DATA = os.environ.get("OMATABLE_DATA") or os.path.join(
    os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "omatable")
VENV = os.path.join(DATA, "venv")
LOCK = os.environ.get("OMATABLE_LOCK") or os.path.join(HERE, "requirements.lock")


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def stamp():
    with open(LOCK, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return f"{sys.version_info.major}.{sys.version_info.minor} {digest}"


def fatal(message):
    emit({"event": "fatal", "code": "drivers", "message": message})
    sys.exit(1)


def ensure_venv():
    python = os.path.join(VENV, "bin", "python")
    want = stamp()
    try:
        with open(os.path.join(VENV, ".omatable-stamp")) as f:
            if f.read().strip() == want and os.path.exists(python):
                return python
    except OSError:
        pass
    os.makedirs(DATA, mode=0o700, exist_ok=True)
    tmp = VENV + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    emit({"event": "progress", "text": "Preparing database drivers…", "pct": 10})
    try:
        venv.create(tmp, with_pip=True, clear=True)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(tmp, ignore_errors=True)
        fatal(f"could not create {tmp}: {e}")
    emit({"event": "progress", "text": "Installing database drivers…", "pct": 40})
    pip = [os.path.join(tmp, "bin", "python"), "-m", "pip", "install", "--disable-pip-version-check",
           "--no-input", "--require-hashes", "--no-deps", "--only-binary", ":all:", "-r", LOCK]
    r = subprocess.run(pip, capture_output=True, text=True)
    if r.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        lines = [l for l in (r.stderr or r.stdout).splitlines() if l.strip()]
        fatal("installing drivers failed: " + " ".join(lines[-3:]))
    with open(os.path.join(tmp, ".omatable-stamp"), "w") as f:
        f.write(want + "\n")
    shutil.rmtree(VENV, ignore_errors=True)
    os.rename(tmp, VENV)
    emit({"event": "progress", "text": "Drivers ready", "pct": 100})
    return python


def main():
    python = ensure_venv()
    env = dict(os.environ, PYTHONPATH=HERE)
    os.execve(python, [python, "-m", "omatable"], env)


if __name__ == "__main__":
    main()
```

Run: `chmod +x tablectl`

- [ ] **Step 5: Run tests to verify they pass**

Run: `OMATABLE_TEST_NET=1 /usr/bin/python3 -m unittest discover -s tests -v`
Expected: all tests OK (bootstrap tests take ~30 s for the download).

Note on the rename: a venv's `bin/python` is a symlink to the system interpreter and `sys.prefix` is derived from its location, so renaming `venv.new` to `venv` is safe; only the console-script shebangs in `bin/` keep the old path, and they are never used.

- [ ] **Step 6: Commit**

```bash
git add tablectl tools requirements.lock tests/test_bootstrap.py
git commit -m "Install pinned, hash-verified drivers before starting the helper"
```

---

### Task 6: Manifest, bar icon and window shell

**Files:**
- Create: `manifest.json`, `Bar.qml`, `Panel.qml`, `qml/Helper.qml`

**Interfaces:**
- Consumes: `tablectl` protocol (events `ready`, `progress`, `fatal`).
- Produces:
  - `Helper` QML item: properties `program: string`, `status: "stopped"|"starting"|"installing"|"ready"`, `progressText`, `progressPct`, `fatalText`; functions `start()`, `stop()`, `request(method: string, params: object, callback: function(result, error))`; signal `message(var msg)` for events.
  - `Panel.qml` root exposes `opened`, `open(payload)`, `close()`, `requestClose()`, and a `helper` id other views receive as a property.

- [ ] **Step 1: Write the manifest and bar icon**

`manifest.json`:
```json
{
  "schemaVersion": 1,
  "id": "blackcode.omatable",
  "name": "OmaTable",
  "version": "0.1.0",
  "author": "BlackCode",
  "license": "MIT",
  "description": "OmaTable: a TablePlus-style database GUI for Omarchy. Browse, edit and query PostgreSQL, MySQL, MariaDB, SQLite, Redis, Valkey and MongoDB, directly or through SSH, with your OmaDB servers listed automatically",
  "kinds": [
    "bar-widget",
    "panel"
  ],
  "entryPoints": {
    "barWidget": "Bar.qml",
    "panel": "Panel.qml"
  },
  "barWidget": {
    "displayName": "OmaTable",
    "description": "Open the OmaTable database client",
    "category": "Development",
    "allowMultiple": false,
    "defaultSection": "right",
    "defaults": {},
    "schema": []
  }
}
```

`Bar.qml`:
```qml
import QtQuick
import Quickshell
import qs.Ui
import qs.Commons

// Bar icon: click opens or closes the OmaTable window (the panel half of this plugin).
BarWidget {
  id: root
  moduleName: "blackcode.omatable"
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: ""
    tooltipText: "OmaTable"
    onPressed: function() { Quickshell.execDetached(["omarchy-shell", "shell", "toggle", "blackcode.omatable", "{}"]) }
  }
}
```

Before relying on `WidgetButton`, check its API: run `grep -n 'property\|signal' /usr/share/omarchy/shell/Ui/WidgetButton.qml`. If it has no `text`/`tooltip`/`clicked`, replace `WidgetButton` with the same pattern the OmaDB bar button uses in `~/.config/omarchy/plugins/blackcode.omadb/Panel.qml` (search for `MouseArea` near the top-level bar content) and keep the `onClicked` command unchanged.

- [ ] **Step 2: Write the helper wrapper**

`qml/Helper.qml`:
```qml
import QtQuick
import Quickshell
import Quickshell.Io

// Owns the tablectl process and the request/reply bookkeeping.
Item {
  id: helperProc

  property string program: ""
  property string status: "stopped"   // stopped | starting | installing | ready
  property string progressText: ""
  property int progressPct: 0
  property string fatalText: ""
  property string lastStderr: ""
  property int nextId: 1
  property var callbacks: ({})

  signal message(var msg)

  function start() {
    if (proc.running) return
    fatalText = ""
    status = "starting"
    proc.running = true
  }

  function stop() {
    proc.running = false
  }

  function request(method, params, callback) {
    if (!proc.running || status !== "ready") {
      if (callback) callback(null, { code: "stopped", message: "The helper is not running" })
      return
    }
    const id = nextId++
    callbacks[id] = callback || null
    proc.write(JSON.stringify({ id: id, method: method, params: params || {} }) + "\n")
  }

  function receive(line) {
    let msg
    try { msg = JSON.parse(line) } catch (e) { return }
    if (msg.event !== undefined) {
      if (msg.event === "ready") status = "ready"
      else if (msg.event === "progress") { status = "installing"; progressText = msg.text; progressPct = msg.pct || 0 }
      else if (msg.event === "fatal") fatalText = msg.message
      helper.message(msg)
      return
    }
    const cb = callbacks[msg.id]
    delete callbacks[msg.id]
    if (cb) cb(msg.result === undefined ? null : msg.result, msg.error || null)
  }

  Process {
    id: proc
    command: [helper.program]
    stdinEnabled: true
    stdout: SplitParser { onRead: function(line) { helper.receive(String(line)) } }
    stderr: SplitParser { onRead: function(line) { helper.lastStderr = String(line) } }
    onExited: function(exitCode) {
      helperProc.status = "stopped"
      if (exitCode !== 0 && !helperProc.fatalText) helperProc.fatalText = helper.lastStderr || ("Helper exited with code " + exitCode)
      const pending = helper.callbacks
      helper.callbacks = ({})
      for (const id in pending) if (pending[id]) pending[id](null, { code: "stopped", message: "The helper stopped" })
    }
  }
}
```

- [ ] **Step 3: Write the panel window (splash only; Welcome arrives in Task 7)**

`Panel.qml`:
```qml
import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons
import "qml"

// The OmaTable window. The shell loads this as a panel; summon/hide call
// open()/close(), and closing the window ourselves tells the shell.
Item {
  id: root

  readonly property string pluginId: "blackcode.omatable"
  readonly property string ctl: decodeURIComponent(String(Qt.resolvedUrl("tablectl")).replace(/^file:\/\//, ""))
  readonly property bool opened: window.visible
  property bool closingFromHost: false
  property var shell: null

  function open(payloadJson) {
    closingFromHost = false
    window.visible = true
    helperProc.start()
  }

  function close() {
    closingFromHost = true
    window.visible = false
    closingFromHost = false
  }

  function requestClose() {
    if (shell && typeof shell.hide === "function") shell.hide(pluginId)
    else window.visible = false
  }

  Helper {
    id: helperProc
    program: root.ctl
  }

  FloatingWindow {
    id: window
    title: "OmaTable"
    color: Color.background
    implicitWidth: 1100
    implicitHeight: 700
    minimumSize: Qt.size(760, 480)

    onVisibleChanged: {
      if (!visible) helperProc.stop()
      if (!visible && !root.closingFromHost && root.shell && typeof root.shell.hide === "function")
        root.shell.hide(root.pluginId)
    }

    // Splash: shown until the helper is ready (first run installs drivers).
    Column {
      anchors.centerIn: parent
      spacing: 12
      visible: helperProc.status !== "ready"

      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        text: "  OmaTable"
        color: Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body * 2
      }
      Text {
        anchors.horizontalCenter: parent.horizontalCenter
        text: helperProc.fatalText !== "" ? helperProc.fatalText
          : helperProc.status === "installing" ? helperProc.progressText
          : helperProc.status === "stopped" ? "Stopped" : "Starting…"
        color: helperProc.fatalText !== "" ? Color.urgent : Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        width: Math.min(implicitWidth, window.width - 80)
        wrapMode: Text.Wrap
        horizontalAlignment: Text.AlignHCenter
      }
      Button {
        anchors.horizontalCenter: parent.horizontalCenter
        visible: helperProc.status === "stopped"
        text: "Retry"
        onClicked: helperProc.start()
      }
    }
  }
}
```

- [ ] **Step 4: Verify in the real shell**

Run:
```bash
omarchy restart shell
sleep 3
omarchy-shell shell summon blackcode.omatable '{}'
```
Expected: an "OmaTable" window appears showing "Starting…" (or the install progress on first run), then the splash disappears (empty window) once the helper reports `ready`. Check for QML errors with `journalctl --user -n 50 --no-pager | grep -i omatable`. Close the window with the compositor close key; then `omarchy-shell shell summon blackcode.omatable '{}'` again must reopen it. Also add the bar widget (Omarchy settings → Bar → add "OmaTable") and click it: the window toggles.

If the bar widget does not appear in the add list, confirm the plugin is enabled in the Omarchy plugin manager first.

- [ ] **Step 5: Commit**

```bash
git add manifest.json Bar.qml Panel.qml qml/Helper.qml
git commit -m "Add the OmaTable window, bar icon and helper process"
```

---

### Task 7: Welcome window and connection form

**Files:**
- Create: `qml/Field.qml`, `qml/ConnectionForm.qml`, `qml/Welcome.qml`
- Modify: `Panel.qml` (show `Welcome` when ready)

**Interfaces:**
- Consumes: `Helper.request("conn.list" | "conn.save" | "conn.delete" | "conn.duplicate", …)`, public connection dicts from Task 2/3.
- Produces: `Welcome { helper }` with `function reload()`, signal `connectRequested(var conn)` (wired to a placeholder toast in M1; M2 opens the workspace). `ConnectionForm { conn; keyring }` with signals `saved(var conn)`, `cancelled()`, `deleted(string id)`, `duplicated(var conn)`, and property `helper`.

- [ ] **Step 1: Write the field component**

`qml/Field.qml`:
```qml
import QtQuick
import QtQuick.Controls
import qs.Commons

// A label above a single-line input, in the Omarchy theme.
Column {
  id: field
  property string label: ""
  property alias text: input.text
  property alias placeholder: input.placeholderText
  property bool secret: false
  property bool dirty: false
  spacing: 4
  // Equal implicit widths so RowLayout splits the row evenly between fields.
  implicitWidth: 200

  function forceActiveFocus() { input.forceActiveFocus() }

  function setText(value) { input.text = value; dirty = false }

  Text {
    text: field.label
    color: Color.muted
    font.family: Style.font.family
    font.pixelSize: Style.font.bodySmall
  }
  TextField {
    id: input
    width: field.width
    echoMode: field.secret ? TextInput.Password : TextInput.Normal
    color: Color.foreground
    placeholderTextColor: Color.muted
    font.family: Style.font.family
    font.pixelSize: Style.font.body
    onTextEdited: field.dirty = true
    background: Rectangle {
      color: Qt.lighter(Color.background, 1.25)
      border.color: input.activeFocus ? Color.accent : Qt.lighter(Color.background, 1.6)
      radius: 4
    }
  }
}
```

- [ ] **Step 2: Write the connection form**

`qml/ConnectionForm.qml`:
```qml
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons

// Create or edit one connection. Passwords are only sent when typed, so an
// untouched field keeps the saved one.
Flickable {
  id: form
  property var helper: null
  property var conn: null          // public connection dict, or null for a new one
  property bool keyring: true
  property string errorText: ""
  property bool busy: false

  signal saved(var conn)
  signal cancelled()
  signal deleted(string id)
  signal duplicated(var conn)

  readonly property var engines: [
    { id: "postgresql", title: "PostgreSQL", port: 5432 }, { id: "mysql", title: "MySQL", port: 3306 },
    { id: "mariadb", title: "MariaDB", port: 3306 }, { id: "sqlite", title: "SQLite", port: 0 },
    { id: "redis", title: "Redis", port: 6379 }, { id: "valkey", title: "Valkey", port: 6379 },
    { id: "mongodb", title: "MongoDB", port: 27017 }]
  readonly property var tags: ["local", "development", "staging", "production"]
  readonly property var sshAuths: ["agent", "key", "password"]
  readonly property string engine: engines[engineBox.currentIndex].id
  readonly property bool isNew: !conn || !conn.id
  readonly property bool fromOmadb: !!conn && conn.source === "omadb"

  contentHeight: body.implicitHeight + 32
  clip: true

  function load(c) {
    conn = c
    errorText = ""
    const v = c || {}
    nameField.setText(v.name || "")
    engineBox.currentIndex = Math.max(0, engines.findIndex(function(e) { return e.id === (v.engine || "postgresql") }))
    hostField.setText(v.host || "127.0.0.1")
    portField.setText(v.port ? String(v.port) : String(engines[engineBox.currentIndex].port))
    userField.setText(v.user || "")
    passField.setText("")
    passField.placeholder = v.has_password ? "Saved (leave empty to keep)" : ""
    dbField.setText(v.database || "")
    pathField.setText(v.path || "")
    tagBox.currentIndex = Math.max(0, tags.indexOf(v.tag || "local"))
    colorField.setText(v.color || "")
    const ssh = v.ssh || {}
    sshSwitch.checked = !!ssh.enabled
    sshHost.setText(ssh.host || "")
    sshPort.setText(String(ssh.port || 22))
    sshUser.setText(ssh.user || "")
    sshAuthBox.currentIndex = Math.max(0, sshAuths.indexOf(ssh.auth || "agent"))
    sshKey.setText(ssh.key_path || "")
    sshPass.setText("")
    sshPass.placeholder = v.has_ssh_password ? "Saved (leave empty to keep)" : ""
    nameField.forceActiveFocus()
  }

  function collect() {
    const c = {
      name: nameField.text, engine: engine, host: hostField.text, port: parseInt(portField.text) || 0,
      user: userField.text, database: dbField.text, path: pathField.text,
      tag: tags[tagBox.currentIndex], color: colorField.text || null,
      ssh: sshSwitch.checked ? { enabled: true, host: sshHost.text, port: parseInt(sshPort.text) || 22,
        user: sshUser.text, auth: sshAuths[sshAuthBox.currentIndex], key_path: sshKey.text } : null
    }
    if (!isNew && !fromOmadb) c.id = conn.id
    // OmaDB servers saved as a connection carry their password along.
    if (passField.dirty) c.password = passField.text
    else if (fromOmadb) c.omadb = conn.name
    if (sshPass.dirty) c.ssh_password = sshPass.text
    return c
  }

  function save() {
    if (busy) return
    busy = true
    errorText = ""
    const c = collect()
    const method = c.omadb ? "conn.import_omadb" : "conn.save"
    helper.request(method, c.omadb ? { name: c.omadb, conn: c } : { conn: c }, function(result, error) {
      busy = false
      if (error) { errorText = error.message; return }
      form.saved(result)
    })
  }

  ColumnLayout {
    id: body
    x: 16; y: 16
    width: form.width - 32
    spacing: 12

    Text {
      text: form.fromOmadb ? "OmaDB server · " + form.conn.name : form.isNew ? "New connection" : "Edit connection"
      color: Color.foreground
      font.family: Style.font.family
      font.pixelSize: Style.font.body * 1.4
      font.bold: true
    }

    Field { id: nameField; label: "Name"; Layout.fillWidth: true; placeholder: "My database" }

    RowLayout {
      Layout.fillWidth: true
      spacing: 12
      Column {
        spacing: 4
        Layout.fillWidth: true
        Text { text: "Engine"; color: Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
        ComboBox {
          id: engineBox
          width: parent.width
          enabled: !form.fromOmadb
          model: form.engines.map(function(e) { return e.title })
          onActivated: portField.setText(String(form.engines[currentIndex].port))
        }
      }
      Column {
        spacing: 4
        Layout.preferredWidth: 160
        Text { text: "Tag"; color: Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
        ComboBox { id: tagBox; width: parent.width; model: form.tags }
      }
      Field { id: colorField; label: "Color"; Layout.preferredWidth: 110; placeholder: "#rrggbb" }
    }

    Field { id: pathField; label: "Database file"; Layout.fillWidth: true; visible: form.engine === "sqlite"; placeholder: "/path/to/database.db" }

    RowLayout {
      Layout.fillWidth: true
      spacing: 12
      visible: form.engine !== "sqlite"
      Field { id: hostField; label: "Host"; Layout.fillWidth: true; enabled: !form.fromOmadb }
      Field { id: portField; label: "Port"; Layout.preferredWidth: 110; enabled: !form.fromOmadb }
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: 12
      visible: form.engine !== "sqlite"
      Field { id: userField; label: "User"; Layout.fillWidth: true }
      Field { id: passField; label: "Password"; Layout.fillWidth: true; secret: true }
    }

    Field {
      id: dbField
      Layout.fillWidth: true
      visible: form.engine !== "sqlite"
      label: form.engine === "redis" || form.engine === "valkey" ? "Database index" : "Database"
    }

    Text {
      Layout.fillWidth: true
      visible: !form.keyring && form.engine !== "sqlite"
      text: "No keyring is available: passwords are stored in ~/.config/omatable/connections.json (readable only by you)."
      color: Color.muted
      wrapMode: Text.Wrap
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
    }

    // ---- SSH tunnel ----
    RowLayout {
      Layout.fillWidth: true
      visible: form.engine !== "sqlite"
      Switch { id: sshSwitch }
      Text { text: "Connect over SSH"; color: Color.foreground; font.family: Style.font.family; font.pixelSize: Style.font.body }
    }

    GridLayout {
      Layout.fillWidth: true
      visible: sshSwitch.checked && form.engine !== "sqlite"
      columns: 2
      columnSpacing: 12
      rowSpacing: 12
      Field { id: sshHost; label: "SSH host"; Layout.fillWidth: true }
      Field { id: sshPort; label: "SSH port"; Layout.preferredWidth: 110 }
      Field { id: sshUser; label: "SSH user"; Layout.fillWidth: true }
      Column {
        spacing: 4
        Layout.preferredWidth: 110
        Text { text: "Auth"; color: Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.bodySmall }
        ComboBox { id: sshAuthBox; width: parent.width; model: form.sshAuths }
      }
      Field { id: sshKey; label: "Key file"; Layout.fillWidth: true; Layout.columnSpan: 2; visible: sshAuthBox.currentIndex === 1; placeholder: "~/.ssh/id_ed25519" }
      Field { id: sshPass; label: "SSH password"; Layout.fillWidth: true; Layout.columnSpan: 2; visible: sshAuthBox.currentIndex === 2; secret: true }
    }

    Text {
      Layout.fillWidth: true
      visible: form.errorText !== ""
      text: form.errorText
      color: Color.urgent
      wrapMode: Text.Wrap
      font.family: Style.font.family
      font.pixelSize: Style.font.body
    }

    RowLayout {
      Layout.fillWidth: true
      spacing: 8
      Button { text: form.fromOmadb ? "Save as connection" : "Save"; enabled: !form.busy; onClicked: form.save() }
      Button { text: "Cancel"; onClicked: form.cancelled() }
      Item { Layout.fillWidth: true }
      Button {
        text: "Duplicate"
        visible: !form.isNew && !form.fromOmadb
        onClicked: form.helper.request("conn.duplicate", { id: form.conn.id }, function(r, e) {
          if (e) form.errorText = e.message; else form.duplicated(r)
        })
      }
      Button {
        id: deleteButton
        property bool armed: false
        text: armed ? "Click again to delete" : "Delete"
        visible: !form.isNew && !form.fromOmadb
        onClicked: {
          if (!armed) { armed = true; disarm.restart(); return }
          armed = false
          form.helper.request("conn.delete", { id: form.conn.id }, function(r, e) {
            if (e) form.errorText = e.message; else form.deleted(form.conn.id)
          })
        }
        Timer { id: disarm; interval: 3000; onTriggered: deleteButton.armed = false }
      }
    }
  }
}
```

The form calls `conn.import_omadb(name, conn)` for "Save as connection" on an OmaDB server, so add that method to `omatable/__main__.py` inside `build()`:
```python
    @server.method("conn.import_omadb")
    def conn_import_omadb(name, conn):
        source = omadb.server(name)
        merged = {**conn, "password": conn["password"] if "password" in conn else source["password"]}
        merged.pop("omadb", None)
        merged.pop("id", None)
        return store.save(merged)
```
and add to `tests/test_omadb.py`:
```python
    def test_import_omadb_copies_password(self):
        import tempfile as tf
        from omatable.__main__ import build
        from omatable.store import Store
        from tests.test_store import MemoryKeyring
        with tf.TemporaryDirectory() as cfg:
            store = Store(cfg, MemoryKeyring())
            server = build(store)
            saved = server.methods["conn.import_omadb"](
                name="app", conn={"name": "App", "engine": "postgresql", "host": "127.0.0.1", "port": 5433,
                                  "user": "dev", "database": "postgres", "omadb": "app"})
            self.assertEqual(store.get(saved["id"], secrets=True)["password"], "pw")
```
Make `tests` importable: create an empty `tests/__init__.py`.

Run: `/usr/bin/python3 -m unittest discover -s tests -v` (from the plugin root, with `-t .` if discovery complains: `/usr/bin/python3 -m unittest discover -s tests -t . -v`)
Expected: all OK.

- [ ] **Step 3: Write the welcome view**

`qml/Welcome.qml`:
```qml
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import qs.Commons

// TablePlus-style start screen: connections on the left, the form on the right.
Item {
  id: welcome
  property var helper: null
  property var connections: []
  property var omadbServers: []
  property bool keyring: true
  property string search: ""
  property string selectedId: ""
  property string toast: ""

  signal connectRequested(var conn)

  readonly property var tagOrder: ["production", "staging", "development", "local"]
  readonly property var tagColors: ({ production: "#e05252", staging: "#e0a352", development: "#52a3e0", local: "#7a8a7a" })
  readonly property var engineIcons: ({ postgresql: "", mysql: "", mariadb: "", sqlite: "",
    redis: "", valkey: "", mongodb: "" })

  function reload() {
    helper.request("conn.list", {}, function(result, error) {
      if (error) { toast = error.message; return }
      connections = result.connections
      omadbServers = result.omadb
      keyring = result.keyring
    })
  }

  function matches(c) {
    const q = search.toLowerCase()
    return q === "" || (c.name + " " + c.host + " " + c.engine + " " + c.database).toLowerCase().indexOf(q) !== -1
  }

  // Rows for the list: a header per group, then its connections.
  readonly property var rows: {
    const out = []
    const omadb = omadbServers.filter(matches)
    if (omadb.length) {
      out.push({ header: "OmaDB" })
      omadb.forEach(function(c) { out.push({ conn: c }) })
    }
    tagOrder.forEach(function(tag) {
      const group = connections.filter(function(c) { return c.tag === tag && matches(c) })
      if (!group.length) return
      out.push({ header: tag.charAt(0).toUpperCase() + tag.slice(1) })
      group.forEach(function(c) { out.push({ conn: c }) })
    })
    return out
  }

  function select(c) {
    selectedId = c ? c.id : ""
    form.visible = true
    form.load(c)
  }

  function subtitle(c) {
    if (c.engine === "sqlite") return c.path
    return (c.user ? c.user + "@" : "") + c.host + ":" + c.port + (c.database ? "/" + c.database : "")
  }

  Component.onCompleted: reload()

  Connections {
    target: welcome.helper
    function onStatusChanged() { if (welcome.helperProc.status === "ready") welcome.reload() }
  }

  RowLayout {
    anchors.fill: parent
    spacing: 0

    // ---- connection list ----
    Rectangle {
      Layout.preferredWidth: 340
      Layout.fillHeight: true
      color: Qt.darker(Color.background, 1.15)

      ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        RowLayout {
          Layout.fillWidth: true
          TextField {
            id: searchField
            Layout.fillWidth: true
            placeholderText: "Search connections"
            color: Color.foreground
            font.family: Style.font.family
            onTextChanged: welcome.search = text
            Keys.onDownPressed: list.forceActiveFocus()
          }
          Button { text: "+"; onClicked: welcome.select(null) }
        }

        ListView {
          id: list
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          model: welcome.rows
          keyNavigationEnabled: true
          delegate: Item {
            required property var modelData
            required property int index
            width: list.width
            height: modelData.header ? 28 : 52

            Text {
              visible: !!modelData.header
              anchors.bottom: parent.bottom
              anchors.bottomMargin: 4
              text: modelData.header || ""
              color: Color.muted
              font.family: Style.font.family
              font.pixelSize: Style.font.bodySmall
              font.bold: true
            }

            Rectangle {
              visible: !modelData.header
              anchors.fill: parent
              anchors.topMargin: 2
              anchors.bottomMargin: 2
              radius: 6
              color: modelData.conn && modelData.conn.id === welcome.selectedId
                ? Qt.lighter(Color.background, 1.5) : hover.containsMouse ? Qt.lighter(Color.background, 1.25) : "transparent"

              Rectangle { width: 4; height: parent.height - 12; anchors.verticalCenter: parent.verticalCenter; x: 4; radius: 2
                color: modelData.conn ? modelData.conn.color : "transparent" }

              Text {
                id: icon
                x: 16
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.conn ? (welcome.engineIcons[modelData.conn.engine] || "") : ""
                color: Color.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.body * 1.6
              }
              Column {
                anchors.left: icon.right
                anchors.leftMargin: 12
                anchors.right: badge.left
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                Text {
                  width: parent.width
                  elide: Text.ElideRight
                  text: modelData.conn ? modelData.conn.name : ""
                  color: Color.foreground
                  font.family: Style.font.family
                  font.pixelSize: Style.font.body
                }
                Text {
                  width: parent.width
                  elide: Text.ElideRight
                  text: modelData.conn ? welcome.subtitle(modelData.conn) : ""
                  color: Color.muted
                  font.family: Style.font.family
                  font.pixelSize: Style.font.bodySmall
                }
              }
              Text {
                id: badge
                anchors.right: parent.right
                anchors.rightMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                text: !modelData.conn ? "" : modelData.conn.source === "omadb" ? (modelData.conn.running ? "●" : "○")
                  : modelData.conn.ssh ? "" : ""
                color: modelData.conn && modelData.conn.source === "omadb" && modelData.conn.running ? "#5fb85f" : Color.muted
                font.family: Style.font.family
                font.pixelSize: Style.font.body
              }

              MouseArea {
                id: hover
                anchors.fill: parent
                hoverEnabled: true
                onClicked: welcome.select(modelData.conn)
                onDoubleClicked: welcome.connectRequested(modelData.conn)
              }
            }
          }
        }

        Text {
          Layout.fillWidth: true
          visible: welcome.rows.length === 0
          text: welcome.search ? "No matches" : "No connections yet. Press + to add one."
          color: Color.muted
          wrapMode: Text.Wrap
          font.family: Style.font.family
          font.pixelSize: Style.font.body
        }
      }
    }

    // ---- form / empty state ----
    Item {
      Layout.fillWidth: true
      Layout.fillHeight: true

      Column {
        anchors.centerIn: parent
        visible: !form.visible
        spacing: 8
        Text { anchors.horizontalCenter: parent.horizontalCenter; text: ""; color: Color.muted
          font.family: Style.font.family; font.pixelSize: 64 }
        Text { anchors.horizontalCenter: parent.horizontalCenter; text: "Select a connection, or press + to create one"
          color: Color.muted; font.family: Style.font.family; font.pixelSize: Style.font.body }
      }

      ConnectionForm {
        id: form
        anchors.fill: parent
        visible: false
        helper: welcome.helper
        keyring: welcome.keyring
        onSaved: function(c) { welcome.reload(); welcome.select(c); welcome.toast = "Saved " + c.name }
        onCancelled: { visible = false; welcome.selectedId = "" }
        onDeleted: function(id) { visible = false; welcome.selectedId = ""; welcome.reload() }
        onDuplicated: function(c) { welcome.reload(); welcome.select(c) }
      }
    }
  }

  // ---- toast ----
  Rectangle {
    anchors.bottom: parent.bottom
    anchors.horizontalCenter: parent.horizontalCenter
    anchors.bottomMargin: 16
    visible: welcome.toast !== ""
    width: toastText.implicitWidth + 24
    height: toastText.implicitHeight + 12
    radius: 6
    color: Qt.lighter(Color.background, 1.6)
    Text { id: toastText; anchors.centerIn: parent; text: welcome.toast; color: Color.foreground
      font.family: Style.font.family; font.pixelSize: Style.font.body }
    Timer { running: welcome.toast !== ""; interval: 3000; onTriggered: welcome.toast = "" }
  }
}
```

- [ ] **Step 4: Show the welcome view from the panel**

In `Panel.qml`, inside `FloatingWindow` after the splash `Column`, add:
```qml
    Welcome {
      anchors.fill: parent
      visible: helperProc.status === "ready"
      helper: helperProc
      onConnectRequested: function(conn) { toast = "Connecting arrives in the next milestone: " + conn.name }
    }
```

- [ ] **Step 5: Verify in the real shell**

Run: `omarchy restart shell && sleep 3 && omarchy-shell shell summon blackcode.omatable '{}'`
Expected:
1. Window opens on the welcome screen; with no OmaDB servers and no saved connections it shows "No connections yet".
2. Click **+**, fill Name "Test PG", host `127.0.0.1`, user `postgres`, password `pw`, tag Development, Save → the row appears under "Development" with toast "Saved Test PG"; `cat ~/.config/omatable/connections.json` contains no `pw`; `secret-tool lookup app omatable conn <id> kind db` prints `pw`.
3. Duplicate → "Test PG copy" appears; Delete twice → it disappears.
4. Create an OmaDB server (`~/.config/omarchy/plugins/blackcode.omadb/dbctl create postgresql 17 zt-ui 55432 off native dev pw`), reopen the window → it is listed under "OmaDB" with a green dot; selecting it shows the form with host/port locked; "Save as connection" creates a saved connection with the password copied. Remove it after: `dbctl remove zt-ui`.
5. Turn on SSH, choose `key` → the Key file field appears; Save with empty SSH host → error "SSH needs a host and a user" shown in red.

Use the scratchpad click helpers (`/tmp/claude-1000/-home-blackcode-desktop/e4b4a5c1-17c8-4668-b6e7-8f0ea7793e33/scratchpad/click`) or click by hand; take a screenshot with `grim` to confirm layout.

- [ ] **Step 6: Commit**

```bash
git add qml Panel.qml omatable/__main__.py tests
git commit -m "Add the welcome window with the connection list and form"
```

---

### Task 8: README, license, push

**Files:**
- Create: `README.md`, `LICENSE`, `.gitignore`

- [ ] **Step 1: Write the files**

`.gitignore`:
```
__pycache__/
*.pyc
```

`LICENSE`: MIT, copyright `2026 BlackCode` (same text as `~/.config/omarchy/plugins/blackcode.omadb/LICENSE` with the year/name kept).

`README.md`:
```markdown
# OmaTable

A TablePlus-style database GUI for [Omarchy](https://omarchy.org). Status: early (M1): connections management.

- PostgreSQL, MySQL, MariaDB, SQLite, Redis, Valkey and MongoDB
- Servers from [OmaDB](https://github.com/blackcodesh/omadb) are listed automatically, with their credentials
- Passwords are kept in your keyring (Secret Service), never in plain files when a keyring is available
- SSH tunnels with agent, key or password login

## Install

Install from the Omarchy plugin manager, or:

    git clone https://github.com/blackcodesh/omatable ~/.config/omarchy/plugins/blackcode.omatable
    omarchy restart shell

Then add the **OmaTable** bar widget, or open the window with:

    omarchy-shell shell summon blackcode.omatable '{}'

The first launch installs the database drivers into `~/.local/share/omatable/venv`. Every wheel is pinned by
version and SHA-256 in `requirements.lock`; anything that does not match is refused.

## Files

- `~/.config/omatable/connections.json`: saved connections (mode 600)
- `~/.local/share/omatable/venv`: the drivers

## Development

    /usr/bin/python3 -m unittest discover -s tests -t . -v           # unit tests
    OMATABLE_TEST_NET=1 /usr/bin/python3 -m unittest discover -s tests -t . -v   # plus driver install
    tools/update-requirements-lock                          # bump drivers

## License

MIT
```

- [ ] **Step 2: Run the whole suite**

Run: `OMATABLE_TEST_NET=1 /usr/bin/python3 -m unittest discover -s tests -t . -v`
Expected: all OK.

- [ ] **Step 3: Commit**

```bash
git add README.md LICENSE .gitignore
git commit -m "Add README and license"
```

Pushing to GitHub (`gh repo create blackcodesh/omatable --public --source . --push`) happens only after the user confirms; ask at the end of M1.
