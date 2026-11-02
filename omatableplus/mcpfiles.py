"""The one folder where MCP tools read and write files.

The helper's export, backup, restore and import methods take any path. Handed to a model as they are, they would
let it write or read anywhere with the user's rights. Here a tool names a bare file in this folder, nothing else.
"""

import hashlib
import json
import os
import re
import tempfile

from .protocol import Error

RECORD = ".backups.json"     # name -> sha256 of every file backup_database wrote; a name tool arguments cannot reach
HEAD_LINES = 6
LINE_CUT = 100
COMMAND_LINES = 5
# What the psql and mysql clients run as a shell command or a script when they meet it at the start of a line.
# `\connect`, `\restrict`, `\N` (a NULL in COPY data) and a lone `\.` (end of COPY data) are in every pg_dump.
RUNS_COMMANDS = re.compile(r"^\s*(?:\\(?!(?:connect|restrict|unrestrict)\b|N(?:\t|$)|\.\s*$)|system\s|source\s)", re.I)


def digest(path):
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def glance(path):
    """What a person needs to decide on a file: its size, its first lines, and the lines a client would run."""
    head, commands, lines = [], [], 0
    with open(path, "rb") as f:
        for raw in iter(lambda: f.readline(4096), b""):
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            lines += 1
            if lines <= HEAD_LINES:
                head.append(line[:LINE_CUT])
            if RUNS_COMMANDS.match(line) and len(commands) < COMMAND_LINES:
                commands.append(line[:LINE_CUT])
    text = [f"{os.path.basename(path)}: {os.path.getsize(path)} bytes, {lines} lines", "First lines:"]
    text += [f"  {line}" for line in head]
    if commands:
        text += ["Lines the psql and mysql clients run as commands:"] + [f"  {line}" for line in commands]
    return "\n".join(text)


class Sandbox:
    def __init__(self, root):
        self.root = os.path.abspath(os.path.expanduser(root))

    def make(self):
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        return os.path.realpath(self.root)

    def new(self, name):
        """The path of a file that does not exist yet; the folder is created on first use."""
        path = os.path.join(self.make(), self.name(name))
        if os.path.lexists(path):
            raise Error("exists", f"{os.path.basename(path)} already exists in the MCP files folder; pick another name")
        return path

    def existing(self, name):
        path = os.path.join(self.make(), self.name(name))
        if os.path.islink(path):
            raise Error("invalid", f"{os.path.basename(path)} is a symbolic link")
        if not os.path.isfile(path):
            raise Error("notfound", f"no file {os.path.basename(path)} in the MCP files folder; see list_files")
        return path

    def vouch(self, name):
        """Remember what a backup wrote, so restore can tell it from a file the model shaped."""
        path = self.existing(name)
        known = self.vouched_for()
        known[os.path.basename(path)] = digest(path)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
        with os.fdopen(fd, "w") as f:
            json.dump(known, f)
        os.replace(tmp, os.path.join(os.path.dirname(path), RECORD))

    def vouches(self, path):
        return self.vouched_for().get(os.path.basename(path)) == digest(path)

    def vouched(self, name):
        """The path of a file that backup_database wrote and nobody changed since."""
        path = self.existing(name)
        if not self.vouches(path):
            raise Error("invalid", f"{os.path.basename(path)} was not written by backup_database, or changed since; "
                                   "restore_database only loads those unless your client can ask you first. "
                                   "Restore any other file in the OmaTablePlus window")
        return path

    def vouched_for(self):
        try:
            with open(os.path.join(self.root, RECORD)) as f:
                known = json.load(f)
        except (OSError, ValueError):
            return {}
        return known if isinstance(known, dict) else {}

    def listing(self):
        if not os.path.isdir(self.root):
            return []
        out = []
        for entry in sorted(os.scandir(self.root), key=lambda e: e.name):
            if not entry.name.startswith(".") and entry.is_file(follow_symlinks=False):
                out.append({"name": entry.name, "bytes": entry.stat(follow_symlinks=False).st_size})
        return out

    @staticmethod
    def name(value):
        if not isinstance(value, str) or not value or value.startswith(".") or "\0" in value \
                or "/" in value or "\\" in value:
            raise Error("invalid", "file must be a bare file name inside the MCP files folder (see list_files)")
        return value
