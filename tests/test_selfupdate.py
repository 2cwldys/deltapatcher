"""DeltaPatcher's own updates, against a stand-in for GitHub's releases API on this machine."""

import hashlib
import http.server
import io
import json
import os
import sys
import tarfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import REPO, __version__, cli, selfupdate  # noqa: E402


class FakeGitHub(object):
    """GET /repos/<REPO>/releases/latest, and the files it names; counts the API calls."""

    def __init__(self):
        self.files = {}
        self.release = None
        self.api_calls = 0
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/repos/%s/releases/latest" % REPO:
                    outer.api_calls += 1
                    if outer.release is None:
                        self.send_error(404)
                        return
                    body = json.dumps(outer.release).encode()
                elif self.path in outer.files:
                    body = outer.files[self.path]
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def publish(self, version, files):
        assets = []
        for name, data in files.items():
            self.files["/dl/" + name] = data
            assets.append({"name": name, "browser_download_url": self.base + "/dl/" + name, "size": len(data),
                           "digest": "sha256:" + hashlib.sha256(data).hexdigest()})
        self.release = {"tag_name": "v" + version, "html_url": "https://github.com/%s/releases/v%s" % (REPO, version),
                        "body": "What is new", "assets": assets}

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def cli_tar(data):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        info = tarfile.TarInfo("deltapatcher-cli")
        info.size = len(data)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


class SelfUpdate(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        self.gh = FakeGitHub()
        self.addCleanup(self.gh.close)
        patcher = mock.patch.object(selfupdate, "API", self.gh.base)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_versions_are_compared_by_number(self):
        self.assertTrue(selfupdate.is_newer("0.10.0", "0.9.9"))
        self.assertTrue(selfupdate.is_newer("v1.0.0", "0.99.0"))
        self.assertFalse(selfupdate.is_newer("v0.2.0", "0.2.0"))
        self.assertFalse(selfupdate.is_newer("0.1.9", "0.2.0"))
        self.assertFalse(selfupdate.is_newer("", "0.2.0"))
        self.assertFalse(selfupdate.is_newer("nightly", "0.2.0"))

    def test_the_file_for_each_system(self):
        names = ["DeltaPatcher-0.3.0-setup.exe", "DeltaPatcher-0.3.0-windows-x64.zip", "DeltaPatcher-0.3.0.AppImage",
                 "DeltaPatcher-0.3.0-arm64.dmg", "DeltaPatcher-0.3.0-arm64-mac.zip", "deltapatcher-cli-windows-x64.exe",
                 "deltapatcher-cli-linux-x64.tar.gz", "deltapatcher-cli-macos-arm64.tar.gz"]
        assets = [{"name": n} for n in names]
        cases = {("windows", "x64"): {"cli": "deltapatcher-cli-windows-x64.exe", "setup": "DeltaPatcher-0.3.0-setup.exe",
                                      "zip": "DeltaPatcher-0.3.0-windows-x64.zip"},
                 ("linux", "x64"): {"cli": "deltapatcher-cli-linux-x64.tar.gz", "appimage": "DeltaPatcher-0.3.0.AppImage"},
                 ("macos", "arm64"): {"cli": "deltapatcher-cli-macos-arm64.tar.gz", "dmg": "DeltaPatcher-0.3.0-arm64.dmg"}}
        for sysarch, kinds in cases.items():
            with mock.patch.object(selfupdate, "system", return_value=sysarch):
                for kind, name in kinds.items():
                    self.assertEqual(selfupdate.pick(assets, kind)["name"], name, (sysarch, kind))
        with mock.patch.object(selfupdate, "system", return_value=("linux", "arm64")):
            self.assertIsNone(selfupdate.pick(assets, "cli"))                  # none built for it

    def test_latest_and_a_checked_download(self):
        self.assertIsNone(selfupdate.latest())                                 # no release yet
        self.gh.publish("9.9.9", {"deltapatcher-cli-windows-x64.exe": b"new program"})
        found = selfupdate.latest()
        self.assertEqual((found["version"], found["tag"]), ("9.9.9", "v9.9.9"))
        asset = found["assets"][0]
        self.assertEqual(asset["sha256"], hashlib.sha256(b"new program").hexdigest())
        path = selfupdate.download(asset, folder=self.p("dl"))
        self.assertEqual(read(path), b"new program")

        bad = dict(asset, sha256="0" * 64)                                     # changed on the way: not used
        with self.assertRaises(selfupdate.UpdateCheckError):
            selfupdate.download(bad, folder=self.p("dl2"))
        self.assertEqual(os.listdir(self.p("dl2")), [])
        short = dict(asset, size=asset["size"] + 1)
        with self.assertRaises(selfupdate.UpdateCheckError):
            selfupdate.download(short, folder=self.p("dl3"))

    def test_the_running_program_is_replaced(self):
        program = self.p("bin", "deltapatcher-cli.exe" if os.name == "nt" else "deltapatcher-cli")
        write(program, b"old program")
        write(self.p("dl", "new"), b"new program")
        selfupdate.replace_running_program(self.p("dl", "new"), program)
        self.assertEqual(read(program), b"new program")
        if os.name == "nt":
            self.assertEqual(read(program + ".old"), b"old program")           # Windows: renamed, removed later
            selfupdate.remove_leftover(program)
        self.assertFalse(os.path.exists(program + ".old"))
        write(self.p("dl", "deltapatcher-cli-linux-x64.tar.gz"), cli_tar(b"from the archive"))
        self.assertEqual(read(selfupdate.cli_from(self.p("dl", "deltapatcher-cli-linux-x64.tar.gz"))), b"from the archive")

    def test_the_daily_check_asks_once(self):
        self.gh.publish("9.9.9", {})
        self.assertEqual(selfupdate.cached_check(self.p("cfg")), "9.9.9")
        self.assertEqual(selfupdate.cached_check(self.p("cfg")), "9.9.9")
        self.assertEqual(self.gh.api_calls, 1)
        self.assertEqual(selfupdate.cached_check(self.p("cfg2"), enabled=False), "")
        self.assertEqual(self.gh.api_calls, 1)

    def run_json(self, argv):
        out = io.StringIO()
        real = sys.stdout
        sys.stdout = out
        try:
            rc = cli.main(["--json", "--no-log"] + argv)
        finally:
            sys.stdout = real
        return rc, [json.loads(x) for x in out.getvalue().splitlines() if x.strip()][-1]

    def test_the_command(self):
        rc, last = self.run_json(["self-update", "--check"])
        self.assertEqual((rc, last["data"]["newer"]), (0, False))              # nothing published
        self.gh.publish(__version__, {})
        rc, last = self.run_json(["self-update", "--check"])
        self.assertEqual((rc, last["data"]["newer"]), (0, False))              # the same version
        self.gh.publish("9.9.9", {})
        rc, last = self.run_json(["self-update", "--check"])
        self.assertEqual((rc, last["data"]["newer"], last["data"]["latest"]), (2, True, "9.9.9"))
        rc, last = self.run_json(["self-update"])                              # from source: not replaced
        self.assertEqual((rc, last["code"]), (1, "from-source"))
        rc, last = self.run_json(["self-update", "--app", "dmg"])              # no such file in the release
        self.assertEqual((rc, last["code"]), (1, "no-asset"))


if __name__ == "__main__":
    unittest.main()
