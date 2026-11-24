"""ssh install-key: the key put on a server with its password, once, against a stand-in SSH server; and the
two scripts that do it on the server, run for real where they can be."""

import base64
import io
import os
import shutil
import socket
import subprocess
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import ssh  # noqa: E402

try:
    import paramiko
except ImportError:
    paramiko = None


class StandIn(object):
    """An SSH server on 127.0.0.1 that takes deploy / secret, keeps the lines it is given, and answers like
    a Linux server (it runs the sh command) or a Windows one (cmd.exe: sh is unknown, powershell works)."""

    def __init__(self, windows=False):
        self.windows = windows
        self.lines = []
        self.attempts = 0
        self.host_key = paramiko.ECDSAKey.generate()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.transports = []
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            t = paramiko.Transport(conn)
            t.add_server_key(self.host_key)
            self.transports.append(t)
            t.start_server(server=self.Interface(self))

    def handle(self, chan, command):
        data = b""
        while True:
            block = chan.recv(4096)
            if not block:
                break
            data += block
        line = data.decode().strip()
        unix = command.startswith("sh -c")
        if (unix and self.windows) or (not unix and not self.windows):
            chan.sendall_stderr(b"'sh' is not recognized as an internal or external command\r\n")
            chan.send_exit_status(1)
        elif line in self.lines:
            chan.sendall(ssh.KEY_ALREADY.encode() + b"\n")
            chan.send_exit_status(0)
        else:
            self.lines.append(line)
            chan.sendall(ssh.KEY_ADDED.encode() + b"\n")
            chan.send_exit_status(0)
        chan.close()

    def close(self):
        self.sock.close()
        for t in self.transports:
            t.close()

    if paramiko:
        class Interface(paramiko.ServerInterface):
            def __init__(self, owner):
                self.owner = owner

            def check_auth_password(self, username, password):
                self.owner.attempts += 1
                ok = (username, password) == ("deploy", "secret")
                return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

            def get_allowed_auths(self, username):
                return "password"

            def check_channel_request(self, kind, chanid):
                return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

            def check_channel_exec_request(self, channel, command):
                threading.Thread(target=self.owner.handle, args=(channel, command.decode()), daemon=True).start()
                return True


@unittest.skipUnless(paramiko and ssh.can_generate(), "needs paramiko and cryptography")
class InstallKey(TempCase):
    def server(self, windows=False, trust=True, trust_other=False):
        srv = StandIn(windows)
        self.addCleanup(srv.close)
        key = self.p("keys", "k_ed25519")
        if not os.path.isfile(key):
            ssh.generate_key(key, "", "test@here")
        t = ssh.Target("127.0.0.1", "deploy", srv.port, key, known_hosts=self.p("kh", "known_hosts"))
        shown = paramiko.ECDSAKey.generate() if trust_other else srv.host_key
        if trust:
            ssh.trust(t, [(shown.get_name(), shown.get_base64(), ssh.fingerprint(shown.get_base64()))])
        return srv, t

    def test_a_linux_server(self):
        srv, t = self.server()
        self.assertEqual(ssh.install_key(t, "secret"), "added")
        self.assertEqual(srv.lines, [ssh.authorized_keys_line(t.key)])
        self.assertEqual(ssh.install_key(t, "secret"), "already there")       # never added twice
        self.assertEqual(len(srv.lines), 1)

    def test_a_windows_server(self):
        srv, t = self.server(windows=True)
        self.assertEqual(ssh.install_key(t, "secret"), "added")               # sh unknown: PowerShell did it
        self.assertEqual(srv.lines, [ssh.authorized_keys_line(t.key)])

    def test_a_wrong_password(self):
        srv, t = self.server()
        with self.assertRaises(ssh.SshError) as cm:
            ssh.install_key(t, "guess")
        self.assertIn("refused the password", str(cm.exception))
        self.assertEqual(srv.lines, [])

    def test_no_password_to_an_untrusted_or_changed_server(self):
        srv, t = self.server(trust=False)
        with self.assertRaises(ssh.SshError):
            ssh.install_key(t, "secret")
        srv2, t2 = self.server(trust_other=True)
        with self.assertRaises(ssh.SshError) as cm:
            ssh.install_key(t2, "secret")
        self.assertIn("did not show the host key you trusted", str(cm.exception))
        self.assertEqual((srv.attempts, srv2.attempts), (0, 0))            # the password was never sent


class Scripts(TempCase):
    LINE = 'restrict,command="internal-sftp" ssh-ed25519 AAAATEST test@here'

    @unittest.skipUnless(shutil.which("sh"), "needs a POSIX sh")
    def test_the_unix_script(self):
        env = dict(os.environ, HOME=self.p("home"))
        os.makedirs(self.p("home"))
        for expected in (ssh.KEY_ADDED, ssh.KEY_ALREADY):
            r = subprocess.run(["sh", "-c", ssh.UNIX_INSTALL[len("sh -c '"):-1]], input=(self.LINE + "\n").encode(),
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.assertIn(expected, r.stdout.decode(), r.stdout)
        self.assertEqual(read(self.p("home", ".ssh", "authorized_keys")).decode().splitlines(), [self.LINE])
        if os.name != "nt":
            self.assertEqual(os.stat(self.p("home", ".ssh", "authorized_keys")).st_mode & 0o777, 0o600)

    @unittest.skipUnless(os.name == "nt", "the Windows script")
    def test_the_windows_script(self):
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            self.skipTest("an administrator's keys would go to ProgramData")
        env = dict(os.environ, USERPROFILE=self.p("profile"))
        os.makedirs(self.p("profile"))
        cmd = ssh.windows_install_command().split()
        for expected in (ssh.KEY_ADDED, ssh.KEY_ALREADY):
            r = subprocess.run(cmd, input=(self.LINE + "\n").encode(), env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
            self.assertIn(expected, r.stdout.decode(errors="replace"), r.stdout)
        self.assertEqual(read(self.p("profile", ".ssh", "authorized_keys")).decode().split(), self.LINE.split())


if __name__ == "__main__":
    unittest.main()
