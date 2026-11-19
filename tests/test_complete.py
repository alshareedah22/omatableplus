"""Runs the editor's JavaScript checks (tests/js/*.test.js: autocomplete, formatter, database URLs) with node, when node is installed."""

import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(__file__)
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "node is not installed")
class CompleteTest(unittest.TestCase):
    def run_js(self, name):
        out = subprocess.run([NODE, os.path.join(HERE, "js", name)], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "ok")

    def test_complete_js(self):
        self.run_js("complete.test.js")

    def test_format_js(self):
        self.run_js("format.test.js")

    def test_dburl_js(self):
        self.run_js("dburl.test.js")


if __name__ == "__main__":
    unittest.main()
