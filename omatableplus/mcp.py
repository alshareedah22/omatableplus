"""MCP server over stdio, standard library only: JSON-RPC 2.0, one message per line.

`python -m omatableplus.mcp [--write | --destructive]` lets an MCP client (Claude Code, Codex...) work with the
connections saved in OmaTablePlus. Each tool calls the same helper methods the window uses, in this process, so
read-only connections and production tags behave exactly as they do in the window.

Rules that keep a model from doing more than a person at the window could:
  * the helper's `confirmed` flag is never an argument: a production write is refused, not confirmed;
  * three tiers chosen at launch, never by the model: read (default), --write, --destructive (implies --write);
  * without --write every SQL session is opened read-only, which the database driver enforces;
  * Redis and MongoDB are read only, and files only live in one folder (see mcpfiles).

Requests are served one at a time.
"""

import argparse
import json
import os
import signal
import sys

from . import __version__, sqlsplit
from .mcp_admin import TOOLS as ADMIN_TOOLS
from .mcp_admin import AdminTools
from .mcp_nosql import TOOLS as NOSQL_TOOLS
from .mcp_nosql import NosqlTools
from .mcpfiles import Sandbox
from .mcpschema import (CELL_LIMIT, CHANGES, CONNECTION, DATABASE, DEFAULT_ROWS, DESTRUCTIVE, FAMILIES, FILTERS,
                        HISTORY_LIMIT, MATCH_LIMIT, READ, ROWS, SCHEMA, SORT, SQL, SQL_ENGINES, TABLE, WRITE,
                        check, page_size, schema_of, shrink, shrink_rows)
from .mcpsql import refuse_file_effects
from .protocol import Error, encode

SUPPORTED = ("2024-11-05", "2025-03-26", "2025-06-18")
LATEST = SUPPORTED[-1]
DEFAULT_FILES = os.path.join(os.path.expanduser("~"), "Downloads", "omatableplus-mcp")
DESTRUCTIVE_SQL = {"DROP", "TRUNCATE", "GRANT", "REVOKE"}

HINTS = {
    "confirm": ("This connection is tagged production and MCP cannot confirm a write. Run the change in the "
                "OmaTablePlus window, or retag the connection."),
    "destructive": "Start the MCP server with --destructive to allow dropping, truncating and changing rights.",
}
READ_ONLY_HINT = "The MCP server was started without --write, so every session is read-only."
ENGINES_SAID = "SQL (PostgreSQL, MySQL/MariaDB, SQLite), plus Redis and MongoDB for reading"
INSTRUCTIONS = {
    READ: (f"OmaTablePlus databases: {ENGINES_SAID}. This server is read-only: SQL sessions are opened read-only and "
           "the database refuses writes. Start with list_connections, then list_objects, describe_object or "
           "search_schema, then search_rows or execute_sql; redis_* and mongo_* tools read Redis and MongoDB."),
    WRITE: (f"OmaTablePlus databases: {ENGINES_SAID}. This server is in write mode: SQL writes run on connections "
            "that are neither read-only nor tagged production; production writes are refused. Start with "
            "list_connections. To edit rows prefer preview_changes then commit_changes (parameterised, by primary "
            "key, all or nothing) over hand-written SQL. Dropping, truncating and rights changes are not enabled. "
            "Import, export, backup and restore only use files of the MCP files folder (list_files)."),
    DESTRUCTIVE: (f"OmaTablePlus databases: {ENGINES_SAID}. This server is in destructive mode: besides writing, it "
                  "may drop, truncate, restore, stop processes and change users, on connections that are neither "
                  "read-only nor tagged production; production is still refused. Preview before you change: "
                  "preview_changes, preview_alter_table, preview_user_change. Import, export, backup and restore "
                  "only use files of the MCP files folder (list_files)."),
}

# name, description, properties, required, tier
SQL_TOOLS = (
    ("list_connections", "List the saved connections and OmaDBngin servers (id, name, engine, family, tag, "
     "read-only). family says which tools fit: sql, redis (redis_*) or mongodb (mongo_*).", {}, (), READ),
    ("list_databases", "List the databases of a connection and the current one.",
     {"connection": CONNECTION}, ("connection",), READ),
    ("list_objects", "List tables and views per schema.",
     {"connection": CONNECTION, "database": DATABASE, "schema": {**SCHEMA, "description": "Only this schema."}},
     ("connection",), READ),
    ("describe_object", "Columns (with primary key flags), indexes, foreign keys and CREATE statement of a table; "
     "or the definition of a view, function or trigger.",
     {"connection": CONNECTION, "database": DATABASE, "schema": SCHEMA,
      "name": {"type": "string", "description": "Object name."},
      "kind": {"type": "string", "description": "table (default), view, function or trigger."}},
     ("connection", "schema", "name"), READ),
    ("search_schema", "Find tables and columns whose name contains a text (case-insensitive).",
     {"connection": CONNECTION, "database": DATABASE, "pattern": {"type": "string", "description": "Text to find."}},
     ("connection", "pattern"), READ),
    ("search_rows", "Read rows of one table with filters, ordering and paging; also says how many rows match.",
     {"connection": CONNECTION, "database": DATABASE, "schema": SCHEMA, "table": TABLE, "filters": FILTERS,
      "match": {"type": "string", "description": "and (default) or or: how filters combine."}, "sort": SORT,
      "limit": ROWS, "offset": {"type": "integer", "description": "Rows to skip."}},
     ("connection", "schema", "table"), READ),
    ("execute_sql", "Run SQL. Rows are capped and long cells shortened; `truncated` says when rows were cut. "
     "Writes only run in write mode, on connections that are not read-only or production. SQL that opens or "
     "creates files (ATTACH, VACUUM INTO, COPY, INTO OUTFILE) is refused: export_data and backup_database do that.",
     {"connection": CONNECTION, "database": DATABASE, "sql": SQL, "max_rows": ROWS}, ("connection", "sql"), READ),
    ("explain_sql", "Show the execution plan of one statement; analyze=true runs it (reads only).",
     {"connection": CONNECTION, "database": DATABASE, "sql": SQL,
      "analyze": {"type": "boolean", "description": "Run the statement to get real timings."}},
     ("connection", "sql"), READ),
    ("recent_queries", "Recent queries run from OmaTablePlus (the window and this server), newest first.",
     {"connection": CONNECTION, "search": {"type": "string", "description": "Only SQL containing this text."},
      "limit": {"type": "integer", "description": f"At most {HISTORY_LIMIT} (default 20)."}}, (), READ),
    ("preview_changes", "Show the exact SQL that inserts, updates and deletes would run, without running it. "
     "Needs a primary key (SQLite: rowid).",
     {"connection": CONNECTION, "database": DATABASE, "schema": SCHEMA, "table": TABLE, **CHANGES},
     ("connection", "schema", "table"), READ),
    ("commit_changes", "Apply deletes, then updates, then inserts in one transaction, by primary key. Each update "
     "and delete must match exactly one row or nothing is saved.",
     {"connection": CONNECTION, "database": DATABASE, "schema": SCHEMA, "table": TABLE, **CHANGES},
     ("connection", "schema", "table"), WRITE),
)

TOOLS = SQL_TOOLS + ADMIN_TOOLS + NOSQL_TOOLS


def failure(error, write):
    out = {"code": error.code, "message": error.message}
    if error.detail:
        out["detail"] = error.detail
    if error.code in HINTS:
        out["hint"] = HINTS[error.code]
    elif error.code == "readonly" and not write:
        out["hint"] = READ_ONLY_HINT
    return out


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class McpServer(AdminTools, NosqlTools):
    def __init__(self, helper, write=False, destructive=False, files=None, out=None):
        self.helper = helper
        self.methods = helper.methods
        self.destructive = destructive
        self.write = write or destructive
        self.files = Sandbox(files or DEFAULT_FILES)
        self.out = out or sys.stdout
        self.open = {}      # (connection id, read-only) -> helper session id
        enabled = {READ} | ({WRITE} if self.write else set()) | ({DESTRUCTIVE} if destructive else set())
        self.tools = {name: {"description": description, "inputSchema": schema_of(properties, required)}
                      for name, description, properties, required, tier in TOOLS if tier in enabled}
        self.requests = {"initialize": self.initialize, "ping": lambda params: {}, "tools/list": self.tools_list,
                         "tools/call": self.tools_call}

    # JSON-RPC ---------------------------------------------------------------
    def send(self, message):
        self.out.write(json.dumps({"jsonrpc": "2.0", **message}, default=encode) + "\n")
        self.out.flush()

    def handle(self, line):
        try:
            message = json.loads(line)
        except ValueError:
            return self.send({"id": None, "error": {"code": -32700, "message": "Parse error"}})
        if not isinstance(message, dict):
            return self.send({"id": None, "error": {"code": -32600, "message": "Invalid request"}})
        if "method" not in message or "id" not in message:
            return      # a notification, or the answer to a request this server never sent
        rid = message["id"]
        handler = self.requests.get(message["method"])
        if handler is None:
            return self.send({"id": rid, "error": {"code": -32601, "message": f"Method not found: {message['method']}"}})
        try:
            params = message.get("params") or {}
            if not isinstance(params, dict):
                raise RpcError(-32602, "params must be an object")
            result = handler(params)
        except RpcError as e:
            self.send({"id": rid, "error": {"code": e.code, "message": e.message}})
        else:
            self.send({"id": rid, "result": result})

    def serve(self, inp=None):
        try:
            for line in inp or sys.stdin:
                if line.strip():
                    self.handle(line)
        finally:
            self.close()

    def close(self):
        self.open.clear()
        self.helper.sessions.close_all()

    def initialize(self, params):
        wanted = params.get("protocolVersion")
        return {"protocolVersion": wanted if wanted in SUPPORTED else LATEST, "capabilities": {"tools": {}},
                "serverInfo": {"name": "omatableplus", "version": __version__}, "instructions": INSTRUCTIONS[self.tier()]}

    def tier(self):
        return DESTRUCTIVE if self.destructive else WRITE if self.write else READ

    def tools_list(self, params):
        return {"tools": [{"name": name, **tool} for name, tool in self.tools.items()]}

    def tools_call(self, params):
        name = params.get("name")
        if name not in self.tools:
            raise RpcError(-32602, f"Unknown tool: {name}")
        try:
            arguments = params.get("arguments") or {}
            check(arguments, self.tools[name]["inputSchema"])
            payload = getattr(self, "tool_" + name)(**arguments)
            failed = any("error" in r for r in payload.get("results", ()))
        except Error as e:
            if e.code == "tunnel":
                self.drop_sessions()
            payload, failed = {"error": failure(e, self.write)}, True
        except Exception as e:  # noqa: BLE001 - a tool must always answer, the model reads the failure
            payload, failed = {"error": {"code": "internal", "message": f"{type(e).__name__}: {e}"}}, True
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=encode)
        return {"content": [{"type": "text", "text": text}], "isError": failed}

    # connections and sessions -----------------------------------------------
    def connection(self, ref, engines=SQL_ENGINES):
        saved = self.helper.sessions.store.list()
        found = self.matching(saved, ref)
        if not found:
            found = self.matching(self.methods["conn.list"]()["omadb"], ref)
        if not found:
            raise Error("notfound", f"no connection {ref!r}; call list_connections")
        if len(found) > 1:
            raise Error("invalid", f"{len(found)} connections are named {ref!r}; use the id")
        if found[0].get("engine") not in engines:
            raise Error("unsupported", f"{found[0].get('engine')} connections do not fit this tool "
                                       f"(it works with {', '.join(engines)}); list_connections gives each family")
        return found[0]

    def saved_connection(self, ref):
        """A connection of the OmaTablePlus config; OmaDBngin servers are not ours to copy or delete."""
        found = self.matching(self.helper.sessions.store.list(), ref)
        if not found:
            raise Error("notfound", f"no saved connection {ref!r}; call list_connections")
        if len(found) > 1:
            raise Error("invalid", f"{len(found)} connections are named {ref!r}; use the id")
        return found[0]

    @staticmethod
    def matching(connections, ref):
        by_id = [c for c in connections if c["id"] == ref]
        return by_id or [c for c in connections if str(c.get("name", "")).lower() == ref.lower()]

    def session(self, ref, database=None, engines=SQL_ENGINES, readonly=None):
        """readonly=True asks for a session the driver keeps read-only whatever the mode (exports, NoSQL)."""
        readonly = not self.write if readonly is None else readonly
        conn = self.connection(ref, engines)
        key = (conn["id"], readonly)
        sid = self.open.get(key)
        if sid not in self.helper.sessions.items:
            sid = self.methods["session.open"](id=conn["id"], readonly=readonly)["session"]
            self.open[key] = sid
        if database:
            self.methods["session.use"](session=sid, database=database)
        return sid

    def drop_sessions(self):
        for sid in self.open.values():
            self.helper.sessions.close(sid)
        self.open.clear()

    def drop_sessions_of(self, conn_id):
        for key in [key for key in self.open if key[0] == conn_id]:
            self.helper.sessions.close(self.open.pop(key))

    # tools ------------------------------------------------------------------
    def tool_list_connections(self):
        listed = self.methods["conn.list"]()
        keep = ("id", "name", "engine", "tag", "readonly", "host", "port", "database", "folder")
        return {"connections": [{**{k: c[k] for k in keep if k in c}, "family": FAMILIES.get(c.get("engine")),
                                 "supported": c.get("engine") in FAMILIES}
                                for c in listed["connections"] + listed["omadb"]]}

    def tool_list_databases(self, connection):
        return self.methods["session.databases"](session=self.session(connection))

    def tool_list_objects(self, connection, database=None, schema=None):
        tree = self.methods["schema.tree"](session=self.session(connection, database))
        return {"database": tree["database"], "schemas": [s for s in tree["schemas"] if not schema or s["name"] == schema]}

    def tool_describe_object(self, connection, schema, name, database=None, kind="table"):
        sid = self.session(connection, database)
        if kind in ("table", "view"):
            return self.methods["struct.get"](session=sid, schema=schema, table=name)
        return self.methods["schema.definition"](session=sid, schema=schema, name=name, kind=kind)

    def tool_search_schema(self, connection, pattern, database=None):
        catalog = self.methods["schema.catalog"](session=self.session(connection, database))
        needle = pattern.lower()
        matches = []
        for table in catalog["tables"]:
            by_name = needle in table["name"].lower()
            columns = table["columns"] if by_name else [c for c in table["columns"] if needle in c["name"].lower()]
            if by_name or columns:
                matches.append({"schema": table["schema"], "table": table["name"], "kind": table["kind"],
                                "columns": columns[:MATCH_LIMIT]})
        return {"matches": matches[:MATCH_LIMIT], "truncated": len(matches) > MATCH_LIMIT or catalog["truncated"]}

    def tool_search_rows(self, connection, schema, table, database=None, filters=None, match="and", sort=None,
                         limit=DEFAULT_ROWS, offset=0):
        sid = self.session(connection, database)
        offset = max(0, offset)
        where = {"session": sid, "schema": schema, "table": table, "filters": filters or [], "match": match}
        page = self.methods["table.rows"](**where, sort=sort or [], offset=offset, limit=page_size(limit))
        matched = self.methods["table.count"](**where)["count"]
        return {"columns": [c["name"] for c in page["columns"]], "rows": shrink_rows(page["rows"]),
                "key": page["key"], "matched": matched, "truncated": matched > offset + len(page["rows"])}

    def tool_execute_sql(self, connection, sql, database=None, max_rows=DEFAULT_ROWS):
        engine = self.connection(connection)["engine"]
        refuse_file_effects(sql, engine)
        if self.write and not self.destructive:
            self.refuse_destructive_sql(sql, engine)
        out = self.methods["query.run"](session=self.session(connection, database), text=sql)
        return {"database": out["database"], "results": [self.result(r, page_size(max_rows)) for r in out["results"]]}

    @staticmethod
    def refuse_destructive_sql(sql, engine):
        """A first-keyword check, not a parser: the database user's rights remain the real boundary."""
        for _, _, statement in sqlsplit.split(sql, engine):
            words = sqlsplit.words(statement, engine)
            first = words[0] if words else ""
            if first in DESTRUCTIVE_SQL or (first == "ALTER" and "DROP" in words):
                raise Error("destructive", f"{first} is refused without --destructive")

    @staticmethod
    def result(raw, cap):
        item = {"sql": shrink(raw["sql"][:CELL_LIMIT]), "ms": raw["ms"]}
        if "error" in raw:
            item["error"] = raw["error"]
        elif "columns" in raw:
            item.update(columns=[c["name"] for c in raw["columns"]], rows=shrink_rows(raw["rows"][:cap]),
                        truncated=bool(raw["truncated"]) or len(raw["rows"]) > cap)
        else:
            item["affected"] = raw["affected"]
        return item

    def tool_explain_sql(self, connection, sql, database=None, analyze=False):
        refuse_file_effects(sql, self.connection(connection)["engine"])
        out = self.methods["query.explain"](session=self.session(connection, database), text=sql, analyze=analyze)
        return {k: v for k, v in out.items() if k != "range"}

    def tool_recent_queries(self, connection=None, search="", limit=20):
        conn_id = self.connection(connection)["id"] if connection else None
        items = self.methods["query.history"](conn=conn_id, search=search, limit=max(1, min(limit, HISTORY_LIMIT)))
        return {"items": [{**{k: v for k, v in item.items() if k != "conn"}, "sql": shrink(str(item.get("sql", "")))}
                          for item in items["items"]]}

    def tool_preview_changes(self, connection, schema, table, database=None, inserts=None, updates=None, deletes=None):
        return self.methods["table.preview"](
            session=self.session(connection, database), schema=schema, table=table,
            changes={"inserts": inserts or [], "updates": updates or [], "deletes": deletes or []})

    def tool_commit_changes(self, connection, schema, table, database=None, inserts=None, updates=None, deletes=None):
        return self.methods["table.commit"](
            session=self.session(connection, database), schema=schema, table=table,
            changes={"inserts": inserts or [], "updates": updates or [], "deletes": deletes or []})


def main(argv=None):
    from .__main__ import build
    from .store import Store

    parser = argparse.ArgumentParser(prog="python -m omatableplus.mcp", description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true",
                        help="allow writes on connections that are neither read-only nor tagged production")
    parser.add_argument("--destructive", action="store_true",
                        help="also allow drop, truncate, restore, killing processes and user changes (implies --write)")
    parser.add_argument("--files-dir", default=DEFAULT_FILES,
                        help="the only folder import, export, backup and restore use (default: %(default)s)")
    options = parser.parse_args(argv)
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    out, sys.stdout = sys.stdout, sys.stderr    # a stray print must never reach the client
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    McpServer(build(Store()), write=options.write, destructive=options.destructive, files=options.files_dir,
              out=out).serve()


if __name__ == "__main__":
    main()
