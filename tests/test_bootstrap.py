import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
VERSION = re.search(r'__version__ = "([^"]+)"', open(os.path.join(ROOT, "omatableplus", "__init__.py")).read()).group(1)
CTL = os.path.join(ROOT, "tablectl")
NET = os.environ.get("OMATABLEPLUS_TEST_NET") == "1"


def run(env_extra, stdin=""):
    env = {**os.environ, "OMATABLEPLUS_NO_KEYRING": "1", **env_extra}
    r = subprocess.run([CTL], input=stdin, capture_output=True, text=True, env=env, timeout=600)
    return r.returncode, [json.loads(l) for l in r.stdout.splitlines() if l.startswith("{")], r.stderr


class LockTest(unittest.TestCase):
    def test_every_requirement_is_pinned_with_hashes(self):
        with open(os.path.join(ROOT, "requirements.lock")) as f:
            text = f.read()
        reqs = re.findall(r"^([A-Za-z0-9_.-]+)==([^ ]+) \\\n((?:    --hash=sha256:[0-9a-f]{64}(?: \\\n|\n))+)", text, re.M)
        names = {r[0] for r in reqs}
        self.assertEqual(names, {"psycopg", "psycopg-binary", "PyMySQL", "redis", "pymongo", "dnspython",
                                 "typing-extensions"})


class MigrateTest(unittest.TestCase):
    """What OmaTable (the name before 1.6) left behind moves to the OmaTablePlus paths."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg, self.data = os.path.join(self.tmp, "config"), os.path.join(self.tmp, "data")

    def migrate(self):
        import importlib.machinery
        import importlib.util
        from unittest import mock
        env = {"XDG_CONFIG_HOME": self.cfg, "XDG_DATA_HOME": self.data}
        with mock.patch.dict(os.environ, env):
            os.environ.pop("OMATABLEPLUS_CONFIG", None)
            os.environ.pop("OMATABLEPLUS_DATA", None)
            loader = importlib.machinery.SourceFileLoader("tablectl_under_test", CTL)
            spec = importlib.util.spec_from_loader(loader.name, loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            mod.migrate()

    def make(self, *parts, text=""):
        path = os.path.join(self.tmp, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def test_moves_settings_and_drops_the_old_drivers(self):
        self.make("config", "omatable", "connections.json", text='[{"name": "x"}]')
        self.make("config", "omatable", "history.jsonl")
        self.make("data", "omatable", "venv", ".omatable-stamp")
        self.migrate()
        with open(os.path.join(self.cfg, "omatableplus", "connections.json")) as f:
            self.assertEqual(f.read(), '[{"name": "x"}]')
        self.assertTrue(os.path.exists(os.path.join(self.cfg, "omatableplus", "history.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.cfg, "omatable")))
        self.assertFalse(os.path.exists(os.path.join(self.data, "omatable")))

    def test_leaves_alone_what_is_not_ours_or_already_moved(self):
        self.make("config", "omatableplus", "connections.json", text="[]")
        self.make("config", "omatable", "connections.json", text='[{"name": "old"}]')
        self.make("data", "omatable", "venv", "something-else")          # no stamp: not our venv
        self.migrate()
        with open(os.path.join(self.cfg, "omatableplus", "connections.json")) as f:
            self.assertEqual(f.read(), "[]")
        self.assertTrue(os.path.exists(os.path.join(self.cfg, "omatable", "connections.json")))
        self.assertTrue(os.path.exists(os.path.join(self.data, "omatable", "venv", "something-else")))


def load_tablectl():
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("tablectl_under_test", CTL)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class McpLauncherTest(unittest.TestCase):
    """`tablectl mcp` runs the MCP server from the drivers' venv; stdout then belongs to the MCP client."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def launch(self, argv):
        import contextlib
        import io
        import warnings
        from unittest import mock
        mod = load_tablectl()
        out, err = io.StringIO(), io.StringIO()

        def fake_venv():
            mod.emit({"event": "progress", "text": "Preparing database drivers…"})
            return "/venv/bin/python"

        with mock.patch.object(mod, "migrate"), mock.patch.object(mod, "ensure_venv", fake_venv), \
                mock.patch.object(mod.os, "execve") as execve, mock.patch.object(mod.sys, "argv", ["tablectl", *argv]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), warnings.catch_warnings():
            # a connection another test left open can be collected here, and its warning would land in err
            warnings.simplefilter("ignore", ResourceWarning)
            mod.main()
        return out.getvalue(), err.getvalue(), execve.call_args.args[:2]

    def test_mcp_mode_keeps_stdout_for_the_client_and_forwards_flags(self):
        out, err, (path, argv) = self.launch(["mcp", "--write"])
        self.assertEqual(out, "")
        self.assertIn('"progress"', err)
        self.assertEqual((path, argv), ("/venv/bin/python", ["/venv/bin/python", "-m", "omatableplus.mcp", "--write"]))

    def test_the_window_still_gets_its_events_on_stdout(self):
        out, err, (path, argv) = self.launch([])
        self.assertIn('"progress"', out)
        self.assertEqual(err, "")
        self.assertEqual(argv, ["/venv/bin/python", "-m", "omatableplus"])

    def test_the_readme_example_names_the_launcher_with_an_absolute_path(self):
        # most MCP clients run `command` without a shell, so a `~` would be looked for as a folder name
        with open(os.path.join(ROOT, "README.md")) as f:
            readme = f.read()
        section = readme[readme.index("## MCP server"):]
        config = json.loads(re.search(r"```json\n(.*?)\n```", section, re.S).group(1))
        command = config["mcpServers"]["omatableplus"]["command"]
        self.assertTrue(os.path.isabs(command), command)
        self.assertEqual(os.path.basename(command), os.path.basename(CTL))
        self.assertIn("blackcode.omatableplus", command)
        self.assertEqual(config["mcpServers"]["omatableplus"]["args"], ["mcp"])
        self.assertRegex(section.split("```")[2], r"(?is)replace.*path")

    def test_end_to_end_with_drivers_already_installed(self):
        mod = load_tablectl()
        data = os.path.join(self.tmp, "data")
        os.makedirs(os.path.join(data, "venv", "bin"))
        os.symlink("/usr/bin/python3", os.path.join(data, "venv", "bin", "python"))
        with open(os.path.join(data, "venv", ".omatableplus-stamp"), "w") as f:
            f.write(mod.stamp() + "\n")
        env = {**os.environ, "OMATABLEPLUS_DATA": data, "OMATABLEPLUS_CONFIG": os.path.join(self.tmp, "cfg"),
               "OMATABLEPLUS_NO_KEYRING": "1", "OMATABLEPLUS_DBCTL": os.path.join(self.tmp, "none")}
        asks = "".join(json.dumps(m) + "\n" for m in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
        names = {}
        for flags in ([], ["--write"]):
            r = subprocess.run([CTL, "mcp", *flags], input=asks, capture_output=True, text=True, env=env, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            replies = [json.loads(line) for line in r.stdout.splitlines()]
            self.assertEqual([x["id"] for x in replies], [1, 2], r.stdout)
            names[bool(flags)] = {t["name"] for t in replies[1]["result"]["tools"]}
        self.assertNotIn("commit_changes", names[False])
        self.assertIn("commit_changes", names[True])


@unittest.skipUnless(NET, "set OMATABLEPLUS_TEST_NET=1 to download drivers")
class BootstrapTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_install_then_reuse(self):
        data = os.path.join(self.tmp, "data")
        env = {"OMATABLEPLUS_DATA": data, "OMATABLEPLUS_CONFIG": os.path.join(self.tmp, "cfg")}
        code, msgs, err = run(env, json.dumps({"id": 1, "method": "app.version"}) + "\n")
        self.assertEqual(code, 0, err)
        self.assertIn("progress", [m.get("event") for m in msgs])
        self.assertEqual(msgs[-1], {"id": 1, "result": {"version": VERSION}})
        py = os.path.join(data, "venv", "bin", "python")
        imports = "import psycopg, pymysql, redis, pymongo; print('ok')"
        self.assertEqual(subprocess.run([py, "-c", imports], capture_output=True, text=True).stdout.strip(), "ok")
        code, msgs, _ = run(env, "")
        self.assertEqual(code, 0)
        self.assertNotIn("progress", [m.get("event") for m in msgs])
        self.assertEqual(msgs[0]["event"], "ready")

    def test_tampered_lock_refuses_and_leaves_nothing(self):
        with open(os.path.join(ROOT, "requirements.lock")) as f:
            text = f.read()
        bad = re.sub(r"(PyMySQL==[^\n]+\n    --hash=sha256:)[0-9a-f]{64}", r"\g<1>" + "0" * 64, text)
        lock = os.path.join(self.tmp, "bad.lock")
        with open(lock, "w") as f:
            f.write(bad)
        data = os.path.join(self.tmp, "data")
        code, msgs, _ = run({"OMATABLEPLUS_DATA": data, "OMATABLEPLUS_LOCK": lock})
        self.assertEqual(code, 1)
        self.assertEqual(msgs[-1]["event"], "fatal")
        self.assertEqual(msgs[-1]["code"], "drivers")
        self.assertFalse(os.path.exists(os.path.join(data, "venv")))
        self.assertFalse(os.path.exists(os.path.join(data, "venv.new")))


if __name__ == "__main__":
    unittest.main()
