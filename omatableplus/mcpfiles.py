"""The one folder where MCP tools read and write files.

The helper's export, backup, restore and import methods take any path. Handed to a model as they are, they would
let it write or read anywhere with the user's rights. Here a tool names a bare file in this folder, nothing else.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import tempfile

from .protocol import Error

RECORD = ".backups.json"     # name -> sha256 of every file backup_database wrote; a name tool arguments cannot reach
LOCK = ".backups.lock"       # held while the record is read, changed and replaced; hidden like the record
HEAD_LINES = 6
LINE_CUT = 100
LINE_READ = 4096             # a longer physical line is read to its end, but only this much of it is kept
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


def physical_lines(path):
    """The first LINE_READ bytes of each physical line.

    A longer line is read to its end in chunks and counted once, so what follows the first chunk is never taken
    for the start of a line. What is kept of it is always longer than LINE_CUT, so `shown` marks it as cut.
    """
    with open(path, "rb") as f:
        for raw in iter(lambda: f.readline(LINE_READ), b""):
            last = raw
            while not last.endswith(b"\n") and last:
                last = f.readline(LINE_READ)
            yield raw.decode("utf-8", "replace").rstrip("\r\n")


def shown(line):
    return line[:LINE_CUT] + "..." if len(line) > LINE_CUT else line


def glance(path):
    """What a person needs to decide on a file: its size, its first lines, and the lines a client would run."""
    head, commands, lines, more = [], [], 0, 0
    for line in physical_lines(path):
        lines += 1
        if lines <= HEAD_LINES:
            head.append(shown(line))
        if RUNS_COMMANDS.match(line) and len(commands) < COMMAND_LINES:
            commands.append(shown(line))
        elif RUNS_COMMANDS.match(line):
            more += 1
    text = [f"{os.path.basename(path)}: {os.path.getsize(path)} bytes, {lines} lines", "First lines:"]
    text += [f"  {line}" for line in head]
    if commands:
        text += ["Lines the psql and mysql clients run as commands:"] + [f"  {line}" for line in commands]
    if more:
        text.append(f"  ({more} more not shown)")
    return "\n".join(text)


class Sandbox:
    def __init__(self, root):
        self.root = os.path.abspath(os.path.expanduser(root))

    def make(self):
        """The folder, created private. One that exists is never changed (it may be ~/Downloads), only refused when
        others can write to it: they could swap a file between the hash and the read."""
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        if os.stat(self.root).st_mode & 0o022:
            raise Error("invalid", f"{self.root} can be written to by other users; reserve it to your user "
                                   "(chmod go-w) or pick another folder for the MCP files")
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
        with self.locked():
            known = self.record()
            if known is None:
                raise Error("invalid", f"{RECORD} cannot be read, so it was not rewritten; "
                                       "move it away from the MCP files folder to start a new one")
            known[os.path.basename(path)] = digest(path)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
            with os.fdopen(fd, "w") as f:
                json.dump(known, f)
            os.replace(tmp, os.path.join(os.path.dirname(path), RECORD))

    @contextlib.contextmanager
    def locked(self):
        """One reader-modifier-replacer of the record at a time, also across server processes."""
        fd = os.open(os.path.join(self.make(), LOCK), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

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
        """The record; a damaged one vouches for nothing."""
        return self.record() or {}

    def record(self):
        """The record: {} when there is none yet, None when it exists but is not a readable record."""
        try:
            with open(os.path.join(self.root, RECORD)) as f:
                known = json.load(f)
        except FileNotFoundError:
            return {}
        except OSError as caught:
            raise Error("invalid", f"{RECORD} cannot be read ({caught.strerror})") from caught
        except ValueError:
            return None
        return known if isinstance(known, dict) else None

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
