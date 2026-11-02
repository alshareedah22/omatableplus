import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omatableplus.mcpsql import (refuse_destructive, refuse_file_effects, refuse_session_escapes,
                                 refuse_unless_read)
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


class RefuseFileEffectsTest(unittest.TestCase):
    def code(self, sql, engine):
        with self.assertRaises(Error) as caught:
            refuse_file_effects(sql, engine)
        return caught.exception.code

    def test_statements_that_open_read_or_create_a_file_are_refused_in_any_position(self):
        for sql in ("VACUUM INTO '/tmp/v.db'", "vacuum main into '/tmp/m.db'", "SELECT 1; VACUUM INTO '/tmp/s.db'",
                    "ATTACH DATABASE '/tmp/a.db' AS other", "/* x */ ATTACH '/tmp/a.db' AS a",
                    "COPY notes TO '/tmp/x'", "COPY notes FROM PROGRAM 'id'",
                    "LOAD DATA INFILE '/etc/passwd' INTO TABLE notes"):
            self.assertEqual(self.code(sql, "sqlite"), "files", sql)

    def test_a_select_into_outfile_or_dumpfile_is_refused(self):
        for sql in ("SELECT * FROM t INTO OUTFILE '/tmp/x'", "SELECT 1 INTO DUMPFILE '/tmp/x'"):
            self.assertEqual(self.code(sql, "mysql"), "files", sql)

    def test_the_code_of_a_mysql_versioned_comment_is_refused_because_the_word_check_cannot_see_it(self):
        for engine in ("mysql", "mariadb"):
            for sql in ("SELECT 1 /*!50000 INTO OUTFILE '/tmp/x' */", "SELECT 1 /*M!100000 INTO OUTFILE '/tmp/x' */"):
                self.assertEqual(self.code(sql, engine), "invalid", (engine, sql))

    def test_a_versioned_comment_is_only_a_comment_for_the_other_engines(self):
        refuse_file_effects("SELECT 1 /*!50000 + 1 */", "postgresql")
        refuse_file_effects("SELECT 1 /*!50000 + 1 */", "sqlite")

    def test_words_that_only_look_like_those_statements_pass(self):
        for sql in ("SELECT 'VACUUM INTO x, ATTACH, COPY, OUTFILE' AS words", "SELECT 1 /* ATTACH */", "VACUUM",
                    "SELECT 1 AS copy_of"):
            refuse_file_effects(sql, "sqlite")


class RefuseSessionEscapesTest(unittest.TestCase):
    def test_postgresql_calls_that_lift_the_read_only_session_are_refused(self):
        for sql in ("SELECT set_config('default_transaction_read_only', 'off', false)",
                    "SELECT pg_catalog.set_config('transaction_read_only', 'off', false)",
                    "SELECT query_to_xml('SELECT 1', true, true, '')",
                    "SELECT query_to_xml_and_xmlschema('SELECT 1', true, true, '')",
                    "SELECT query_to_xmlschema('SELECT 1', true, true, '')",
                    "SELECT cursor_to_xml('c', 1, true, true, '')",
                    "SELECT * FROM ts_stat('SELECT 1::tsvector')",
                    "SELECT dblink_exec('dbname=x', 'DELETE FROM notes')", "SELECT dblink('dbname=x', 'SELECT 1')",
                    "SELECT 1; SELECT SET_CONFIG('a', 'b', false)"):
            with self.assertRaises(Error, msg=sql) as caught:
                refuse_session_escapes(sql, "postgresql")
            self.assertEqual(caught.exception.code, "invalid", sql)

    def test_the_same_names_are_harmless_text_or_belong_to_other_engines(self):
        for sql in ("SELECT 'set_config query_to_xml ts_stat dblink' AS words", "SELECT 1 AS set_config_note"):
            refuse_session_escapes(sql, "postgresql")
        refuse_session_escapes("SELECT set_config('a', 'b', false)", "mysql")
        refuse_session_escapes("SELECT 1 FROM dblink_calls", "sqlite")


class RefuseDestructiveTest(unittest.TestCase):
    def code(self, sql, engine="sqlite"):
        with self.assertRaises(Error, msg=sql) as caught:
            refuse_destructive(sql, engine)
        return caught.exception.code

    def test_drop_truncate_and_changes_of_rights_are_refused(self):
        for sql in ("DROP TABLE notes", "TRUNCATE TABLE notes", "GRANT ALL ON *.* TO x", "REVOKE ALL ON *.* FROM x",
                    "SELECT 1; DROP TABLE notes", "  -- hi\n drop table notes"):
            self.assertEqual(self.code(sql), "destructive", sql)

    def test_alter_is_refused_only_when_it_drops(self):
        self.assertEqual(self.code("ALTER TABLE notes DROP COLUMN body"), "destructive")
        refuse_destructive("ALTER TABLE notes ADD COLUMN body TEXT", "sqlite")

    def test_what_hides_a_drop_from_the_first_keyword_is_refused_too(self):
        for sql in ("PREPARE s FROM 'DROP TABLE t'", "EXECUTE s", "SELECT 1; prepare s from 'DROP TABLE t'",
                    "DO $$ BEGIN DROP TABLE notes; END $$",
                    "PRAGMA writable_schema = 1; DELETE FROM sqlite_master WHERE name = 'notes'",
                    "PRAGMA writable_schema(1)"):
            self.assertEqual(self.code(sql), "destructive", sql)

    def test_statements_that_do_not_drop_pass(self):
        for sql in ("SELECT 1", "INSERT INTO notes VALUES (1)", "UPDATE notes SET body = 'drop table'",
                    "DELETE FROM notes WHERE id = 1", "CREATE TABLE t (x)", "PRAGMA user_version"):
            refuse_destructive(sql, "sqlite")


if __name__ == "__main__":
    unittest.main()
