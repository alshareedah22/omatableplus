import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from omatableplus import tunnel
from omatableplus.protocol import Error

SSHD = "/usr/bin/sshd"


def echo_server():
    """A TCP server on 127.0.0.1 that echoes one message per connection."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            with c:
                c.sendall(c.recv(1024))
    threading.Thread(target=loop, daemon=True).start()
    return srv


class Sshd:
    """An unprivileged sshd on a free port that accepts one generated key."""

    def __init__(self, password_auth=False):
        self.dir = tempfile.mkdtemp()
        d = self.dir
        for name in ("host", "user"):
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", os.path.join(d, name)], check=True)
        shutil.copy(os.path.join(d, "user.pub"), os.path.join(d, "authorized_keys"))
        self.port = tunnel.free_port()
        with open(os.path.join(d, "cfg"), "w") as f:
            f.write(f"Port {self.port}\nListenAddress 127.0.0.1\nHostKey {d}/host\nPidFile {d}/pid\n"
                    f"AuthorizedKeysFile {d}/authorized_keys\nStrictModes no\nUsePAM no\n"
                    f"PasswordAuthentication {'yes' if password_auth else 'no'}\n"
                    "KbdInteractiveAuthentication no\nAllowTcpForwarding yes\n")
        self.proc = subprocess.Popen([SSHD, "-D", "-e", "-f", f"{d}/cfg"], stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        self.key = os.path.join(d, "user")

    def close(self):
        self.proc.terminate()
        self.proc.wait()
        shutil.rmtree(self.dir, ignore_errors=True)


class TunnelUnitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["OMATABLEPLUS_SSH_KNOWN_HOSTS"] = os.path.join(self.tmp.name, "known_hosts")

    def tearDown(self):
        os.environ.pop("OMATABLEPLUS_SSH_KNOWN_HOSTS", None)
        self.tmp.cleanup()

    def test_free_port(self):
        port = tunnel.free_port()
        self.assertTrue(1024 < port < 65536)

    def test_command_has_no_secret(self):
        t = tunnel.Tunnel({"host": "jump", "port": 2222, "user": "me", "auth": "password"}, "db", 5432, "s3cret")
        argv = t.command(40000)
        self.assertNotIn("s3cret", " ".join(argv))
        self.assertIn("ExitOnForwardFailure=yes", argv)
        self.assertIn("127.0.0.1:40000:db:5432", argv)
        self.assertEqual(argv[-1], "me@jump")
        self.assertIn("2222", argv)
        key = tunnel.Tunnel({"host": "h", "user": "u", "auth": "key", "key_path": "/k"}, "db", 1).command(1)
        self.assertIn("BatchMode=yes", key)
        self.assertIn("/k", key)

    def test_askpass_reads_fifo(self):
        fifo = os.path.join(self.tmp.name, "fifo")
        os.mkfifo(fifo, 0o600)

        def feed():
            with open(fifo, "w") as f:
                f.write("pa ss\n")
        threading.Thread(target=feed, daemon=True).start()
        out = subprocess.run([tunnel.ASKPASS, "Password:"], capture_output=True, text=True, timeout=5,
                             env={**os.environ, "OMATABLEPLUS_ASKPASS_FIFO": fifo})
        self.assertEqual(out.stdout, "pa ss\n")

    def test_failure_reports_stderr(self):
        t = tunnel.Tunnel({"host": "127.0.0.1", "port": 1, "user": "nobody", "auth": "agent"}, "127.0.0.1", 5432)
        with self.assertRaises(Error) as e:
            t.start(timeout=10)
        self.assertEqual(e.exception.code, "tunnel")
        self.assertIn("refused", e.exception.message.lower())
        self.assertFalse(t.alive())


@unittest.skipUnless(os.access(SSHD, os.X_OK), "sshd not installed")
class TunnelSshdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["OMATABLEPLUS_SSH_KNOWN_HOSTS"] = os.path.join(self.tmp.name, "known_hosts")

    def tearDown(self):
        os.environ.pop("OMATABLEPLUS_SSH_KNOWN_HOSTS", None)
        self.tmp.cleanup()

    def test_key_tunnel_forwards(self):
        sshd, echo = Sshd(), echo_server()
        try:
            t = tunnel.Tunnel({"host": "127.0.0.1", "port": sshd.port, "user": os.environ["USER"],
                               "auth": "key", "key_path": sshd.key}, "127.0.0.1", echo.getsockname()[1])
            port = t.start()
            with socket.create_connection(("127.0.0.1", port), timeout=5) as c:
                c.sendall(b"ping")
                self.assertEqual(c.recv(16), b"ping")
            self.assertTrue(t.alive())
            t.close()
            self.assertFalse(t.alive())
        finally:
            echo.close()
            sshd.close()

    def test_password_goes_through_askpass(self):
        # An unprivileged sshd cannot check passwords, so auth fails, but the
        # FIFO must have been read by ssh's askpass, proving the path works.
        sshd = Sshd(password_auth=True)
        try:
            t = tunnel.Tunnel({"host": "127.0.0.1", "port": sshd.port, "user": os.environ["USER"],
                               "auth": "password"}, "127.0.0.1", 5432, password="wrong")
            with self.assertRaises(Error) as e:
                t.start(timeout=15)
            self.assertEqual(e.exception.code, "tunnel")
            self.assertTrue(t.askpass_used)
        finally:
            sshd.close()


class ForwardHost(unittest.TestCase):
    def test_accepts_names_and_addresses(self):
        for host, want in (("db", "db"), ("127.0.0.1", "127.0.0.1"), ("db.internal", "db.internal"),
                           ("::1", "[::1]"), ("[::1]", "[::1]"), ("fe80::1", "[fe80::1]"), ("", "127.0.0.1")):
            self.assertEqual(tunnel.forward_host(host), want, host)

    def test_refuses_anything_that_could_change_the_forward_spec(self):
        for host in ("a:b", "1.2.3.4:5", "-oProxyCommand=x", "a b", "a\nb", "db;ls", "[a:b]"):
            with self.assertRaises(Error, msg=host):
                tunnel.forward_host(host)

    def test_command_uses_the_checked_host(self):
        argv = tunnel.Tunnel({"host": "jump", "user": "me", "auth": "agent"}, "::1", 5432).command(6000)
        self.assertIn("127.0.0.1:6000:[::1]:5432", argv)
        with self.assertRaises(Error):
            tunnel.Tunnel({"host": "jump", "user": "me", "auth": "agent"}, "db:22", 5432).command(6000)



if __name__ == "__main__":
    unittest.main()
