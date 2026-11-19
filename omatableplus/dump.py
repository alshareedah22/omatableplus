"""Backup and restore of a whole database.

PostgreSQL and MySQL/MariaDB use the server's own tools (pg_dump/psql/pg_restore,
mysqldump or mariadb-dump/mysql or mariadb), found on PATH or in OmaDBngin's
engines. SQLite copies the file with the backup API. MongoDB is dumped here in
mongodump's layout (one .bson and one .metadata.json per collection) inside a
.tar.gz, so mongorestore can read it too.

Backups are written to a temporary file next to the target and renamed into
place, mode 600. Passwords go to the tools through the environment or a
mode-600 option file, never on the command line.
"""

import contextlib
import glob
import io
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time

from . import transfer
from .protocol import Error

ENGINE_DIRS = ("~/.local/share/omadbngin/engines", "~/.local/share/omadb/engines")
TOOLS = {
    "postgresql": {"dump": ["pg_dump"], "load": ["psql"], "restore": ["pg_restore"]},
    "mysql": {"dump": ["mysqldump", "mariadb-dump"], "load": ["mysql", "mariadb"]},
    "mariadb": {"dump": ["mariadb-dump", "mysqldump"], "load": ["mariadb", "mysql"]},
}
ENGINE_OF_TOOL = {"pg_dump": "postgresql", "psql": "postgresql", "pg_restore": "postgresql",
                  "mysqldump": "mysql", "mysql": "mysql", "mariadb-dump": "mariadb", "mariadb": "mariadb"}
VERSION = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def _version(text):
    m = VERSION.search(text or "")
    return tuple(int(x or 0) for x in m.groups()) if m else ()


def candidates(name):
    """(version, path) for every copy of a tool: OmaDBngin's engines, then PATH."""
    out = []
    engine = ENGINE_OF_TOOL.get(name, "")
    for base in ENGINE_DIRS:
        for path in glob.glob(os.path.join(os.path.expanduser(base), engine, "*", "bin", name)):
            if os.access(path, os.X_OK):
                out.append((_version(path.split(os.sep)[-3]), path))
    found = shutil.which(name)
    if found and all(os.path.realpath(found) != os.path.realpath(p) for _, p in out):
        try:
            v = subprocess.run([found, "--version"], capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            v = ""
        out.append((_version(v.split(")")[-1] if "PostgreSQL" in v else v), found))
    return out


def find_tool(names, server=()):
    """The best tool among names for a server version: the same major, else the newest."""
    every = [(v, p) for n in names for v, p in candidates(n)]
    if not every:
        raise Error("missing", f"{names[0]} was not found; install it, or install that engine with OmaDBngin")
    same = [c for c in every if server and c[0][:1] == server[:1]]
    return max(same or every)[1]


class Job:
    """A running tool, so the session can stop it."""

    def __init__(self):
        self.proc = None
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()

    def run(self, cmd, env=None, stdin=None, stdout=None):
        env = {k: v for k, v in {**os.environ, **(env or {})}.items() if v is not None}  # None unsets
        self.proc = subprocess.Popen(cmd, env=env, stdin=stdin or subprocess.DEVNULL,
                                     stdout=stdout or subprocess.DEVNULL, stderr=subprocess.PIPE)
        err = self.proc.communicate()[1].decode("utf-8", "replace")
        if self.cancelled:
            raise Error("cancelled", "Cancelled")
        if self.proc.returncode:
            lines = [ln for ln in err.strip().splitlines() if ln.strip()]
            raise Error("tool", (lines[-1] if lines else f"{os.path.basename(cmd[0])} failed") +
                        (f" ({os.path.basename(cmd[0])} exited with {self.proc.returncode})"))
        return err


@contextlib.contextmanager
def _output(path, overwrite):
    """A temporary path next to path, renamed onto it when the block succeeds."""
    path = transfer.target_path(path, overwrite)
    fd, tmp = tempfile.mkstemp(prefix=".omatableplus-", dir=os.path.dirname(path))
    os.close(fd)
    os.chmod(tmp, 0o600)
    try:
        yield path, tmp
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _source(path):
    path = os.path.abspath(os.path.expanduser(str(path or "").strip()))
    if not os.path.isfile(path):
        raise Error("notfound", f"no file at {path}")
    return path


def _engine(adapter):
    engine = adapter.conn.get("engine") or adapter.engine
    if engine in ("mysql", "mariadb"):
        with contextlib.suppress(Exception):
            return "mariadb" if adapter.version().startswith("MariaDB") else "mysql"
    return engine


def _server_version(adapter):
    with contextlib.suppress(Exception):
        return _version(adapter.version())
    return ()


# ---- PostgreSQL --------------------------------------------------------------

def _pg_env(c, database):
    # The whole connection goes in the environment. A database name is chosen by whoever created
    # it, and as `-d` (or `-h`, `-U`) libpq would read one like "host=… " as a connection string
    # of its own and send PGPASSWORD wherever it points; PG* variables are only ever plain values.
    return {"PGHOST": c.get("host") or "127.0.0.1", "PGPORT": str(c.get("port") or 5432),
            "PGUSER": c.get("user") or "postgres", "PGDATABASE": database,
            "PGPASSWORD": c.get("password") or "", "PGSSLMODE": c.get("tls") or "prefer",
            "PGAPPNAME": "OmaTablePlus", "PGCONNECT_TIMEOUT": "10",
            # inherited ones that would take precedence over the host above
            "PGHOSTADDR": None, "PGSERVICE": None, "PGSERVICEFILE": None}


PG_ARGS = ["--no-password"]


# ---- MySQL / MariaDB ---------------------------------------------------------

def _my_quote(value):
    """A value for a my.cnf-style option file, double-quoted so '#', quotes and leading or
    trailing spaces survive. Inside quotes the parser reads \\ and \" as escapes."""
    text = str(value).replace("\r", "").replace("\n", "")
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


@contextlib.contextmanager
def _my_options(c, engine):
    """A mode-600 option file with the connection, so the password stays off argv."""
    tls = c.get("tls") or "prefer"
    lines = ["[client]", f"host={c.get('host') or '127.0.0.1'}", f"port={c.get('port') or 3306}",
             f"user={c.get('user') or 'root'}"]
    if c.get("password"):
        lines.append("password=" + _my_quote(c["password"]))
    if tls == "disable":
        lines.append("skip-ssl" if engine == "mariadb" else "ssl-mode=DISABLED")
    elif tls == "prefer" and engine == "mariadb":
        lines.append("skip-ssl-verify-server-cert")
    fd, path = tempfile.mkstemp(prefix=".omatableplus-my-", suffix=".cnf")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("\n".join(lines) + "\n")
        yield path
    finally:
        with contextlib.suppress(OSError):
            os.unlink(path)


# ---- MongoDB -----------------------------------------------------------------

def _mongo_backup(adapter, database, tmp):
    import bson
    import bson.json_util
    db = adapter.client[database]
    with tarfile.open(tmp, "w:gz") as tar:
        def add(name, data):
            info = tarfile.TarInfo(f"{database}/{name}")
            info.size = len(data)
            info.mtime = int(time.time())
            info.mode = 0o600
            tar.addfile(info, io.BytesIO(data))
        for spec in db.list_collections():
            name, kind = spec["name"], spec.get("type", "collection")
            if name.startswith("system."):
                continue
            meta = {"options": spec.get("options") or {}, "type": kind, "collectionName": name}
            if kind == "collection":
                meta["indexes"] = list(db[name].list_indexes())
                buf = io.BytesIO()
                for doc in db[name].find({}, batch_size=1000):
                    buf.write(bson.encode(doc))
                add(f"{name}.bson", buf.getvalue())
            else:
                meta["indexes"] = []
                add(f"{name}.bson", b"")
            add(f"{name}.metadata.json", bson.json_util.dumps(meta).encode())


def _mongo_restore(adapter, database, path):
    import bson
    import bson.json_util
    db = adapter.client[database]
    with tarfile.open(path, "r:*") as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}
        metas = sorted(n for n in members if n.endswith(".metadata.json") and n.count("/") == 1)
        if not metas:
            raise Error("invalid", "this is not a MongoDB backup (no <database>/<collection>.metadata.json inside)")
        names = [m.split("/", 1)[1][:-len(".metadata.json")] for m in metas]
        taken = sorted(set(names) & set(db.list_collection_names()))
        if taken:  # checked first, so a clash writes nothing
            raise Error("exists", f"{', '.join(taken)} already exist{'s' if len(taken) == 1 else ''} in {database}; "
                        "restore into an empty database or drop them first")
        views = []
        for meta_name in metas:
            meta = bson.json_util.loads(tar.extractfile(members[meta_name]).read())
            name = meta_name.split("/", 1)[1][:-len(".metadata.json")]
            if meta.get("type") == "view" or "viewOn" in (meta.get("options") or {}):
                views.append((name, meta["options"]))
                continue
            db.create_collection(name, **{k: v for k, v in (meta.get("options") or {}).items() if k != "uuid"})
            data = members.get(meta_name[:-len(".metadata.json")] + ".bson")
            if data is not None:
                batch = []
                for doc in bson.decode_file_iter(tar.extractfile(data)):
                    batch.append(doc)
                    if len(batch) >= 1000:
                        db[name].insert_many(batch, ordered=True)
                        batch = []
                if batch:
                    db[name].insert_many(batch, ordered=True)
            for ix in meta.get("indexes") or ():
                if ix.get("name") == "_id_":
                    continue
                keys = list(ix["key"].items())
                opts = {k: v for k, v in ix.items() if k not in ("key", "v", "ns")}
                db[name].create_index(keys, **opts)
        for name, options in views:
            db.command({"create": name, "viewOn": options["viewOn"], "pipeline": options.get("pipeline", [])})
    return len(metas)


# ---- entry points ------------------------------------------------------------

def backup(adapter, job, path, database=None, overwrite=False):
    """Write database (default: the current one) to path. Returns {path, bytes, ms}."""
    engine = _engine(adapter)
    database = database or adapter.database
    c = adapter.conn
    start = time.monotonic()
    if engine in ("redis", "valkey"):
        raise Error("unsupported", "Redis and Valkey save their own snapshots; use BGSAVE in the console")
    if engine != "sqlite" and not database:
        raise Error("invalid", "choose a database to back up")
    with _output(path, overwrite) as (path, tmp):
        if engine == "postgresql":
            tool = find_tool(TOOLS["postgresql"]["dump"], _server_version(adapter))
            job.run([tool, *PG_ARGS, "--format=plain", "--no-owner", "--no-privileges", "-f", tmp],
                    env=_pg_env(c, database))
        elif engine in ("mysql", "mariadb"):
            tool = find_tool(TOOLS[engine]["dump"], _server_version(adapter))
            with _my_options(c, engine) as opts:
                job.run([tool, f"--defaults-extra-file={opts}", "--single-transaction", "--routines", "--triggers",
                         "--no-tablespaces", f"--result-file={tmp}", "--", database])
        elif engine == "sqlite":
            dst = sqlite3.connect(tmp)
            try:
                adapter.db.backup(dst)
            finally:
                dst.close()
        elif engine == "mongodb":
            _mongo_backup(adapter, database, tmp)
        else:
            raise Error("unsupported", f"backups are not supported for {engine}")
    return {"path": path, "bytes": os.path.getsize(path), "ms": round((time.monotonic() - start) * 1000),
            "database": database}


def restore(adapter, job, path, database=None):
    """Load a backup into database (default: the current one)."""
    engine = _engine(adapter)
    database = database or adapter.database
    c = adapter.conn
    path = _source(path)
    start = time.monotonic()
    if engine == "postgresql":
        with open(path, "rb") as f:
            custom = f.read(5) == b"PGDMP"
        server = _server_version(adapter)
        if custom:
            tool = find_tool(TOOLS["postgresql"]["restore"], server)
            job.run([tool, *PG_ARGS, "--no-owner", "--no-privileges", "--single-transaction",
                     "--exit-on-error", path], env=_pg_env(c, database))
        else:
            tool = find_tool(TOOLS["postgresql"]["load"], server)
            job.run([tool, *PG_ARGS, "-X", "-q", "-v", "ON_ERROR_STOP=1", "--single-transaction",
                     "-f", path], env=_pg_env(c, database))
    elif engine in ("mysql", "mariadb"):
        if not database:
            raise Error("invalid", "choose a database to restore into")
        tool = find_tool(TOOLS[engine]["load"], _server_version(adapter))
        with _my_options(c, engine) as opts, open(path, "rb") as f:
            job.run([tool, f"--defaults-extra-file={opts}", "--", database], stdin=f)
    elif engine == "mongodb":
        _mongo_restore(adapter, database, path)
    elif engine == "sqlite":
        raise Error("unsupported", "to restore a SQLite file, add a connection for it; a .sql file runs with Run SQL file")
    else:
        raise Error("unsupported", f"restore is not supported for {engine}")
    adapter.forget()
    return {"ms": round((time.monotonic() - start) * 1000), "database": database}
