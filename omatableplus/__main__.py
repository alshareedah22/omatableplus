"""Entry point: `python -m omatableplus` serves the protocol on stdin/stdout."""

import os
import re
import signal
import sys
import tomllib

from . import __version__, admin, dump, files, objects, omadb, safety, sqlsplit, transfer
from .history import History, Saved
from .protocol import Error, Server
from .session import Sessions
from .store import Store

DDL = ("CREATE", "DROP", "ALTER", "RENAME")
# Editor colour role -> colors.toml keys to try, in order.
PALETTE = {"keyword": ("magenta", "color5"), "string": ("green", "color2"), "number": ("yellow", "color3"),
           "comment": ("dark_foreground", "color8"), "identifier": ("cyan", "color6")}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def palette():
    path = os.environ.get("OMATABLEPLUS_THEME_COLORS") or os.path.join(
        os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
        "omarchy", "current", "theme", "colors.toml")
    try:
        with open(path, "rb") as f:
            colors = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    out = {}
    for role, keys in PALETTE.items():
        for key in keys:
            value = colors.get(key)
            if isinstance(value, str) and HEX.match(value):
                out[role] = value
                break
    return out


def build(store, server=None, sessions=None):
    server = server or Server()
    sessions = sessions or Sessions(store)
    server.sessions = sessions
    history = History(store.dir)
    saved = Saved(store.dir)

    def ask(s, confirmed, question, detail=None):
        """Every write passes here. Read-only connections refuse it; production
        connections confirm it (the window asks and resends with confirmed)."""
        if s.conn.get("readonly"):
            raise Error("readonly", f"{s.conn.get('name') or 'This connection'} is read-only, so nothing that changes "
                        "data runs here. Connect without Read-only to make changes.", detail)
        if s.conn.get("tag") == "production" and not confirmed:
            raise Error("confirm", question, detail)

    def guarded(s, confirmed):
        """Whether a write has to go through ask(): on read-only connections always."""
        return bool(s.conn.get("readonly")) or (not confirmed and s.conn.get("tag") == "production")

    @server.method("app.version")
    def version():
        return {"version": __version__}

    @server.method("app.paths")
    def app_paths():
        home = os.path.expanduser("~")
        downloads = os.path.join(home, "Downloads")
        return {"home": home, "downloads": downloads if os.path.isdir(downloads) else home}

    @server.method("app.palette")
    def app_palette():
        return palette()

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

    @server.method("conn.import_omadb")
    def conn_import_omadb(name, conn):
        source = omadb.server(name)
        merged = {**conn, "password": conn["password"] if "password" in conn else source["password"]}
        merged.pop("omadb", None)
        merged.pop("id", None)
        return store.save(merged)

    @server.method("conn.test")
    def conn_test(conn):
        return sessions.test(conn)

    @server.method("session.open")
    def session_open(id, readonly=False):  # noqa: A002
        return sessions.open(id, bool(readonly))

    @server.method("session.close")
    def session_close(session):
        sessions.close(session)
        return {"ok": True}

    @server.method("session.databases")
    def session_databases(session):
        s = sessions.get(session)
        return {"databases": s.run(s.adapter.databases), "database": s.adapter.database}

    @server.method("session.use")
    def session_use(session, database):
        s = sessions.get(session)
        s.run(s.adapter.use, database)
        return {"database": s.adapter.database}

    @server.method("schema.tree")
    def schema_tree(session):
        s = sessions.get(session)
        return {"schemas": s.run(s.adapter.tree), "database": s.adapter.database}

    @server.method("schema.catalog")
    def schema_catalog(session):
        s = sessions.get(session)
        return s.run(s.adapter.catalog)

    @server.method("schema.relations")
    def schema_relations(session, schema=None):
        s = sessions.get(session)
        return s.run(s.adapter.relations, schema)

    @server.method("schema.definition")
    def schema_definition(session, schema, name, kind):
        s = sessions.get(session)
        return s.run(s.adapter.definition, schema, name, kind)

    @server.method("schema.template")
    def schema_template(session, schema, kind, table=None):
        s = sessions.get(session)
        return {"sql": objects.template(s.adapter.d, schema, kind, table)}

    @server.method("schema.drop_object")
    def schema_drop_object(session, schema, name, kind, table=None, signature=None, confirmed=False):
        s = sessions.get(session)
        ask(s, confirmed, f"Drop the {kind} {name}?")
        return s.run(s.adapter.drop_object, schema, name, kind, table, signature)

    @server.method("table.rows")
    def table_rows(session, schema, table, offset=0, limit=300, sort=None, filters=None, match="and"):
        s = sessions.get(session)
        return s.run(s.adapter.rows, schema, table, offset, limit, sort or [], filters or [], match)

    @server.method("table.page")
    def table_page(session, schema, table, offset=0, limit=300, sort=None, filters=None, match="and"):
        s = sessions.get(session)
        return s.run(s.adapter.page, schema, table, offset, limit, sort or [], filters or [], match)

    @server.method("table.count")
    def table_count(session, schema, table, filters=None, match="and"):
        s = sessions.get(session)
        return s.run(s.adapter.count, schema, table, filters or [], match)

    @server.method("table.preview")
    def table_preview(session, schema, table, changes):
        s = sessions.get(session)
        return s.run(s.adapter.preview, schema, table, changes)

    @server.method("table.commit")
    def table_commit(session, schema, table, changes, confirmed=False):
        s = sessions.get(session)
        if guarded(s, confirmed):
            stmts = s.run(s.adapter.preview, schema, table, changes)["statements"]
            ask(s, False, f"Save {len(stmts)} change{'s' if len(stmts) != 1 else ''} to {table}?", "\n".join(stmts))
        return s.run(s.adapter.commit, schema, table, changes)

    @server.method("struct.get")
    def struct_get(session, schema, table):
        s = sessions.get(session)
        return s.run(s.adapter.structure, schema, table)

    @server.method("struct.preview")
    def struct_preview(session, schema, table, changes):
        s = sessions.get(session)
        return s.run(s.adapter.alter_preview, schema, table, changes)

    @server.method("struct.apply")
    def struct_apply(session, schema, table, changes, confirmed=False):
        s = sessions.get(session)
        if guarded(s, confirmed):
            stmts = s.run(s.adapter.alter_preview, schema, table, changes)["statements"]
            ask(s, False, f"Change the structure of {table}?", "\n".join(stmts))
        return s.run(s.adapter.alter, schema, table, changes)

    @server.method("table.drop")
    def table_drop(session, schema, table, kind="table", confirmed=False):
        s = sessions.get(session)
        ask(s, confirmed, f"Drop the {kind} {table}?")
        return s.run(s.adapter.drop, schema, table, kind)

    @server.method("table.create_preview")
    def table_create_preview(session, schema, table, columns):
        s = sessions.get(session)
        return s.run(s.adapter.create_table_preview, schema, table, columns)

    @server.method("table.create")
    def table_create(session, schema, table, columns, confirmed=False):
        s = sessions.get(session)
        if guarded(s, confirmed):
            stmts = s.run(s.adapter.create_table_preview, schema, table, columns)["statements"]
            ask(s, False, f"Create the table {table}?", "\n".join(stmts))
        return s.run(s.adapter.create_table, schema, table, columns)

    @server.method("db.create")
    def db_create(session, name, collection="", confirmed=False):
        s = sessions.get(session)
        ask(s, confirmed, f"Create the database {name}?")
        if collection:
            s.run(s.adapter.create_database, name, collection)
        else:
            s.run(s.adapter.create_database, name)
        return {"databases": s.run(s.adapter.databases), "database": s.adapter.database}

    @server.method("db.drop")
    def db_drop(session, name, confirmed=False):
        s = sessions.get(session)
        ask(s, confirmed, f"Drop the database {name}?")
        s.run(s.adapter.drop_database, name)
        return {"databases": s.run(s.adapter.databases), "database": s.adapter.database}

    def run_job(s, fn, *args):
        job = dump.Job()
        s.job = job
        try:
            return s.run(fn, s.adapter, job, *args)
        finally:
            s.job = None

    @server.method("db.backup")
    def db_backup(session, path, database=None, overwrite=False):
        s = sessions.get(session)
        return run_job(s, dump.backup, path, database, overwrite)

    @server.method("db.restore")
    def db_restore(session, path, database=None, confirmed=False):
        s = sessions.get(session)
        ask(s, confirmed, f"Restore {os.path.basename(str(path))} into {database or s.adapter.database}?")
        return run_job(s, dump.restore, path, database)

    @server.method("table.truncate")
    def table_truncate(session, schema, table, confirmed=False):
        s = sessions.get(session)
        ask(s, confirmed, f"Delete every row in {table}?")
        return s.run(s.adapter.truncate, schema, table)

    @server.method("export.run")
    def export_run(session, target, format, path, overwrite=False, table_name=None):  # noqa: A002
        s = sessions.get(session)
        return s.run(transfer.export, s.adapter, target, format, path, overwrite, table_name)

    @server.method("import.preview")
    def import_preview(path, delimiter=None):
        return transfer.preview_csv(path, delimiter or None)

    @server.method("import.run")
    def import_run(session, schema, table, path, mapping, delimiter=",", header=True, empty_null=True, confirmed=False):
        s = sessions.get(session)
        ask(s, confirmed, f"Import {os.path.basename(str(path))} into {table}?")
        return s.run(transfer.import_csv, s.adapter, schema, table, path, mapping, delimiter, header, empty_null)

    @server.method("query.run")
    def query_run(session, text, cursor=None, confirmed=False):
        s = sessions.get(session)
        engine = s.conn.get("engine")
        text = str(text or "")
        if cursor is None:
            stmts = sqlsplit.split(text, engine)
        else:
            one = sqlsplit.at(text, sqlsplit.from_utf16(text, int(cursor)), engine)
            stmts = [one] if one else []
        if not stmts:
            raise Error("invalid", "There is no SQL to run")
        if guarded(s, confirmed):
            writes = safety.sql_writes([sql for _, _, sql in stmts], engine)
            if writes:
                n = len(writes)
                ask(s, False, f"Run {n} statement{'s' if n != 1 else ''} that can change data?", ";\n".join(writes) + ";")
        out = s.run(s.adapter.run, [sql for _, _, sql in stmts])
        ddl = any(sqlsplit.keyword(sql) in DDL for _, _, sql in stmts)
        if ddl:
            s.run(s.adapter.forget)
        for r in out["results"]:
            # rows of a plain one-table SELECT can be edited in the result grid
            if r.get("columns") and not s.conn.get("readonly"):
                r["source"] = s.run(s.adapter.edit_source, r["sql"], [c["name"] for c in r["columns"]])
        results = out["results"]
        err = results[-1].get("error") if results else None
        start, end = stmts[0][0], stmts[-1][1]
        history.add({"conn": s.conn.get("id"), "name": s.conn.get("name"), "engine": engine,
                     "database": out["database"], "sql": text[start:end].strip(), "ok": err is None,
                     "error": err["message"] if err else None, "ms": sum(r["ms"] for r in results),
                     "statements": len(stmts)})
        return {**out, "ddl": ddl, "range": [sqlsplit.to_utf16(text, start), sqlsplit.to_utf16(text, end)]}

    @server.method("file.list")
    def file_list(path="", extensions=None):
        return files.listing(path, extensions)

    @server.method("file.read")
    def file_read(path):
        return files.read_text(path)

    @server.method("file.write")
    def file_write(path, text, overwrite=False):
        return files.write_text(path, text, overwrite)

    @server.method("query.run_file")
    def query_run_file(session, path, confirmed=False):
        """Run every statement of a SQL file, stopping at the first error; returns a summary, not the rows."""
        s = sessions.get(session)
        engine = s.conn.get("engine")
        text = files.read_text(path, files.MAX_SCRIPT_BYTES)["text"]
        name = os.path.basename(str(path))
        stmts = [sql for _, _, sql in sqlsplit.split(text, engine)]
        if not stmts:
            raise Error("invalid", f"{name} has no SQL to run")
        if guarded(s, confirmed):
            writes = safety.sql_writes(stmts, engine)
            if writes:
                shown = ";\n".join(writes[:30]) + ";" + (f"\n… and {len(writes) - 30} more" if len(writes) > 30 else "")
                ask(s, False, f"Run {name}? {len(writes)} of its {len(stmts)} statements can change data.", shown)
        out = s.run(s.adapter.run, stmts, 1)
        results = out["results"]
        ddl = any(sqlsplit.keyword(sql) in DDL for sql in stmts[:len(results)])
        if ddl:
            s.run(s.adapter.forget)
        err = results[-1].get("error") if results else None
        ms = sum(r["ms"] for r in results)
        history.add({"conn": s.conn.get("id"), "name": s.conn.get("name"), "engine": engine,
                     "database": out["database"], "sql": f"-- {name} ({len(stmts)} statements)", "ok": err is None,
                     "error": err["message"] if err else None, "ms": ms, "statements": len(results)})
        summary = {"total": len(stmts), "ran": len(results) - (1 if err else 0), "ms": ms, "ddl": ddl,
                   "affected": sum(max(r.get("affected") or 0, 0) for r in results), "database": out["database"]}
        if err:
            summary["error"] = {**err, "statement": len(results), "sql": results[-1]["sql"][:2000]}
        return summary

    @server.method("query.explain")
    def query_explain(session, text, cursor=None, analyze=False):
        s = sessions.get(session)
        engine = s.conn.get("engine")
        text = str(text or "")
        if cursor is None:
            stmts = sqlsplit.split(text, engine)
            if len(stmts) > 1:
                raise Error("invalid", "Explain works on one statement; put the cursor in it or select it")
            one = stmts[0] if stmts else None
        else:
            one = sqlsplit.at(text, sqlsplit.from_utf16(text, int(cursor)), engine)
        if not one:
            raise Error("invalid", "There is no SQL to explain")
        sql = one[2]
        if sqlsplit.keyword(sql) == "EXPLAIN":
            raise Error("invalid", "Leave out EXPLAIN: the Explain button adds it")
        if analyze and safety.sql_writes([sql], engine):
            raise Error("invalid", "Explain analyze runs the statement, so it is only offered for queries that read")
        out = s.run(s.adapter.explain, sql, bool(analyze))
        return {**out, "sql": sql, "analyze": bool(analyze),
                "range": [sqlsplit.to_utf16(text, one[0]), sqlsplit.to_utf16(text, one[1])]}

    @server.method("server.processes")
    def server_processes(session):
        s = sessions.get(session)
        return {"processes": s.run(s.adapter.processes)}

    @server.method("server.kill")
    def server_kill(session, id, hard=False, confirmed=False):  # noqa: A002
        s = sessions.get(session)
        ask(s, confirmed, f"{'Close the connection of' if hard else 'Stop the query of'} process {id}?")
        return s.run(s.adapter.kill, id, bool(hard))

    @server.method("users.list")
    def users_list(session):
        s = sessions.get(session)
        family = "mysql" if s.adapter.d.name == "mysql" else s.adapter.d.name
        return {"users": s.run(s.adapter.users), "privileges": admin.PRIVILEGES.get(family, {})}

    @server.method("users.grants")
    def users_grants(session, name, host=""):
        s = sessions.get(session)
        return {"grants": s.run(s.adapter.user_grants, {"name": name, "host": host})}

    USER_CHANGES = {"create": admin.create_user, "alter": admin.alter_user, "drop": admin.drop_user,
                    "grant": admin.grant, "revoke": lambda d, spec: admin.grant(d, spec, revoke=True)}

    @server.method("users.change")
    def users_change(session, op, spec, preview=False, confirmed=False):
        s = sessions.get(session)
        build_sql = USER_CHANGES.get(op)
        if build_sql is None:
            raise Error("invalid", f"unknown change {op!r}")
        pairs = build_sql(s.adapter.d, spec or {})
        masked = [m for _, m in pairs]
        if preview:
            return {"statements": masked}
        ask(s, confirmed, f"Apply {len(pairs)} user change{'s' if len(pairs) != 1 else ''}?", ";\n".join(masked) + ";")
        s.run(s.adapter.admin_run, [sql for sql, _ in pairs])
        return {"ok": True, "statements": masked}

    @server.method("query.cancel")
    def query_cancel(session):
        sessions.get(session).cancel()
        return {"ok": True}

    @server.method("query.history")
    def query_history(conn=None, search="", limit=200):
        return {"items": history.list(conn, search, limit)}

    @server.method("query.history_clear")
    def query_history_clear(conn=None):
        history.clear(conn)
        return {"ok": True}

    @server.method("query.save")
    def query_save(name, sql, id=None):  # noqa: A002
        return saved.save(name, sql, id)

    @server.method("query.saved")
    def query_saved():
        return {"items": saved.list()}

    @server.method("query.delete_saved")
    def query_delete_saved(id):  # noqa: A002
        saved.delete(id)
        return {"ok": True}

    def kind(session, engine):
        s = sessions.get(session)
        if getattr(s.adapter, "engine", "") != engine:
            raise Error("invalid", "this is not a " + ("Redis" if engine == "redis" else "MongoDB") + " connection")
        return s

    def label(key):
        if isinstance(key, dict) and key.get("$t") == "bytes":
            return "‹binary key›"
        return str(key)

    def remember(s, text, ms, error=None):
        history.add({"conn": s.conn.get("id"), "name": s.conn.get("name"), "engine": s.conn.get("engine"),
                     "database": s.adapter.database, "sql": str(text).strip(), "ok": error is None,
                     "error": error, "ms": ms, "statements": 1})

    @server.method("redis.scan")
    def redis_scan(session, pattern="*", cursor="0", count=500, type=None):  # noqa: A002
        s = kind(session, "redis")
        return s.run(s.adapter.scan, pattern, cursor, count, type)

    @server.method("redis.keyspace")
    def redis_keyspace(session):
        s = kind(session, "redis")
        return s.run(s.adapter.keyspace)

    @server.method("redis.get")
    def redis_get(session, key):
        s = kind(session, "redis")
        return s.run(s.adapter.get, key)

    @server.method("redis.create")
    def redis_create(session, key, type, value, ttl=None, confirmed=False):  # noqa: A002
        s = kind(session, "redis")
        ask(s, confirmed, f"Create the key {label(key)}?")
        return s.run(s.adapter.create, key, type, value, ttl)

    @server.method("redis.edit")
    def redis_edit(session, key, type, action, item=None, value=None, score=None, old=None, confirmed=False):  # noqa: A002
        s = kind(session, "redis")
        ask(s, confirmed, f"Change the key {label(key)}?")
        return s.run(s.adapter.edit, key, type, action, item, value, score, old)

    @server.method("redis.del")
    def redis_del(session, keys, confirmed=False):
        s = kind(session, "redis")
        ask(s, confirmed, f"Delete {len(keys)} key{'s' if len(keys) != 1 else ''}?", "\n".join(label(k) for k in keys))
        return s.run(s.adapter.delete, keys)

    @server.method("redis.rename")
    def redis_rename(session, key, new, confirmed=False):
        s = kind(session, "redis")
        ask(s, confirmed, f"Rename {label(key)} to {label(new)}?")
        return s.run(s.adapter.rename, key, new)

    @server.method("redis.ttl")
    def redis_ttl(session, key, ttl=None, confirmed=False):
        s = kind(session, "redis")
        ask(s, confirmed, f"Change the expiry of {label(key)}?")
        return s.run(s.adapter.expire, key, ttl)

    @server.method("redis.cmd")
    def redis_cmd(session, text, confirmed=False):
        s = kind(session, "redis")
        if guarded(s, confirmed):
            args = s.adapter.parse(text)
            if args[0].upper() != "SELECT" and s.run(s.adapter.writes, args):
                ask(s, False, "Run a command that can change data?", str(text).strip())
        try:
            out = s.run(s.adapter.command, text)
        except Error as e:
            if e.code == "query":
                remember(s, text, 0, e.message)
            raise
        remember(s, text, out["ms"])
        return out

    @server.method("mongo.find")
    def mongo_find(session, collection, filter="", sort="", projection="", skip=0, limit=100):  # noqa: A002
        s = kind(session, "mongodb")
        return s.run(s.adapter.find, collection, filter, sort, projection, skip, limit)

    @server.method("mongo.aggregate")
    def mongo_aggregate(session, collection, pipeline, limit=100, confirmed=False):
        s = kind(session, "mongodb")
        if guarded(s, confirmed) and str(pipeline or "").strip():
            from . import mongojs  # needs the drivers' bson
            stages = mongojs.parse(pipeline, "pipeline")
            if isinstance(stages, list) and safety.pipeline_writes(stages):
                ask(s, False, f"Run a pipeline that writes ($out/$merge) from {collection}?", pipeline)
        return s.run(s.adapter.aggregate, collection, pipeline, limit)

    @server.method("mongo.count")
    def mongo_count(session, collection, filter=""):  # noqa: A002
        s = kind(session, "mongodb")
        return s.run(s.adapter.count, collection, filter)

    @server.method("mongo.insert")
    def mongo_insert(session, collection, text, confirmed=False):
        s = kind(session, "mongodb")
        ask(s, confirmed, f"Insert into {collection}?", text)
        return s.run(s.adapter.insert, collection, text)

    @server.method("mongo.update")
    def mongo_update(session, collection, id, text, confirmed=False):  # noqa: A002
        s = kind(session, "mongodb")
        ask(s, confirmed, f"Replace a document in {collection}?", text)
        return s.run(s.adapter.update, collection, id, text)

    @server.method("mongo.delete")
    def mongo_delete(session, collection, ids, confirmed=False):
        s = kind(session, "mongodb")
        ask(s, confirmed, f"Delete {len(ids)} document{'s' if len(ids) != 1 else ''} from {collection}?")
        return s.run(s.adapter.delete, collection, ids)

    @server.method("mongo.indexes")
    def mongo_indexes(session, collection):
        s = kind(session, "mongodb")
        return {"indexes": s.run(s.adapter.indexes, collection)}

    @server.method("mongo.create_index")
    def mongo_create_index(session, collection, keys, unique=False, name="", confirmed=False):
        s = kind(session, "mongodb")
        ask(s, confirmed, f"Create an index on {collection}?", keys)
        return s.run(s.adapter.create_index, collection, keys, unique, name)

    @server.method("mongo.drop_index")
    def mongo_drop_index(session, collection, name, confirmed=False):
        s = kind(session, "mongodb")
        ask(s, confirmed, f"Drop the index {name} on {collection}?")
        return s.run(s.adapter.drop_index, collection, name)

    @server.method("mongo.create_collection")
    def mongo_create_collection(session, name, confirmed=False):
        s = kind(session, "mongodb")
        ask(s, confirmed, f"Create the collection {name}?")
        return s.run(s.adapter.create_collection, name)

    @server.method("mongo.drop_collection")
    def mongo_drop_collection(session, name, confirmed=False):
        s = kind(session, "mongodb")
        ask(s, confirmed, f"Drop {name} and everything in it?")
        return s.run(s.adapter.drop_collection, name)

    @server.method("mongo.run")
    def mongo_run(session, text, confirmed=False):
        s = kind(session, "mongodb")
        from . import mongojs
        if guarded(s, confirmed) and safety.mongo_writes(mongojs.parse(text, "command")):
            ask(s, False, "Run a command that can change data?", str(text).strip())
        try:
            out = s.run(s.adapter.command, text)
        except Error as e:
            if e.code == "query":
                remember(s, text, 0, e.message)
            raise
        remember(s, text, out["ms"])
        return out

    return server


def main():
    server = build(Store())
    # The window stops us with SIGTERM; exit through the finally so tunnels close.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    server.event("ready", version=__version__)
    try:
        server.serve()
    finally:
        server.sessions.close_all()


if __name__ == "__main__":
    main()
