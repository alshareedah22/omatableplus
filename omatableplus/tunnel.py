"""SSH tunnels: `ssh -N -L 127.0.0.1:<free port>:<db host>:<db port>`.

Password auth goes through SSH_ASKPASS: ssh runs `askpass`, which reads the
password from a FIFO in a private 700 directory. The password never appears
in argv or the environment, and the FIFO is written exactly once.
"""

import ctypes
import ipaddress
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time

from .protocol import Error

_HOST_RE = re.compile(r"^[A-Za-z0-9._:%\-\[\]]+$")
_USER_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._@+\-]*$")


_FWD_HOST_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def _is_ipv6(text):
    try:
        ipaddress.IPv6Address(text)
        return True
    except ValueError:
        return False


def forward_host(host):
    """The host for the -L spec, checked: ':' would otherwise change the spec. A real IPv6
    address goes in brackets, as ssh wants it."""
    host = str(host or "127.0.0.1")
    if host.startswith("[") and host.endswith("]") and _is_ipv6(host[1:-1]):
        return host
    if _is_ipv6(host):
        return "[" + host + "]"
    if host.startswith("-") or not _FWD_HOST_RE.match(host):
        raise Error("tunnel", "invalid database host for the tunnel: use a host name or IP address "
                              "without spaces, ':' (except in an IPv6 address) or control characters")
    return host


def check_target(ssh):
    """Refuse SSH settings that ssh would read as options or that hold control characters."""
    host = str(ssh.get("host") or "")
    user = str(ssh.get("user") or "")
    if not host or host.startswith("-") or not _HOST_RE.match(host):
        raise Error("tunnel", "invalid SSH host: use a host name or IP address that does not start with '-' "
                              "and has no spaces or control characters")
    if not user or user.startswith("-") or not _USER_RE.match(user):
        raise Error("tunnel", "invalid SSH user: use a user name that does not start with '-' "
                              "and has no spaces or control characters")
    if (ssh.get("auth") or "agent") == "key":
        key = str(ssh.get("key_path") or "")
        if not key or key.startswith("-") or any(ord(c) < 32 or ord(c) == 127 for c in key):
            raise Error("tunnel", "invalid SSH key path: it must be set, must not start with '-' "
                                  "and must have no control characters")

ASKPASS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "askpass")
PR_SET_PDEATHSIG = 1

try:  # looked up before any fork: preexec_fn must not import or allocate much
    _prctl = ctypes.CDLL(None, use_errno=True).prctl
except (OSError, AttributeError):
    _prctl = None


def _die_with_parent():
    """ssh runs in its own session, so make the kernel stop it if tablectl dies."""
    if _prctl is not None:
        _prctl(PR_SET_PDEATHSIG, signal.SIGTERM)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Tunnel:
    def __init__(self, ssh, target_host, target_port, password=None):
        self.ssh = ssh
        self.target_host = target_host or "127.0.0.1"
        self.target_port = int(target_port)
        self.password = password
        self.proc = None
        self.port = None
        self.stderr = []
        self.askpass_used = False
        self._dir = None

    def command(self, local_port):
        ssh = self.ssh
        check_target(ssh)
        argv = ["ssh", "-N", "-L", f"127.0.0.1:{local_port}:{forward_host(self.target_host)}:{self.target_port}",
                "-p", str(ssh.get("port") or 22),
                "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30",
                "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]
        known = os.environ.get("OMATABLEPLUS_SSH_KNOWN_HOSTS")
        if known:
            argv += ["-o", f"UserKnownHostsFile={known}"]
        auth = ssh.get("auth") or "agent"
        if auth == "password":
            argv += ["-o", "PreferredAuthentications=password,keyboard-interactive",
                     "-o", "NumberOfPasswordPrompts=1", "-o", "PubkeyAuthentication=no"]
        else:
            argv += ["-o", "BatchMode=yes"]
            if auth == "key":
                argv += ["-i", os.path.abspath(os.path.expanduser(ssh["key_path"])), "-o", "IdentitiesOnly=yes"]
        argv += ["--", f"{ssh.get('user')}@{ssh.get('host')}"]
        return argv

    def _feed(self, fifo):
        try:
            with open(fifo, "w") as f:  # blocks until askpass opens it
                self.askpass_used = True
                f.write((self.password or "") + "\n")
        except OSError:
            pass

    def _drain(self, pipe):
        for line in iter(pipe.readline, ""):
            self.stderr.append(line.rstrip())
            del self.stderr[:-20]
        pipe.close()

    def start(self, timeout=20):
        check_target(self.ssh)
        port = free_port()
        env = {k: v for k, v in os.environ.items() if not k.startswith("SSH_ASKPASS")}
        fifo = None
        if (self.ssh.get("auth") or "agent") == "password":
            self._dir = tempfile.mkdtemp(prefix="omatableplus-")
            fifo = os.path.join(self._dir, "pass")
            os.mkfifo(fifo, 0o600)
            env.update(SSH_ASKPASS=ASKPASS, SSH_ASKPASS_REQUIRE="force", OMATABLEPLUS_ASKPASS_FIFO=fifo)
            threading.Thread(target=self._feed, args=(fifo,), daemon=True).start()
        try:
            self.proc = subprocess.Popen(self.command(port), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.PIPE, text=True, env=env, start_new_session=True,
                                         preexec_fn=_die_with_parent)
        except OSError as e:
            self._cleanup()
            raise Error("tunnel", f"cannot run ssh: {e}") from e
        threading.Thread(target=self._drain, args=(self.proc.stderr,), daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                time.sleep(0.1)  # let the drain thread catch the last lines
                self._cleanup()
                raise Error("tunnel", "SSH tunnel failed: " + (" ".join(self.stderr) or
                                                               f"ssh exited with code {self.proc.returncode}"))
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            except OSError:
                time.sleep(0.15)
                continue
            self.port = port
            self._cleanup(keep_proc=True)
            return port
        self.close()
        raise Error("tunnel", "SSH tunnel timed out" + (": " + " ".join(self.stderr) if self.stderr else ""))

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def _cleanup(self, keep_proc=False):
        if self._dir:
            fifo = os.path.join(self._dir, "pass")
            if not self.askpass_used and os.path.exists(fifo):
                try:  # unblock the feeder thread
                    os.close(os.open(fifo, os.O_RDONLY | os.O_NONBLOCK))
                except OSError:
                    pass
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None
        if not keep_proc and self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()

    def close(self):
        self._cleanup()
        if self.proc is not None:
            if self.proc.poll() is None:
                self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
