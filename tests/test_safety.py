import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omatableplus import safety


class SQLTest(unittest.TestCase):
    def writes(self, sql, engine="postgresql"):
        return bool(safety.sql_writes([sql], engine))

    def test_reads(self):
        for sql in ("SELECT * FROM t", "  -- c\nselect 1", "(SELECT 1) UNION (SELECT 2)", "SHOW TABLES",
                    "EXPLAIN SELECT * FROM t", "WITH x AS (SELECT 1) SELECT * FROM x", "DESCRIBE t",
                    "SELECT 'delete from t' AS note", 'SELECT "update" FROM t', "PRAGMA table_info(t)",
                    "VALUES (1)", "USE shop", "SELECT * FROM t WHERE note = 'INTO'",
                    "SELECT replace(name, 'a', 'b') FROM t", "SELECT * FROM t FOR UPDATE",
                    "SELECT * FROM t FOR NO KEY UPDATE", "SELECT * FROM t LOCK IN SHARE MODE"):
            self.assertFalse(self.writes(sql, "mysql" if sql.startswith("USE") else "postgresql"), sql)

    def test_writes(self):
        for sql in ("INSERT INTO t VALUES (1)", "update t set a=1", "DELETE FROM t", "DROP TABLE t",
                    "ALTER TABLE t ADD c int", "TRUNCATE t", "CREATE TABLE x (a int)", "GRANT ALL ON t TO u",
                    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", "EXPLAIN ANALYZE DELETE FROM t",
                    "SELECT * INTO backup FROM t", "SET GLOBAL max_connections = 10", "PRAGMA journal_mode = WAL",
                    "CALL refresh()", "VACUUM", "BEGIN", "COPY t FROM '/tmp/x'", "unknown thing"):
            self.assertTrue(self.writes(sql), sql)
        self.assertTrue(self.writes("SELECT * FROM t INTO OUTFILE '/tmp/x'", "mysql"))
        self.assertFalse(self.writes("EXPLAIN DELETE FROM t"))  # plans without running

    def test_a_pragma_called_like_a_function_writes_unless_it_only_looks_something_up(self):
        for sql in ("PRAGMA table_info(t)", "PRAGMA main.index_list(t)", "pragma foreign_key_list ( 't' )",
                    "PRAGMA table_xinfo('t')", "PRAGMA integrity_check(10)", "PRAGMA user_version"):
            self.assertFalse(self.writes(sql, "sqlite"), sql)
        for sql in ("PRAGMA user_version(9)", "PRAGMA main.user_version(9)", "PRAGMA journal_mode(WAL)",
                    "PRAGMA user_version(table_info)", "/* c */ PRAGMA user_version(9)", "PRAGMA writable_schema(1)"):
            self.assertTrue(self.writes(sql, "sqlite"), sql)

    def test_only_writes_returned(self):
        self.assertEqual(safety.sql_writes(["SELECT 1", "DELETE FROM t", "SHOW x"], "mysql"), ["DELETE FROM t"])


class MongoTest(unittest.TestCase):
    def test_commands(self):
        for cmd in ({"ping": 1}, {"dbStats": 1}, {"count": "c"}, {"find": "c", "filter": {}},
                    {"aggregate": "c", "pipeline": [{"$match": {}}], "cursor": {}}, {"listCollections": 1}):
            self.assertFalse(safety.mongo_writes(cmd), cmd)
        for cmd in ({"drop": "c"}, {"dropDatabase": 1}, {"insert": "c", "documents": []}, {"shutdown": 1},
                    {"aggregate": "c", "pipeline": [{"$out": "x"}]}, {"createIndexes": "c"}, {}, {"renameCollection": "a"}):
            self.assertTrue(safety.mongo_writes(cmd), cmd)
        self.assertTrue(safety.pipeline_writes([{"$match": {}}, {"$merge": {"into": "x"}}]))
        self.assertFalse(safety.pipeline_writes([{"$match": {}}]))


if __name__ == "__main__":
    unittest.main()
