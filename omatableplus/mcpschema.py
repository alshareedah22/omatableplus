"""What the MCP tools share: limits, argument schemas, argument checking and shrinking what a model reads."""

import json

from .protocol import Error

READ, WRITE, DESTRUCTIVE = "read", "write", "destructive"     # a tool's tier, see McpServer
SQL_ENGINES = ("postgresql", "mysql", "mariadb", "sqlite")
SERVER_ENGINES = ("postgresql", "mysql", "mariadb")
REDIS_ENGINES = ("redis", "valkey")
MONGO_ENGINES = ("mongodb",)
FAMILIES = {**dict.fromkeys(SQL_ENGINES, "sql"), **dict.fromkeys(REDIS_ENGINES, "redis"),
            **dict.fromkeys(MONGO_ENGINES, "mongodb")}

DEFAULT_ROWS = 50
MAX_ROWS = 200
CELL_LIMIT = 1000
MATCH_LIMIT = 50
HISTORY_LIMIT = 100

CONNECTION = {"type": "string", "description": "Connection id or name, as listed by list_connections."}
DATABASE = {"type": "string", "description": "Database to switch to first. Needed when the connection has no default "
                                             "database (list_databases then shows an empty `database`)."}
SCHEMA = {"type": "string", "description": "Schema name (SQLite: main, PostgreSQL: public, MySQL: the database)."}
TABLE = {"type": "string", "description": "Table name."}
SQL = {"type": "string", "description": "SQL text. Several statements run in order and stop at the first error."}
ROWS = {"type": "integer", "description": f"Rows to return (default {DEFAULT_ROWS}, at most {MAX_ROWS})."}
FILE = {"type": "string", "description": "A bare file name in the MCP files folder (see list_files); no folder, no path."}
FILTERS = {
    "type": "array",
    "description": "Conditions on columns. Ops: = <> < > <= >=, contains, not_contains, starts, ends, in "
                   "(comma separated values), null, not_null.",
    "items": {"type": "object", "properties": {"column": {"type": "string"}, "op": {"type": "string"},
                                               "value": {}}, "required": ["column", "op"]},
}
SORT = {"type": "array", "description": "Ordering, first item first.",
        "items": {"type": "object", "properties": {"column": {"type": "string"}, "desc": {"type": "boolean"}},
                  "required": ["column"]}}
KEYED = {"type": "array", "description": "Each item is {key: {pk column: value, ...}} with the row's full primary "
                                         "key (see describe_object).", "items": {"type": "object"}}
CHANGES = {
    "inserts": {"type": "array", "description": "Rows to insert: [{values: {column: value}}].",
                "items": {"type": "object"}},
    "updates": {"type": "array", "description": "Rows to change: [{key: {pk column: value}, values: "
                                                "{column: value}}].", "items": {"type": "object"}},
    "deletes": {**KEYED, "description": "Rows to delete: [{key: {pk column: value}}], by full primary key."},
}

TYPES = {"string": str, "integer": int, "boolean": bool, "array": list, "object": dict}


def schema_of(properties, required):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def check(arguments, schema):
    """Reject what the schema does not declare: an unknown `confirmed` must never reach the helper.

    What is inside an array or an object is held to the schema too (type, enum, required, items), so that a
    malformed filter or change is an input error the model can read, not an internal one."""
    if not isinstance(arguments, dict):
        raise Error("invalid", "arguments must be an object")
    properties = schema["properties"]
    for name in arguments:
        if name not in properties:
            raise Error("invalid", f"unknown argument {name!r}")
    for name in schema["required"]:
        if name not in arguments:
            raise Error("invalid", f"missing argument {name!r}")
    for name, value in arguments.items():
        check_value(value, properties[name], name)


def check_value(value, schema, where):
    kind = schema.get("type")
    if kind and (not isinstance(value, TYPES[kind]) or (kind == "integer" and isinstance(value, bool))):
        raise Error("invalid", f"{where!r} must be {'an' if kind in ('integer', 'array', 'object') else 'a'} {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise Error("invalid", f"{where!r} must be one of {', '.join(map(str, schema['enum']))}")
    if isinstance(value, dict):
        for name in schema.get("required", ()):
            if name not in value:
                raise Error("invalid", f"{where!r} is missing {name!r}")
        for name, item in value.items():
            if name in schema.get("properties", {}):
                check_value(item, schema["properties"][name], f"{where}.{name}")
    elif isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            check_value(item, schema["items"], f"{where}[{index}]")


def shrink(value):
    """What a model can read: no raw bytes, no huge text."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"‹binary {len(value)} bytes›"
    if isinstance(value, (dict, list)):       # a JSON column: its text is what gets cut
        text = json.dumps(value, ensure_ascii=False, default=str)
        return shrink(text) if len(text) > CELL_LIMIT else value
    if isinstance(value, str) and len(value) > CELL_LIMIT:
        return value[:CELL_LIMIT] + f"…[+{len(value) - CELL_LIMIT} chars]"
    return value


def shrink_rows(rows):
    return [[shrink(cell) for cell in row] for row in rows]


def trim(value):
    """`shrink` for a payload of any shape: long text cut, bytes hidden, long lists cut with a marker."""
    if isinstance(value, dict):
        return {key: trim(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        kept = [trim(item) for item in value[:MAX_ROWS]]
        return kept + [f"…[+{len(value) - MAX_ROWS} items]"] if len(value) > MAX_ROWS else kept
    return shrink(value)


def page_size(limit):
    return max(1, min(limit, MAX_ROWS))
