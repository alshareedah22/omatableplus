import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omatableplus.mcpsql import refuse_unless_read
from omatableplus.protocol import Error


class RefuseUnlessReadTest(unittest.TestCase):
    def code(self, sql, engine="sqlite"):
        with self.assertRaises(Error) as caught:
            refuse_unless_read(sql, engine)
        return caught.exception.code

    def test_a_second_statement_is_refused_even_when_the_first_one_reads(self):
        # a server driver may run both: PostgreSQL's simple query protocol does
        for sql in ("SELECT 1; SELECT 2", "SELECT 1; DELETE FROM notes", "SELECT 1; VACUUM INTO '/tmp/x'", ""):
            self.assertEqual(self.code(sql), "invalid", sql)

    def test_one_statement_that_reads_passes(self):
        for sql in ("SELECT 1", "  -- all\nWITH x AS (SELECT 1 AS n) SELECT n FROM x;", "VALUES (1), (2)"):
            refuse_unless_read(sql, "sqlite")

    def test_a_statement_that_writes_is_refused(self):
        for sql in ("DELETE FROM notes", "WITH d AS (DELETE FROM notes RETURNING id) SELECT * FROM d",
                    "SELECT * INTO copy FROM notes", "PRAGMA user_version(9)"):
            self.assertEqual(self.code(sql, "postgresql"), "invalid", sql)


if __name__ == "__main__":
    unittest.main()
