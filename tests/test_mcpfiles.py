import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omatableplus.mcpfiles import Sandbox
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

    def test_a_symlinked_folder_is_not_followed_out_of_the_sandbox_name_space(self):
        os.makedirs(self.root)
        os.symlink(self.tmp.name, os.path.join(self.root, "up"))
        self.assertEqual(self.code(self.box.new, "up/x.csv"), "invalid")

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
