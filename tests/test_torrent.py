"""Torrents beside HTTP, the parts that need no libtorrent: the settings and their checks, refusing torrents, the
release item's signed bytes and its numbers, the torrent made here, project files version 3, the publisher's torrent
in a release, which files of a folder a seed offers, the pause handshake between an update and a seed run, and what
the store, deploy, serve and backups do with release.torrent and release.item."""

import base64
import contextlib
import hashlib
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, snapshot, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import (backup, cli, client, config, deploy, hashing, presets, projects, publish,  # noqa: E402
                          publisher, serve, signing, torrent)
from deltapatcher.manifest import Manifest  # noqa: E402
from deltapatcher.source import Source, SourceError  # noqa: E402
from deltapatcher.torrent import bencode, folder, item, krpc, make, pause  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TID = "0123456789abcdef0123456789abcdef"


def run_json(argv, stdin=""):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(sys, "stdin", io.StringIO(stdin)):
        rc = cli.main(["--json", "--no-log"] + argv)
    return rc, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def needs_signing(case):
    if not signing.can_sign():
        if os.environ.get("DP_REQUIRE_TORRENT") == "1":            # CI: a torrent test never skips there
            case.fail("DP_REQUIRE_TORRENT=1, and cryptography is not installed")
        case.skipTest("needs cryptography")


class Base(TempCase):
    def key(self):
        needs_signing(self)
        path = self.p("keys", "sign.pem")
        if not os.path.exists(path):
            signing.generate_key(path, "")
        return path, signing.load_key(path)

    def publisher(self, name="Mod", **values):
        """A publisher's project shared by torrent, signed, releasing src/ into store/."""
        path, _key = self.key()
        p = projects.new(name)
        write(self.p("src", "readme.txt"), "one\n")
        write(self.p("src", "data", "pak0.bin"), os.urandom(70000))
        base = {"publish_src": self.p("src"), "store_dir": self.p("store"), "sign": True, "signing_key": path,
                "public_url": "https://game.example.com/mod/"}
        base.update(values)
        config.save(base, p)
        rc, ev = run_json(["--project", name, "config", "set", "torrent=yes", "seeds=203.0.113.10:6881"])
        self.assertEqual(rc, 0, ev[-1])
        return p


class Settings(Base):
    def test_defaults_and_choices(self):
        cfg = config.load()
        self.assertEqual((cfg["torrent"], cfg["torrent_id"], cfg["torrent_name"], cfg["seeds"], cfg["seed"]),
                         (False, "", "", "", False))
        self.assertEqual((cfg["torrents"], cfg["torrent_port"], cfg["seed_upload_kbps"], cfg["torrent_port_mapping"]),
                         ("allow", 6881, 0, False))
        self.assertEqual(config._convert("torrents", "REFUSE"), "refuse")
        self.assertEqual(config._convert("torrents", "maybe"), "refuse")              # fails closed
        self.assertIsNone(torrent.info(cfg))
        for s, key, _d, what in config.SCHEMA:
            if s in ("torrent",) or key in ("torrents", "torrent_port", "seed_upload_kbps", "torrent_port_mapping"):
                self.assertFalse(any(f in key for f in config.FORBIDDEN), key)
                self.assertTrue(all(32 <= ord(c) < 127 for c in what), key)

    def test_turning_torrents_on_needs_signing_and_makes_the_id_once(self):
        p = projects.new("Mod")
        rc, ev = run_json(["--project", "Mod", "config", "set", "torrent=yes"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "sign-needed"))
        rc, ev = run_json(["--project", "Mod", "config", "set", "sign=yes", "torrent=yes", "seeds=a.example.com:6881; "
                           "203.0.113.10:6881 a.example.com:6881"])
        self.assertEqual(rc, 0, ev[-1])
        cfg = config.load(p)
        self.assertRegex(cfg["torrent_id"], r"^[0-9a-f]{32}$")
        self.assertEqual(cfg["seeds"], "a.example.com:6881, 203.0.113.10:6881")
        made = cfg["torrent_id"]
        rc, ev = run_json(["--project", "Mod", "config", "set", "torrent=no"])
        rc, ev = run_json(["--project", "Mod", "config", "set", "torrent=yes"])
        self.assertEqual(config.load(p)["torrent_id"], made)                       # never changes
        rc, ev = run_json(["--project", "Mod", "config", "set", "torrent_id=" + "f" * 32])
        self.assertEqual((rc, ev[-1]["code"]), (1, "bad-setting"))
        for bad in ("seeds=nohost", "seeds=a.example.com:0", "seeds=a b:1", "torrent_name=../x", "torrent_name=con",
                    "torrent_port=70000", "seed_upload_kbps=-1"):
            rc, ev = run_json(["--project", "Mod", "config", "set", bad])
            self.assertEqual((rc, ev[-1]["code"]), (1, "bad-setting"), bad)

    def test_seed_needs_a_project_shared_by_torrent(self):
        projects.new("Plain")
        rc, ev = run_json(["--project", "Plain", "config", "set", "seed=yes"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "not-shared"))

    def test_names_seeds_and_web_seeds(self):
        self.assertEqual(torrent.default_name("https://game.example.com/files/mymod/"), "mymod")
        self.assertEqual(torrent.default_name("https://game.example.com/"), "release")
        self.assertEqual(torrent.default_name("https://game.example.com/a%20b/"), "release")
        self.assertEqual(torrent.web_seed("https://game.example.com/files/mymod/", "mymod"), "https://game.example.com/files/")
        self.assertEqual(torrent.web_seed("https://game.example.com/mymod", "mymod"), "https://game.example.com/")
        self.assertEqual(torrent.web_seed("https://game.example.com/", "release"), "")
        self.assertEqual(torrent.web_seed("D:/store/mymod", "mymod"), "")
        self.assertEqual(torrent.seeds_of("[2001:db8::1]:6881, x.example.com:1"), ["[2001:db8::1]:6881", "x.example.com:1"])
        self.assertEqual(torrent.object_problem({"id": TID, "name": "mod", "seeds": ["a.example.com:1"]}), "")
        self.assertNotEqual(torrent.object_problem({"id": TID, "name": "mod", "seeds": ["a:1", "a:1"]}), "")
        self.assertNotEqual(torrent.object_problem({"id": "x", "name": "mod", "seeds": []}), "")
        self.assertNotEqual(torrent.object_problem({"id": TID, "name": "mod", "seeds": [], "tracker": "x"}), "")

    def test_a_project_with_no_address_updates_by_torrent_only(self):
        cfg = dict(config.DEFAULTS, torrent=True, torrent_id=TID)
        self.assertTrue(torrent.torrent_only(cfg))
        self.assertFalse(torrent.torrent_only(dict(cfg, source_url="https://game.example.com/")))
        self.assertFalse(torrent.torrent_only(dict(cfg, public_url="https://game.example.com/")))
        self.assertFalse(torrent.torrent_only(dict(config.DEFAULTS)))

    def test_no_passphrase_anywhere(self):
        seed = cli.build_parser()._subparsers._group_actions[0].choices["seed"]
        self.assertNotIn("passphrase", seed.format_help().lower())
        self.assertNotIn("password", seed.format_help().lower())
        self.assertEqual(sorted(o for a in seed._actions for o in a.option_strings), ["--all", "--help", "--project", "-h"])


class Refused(Base):
    """general.torrents = refuse: no libtorrent session is ever made, whatever a project says."""

    def setUp(self):
        Base.setUp(self)
        config.save({"torrents": "refuse"})
        self.made = []
        from deltapatcher.torrent import session
        patcher = mock.patch.object(session, "make_session", side_effect=lambda s: self.made.append(s))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_update_with_an_address_uses_it_alone(self):
        write(self.p("store-src", "a.txt"), "a\n")
        publish.publish(self.p("store-src"), self.p("store"))
        p = projects.new("Shared")
        config.save({"source_url": self.p("store"), "install_dir": self.p("game"), "torrent": True,
                     "torrent_id": TID, "seeds": "127.0.0.1:9"}, p)
        rc, ev = run_json(["--project", "Shared", "update"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(read(self.p("game", "a.txt")), b"a\n")
        self.assertEqual(ev[-1]["data"]["torrent"], {"used": False, "only": False, "refused": True})
        self.assertEqual(self.made, [])

    def test_a_project_with_no_address_says_so(self):
        p = projects.new("Only")
        config.save({"install_dir": self.p("game"), "torrent": True, "torrent_id": TID,
                     "trust_key": "ed25519:" + "A" * 43 + "="}, p)
        rc, ev = run_json(["--project", "Only", "update"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "torrents-refused"))
        self.assertEqual(ev[-1]["message"], "This project updates only by torrent, and torrents are refused in your "
                                            "settings (Settings, Torrents).")
        self.assertEqual(self.made, [])

    def test_seed_ends_at_once(self):
        p = projects.new("Only")
        config.save({"torrent": True, "torrent_id": TID, "seed": True}, p)
        t0 = time.time()
        rc, ev = run_json(["seed", "--all"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "torrents-refused"))
        self.assertIn("refused", ev[-1]["message"])
        self.assertLess(time.time() - t0, 5)
        self.assertEqual(self.made, [])
        rc, ev = run_json(["version", "--torrent"])                                 # not even to test
        self.assertEqual((rc, ev[-1]["code"]), (1, "torrents-refused"))
        self.assertEqual(self.made, [])


class Yes(Base):
    def test_never_seeding_without_the_persons_yes(self):
        p = projects.new("Shared")
        config.save({"torrent": True, "torrent_id": TID, "install_dir": self.p("game")}, p)      # seed = no
        from deltapatcher.torrent import seed
        with mock.patch.object(torrent, "available", return_value=True), \
                mock.patch.object(seed, "Run", side_effect=AssertionError("a seed run was started")):
            rc, ev = run_json(["seed", "--all"])
            self.assertEqual((rc, ev[-1]["code"]), (1, "nothing-to-seed"))
            rc, ev = run_json(["--project", "Default", "seed"])
            self.assertEqual((rc, ev[-1]["code"]), (1, "not-shared"))


class Items(Base):
    def test_the_signed_bytes_and_a_roundtrip(self):
        _path, key = self.key()
        pub = signing.parse_key(signing.public_key_text(key))
        v = item.value(b"\x01" * 32, "ab" * 32)
        self.assertEqual(bencode.encode(v), b"d2:ih32:" + b"\x01" * 32 + b"1:m64:" + b"ab" * 32 + b"e")
        self.assertEqual(item.signed_part(TID.encode(), 7, b"x"), b"4:salt32:" + TID.encode() + b"3:seqi7e1:vx")
        data = item.make(key, TID, b"\x01" * 32, "ab" * 32, 1795529938)
        it = item.read(data, signing.public_key_text(key), TID)
        self.assertEqual((it.seq, it.ih, it.m, it.k), (1795529938, b"\x01" * 32, "ab" * 32, pub))
        self.assertEqual(it.to_bytes(), data)
        self.assertTrue(signing.verify(pub, item.signed_part(TID.encode(), it.seq, bencode.encode(v)), it.sig))
        self.assertEqual(item.target(pub, TID), hashlib.sha1(pub + TID.encode()).digest())
        doc = bencode.decode(data)
        for k, bad in ((b"seq", 1795529939), (b"salt", b"f" * 32)):
            forged = dict(doc)
            forged[k] = bad
            with self.assertRaises(torrent.TorrentError):
                item.read(bencode.encode(forged), signing.public_key_text(key), TID)
        signing.generate_key(self.p("other.pem"), "")
        other = signing.load_key(self.p("other.pem"))
        with self.assertRaises(torrent.TorrentError):
            item.read(data, signing.public_key_text(other), TID)                    # another publisher's key
        with self.assertRaises(torrent.TorrentError):
            item.read(data, signing.public_key_text(key), "f" * 32)                 # another torrent

    def test_numbers_only_go_up(self):
        self.assertEqual(item.seq_for("2026-11-24T00:00:00Z"), 1795478400)
        self.assertEqual(item.seq_for("2026-11-24T00:00:00Z", 1795478400), 1795478401)       # the same second again
        self.assertEqual(item.seq_for("2020-01-01T00:00:00Z", 1795478400), 1795478401)       # an older release restored
        _path, key = self.key()
        a = item.read(item.make(key, TID, b"\x01" * 32, "ab" * 32, 10), signing.public_key_text(key), TID)
        b = item.read(item.make(key, TID, b"\x02" * 32, "cd" * 32, 11), signing.public_key_text(key), TID)
        self.assertIs(item.newest([a, b]), b)
        self.assertIs(item.newest([a, b], kept_seq=11), b)
        self.assertIsNone(item.newest([a], kept_seq=11))                               # lower than accepted: refused
        kept = torrent.Kept(self.p("state"), TID)
        kept.accept(11)
        kept.accept(5)
        kept.met(["203.0.113.10:6881", "bad", "203.0.113.10:6881"], own={"127.0.0.1:1"})
        kept.save()
        again = torrent.Kept(self.p("state"), TID)
        self.assertEqual((again.seq, again.peers), (11, ["203.0.113.10:6881"]))

    def test_bencode_is_one_way(self):
        self.assertEqual(bencode.encode({"b": 1, "a": [b"x", "y"]}), b"d1:al1:x1:ye1:bi1ee")
        self.assertEqual(bencode.decode(b"d1:al1:x1:ye1:bi1ee"), {b"a": [b"x", b"y"], b"b": 1})
        for bad in (b"d1:bi1e1:ai1ee", b"i03e", b"i-0e", b"3:ab", b"i1ei2e", b"d1:ai1e1:ai1ee"):
            with self.assertRaises(bencode.BencodeError, msg=bad):
                bencode.decode(bad)


class Made(Base):
    def test_a_torrent_of_the_store_own_layout(self):
        blob = os.urandom(100000)
        sha = hashlib.sha256(blob).hexdigest()
        write(self.p("blobs", sha), blob)
        manifest = b'{"format": 1}\n'
        data, ih = make.make("mod", manifest, ("x" * 64 + ".sig", b"sig\n"), {sha: len(blob)}, self.p("blobs"),
                             web_seed="https://game.example.com/")
        self.assertEqual(make.infohash(data), ih)
        doc = bencode.decode(data)
        self.assertNotIn(b"announce", doc)                                          # never a tracker
        self.assertNotIn(b"announce-list", doc)
        self.assertEqual(doc[b"url-list"], [b"https://game.example.com/"])
        files, name = make.files_of(doc)
        self.assertEqual(name, "mod")
        self.assertEqual(sorted(files), ["blobs/" + sha, "manifest.json", "sigs/" + "x" * 64 + ".sig"])
        self.assertEqual(files["manifest.json"], (len(manifest), make.merkle_root(manifest)))
        self.assertEqual(make.merkle_root(b"a"), hashlib.sha256(b"a").digest())
        self.assertEqual(make.piece_length_for(1000), 16384)
        self.assertEqual(make.piece_length_for(1 << 30), 512 << 10)
        again, ih2 = make.make("mod", manifest, ("x" * 64 + ".sig", b"sig\n"), {sha: len(blob)}, self.p("blobs"),
                               web_seed="https://game.example.com/")
        self.assertEqual((again, ih2), (data, ih))                                    # the same files, the same torrent

    def test_publish_makes_the_torrent_and_the_item_and_keeps_them(self):
        p = self.publisher()
        rc, ev = run_json(["--project", "Mod", "publish", "--sign"])
        self.assertEqual(rc, 0, ev[-1])
        t1 = ev[-1]["data"]["torrent"]
        self.assertTrue(t1["made"])
        store = self.p("store")
        raw = read(os.path.join(store, "manifest.json"))
        cfg = config.load(p)
        it = item.read(read(os.path.join(store, "release.item")), signing.public_key_text(self.key()[1]), cfg["torrent_id"])
        self.assertEqual(it.m, hashlib.sha256(raw).hexdigest())
        self.assertEqual(it.ih, make.infohash(read(os.path.join(store, "release.torrent"))))
        files, name = make.files_of(bencode.decode(read(os.path.join(store, "release.torrent"))))
        self.assertEqual(name, "mod")                                                # the address's last part
        self.assertEqual(bencode.decode(read(os.path.join(store, "release.torrent")))[b"url-list"],
                         [b"https://game.example.com/"])                             # its parent: the web seed
        man = Manifest.from_json(raw)
        self.assertEqual({f for f in files if f.startswith("blobs/")}, {"blobs/" + s for s in man.blobs()})
        self.assertEqual(man.project["torrent"], {"id": cfg["torrent_id"], "name": "mod", "seeds": ["203.0.113.10:6881"]})
        rc, ev = run_json(["--project", "Mod", "publish", "--sign"])                 # the same release: nothing moves
        self.assertEqual(ev[-1]["data"]["torrent"], dict(t1, made=False))
        write(self.p("src", "readme.txt"), "two\n")
        rc, ev = run_json(["--project", "Mod", "publish", "--sign"])
        self.assertGreater(ev[-1]["data"]["torrent"]["seq"], t1["seq"])
        rc, ev = run_json(["--project", "Mod", "publish"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "sign-needed"))                  # never unsigned

    def test_deploy_sends_them_before_the_manifest_and_backups_keep_them(self):
        p = self.publisher()
        run_json(["--project", "Mod", "publish", "--sign"])
        order = []
        real = deploy.FolderTarget.put_extra, deploy.FolderTarget.put_manifests
        with mock.patch.object(deploy.FolderTarget, "put_extra", lambda s, rel, d: (order.append(rel), real[0](s, rel, d))), \
                mock.patch.object(deploy.FolderTarget, "put_manifests", lambda s, n, pr: (order.append("manifest"), real[1](s, n, pr))[1]):
            deploy.deploy(self.p("store"), deploy.FolderTarget(self.p("www")))
        self.assertEqual(order[-3:], ["release.torrent", "release.item", "manifest"])
        self.assertEqual(read(self.p("www", "release.item")), read(self.p("store", "release.item")))
        config.save({"deploy_mode": "folder", "deploy_folder": self.p("www")}, p)
        rc, ev = run_json(["--project", "Mod", "backup"])
        self.assertEqual(rc, 0, ev[-1])
        name = ev[-1]["data"]["name"]
        folder_of = backup.folder_of(p, config.load(p))
        self.assertEqual(read(os.path.join(folder_of, name, "release.torrent")), read(self.p("www", "release.torrent")))
        os.remove(self.p("www", "release.torrent"))
        os.remove(self.p("www", "release.item"))
        rc, ev = run_json(["--project", "Mod", "backup", "restore", name, "--yes"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(read(self.p("www", "release.torrent")), read(self.p("store", "release.torrent")))

    def test_serve_is_a_web_seed(self):
        self.publisher()
        run_json(["--project", "Mod", "publish", "--sign"])
        srv, _t = serve.serve_in_background(self.p("store"), "127.0.0.1", 0)
        try:
            base = "http://127.0.0.1:%d/" % srv.server_address[1]
            man = Manifest.from_json(read(self.p("store", "manifest.json")))
            sha = sorted(man.blobs())[0]
            for path in ("release.torrent", "release.item", "mod/manifest.json", "mod/blobs/" + sha):
                with urllib.request.urlopen(base + path) as r:
                    self.assertEqual(r.status, 200, path)
            for path in ("other/blobs/" + sha, "mod/.htaccess", "mod/history.jsonl"):
                with self.assertRaises(urllib.error.HTTPError, msg=path):
                    urllib.request.urlopen(base + path)
        finally:
            srv.shutdown()
            srv.server_close()


class ProjectFiles(Base):
    def test_version_3_carries_the_torrent_and_needs_the_key(self):
        self.publisher()
        rc, ev = run_json(["project", "export", "Mod", "--out", self.p("mod.deltapatch")])
        self.assertEqual((rc, ev[-1]["code"]), (1, "no-key"))                      # unsigned and no trust_key
        rc, ev = run_json(["project", "export", "Mod", "--out", self.p("mod.deltapatch"), "--sign"])
        self.assertEqual(rc, 0, ev[-1])
        found = presets.read(self.p("mod.deltapatch"))
        self.assertEqual(found["version"], 3)
        e = found["projects"][0]
        self.assertEqual(set(e["torrent"]), {"id", "name", "seeds"})
        self.assertIn("By torrent", read(self.p("mod.deltapatch")).decode())
        with mock.patch.object(presets, "VERSION", 2):                              # a DeltaPatcher before version 3
            with self.assertRaises(presets.PresetError) as caught:
                presets.read(self.p("mod.deltapatch"))
        self.assertEqual(caught.exception.code, "newer")
        self.assertIn("made by a newer DeltaPatcher: update DeltaPatcher first", str(caught.exception))

    def test_a_project_with_no_address_and_the_seed_question(self):
        _path, key = self.key()
        e = {"title": "Only", "trust_key": signing.public_key_text(key),
             "torrent": {"id": TID, "name": "only", "seeds": ["203.0.113.10:6881"]}}
        write(self.p("only.deltapatch"), presets.build([e], key, updates=True))
        bad = dict(e)
        del bad["trust_key"]
        with self.assertRaises(presets.PresetError):
            presets.check_data({"format": presets.FORMAT, "version": 3, "made": "2026-11-24T00:00:00Z",
                                "updates": False, "projects": [bad]})
        with self.assertRaises(presets.PresetError):                                # a torrent in a version 2 file
            presets.check_data({"format": presets.FORMAT, "version": 2, "made": "2026-11-24T00:00:00Z",
                                "updates": False, "projects": [dict(e, source_url="https://game.example.com/")]})
        os.environ["DELTAPATCHER_CONFIG"] = self.p("player", "deltapatcher.ini")
        rc, ev = run_json(["project", "import", self.p("only.deltapatch"), "--folder", self.p("game"), "--yes"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(ev[-1]["data"]["added"][0]["seed"], False)                  # off unless they say yes
        cfg = config.load(projects.resolve("Only"))
        self.assertEqual((cfg["torrent"], cfg["torrent_id"], cfg["torrent_name"], cfg["seeds"], cfg["seed"]),
                         (True, TID, "only", "203.0.113.10:6881", False))
        self.assertTrue(torrent.torrent_only(cfg))
        rc, ev = run_json(["project", "import", self.p("only.deltapatch"), "--name", "Only 2", "--as-new",
                           "--folder", self.p("game2"), "--yes", "--seed"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertTrue(config.load(projects.resolve("Only 2"))["seed"])
        config.save({"torrents": "refuse"})
        rc, ev = run_json(["project", "import", self.p("only.deltapatch"), "--dry-run"])
        self.assertTrue(ev[-1]["data"]["torrents_refused"])
        self.assertTrue(any("torrents are refused" in x.get("text", "") for x in ev))


class Publisher(Base):
    def test_a_changed_seeds_list_is_asked_like_an_address(self):
        _path, key = self.key()
        trusted = signing.public_key_text(key)
        cfg = dict(config.DEFAULTS, source_url="https://game.example.com/mod/", trust_key=trusted, torrent=True,
                   torrent_id=TID, torrent_name="mod", seeds="203.0.113.10:6881", follow_publisher=True)
        obj = {"title": "Mod", "source_url": "https://game.example.com/mod/", "trust_key": trusted,
               "torrent": {"id": TID, "name": "mod", "seeds": ["203.0.113.10:6881", "203.0.113.11:6881"]}}
        self.assertEqual(publisher.read_object(obj)["torrent"], obj["torrent"])
        damaged = publisher.read_object(dict(obj, torrent={"id": TID}))                 # not a torrent: passed over
        self.assertNotIn("torrent", damaged)
        self.assertEqual(damaged["source_url"], obj["source_url"])
        change = publisher.change_of(cfg, obj, trusted, trusted)
        self.assertEqual([i["field"] for i in change["changes"]], ["torrent"])
        self.assertEqual((change["trust"], change["applicable"]), ("signed", ["torrent"]))
        self.assertIn("seeds only if you say yes", change["warning"])
        refused = publisher.change_of(cfg, obj, "", trusted)
        self.assertEqual((refused["trust"], refused["applicable"]), ("refused", []))
        p = projects.new("Mod")
        config.save(cfg, p)
        publisher.apply(p, config.load(p), obj, change, trusted, "2026-11-24T00:00:00Z")
        after = config.load(p)
        self.assertEqual((after["seeds"], after["seed"]), ("203.0.113.10:6881, 203.0.113.11:6881", False))
        self.assertIsNone(publisher.change_of(after, obj, trusted, trusted))
        # details with no torrent keep their old name: a Not now answered before torrents existed still holds
        plain = {"description": "x", "source_url": "https://game.example.com/"}
        old = hashlib.sha256(json.dumps({k: str(plain.get(k) or "") for k in
                                         ("description", "website", "install_hint", "install_folder", "source_url",
                                          "trust_key")}, sort_keys=True).encode("utf-8")).hexdigest()
        self.assertEqual(publisher.offer_hash(plain), old)


class FolderMap(Base):
    """What a seed offers of an installed folder: only files the engine checked; never a changed or protected one."""

    def files(self, man, raw):
        out, i = [], 0
        signame = "sigs/%s.sig" % hashlib.sha256(raw).hexdigest()
        out.append(folder.TFile(i, "manifest.json", len(raw), 0, 1, root=make.merkle_root(raw)))
        i += 1
        out.append(folder.TFile(i, ".pad/1", 100, 0, 0, pad=True))
        i += 1
        out.append(folder.TFile(i, signame, 4, 1, 1, root=make.merkle_root(b"sig\n")))
        piece = 2
        for sha, size in sorted(man.blobs().items()):
            i += 1
            n = (size + 16383) // 16384
            out.append(folder.TFile(i, "blobs/" + sha, size, piece, n))
            piece += n
        return out, piece, signame

    def test_changed_protected_and_missing_files_are_never_offered(self):
        write(self.p("src", "a.bin"), os.urandom(40000))
        write(self.p("src", "b.bin"), os.urandom(20000))
        write(self.p("src", "c.bin"), os.urandom(30000))
        write(self.p("src", "user.cfg"), b"defaults\n")
        write(self.p("src", "gone.bin"), os.urandom(5000))
        publish.publish(self.p("src"), self.p("store"), protect=["/user.cfg"])
        client.update(self.p("store"), self.p("game"))
        raw = read(self.p("store", "manifest.json"))
        man = Manifest.from_json(raw)
        files, pieces, signame = self.files(man, raw)
        write(self.p("game", ".deltapatcher", "release", "manifest.json"), raw)
        write(self.p("game", ".deltapatcher", "release", signame), b"sig\n")
        write(self.p("game", "b.bin"), os.urandom(20000))                           # changed here, same size
        os.remove(self.p("game", "gone.bin"))
        cache = folder.hash_cache(self.p("game"))
        m = folder.for_folder(files, pieces, self.p("game"), [man], ".deltapatcher/release", cache)
        offered = {v for k, v in m.offered.items()}
        self.assertEqual(offered, {"a.bin", "c.bin", ".deltapatcher/release/manifest.json",
                                   ".deltapatcher/release/" + signame})
        by_path = {f.path: f for f in files}
        held = {man.files[p].sha256 for p in ("b.bin", "gone.bin", "user.cfg")}
        for sha in held:
            f = by_path["blobs/" + sha]
            self.assertEqual(m.priorities[f.index], 0)
            self.assertTrue(m.renamed[f.index].startswith(".deltapatcher/release/absent/"))
            self.assertFalse(any(m.have[f.first:f.first + f.pieces]))
        a = by_path["blobs/" + man.files["a.bin"].sha256]
        self.assertTrue(all(m.have[a.first:a.first + a.pieces]))
        self.assertEqual(folder.changed(self.p("game"), m.watch), [])
        write(self.p("game", "a.bin"), os.urandom(40001))
        self.assertEqual(folder.changed(self.p("game"), m.watch), ["a.bin"])
        self.assertEqual(folder.layout_problem(files), "")
        self.assertNotEqual(folder.layout_problem(files + [folder.TFile(99, "readme.txt", 1, 0, 1)]), "")


class Handshake(Base):
    """An update asks the seed runs of its folder to pause, and goes on once they answer, or are gone."""

    def test_an_update_waits_for_the_answer(self):
        root = self.p("game")
        os.makedirs(root)
        seat = pause.Seat(root)
        seat.pid = 4242                         # a seed run in another process, as the update sees it
        seat.beat(force=True)
        events, stop = [], threading.Event()

        def seed_side():
            while not stop.is_set():
                if seat.asked() and "paused" not in events:
                    time.sleep(0.3)
                    events.append("paused")
                    seat.answer()
                elif not seat.asked() and "paused" in events:
                    seat.resumed()
                    events.append("resumed")
                    return
                time.sleep(0.05)
        with mock.patch.object(pause, "pid_alive", lambda pid: True):
            t = threading.Thread(target=seed_side, daemon=True)
            t.start()
            t0 = time.time()
            with pause.paused(root):
                waited = time.time() - t0
                self.assertEqual(events, ["paused"])
            t.join(10)
        stop.set()
        self.assertGreaterEqual(waited, 0.25)
        self.assertEqual(events, ["paused", "resumed"])
        self.assertFalse(os.path.exists(os.path.join(pause.folder_dir(root), "4242.pause")))

    def test_an_answer_that_never_comes_holds_an_update_for_a_while_only(self):
        root = self.p("game")
        os.makedirs(root)
        seat = pause.Seat(root)
        seat.pid = 4242
        seat.beat(force=True)
        said = []
        with mock.patch.object(pause, "pid_alive", lambda pid: True), mock.patch.object(pause, "WAIT", 0.5):
            t0 = time.time()
            with pause.paused(root, log=said.append):
                pass
        self.assertLess(time.time() - t0, 5)                                         # its 0.5 s, then it goes on
        self.assertTrue(any("did not pause" in x for x in said), said)

    def test_a_request_of_an_update_that_ended_never_holds_a_seed_run(self):
        root = self.p("game")
        os.makedirs(root)
        seat = pause.Seat(root)
        seat.beat(force=True)
        d = pause.folder_dir(root)
        write(os.path.join(d, "999.0badc0de.asking"), "")
        # its process id is in use again (this very process), but no update holds its lock any more
        pause._write(seat._p("pause"), {"by": os.getpid(), "at": time.time(), "lock": "999.0badc0de.asking"})
        self.assertIsNone(seat.request())
        self.assertFalse(os.path.exists(seat._p("pause")))
        with mock.patch.object(pause, "WAIT", 0.3):
            with pause.paused(root):                                  # an update that runs: its request stands
                self.assertIsNotNone(seat.request())
        self.assertIsNone(seat.request())
        self.assertEqual([n for n in os.listdir(d) if n.endswith(".asking")], [])

    def test_a_request_read_at_that_moment_is_still_taken_back(self):
        d = pause.folder_dir(self.p("game"))
        os.makedirs(d)
        path = os.path.join(d, "4242.pause")
        write(path, '{"by": 1, "at": 0}')
        reading = open(path, "rb")                  # the seed run reads it just then (Windows: it cannot go yet)
        threading.Timer(0.2, reading.close).start()
        pause._remove(path)
        self.assertFalse(os.path.exists(path))
        write(path, '{"by": 1, "at": 0}')
        reading = open(path, "rb")
        threading.Timer(0.2, reading.close).start()
        pause._write(path, {"by": 2, "at": 1})       # and one replaced while it is read
        self.assertEqual(json.loads(read(path).decode("utf-8"))["by"], 2)

    def test_a_seed_run_that_is_gone_holds_nothing(self):
        root = self.p("game")
        d = pause.folder_dir(root)
        os.makedirs(d)
        write(os.path.join(d, "4000000.seeding"), "{}")                            # no such process
        old = os.path.join(d, "123.seeding")
        write(old, "{}")
        os.utime(old, (time.time() - 60, time.time() - 60))                         # not written for a minute
        t0 = time.time()
        with pause.paused(root):
            pass
        self.assertLess(time.time() - t0, 5)                                         # never the 10 s wait
        self.assertEqual(pause.seeders(root), [])

    def test_between_two_processes(self):
        root = self.p("game")
        os.makedirs(root)
        code = ("import os, sys, time; sys.path.insert(0, %r)\n"
                "from deltapatcher.torrent import pause\n"
                "seat = pause.Seat(%r); seat.beat(force=True); print('ready', os.getpid(), flush=True)\n"
                "end = time.time() + 20; state = 'seeding'\n"
                "while time.time() < end:\n"
                "    if seat.asked() and state == 'seeding':\n"
                "        state = 'paused'; seat.answer(); print('paused', flush=True)\n"
                "    elif not seat.asked() and state == 'paused':\n"
                "        seat.resumed(); print('resumed', flush=True); break\n"
                "    seat.beat(); time.sleep(0.05)\n"
                "seat.leave()\n") % (ROOT, root)
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        try:
            word, pid = proc.stdout.readline().split()          # a venv's python starts the real one as a child
            self.assertEqual(word, "ready")
            self.assertEqual(pause.seeders(root), [int(pid)])
            with pause.paused(root):
                self.assertEqual(proc.stdout.readline().strip(), "paused")
            self.assertEqual(proc.stdout.readline().strip(), "resumed")
            proc.wait(30)
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()
        self.assertEqual(pause.seeders(root), [])


class FailClosed(Base):
    """general.torrents: only allow lets BitTorrent in; anything else written there refuses it."""

    def test_config_set_takes_allow_or_refuse_only(self):
        for value in ("no", "off", "false", "0", "deny", "refused", "", "yes"):
            rc, ev = run_json(["config", "set", "torrents=%s" % value])
            self.assertEqual((rc, ev[-1]["code"], ev[-1]["message"]), (1, "bad-setting", "torrents: allow or refuse"),
                             value)
        self.assertEqual(config.load()["torrents"], "allow")
        rc, ev = run_json(["config", "set", "torrents=Refuse"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertTrue(torrent.refused(config.load()))
        rc, ev = run_json(["config", "set", "torrents=allow"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertFalse(torrent.refused(config.load()))

    def test_anything_but_allow_in_the_file_refuses(self):
        path = config.ensure()
        text = read(path).decode("utf-8")
        self.assertIn("\ntorrents = allow\n", text)
        for written, refuses in (("no", True), ("yes", True), ("", True), ("maybe", True), ("refuse", True),
                                 ("allow", False), ("ALLOW", False)):
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(re.sub(r"(?m)^torrents = allow$", "torrents = %s" % written, text))
            self.assertEqual(torrent.refused(config.load()), refuses, written)
        self.assertFalse(torrent.refused({}))                       # never set anywhere: allow, the default


class FakeNode(object):
    """A DHT node on 127.0.0.1 for these tests: it answers a get with what `holds` says and keeps the puts it gets;
    with spoof, its answers come from another address (someone beside it who saw the query)."""

    def __init__(self, holds=None, spoof=False, junk=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.ep = self.sock.getsockname()
        self.out = self.sock
        if spoof:
            self.out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.out.bind(("127.0.0.1", 0))
        self.junk = junk                            # a datagram sent before each answer
        self.holds = holds or {}
        self.id = os.urandom(20)
        self.puts = []
        self.done = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while not self.done.is_set():
            try:
                data, frm = self.sock.recvfrom(65536)
            except (socket.timeout, ConnectionResetError):
                continue
            except OSError:
                return
            msg = bencode.decode(data)
            r = {b"id": self.id}
            if msg[b"q"] == b"get":
                r[b"token"] = b"token"
                r.update(self.holds)
            elif msg[b"q"] == b"put":
                self.puts.append(msg[b"a"])
            try:
                if self.junk is not None:
                    self.out.sendto(self.junk, frm)
                self.out.sendto(bencode.encode({b"t": msg[b"t"], b"y": b"r", b"r": r}), frm)
            except OSError:
                pass

    def close(self):
        self.done.set()
        for s in {self.sock, self.out}:
            s.close()


class Krpc(Base):
    """What a DHT node says is not taken on its word: a number counts only with an item signed by the publisher key
    for this torrent, and an answer only from the address it was asked at."""

    def doc(self, seq):
        _path, key = self.key()
        return bencode.decode(item.make(key, TID, b"\1" * 32, "ab" * 32, seq))

    def node(self, **kw):
        n = FakeNode(**kw)
        self.addCleanup(n.close)
        return n

    def put(self, doc, n, log=None):
        return krpc.put_item(doc, item.target(doc[b"k"], TID), [n.ep], bind_ip="127.0.0.1", seconds=10, log=log)

    def test_a_number_without_an_item_does_not_stop_a_put(self):
        doc = self.doc(100)
        n = self.node(holds={b"seq": 2 ** 40})
        self.assertEqual(self.put(doc, n), (1, 0))
        self.assertEqual(n.puts[0][b"seq"], 100)

    def test_a_forged_item_does_not_stop_a_put(self):
        doc = self.doc(100)
        n = self.node(holds={b"k": doc[b"k"], b"seq": 2 ** 40, b"sig": b"\0" * 64, b"v": doc[b"v"]})
        self.assertEqual(self.put(doc, n), (1, 0))
        self.assertEqual(n.puts[0][b"seq"], 100)

    def test_a_newer_item_signed_by_the_publisher_key_stops_it(self):
        doc, newer = self.doc(100), self.doc(150)
        n = self.node(holds={k: newer[k] for k in (b"k", b"seq", b"sig", b"v")})
        said = []
        self.assertEqual(self.put(doc, n, log=said.append), (0, 150))
        self.assertEqual(n.puts, [])
        self.assertTrue(any("a newer release item, numbered 150 and signed by the publisher key" in s for s in said), said)

    def test_an_answer_from_another_address_is_not_taken(self):
        for n, taken in ((self.node(spoof=True), False), (self.node(), True)):
            c = krpc.Client("127.0.0.1", wait=3.0)
            try:
                got = c.ask([n.ep], b"get", {b"target": b"\0" * 20})
            finally:
                c.close()
            self.assertEqual(got[n.ep] is not None, taken)


class NextRelease(Base):
    """A newer release a seed run found is seeded from next/ only while it is newer than the folder's own."""

    def release(self, version, seq, where):
        _path, key = self.key()
        raw = Manifest("Mod", version, files=[]).to_json()
        files = {"manifest.json": raw, "release.torrent": b"d4:infod4:name3:modee",
                 "release.item": item.make(key, TID, b"\2" * 32, hashlib.sha256(raw).hexdigest(), seq)}
        for rel, data in files.items():
            write(os.path.join(where, *rel.split("/")), data)
        return raw

    def seeded(self, root):
        from deltapatcher.torrent import seed
        _path, key = self.key()
        p = projects.new("Player")
        config.save({"install_dir": root, "trust_key": signing.public_key_text(key), "torrent": True,
                     "torrent_id": TID, "source_url": "https://game.example.com/mod/"}, p)
        said = []
        run = mock.Mock(seeded=[], swarm=None, ip=None)
        run.log = lambda text, s=None: said.append(text)
        return seed.Seeded(run, p, config.load(p)), said

    def test_an_outdated_next_is_never_seeded(self):
        state = self.p("game", ".deltapatcher")
        nxt = os.path.join(state, pause.DIR, pause.NEXT)
        self.release("1.1", 100, nxt)                                       # found while the folder had 1.0
        now = self.release("1.2", 200, os.path.join(state, "release"))      # then the folder updated to 1.2
        s, _said = self.seeded(self.p("game"))
        raw, _sig, _tor, it, manifests = s.release_now()
        self.assertEqual((raw, it.seq, len(manifests)), (now, 200, 1))
        self.assertFalse(os.path.exists(nxt))
        newest = self.release("1.3", 300, nxt)                              # one newer than the folder's still is
        raw, _sig, _tor, it, manifests = s.release_now()
        self.assertEqual((raw, it.seq, len(manifests)), (newest, 300, 2))

    def test_an_update_takes_next_away(self):
        state = self.p("game", ".deltapatcher")
        nxt = os.path.join(state, pause.DIR, pause.NEXT)
        self.release("1.1", 100, nxt)
        from deltapatcher.torrent import session
        session.keep_release(state, Manifest("Mod", "1.2", files=[]).to_json(), None, {})
        self.assertFalse(os.path.exists(nxt))

    def test_a_switch_that_cannot_write_is_said_and_looked_at_again(self):
        os.makedirs(self.p("game"))
        s, said = self.seeded(self.p("game"))
        from deltapatcher.torrent import seed
        with mock.patch.object(seed, "_write_files", side_effect=OSError("the file is in use")):
            s._switch({"manifest.json": b"{}"})                             # the run goes on
        self.assertTrue(any("the newer release could not be written here (the file is in use)" in x for x in said),
                        said)
        self.assertGreater(s.next_newer, time.time() + 30)


class WorkFolder(Base):
    """Each finding of a release works in a folder of its own: a seed run's look never takes a check's files."""

    def source(self, root):
        from deltapatcher.torrent import session
        key = "ed25519:" + base64.b64encode(b"\3" * 32).decode("ascii")
        return session.TorrentSource({"id": TID, "name": "only", "seeds": []}, key, root, {})

    def test_a_look_beside_a_check_of_the_same_folder(self):
        root = self.p("game")
        check, look = self.source(root), self.source(root)
        mine = os.path.join(check._work(), "manifest.json")
        write(mine, "{}")
        look._work()
        self.assertNotEqual(check.work, look.work)
        look.close()
        self.assertTrue(os.path.isfile(mine))
        check.close()
        self.assertFalse(os.path.exists(os.path.join(root, ".deltapatcher", "torrent")))

    def test_the_work_folder_of_a_process_that_ended_goes(self):
        root = self.p("game")
        gone = os.path.join(root, ".deltapatcher", "torrent", "%d.0badc0de" % (2 ** 30 + 7))
        os.makedirs(gone)
        s = self.source(root)
        s._work()
        self.assertFalse(os.path.exists(gone))
        s.close()

    def test_a_damaged_kept_torrent_is_as_good_as_none(self):
        from deltapatcher.torrent import session
        self.assertIsNone(session._infohash_or_none(b"not a torrent"))
        self.assertIsNone(session._infohash_or_none(b"d4:infoi1"))
        _path, key = self.key()
        trusted = signing.public_key_text(key)
        state = self.p("game", ".deltapatcher")
        write(os.path.join(state, "release", "manifest.json"), b"{}")
        write(os.path.join(state, "release", "release.item"),
              item.make(key, TID, b"\5" * 32, hashlib.sha256(b"{}").hexdigest(), 5))
        write(os.path.join(state, "release", "release.torrent"), b"damaged")
        s = session.TorrentSource({"id": TID, "name": "only", "seeds": []}, trusted, self.p("game"), {})
        s.item = item.read(read(os.path.join(state, "release", "release.item")), trusted, TID)
        self.assertTrue(s.needs_keep(b"{}"))                                # no MakeError: it is kept again


class Lookups(Base):
    """Seeds are looked up once a round, and never past a deadline or a stop."""

    def test_seeds_are_resolved_once_a_round(self):
        from deltapatcher.torrent import session
        sw = session.Swarm.__new__(session.Swarm)
        sw.ip, sw.log = "", (lambda s: None)
        sw.dht_nodes = lambda: [("127.0.0.%d" % i, 6881) for i in range(2, 60)]
        sw.pump = lambda ms: time.sleep(0.05)
        calls, rounds = [], []

        def lookup(host, port, *a, **k):
            calls.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
        with mock.patch.object(session.socket, "getaddrinfo", side_effect=lookup), \
                mock.patch.object(session.krpc, "get_items", side_effect=lambda *a, **k: rounds.append(1) or []):
            sw.get_items(b"\4" * 32, TID, "ed25519:x", ["a.example.com:1", "b.example.com:2"], seconds=0.5)
        self.assertGreater(len(rounds), 1)
        self.assertLessEqual(len(calls), 2 * len(rounds))

    def test_a_lookup_ends_at_its_deadline_or_a_stop(self):
        from deltapatcher.torrent import session
        stop = threading.Event()
        stop.set()
        with mock.patch.object(session.socket, "getaddrinfo", side_effect=AssertionError("looked up")):
            self.assertEqual(session.resolve(["a.example.com:1"], time.time() - 1), [])
            self.assertEqual(session.resolve(["a.example.com:1"], None, stop), [])

    def test_this_computers_name_is_never_waited_for(self):
        """A resolver can take half a minute for this computer's own name (macOS asks mDNS when the name is not in
        DNS): a session's own addresses come at once without it, and with its addresses once the lookup answers."""
        from deltapatcher.torrent import session
        answer = threading.Event()
        self.addCleanup(answer.set)

        def lookup(host, port, *a, **k):
            answer.wait(10)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 0))]
        sw = session.Swarm.__new__(session.Swarm)
        sw.ip, sw.ses = "", mock.Mock()
        sw.ses.listen_port.return_value = 6881
        with mock.patch.object(session, "_HOST", {"addresses": [], "started": None, "thread": None}), \
                mock.patch.object(session.socket, "getaddrinfo", side_effect=lookup):
            t0 = time.time()
            self.assertEqual(sw.own(), {"127.0.0.1:6881"})
            self.assertLess(time.time() - t0, 1.0)
            answer.set()
            session._HOST["thread"].join(5)
            self.assertEqual(sw.own(), {"127.0.0.1:6881", "203.0.113.10:6881"})


class Cached(Base):
    def test_a_resumed_update_does_not_fetch_its_cached_blobs_again(self):
        write(self.p("src", "a.bin"), os.urandom(50000))
        write(self.p("src", "b.bin"), os.urandom(60000))
        publish.publish(self.p("src"), self.p("store"))
        man = Manifest.from_json(read(self.p("store", "manifest.json")))
        a, b = man.files["a.bin"].sha256, man.files["b.bin"].sha256
        write(self.p("game", ".deltapatcher", "cache", a), read(self.p("store", "blobs", a)))     # a stopped update's
        asked = []

        class Side(object):
            def fetch(self, src, need, cdir, progress, cancel, raw=None, signer=None):
                asked.append(dict(need))

            def needs_keep(self, raw):
                return False

            def keep(self, src, raw, sig, signer):
                pass
        client.update(self.p("store"), self.p("game"), torrent=Side())
        self.assertEqual(asked, [{b: 60000}])
        self.assertEqual(snapshot(self.p("game")), snapshot(self.p("src")))


class AddressItem(Base):
    """A release's torrent from the address is read by libtorrent only once its release item, signed by the
    publisher key, names this manifest and this torrent."""

    def test_the_item_is_checked_before_the_torrent_is_read(self):
        from deltapatcher.torrent import session
        p = self.publisher()
        run_json(["--project", "Mod", "publish", "--sign"])
        raw = read(self.p("store", "manifest.json"))
        _path, key = self.key()
        trusted = signing.public_key_text(key)
        tinfo = torrent.info(config.load(p))
        cases = ((b'{"format": 1}', trusted, "another release's (one going up now?)"),
                 (raw, "", "no publisher key signs this release"),
                 (raw, "ed25519:" + base64.b64encode(b"\6" * 32).decode("ascii"), "a release item is refused"))
        for manifest, signer, why in cases:
            said = []
            side = session.AddressTorrent(tinfo, None, self.p("game", ".deltapatcher"), {}, log=said.append)
            with mock.patch.object(session, "read_torrent", side_effect=AssertionError("libtorrent read it")):
                side.fetch(Source(self.p("store")), {}, self.p("cache"), lambda d, t, l: None, None, raw=manifest,
                           signer=signer)
            self.assertTrue(any(why in x and "everything comes from the address" in x for x in said), (why, said))
        os.remove(self.p("store", "release.item"))
        said = []
        side = session.AddressTorrent(tinfo, trusted, self.p("game", ".deltapatcher"), {}, log=said.append)
        with mock.patch.object(session, "read_torrent", side_effect=AssertionError("libtorrent read it")):
            side.fetch(Source(self.p("store")), {}, self.p("cache"), lambda d, t, l: None, None, raw=raw,
                       signer=trusted)
        self.assertTrue(any("comes without its release item" in x for x in said), said)


class Reserved(Base):
    def test_a_torrent_is_never_named_after_a_file_or_folder_of_the_store(self):
        for name in ("blobs", "SIGS", "manifest.json", "Release.Torrent", "release.item", "manifest.previous.json"):
            self.assertIn("is the name of a file or folder of the store", torrent.name_problem(name), name)
        self.assertEqual(torrent.default_name("https://game.example.com/files/blobs/"), "release")
        rc, ev = run_json(["config", "set", "torrent_name=blobs"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "bad-setting"))
        # a store whose torrent has that name anyway (made before this rule) still serves its blobs
        write(self.p("src", "a.bin"), os.urandom(1000))
        publish.publish(self.p("src"), self.p("store"))
        man = Manifest.from_json(read(self.p("store", "manifest.json")))
        data, _ih = make.make("blobs", read(self.p("store", "manifest.json")), None, man.blobs(),
                              self.p("store", "blobs"))
        write(self.p("store", "release.torrent"), data)
        srv, _t = serve.serve_in_background(self.p("store"), "127.0.0.1", 0)
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/blobs/%s" % (srv.server_address[1],
                                                                          sorted(man.blobs())[0])) as r:
                self.assertEqual(r.status, 200)
        finally:
            srv.shutdown()
            srv.server_close()


class SmallThings(Base):
    def test_numbers_are_read_one_way_only(self):
        for bad in (b"i+5e", b"i 5e", b"i1_0e", b"i5 e", b"i5\ne", b"i-0e", b"i05e", b"ie", b"i-e",
                    b"5 :abcde", b"1_0:abcdefghij", b"+5:abcde", b"05:abcde", b"5\n:abcde"):
            with self.assertRaises(bencode.BencodeError, msg=bad):
                bencode.decode(bad)
        for good, value in ((b"i-5e", -5), (b"i0e", 0), (b"i42e", 42), (b"0:", b""), (b"3:abc", b"abc")):
            self.assertEqual(bencode.decode(good), value)

    @unittest.skipUnless(os.name == "nt", "the Windows way of asking whether a process runs")
    def test_a_process_this_user_may_not_open_runs(self):
        import ctypes
        fake = mock.Mock()
        fake.OpenProcess.return_value = 0
        for error, alive in ((5, True), (87, False)):                     # access denied; no such process
            with mock.patch.object(ctypes, "WinDLL", return_value=fake), \
                    mock.patch.object(ctypes, "get_last_error", return_value=error):
                self.assertEqual(torrent.pid_alive(4321), alive, error)

    def test_the_number_kept_never_goes_down(self):
        state = self.p("game", ".deltapatcher")
        slow = torrent.Kept(state, TID)                     # read before another run of the folder accepted 500
        quick = torrent.Kept(state, TID)
        quick.accept(500)
        quick.save()
        slow.save()
        self.assertEqual(torrent.Kept(state, TID).seq, 500)

    def test_seed_runs_take_turns_with_seeding_json(self):
        other = []

        def second():
            with torrent._seeding_lock(0.3) as held:
                other.append(held)
        with torrent._seeding_lock() as held:
            self.assertTrue(held)
            t = threading.Thread(target=second)
            t.start()
            t.join(5)
        self.assertEqual(other, [False])
        with torrent._seeding_lock(0.3) as again:
            self.assertTrue(again)
        torrent.write_seeding({"started": "2026-11-24T00:00:00Z", "port": 1, "projects": []})
        self.assertEqual([r["pid"] for r in torrent.seeding_runs()], [os.getpid()])
        torrent.write_seeding(None)
        self.assertFalse(os.path.exists(torrent.seeding_path()))

    def test_one_rule_for_no_address_also_in_a_project_file(self):
        _path, key = self.key()
        e = {"title": "Only", "source_url": "http://localhost:8000/", "trust_key": signing.public_key_text(key),
             "torrent": {"id": TID, "name": "only", "seeds": ["127.0.0.1:9"]}}
        write(self.p("only.deltapatch"), presets.build([e], key, updates=True))
        config.save({"torrents": "refuse"})
        rc, ev = run_json(["update", self.p("game"), "--project-file", self.p("only.deltapatch")])
        self.assertEqual((rc, ev[-1]["code"], ev[-1]["message"]), (1, "torrents-refused", torrent.REFUSED_ONLY))

    def test_a_publish_with_no_address_says_how_players_find_it(self):
        self.publisher(public_url="")
        rc, ev = run_json(["--project", "Mod", "publish", "--sign"])
        self.assertEqual(rc, 0, ev[-1])
        texts = [x.get("text", "") for x in ev if x.get("event") == "log"]
        self.assertTrue(any("Mod has no address: players find this release through the DHT, once a seed run of this "
                            "store puts its release item there" in t for t in texts), texts)

    def test_turning_torrents_off_takes_the_torrent_out_of_the_store(self):
        self.publisher()
        run_json(["--project", "Mod", "publish", "--sign"])
        self.assertTrue(os.path.isfile(self.p("store", "release.torrent")))
        rc, ev = run_json(["--project", "Mod", "config", "set", "torrent=no"])
        self.assertEqual(rc, 0, ev[-1])
        rc, ev = run_json(["--project", "Mod", "publish", "--sign"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertFalse(os.path.exists(self.p("store", "release.torrent")))
        self.assertFalse(os.path.exists(self.p("store", "release.item")))
        texts = [x.get("text", "") for x in ev if x.get("event") == "log"]
        self.assertIn("took release.torrent and release.item out of the store: Mod is not shared by torrent "
                      "(torrent = no)", texts)


class Quiet(Base):
    """What a seed run or an update does around the swarm, kept out of the way of everything else."""

    def test_a_cache_that_cannot_be_written_never_fails_an_update_or_a_seed(self):
        write(self.p("src", "a.bin"), os.urandom(5000))
        publish.publish(self.p("src"), self.p("store"))
        with mock.patch.object(hashing.HashCache, "save", side_effect=PermissionError("in use")):
            client.update(self.p("store"), self.p("game"))
        self.assertEqual(snapshot(self.p("game")), snapshot(self.p("src")))
        cache = mock.Mock()
        cache.save.side_effect = PermissionError("in use")
        self.assertEqual(folder.for_store([], 0, self.p("store"), cache).offered, {})
        self.assertEqual(folder.for_folder([], 0, self.p("game"), [], ".deltapatcher/release", cache).offered, {})

    def test_a_look_makes_no_session_once_torrents_are_refused(self):
        from deltapatcher.torrent import session
        os.makedirs(self.p("game"))
        s, _said = NextRelease.seeded(self, self.p("game"))
        config.save({"torrents": "refuse"})
        with mock.patch.object(session, "TorrentSource", side_effect=AssertionError("a session for a look")):
            self.assertIsNone(s._newer_by_dht(threading.Event()))

    def test_only_a_seed_run_asks_the_router_for_a_port(self):
        from deltapatcher.torrent import session
        key = "ed25519:" + base64.b64encode(b"\3" * 32).decode("ascii")
        src = session.TorrentSource({"id": TID, "name": "only", "seeds": []}, key, self.p("game"),
                                    {"torrent_port_mapping": True, "seed_upload_kbps": 0})
        made = []
        with mock.patch.object(session, "Swarm", side_effect=lambda *a, **k: made.append(k) or mock.Mock()):
            src.open()
        self.assertEqual(made[0]["port_mapping"], False)

    def test_a_release_with_no_address_is_found_before_the_folder_is_locked(self):
        game = self.p("game")
        state = os.path.join(game, ".deltapatcher")
        found = []

        class Swarmed(object):
            http = False

            def __str__(self):
                return "the torrent only"

            def prepare(self):
                with client.Lock(state):            # nothing holds the folder while the release is looked for
                    found.append("prepared")

            def for_release(self, version):
                pass

            def get_manifest(self):
                raise SourceError("nothing found")
        with self.assertRaises(SourceError):
            client.update("", game, source=Swarmed())
        self.assertEqual(found, ["prepared"])


class HttpOnly(Base):
    """A project not shared by torrent sees nothing of it: its backups never ask for a torrent, and a plain version
    never loads libtorrent."""

    def test_backups_and_version_never_touch_torrents(self):
        write(self.p("src", "a.txt"), "a\n")
        publish.publish(self.p("src"), self.p("store"))
        deploy.deploy(self.p("store"), deploy.FolderTarget(self.p("www")))
        write(self.p("www", "release.torrent"), b"left there by an older release")
        p = projects.new("Plain")
        config.save({"deploy_mode": "folder", "deploy_folder": self.p("www")}, p)
        asked = []
        real = deploy.FolderTarget.get_file
        with mock.patch.object(deploy.FolderTarget, "get_file", lambda s, n: (asked.append(n), real(s, n))[1]):
            rc, ev = run_json(["--project", "Plain", "backup"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertIn("manifest.json", asked)
        self.assertFalse(set(asked) & set(deploy.TORRENT_FILES), asked)
        with mock.patch.object(torrent, "available", side_effect=AssertionError("libtorrent was loaded")):
            rc, ev = run_json(["version"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertNotIn("torrent", ev[-1]["data"])


HUGE = b"9" * 5000                         # a number Python 3.11 and later refuse to read (over 4300 digits)


class Oversized(Base):
    """A number too long to be anything is refused as a damaged message or file, never an error that ends a run."""

    def test_a_datagram_with_a_huge_number_changes_nothing(self):
        junk = b"d1:ti" + HUGE + b"e1:y1:re"
        with self.assertRaises(bencode.BencodeError):
            bencode.decode(junk)
        with self.assertRaises(bencode.BencodeError):
            bencode.decode(b"1" * 21 + b":x")                           # a length too long as well
        n = FakeNode(junk=junk)
        self.addCleanup(n.close)
        c = krpc.Client("127.0.0.1", wait=3.0)
        try:
            got = c.ask([n.ep], b"get", {b"target": b"\0" * 20})
        finally:
            c.close()
        self.assertEqual(got[n.ep][b"id"], n.id)                         # the junk was dropped, the answer taken

    def test_a_backup_with_a_crafted_release_item_ends_cleanly(self):
        p = self.publisher()
        run_json(["--project", "Mod", "publish", "--sign"])
        deploy.deploy(self.p("store"), deploy.FolderTarget(self.p("www")))
        config.save({"deploy_mode": "folder", "deploy_folder": self.p("www")}, p)
        for crafted in (b"d1:ki" + HUGE + b"ee", b"d1:k" + b"x" * 9000 + b"e"):
            write(self.p("www", "release.item"), crafted)
            rc, ev = run_json(["--project", "Mod", "backup"])
            self.assertEqual(rc, 0, ev[-1])
            made = os.path.join(backup.folder_of(p, config.load(p)), ev[-1]["data"]["name"])
            self.assertFalse(os.path.exists(os.path.join(made, "release.item")))
            self.assertTrue(os.path.isfile(os.path.join(made, "manifest.json")))


class UnreadableSettings(Base):
    """The person's choice about torrents that cannot be read is a refusal, said once; a byte order mark is fine."""

    def write_main(self, data):
        with open(config.ensure(), "wb") as f:
            f.write(data)

    def placeholder_with(self, value):
        return config.placeholder().replace("\ntorrents = allow\n", "\ntorrents = %s\n" % value).encode("utf-8")

    def test_a_byte_order_mark_is_fine(self):
        for value, refuses in (("refuse", True), ("allow", False)):
            self.write_main(b"\xef\xbb\xbf" + self.placeholder_with(value))
            self.assertEqual(torrent.refused(config.load()), refuses, value)
            self.assertEqual(config.UNREADABLE, {})
        rc, ev = run_json(["config", "set", "torrents=refuse"])              # an edit keeps the mark, and one section
        self.assertEqual(rc, 0, ev[-1])
        data = read(config.path())
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(data.count(b"\n[general]\n") + data.startswith(b"\xef\xbb\xbf[general]"), 1)
        self.assertTrue(torrent.refused(config.load()))

    def test_a_file_that_cannot_be_read_refuses_torrents_and_says_so_once(self):
        cases = (self.placeholder_with("allow") + b"torrents refuse\n",         # a line without =
                 b"\xff\xfe\x00[general]\ntorrents = allow\n")                 # not UTF-8
        for data in cases:
            self.write_main(data)
            self.assertTrue(torrent.refused(config.load()), data[-20:])
            self.assertEqual(config.UNREADABLE["path"], config.path())
            rc, ev = run_json(["version"])
            said = [e["text"] for e in ev if e.get("event") == "log" and "cannot be read" in e.get("text", "")]
            self.assertEqual(len(said), 1, ev)
            self.assertTrue(said[0].startswith("Warning: the settings file %s cannot be read (" % config.path()))
            self.assertTrue(said[0].endswith("): torrents are refused until it is fixed."))
        self.write_main(self.placeholder_with("allow"))
        self.assertFalse(torrent.refused(config.load()))
        self.assertEqual(config.UNREADABLE, {})


class LockedFloor(Base):
    """Inside the folder's lock, a release found before it is checked again against the number the folder took."""

    def test_an_item_below_what_the_folder_took_meanwhile_is_refused(self):
        from deltapatcher.torrent import session
        _path, key = self.key()
        trusted = signing.public_key_text(key)
        src = session.TorrentSource({"id": TID, "name": "only", "seeds": []}, trusted, self.p("game"), {})
        src.item = mock.Mock(seq=100)
        src.confirm()                                                      # nothing taken yet: it holds
        k = torrent.Kept(self.p("game", ".deltapatcher"), TID)
        k.accept(200)
        k.save()                                                           # another update took 200 meanwhile
        with self.assertRaises(SourceError) as caught:
            src.confirm()
        self.assertEqual(str(caught.exception), "the release found (number 100) is older than the one this folder "
                                                "accepted meanwhile (number 200): update again")

    def test_it_is_checked_with_the_lock_held(self):
        state = self.p("game", ".deltapatcher")
        seen = []

        class Found(object):
            http = False

            def __str__(self):
                return "the torrent only"

            def prepare(self):
                pass

            def confirm(self):
                try:
                    with client.Lock(state):
                        seen.append("free")
                except client.UpdateError:
                    seen.append("held")

            def for_release(self, version):
                pass

            def get_manifest(self):
                raise SourceError("nothing found")
        with self.assertRaises(SourceError):
            client.update("", self.p("game"), source=Found())
        self.assertEqual(seen, ["held"])


class ToTheEnd(Base):
    """A blob is checked as it goes in place, a damaged cached one comes again from the swarm, and a lookup that hangs
    is given up."""

    def store(self):
        write(self.p("src", "a.bin"), os.urandom(50000))
        write(self.p("src", "b.bin"), os.urandom(60000))
        publish.publish(self.p("src"), self.p("store"))
        return Manifest.from_json(read(self.p("store", "manifest.json")))

    def test_a_blob_that_changed_in_the_cache_is_never_put_in_place(self):
        self.store()
        real = hashing.copy_hashing

        def changed(src, dst):
            sha, size = real(src, dst)
            return "0" * 64, size
        with mock.patch.object(hashing, "copy_hashing", side_effect=changed):
            with self.assertRaises(client.UpdateError) as caught:
                client.update(self.p("store"), self.p("game"))
        self.assertIn("in the cache no longer matches its SHA-256", str(caught.exception))
        self.assertEqual(snapshot(self.p("game")), {})                     # undone

    def test_a_damaged_cached_blob_comes_again_from_the_swarm(self):
        man = self.store()
        a = man.files["a.bin"].sha256
        damaged = bytearray(read(self.p("store", "blobs", a)))
        damaged[0] ^= 0xFF
        write(self.p("game", ".deltapatcher", "cache", a), bytes(damaged))  # the right size, the wrong bytes
        store, asked = self.p("store"), []

        class Swarm(object):
            def fetch(self, src, need, cdir, progress, cancel, raw=None, signer=None):
                asked.append(sorted(need))
                for sha in need:
                    write(os.path.join(cdir, sha), read(os.path.join(store, "blobs", sha)))

            def needs_keep(self, raw):
                return False

            def keep(self, src, raw, sig, signer):
                pass

        class NoAddress(Source):                                           # a project with no address: no other source
            def fetch_blob(self, sha, dest, size, progress=None, cancel=None):
                raise SourceError("no peer had it")
        client.update(store, self.p("game"), torrent=Swarm(), source=NoAddress(store))
        self.assertEqual(asked, [sorted(man.blobs())])
        self.assertEqual(snapshot(self.p("game")), snapshot(self.p("src")))

    def test_the_address_speed_is_measured_from_downloads_long_enough_to_tell(self):
        from deltapatcher.torrent import session
        self.assertIsNone(session._speed([]))
        self.assertIsNone(session._speed([(4, 0.01)]))                    # a few bytes say nothing of it
        self.assertEqual(session._speed([(4, 0.01), (1 << 20, 0.5), (1 << 20, 1.5)]), (2 << 20) / 2.0)

    def test_a_lookup_that_hangs_is_given_up(self):
        from deltapatcher.torrent import session

        def hang(*a, **k):
            time.sleep(3)
            return []
        with mock.patch.object(session.socket, "getaddrinfo", side_effect=hang):
            t0 = time.time()
            self.assertEqual(session.resolve(["slow.example.com:1"], time.time() + 0.5), [])
            self.assertLess(time.time() - t0, 2.0)

    def test_digits_are_ascii_digits(self):
        self.assertEqual(torrent.seed_problem("a.example.com:\u00b9\u00b2"), "a port from 1 to 65535 after the :")
        with self.assertRaises(presets.PresetError):
            presets.check_data({"format": presets.FORMAT, "version": 3, "made": "2026-11-24T00:00:00Z",
                                "updates": False, "projects": [{"title": "Only", "trust_key": "ed25519:x",
                                                                "torrent": {"id": TID, "name": "only",
                                                                            "seeds": ["a.example.com:\u00b2"]}}]})
        d = pause.folder_dir(self.p("game"))
        os.makedirs(d)
        write(os.path.join(d, "\u00b2\u00b3.seeding"), "{}")
        self.assertEqual(pause.seeders(self.p("game")), [])


class Untouched(Base):
    def test_a_project_not_shared_by_torrent_updates_as_before(self):
        write(self.p("src", "a.txt"), "a\n")
        publish.publish(self.p("src"), self.p("store"))
        before = snapshot(self.p("src"))
        rc, ev = run_json(["update", self.p("game"), "--source", self.p("store")])
        self.assertEqual(rc, 0, ev[-1])
        self.assertNotIn("torrent", ev[-1]["data"])
        self.assertEqual(snapshot(self.p("game")), before)
        self.assertFalse(os.path.exists(self.p("game", ".deltapatcher", "release")))


if __name__ == "__main__":
    unittest.main()
