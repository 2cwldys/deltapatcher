"""The engine's --json mode, which the DeltaPatcher app drives, and the passphrase relay for ssh-add."""

import io
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import cli, ssh  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_json(argv, stdin_text=""):
    """cli.main with --json; returns (exit code, [events])."""
    out = io.StringIO()
    real_out, real_in = sys.stdout, sys.stdin
    sys.stdout, sys.stdin = out, io.StringIO(stdin_text)
    try:
        rc = cli.main(["--json", "--no-log"] + argv)
    finally:
        sys.stdout, sys.stdin = real_out, real_in
    return rc, [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]


class JsonMode(TempCase):
    def test_events_and_result(self):
        write(self.p("src", "a.pk3"), os.urandom(3000))
        rc, ev = run_json(["publish", self.p("src"), self.p("store"), "--name", "G", "--version", "1"])
        self.assertEqual(rc, 0)
        self.assertEqual(ev[-1]["event"], "result")
        self.assertEqual(ev[-1]["data"]["files"], 1)
        self.assertTrue(any(e["event"] == "log" and e["text"].startswith("published") and e["style"] == "ok" for e in ev))
        self.assertTrue(all("\x1b" not in json.dumps(e) for e in ev))
        rc, ev = run_json(["update", self.p("inst"), "--source", self.p("store"), "--check"])
        self.assertEqual(rc, 2)
        self.assertEqual(ev[-1]["data"]["add"], 1)
        rc, ev = run_json(["update", self.p("inst"), "--source", self.p("store")])
        self.assertEqual(rc, 0)
        self.assertTrue(any(e["event"] == "progress" for e in ev))
        self.assertTrue(ev[-1]["data"]["empty"] is False)

    def test_error_has_a_code(self):
        rc, ev = run_json(["update", self.p("inst"), "--source", self.p("nothing-here")])
        self.assertEqual(rc, 1)
        self.assertEqual(ev[-1]["event"], "error")
        self.assertIn("manifest.json", ev[-1]["message"])

    def test_config_get_and_set(self):
        rc, ev = run_json(["config", "set", "source_url=https://game.example.com/", "serve_port=8123", "sign=yes"])
        self.assertEqual(rc, 0)
        s = ev[-1]["data"]["settings"]
        self.assertEqual((s["source_url"], s["serve_port"], s["sign"]), ("https://game.example.com/", 8123, True))
        rc, ev = run_json(["config", "get"])
        self.assertEqual(ev[-1]["data"]["settings"]["serve_port"], 8123)
        self.assertTrue(any(i["key"] == "trust_key" for i in ev[-1]["data"]["schema"]))
        rc, ev = run_json(["config", "set", "nonsense=1"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "no-such-setting"))
        run_json(["config", "set", "source_url=http://localhost:8000/", "serve_port=8000", "sign=no"])

    def test_passphrase_from_stdin_signs(self):
        if not cli.signing.can_sign():
            self.skipTest("needs cryptography")
        key = self.p("keys", "sign.pem")
        rc, ev = run_json(["sign", "keygen", "--path", key, "--passphrase-stdin"], "a long passphrase here\n")
        self.assertEqual(rc, 0, ev)
        pub = ev[-1]["data"]["public_key"]
        rc, ev = run_json(["sign", "status", "--path", key])
        self.assertTrue(ev[-1]["data"]["has_passphrase"])
        write(self.p("src", "a.txt"), b"a")
        rc, ev = run_json(["publish", self.p("src"), self.p("store"), "--sign", key, "--passphrase-stdin"],
                          "a long passphrase here\n")
        self.assertEqual(rc, 0, ev)
        self.assertTrue(ev[-1]["data"]["signed"])
        rc, ev = run_json(["update", self.p("inst"), "--source", self.p("store"), "--trust-key", pub])
        self.assertEqual(rc, 0, ev)
        rc, ev = run_json(["sign", "keygen", "--path", self.p("keys", "short.pem"), "--passphrase-stdin"], "short\n")
        self.assertEqual(ev[-1]["code"], "passphrase-short")

    def test_download_failure_is_a_failure_not_a_stop(self):
        write(self.p("src", "a.bin"), os.urandom(5000))
        write(self.p("src", "b.bin"), os.urandom(5000))
        cli.publish.publish(self.p("src"), self.p("store"))
        blobs = os.path.join(self.p("store"), "blobs")
        victim = sorted(os.listdir(blobs))[0]
        write(os.path.join(blobs, victim), b"tampered" * 625)     # same size, other bytes
        rc, ev = run_json(["update", self.p("inst"), "--source", self.p("store")])
        self.assertEqual((rc, ev[-1]["code"]), (1, "failed"))
        self.assertIn("SHA-256", ev[-1]["message"])

    def test_config_set_changes_only_its_keys(self):
        run_json(["config", "set", "name=Kept"])
        p = os.environ["DELTAPATCHER_CONFIG"]
        with open(p, "a", encoding="utf-8") as f:
            f.write("; a note of mine\n")
        rc, ev = run_json(["config", "set", "deploy_mode=Folder"])
        s = ev[-1]["data"]["settings"]
        self.assertEqual((s["name"], s["deploy_mode"]), ("Kept", "folder"))
        rc, ev = run_json(["config", "set", "deploy_mode=local"])
        self.assertEqual(ev[-1]["data"]["settings"]["deploy_mode"], "sftp")
        self.assertIn("; a note of mine", read(p).decode())
        run_json(["config", "set", "name=", "deploy_mode=sftp"])

    def test_host_key_states(self):
        kh = self.p("kh", "known_hosts")
        t = ssh.Target("example.com", known_hosts=kh)
        a = ("ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl")
        b = ("ssh-ed25519", "AAAAC3NzaC1lZDI1NTE5AAAAIJJx9VJfHFWwGhHjDvc2EHwM0VyuH6YDaJZ2T7c8XcJf")
        scanned = lambda k: [(k[0], k[1], ssh.fingerprint(k[1]))]
        self.assertEqual(ssh.host_key_state(t, scanned(a)), "unknown")
        ssh.trust(t, scanned(a))
        self.assertEqual(ssh.host_key_state(t, scanned(a)), "trusted")
        self.assertEqual(ssh.host_key_state(t, scanned(b)), "changed")

    def test_unexpected_errors_still_end_with_an_error_line(self):
        real = cli.cmd_version
        cli.HANDLERS["version"] = lambda a, cfg: 1 / 0
        try:
            rc, ev = run_json(["version"])
        finally:
            cli.HANDLERS["version"] = real
        self.assertEqual((rc, ev[-1]["event"], ev[-1]["code"]), (1, "error", "internal"))

    def test_trust_accept_refuses_other_fingerprints(self):
        real = ssh.scan_host
        ssh.scan_host = lambda t, timeout=15: [("ssh-ed25519", "AAAA", "SHA256:offered")]
        try:
            rc, ev = run_json(["ssh", "trust", "--host", "example.com", "--accept", "SHA256:something-else"])
        finally:
            ssh.scan_host = real
        self.assertEqual((rc, ev[-1]["code"]), (1, "host-changed"))


class ServeStopsWithStdin(TempCase):
    def test_closing_stdin_stops_the_server(self):
        write(self.p("src", "a.txt"), b"a")
        cli.publish.publish(self.p("src"), self.p("store"))
        env = dict(os.environ, PYTHONPATH=ROOT)
        p = subprocess.Popen([sys.executable, "-m", "deltapatcher", "--json", "--no-log", "serve", self.p("store"), "--port", "0"],
                             env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            serving = None
            for line in p.stdout:
                ev = json.loads(line)
                if ev.get("event") == "serving":
                    serving = ev["url"]
                    break
            self.assertTrue(serving and serving.startswith("http://"), serving)
            p.stdin.close()                     # what the app does to stop it, and what its exit does
            rc = p.wait(timeout=15)
            rest = [json.loads(l) for l in p.stdout.read().splitlines() if l.strip()]
            self.assertEqual(rc, 0)
            self.assertEqual(rest[-1]["event"], "result")
        finally:
            if p.poll() is None:
                p.kill()


class AskpassRelay(unittest.TestCase):
    def test_relay_hands_over_the_passphrase_once(self):
        token = secrets.token_hex(16)
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def serve():
            conn, _ = srv.accept()
            with conn:
                if conn.recv(64).decode().strip() == token:
                    conn.sendall(b"pass phrase \xc3\xa9\n")
            srv.close()

        threading.Thread(target=serve, daemon=True).start()
        env = dict(os.environ, DELTAPATCHER_ASKPASS="relay", DELTAPATCHER_ASKPASS_PORT=str(port),
                   DELTAPATCHER_ASKPASS_TOKEN=token, PYTHONPATH=ROOT)
        r = subprocess.run([sys.executable, "-m", "deltapatcher", "Enter passphrase for key:"], env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "pass phrase é\n".encode("utf-8"))

    def test_wrong_token_gets_nothing(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        def serve():
            conn, _ = srv.accept()
            with conn:
                conn.recv(64)       # a listener that does not know this token sends nothing
            srv.close()

        threading.Thread(target=serve, daemon=True).start()
        env = dict(os.environ, DELTAPATCHER_ASKPASS="relay", DELTAPATCHER_ASKPASS_PORT=str(port),
                   DELTAPATCHER_ASKPASS_TOKEN="wrong", PYTHONPATH=ROOT)
        r = subprocess.run([sys.executable, "-m", "deltapatcher", "x"], env=env, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=30)
        self.assertEqual((r.returncode, r.stdout), (1, b""))


if __name__ == "__main__":
    unittest.main()
