"""MCP tools beyond browsing SQL: schema changes, files, server administration, saved queries, connections.

A mixin of McpServer. Every change still goes through the helper methods, so read-only connections and the
production tag refuse exactly what they refuse in the window; MCP never confirms. Tiers:
  write        changes that add or alter (--write)
  destructive  changes that remove (--destructive)
Files are only ever named inside the MCP files folder, see mcpfiles.
"""

from .mcpschema import (CONNECTION, DATABASE, DESTRUCTIVE, FILE, FILTERS, READ, SCHEMA, SERVER_ENGINES, SORT, SQL,
                        TABLE, WRITE, shrink, trim)
from .mcpsql import refuse_unless_read
from .protocol import Error

SPEC = {"type": "object", "description": "Fields of the change. Passwords are not accepted here: set them in the "
                                         "OmaTablePlus window."}
COLUMNS = {"type": "array", "description": "[{name, type, nullable (default true), default, pk}]; the columns marked "
                                           "pk form the primary key.", "items": {"type": "object"}}
ALTER = {"type": "object", "description": "{columns: [{op: add|alter|drop, name, type, nullable, default, new_name}], "
                                          "indexes: [{op: add|drop, name, columns, unique}], foreign_keys: [{op: "
                                          "add|drop, name, ...}]}. Dropping needs --destructive."}
USER_OP = {"type": "string", "description": "create, alter, drop, grant or revoke."}
PROCESS = {"type": "integer", "description": "Process id, as listed by list_processes."}

TOOLS = (
    ("list_relations", "Foreign keys between tables, for a schema or the whole database.",
     {"connection": CONNECTION, "database": DATABASE, "schema": SCHEMA}, ("connection",), READ),
    ("list_saved_queries", "The queries saved in OmaTablePlus (name and SQL).", {}, (), READ),
    ("list_processes", "Running queries and sessions of a PostgreSQL or MySQL/MariaDB server.",
     {"connection": CONNECTION}, ("connection",), READ),
    ("list_users", "Users (roles) of a PostgreSQL or MySQL/MariaDB server, and the privileges that exist.",
     {"connection": CONNECTION}, ("connection",), READ),
    ("get_user_grants", "What a user may do.",
     {"connection": CONNECTION, "name": {"type": "string", "description": "User name."},
      "host": {"type": "string", "description": "MySQL host part, default %."}}, ("connection", "name"), READ),
    ("preview_user_change", "The statements a user change would run, without running them.",
     {"connection": CONNECTION, "op": USER_OP, "spec": SPEC}, ("connection", "op", "spec"), READ),
    ("preview_create_table", "The CREATE TABLE statement for these columns, without running it.",
     {"connection": CONNECTION, "database": DATABASE, "schema": SCHEMA, "table": TABLE, "columns": COLUMNS},
     ("connection", "schema", "table", "columns"), READ),
    ("preview_alter_table", "The statements a structure change would run, without running them.",
     {"connection": CONNECTION, "database": DATABASE, "schema": SCHEMA, "table": TABLE, "changes": ALTER},
     ("connection", "schema", "table", "changes"), READ),
    ("preview_import", "The header and first rows of a CSV file of the MCP files folder.",
     {"file": FILE, "delimiter": {"type": "string", "description": "Default: guessed."}}, ("file",), READ),
    ("list_files", "The files of the MCP files folder, the only place import, export, backup and restore use.",
     {}, (), READ),

    ("create_table", "Create a table.",
     {"connection": CONNECTION, "database": DATABASE, "schema": SCHEMA, "table": TABLE, "columns": COLUMNS},
     ("connection", "schema", "table", "columns"), WRITE),
    ("alter_table", "Change a table's structure, all or nothing where the engine allows it. Dropping a column, an "
     "index or a foreign key needs --destructive.",
     {"connection": CONNECTION, "database": DATABASE, "schema": SCHEMA, "table": TABLE, "changes": ALTER},
     ("connection", "schema", "table", "changes"), WRITE),
    ("save_query", "Save a query in OmaTablePlus under a new name; an existing one cannot be replaced.",
     {"name": {"type": "string", "description": "Name of the saved query."}, "sql": SQL}, ("name", "sql"), WRITE),
    ("import_csv", "Insert the rows of a CSV file of the MCP files folder into a table; see preview_import.",
     {"connection": CONNECTION, "database": DATABASE, "schema": SCHEMA, "table": TABLE, "file": FILE,
      "mapping": {"type": "array", "description": "One entry per CSV column: the table column that takes it, or "
                                                  "an empty string to skip it.", "items": {"type": "string"}},
      "delimiter": {"type": "string", "description": "Default ,"},
      "header": {"type": "boolean", "description": "The first line is a header to skip (default true)."},
      "empty_null": {"type": "boolean", "description": "Empty cells become NULL (default true)."}},
     ("connection", "schema", "table", "file", "mapping"), WRITE),
    ("export_data", "Write a table, or the result of one query, to a new file of the MCP files folder. The query "
     "must be one SELECT, WITH or VALUES; never overwrites.",
     {"connection": CONNECTION, "database": DATABASE, "file": FILE,
      "format": {"type": "string", "description": "csv, json or sql."},
      "sql": {"type": "string", "description": "One query to export. Or give schema and table."},
      "schema": SCHEMA, "table": TABLE, "filters": FILTERS, "sort": SORT,
      "match": {"type": "string", "description": "and (default) or or: how filters combine."},
      "table_name": {"type": "string", "description": "Table name written in a sql export."}},
     ("connection", "file", "format"), WRITE),
    ("backup_database", "Dump a database to a new file of the MCP files folder; never overwrites.",
     {"connection": CONNECTION, "database": DATABASE, "file": FILE}, ("connection", "file"), WRITE),
    ("create_database", "Create a database (PostgreSQL, MySQL/MariaDB).",
     {"connection": CONNECTION, "name": {"type": "string", "description": "New database name."}},
     ("connection", "name"), WRITE),
    ("create_connection", "Save a connection to a PostgreSQL or MySQL/MariaDB server. It is created read-only and "
     "tagged production, without a password: a person completes and relaxes it in the window.",
     {"name": {"type": "string"}, "engine": {"type": "string", "description": "postgresql, mysql or mariadb."},
      "host": {"type": "string"}, "port": {"type": "integer"}, "user": {"type": "string"},
      "database": {"type": "string"}, "folder": {"type": "string"},
      "tls": {"type": "string", "description": "disable, prefer (default) or require."}},
     ("name", "engine", "host"), WRITE),
    ("duplicate_connection", "Copy a saved connection with its tag and read-only switch.",
     {"connection": CONNECTION}, ("connection",), WRITE),

    ("drop_object", "Drop a table, view, function or trigger.",
     {"connection": CONNECTION, "database": DATABASE, "schema": SCHEMA,
      "name": {"type": "string", "description": "Object name."},
      "kind": {"type": "string", "description": "table, view, function or trigger."},
      "table": {"type": "string", "description": "Table of a trigger, when the name is not enough."},
      "signature": {"type": "string", "description": "Argument list of a PostgreSQL function with several versions."}},
     ("connection", "schema", "name", "kind"), DESTRUCTIVE),
    ("truncate_table", "Delete every row of a table.",
     {"connection": CONNECTION, "database": DATABASE, "schema": SCHEMA, "table": TABLE},
     ("connection", "schema", "table"), DESTRUCTIVE),
    ("drop_database", "Drop a database and everything in it.",
     {"connection": CONNECTION, "name": {"type": "string", "description": "Database to drop."}},
     ("connection", "name"), DESTRUCTIVE),
    ("restore_database", "Load a file that backup_database wrote in the MCP files folder, and nobody changed since, "
     "into the database. Any other file is refused: restore it in the OmaTablePlus window.",
     {"connection": CONNECTION, "database": DATABASE, "file": FILE}, ("connection", "file"), DESTRUCTIVE),
    ("kill_process", "Stop the query of a process, or with hard=true close its connection.",
     {"connection": CONNECTION, "process_id": PROCESS,
      "hard": {"type": "boolean", "description": "Close the connection, not only stop the query."}},
     ("connection", "process_id"), DESTRUCTIVE),
    ("change_user", "Create, alter, drop, grant or revoke a user. See preview_user_change first.",
     {"connection": CONNECTION, "op": USER_OP, "spec": SPEC}, ("connection", "op", "spec"), DESTRUCTIVE),
    ("delete_saved_query", "Delete a saved query.",
     {"query_id": {"type": "string", "description": "Id, as listed by list_saved_queries."}}, ("query_id",),
     DESTRUCTIVE),
    ("delete_connection", "Delete a saved connection and its stored secrets. Read-only and production connections "
     "are refused.", {"connection": CONNECTION},
     ("connection",), DESTRUCTIVE),
)

PUBLIC_CONNECTION = ("id", "name", "engine", "tag", "readonly", "host", "port", "database", "folder")


class AdminTools:
    """Needs from the server: methods, helper, files, destructive, session(), saved_connection()."""

    # read ---------------------------------------------------------------------
    def tool_list_relations(self, connection, database=None, schema=None):
        return trim(self.methods["schema.relations"](session=self.session(connection, database), schema=schema))

    def tool_list_saved_queries(self):
        return {"items": [{key: shrink(value) for key, value in item.items()}
                          for item in self.methods["query.saved"]()["items"]]}

    def tool_list_processes(self, connection):
        return trim(self.methods["server.processes"](session=self.session(connection)))

    def tool_list_users(self, connection):
        return trim(self.methods["users.list"](session=self.session(connection)))

    def tool_get_user_grants(self, connection, name, host=""):
        return trim(self.methods["users.grants"](session=self.session(connection), name=name, host=host))

    def tool_preview_user_change(self, connection, op, spec):
        return self.methods["users.change"](session=self.session(connection), op=op, spec=self.without_secret(spec),
                                            preview=True)

    def tool_preview_create_table(self, connection, schema, table, columns, database=None):
        return self.methods["table.create_preview"](session=self.session(connection, database), schema=schema,
                                                    table=table, columns=columns)

    def tool_preview_alter_table(self, connection, schema, table, changes, database=None):
        return self.methods["struct.preview"](session=self.session(connection, database), schema=schema, table=table,
                                              changes=changes)

    def tool_preview_import(self, file, delimiter=None):
        out = self.methods["import.preview"](path=self.files.existing(file), delimiter=delimiter)
        return trim({**{key: value for key, value in out.items() if key != "path"}, "file": file})

    def tool_list_files(self):
        return {"files": self.files.listing()}

    # write --------------------------------------------------------------------
    def tool_create_table(self, connection, schema, table, columns, database=None):
        return self.methods["table.create"](session=self.session(connection, database), schema=schema, table=table,
                                            columns=columns)

    def tool_alter_table(self, connection, schema, table, changes, database=None):
        self.refuse_drops(changes)
        return self.methods["struct.apply"](session=self.session(connection, database), schema=schema, table=table,
                                            changes=changes)

    def tool_save_query(self, name, sql):
        return {key: shrink(value) for key, value in self.methods["query.save"](name=name, sql=sql).items()}

    def tool_import_csv(self, connection, schema, table, file, mapping, database=None, delimiter=",", header=True,
                        empty_null=True):
        path = self.files.existing(file)
        return self.methods["import.run"](session=self.session(connection, database), schema=schema, table=table,
                                          path=path, mapping=mapping, delimiter=delimiter, header=header,
                                          empty_null=empty_null)

    def tool_export_data(self, connection, file, format, database=None, sql=None, schema=None, table=None,  # noqa: A002
                         filters=None, sort=None, match="and", table_name=None):
        target = self.export_target(sql, schema, table, filters, sort, match, self.connection(connection)["engine"])
        path = self.files.new(file)
        out = self.methods["export.run"](session=self.session(connection, database, readonly=True), target=target,
                                         format=format, path=path, overwrite=False, table_name=table_name)
        return {"file": file, "rows": out["rows"], "bytes": out["bytes"]}

    @staticmethod
    def export_target(sql, schema, table, filters, sort, match, engine):
        if sql is not None and (schema or table):
            raise Error("invalid", "give a query or a table to export, not both")
        if sql is not None:
            refuse_unless_read(sql, engine)
            return {"sql": sql}
        if not (schema and table):
            raise Error("invalid", "give a query (sql) or a table (schema and table) to export")
        return {"schema": schema, "table": table, "filters": filters or [], "sort": sort or [], "match": match}

    def tool_backup_database(self, connection, file, database=None):
        path = self.files.new(file)
        out = self.methods["db.backup"](session=self.session(connection, database, readonly=True), path=path,
                                        overwrite=False)
        self.files.vouch(file)
        return {"file": file, "bytes": out["bytes"], "ms": out["ms"], "database": out["database"]}

    def tool_create_database(self, connection, name):
        return self.methods["db.create"](session=self.session(connection), name=name)

    def tool_create_connection(self, name, engine, host, port=None, user="", database="", folder="", tls="prefer"):
        if engine not in SERVER_ENGINES:
            raise Error("invalid", f"engine must be one of {', '.join(SERVER_ENGINES)}")
        saved = self.helper.sessions.store.save({
            "name": name, "engine": engine, "host": host, "port": port, "user": user, "database": database,
            "folder": folder, "tls": tls, "tag": "production", "readonly": True})
        return {key: saved[key] for key in PUBLIC_CONNECTION if key in saved}

    def tool_duplicate_connection(self, connection):
        copy = self.helper.sessions.store.duplicate(self.saved_connection(connection)["id"])
        return {key: copy[key] for key in PUBLIC_CONNECTION if key in copy}

    # destructive --------------------------------------------------------------
    def tool_drop_object(self, connection, schema, name, kind, database=None, table=None, signature=None):
        sid = self.session(connection, database)
        if kind in ("table", "view"):
            return self.methods["table.drop"](session=sid, schema=schema, table=name, kind=kind)
        return self.methods["schema.drop_object"](session=sid, schema=schema, name=name, kind=kind, table=table,
                                                  signature=signature)

    def tool_truncate_table(self, connection, schema, table, database=None):
        return self.methods["table.truncate"](session=self.session(connection, database), schema=schema, table=table)

    def tool_drop_database(self, connection, name):
        return self.methods["db.drop"](session=self.session(connection), name=name)

    def tool_restore_database(self, connection, file, database=None):
        return self.methods["db.restore"](session=self.session(connection, database), path=self.files.vouched(file))

    def tool_kill_process(self, connection, process_id, hard=False):
        return self.methods["server.kill"](session=self.session(connection), id=process_id, hard=hard)

    def tool_change_user(self, connection, op, spec):
        return self.methods["users.change"](session=self.session(connection), op=op, spec=self.without_secret(spec))

    def tool_delete_saved_query(self, query_id):
        return self.methods["query.delete_saved"](id=query_id)

    def tool_delete_connection(self, connection):
        conn = self.saved_connection(connection)
        self.refuse_protected(conn)
        self.drop_sessions_of(conn["id"])
        self.helper.sessions.store.delete(conn["id"])
        return {"ok": True}

    # guards -------------------------------------------------------------------
    @staticmethod
    def refuse_protected(conn):
        """The helper has no method that gates a connection's own removal, so this does what ask() does."""
        name = conn.get("name") or "This connection"
        if conn.get("readonly"):
            raise Error("readonly", f"{name} is read-only, so MCP does not delete it. Delete it in the OmaTablePlus window.")
        if conn.get("tag") == "production":
            raise Error("confirm", f"{name} is tagged production and MCP cannot confirm its deletion.")

    @staticmethod
    def without_secret(spec):
        """A password given to a model would sit in its context and in the history of the conversation."""
        if any("pass" in str(key).lower() for key in spec):
            raise Error("invalid", "passwords cannot be given over MCP; set them in the OmaTablePlus window")
        return spec

    def refuse_drops(self, changes):
        if self.destructive:
            return
        for key in ("columns", "indexes", "foreign_keys"):
            for item in changes.get(key) or ():
                if isinstance(item, dict) and item.get("op") == "drop":
                    raise Error("destructive", "dropping a column, an index or a foreign key needs --destructive")
