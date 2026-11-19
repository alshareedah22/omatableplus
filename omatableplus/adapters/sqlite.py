"""SQLite through the standard library. Keyless tables are edited by rowid."""

import os
import sqlite3
import time
import urllib.parse

from .. import explain
from ..protocol import Error
from .base import SQLAdapter

SCHEMA = "main"


class SQLite(SQLAdapter):
    engine = "sqlite"

    def connect(self):
        path = os.path.expanduser(self.conn.get("path") or "")
        if not os.path.isfile(path):
            raise Error("notfound", f"no SQLite file at {path}")
        uri = "file:" + urllib.parse.quote(os.path.abspath(path)) + ("?mode=ro" if self.conn.get("readonly") or self.conn.get("db_readonly") else "?mode=rw")
        with self.guard():
            self.db = sqlite3.connect(uri, uri=True, check_same_thread=False, isolation_level=None, timeout=5)
            self.db.execute("SELECT 1 FROM sqlite_master LIMIT 1")
            if self.conn.get("statement_timeout"):
                self.db.set_progress_handler(self.deadline(float(self.conn["statement_timeout"])), 10000)
        self.database = SCHEMA

    @staticmethod
    def deadline(seconds):
        """A progress handler that interrupts a statement running longer than `seconds`. The handler only runs
        while a statement does, so a long pause since its last call means a new statement has begun."""
        state = {"start": None, "last": 0.0}

        def handler():
            now = time.monotonic()
            if state["start"] is None or now - state["last"] > 1.0:
                state["start"] = now
            state["last"] = now
            return 1 if now - state["start"] > seconds else 0
        return handler

    def translate(self, exc):
        if isinstance(exc, sqlite3.DatabaseError) and "not a database" in str(exc):
            return Error("invalid", "this file is not a SQLite database")
        if isinstance(exc, sqlite3.OperationalError) and str(exc) == "interrupted":
            return Error("cancelled", "Query cancelled")
        return None

    def interrupt(self):
        self.db.interrupt()

    def version(self):
        return "SQLite " + sqlite3.sqlite_version

    def databases(self):
        return [SCHEMA]

    def use(self, database):
        if database != SCHEMA:
            raise Error("notfound", f"database {database!r} not found")

    def tree(self):
        self.forget()
        _, rows = self.query("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') "
                             "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name")
        return [{"name": SCHEMA, "tables": [n for n, t in rows if t == "table"],
                 "views": [n for n, t in rows if t == "view"], "functions": [], "sequences": []}]

    def relation_rows(self):
        pks, fks = [], []
        for (table,) in self.query("SELECT name FROM sqlite_master WHERE type = 'table' "
                                   "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'")[1]:
            q = self.d.quote(table)
            for _, name, _, _, _, pk in self.query(f"PRAGMA table_info({q})")[1]:
                if pk:
                    pks.append((SCHEMA, table, name))
            groups = {}
            for fid, _, ref, frm, to, *_ in self.query(f"PRAGMA foreign_key_list({q})")[1]:
                g = groups.setdefault(fid, [f"fk_{table}_{fid}", SCHEMA, table, [], SCHEMA, ref, []])
                g[3].append(frm)
                g[6].append(to)
            fks += [tuple(g) for g in groups.values()]
        return pks, fks

    def resolve_schema(self, table, quoted):
        return SCHEMA

    def definition(self, schema, name, kind):
        if kind not in ("view", "trigger"):
            raise Error("unsupported", "SQLite has no stored functions")
        rows = self.query("SELECT sql FROM sqlite_master WHERE type = ? AND name = ?", (kind, name))[1]
        if not rows:
            raise Error("notfound", f"no {kind} {name}")
        # SQLite cannot replace either in place: drop, then create again
        return {"sql": f"DROP {kind.upper()} IF EXISTS {self.d.quote(name)};\n{rows[0][0]};"}

    def drop_object(self, schema, name, kind, table=None, signature=None):
        if kind != "trigger":
            raise Error("unsupported", "SQLite has no stored functions")
        self.query(f"DROP TRIGGER {self.d.quote(name)}")
        self.forget()
        return {"ok": True}

    def explain(self, sql, analyze=False):
        if analyze:
            raise Error("unsupported", "SQLite has no EXPLAIN ANALYZE; Explain shows the plan")
        _, rows = self.query(f"EXPLAIN QUERY PLAN {sql}")
        out = explain.sqlite(rows)
        out["raw"] = "\n".join(str(r[-1]) for r in rows)
        return out

    def catalog_rows(self):
        _, rows = self.query(
            "SELECT m.name, m.type, p.name, p.type FROM sqlite_master m, pragma_table_info(m.name) p "
            "WHERE m.type IN ('table', 'view') AND m.name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY m.name, p.cid")
        return [(SCHEMA, t, kind, c, typ) for t, kind, c, typ in rows]

    def _kind(self, table):
        _, rows = self.query("SELECT type FROM sqlite_master WHERE name = ?", (table,))
        return rows[0][0] if rows else None

    def load_columns(self, schema, table):
        _, rows = self.query(f"PRAGMA {self.d.quote(schema)}.table_info({self.d.quote(table)})")
        return [{"name": name, "type": typ or "", "nullable": not notnull, "default": dflt, "pk": pk}
                for _, name, typ, notnull, dflt, pk in rows]

    def key(self, schema, table):
        pk = sorted((c for c in self.columns(schema, table) if c["pk"]), key=lambda c: c["pk"])
        return [c["name"] for c in pk]

    def row_extra(self, schema, table, key):
        if key or self._kind(table) != "table":
            return "", (), {}
        try:
            self.query(f"SELECT rowid FROM {self.d.table(schema, table)} LIMIT 0")
        except Error:  # WITHOUT ROWID
            return "", (), {}
        return "rowid AS __rowid, ", ("__rowid",), {"__rowid": "rowid"}

    def structure(self, schema, table):
        q = self.d.quote
        cols = self.load_columns(schema, table)
        if not cols:
            raise Error("notfound", f"table {table!r} not found")
        columns = [{"name": c["name"], "type": c["type"], "nullable": c["nullable"], "default": c["default"],
                    "comment": "", "pk": bool(c["pk"]), "extra": ""} for c in cols]
        indexes = []
        for _, name, unique, origin, _ in self.query(f"PRAGMA {q(schema)}.index_list({q(table)})")[1]:
            info = self.query(f"PRAGMA {q(schema)}.index_info({q(name)})")[1]
            indexes.append({"name": name, "unique": bool(unique), "primary": origin == "pk",
                            "columns": [r[2] for r in sorted(info)]})
        if any(c["pk"] for c in cols) and not any(i["primary"] for i in indexes):
            # an INTEGER PRIMARY KEY is the rowid and has no index of its own
            indexes.insert(0, {"name": "PRIMARY", "unique": True, "primary": True,
                               "columns": [c["name"] for c in sorted(cols, key=lambda c: c["pk"]) if c["pk"]]})
        fks = {}
        for fid, _, ref, frm, to, upd, dele, _ in self.query(f"PRAGMA {q(schema)}.foreign_key_list({q(table)})")[1]:
            fk = fks.setdefault(fid, {"name": f"fk_{fid}", "columns": [], "ref_schema": schema, "ref_table": ref,
                                      "ref_columns": [], "on_update": upd, "on_delete": dele})
            fk["columns"].append(frm)
            fk["ref_columns"].append(to)
        sqls = [r[0] + ";" for r in self.query(
            "SELECT sql FROM sqlite_master WHERE tbl_name = ? AND sql IS NOT NULL "
            "ORDER BY type = 'table' DESC, name", (table,))[1]]
        triggers = [{"name": n, "definition": sql + ";"} for n, sql in self.query(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ? ORDER BY name", (table,))[1]]
        return {"columns": columns, "indexes": indexes, "foreign_keys": list(fks.values()), "triggers": triggers,
                "ddl": "\n".join(sqls)}

    def truncate(self, schema, table):
        self.query(f"DELETE FROM {self.d.table(schema, table)}")
        return {"ok": True}
