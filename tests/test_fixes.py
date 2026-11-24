"""One test per problem the first review found, so none of them comes back."""

import http.server
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from deltapatcher import client, deploy, publish, serve, ssh, source  # noqa: E402
from deltapatcher.manifest import Manifest, ManifestError, check_path  # noqa: E402
from helpers import TempCase, read, write  # noqa: E402
from test_deploy import REMOTE, FakeSftp  # noqa: E402

WINDOWS = os.name == "nt"


def case_insensitive(folder):
    probe = os.path.join(folder, "CaseProbe")
    write(probe, "x")
    try:
        return os.path.exists(os.path.join(folder, "caseprobe"))
    finally:
        os.remove(probe)


class Client(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        self.src, self.store, self.inst = self.p("src"), self.p("store"), self.p("inst")

    def pub(self, version="1"):
        publish.publish(self.src, self.store, "G", version)

    def test_case_only_rename_keeps_the_file(self):
        write(self.p("src", "Maps", "x.bsp"), b"map bytes")
        self.pub("1")
        client.update(self.store, self.inst)
        if not case_insensitive(self.inst):
            self.skipTest("this file system tells Maps from maps")
        os.rename(self.p("src", "Maps"), self.p("src", "maps"))
        self.pub("2")
        plan = client.update(self.store, self.inst)
        self.assertEqual(plan.remove, [])
        self.assertEqual(read(self.p("inst", "maps", "x.bsp")), b"map bytes")
        self.assertTrue(client.update(self.store, self.inst, check_only=True).empty())

    def test_read_only_files_update_and_roll_back(self):
        write(self.p("src", "game.dll"), b"v1")
        self.pub("1")
        client.update(self.store, self.inst)
        os.chmod(self.p("inst", "game.dll"), stat.S_IREAD)
        for v in ("v2", "v3"):
            write(self.p("src", "game.dll"), v.encode())
            self.pub(v)
            client.update(self.store, self.inst)
            self.assertEqual(read(self.p("inst", "game.dll")), v.encode())
        client.rollback(self.inst)
        self.assertEqual(read(self.p("inst", "game.dll")), b"v2")

    def test_stale_lock_file_does_not_block(self):
        write(self.p("src", "a.txt"), b"a")
        self.pub()
        write(self.p("inst", ".deltapatcher", "lock"), b"12345")     # left by a process that died
        client.update(self.store, self.inst)
        self.assertEqual(read(self.p("inst", "a.txt")), b"a")

    def test_lock_held_refuses(self):
        write(self.p("src", "a.txt"), b"a")
        self.pub()
        with client.Lock(os.path.join(self.inst, ".deltapatcher")):
            with self.assertRaises(client.UpdateError):
                client.update(self.store, self.inst)

    @unittest.skipUnless(WINDOWS, "an open file blocks a rename on Windows only")
    def test_failed_update_keeps_the_last_rollback(self):
        write(self.p("src", "a.txt"), b"one")
        write(self.p("src", "b.txt"), b"one")
        self.pub("1")
        client.update(self.store, self.inst)
        write(self.p("src", "a.txt"), b"two")
        self.pub("2")
        client.update(self.store, self.inst)
        write(self.p("src", "a.txt"), b"three")
        write(self.p("src", "b.txt"), b"three")
        self.pub("3")
        with open(self.p("inst", "b.txt"), "rb"):        # the game holds it open
            with self.assertRaises(OSError):
                client.update(self.store, self.inst)
        self.assertEqual(read(self.p("inst", "a.txt")), b"two")     # the failed update was undone
        client.rollback(self.inst)                                    # and update 2 can still be rolled back
        self.assertEqual(read(self.p("inst", "a.txt")), b"one")

    def test_files_that_were_already_there_are_not_removed(self):
        write(self.p("src", "stock.pak"), b"stock")
        write(self.p("src", "mod.pak"), b"mod")
        self.pub("1")
        write(self.p("inst", "stock.pak"), b"stock")      # the player's game had it before
        client.update(self.store, self.inst)
        os.remove(self.p("src", "stock.pak"))
        os.remove(self.p("src", "mod.pak"))
        write(self.p("src", "other.pak"), b"x")
        self.pub("2")
        client.update(self.store, self.inst)
        self.assertTrue(os.path.exists(self.p("inst", "stock.pak")))
        self.assertFalse(os.path.exists(self.p("inst", "mod.pak")))

    def test_check_only_changes_nothing(self):
        write(self.p("src", "a.txt"), b"a")
        self.pub()
        client.update(self.store, self.inst, check_only=True)
        self.assertFalse(os.path.exists(self.p("inst", ".deltapatcher", "state.json")))
        self.assertFalse(os.path.exists(self.p("inst", "a.txt")))

    def test_journal_reading(self):
        j = self.p("j.jsonl")
        write(j, '{"started": 1, "had_state": true}\n{"op": "add", "path": "done"}\n{"op": "add", "path": "x"}\n{"op": "rem')
        header, ops, done = client._read_journal(j)
        self.assertFalse(done)
        self.assertEqual([o["path"] for o in ops], ["done", "x"])
        self.assertTrue(header["had_state"])

    def test_a_file_that_appears_during_the_update(self):
        write(self.p("src", "config", "user.cfg"), b"default")
        self.pub()
        publish.publish(self.src, self.store, "G", "1", protect=["config/*.cfg"])
        man = Manifest.from_json(read(os.path.join(self.store, "manifest.json")))
        state = client.State(self.inst)
        os.makedirs(self.p("inst", ".deltapatcher"), exist_ok=True)
        from deltapatcher import hashing
        plan = client.make_plan(man, self.inst, state, hashing.HashCache(self.p("hc.json")))
        self.assertEqual([e.path for e in plan.add], ["config/user.cfg"])
        write(self.p("inst", "config", "user.cfg"), b"made by the game meanwhile")
        real = client.make_plan
        client.make_plan = lambda *a: plan        # the plan from before the file appeared
        try:
            client.update(self.store, self.inst)
        finally:
            client.make_plan = real
        self.assertEqual(read(self.p("inst", "config", "user.cfg")), b"made by the game meanwhile")


class Paths(unittest.TestCase):
    def test_more_device_names_and_own_folder(self):
        for p in ("con .txt", "nul .txt", "aux  .log", "COM\u00b9", "COM\u00b9.txt", "lpt\u00b2.dat",
                  ".deltapatcher/rollback/journal.jsonl", ".DeltaPatcher/x", "DELTAP~1/x"):
            with self.assertRaises(ManifestError, msg=p):
                check_path(p)
        for p in ("console.txt", "com10.txt", "deltapatcher/x", "x/.deltapatcher"):
            self.assertEqual(check_path(p), p)


class Publish(TempCase):
    @unittest.skipUnless(WINDOWS, "junctions are a Windows thing")
    def test_junction_not_followed(self):
        write(self.p("outside", "private.txt"), b"secret")
        write(self.p("src", "a.txt"), b"a")
        r = subprocess.run(["cmd", "/c", "mklink", "/J", self.p("src", "linked"), self.p("outside")],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode != 0:
            self.skipTest("mklink /J failed")
        skipped = []
        self.assertEqual(publish.walk(self.p("src"), skipped=skipped), ["a.txt"])
        self.assertEqual(skipped[0][0], "linked")

    def test_same_release_again_keeps_the_manifest(self):
        write(self.p("src", "a.txt"), b"one")
        publish.publish(self.p("src"), self.p("store"), "G", "1")
        write(self.p("src", "a.txt"), b"two")
        publish.publish(self.p("src"), self.p("store"), "G", "2")
        cur = read(self.p("store", "manifest.json"))
        prev = read(self.p("store", "manifest.previous.json"))
        publish.publish(self.p("src"), self.p("store"), "G", "2")
        self.assertEqual(read(self.p("store", "manifest.json")), cur)
        self.assertEqual(read(self.p("store", "manifest.previous.json")), prev)


class Deploys(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        write(self.p("src", "a.pk3"), os.urandom(20000))
        publish.publish(self.p("src"), self.p("store"), "G", "1")

    def two(self):
        write(self.p("src", "a.pk3"), os.urandom(20000))
        publish.publish(self.p("src"), self.p("store"), "G", "2")

    def test_folder_keeps_other_files(self):
        write(self.p("web", "blobs", "web.config"), b"<configuration/>")
        deploy.deploy(self.p("store"), deploy.FolderTarget(self.p("web")))
        self.two()
        deploy.deploy(self.p("store"), deploy.FolderTarget(self.p("web")))
        self.assertTrue(os.path.exists(self.p("web", "blobs", "web.config")))

    def test_unreadable_target_manifest_removes_nothing(self):
        tgt = deploy.FolderTarget(self.p("web"))
        deploy.deploy(self.p("store"), tgt)
        before = set(os.listdir(self.p("web", "blobs")))
        write(self.p("web", "manifest.json"), b"{ broken")
        self.two()
        r = deploy.deploy(self.p("store"), tgt)
        self.assertEqual(r.removed, 0)
        self.assertTrue(before <= set(os.listdir(self.p("web", "blobs"))))

    def test_redeploy_keeps_the_previous_release(self):
        tgt = deploy.FolderTarget(self.p("web"))
        deploy.deploy(self.p("store"), tgt)
        v1 = set(Manifest.from_json(read(self.p("store", "manifest.json"))).blobs())
        self.two()
        deploy.deploy(self.p("store"), tgt)
        publish.publish(self.p("src"), self.p("store"), "G", "2")     # nothing changed
        deploy.deploy(self.p("store"), tgt)
        self.assertTrue(v1 <= set(os.listdir(self.p("web", "blobs"))))
        self.assertEqual(Manifest.from_json(read(self.p("web", "manifest.previous.json"))).version, "1")

    def _fake(self, **kw):
        os.makedirs(self.p("server"), exist_ok=True)
        fake = FakeSftp(self.p("server"), **kw)
        self.real, ssh.sftp = ssh.sftp, fake
        self.addCleanup(lambda: setattr(ssh, "sftp", self.real))
        return deploy.SftpTarget(ssh.Target("example.com", "deploy", 22, "", REMOTE))

    def test_failed_manifest_upload_keeps_the_live_one(self):
        tgt = self._fake(posix_rename=False)
        deploy.deploy(self.p("store"), tgt)
        live = read(self.p("server", "manifest.json"))
        self.two()
        orig = FakeSftp.do

        def full_disk(fake, argv, cwd, out):
            if argv[0] == "put" and "manifest" in argv[1]:
                raise OSError("Couldn't write to remote file: Failure (disk full)")
            return orig(fake, argv, cwd, out)

        FakeSftp.do = full_disk
        try:
            with self.assertRaises(deploy.DeployError):
                deploy.deploy(self.p("store"), tgt)
        finally:
            FakeSftp.do = orig
        self.assertEqual(read(self.p("server", "manifest.json")), live)

    def test_wrong_size_blob_repaired_without_posix_rename(self):
        tgt = self._fake(posix_rename=False)
        deploy.deploy(self.p("store"), tgt)
        blob = [f for f in os.listdir(self.p("server", "blobs"))][0]
        write(self.p("server", "blobs", blob), b"truncated")
        r = deploy.deploy(self.p("store"), tgt)
        self.assertEqual(r.sent, 1)
        self.assertEqual(read(self.p("server", "blobs", blob)), read(self.p("store", "blobs", blob)))


class _Flaky(http.server.BaseHTTPRequestHandler):
    """Serves a store, but the first answer for each blob stops halfway (a clean close)."""
    root = None
    cut = set()
    ranges = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        name = self.path.lstrip("/")
        full = os.path.join(self.root, *name.split("/"))
        if not os.path.isfile(full):
            self.send_error(404)
            return
        data = read(full)
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            self.ranges.append(start)
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, len(data) - 1, len(data)))
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(data) - start))
        self.end_headers()
        if name.startswith("blobs/") and name not in self.cut:
            self.cut.add(name)
            self.wfile.write(data[start:start + (len(data) - start) // 2])
            self.close_connection = True
            return
        self.wfile.write(data[start:])


class Network(TempCase):
    def test_cut_download_resumes(self):
        payload = os.urandom(200000)
        write(self.p("src", "big.pk3"), payload)
        publish.publish(self.p("src"), self.p("store"), "G", "1")
        _Flaky.root, _Flaky.cut, _Flaky.ranges = self.p("store"), set(), []
        srv = http.server.HTTPServer(("127.0.0.1", 0), _Flaky)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            client.update("http://127.0.0.1:%d/" % srv.server_address[1], self.p("inst"))
        finally:
            srv.shutdown()
            srv.server_close()
        # the file arrives whole. (How many range requests it took depends on the stand-in server: on
        # Windows its close sometimes resets the connection; test_resume_from_where_it_stopped pins the resume.)
        self.assertEqual(read(self.p("inst", "big.pk3")), payload)

    def test_resume_from_where_it_stopped(self):
        payload = os.urandom(200000)
        calls = []

        class Resp(object):
            def __init__(self, data, status):
                self.data, self.status, self.pos = data, status, 0

            def read(self, n):
                b = self.data[self.pos:self.pos + n]
                self.pos += len(b)
                return b

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_open(rel, start=0):
            calls.append(start)
            if len(calls) == 1:
                return Resp(payload[:100000], 200)        # the connection ends half way, cleanly
            return Resp(payload[start:], 206)

        src = source.Source("http://example.invalid/")
        src._open = fake_open
        import hashlib
        dest = self.p("blob")
        src.fetch_blob(hashlib.sha256(payload).hexdigest(), dest, len(payload))
        self.assertEqual(read(dest), payload)
        self.assertEqual(calls, [0, 100000])

    def test_redirect_to_another_host_drops_extra_headers(self):
        seen = {}

        class B(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.update({k.lower(): v for k, v in self.headers.items()})
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        b = http.server.HTTPServer(("127.0.0.1", 0), B)
        threading.Thread(target=b.serve_forever, daemon=True).start()
        target = "http://localhost:%d/manifest.json" % b.server_address[1]

        class A(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()

        a = http.server.HTTPServer(("127.0.0.1", 0), A)
        threading.Thread(target=a.serve_forever, daemon=True).start()
        try:
            src = source.Source("http://127.0.0.1:%d/" % a.server_address[1], headers={"Authorization": "Bearer secret"})
            self.assertEqual(src.get_manifest(), b"{}")
        finally:
            for s in (a, b):
                s.shutdown()
                s.server_close()
        self.assertNotIn("authorization", seen)
        self.assertIn("user-agent", seen)

    def test_localhost_skips_the_ipv6_wait(self):
        # the server listens on 127.0.0.1; trying ::1 first cost two seconds a request on Windows
        write(self.p("src", "a.bin"), os.urandom(100))
        publish.publish(self.p("src"), self.p("store"), "G", "1")
        srv, _t = serve.serve_in_background(self.p("store"), "127.0.0.1", 0)
        try:
            src = source.Source("http://localhost:%d/" % srv.server_address[1])
            started = time.time()
            for _ in range(3):
                self.assertTrue(src.get_manifest())
            self.assertLess(time.time() - started, 3.0)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_backwards_range_is_refused(self):
        write(self.p("src", "a.bin"), os.urandom(100))
        publish.publish(self.p("src"), self.p("store"), "G", "1")
        sha = list(Manifest.from_json(read(self.p("store", "manifest.json"))).blobs())[0]
        srv, _t = serve.serve_in_background(self.p("store"), "127.0.0.1", 0)
        try:
            req = urllib.request.Request("http://127.0.0.1:%d/blobs/%s" % (srv.server_address[1], sha),
                                         headers={"Range": "bytes=10-5"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req)
            self.assertEqual(cm.exception.code, 416)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
