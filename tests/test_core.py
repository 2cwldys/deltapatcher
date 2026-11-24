import json
import os
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from deltapatcher import cli, client, publish, serve  # noqa: E402
from deltapatcher.manifest import Entry, Manifest, ManifestError, check_path  # noqa: E402
from helpers import TempCase, read, snapshot, write  # noqa: E402

PK3 = os.urandom(300 * 1024)
ZIP = os.urandom(120 * 1024)


class Paths(unittest.TestCase):
    def test_good(self):
        for p in ("a.txt", "data/pak0.pk3", "maps/de_dust2.bsp", "a b/c d.zip", "x/.hidden", "deep/" * 20 + "f"):
            self.assertEqual(check_path(p), p)

    def test_bad(self):
        for p in ("", "/etc/passwd", "../x", "a/../b", "a//b", "./a", "a\\b", "C:/x", "c:x", "a/", "con", "NUL.txt",
                  "lpt1.dat", "x/aux", "dot.", "space ", "tab\there", "q?", "star*", "~/x", "a/./b"):
            with self.assertRaises(ManifestError, msg=p):
                check_path(p)


class ManifestFormat(unittest.TestCase):
    sha = "a" * 64

    def test_roundtrip(self):
        m = Manifest("Game", "1.0", files=[Entry("b.pk3", self.sha, 5), Entry("a.zip", "b" * 64, 7)], protect=["cfg/*.cfg"])
        m2 = Manifest.from_json(m.to_json())
        self.assertEqual(sorted(m2.files), ["a.zip", "b.pk3"])
        self.assertEqual(m2.bytes, 12)
        self.assertEqual(m2.protect, ["cfg/*.cfg"])
        self.assertTrue(m2.is_protected("cfg/user.cfg"))
        self.assertFalse(m2.is_protected("other/user.cfg"))

    def test_refused(self):
        with self.assertRaises(ManifestError):
            Manifest(files=[Entry("A.txt", self.sha, 1), Entry("a.txt", self.sha, 1)])
        with self.assertRaises(ManifestError):
            Manifest(files=[Entry("a", self.sha, 1), Entry("a/b", self.sha, 1)])
        bad = {"format": 1, "files": [{"path": "../evil", "sha256": self.sha, "size": 1}]}
        with self.assertRaises(ManifestError):
            Manifest.from_json(json.dumps(bad))
        with self.assertRaises(ManifestError):
            Manifest.from_json(json.dumps({"format": 2, "files": []}))
        with self.assertRaises(ManifestError):
            Manifest.from_json(json.dumps({"format": 1, "files": [{"path": "a", "sha256": "XYZ", "size": 1}]}))
        with self.assertRaises(ManifestError):
            Manifest.from_json(b"not json")


class RoundTrip(TempCase):
    """publish -> serve over http -> update, then a second release, rollback and the refusals."""

    def setUp(self):
        TempCase.setUp(self)
        self.src, self.store, self.inst = self.p("src"), self.p("store"), self.p("install")
        write(self.p("src", "readme.txt"), "release one\n")
        write(self.p("src", "data", "pak0.pk3"), PK3)
        write(self.p("src", "data", "pak0_copy.pk3"), PK3)          # same bytes: one blob
        write(self.p("src", "maps", "pack.zip"), ZIP)
        write(self.p("src", "old.txt"), "dropped in release two\n")
        write(self.p("src", "config", "user.cfg"), "volume 1\n")
        write(self.p("src", ".git", "HEAD"), "ignored\n")
        self._publish("1.0")
        self.srv, _t = serve.serve_in_background(self.store, "127.0.0.1", 0)
        self.url = "http://127.0.0.1:%d/" % self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        TempCase.tearDown(self)

    def _publish(self, version):
        publish.publish(self.src, self.store, "Test Game", version, protect=["config/*.cfg"])

    def test_full_cycle(self):
        man = Manifest.from_json(read(os.path.join(self.store, "manifest.json")))
        self.assertNotIn(".git/HEAD", man.files)
        self.assertEqual(len(man.files), 6)
        self.assertEqual(len(man.blobs()), 5)                         # the two pk3s share a blob

        plan = client.update(self.url, self.inst)
        self.assertEqual(len(plan.add), 6)
        self.assertEqual(len(plan.download()), 5)
        released = {k: v for k, v in snapshot(self.src).items() if not k.startswith(".git/")}
        self.assertEqual(snapshot(self.inst), released)
        self.assertTrue(client.update(self.url, self.inst, check_only=True).empty())

        # the player edits things: a protected config, a file release two drops, a file of their own
        write(self.p("install", "config", "user.cfg"), "volume 9\n")
        write(self.p("install", "old.txt"), "my notes\n")
        write(self.p("install", "mine.txt"), "not the release's\n")
        # release two: readme changes, pack.zip goes, new.bin arrives, old.txt goes
        write(self.p("src", "readme.txt"), "release two\n")
        os.remove(self.p("src", "maps", "pack.zip"))
        os.remove(self.p("src", "old.txt"))
        new = os.urandom(50 * 1024)
        write(self.p("src", "new.bin"), new)
        self._publish("2.0")

        plan = client.update(self.url, self.inst)
        self.assertEqual(sorted(e.path for e in plan.add), ["new.bin"])
        self.assertEqual(sorted(e.path for e in plan.replace), ["readme.txt"])
        self.assertEqual(sorted(r[0] for r in plan.remove), ["maps/pack.zip"])
        self.assertEqual(plan.kept_changed, ["old.txt"])
        self.assertEqual(plan.protected, ["config/user.cfg"])
        self.assertEqual(len(plan.download()), 2)                     # only the two new contents travel
        self.assertEqual(read(self.p("install", "readme.txt")), b"release two\n")
        self.assertEqual(read(self.p("install", "new.bin")), new)
        self.assertFalse(os.path.exists(self.p("install", "maps", "pack.zip")))
        self.assertFalse(os.path.exists(self.p("install", "maps")))  # emptied folder removed
        self.assertEqual(read(self.p("install", "config", "user.cfg")), b"volume 9\n")
        self.assertEqual(read(self.p("install", "old.txt")), b"my notes\n")
        self.assertEqual(read(self.p("install", "mine.txt")), b"not the release's\n")

        client.rollback(self.inst)
        self.assertEqual(read(self.p("install", "readme.txt")), b"release one\n")
        self.assertEqual(read(self.p("install", "maps", "pack.zip")), ZIP)
        self.assertFalse(os.path.exists(self.p("install", "new.bin")))

    def test_damaged_blob_changes_nothing(self):
        client.update(self.url, self.inst)
        before = snapshot(self.inst)
        write(self.p("src", "readme.txt"), "release two\n")
        self._publish("2.0")
        man = Manifest.from_json(read(os.path.join(self.store, "manifest.json")))
        sha = man.files["readme.txt"].sha256
        write(os.path.join(self.store, "blobs", sha), b"release tw0\n")   # same size, other bytes
        with self.assertRaises(client.UpdateError):
            client.update(self.url, self.inst)
        self.assertEqual(snapshot(self.inst), before)

    def test_missing_blob_changes_nothing(self):
        client.update(self.url, self.inst)
        before = snapshot(self.inst)
        write(self.p("src", "readme.txt"), "release two\n")
        self._publish("2.0")
        man = Manifest.from_json(read(os.path.join(self.store, "manifest.json")))
        os.remove(os.path.join(self.store, "blobs", man.files["readme.txt"].sha256))
        with self.assertRaises(Exception) as cm:
            client.update(self.url, self.inst)
        self.assertIn("404", str(cm.exception))
        self.assertEqual(snapshot(self.inst), before)

    def test_malicious_manifest_refused(self):
        evil = {"format": 1, "name": "x", "files": [{"path": "../outside.txt", "sha256": "c" * 64, "size": 3}]}
        write(os.path.join(self.store, "manifest.json"), json.dumps(evil))
        with self.assertRaises(client.UpdateError):
            client.update(self.url, self.inst)
        self.assertFalse(os.path.exists(self.p("outside.txt")))

    def test_resume_partial_download(self):
        man = Manifest.from_json(read(os.path.join(self.store, "manifest.json")))
        sha = man.files["data/pak0.pk3"].sha256
        part = self.p("install", ".deltapatcher", "cache", sha + ".part")
        write(part, PK3[:100000])
        client.update(self.url, self.inst)
        self.assertEqual(read(self.p("install", "data", "pak0.pk3")), PK3)

    def test_folder_source(self):
        client.update(self.store, self.inst)
        self.assertEqual(read(self.p("install", "data", "pak0.pk3")), PK3)

    def test_server_serves_only_the_store(self):
        base = self.url
        r = urllib.request.urlopen(urllib.request.Request(base + "blobs/" + Manifest.from_json(
            read(os.path.join(self.store, "manifest.json"))).files["data/pak0.pk3"].sha256, headers={"Range": "bytes=10-19"}))
        self.assertEqual(r.status, 206)
        self.assertEqual(r.read(), PK3[10:20])
        for bad in ("", "blobs/", "../manifest.json", ".deltapatcher-hashcache.json", "blobs/abc"):
            with self.assertRaises(urllib.error.HTTPError, msg=bad):
                urllib.request.urlopen(base + bad)


class Cli(TempCase):
    def test_publish_update_check(self):
        write(self.p("src", "a.pk3"), PK3)
        self.assertEqual(cli.main(["publish", self.p("src"), self.p("store"), "--name", "x", "--version", "1"]), 0)
        self.assertEqual(cli.main(["update", self.p("inst"), "--source", self.p("store")]), 0)
        self.assertEqual(cli.main(["update", self.p("inst"), "--source", self.p("store"), "--check"]), 0)
        write(self.p("src", "a.pk3"), b"changed")
        cli.main(["publish", self.p("src"), self.p("store")])
        self.assertEqual(cli.main(["update", self.p("inst"), "--source", self.p("store"), "--check"]), 2)
        self.assertEqual(cli.main(["update", self.p("nowhere"), "--source", self.p("missing-store")]), 1)


if __name__ == "__main__":
    unittest.main()
