"""Open database sessions: one driver connection, one worker thread and an
optional SSH tunnel each. Driver connections are not shared across threads,
so every call on a session runs on its own single worker.
"""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import adapters, omadb
from .protocol import Error
from .store import validate
from .tunnel import Tunnel


class Session:
    def __init__(self, sid, conn, adapter, tunnel):
        self.id = sid
        self.conn = conn
        self.adapter = adapter
        self.tunnel = tunnel
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="session-" + sid)
        self.job = None     # a running backup or restore tool

    def run(self, fn, *args, **kwargs):
        if self.tunnel is not None and not self.tunnel.alive():
            raise Error("tunnel", "the SSH tunnel closed: " + (" ".join(self.tunnel.stderr) or "ssh exited"))
        return self.worker.submit(fn, *args, **kwargs).result()

    def cancel(self):
        # Not through the worker: it is busy running the statement to stop.
        if self.job is not None:
            self.job.cancel()
        else:
            self.adapter.cancel()

    def info(self):
        a, c = self.adapter, self.conn
        return {"session": self.id, "id": c.get("id"), "name": c.get("name"), "engine": c.get("engine"),
                "tag": c.get("tag") or "local", "color": c.get("color"), "database": a.database,
                "readonly": bool(c.get("readonly")),
                "databases": a.databases(), "version": a.version(), "ssh": self.tunnel is not None}

    def close(self):
        try:
            self.worker.submit(self.adapter.close).result(timeout=10)
        except Exception:  # noqa: BLE001 - closing must always finish
            pass
        self.worker.shutdown(wait=False)
        if self.tunnel is not None:
            self.tunnel.close()


def connect(conn):
    """A connected adapter for conn (with secrets), through a tunnel if it has one."""
    target = dict(conn)
    tunnel = None
    ssh = conn.get("ssh")
    if ssh and ssh.get("enabled") and conn.get("engine") != "sqlite":
        tunnel = Tunnel(ssh, conn.get("host"), conn.get("port"), conn.get("ssh_password"))
        target.update(host="127.0.0.1", port=tunnel.start())
    adapter = adapters.create(target)
    try:
        adapter.connect()
    except BaseException:
        adapter.close()
        if tunnel is not None:
            tunnel.close()
        raise
    return adapter, tunnel


class Sessions:
    def __init__(self, store):
        self.store = store
        self.items = {}
        self.lock = threading.Lock()

    def resolve(self, conn_id):
        if str(conn_id).startswith("omadb:"):
            return omadb.server(str(conn_id)[6:])
        return self.store.get(conn_id, secrets=True)

    def open(self, conn_id, readonly=False, db_readonly=False, timeout=0):
        conn = self.resolve(conn_id)
        if readonly:
            # read-only can be asked for at connect time (OmaDBngin servers have no saved switch)
            conn = {**conn, "readonly": True}
        if db_readonly:
            # read-only at the database only: the helper still answers a production write with "confirm"
            conn = {**conn, "db_readonly": True}
        if timeout:
            conn = {**conn, "statement_timeout": int(timeout)}      # seconds; the MCP server's sessions only
        adapter, tunnel = connect(conn)
        session = Session(uuid.uuid4().hex[:12], conn, adapter, tunnel)
        with self.lock:
            self.items[session.id] = session
        try:
            return session.run(session.info)
        except BaseException:
            self.close(session.id)
            raise

    def test(self, conn):
        """Try the connection described by an unsaved form; saved secrets fill the gaps."""
        conn_id = conn.get("id")
        if conn_id and not str(conn_id).startswith("omadb:"):
            saved = self.resolve(conn_id)
        elif conn_id:
            saved = omadb.server(str(conn_id)[6:])
        else:
            saved = {}
        target = {**validate(conn), "id": conn_id,
                  "password": conn["password"] if "password" in conn else saved.get("password", ""),
                  "ssh_password": conn["ssh_password"] if "ssh_password" in conn else saved.get("ssh_password", "")}
        start = time.monotonic()
        adapter, tunnel = connect(target)
        try:
            version = adapter.version()
        finally:
            adapter.close()
            if tunnel is not None:
                tunnel.close()
        return {"ok": True, "version": version, "ms": round((time.monotonic() - start) * 1000)}

    def get(self, sid):
        with self.lock:
            session = self.items.get(sid)
        if session is None:
            raise Error("notfound", "this session is closed; reconnect")
        return session

    def close(self, sid):
        with self.lock:
            session = self.items.pop(sid, None)
        if session is not None:
            session.close()

    def close_all(self):
        with self.lock:
            sessions, self.items = list(self.items.values()), {}
        for s in sessions:
            s.close()
