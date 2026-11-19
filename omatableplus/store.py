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
import tempfile
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
LEGACY_APP = "omatable"   # the keyring attribute before the rename to OmaTablePlus


def default_dir():
    if os.environ.get("OMATABLEPLUS_CONFIG"):
        return os.environ["OMATABLEPLUS_CONFIG"]
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "omatableplus")


class Keyring:
    """The Secret Service through secret-tool; every call is best effort."""

    def __init__(self, enabled=True):
        self.enabled = bool(enabled) and os.environ.get("OMATABLEPLUS_NO_KEYRING") != "1" \
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
            probe = ["app", "omatableplus", "conn", "probe", "kind", "probe"]
            ok = self._run(["store", "--label", "OmaTablePlus probe", *probe], "probe")
            self._available = bool(ok and ok.returncode == 0)
            if self._available:
                self._run(["clear", *probe])
        return self._available

    def set(self, conn_id, kind, secret):
        if not self.available():
            return False
        r = self._run(["store", "--label", f"OmaTablePlus {kind} password ({conn_id})",
                       "app", "omatableplus", "conn", conn_id, "kind", kind], secret)
        return bool(r and r.returncode == 0)

    def get(self, conn_id, kind):
        if not self.available():
            return None
        r = self._run(["lookup", "app", "omatableplus", "conn", conn_id, "kind", kind])
        if r and r.returncode == 0:
            return r.stdout
        # Saved before the rename to OmaTablePlus: move it across on first use.
        r = self._run(["lookup", "app", LEGACY_APP, "conn", conn_id, "kind", kind])
        if not (r and r.returncode == 0):
            return None
        if self.set(conn_id, kind, r.stdout):
            self._run(["clear", "app", LEGACY_APP, "conn", conn_id, "kind", kind])
        return r.stdout

    def clear(self, conn_id, kind):
        if self.available():
            self._run(["clear", "app", "omatableplus", "conn", conn_id, "kind", kind])
            self._run(["clear", "app", LEGACY_APP, "conn", conn_id, "kind", kind])


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
           "folder": _str(conn, "folder", 64), "readonly": bool(conn.get("readonly")),
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
    if engine == "mongodb":
        out["auth_source"] = _str(conn, "auth_source", 128)
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
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".connections-", suffix=".tmp")  # mode 600
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(conns, f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

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
                if "auth_source" not in fields:
                    # no longer MongoDB: do not bring an old auth database back later
                    stored.pop("auth_source", None)
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
