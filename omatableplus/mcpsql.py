"""What raw SQL may do when a model sends it: first-keyword checks, not a parser.

The database user's rights stay the real boundary. These checks only close what a model could do with the MCP
server's own rights: reach files outside the MCP files folder (see mcpfiles), or run a query that is not a read
where only a read is meant.
"""

import re
from collections import namedtuple

from . import safety, sqlsplit
from .protocol import Error

FILE_STATEMENTS = {"ATTACH", "COPY", "LOAD"}      # SQLite ATTACH opens or creates a file; COPY, LOAD DATA read or write one
FILE_CLAUSES = {"OUTFILE", "DUMPFILE"}            # SELECT ... INTO OUTFILE
# Called from a plain SELECT, these read (or write) a file of the machine the database runs on
FILE_FUNCTIONS = {"LOAD_FILE", "PG_READ_FILE", "PG_READ_BINARY_FILE", "PG_LS_DIR", "PG_STAT_FILE", "LO_IMPORT",
                  "LO_EXPORT"}
QUOTES = '"`['
READS = {"SELECT", "WITH", "VALUES", "TABLE"}
DESTRUCTIVE = {"DROP", "TRUNCATE", "GRANT", "REVOKE"}
OPAQUE = {"PREPARE", "EXECUTE", "DO"}              # run text or code this check cannot read: PREPARE ... FROM 'DROP ...'
RUNS_COMMENT = re.compile(r"/\*M?!")
# PostgreSQL: called from a SELECT, these change session settings or run SQL text, so they can turn the
# read-only default of the session off (checked on a real server); dblink runs SQL on another connection
LIFT_READ_ONLY = {"SET_CONFIG", "QUERY_TO_XML", "QUERY_TO_XML_AND_XMLSCHEMA", "QUERY_TO_XMLSCHEMA", "CURSOR_TO_XML",
                  "TS_STAT"}


Statement = namedtuple("Statement", "first words called into")


def tokens(sql, engine):
    """(word, what follows it) for each word of the SQL, upper-cased; strings and comments are skipped.

    A quoted name is a word too (without its quotes): sqlsplit.words skips it as it skips a string, but a guard
    cannot, because "set_config"(...) is a call. What follows is the next character that is neither blank nor
    inside a comment, or "" at the end: set_config /* x */ (...) is a call as well.
    """
    out, i, n = [], 0, len(sql)
    while i < n:
        skip = sqlsplit._comment(sql, i, engine) or sqlsplit._quoted(sql, i, engine)
        if skip:
            if sql[i] in QUOTES:
                out.append((unquoted(sql[i:i + skip]), follower(sql, i + skip, engine)))
            i += skip
            continue
        m = sqlsplit.WORD.match(sql, i)
        if m and (i == 0 or not sqlsplit._ident(sql[i - 1])):
            out.append((m.group(0).upper(), follower(sql, m.end(), engine)))
            i = m.end()
        else:
            i += 1
    return out


def follower(sql, i, engine):
    while i < len(sql):
        skip = sqlsplit._comment(sql, i, engine)
        if skip:
            i += skip
        elif sql[i].isspace():
            i += 1
        else:
            return sql[i]
    return ""


def unquoted(name):
    """A quoted name without its quotes, in upper case (an unclosed one loses only its opening quote)."""
    close = "]" if name[0] == "[" else name[0]
    return (name[1:-1] if len(name) > 1 and name[-1] == close else name[1:]).strip().upper()


def statements(sql, engine):
    """The first word, the words, the words that are called (followed by a parenthesis) and the words that follow
    INTO, for each statement."""
    out = []
    for _, _, statement in sqlsplit.split(sql, engine):
        found = tokens(statement, engine)
        words = [word for word, _ in found]
        out.append(Statement(
            words[0] if words else "", words,
            {word for word, after in found if after == "("},
            {word for before, word in zip(words, words[1:]) if before == "INTO"}))
    return out


def refuse_hidden_code(sql, engine):
    """MySQL and MariaDB run the code of a /*! ... */ comment, which the word check skips like any comment."""
    if engine in ("mysql", "mariadb") and RUNS_COMMENT.search(sql):
        raise Error("invalid", "MySQL runs the code inside /*! ... */ comments and MCP cannot check it; "
                              "write the statement without one")


def outside_the_folder(what):
    return Error("files", f"{what} would read or create a file outside the MCP files folder; "
                          "use export_data, backup_database or import_csv, which stay inside it")


def refuse_file_effects(sql, engine):
    refuse_hidden_code(sql, engine)
    for first, words, called, into in statements(sql, engine):
        if first in FILE_STATEMENTS or (first == "VACUUM" and "INTO" in words) or FILE_CLAUSES.intersection(into):
            raise outside_the_folder(first)
        for word in called:
            if word in FILE_FUNCTIONS:
                raise outside_the_folder(word)


def refuse_session_escapes(sql, engine):
    """The helper's gate reads these statements as reads, so only the read-only session stood in the way."""
    if engine != "postgresql":
        return
    for statement in statements(sql, engine):
        for word in statement.called:
            if word in LIFT_READ_ONLY or word.startswith("DBLINK"):
                raise Error("invalid", f"{word.lower()} can change the session settings or run SQL text, which would "
                                       "lift the read-only session; MCP refuses it")


def refuse_unless_read(sql, engine):
    """One statement that reads, for export_data: its session is read-only only as far as the driver goes."""
    refuse_hidden_code(sql, engine)
    refuse_session_escapes(sql, engine)
    found = sqlsplit.split(sql, engine)
    if len(found) != 1:
        raise Error("invalid", "give exactly one query to export")
    first = statements(found[0][2], engine)[0].first
    if first not in READS or safety.sql_writes([found[0][2]], engine):
        raise Error("invalid", "export_data only runs a query that reads (SELECT, WITH or VALUES)")
    refuse_file_effects(sql, engine)


def sets_writable_schema(words):
    """The pragma, with or without a schema before its name; EXPLAIN PRAGMA runs it too. Not a column of that name."""
    return any(word == "WRITABLE_SCHEMA" and "PRAGMA" in words[max(0, i - 2):i] for i, word in enumerate(words))


def refuse_destructive(sql, engine):
    """Without --destructive: no drop, truncate or change of rights, and nothing that hides one."""
    refuse_hidden_code(sql, engine)
    for first, words, _, _ in statements(sql, engine):
        if first in DESTRUCTIVE or (first == "ALTER" and "DROP" in words):
            raise Error("destructive", f"{first} is refused without --destructive")
        # SQLite: with writable_schema on, a plain DELETE on sqlite_master drops tables
        if first in OPAQUE or sets_writable_schema(words):
            what = "WRITABLE_SCHEMA" if sets_writable_schema(words) else first
            raise Error("destructive", f"{what} can change or run what this server cannot check; "
                                       "it is refused without --destructive")
