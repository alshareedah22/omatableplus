"""PostgreSQL through psycopg 3."""

import json

from .. import explain
from ..protocol import Error
from .base import SQLAdapter

ACTIONS = {"a": "NO ACTION", "r": "RESTRICT", "c": "CASCADE", "n": "SET NULL", "d": "SET DEFAULT"}
HIDDEN_SCHEMAS = ("NOT IN ('pg_catalog', 'information_schema') AND {0} NOT LIKE 'pg\\_toast%%' "
                  "AND {0} NOT LIKE 'pg\\_temp\\_%%'")


class Postgres(SQLAdapter):
    engine = "postgresql"
    snapshot = "BEGIN ISOLATION LEVEL REPEATABLE READ"

    def connect(self, database=None):
        import psycopg
        self.psycopg = psycopg
        c = self.conn
        dbname = database or self.database or "postgres"
        with self.guard():
            db = psycopg.connect(host=c.get("host") or "127.0.0.1", port=int(c.get("port") or 5432),
                                 user=c.get("user") or None, password=c.get("password") or None,
                                 dbname=dbname, sslmode=c.get("tls") or "prefer", connect_timeout=10,
                                 application_name="OmaTablePlus", autocommit=True,
                                 # read-only connections are read-only on the server too
                                 options="-c default_transaction_read_only=on" if c.get("readonly") else None)
        self.close()
        self.db = db
        self.database = dbname
        self.forget()

    def translate(self, exc):
        psycopg = self.psycopg
        if not isinstance(exc, psycopg.Error):
            return None
        msg = str(exc).strip()
        state = getattr(exc, "sqlstate", None)
        low = msg.lower()
        if state == "57014":
            return Error("cancelled", "Query cancelled")
        if state in ("28P01", "28000") or "password authentication failed" in low or "no password supplied" in low:
            return Error("auth", msg)
        if state == "3D000" or ("database" in low and "does not exist" in low and isinstance(exc, psycopg.OperationalError)):
            return Error("notfound", msg)
        if isinstance(exc, psycopg.OperationalError) and state is None:
            return Error("network", msg)
        return Error("sql", msg)

    def stream(self, sql, params=None, batch=2000):
        # cursor.stream reads rows one by one instead of the whole result
        with self.guard():
            cur = self.db.cursor()
            try:
                names, rows = None, []
                for row in cur.stream(self.d.finish(sql) if params else sql, params or None):
                    if names is None:
                        names = [d[0] for d in cur.description]
                    rows.append(row)
                    if len(rows) >= batch:
                        yield names, rows
                        rows = []
                if names is None:
                    if not cur.description:
                        raise Error("invalid", "This statement returns no rows to export")
                    names = [d[0] for d in cur.description]
                yield names, rows
            finally:
                cur.close()

    def describe(self, cursor):
        types = self.db.adapters.types
        return [getattr(types.get(d.type_code), "name", "") for d in cursor.description]

    def interrupt(self):
        self.db.cancel_safe(timeout=5)

    def version(self):
        return "PostgreSQL " + self.query("SHOW server_version")[1][0][0]

    def databases(self):
        return [r[0] for r in self.query("SELECT datname FROM pg_database WHERE NOT datistemplate "
                                         "AND datallowconn ORDER BY datname")[1]]

    def use(self, database):
        self.connect(database)

    def drop_database(self, name):
        if name == self.database:
            raise Error("invalid", f"You are connected to {name}; switch to another database to drop it")
        return super().drop_database(name)

    def tree(self):
        self.forget()
        hide = HIDDEN_SCHEMAS.format("n.nspname")
        schemas = {r[0]: {"name": r[0], "tables": [], "views": [], "functions": [], "sequences": []}
                   for r in self.query(f"SELECT n.nspname FROM pg_namespace n WHERE n.nspname {hide} "
                                       "ORDER BY n.nspname = 'public' DESC, n.nspname")[1]}
        rels = self.query("SELECT n.nspname, c.relname, c.relkind FROM pg_class c "
                          "JOIN pg_namespace n ON n.oid = c.relnamespace "
                          f"WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S') AND n.nspname {hide} "
                          "AND NOT c.relispartition ORDER BY c.relname")[1]
        for schema, name, kind in rels:
            group = {"v": "views", "m": "views", "S": "sequences"}.get(kind, "tables")
            if schema in schemas:
                schemas[schema][group].append(name)
        funcs = self.query("SELECT DISTINCT n.nspname, p.proname FROM pg_proc p "
                           "JOIN pg_namespace n ON n.oid = p.pronamespace "
                           f"WHERE n.nspname {hide} AND p.prokind IN ('f', 'p') ORDER BY p.proname")[1]
        for schema, name in funcs:
            if schema in schemas:
                schemas[schema]["functions"].append(name)
        return list(schemas.values())

    def relation_rows(self):
        hide = HIDDEN_SCHEMAS.format("ns.nspname")
        pks = self.query(
            "SELECT ns.nspname, cl.relname, a.attname FROM pg_index i JOIN pg_class cl ON cl.oid = i.indrelid "
            "JOIN pg_namespace ns ON ns.oid = cl.relnamespace "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            f"WHERE i.indisprimary AND ns.nspname {hide}")[1]
        fks = self.query(
            "SELECT con.conname, ns.nspname, cl.relname, "
            "ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(n, i) "
            "JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n ORDER BY k.i), "
            "fns.nspname, fcl.relname, "
            "ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(n, i) "
            "JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n ORDER BY k.i) "
            "FROM pg_constraint con JOIN pg_class cl ON cl.oid = con.conrelid "
            "JOIN pg_namespace ns ON ns.oid = cl.relnamespace JOIN pg_class fcl ON fcl.oid = con.confrelid "
            f"JOIN pg_namespace fns ON fns.oid = fcl.relnamespace WHERE con.contype = 'f' AND ns.nspname {hide} "
            "ORDER BY 2, 3, 1")[1]
        return pks, fks

    def resolve_schema(self, table, quoted):
        rows = self.query("SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                          "WHERE c.oid = to_regclass(%s)", (self.d.quote(table) if quoted else table,))[1]
        return rows[0][0] if rows else None

    def _routines(self, schema, name):
        return self.query(
            "SELECT p.prokind, pg_get_functiondef(p.oid), pg_get_function_identity_arguments(p.oid) "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = %s AND p.proname = %s AND p.prokind IN ('f', 'p') ORDER BY p.oid", (schema, name))[1]

    def definition(self, schema, name, kind):
        t = self.d.table(schema, name)
        if kind == "view":
            rows = self.query("SELECT c.relkind, pg_get_viewdef(c.oid, true) FROM pg_class c "
                              "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relname = %s "
                              "AND c.relkind IN ('v', 'm')", (schema, name))[1]
            if not rows:
                raise Error("notfound", f"no view {name}")
            body = rows[0][1].strip().rstrip(";")
            if rows[0][0] == "m":
                return {"sql": f"DROP MATERIALIZED VIEW {t};\nCREATE MATERIALIZED VIEW {t} AS\n{body};"}
            return {"sql": f"CREATE OR REPLACE VIEW {t} AS\n{body};"}
        if kind == "function":
            rows = self._routines(schema, name)
            if not rows:
                raise Error("notfound", f"no function {name}")
            return {"sql": "\n\n".join(dfn.strip() + ";" for _, dfn, _ in rows),
                    "overloads": [sig for _, _, sig in rows]}
        if kind == "trigger":
            rows = self.query("SELECT pg_get_triggerdef(t.oid, true), c.relname FROM pg_trigger t "
                              "JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
                              "WHERE n.nspname = %s AND t.tgname = %s AND NOT t.tgisinternal", (schema, name))[1]
            if not rows:
                raise Error("notfound", f"no trigger {name}")
            on = self.d.table(schema, rows[0][1])
            return {"sql": f"DROP TRIGGER IF EXISTS {self.d.quote(name)} ON {on};\n{rows[0][0]};"}
        raise Error("invalid", f"unknown object kind {kind!r}")

    def drop_object(self, schema, name, kind, table=None, signature=None):
        q = self.d.quote
        if kind == "function":
            rows = self._routines(schema, name)
            if signature is not None:
                rows = [r for r in rows if r[2] == signature]
            if not rows:
                raise Error("notfound", f"no function {name}({signature or ''})")
            if len(rows) > 1:
                raise Error("invalid", f"{name} has {len(rows)} versions; pick one to drop")
            prokind, _, sig = rows[0]
            self.query(f"DROP {'PROCEDURE' if prokind == 'p' else 'FUNCTION'} {self.d.table(schema, name)}({sig})")
        elif kind == "trigger":
            if not table:
                table = self.query("SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                                   "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND t.tgname = %s",
                                   (schema, name))[1][0][0]
            self.query(f"DROP TRIGGER {q(name)} ON {self.d.table(schema, table)}")
        else:
            raise Error("invalid", f"unknown object kind {kind!r}")
        self.forget()
        return {"ok": True}

    def explain(self, sql, analyze=False):
        opts = "FORMAT JSON" + (", ANALYZE, BUFFERS" if analyze else "")
        cur = self.db.cursor()
        try:
            with self.guard():
                # ANALYZE runs the statement: inside a transaction that is rolled back
                cur.execute("BEGIN")
                try:
                    cur.execute(f"EXPLAIN ({opts}) {sql}")
                    data = cur.fetchone()[0]
                finally:
                    cur.execute("ROLLBACK")
        finally:
            cur.close()
        out = explain.postgres(data)
        out["raw"] = json.dumps(data, indent=2)
        return out

    def processes(self):
        cols, rows = self.query(
            "SELECT pid, usename, datname, COALESCE(host(client_addr), 'local'), application_name, state, "
            "EXTRACT(EPOCH FROM now() - COALESCE(query_start, backend_start))::float, wait_event_type, query "
            "FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND backend_type = 'client backend' "
            "ORDER BY state = 'active' DESC, query_start NULLS LAST")
        return [{"id": pid, "user": user, "database": db, "client": client, "app": app, "state": state or "",
                 "seconds": round(secs or 0, 1), "wait": wait or "", "query": query or ""}
                for pid, user, db, client, app, state, secs, wait, query in rows]

    def kill(self, pid, hard=False):
        fn = "pg_terminate_backend" if hard else "pg_cancel_backend"
        ok = self.query(f"SELECT {fn}(%s)", (int(pid),))[1][0][0]
        if not ok:
            raise Error("notfound", f"process {pid} is gone, or you may not stop it")
        return {"ok": True}

    def users(self):
        rows = self.query(
            "SELECT r.rolname, r.rolcanlogin, r.rolsuper, r.rolcreatedb, r.rolcreaterole, r.rolreplication, "
            "r.rolvaliduntil::text, ARRAY(SELECT b.rolname FROM pg_auth_members m JOIN pg_roles b ON b.oid = m.roleid "
            "WHERE m.member = r.oid ORDER BY 1) FROM pg_roles r WHERE r.rolname !~ '^pg_' ORDER BY 1")[1]
        return [{"name": n, "host": "", "login": login, "superuser": sup, "createdb": cdb, "createrole": crole,
                 "replication": repl, "valid_until": until, "member_of": list(member or [])}
                for n, login, sup, cdb, crole, repl, until, member in rows]

    def user_grants(self, spec):
        name = spec.get("name")
        out = []
        db = self.database
        privs = [p for p in ("CONNECT", "CREATE", "TEMPORARY")
                 if self.query("SELECT has_database_privilege(%s, %s, %s)", (name, db, p))[1][0][0]]
        if privs:
            out.append(f"{', '.join(privs)} ON DATABASE {db}")
        hide = HIDDEN_SCHEMAS.format("nspname")
        for schema, usage, create in self.query(
                "SELECT nspname, has_schema_privilege(%s, oid, 'USAGE'), has_schema_privilege(%s, oid, 'CREATE') "
                f"FROM pg_namespace WHERE nspname {hide} ORDER BY 1", (name, name))[1]:
            privs = [p for p, ok in (("USAGE", usage), ("CREATE", create)) if ok]
            if privs:
                out.append(f"{', '.join(privs)} ON SCHEMA {schema}")
        for schema, table, privs in self.query(
                "SELECT table_schema, table_name, string_agg(privilege_type, ', ' ORDER BY privilege_type) "
                "FROM information_schema.role_table_grants WHERE grantee = %s GROUP BY 1, 2 ORDER BY 1, 2", (name,))[1]:
            out.append(f"{privs} ON {schema}.{table}")
        return out

    def catalog_rows(self):
        hide = HIDDEN_SCHEMAS.format("n.nspname")
        return self.query(
            "SELECT n.nspname, c.relname, CASE WHEN c.relkind IN ('v', 'm') THEN 'view' ELSE 'table' END, "
            "a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            f"WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND n.nspname {hide} AND NOT c.relispartition "
            "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY n.nspname, c.relname, a.attnum")[1]

    def load_columns(self, schema, table):
        rows = self.query(
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull, "
            "pg_get_expr(d.adbin, d.adrelid), COALESCE(a.attnum = ANY(i.indkey), false) "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
            "LEFT JOIN pg_index i ON i.indrelid = c.oid AND i.indisprimary "
            "WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped "
            "ORDER BY a.attnum", (schema, table))[1]
        return [{"name": n, "type": t, "nullable": nullable, "default": dflt, "pk": pk}
                for n, t, nullable, dflt, pk in rows]

    def key(self, schema, table):
        rows = self.query(
            "SELECT array_agg(a.attname::text ORDER BY k.ord) FROM pg_index i "
            "JOIN pg_class c ON c.oid = i.indrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ord) "
            "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum "
            "WHERE n.nspname = %s AND c.relname = %s AND c.relkind IN ('r', 'p') AND i.indisunique "
            "AND i.indpred IS NULL AND i.indexprs IS NULL "
            "GROUP BY i.indexrelid, i.indisprimary HAVING bool_and(a.attnotnull) "
            "ORDER BY i.indisprimary DESC, count(*) LIMIT 1", (schema, table))[1]
        return list(rows[0][0]) if rows else []

    def structure(self, schema, table):
        cols = self.query(
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod), NOT a.attnotnull, "
            "pg_get_expr(d.adbin, d.adrelid), col_description(c.oid, a.attnum), "
            "COALESCE(a.attnum = ANY(i.indkey), false), a.attidentity, a.attgenerated "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
            "LEFT JOIN pg_index i ON i.indrelid = c.oid AND i.indisprimary "
            "WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped "
            "ORDER BY a.attnum", (schema, table))[1]
        if not cols:
            raise Error("notfound", f"table {table!r} not found")
        columns = []
        for name, typ, nullable, dflt, comment, pk, identity, generated in cols:
            extra = "identity" if identity else "generated" if generated else ""
            columns.append({"name": name, "type": typ, "nullable": nullable,
                            "default": None if generated else dflt, "comment": comment or "", "pk": pk, "extra": extra})
        indexes = [{"name": n, "unique": u, "primary": p, "columns": list(c)} for n, u, p, c in self.query(
            "SELECT ic.relname, i.indisunique, i.indisprimary, "
            "ARRAY(SELECT pg_get_indexdef(i.indexrelid, k, true) FROM generate_series(1, i.indnkeyatts) k ORDER BY k) "
            "FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid JOIN pg_class c ON c.oid = i.indrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relname = %s "
            "ORDER BY i.indisprimary DESC, ic.relname", (schema, table))[1]]
        keyed = "ARRAY(SELECT a.attname::text FROM unnest(con.{0}) WITH ORDINALITY k(n, o) " \
                "JOIN pg_attribute a ON a.attrelid = con.{1} AND a.attnum = k.n ORDER BY k.o)"
        fks = [{"name": n, "columns": list(c), "ref_schema": rs, "ref_table": rt, "ref_columns": list(rc),
                "on_update": ACTIONS.get(u, u), "on_delete": ACTIONS.get(dl, dl)}
               for n, c, rs, rt, rc, u, dl in self.query(
                   f"SELECT con.conname, {keyed.format('conkey', 'conrelid')}, rn.nspname, rc.relname, "
                   f"{keyed.format('confkey', 'confrelid')}, con.confupdtype, con.confdeltype "
                   "FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid "
                   "JOIN pg_namespace n ON n.oid = c.relnamespace JOIN pg_class rc ON rc.oid = con.confrelid "
                   "JOIN pg_namespace rn ON rn.oid = rc.relnamespace "
                   "WHERE con.contype = 'f' AND n.nspname = %s AND c.relname = %s ORDER BY con.conname",
                   (schema, table))[1]]
        triggers = [{"name": n, "definition": dfn + ";"} for n, dfn in self.query(
            "SELECT t.tgname, pg_get_triggerdef(t.oid, true) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relname = %s "
            "AND NOT t.tgisinternal ORDER BY 1", (schema, table))[1]]
        return {"columns": columns, "indexes": indexes, "foreign_keys": fks, "triggers": triggers,
                "ddl": self._ddl(schema, table, columns)}

    def _ddl(self, schema, table, columns):
        q, t = self.d.quote, self.d.table(schema, table)
        lines = []
        for c in columns:
            line = f"  {q(c['name'])} {c['type']}"
            if c["extra"] == "identity":
                line += " GENERATED BY DEFAULT AS IDENTITY"
            if not c["nullable"]:
                line += " NOT NULL"
            if c["default"] is not None:
                line += " DEFAULT " + c["default"]
            lines.append(line)
        cons = self.query(
            "SELECT con.conname, pg_get_constraintdef(con.oid, true) FROM pg_constraint con "
            "JOIN pg_class c ON c.oid = con.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relname = %s "
            "ORDER BY position(con.contype::text in 'pufcx'), con.conname",
            (schema, table))[1]
        lines += [f"  CONSTRAINT {q(n)} {definition}" for n, definition in cons]
        out = [f"CREATE TABLE {t} (\n" + ",\n".join(lines) + "\n);"]
        out += [r[0] + ";" for r in self.query(
            "SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relname = %s "
            "AND NOT EXISTS (SELECT 1 FROM pg_constraint con WHERE con.conindid = i.indexrelid) "
            "ORDER BY 1", (schema, table))[1]]
        out += [f"COMMENT ON COLUMN {t}.{q(c['name'])} IS {self._literal(c['comment'])};"
                for c in columns if c["comment"]]
        return "\n".join(out)

    def _literal(self, value):
        from .. import sqlgen
        return sqlgen.literal(value, self.d)
