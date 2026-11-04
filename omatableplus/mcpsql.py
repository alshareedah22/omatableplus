"""What raw SQL may do when a model sends it: first-keyword checks, not a parser.

The database user's rights stay the real boundary. These checks only close what a model could do with the MCP
server's own rights: reach files outside the MCP files folder (see mcpfiles), or run a query that is not a read
where only a read is meant.
"""

import re

from . import safety, sqlsplit
from .protocol import Error

FILE_STATEMENTS = {"ATTACH", "COPY", "LOAD"}      # SQLite ATTACH opens or creates a file; COPY, LOAD DATA read or write one
FILE_CLAUSES = {"OUTFILE", "DUMPFILE"}            # SELECT ... INTO OUTFILE
READS = {"SELECT", "WITH", "VALUES", "TABLE"}
RUNS_COMMENT = re.compile(r"/\*M?!")


def statements(sql, engine):
    """(first word, words) of each statement."""
    out = []
    for _, _, statement in sqlsplit.split(sql, engine):
        words = sqlsplit.words(statement, engine)
        out.append((words[0] if words else "", words))
    return out


def refuse_hidden_code(sql, engine):
    """MySQL and MariaDB run the code of a /*! ... */ comment, which the word check skips like any comment."""
    if engine in ("mysql", "mariadb") and RUNS_COMMENT.search(sql):
        raise Error("invalid", "MySQL runs the code inside /*! ... */ comments and MCP cannot check it; "
                              "write the statement without one")


def refuse_file_effects(sql, engine):
    refuse_hidden_code(sql, engine)
    for first, words in statements(sql, engine):
        if first in FILE_STATEMENTS or (first == "VACUUM" and "INTO" in words) or FILE_CLAUSES.intersection(words):
            raise Error("files", f"{first} would read or create a file outside the MCP files folder; "
                                 "use export_data, backup_database or import_csv, which stay inside it")


def refuse_unless_read(sql, engine):
    """One statement that reads, for export_data: its session is read-only only as far as the driver goes."""
    refuse_hidden_code(sql, engine)
    found = sqlsplit.split(sql, engine)
    if len(found) != 1:
        raise Error("invalid", "give exactly one query to export")
    first, _ = statements(found[0][2], engine)[0]
    if first not in READS or safety.sql_writes([found[0][2]], engine):
        raise Error("invalid", "export_data only runs a query that reads (SELECT, WITH or VALUES)")
