"""What every SQL engine shares: paging, counting and committing grid edits.

Engines supply connect/close, the catalog queries (databases, tree, columns,
key) and an error translator; this class runs the sqlgen statements. One
adapter is used by one thread at a time (its session's worker).
"""

import contextlib
import math
import time

from .. import ddl, editable, explain, sqlgen
from ..protocol import Error

MAX_ROWS = 1000


class SQLAdapter:
    engine = ""
    snapshot = "BEGIN"     # opens a read that sees one state of the data
    running = False
    cancelled = False

    def __init__(self, conn):
        self.conn = conn
        self.database = conn.get("database") or ""
        self.d = sqlgen.dialect(conn.get("engine") or self.engine)
        self.db = None
        self._columns = {}

    # engine hooks ---------------------------------------------------------
    def connect(self):
        raise NotImplementedError

    def translate(self, exc):
        """Map a driver exception to an Error, or return None."""
        return None

    def version(self):
        return ""

    def databases(self):
        return []

    def use(self, database):
        raise Error("invalid", "this engine has no databases to switch")

    def tree(self):
        raise NotImplementedError

    def load_columns(self, schema, table):
        raise NotImplementedError

    def key(self, schema, table):
        """Columns that identify one row: the primary key, else a unique not-null key, else []."""
        return []

    def row_extra(self, schema, table, key):
        """Extra select list prefix, hidden column names and raw key SQL (sqlite rowid)."""
        return "", (), {}

    def structure(self, schema, table):
        """Columns, indexes, foreign keys and the CREATE statement."""
        raise NotImplementedError

    def relation_rows(self):
        """(pk rows [(schema, table, column)], fk rows [(name, schema, table, cols, ref_schema, ref_table, ref_cols)])."""
        raise Error("unsupported", "diagrams are not available for this engine")

    def relations(self, schema=None, limit=200):
        """Tables (with columns and key flags) and the foreign keys between them, for the diagram."""
        cat = [t for t in self.catalog()["tables"] if t["kind"] == "table" and (not schema or t["schema"] == schema)]
        truncated = len(cat) > limit
        cat = cat[:limit]
        pks, fks = self.relation_rows()
        keyed = {(s, t, c) for s, t, c in pks}
        names = {(t["schema"], t["name"]) for t in cat}
        tables = [{"schema": t["schema"], "name": t["name"],
                   "columns": [{**c, "pk": (t["schema"], t["name"], c["name"]) in keyed} for c in t["columns"]]}
                  for t in cat]
        links = [{"name": n, "from": [s, t, list(cols)], "to": [rs, rt, list(rcols)]}
                 for n, s, t, cols, rs, rt, rcols in fks if (s, t) in names and (rs, rt) in names]
        return {"tables": tables, "links": links, "truncated": truncated}

    def resolve_schema(self, table, quoted):
        """The schema an unqualified table name refers to, or None."""
        return self.database or None

    def edit_source(self, sql, names):
        """{schema, table, key} when the result of sql (with columns names) can be edited, else None."""
        found = editable.table_of(sql)
        if not found:
            return None
        schema, table, quoted = found
        try:
            schema = schema or self.resolve_schema(table, quoted)
            if not schema:
                return None
            columns = [c["name"] for c in self.columns(schema, table)]
            key = self.key(schema, table)
        except Error:
            return None
        if not key or len(set(names)) != len(names) or any(n not in columns for n in names) \
                or any(k not in names for k in key):
            return None
        return {"schema": schema, "table": table, "key": key}

    def definition(self, schema, name, kind):
        """{"sql": SQL that recreates the object, editable} for a view, function or trigger."""
        raise Error("unsupported", f"{kind} definitions are not available for this engine")

    def drop_object(self, schema, name, kind, table=None, signature=None):
        raise Error("unsupported", f"dropping a {kind} is not available for this engine")

    def explain(self, sql, analyze=False):
        """The plan of one statement as an explain tree; analyze runs it."""
        raise Error("unsupported", "query plans are not available for this engine")

    # administration ---------------------------------------------------------
    def processes(self):
        raise Error("unsupported", "the running queries are not available for this engine")

    def kill(self, pid, hard=False):
        raise Error("unsupported", "stopping queries is not available for this engine")

    def users(self):
        raise Error("unsupported", "users are managed by PostgreSQL and MySQL/MariaDB only")

    def user_grants(self, spec):
        raise Error("unsupported", "users are managed by PostgreSQL and MySQL/MariaDB only")

    def admin_run(self, statements):
        self.run_ddl(statements)
        return {"statements": len(statements)}

    def catalog_rows(self):
        """(schema, table, kind, column, type) for every column, in table and column order."""
        raise NotImplementedError

    def catalog(self, limit=20000):
        """Tables and views with their columns, for the editor's autocomplete."""
        tables, out = {}, []
        rows = self.catalog_rows()
        for schema, table, kind, column, typ in rows[:limit]:
            t = tables.get((schema, table))
            if t is None:
                t = tables[(schema, table)] = {"schema": schema, "name": table, "kind": kind, "columns": []}
                out.append(t)
            t["columns"].append({"name": column, "type": typ or ""})
        return {"tables": out, "truncated": len(rows) > limit}

    def describe(self, cursor):
        """Type names for the columns of the cursor's result."""
        return [""] * len(cursor.description)

    def interrupt(self):
        """Stop the running statement; called from another thread."""

    # shared ---------------------------------------------------------------
    @contextlib.contextmanager
    def guard(self):
        try:
            yield
        except Error:
            raise
        except Exception as e:  # noqa: BLE001 - translated or re-raised as a SQL error
            err = self.translate(e)
            raise (err or Error("sql", str(e).strip() or type(e).__name__)) from e

    def close(self):
        if self.db is not None:
            with contextlib.suppress(Exception):
                self.db.close()
            self.db = None

    def execute(self, cur, sql, params=()):
        # Without parameters drivers take the SQL as written; with them a
        # literal % must be doubled first.
        if params:
            cur.execute(self.d.finish(sql), params)
        else:
            cur.execute(sql)

    def query(self, sql, params=()):
        with self.guard():
            cur = self.db.cursor()
            try:
                self.execute(cur, sql, params)
                names = [c[0] for c in cur.description or ()]
                return names, [list(r) for r in cur.fetchall()] if cur.description else []
            finally:
                cur.close()

    def columns(self, schema, table, refresh=False):
        cache = (schema, table)
        if refresh or cache not in self._columns:
            cols = self.load_columns(schema, table)
            if not cols:
                raise Error("notfound", f"table {table!r} not found")
            self._columns[cache] = cols
        return self._columns[cache]

    def forget(self):
        self._columns.clear()

    @staticmethod
    def clean(value):
        if isinstance(value, float) and not math.isfinite(value):
            return str(value)
        return value

    def rows(self, schema, table, offset=0, limit=300, sort=(), filters=(), match="and"):
        cols = self.columns(schema, table, refresh=offset == 0 and not sort and not filters)
        names = [c["name"] for c in cols]
        key = self.key(schema, table)
        extra, hidden, raw = self.row_extra(schema, table, key)
        if raw:
            key = list(raw)
        sql, params = sqlgen.select(self.d, schema, table, names, sort, filters, match, limit, offset, extra)
        start = time.monotonic()
        got, rows = self.query(sql, params)
        info = {c["name"]: c for c in cols}
        return {
            "columns": [{"name": n, "type": info.get(n, {}).get("type", ""), "pk": n in key,
                         "nullable": info.get(n, {}).get("nullable", True),
                         "default": info.get(n, {}).get("default"), "hidden": n in hidden} for n in got],
            "rows": [[self.clean(v) for v in r] for r in rows],
            "key": key, "editable": bool(key), "offset": max(0, int(offset)), "limit": int(limit),
            "ms": round((time.monotonic() - start) * 1000),
        }

    def page(self, schema, table, offset=0, limit=300, sort=(), filters=(), match="and"):
        """A page and the number of matching rows, read from one snapshot."""
        out = {}
        with self.guard():
            cur = self.db.cursor()
            try:
                cur.execute(self.snapshot)
                try:
                    out = self.rows(schema, table, offset, limit, sort, filters, match)
                    out["matched"] = self.count(schema, table, filters, match)["count"]
                finally:
                    cur.execute("ROLLBACK")
            finally:
                cur.close()
        return out

    def count(self, schema, table, filters=(), match="and"):
        names = [c["name"] for c in self.columns(schema, table)]
        sql, params = sqlgen.count(self.d, schema, table, names, filters, match)
        return {"count": self.query(sql, params)[1][0][0]}

    def statements(self, schema, table, changes):
        names = [c["name"] for c in self.columns(schema, table)]
        key = self.key(schema, table)
        _, _, raw = self.row_extra(schema, table, key)
        if raw:
            key = list(raw)
        return sqlgen.changes(self.d, schema, table, names, key, changes or {}, raw)

    def preview(self, schema, table, changes):
        return {"statements": [sqlgen.render(self.d, sql, params) + ";"
                               for sql, params, _ in self.statements(schema, table, changes)]}

    def commit(self, schema, table, changes):
        stmts = self.statements(schema, table, changes)
        if not stmts:
            return {"affected": 0}
        cur = self.db.cursor()
        affected = 0
        with self.guard():
            try:
                cur.execute("BEGIN")
                for sql, params, expect_one in stmts:
                    self.execute(cur, sql, params)
                    n = cur.rowcount
                    if expect_one and n != 1:
                        raise Error("sql", f"{'No row' if n == 0 else f'{n} rows'} matched "
                                           f"{sqlgen.render(self.d, sql, params)}; nothing was saved")
                    affected += max(n, 0)
                cur.execute("COMMIT")
            except BaseException:
                with contextlib.suppress(Exception):
                    cur.execute("ROLLBACK")
                raise
            finally:
                cur.close()
        return {"affected": affected}

    # editor queries ---------------------------------------------------------
    def cancel(self):
        """Thread safe: stop the running run() after (or during) its current statement."""
        self.cancelled = True
        if self.running:
            with contextlib.suppress(Exception):
                self.interrupt()

    def run_one(self, sql, max_rows):
        with self.guard():
            cur = self.db.cursor()
            try:
                cur.execute(sql)
                if not cur.description:
                    return {"affected": cur.rowcount}
                names = [d[0] for d in cur.description]
                types = self.describe(cur)
                rows = cur.fetchmany(max_rows + 1)
                return {"columns": [{"name": n, "type": t} for n, t in zip(names, types)],
                        "rows": [[self.clean(v) for v in r] for r in rows[:max_rows]],
                        "truncated": len(rows) > max_rows}
            finally:
                cur.close()

    def run(self, statements, max_rows=MAX_ROWS):
        """Run statements in order, stopping at the first error; the error is the last result."""
        self.cancelled = False
        results = []
        for sql in statements:
            start = time.monotonic()
            try:
                if self.cancelled:
                    raise Error("cancelled", "Query cancelled")
                self.running = True
                try:
                    out = self.run_one(sql, max(1, int(max_rows)))
                finally:
                    self.running = False
                # Some statements end normally when interrupted (MySQL SLEEP).
                if self.cancelled:
                    raise Error("cancelled", "Query cancelled")
            except Error as e:
                err = {"code": "cancelled", "message": "Query cancelled"} if self.cancelled \
                    else {"code": e.code, "message": e.message}
                results.append({"sql": sql, "error": err, "ms": round((time.monotonic() - start) * 1000)})
                break
            results.append({"sql": sql, **out, "ms": round((time.monotonic() - start) * 1000)})
        return {"results": results, "database": self.database}

    # structure --------------------------------------------------------------
    def alter_statements(self, schema, table, changes):
        return ddl.statements(self.d, schema, table, self.structure(schema, table), changes or {})

    def alter_preview(self, schema, table, changes):
        return {"statements": [s + ";" for s in self.alter_statements(schema, table, changes)]}

    def run_ddl(self, statements):
        """All or nothing, for engines with transactional DDL."""
        cur = self.db.cursor()
        try:
            with self.guard():
                try:
                    cur.execute("BEGIN")
                    for sql in statements:
                        cur.execute(sql)
                    cur.execute("COMMIT")
                except BaseException:
                    with contextlib.suppress(Exception):
                        cur.execute("ROLLBACK")
                    raise
        except Error as e:
            raise Error(e.code, e.message + "; nothing was changed") from e
        finally:
            cur.close()

    def alter(self, schema, table, changes):
        stmts = self.alter_statements(schema, table, changes)
        if not stmts:
            raise Error("invalid", "There are no structure changes")
        try:
            self.run_ddl(stmts)
        finally:
            self.forget()
        return {"statements": len(stmts)}

    def drop(self, schema, table, kind="table"):
        self.query(f"DROP {'VIEW' if kind == 'view' else 'TABLE'} {self.d.table(schema, table)}")
        self.forget()
        return {"ok": True}

    def create_table_preview(self, schema, table, columns):
        return {"statements": [ddl.create_table(self.d, schema, table, columns)]}

    def create_table(self, schema, table, columns):
        self.query(ddl.create_table(self.d, schema, table, columns))
        self.forget()
        return {"ok": True}

    def create_database(self, name):
        self.query(ddl.database(self.d, "CREATE", name))
        return {"ok": True}

    def drop_database(self, name):
        self.query(ddl.database(self.d, "DROP", name))
        return {"ok": True}

    def truncate(self, schema, table):
        self.query(f"TRUNCATE TABLE {self.d.table(schema, table)}")
        return {"ok": True}

    # transfer ---------------------------------------------------------------
    def stream_cursor(self):
        return self.db.cursor()

    def stream(self, sql, params=None, batch=2000):
        """(names, rows) batches without holding the whole result; at least one batch."""
        with self.guard():
            cur = self.stream_cursor()
            try:
                self.execute(cur, sql, params or ())
                if not cur.description:
                    raise Error("invalid", "This statement returns no rows to export")
                names = [d[0] for d in cur.description]
                sent = False
                while True:
                    rows = cur.fetchmany(batch)
                    if not rows:
                        break
                    sent = True
                    yield names, rows
                if not sent:
                    yield names, []
            finally:
                cur.close()

    def insert_many(self, sql, rows, batch=500):
        """Insert (line, values) pairs in one transaction; all or nothing."""
        cur = self.db.cursor()
        count, first, last = 0, None, None
        try:
            with self.guard():
                try:
                    cur.execute("BEGIN")
                    chunk = []
                    for line, values in rows:
                        if not chunk:
                            first = line
                        chunk.append(values)
                        last = line
                        count += 1
                        if len(chunk) >= batch:
                            cur.executemany(self.d.finish(sql), chunk)
                            chunk = []
                    if chunk:
                        cur.executemany(self.d.finish(sql), chunk)
                    cur.execute("COMMIT")
                except BaseException:
                    with contextlib.suppress(Exception):
                        cur.execute("ROLLBACK")
                    raise
        except Error as e:
            where = "" if e.code == "invalid" or first is None else \
                f" (in lines {first}–{last})" if first != last else f" (line {first})"
            raise Error(e.code, f"{e.message}{where}; nothing was imported") from e
        finally:
            cur.close()
        return count
