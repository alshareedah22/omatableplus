"""Static checks on the QML: database text never renders as rich text, and copied data never
becomes a process argument."""

import glob
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
FILES = sorted(glob.glob(os.path.join(ROOT, "qml", "*.qml")) + glob.glob(os.path.join(ROOT, "*.qml")))
OPENER = re.compile(r"(?<![\w.])(Text|Label)\s*\{")
INPUT = re.compile(r"(?<![\w])(?:Ui\.TextField|TextField|TextArea)\s*\{")
# The two code editors insert text from code (completion, formatting) while unfocused.
EDITORS = {"CodeEditor.qml", "JsonEditor.qml"}


def own_level(body):
    """The block's text without its nested blocks."""
    prev = None
    while prev != body:
        prev, body = body, re.sub(r"\{[^{}]*\}", "", body)
    return body


def read(f):
    with open(f) as fh:
        return fh.read()


def blocks(src):
    for m in OPENER.finditer(src):
        depth, j = 1, m.end()
        while depth and j < len(src):
            depth += {"{": 1, "}": -1}.get(src[j], 0)
            j += 1
        yield m.start(), src[m.end():j - 1]


class QmlSafetyTest(unittest.TestCase):
    def test_every_text_is_plain(self):
        # A Text's default format renders HTML, so a cell like <img src="http://…"> would be
        # fetched from the desktop. Every Text and Label says PlainText instead.
        missing = []
        for f in FILES:
            src = read(f)
            for pos, body in blocks(src):
                if "textFormat: Text.PlainText" not in own_level(body):
                    missing.append(f"{os.path.relpath(f, ROOT)}:{src.count(chr(10), 0, pos) + 1}")
        self.assertEqual(missing, [], "Text/Label without textFormat: Text.PlainText")

    def test_no_copy_as_argument(self):
        # Process arguments are readable by every local account (ps); wl-copy gets its data on stdin.
        offenders = [os.path.relpath(f, ROOT) for f in FILES
                     if os.path.basename(f) != "Clipboard.qml" and "wl-copy" in read(f)]
        self.assertEqual(offenders, [])

    def test_unfocused_inputs_read_no_clipboard(self):
        # Qt's text inputs read the clipboard whenever it changes, even hidden ones, and what they read
        # comes from another app. A field is read-only until it has focus, or stays read-only.
        offenders = []
        for f in FILES:
            if os.path.basename(f) in EDITORS:
                continue
            src = read(f)
            for m in INPUT.finditer(src):
                depth, j = 1, m.end()
                while depth and j < len(src):
                    depth += {"{": 1, "}": -1}.get(src[j], 0)
                    j += 1
                own = own_level(src[m.end():j - 1])
                ro = re.search(r"readOnly:\s*([^\n;]+)", own)
                if not ro or not ("!activeFocus" in ro.group(1) or ro.group(1).strip().startswith("true")):
                    offenders.append(f"{os.path.relpath(f, ROOT)}:{src.count(chr(10), 0, m.start()) + 1}")
        self.assertEqual(offenders, [])

    def test_the_connection_form_is_destroyed_while_closed(self):
        src = read(os.path.join(ROOT, "qml", "Welcome.qml"))
        self.assertRegex(src, r"Loader\s*\{[^{}]*id: form\b[^{}]*active: welcome\.formOpen")
        self.assertNotRegex(src, r"(?m)^\s*ConnectionForm\s*\{")

    def test_field_pastes_through_a_bounded_read(self):
        src = read(os.path.join(ROOT, "qml", "Field.qml"))
        self.assertIn("event.matches(StandardKey.Paste)", src)
        self.assertRegex(src, r"timeout \" \+ field\.pasteSeconds")
        self.assertRegex(src, r"head -c \" \+ field\.pasteMaxBytes")
        self.assertRegex(src, r"pasteMaxBytes: \d+")


if __name__ == "__main__":
    unittest.main()
