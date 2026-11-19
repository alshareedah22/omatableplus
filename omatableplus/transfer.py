"""Exports (CSV, JSON, SQL) and CSV imports.

Exports stream rows from the adapter into a temporary file next to the
target and rename it into place, so a failed export leaves nothing behind.
The file is mode 600: it holds database contents.
"""

import csv
import datetime
import json
import math
import os
import tempfile

from . import sqlgen, sqlsplit
from .protocol import Error, encode

FORMATS = ("csv", "json", "sql")
SQL_BATCH = 100


def target_path(path, overwrite=False):
    path = str(path or "").strip()
    if not path:
        raise Error("invalid", "choose a file to write")
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isdir(path):
        raise Error("invalid", f"{path} is a folder")
    if not os.path.isdir(os.path.dirname(path)):
        raise Error("notfound", f"the folder {os.path.dirname(path)} does not exist")
    if os.path.exists(path) and not overwrite:
        raise Error("exists", f"{path} already exists")
    return path


def text(value):
    """A cell as CSV text: empty for NULL, \\x hex for bytes."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "\\x" + bytes(value).hex()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    return str(value)


class CSVWriter:
    def __init__(self, f, d, name):
        self.w = csv.writer(f)

    def header(self, names):
        self.w.writerow(names)

    def rows(self, names, rows):
        self.w.writerows([text(v) for v in r] for r in rows)

    def close(self):
        pass


def _json_value(v):
    # JSON has no NaN or Infinity
    return str(v) if isinstance(v, float) and not math.isfinite(v) else v


class JSONWriter:
    def __init__(self, f, d, name):
        self.f = f
        self.first = True

    def header(self, names):
        self.f.write("[")

    def rows(self, names, rows):
        for r in rows:
            self.f.write(("\n  " if self.first else ",\n  ") + json.dumps(
                {n: _json_value(v) for n, v in zip(names, r)}, default=encode, ensure_ascii=False))
            self.first = False

    def close(self):
        self.f.write("\n]\n" if not self.first else "]\n")


class SQLWriter:
    def __init__(self, f, d, name):
        self.f, self.d, self.name = f, d, name

    def header(self, names):
        self.f.write(f"-- Exported by OmaTablePlus on {datetime.datetime.now().astimezone():%Y-%m-%d %H:%M %Z}\n")
        self.cols = ", ".join(self.d.quote(n) for n in names)

    def rows(self, names, rows):
        for i in range(0, len(rows), SQL_BATCH):
            values = ",\n  ".join("(" + ", ".join(sqlgen.literal(v, self.d) for v in r) + ")"
                                  for r in rows[i:i + SQL_BATCH])
            self.f.write(f"INSERT INTO {self.d.quote(self.name)} ({self.cols}) VALUES\n  {values};\n")

    def close(self):
        pass


WRITERS = {"csv": CSVWriter, "json": JSONWriter, "sql": SQLWriter}


def export(adapter, target, fmt, path, overwrite=False, table_name=None, max_rows=None, max_bytes=None):
    if fmt not in FORMATS:
        raise Error("invalid", f"unknown format {fmt!r}")
    d = adapter.d
    if target.get("sql") is not None:
        stmts = sqlsplit.split(str(target["sql"]), adapter.conn.get("engine") or adapter.engine)
        if len(stmts) != 1:
            raise Error("invalid", "An export runs exactly one statement")
        sql, params, name = stmts[0][2], None, table_name or "export"
    else:
        schema, table = target.get("schema"), target.get("table")
        cols = [c["name"] for c in adapter.columns(schema, table, refresh=True)]
        sql, params = sqlgen.select_all(d, schema, table, cols, target.get("sort") or [],
                                        target.get("filters") or [], target.get("match") or "and")
        name = table_name or table
    path = target_path(path, overwrite)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".omatableplus-", suffix=".part")
    count = 0
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            writer = WRITERS[fmt](f, d, name)
            started = False
            for names, rows in adapter.stream(sql, params):
                if not started:
                    writer.header(names)
                    started = True
                writer.rows(names, rows)
                count += len(rows)
                if max_rows and count > max_rows:
                    raise Error("limit", f"the export passed {max_rows} rows, so it was stopped and nothing was written; "
                                         "export fewer rows (filters or a LIMIT)")
                if max_bytes:
                    f.flush()
                    if os.fstat(f.fileno()).st_size > max_bytes:
                        raise Error("limit", f"the export passed {max_bytes // (1 << 20)} MB, so it was stopped and "
                                             "nothing was written; export fewer rows (filters or a LIMIT)")
            writer.close()
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return {"path": path, "rows": count, "bytes": os.path.getsize(path)}


def _open_csv(path):
    path = os.path.abspath(os.path.expanduser(str(path or "").strip()))
    if not os.path.isfile(path):
        raise Error("notfound", f"no file at {path}")
    return path, open(path, newline="", encoding="utf-8-sig")


def _sniff(sample):
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def preview_csv(path, delimiter=None, limit=20):
    path, f = _open_csv(path)
    try:
        with f:
            delimiter = delimiter or _sniff(f.read(65536))
            f.seek(0)
            reader = csv.reader(f, delimiter=delimiter)
            header = next(reader, [])
            rows, count = [], 0
            for r in reader:
                count += 1
                if len(rows) < limit:
                    rows.append(r)
    except UnicodeDecodeError:
        raise Error("invalid", f"{path} is not UTF-8 text") from None
    except csv.Error as e:
        raise Error("invalid", f"{path}: {e}") from None
    return {"path": path, "delimiter": delimiter, "header": header, "rows": rows, "count": count}


def import_csv(adapter, schema, table, path, mapping, delimiter=",", header=True, empty_null=True):
    d = adapter.d
    cols = [c["name"] for c in adapter.columns(schema, table, refresh=True)]
    targets = [(i, m) for i, m in enumerate(mapping or []) if m]
    if not targets:
        raise Error("invalid", "Map at least one CSV column to a table column")
    names = [m for _, m in targets]
    for m in names:
        if m not in cols:
            raise Error("invalid", f"unknown column {m!r}")
    if len(set(names)) != len(names):
        raise Error("invalid", "Each table column can take only one CSV column")
    sql = (f"INSERT INTO {d.table(schema, table)} ({', '.join(d.quote(m) for m in names)}) "
           f"VALUES ({', '.join([d.mark] * len(names))})")
    path, f = _open_csv(path)
    width = len(mapping)

    def rows(reader):
        if header:
            next(reader, None)
        for r in reader:
            if not r:
                continue
            if len(r) != width:
                raise Error("invalid", f"line {reader.line_num} has {len(r)} fields, expected {width}")
            yield reader.line_num, [None if empty_null and r[i] == "" else r[i] for i, _ in targets]

    try:
        with f:
            return {"inserted": adapter.insert_many(sql, rows(csv.reader(f, delimiter=delimiter or ",")))}
    except UnicodeDecodeError:
        raise Error("invalid", f"{path} is not UTF-8 text") from None
