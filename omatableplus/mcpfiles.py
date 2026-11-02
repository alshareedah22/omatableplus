"""The one folder where MCP tools read and write files.

The helper's export, backup, restore and import methods take any path. Handed to a model as they are, they would
let it write or read anywhere with the user's rights. Here a tool names a bare file in this folder, nothing else.
"""

import os

from .protocol import Error


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
