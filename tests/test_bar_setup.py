"""The bar widget must run the launcher's first-run setup (a regression: 2.0.0 ran `true`)."""

import os
import re
import stat
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")


class BarSetup(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "Bar.qml")) as f:
            self.src = f.read()

    def test_setup_process_runs_the_launcher_setup(self):
        m = re.search(r"Process\s*\{\s*id:\s*setup\b.*?command:\s*(\[[^\]]*\])", self.src, re.S)
        self.assertIsNotNone(m, "no setup Process in Bar.qml")
        self.assertEqual(re.sub(r"\s+", "", m.group(1)), '[root.launcher,"setup"]')

    def test_setup_is_started_on_load(self):
        self.assertRegex(self.src, r"Component\.onCompleted:\s*setup\.running\s*=\s*true")

    def test_the_launcher_exists_and_is_executable(self):
        launcher = os.path.join(ROOT, "app", "omatableplus")
        self.assertTrue(os.stat(launcher).st_mode & stat.S_IXUSR)
        with open(launcher) as f:
            self.assertIn("setup) setup ;;", f.read())


if __name__ == "__main__":
    unittest.main()
