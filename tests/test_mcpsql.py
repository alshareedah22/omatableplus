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

    def test_a_query_that_reads_a_file_of_the_server_is_refused_with_the_files_code(self):
        # export_data only calls this guard, so it has to stand for the file ones too
        for engine, sql in (("mysql", "SELECT LOAD_FILE('/etc/passwd')"),
                            ("postgresql", "SELECT pg_read_file('/etc/passwd')"),
                            ("postgresql", 'SELECT "pg_read_file"(\'/etc/passwd\')')):
            self.assertEqual(self.code(sql, engine), "files", sql)

    def test_a_query_that_names_a_column_like_a_guard_word_passes(self):
        refuse_unless_read("SELECT outfile, load_file, set_config FROM t", "mysql")
        refuse_unless_read("SELECT pg_read_file, set_config FROM t", "postgresql")

    def test_a_query_that_reads_and_names_a_column_in_quotes_passes(self):
        refuse_unless_read('SELECT "name" FROM "notes"', "postgresql")
        refuse_unless_read("SELECT `name` FROM `notes`", "mysql")

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

    def test_the_functions_that_read_a_file_of_the_server_are_refused_in_any_position(self):
        for engine, sql in (("mysql", "SELECT LOAD_FILE('/etc/passwd')"),
                            ("mariadb", "select load_file('/etc/passwd') as secret"),
                            ("mysql", "SELECT 1; SELECT x FROM t WHERE x = LOAD_FILE('/etc/passwd')"),
                            ("postgresql", "SELECT pg_read_file('/etc/passwd')"),
                            ("postgresql", "SELECT pg_catalog.pg_read_binary_file('/etc/passwd')"),
                            ("postgresql", "SELECT * FROM pg_ls_dir('/etc')"),
                            ("postgresql", "SELECT * FROM pg_stat_file('/etc/passwd')"),
                            ("postgresql", "SELECT lo_import('/etc/passwd')"),
                            ("postgresql", "SELECT lo_export(16384, '/tmp/x')")):
            self.assertEqual(self.code(sql, engine), "files", sql)

    def test_the_file_functions_are_seen_when_their_name_is_quoted(self):
        for engine, sql in (("postgresql", 'SELECT "pg_read_file"(\'/etc/passwd\')'),
                            ("postgresql", 'SELECT pg_catalog."pg_ls_dir"(\'/etc\')'),
                            ("mysql", "SELECT `LOAD_FILE`('/etc/passwd')"),
                            ("sqlite", "SELECT [load_file]('/etc/passwd')")):
            self.assertEqual(self.code(sql, engine), "files", sql)

    def test_a_file_function_stays_refused_however_its_call_is_written(self):
        for engine, sql in (("mysql", "SELECT Load_File('/etc/passwd')"),
                            ("mysql", "SELECT LOAD_FILE /*x*/ ('/etc/passwd')"),
                            ("mysql", "SELECT LOAD_FILE\n('/etc/passwd')"),
                            ("mysql", "SELECT LOAD_FILE -- c\n('/etc/passwd')"),
                            ("mysql", "SELECT `LOAD_FILE` ('/etc/passwd')"),
                            ("mysql", "SELECT x FROM (SELECT LOAD_FILE('/etc/passwd') AS x) s"),
                            ("mysql", "WITH c AS (SELECT LOAD_FILE('/etc/passwd') AS x) SELECT x FROM c"),
                            ("mysql", "SELECT 1; SELECT load_file('/etc/passwd')"),
                            ("postgresql", "SELECT pg_catalog.pg_read_file ('/etc/passwd')"),
                            ("postgresql", 'SELECT "pg_catalog"."pg_read_file"(\'/etc/passwd\')'),
                            ("postgresql", "SELECT PG_Read_File\t('/etc/passwd')"),
                            ("postgresql", "SELECT * FROM (SELECT pg_ls_dir('/etc')) s"),
                            ("postgresql", "WITH c AS (SELECT lo_import('/etc/passwd')) SELECT * FROM c"),
                            ("sqlite", "SELECT 1; SELECT load_file /*x*/ ('/etc/passwd')")):
            self.assertEqual(self.code(sql, engine), "files", (engine, sql))

    def test_into_outfile_and_into_dumpfile_stay_refused_however_they_are_spaced(self):
        for sql in ("SELECT * FROM t INTO\nOUTFILE '/tmp/x'", "SELECT 1 INTO /*c*/ DUMPFILE '/tmp/x'",
                    "SELECT 1 INTO -- c\n OUTFILE '/tmp/x'", "select 1 into outfile '/tmp/x'",
                    "SELECT 1; SELECT 2 INTO OUTFILE '/tmp/x'", "SELECT * FROM (SELECT 1) s INTO\tDumpFile '/tmp/x'"):
            self.assertEqual(self.code(sql, "mysql"), "files", sql)

    def test_a_guard_word_that_is_not_called_nor_follows_into_is_a_name_and_passes(self):
        for sql in ("SELECT outfile FROM t", "SELECT dumpfile, outfile FROM t WHERE outfile > 1",
                    'SELECT "outfile" FROM t', "SELECT `dumpfile` FROM `outfile`", "SELECT load_file FROM t",
                    "SELECT 1 AS load_file_count", "SELECT pg_read_file, lo_import AS lo_export FROM t",
                    "SELECT 1 AS pg_ls_dir"):
            for engine in ("mysql", "mariadb", "postgresql", "sqlite"):
                refuse_file_effects(sql, engine)

    def test_the_names_of_file_functions_as_text_or_as_part_of_another_name_pass(self):
        for sql in ("SELECT 'LOAD_FILE pg_read_file lo_import' AS words", "SELECT 1 AS load_file_count",
                    "SELECT 1 /* pg_ls_dir */", 'SELECT "name" FROM "files"'):
            for engine in ("mysql", "postgresql", "sqlite"):
                refuse_file_effects(sql, engine)


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

    def test_the_same_names_are_seen_when_they_are_quoted(self):
        for sql in ("SELECT \"set_config\"('transaction_read_only', 'off', false)",
                    "SELECT pg_catalog.\"set_config\"('transaction_read_only', 'off', false)",
                    'SELECT * FROM "TS_STAT"(\'SELECT 1::tsvector\')',
                    "SELECT \"dblink_exec\"('dbname=x', 'DELETE FROM notes')"):
            with self.assertRaises(Error, msg=sql) as caught:
                refuse_session_escapes(sql, "postgresql")
            self.assertEqual(caught.exception.code, "invalid", sql)

    def test_the_calls_stay_refused_however_they_are_written(self):
        for sql in ("SELECT SeT_CoNfIg('a', 'b', false)", "SELECT set_config /*x*/ ('a', 'b', false)",
                    "SELECT set_config\n('a', 'b', false)", "SELECT set_config -- c\n('a', 'b', false)",
                    "SELECT pg_catalog.set_config ('a', 'b', false)", 'SELECT pg_catalog."set_config"(\'a\', \'b\', false)',
                    "SELECT 1; SELECT set_config('a', 'b', false)",
                    "SELECT * FROM (SELECT set_config('a', 'b', false)) s",
                    "WITH c AS (SELECT set_config('a', 'b', false)) SELECT * FROM c",
                    "SELECT dblink /*x*/ ('dbname=x', 'SELECT 1')", "SELECT DBLINK_EXEC\n('dbname=x', 'DELETE FROM t')",
                    "SELECT * FROM ts_stat ('SELECT 1::tsvector')", "SELECT Query_To_Xml\t('SELECT 1', true, true, '')"):
            with self.assertRaises(Error, msg=sql) as caught:
                refuse_session_escapes(sql, "postgresql")
            self.assertEqual(caught.exception.code, "invalid", sql)

    def test_a_guard_word_that_is_not_called_is_a_name_and_passes(self):
        for sql in ("SELECT set_config FROM t", 'SELECT "set_config", "ts_stat" FROM "dblink"',
                    "SELECT query_to_xml, cursor_to_xml FROM t WHERE ts_stat > 1", "SELECT dblink_exec FROM t",
                    "SELECT a AS dblink_conn, b AS set_config FROM t"):
            refuse_session_escapes(sql, "postgresql")

    def test_a_quoted_name_that_is_not_one_of_them_passes(self):
        for sql in ('SELECT "name", "set_config_note" FROM "notes"', "SELECT 'set_config' AS word",
                    'SELECT "my col" FROM t'):
            refuse_session_escapes(sql, "postgresql")

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

    def test_the_code_of_a_mysql_versioned_comment_is_refused_here_too(self):
        # the guard has to hold alone: /*! ... */ is a comment to the word check and code to MySQL
        for engine in ("mysql", "mariadb"):
            for sql in ("/*!50000 DROP TABLE t */", "SELECT 1 /*M!100000 DROP TABLE t */"):
                self.assertEqual(self.code(sql, engine), "invalid", (engine, sql))
        refuse_destructive("SELECT 1 /*!50000 + 1 */", "sqlite")
        refuse_destructive("SELECT 1 /* DROP TABLE t */", "mysql")

    def test_a_quoted_name_does_not_hide_what_the_guard_looks_for(self):
        for sql in ('PRAGMA "writable_schema"=1', "PRAGMA [writable_schema] = 1", 'PRAGMA main."writable_schema"(1)'):
            self.assertEqual(self.code(sql), "destructive", sql)
        self.assertEqual(self.code("PRAGMA `writable_schema` = 1", "mysql"), "destructive")

    def test_writable_schema_stays_refused_wherever_the_pragma_is(self):
        for sql in ("EXPLAIN PRAGMA writable_schema=1", "pragma Writable_Schema=1", "SELECT 1; PRAGMA writable_schema=1",
                    'PRAGMA main."writable_schema"=1', "/* c */ PRAGMA\nwritable_schema = ON"):
            self.assertEqual(self.code(sql), "destructive", sql)

    def test_writable_schema_that_is_not_the_pragma_is_a_name_and_passes(self):
        for sql in ("SELECT writable_schema FROM t", 'SELECT "writable_schema" FROM t',
                    "UPDATE t SET writable_schema = 1", "PRAGMA table_info(t)", "SELECT 1 AS writable_schema"):
            refuse_destructive(sql, "sqlite")

    def test_quoted_names_and_text_that_are_not_drops_pass(self):
        for sql in ('SELECT "name" FROM "notes"', 'INSERT INTO "notes" ("body") VALUES (\'writable_schema\')',
                    'SELECT "id" FROM "drop_log"', "PRAGMA user_version"):
            refuse_destructive(sql, "sqlite")

    def test_statements_that_do_not_drop_pass(self):
        for sql in ("SELECT 1", "INSERT INTO notes VALUES (1)", "UPDATE notes SET body = 'drop table'",
                    "DELETE FROM notes WHERE id = 1", "CREATE TABLE t (x)", "PRAGMA user_version"):
            refuse_destructive(sql, "sqlite")


if __name__ == "__main__":
    unittest.main()
