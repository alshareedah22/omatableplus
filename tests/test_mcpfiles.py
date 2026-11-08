import fcntl
import os
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omatableplus import mcpfiles
from omatableplus.mcpfiles import Sandbox, digest, glance
from omatableplus.protocol import Error


class SandboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "files")
        self.box = Sandbox(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def code(self, call, *args):
        with self.assertRaises(Error) as caught:
            call(*args)
        return caught.exception.code

    def test_a_new_file_lives_in_the_folder_which_is_created_private(self):
        path = self.box.new("export.csv")
        self.assertEqual(path, os.path.join(os.path.realpath(self.root), "export.csv"))
        self.assertEqual(stat.S_IMODE(os.stat(self.root).st_mode), 0o700)
        self.assertFalse(os.path.exists(path))

    def test_a_folder_that_others_can_write_to_is_refused_and_left_as_it_is(self):
        for mode in (0o770, 0o707, 0o777, 0o722):
            os.makedirs(self.root, exist_ok=True)
            os.chmod(self.root, mode)
            for call in (self.box.new, self.box.existing):
                with self.assertRaises(Error) as caught:
                    call("a.csv")
                self.assertEqual(caught.exception.code, "invalid", oct(mode))
                self.assertIn("reserve it to your user", caught.exception.message)
            self.assertEqual(stat.S_IMODE(os.stat(self.root).st_mode), mode, "the folder is not changed")

    def test_a_folder_that_others_can_only_read_is_accepted_as_it_is(self):
        os.makedirs(self.root)
        for mode in (0o700, 0o750, 0o755, 0o705):
            os.chmod(self.root, mode)
            self.assertTrue(self.box.new("a.csv").endswith("a.csv"), oct(mode))
            self.assertEqual(stat.S_IMODE(os.stat(self.root).st_mode), mode, "the folder is not changed")

    def test_only_a_bare_file_name_is_accepted(self):
        for name in ("../x.csv", "/etc/passwd", "a/b.csv", "..", ".", "", ".hidden.csv", "a\0b", "x\\y", 5, None):
            self.assertEqual(self.code(self.box.new, name), "invalid", repr(name))
            self.assertEqual(self.code(self.box.existing, name), "invalid", repr(name))

    def test_an_existing_file_is_never_overwritten(self):
        self.box.new("a.csv")
        open(os.path.join(self.root, "a.csv"), "w").close()
        self.assertEqual(self.code(self.box.new, "a.csv"), "exists")

    def test_existing_finds_a_regular_file_and_reports_a_missing_one(self):
        self.box.new("seed.csv")
        open(os.path.join(self.root, "seed.csv"), "w").close()
        self.assertTrue(self.box.existing("seed.csv").endswith("seed.csv"))
        self.assertEqual(self.code(self.box.existing, "nope.csv"), "notfound")

    def test_a_symlink_is_refused_even_when_it_points_inside(self):
        os.makedirs(self.root)
        outside = os.path.join(self.tmp.name, "secret.csv")
        open(outside, "w").close()
        os.symlink(outside, os.path.join(self.root, "link.csv"))
        open(os.path.join(self.root, "real.csv"), "w").close()
        os.symlink(os.path.join(self.root, "real.csv"), os.path.join(self.root, "inner.csv"))
        for name in ("link.csv", "inner.csv"):
            self.assertEqual(self.code(self.box.existing, name), "invalid", name)
            self.assertEqual(self.code(self.box.new, name), "exists", name)

    def test_a_symlinked_folder_is_neither_followed_nor_replaced_nor_reached_through_a_path(self):
        os.makedirs(self.root)
        os.symlink(self.tmp.name, os.path.join(self.root, "up"))
        self.assertEqual(self.code(self.box.existing, "up"), "invalid")
        self.assertEqual(self.code(self.box.new, "up"), "exists")
        self.assertEqual(self.code(self.box.new, "up/x.csv"), "invalid")
        self.assertEqual(self.code(self.box.existing, "up/secret.csv"), "invalid")
        self.assertEqual(self.box.listing(), [])

    def written(self, name, content=b"dump"):
        with open(self.box.new(name), "wb") as f:
            f.write(content)

    def test_a_file_is_vouched_for_by_name_and_by_content(self):
        self.written("app.bak")
        self.assertEqual(self.code(self.box.vouched, "app.bak"), "invalid")
        self.box.vouch("app.bak")
        self.assertTrue(self.box.vouched("app.bak").endswith("app.bak"))
        with open(os.path.join(self.root, "app.bak"), "ab") as f:
            f.write(b"; \\! touch /tmp/pwned")
        self.assertEqual(self.code(self.box.vouched, "app.bak"), "invalid")

    def test_a_vouched_name_cannot_be_reused_for_other_content_nor_found_by_another_name(self):
        self.written("a.bak")
        self.written("b.bak")
        self.box.vouch("a.bak")
        self.assertEqual(self.code(self.box.vouched, "b.bak"), "invalid")
        self.assertEqual(self.code(self.box.new, "a.bak"), "exists")

    def test_vouching_needs_a_file_and_keeps_the_record_out_of_reach(self):
        self.assertEqual(self.code(self.box.vouch, "nope.bak"), "notfound")
        self.assertEqual(self.code(self.box.vouched, "nope.bak"), "notfound")
        self.written("a.bak")
        self.box.vouch("a.bak")
        self.assertEqual(self.box.listing(), [{"name": "a.bak", "bytes": 4}])
        for name in (".backups.json", "../files/.backups.json"):
            self.assertEqual(self.code(self.box.existing, name), "invalid", name)
            self.assertEqual(self.code(self.box.new, name), "invalid", name)

    def test_a_damaged_record_vouches_for_nothing(self):
        self.written("a.bak")
        self.box.vouch("a.bak")
        with open(os.path.join(self.root, ".backups.json"), "w") as f:
            f.write("{nope")
        self.assertEqual(self.code(self.box.vouched, "a.bak"), "invalid")

    def record(self):
        return os.path.join(self.root, ".backups.json")

    def test_vouching_does_not_overwrite_a_record_it_could_not_read(self):
        self.written("a.bak")
        self.written("b.bak")
        self.box.vouch("a.bak")
        for damaged in (b"{nope", b"[1, 2]", b"\xff\xfe", b"\"text\""):
            with open(self.record(), "wb") as f:
                f.write(damaged)
            self.assertEqual(self.code(self.box.vouch, "b.bak"), "invalid", damaged)
            with open(self.record(), "rb") as f:
                self.assertEqual(f.read(), damaged, "the record is left as it was")
            self.assertEqual(self.code(self.box.vouched, "b.bak"), "invalid")

    def test_an_unreadable_record_is_an_error_not_an_empty_one(self):
        self.written("a.bak")
        self.box.vouch("a.bak")
        os.remove(self.record())
        os.mkdir(self.record())
        self.assertEqual(self.code(self.box.vouched_for), "invalid")
        self.assertEqual(self.code(self.box.vouched, "a.bak"), "invalid")
        self.assertEqual(self.code(self.box.vouch, "a.bak"), "invalid")
        self.assertTrue(os.path.isdir(self.record()))

    def test_a_missing_record_is_an_empty_one_and_the_first_vouch_creates_it(self):
        self.written("a.bak")
        self.assertEqual(self.box.vouched_for(), {})
        self.assertFalse(os.path.exists(self.record()))
        self.box.vouch("a.bak")
        self.assertEqual(list(self.box.vouched_for()), ["a.bak"])

    def test_vouching_waits_for_whoever_holds_the_lock_of_the_record(self):
        self.written("a.bak")
        self.written("b.bak")
        self.box.vouch("a.bak")
        holder = os.open(os.path.join(self.root, ".backups.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(holder, fcntl.LOCK_EX)
        worker = threading.Thread(target=self.box.vouch, args=("b.bak",))
        try:
            worker.start()
            worker.join(0.3)
            self.assertTrue(worker.is_alive(), "vouch went on without the lock")
            self.assertEqual(list(self.box.vouched_for()), ["a.bak"])
        finally:
            fcntl.flock(holder, fcntl.LOCK_UN)
            os.close(holder)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(sorted(self.box.vouched_for()), ["a.bak", "b.bak"])

    def test_vouching_from_several_threads_loses_no_name(self):
        names = [f"n{i}.bak" for i in range(12)]
        for name in names:
            self.written(name, name.encode())
        workers = [threading.Thread(target=self.box.vouch, args=(name,)) for name in names]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
        self.assertEqual(sorted(self.box.vouched_for()), sorted(names))

    def test_the_lock_file_is_out_of_reach_like_the_record(self):
        self.written("a.bak")
        self.box.vouch("a.bak")
        self.assertTrue(os.path.exists(os.path.join(self.root, ".backups.lock")))
        self.assertEqual(self.box.listing(), [{"name": "a.bak", "bytes": 4}])
        for name in (".backups.lock", "../files/.backups.lock"):
            self.assertEqual(self.code(self.box.existing, name), "invalid", name)
            self.assertEqual(self.code(self.box.new, name), "invalid", name)
            self.assertEqual(self.code(self.box.vouch, name), "invalid", name)

    def test_glance_tells_size_first_lines_and_the_lines_a_client_would_run(self):
        self.written("dump.sql", b"-- dump\n\\connect app\nCOPY t FROM stdin;\n\\N\tx\n\\.\n"
                                 b"\\! touch /tmp/pwned\nSYSTEM id\n\\. other.sql\nsource other.sql\nSELECT 1;\n")
        text = glance(self.box.existing("dump.sql"))
        self.assertIn("dump.sql", text)
        self.assertIn("10 lines", text)
        self.assertIn("-- dump", text)
        for run in ("\\! touch /tmp/pwned", "SYSTEM id", "\\. other.sql", "source other.sql"):
            self.assertIn(run, text.split("run as commands", 1)[1], run)
        for harmless in ("\\connect app", "\\N\tx"):
            self.assertNotIn(harmless, text.split("run as commands", 1)[1], harmless)

    def test_glance_of_a_plain_file_has_no_command_section_and_cuts_long_lines(self):
        self.written("plain.sql", b"SELECT 1;\n" + b"x" * 5000 + b"\n")
        text = glance(self.box.existing("plain.sql"))
        self.assertNotIn("run as commands", text)
        self.assertLess(len(text), 1000)

    def leftovers(self):
        return [name for name in os.listdir(self.root) if name.startswith(".restore")]

    def refused(self, name, expected=None):
        with self.assertRaises(Error) as caught:
            with self.box.verified_copy(name, expected):
                self.fail("the body must not run")
        return caught.exception.code

    def read(self, path):
        with open(path, "rb") as f:
            return f.read()

    def test_a_verified_copy_is_the_vouched_file_in_a_private_hidden_folder_that_goes_away(self):
        self.written("app.bak", b"-- dump\nSELECT 1;\n")
        self.box.vouch("app.bak")
        with self.box.verified_copy("app.bak") as copy:
            self.assertEqual(self.read(copy), b"-- dump\nSELECT 1;\n")
            self.assertEqual(os.path.basename(copy), "app.bak")
            self.assertNotEqual(copy, os.path.join(os.path.realpath(self.root), "app.bak"))
            folder = os.path.dirname(copy)
            self.assertEqual(os.path.dirname(folder), os.path.realpath(self.root))
            self.assertTrue(os.path.basename(folder).startswith(".restore"))
            self.assertEqual(stat.S_IMODE(os.stat(copy).st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(os.stat(folder).st_mode), 0o700)
            self.assertEqual(self.box.listing(), [{"name": "app.bak", "bytes": 18}])
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.read(os.path.join(self.root, "app.bak")), b"-- dump\nSELECT 1;\n")

    def test_a_file_changed_after_it_was_vouched_is_refused_and_leaves_nothing(self):
        self.written("app.bak")
        self.box.vouch("app.bak")
        with open(os.path.join(self.root, "app.bak"), "ab") as f:
            f.write(b"; \\! touch /tmp/pwned")
        with self.assertRaises(Error) as caught:
            with self.box.verified_copy("app.bak"):
                self.fail("the body must not run")
        self.assertEqual(caught.exception.code, "invalid")
        self.assertEqual(self.leftovers(), [])

    def test_a_file_nobody_vouched_for_is_refused_unless_the_hash_the_person_saw_is_given(self):
        self.written("other.sql", b"SELECT 1;\n")
        self.assertEqual(self.refused("other.sql"), "invalid")
        with self.assertRaises(Error) as caught:
            self.box.verified_copy("other.sql").__enter__()
        self.assertIn("was not written by backup_database", caught.exception.message)
        self.assertEqual(self.leftovers(), [], "nothing is created for a file nobody vouched for")
        seen = digest(self.box.existing("other.sql"))
        with self.box.verified_copy("other.sql", expected=seen) as copy:
            self.assertEqual(self.read(copy), b"SELECT 1;\n")
        self.assertEqual(self.leftovers(), [])

    def test_a_hash_that_is_not_the_one_of_the_file_is_refused_even_for_a_vouched_file(self):
        self.written("app.bak")
        self.box.vouch("app.bak")
        self.written("other.sql", b"SELECT 1;\n")
        wrong = digest(self.box.existing("other.sql"))
        for expected in (wrong, "0" * 64, ""):
            with self.assertRaises(Error, msg=expected) as caught:
                with self.box.verified_copy("app.bak", expected=expected):
                    self.fail("the body must not run")
            self.assertEqual(caught.exception.code, "invalid")
        self.assertEqual(self.leftovers(), [])

    def test_a_file_changed_after_the_person_saw_it_is_refused(self):
        self.written("other.sql", b"SELECT 1;\n")
        seen = digest(self.box.existing("other.sql"))
        with open(os.path.join(self.root, "other.sql"), "ab") as f:
            f.write(b"DROP TABLE t;\n")
        self.assertEqual(self.refused("other.sql", seen), "invalid")
        self.assertEqual(self.leftovers(), [])

    def test_a_path_swapped_after_the_file_was_opened_does_not_change_what_was_hashed(self):
        self.written("app.bak", b"what the backup wrote\n")
        self.box.vouch("app.bak")
        self.written("evil.bak", b"\\! touch /tmp/pwned\n")
        real = os.fstat

        def swap_after_open(fd):
            seen = real(fd)
            os.replace(os.path.join(self.root, "evil.bak"), os.path.join(self.root, "app.bak"))
            return seen

        with mock.patch.object(mcpfiles.os, "fstat", swap_after_open):
            with self.box.verified_copy("app.bak") as copy:
                self.assertEqual(self.read(copy), b"what the backup wrote\n")
        self.assertEqual(self.read(os.path.join(self.root, "app.bak")), b"\\! touch /tmp/pwned\n")

    def test_the_copy_does_not_follow_the_file_it_was_made_from(self):
        self.written("app.bak", b"one")
        self.box.vouch("app.bak")
        with self.box.verified_copy("app.bak") as copy:
            with open(os.path.join(self.root, "app.bak"), "ab") as f:
                f.write(b" two")
            self.assertEqual(self.read(copy), b"one")

    def test_a_symbolic_link_a_folder_or_a_pipe_is_not_copied(self):
        self.written("real.bak")
        self.box.vouch("real.bak")
        seen = digest(self.box.existing("real.bak"))
        os.symlink(os.path.join(self.root, "real.bak"), os.path.join(self.root, "link.bak"))
        os.symlink(self.tmp.name, os.path.join(self.root, "up"))
        os.mkdir(os.path.join(self.root, "dir"))
        os.mkfifo(os.path.join(self.root, "pipe"))
        for name in ("link.bak", "up", "dir", "pipe"):
            self.assertEqual(self.refused(name, seen), "invalid", name)
        self.assertEqual(self.refused("nope.bak", seen), "notfound")
        self.assertEqual(self.leftovers(), [])

    def test_the_copy_is_removed_even_when_the_body_raises(self):
        self.written("app.bak")
        self.box.vouch("app.bak")
        with self.assertRaises(RuntimeError):
            with self.box.verified_copy("app.bak") as copy:
                folder = os.path.dirname(copy)
                raise RuntimeError("the restore failed")
        self.assertFalse(os.path.exists(folder))
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.read(os.path.join(self.root, "app.bak")), b"dump")

    def test_only_what_the_copy_created_is_removed(self):
        self.written("app.bak")
        self.box.vouch("app.bak")
        os.mkdir(os.path.join(self.root, ".restore-keepme"))
        with self.box.verified_copy("app.bak"):
            pass
        self.assertEqual(self.leftovers(), [".restore-keepme"])
        self.assertTrue(os.path.exists(os.path.join(self.root, "app.bak")))

    def test_a_tool_cannot_name_the_folder_of_a_copy_nor_a_file_in_it(self):
        self.written("app.bak")
        self.box.vouch("app.bak")
        with self.box.verified_copy("app.bak") as copy:
            folder = os.path.basename(os.path.dirname(copy))
            for name in (folder, ".restore-xxx", f"{folder}/app.bak", f"../files/{folder}/app.bak"):
                for call in (self.box.new, self.box.existing):
                    self.assertEqual(self.code(call, name), "invalid", (call, name))
                self.assertEqual(self.refused(name), "invalid", name)

    def commands_of(self, text):
        return text.split("run as commands", 1)[1] if "run as commands" in text else ""

    def test_glance_says_when_more_commands_than_it_shows_are_there(self):
        self.written("many.sql", b"".join(b"\\! cmd%d\n" % i for i in range(1, 9)) + b"SELECT 1;\n")
        text = glance(self.box.existing("many.sql"))
        for shown in range(1, 6):
            self.assertIn(f"\\! cmd{shown}\n", text + "\n")
        for hidden in range(6, 9):
            self.assertNotIn(f"cmd{hidden}", self.commands_of(text))
        self.assertIn("(3 more not shown)", self.commands_of(text))

    def test_glance_of_exactly_the_limit_of_commands_has_no_more_not_shown(self):
        self.written("five.sql", b"".join(b"\\! cmd%d\n" % i for i in range(1, 6)))
        text = glance(self.box.existing("five.sql"))
        self.assertIn("cmd5", self.commands_of(text))
        self.assertNotIn("more not shown", text)

    def test_glance_marks_the_lines_it_cut_and_only_those(self):
        self.written("cut.sql", b"x" * 150 + b"\n" + b"y" * 100 + b"\n" + b"\\! " + b"z" * 150 + b"\n"
                                + b"\\! " + b"w" * 97 + b"\n")
        text = glance(self.box.existing("cut.sql"))
        self.assertIn("  " + "x" * 100 + "...\n", text + "\n")
        self.assertIn("  " + "y" * 100 + "\n", text + "\n")
        self.assertNotIn("y" * 100 + "...", text)
        self.assertIn("  \\! " + "z" * 97 + "...", self.commands_of(text))
        self.assertIn("\\! " + "w" * 97 + "\n", self.commands_of(text) + "\n")
        self.assertNotIn("w" * 97 + "...", text)

    def test_glance_reads_a_physical_line_of_more_than_4096_bytes_as_one_line(self):
        # the part after the 4096th byte is not the start of a line: it must not count as one nor be run-checked
        self.written("long.sql", b"SELECT 1;\n" + b"x" * 4096 + b"\\! id\n" + b"SELECT 2;\n")
        text = glance(self.box.existing("long.sql"))
        self.assertIn("3 lines", text)
        self.assertNotIn("run as commands", text)
        self.assertIn("  " + "x" * 100 + "...", text)

    def test_glance_marks_and_counts_a_command_line_that_is_longer_than_4096_bytes(self):
        self.written("huge.sql", b"\\! " + b"y" * 5000 + b"\nSELECT 1;\n\\! id\n")
        text = glance(self.box.existing("huge.sql"))
        self.assertIn("3 lines", text)
        self.assertIn("  \\! " + "y" * 97 + "...", self.commands_of(text))
        self.assertIn("  \\! id", self.commands_of(text))

    def test_glance_counts_a_line_at_the_edge_of_the_4096_bytes_once(self):
        for width in (4095, 4096, 4097):
            for end in (b"\n", b"\r\n"):
                self.written(f"edge{width}{len(end)}.sql", b"-- " + b"a" * (width - 3) + end + b"\\! id\n")
                text = glance(self.box.existing(f"edge{width}{len(end)}.sql"))
                self.assertIn("2 lines", text, (width, end))
                self.assertIn("  \\! id", self.commands_of(text), (width, end))

    def test_listing_shows_visible_regular_files_with_their_size(self):
        self.assertEqual(self.box.listing(), [])
        self.box.new("seed.csv")
        for name, size in (("b.csv", 3), ("a.csv", 5), (".hidden", 1)):
            with open(os.path.join(self.root, name), "wb") as f:
                f.write(b"x" * size)
        os.mkdir(os.path.join(self.root, "dir"))
        self.assertEqual(self.box.listing(), [{"name": "a.csv", "bytes": 5}, {"name": "b.csv", "bytes": 3}])


if __name__ == "__main__":
    unittest.main()
