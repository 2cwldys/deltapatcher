"""Torrents beside HTTP with libtorrent, on 127.0.0.x only (never the public network): the settings every session
is made with, a player downloading only what it lacks from a seeding store and its web seed at once, a project with
no address found through its release item in a DHT of local sessions, a seed that never offers a changed or
protected file, an update while another process seeds the folder, a seed run that ends within two seconds, a peer's
damaged data never used, and a player seeding from a copy of its release (the default) and from its folder.

They skip without libtorrent, unless DP_REQUIRE_TORRENT=1 (CI): then a missing libtorrent fails them."""

import contextlib
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, snapshot, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import USER_AGENT, cli, config, presets, projects, serve, signing, torrent  # noqa: E402
from deltapatcher.manifest import Manifest  # noqa: E402
from deltapatcher.torrent import item  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = os.environ.get("DP_REQUIRE_TORRENT") == "1"


def timing(what, seconds):
    """Say a time a test measured, on standard error, when DP_TIMINGS=1 (how much margin a guarantee has here)."""
    if os.environ.get("DP_TIMINGS") == "1":
        sys.stderr.write("timing: %s %.3f s\n" % (what, seconds))


def run_json(argv, stdin=""):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(sys, "stdin", io.StringIO(stdin)):
        rc = cli.main(["--json", "--no-log"] + argv)
    return rc, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def free_port(ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((ip, 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextlib.contextmanager
def test_ip(ip):
    old = os.environ.get(torrent.TEST_IP_ENV)
    os.environ[torrent.TEST_IP_ENV] = ip
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(torrent.TEST_IP_ENV, None)
        else:
            os.environ[torrent.TEST_IP_ENV] = old


class Slow(object):
    """A web server's output at `rate` bytes a second, counted."""

    def __init__(self, f, rate, counted):
        self.f, self.rate, self.counted = f, rate, counted

    def write(self, b):
        self.counted.append(len(b))
        time.sleep(len(b) / float(self.rate))
        return self.f.write(b)

    def __getattr__(self, k):
        return getattr(self.f, k)


def slow_server(store, rate, log):
    """deltapatcher serve's handler for `store`, sending at `rate`; log gets (path, Range header) of each request."""
    base = serve.make_handler(store)

    class H(base):
        def setup(self):
            base.setup(self)
            self.wfile = Slow(self.wfile, rate, log.setdefault("bytes", []))

        def _send(self, head):
            log.setdefault("requests", []).append((self.path, self.headers.get("Range")))
            return base._send(self, head)
    srv = serve.Server(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Base(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        if not torrent.available():
            if REQUIRED:
                self.fail("DP_REQUIRE_TORRENT=1, and %s" % torrent.NO_SUPPORT)
            self.skipTest(torrent.NO_SUPPORT)
        if not signing.can_sign():
            if REQUIRED:                                    # these tests sign releases: never skipped in CI either
                self.fail("DP_REQUIRE_TORRENT=1, and cryptography is not installed")
            self.skipTest("needs cryptography")
        from deltapatcher.torrent import session
        self.S = session
        self.threads = []
        self.cleanups = []

    def tearDown(self):
        for f in reversed(self.cleanups):
            try:
                f()
            except Exception:
                pass
        TempCase.tearDown(self)

    def key(self):
        path = self.p("keys", "sign.pem")
        if not os.path.exists(path):
            signing.generate_key(path, "")
        return path

    def publisher(self, name="Mod", files=None, **values):
        """A publisher's project shared by torrent, signed, releasing src/ into store/; published once."""
        p = projects.new(name)
        for rel, data in (files or {"readme.txt": b"one\n", "data/pak0.bin": os.urandom(600000),
                                    "data/pak1.bin": os.urandom(900000), "maps/a.bsp": os.urandom(300000)}).items():
            write(self.p("src", *rel.split("/")), data)
        base = {"publish_src": self.p("src"), "store_dir": self.p("store"), "sign": True, "signing_key": self.key(),
                "name": name, "version": "1.0"}
        base.update(values)
        config.save(base, p)
        rc, ev = run_json(["--project", name, "config", "set", "torrent=yes"])
        self.assertEqual(rc, 0, ev[-1])
        self.publish(name)
        return p

    def publish(self, name, version=None):
        argv = ["--project", name, "publish", "--sign"] + (["--version", version] if version else [])
        rc, ev = run_json(argv)
        self.assertEqual(rc, 0, ev[-1])
        return ev[-1]["data"]

    def slow_margin(self):
        """An update that asks a seed run to pause waits for its answer 30 seconds instead of 10 (pause.WAIT): it
        comes in well under a second here; a slow computer's margin."""
        from deltapatcher.torrent import pause
        return mock.patch.object(pause, "WAIT", 30.0)

    def seed_run(self, project, ip, port=0, upload_kbps=0):
        """The engine's seed run of one project, in a thread, listening on `ip`; stopped at the end of the test."""
        from deltapatcher.torrent import seed
        general = dict(config.load(), torrent_port=port, seed_upload_kbps=upload_kbps)
        stop = threading.Event()
        said = []
        run = seed.Run([(project, config.load(project))], general, lambda t, s=None: said.append(t),
                       lambda rows: said.append(rows), stop, ip=ip)
        t = threading.Thread(target=run.run, daemon=True)
        t.start()
        end = time.time() + 60                              # a slow computer's margin: it seeds in a second here
        while time.time() < end and (run.swarm is None or not any(s.handle is not None for s in run.seeded)):
            time.sleep(0.05)
        self.cleanups.append(lambda: (stop.set(), t.join(10)))
        return run, said

    def player(self, name, source_url, publisher_cfg, install, **values):
        """A player's project of the publisher's, as its project file sets it up."""
        p = projects.new(name)
        key = signing.public_key_text(signing.load_key(self.key()))
        t = torrent.info(publisher_cfg)
        cfg = {"source_url": source_url, "install_dir": install, "trust_key": key}
        cfg.update(torrent.settings_of({"id": t["id"], "name": t["name"], "seeds": t["seeds"]}))
        cfg.update(values)
        config.save(cfg, p)
        return p

    def setup_player(self, copy=True, **values):
        """A publisher's release (one file protected) and a player's project updated to it; copy=False: the player
        seeds straight from its folder (seed_copy = no)."""
        pub = self.publisher(torrent_name="mod", protect="data/user.cfg",
                             files={"readme.txt": b"one\n", "data/pak0.bin": os.urandom(600000),
                                    "data/pak1.bin": os.urandom(900000), "data/user.cfg": b"defaults\n" * 100,
                                    "maps/a.bsp": os.urandom(300000)})
        srv, _t = serve.serve_in_background(self.p("store"), "127.0.0.1", 0)
        self.cleanups.append(lambda: (srv.shutdown(), srv.server_close()))
        address = "http://127.0.0.1:%d/mod/" % srv.server_address[1]
        if not copy:
            values["seed_copy"] = False
        p = self.player("Player", address, config.load(pub), self.p("game"), **values)
        rc, ev = run_json(["--project", "Player", "update"])
        self.assertEqual(rc, 0, ev[-1])
        return pub, p

    def copy_dir(self, p):
        """Where the seed copy of the project `p` is, as the settings say now."""
        from deltapatcher.torrent import seedcopy
        return seedcopy.place(config.load(p), config.load())

    def downloader(self, ip, ti_bytes, peer, dest, seconds=60, quiet=5.0):
        """A plain BitTorrent peer (libtorrent, its own settings: loopback only) that downloads everything it can
        from `peer`; {torrent path: bytes it got}. It ends when it has everything, or nothing more came for `quiet`
        seconds after something did (a seed holds some files back), or after `seconds`."""
        L = self.S.lt()
        ses = L.session(self.S.pack("%s:0" % ip, ip=ip))
        try:
            ti = L.torrent_info(ti_bytes)
            h = ses.add_torrent({"ti": ti, "save_path": dest})
            h.connect_peer(peer)
            end = time.time() + seconds
            last, moved = -1, time.time()
            while time.time() < end and not h.status().is_seeding:
                ses.pop_alerts()
                time.sleep(0.1)
                done = h.status().total_wanted_done
                if done != last:
                    last, moved = done, time.time()
                elif done > 0 and time.time() - moved > quiet:
                    break
            time.sleep(0.5)
            prog = h.file_progress(flags=1)
            out = {}
            for f in self.S.files_of(ti):
                if not f.pad:
                    out[f.path] = prog[f.index]
            ses.remove_torrent(h)
            return out
        finally:
            del ses


class Settings(Base):
    def test_every_session_is_made_with_safe_settings(self):
        with test_ip(""):
            os.environ.pop(torrent.TEST_IP_ENV, None)
            sw = self.S.Swarm("loopback")
            try:
                got = sw.ses.get_settings()
            finally:
                sw.close()
        major = self.S.fingerprint()
        expect = {"listen_interfaces": "127.0.0.1:0", "outgoing_interfaces": "", "enable_dht": False,
                  "dht_bootstrap_nodes": "", "enable_lsd": False, "enable_upnp": False, "enable_natpmp": False,
                  "enable_outgoing_utp": False, "enable_incoming_utp": False, "user_agent": USER_AGENT,
                  "handshake_client_version": USER_AGENT, "peer_fingerprint": major, "out_enc_policy": 1,
                  "in_enc_policy": 1, "allowed_enc_level": 3, "upload_rate_limit": 0, "dht_restrict_routing_ips": True,
                  "dht_restrict_search_ips": True, "anonymous_mode": False}
        for k, v in expect.items():
            self.assertEqual(got[k], v, k)
        self.assertTrue(major.startswith("-DP") and len(major) == 8)
        with test_ip(""):
            os.environ.pop(torrent.TEST_IP_ENV, None)
            # every interface (a seed run, a project with no address): read from the pack, never made on this computer
            p = self.S.pack(self.S.listen_on("all", 6881), dht=True, dht_read_only=False, lsd=True, upload_kbps=100)
            self.assertEqual(p["listen_interfaces"], "0.0.0.0:6881,[::]:6881")
            self.assertEqual((p["enable_dht"], p["dht_read_only"], p["dht_bootstrap_nodes"], p["enable_lsd"]),
                             (True, False, "", True))
            self.assertEqual((p["enable_upnp"], p["enable_natpmp"], p["upload_rate_limit"]), (False, False, 102400))
            p = self.S.pack(self.S.listen_on("all", 0), dht=True, port_mapping=True)
            self.assertEqual((p["enable_upnp"], p["enable_natpmp"], p["dht_read_only"]), (True, True, True))
            self.assertFalse(self.S.pack("127.0.0.1:0", port_mapping=True)["enable_upnp"])     # never for 127.0.0.1
            # local discovery listens on every interface (UDP 6771) whatever the session does: never at 127.0.0.1 or in
            # a test on one computer
            self.assertFalse(self.S.pack("127.0.0.1:0", lsd=True)["enable_lsd"])
            self.assertFalse(self.S.pack("127.0.0.9:0", lsd=True, ip="127.0.0.9")["enable_lsd"])

    def test_the_seed_run_builds_its_session_safe(self):
        made = []

        class Stop(Exception):
            pass

        def capture(settings):
            made.append(settings)
            raise Stop()
        name = "Only"
        p = self.publisher(name)
        config.save({"seed": True, "torrent_port_mapping": False}, p)
        with test_ip(""):
            os.environ.pop(torrent.TEST_IP_ENV, None)
            with mock.patch.object(self.S, "make_session", side_effect=capture), \
                    mock.patch.object(cli, "start_control_reader"):      # no end of input: the run is not stopped
                rc, ev = run_json(["seed", "--all"])
        self.assertEqual(rc, 1)
        s = made[0]
        self.assertEqual(s["listen_interfaces"], "0.0.0.0:6881,[::]:6881")
        self.assertEqual((s["enable_lsd"], s["enable_dht"], s["dht_bootstrap_nodes"], s["enable_upnp"],
                          s["enable_natpmp"]), (True, True, "", False, False))     # no address: the DHT is on

    @unittest.skipIf(os.name == "nt", "Windows has no limit of open files to raise")
    def test_a_session_has_enough_open_files(self):
        import resource
        sw = self.S.Swarm("loopback", ip="127.0.0.22")
        sw.close()
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = 4096 if hard == resource.RLIM_INFINITY else min(hard, 4096)
        self.assertTrue(soft == resource.RLIM_INFINITY or soft >= want, (soft, hard))

    def test_the_self_test(self):
        rc, ev = run_json(["version", "--torrent"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertTrue(ev[-1]["data"]["torrent"])
        self.assertTrue(ev[-1]["data"]["torrent_test"]["passed"], ev[-1]["data"]["torrent_test"])

    def test_libtorrent_agrees_with_the_torrent_made_here(self):
        self.publisher()
        data = read(self.p("store", "release.torrent"))
        ti, files = self.S.read_torrent(data, "release")
        it = item.read(read(self.p("store", "release.item")), signing.public_key_text(signing.load_key(self.key())),
                       config.load(projects.resolve("Mod"))["torrent_id"])
        self.assertEqual(self.S.infohash_of(ti), it.ih)
        sw = self.S.Swarm("loopback", ip="127.0.0.21")
        try:
            from deltapatcher.torrent import folder
            m = folder.for_store(files, ti.num_pieces(), self.p("store"), folder.hash_cache(self.p("store")))
            h = sw.add(ti, save_path=self.p("store"), mapping=m)
            h.force_recheck()
            end = time.time() + 60
            while time.time() < end and not h.status().is_seeding:
                sw.pump(100)
            self.assertTrue(h.status().is_seeding)                  # libtorrent's own check of every piece
        finally:
            sw.close()
        # a tracker or web seed in a torrent read from anywhere is dropped
        from deltapatcher.torrent import bencode
        doc = bencode.decode(data)
        doc[b"announce"] = b"http://tracker.example.com/announce"
        ti2, _f = self.S.read_torrent(bencode.encode(doc), "release")
        self.assertEqual(list(ti2.trackers()), [])


class Download(Base):
    def test_only_what_it_lacks_from_a_seed_and_from_the_address_at_once(self):
        self.only_what_it_lacks(True, "127.0.0.31", "127.0.0.32")

    def test_only_what_it_lacks_from_a_seed_of_its_folder_and_from_the_address_at_once(self):
        self.only_what_it_lacks(False, "127.0.0.33", "127.0.0.34")

    def only_what_it_lacks(self, seed_copy, seed_ip, update_ip):
        """A's seed (from a copy, seed_copy, or from its folder, on seed_ip) and the slow address at once for B's
        update (on update_ip)."""
        pub = self.publisher(torrent_name="mod")
        log = {}
        srv = slow_server(self.p("store"), 400000, log)
        self.cleanups.append(lambda: (srv.shutdown(), srv.server_close()))
        address = "http://127.0.0.1:%d/mod/" % srv.server_address[1]
        a = self.player("A", address, config.load(pub), self.p("a"), seed_copy=seed_copy)
        b = self.player("B", address, config.load(pub), self.p("b"))
        for name in ("A", "B"):                                                   # release 1, before any seed
            rc, ev = run_json(["--project", name, "update"])
            self.assertEqual(rc, 0, ev[-1])
        self.assertTrue(os.path.isfile(self.p("b", ".deltapatcher", "release", "release.torrent")))
        write(self.p("src", "data", "pak1.bin"), os.urandom(2500000))            # release 2 changes one big file
        write(self.p("src", "maps", "b.bsp"), os.urandom(1500000))               # and adds one
        self.publish("Mod", "2.0")
        rc, ev = run_json(["--project", "A", "update"])
        self.assertEqual(rc, 0, ev[-1])
        write(self.p("a", "maps", "b.bsp"), os.urandom(1500000))                 # A changed it: A never offers it
        run, _said = self.seed_run(a, seed_ip, upload_kbps=350)
        self.assertEqual(run.seeded[0].kind, "copy" if seed_copy else "folder")
        config.save({"seeds": "%s:%d" % (seed_ip, run.swarm.port)}, b)
        log.clear()
        t0 = time.time()
        # the race against a slow swarm is SlowSwarm's: here the seed's 350 KB/s may dip on a busy computer
        with test_ip(update_ip), mock.patch.object(self.S, "RATE_FLOOR", 32 * 1024):
            rc, ev = run_json(["--project", "B", "update"])
        timing("an update of 4 MB, a seed at 350 KB/s and the address at 400 KB/s at once", time.time() - t0)
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(snapshot(self.p("b")), snapshot(self.p("src")))
        texts = [e.get("text", "") for e in ev if e["event"] == "log"]
        self.assertIn("by torrent: 1 of 2 blobs; from the address at the same time: 1 (no peer had them)", texts)
        self.assertFalse([t for t in texts if "made no progress" in t], texts)   # nothing waited for the swarm
        man = Manifest.from_json(read(self.p("store", "manifest.json")))
        asked = [path.rsplit("/", 1)[-1] for path, _rng in log.get("requests", []) if "/blobs/" in path]
        self.assertEqual(asked, [man.files["maps/b.bsp"].sha256])               # only what no peer had
        sent = sum(s.status()["sent"] for s in run.seeded)
        self.assertGreaterEqual(sent, 2500000)                                    # the rest came from the seed
        self.assertLess(sent + sum(log.get("bytes", [])), 4600000)               # and nothing twice

    def test_a_peers_damaged_data_is_never_used(self):
        pub = self.publisher(torrent_name="mod")
        srv, _t = serve.serve_in_background(self.p("store"), "127.0.0.1", 0)
        self.cleanups.append(lambda: (srv.shutdown(), srv.server_close()))
        address = "http://127.0.0.1:%d/" % srv.server_address[1]                 # a store at the root: no web seed
        # a rogue peer offers the release with one blob damaged, never checked
        rogue = self.p("rogue")
        shutil.copytree(self.p("store"), rogue)
        man = Manifest.from_json(read(self.p("store", "manifest.json")))
        bad = man.files["data/pak1.bin"].sha256
        data = bytearray(read(os.path.join(rogue, "blobs", bad)))
        data[1000:1100] = b"\0" * 100
        write(os.path.join(rogue, "blobs", bad), bytes(data))
        L = self.S.lt()
        ti, files = self.S.read_torrent(read(self.p("store", "release.torrent")), "mod")
        from deltapatcher.torrent import folder
        m = folder.Mapping(len(files), ti.num_pieces())
        for f in files:
            if not f.pad:
                m.offer(f, f.path, None)
        sw = self.S.Swarm("loopback", ip="127.0.0.36")
        self.cleanups.append(sw.close)
        sw.add(ti, save_path=rogue, mapping=m, seed=True)
        self.player("Player", address, config.load(pub), self.p("game"), seeds="127.0.0.36:%d" % sw.port)
        with test_ip("127.0.0.37"), mock.patch.object(self.S, "STALL", 4.0):
            rc, ev = run_json(["--project", "Player", "update"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(snapshot(self.p("game")), snapshot(self.p("src")))      # the damaged blob came from the address
        texts = " ".join(e.get("text", "") for e in ev if e["event"] == "log")
        self.assertIn("fails its hash", texts)
        del L


class NoAddress(Base):
    def test_a_player_finds_the_release_through_the_dht_item(self):
        ip = "127.0.0.41"
        port = free_port(ip)
        pub = self.publisher("Only", public_url="")
        config.save({"seeds": "%s:%d" % (ip, port)}, pub)
        self.publish("Only")
        self.assertTrue(torrent.torrent_only(config.load(pub)))
        rc, ev = run_json(["project", "export", "Only", "--out", self.p("only.deltapatch"), "--sign"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertNotIn("source_url", presets.read(self.p("only.deltapatch"))["projects"][0])
        run, said = self.seed_run(pub, ip, port=port)
        self.assertEqual(run.swarm.port, port)
        nodes = []
        for n in (42, 43):                                    # a DHT of a few sessions, all on 127.0.0.x
            other = "127.0.0.%d" % n
            ses = self.S.make_session(self.S.pack("%s:0" % other, dht=True, dht_read_only=False, ip=other))
            ses.add_dht_node((ip, port))
            nodes.append(ses)
        self.cleanups.append(lambda: nodes.clear())
        end = time.time() + 90                                  # a few seconds here; a slow computer's margin
        while time.time() < end and not any("is in the DHT" in str(x) for x in said):
            time.sleep(0.2)
        self.assertTrue(any("is in the DHT" in str(x) for x in said), said)
        main = config.path()
        os.environ["DELTAPATCHER_CONFIG"] = self.p("player", "deltapatcher.ini")
        self.addCleanup(lambda: os.environ.__setitem__("DELTAPATCHER_CONFIG", main))
        rc, ev = run_json(["project", "import", self.p("only.deltapatch"), "--folder", self.p("game"), "--yes"])
        self.assertEqual(rc, 0, ev[-1])
        with test_ip("127.0.0.45"):
            rc, ev = run_json(["--project", "Only", "update"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(snapshot(self.p("game")), snapshot(self.p("src")))
        it_key = config.load(projects.resolve("Only"))["trust_key"]
        it = item.read(read(self.p("game", ".deltapatcher", "release", "release.item")), it_key,
                       config.load(projects.resolve("Only"))["torrent_id"])
        kept = torrent.Kept(self.p("game", ".deltapatcher"), it.salt.decode())
        self.assertEqual(kept.seq, it.seq)
        # a newer release reaches it the same way; one numbered lower than accepted is refused
        os.environ["DELTAPATCHER_CONFIG"] = main
        write(self.p("src", "readme.txt"), "two\n")
        self.publish("Only", "2.0")
        newer = item.read(read(self.p("store", "release.item")), it_key, it.salt.decode())
        self.assertGreater(newer.seq, it.seq)
        end = time.time() + 90
        while time.time() < end and not any("(number %d) is in the DHT" % newer.seq in str(x) for x in said):
            time.sleep(0.2)
        self.assertTrue(any("(number %d) is in the DHT" % newer.seq in str(x) for x in said), said)
        os.environ["DELTAPATCHER_CONFIG"] = self.p("player", "deltapatcher.ini")
        with test_ip("127.0.0.45"):
            rc, ev = run_json(["--project", "Only", "update"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(read(self.p("game", "readme.txt")), b"two\n")
        state = self.p("game", ".deltapatcher")
        k = torrent.Kept(state, it.salt.decode())
        k.seq += 1000
        k.save()
        with test_ip("127.0.0.45"), mock.patch.object(self.S, "FIND", 20.0):
            rc, ev = run_json(["--project", "Only", "check"])
        self.assertEqual((rc, ev[-1]["event"]), (1, "error"))
        texts = " ".join(e.get("text", "") for e in ev if e["event"] == "log")
        self.assertIn("was refused: this folder accepted", texts)


class Seeding(Base):
    def test_a_player_seeds_from_its_folder_and_never_a_changed_file(self):
        _pub, p = self.setup_player(copy=False)
        man = Manifest.from_json(read(self.p("game", ".deltapatcher", "release", "manifest.json")))
        data = bytearray(read(self.p("game", "data", "pak0.bin")))
        data[10] ^= 0xFF
        write(self.p("game", "data", "pak0.bin"), bytes(data))                   # changed here, same size
        before = snapshot(self.p("game"))
        run, _said = self.seed_run(p, "127.0.0.51")
        got = self.downloader("127.0.0.52", read(self.p("game", ".deltapatcher", "release", "release.torrent")),
                              ("127.0.0.51", run.swarm.port), self.p("peer"))
        sizes = {"blobs/" + e.sha256: e.size for e in man.files.values()}
        held = {"blobs/" + man.files["data/pak0.bin"].sha256, "blobs/" + man.files["data/user.cfg"].sha256}
        for path, size in sizes.items():
            self.assertEqual(got[path], 0 if path in held else size, path)       # changed and protected: never
        self.assertEqual(got["manifest.json"], len(read(self.p("store", "manifest.json"))))
        self.assertEqual(snapshot(self.p("game")), before)                        # nothing written into the folder
        for f in run.seeded:
            self.assertEqual(f.state, "seeding")


class TwoProcesses(Base):
    def seed_process(self, ip):
        env = dict(os.environ, PYTHONPATH=ROOT, PYTHONUNBUFFERED="1")
        env[torrent.TEST_IP_ENV] = ip
        proc = subprocess.Popen([sys.executable, "-m", "deltapatcher", "--json", "--no-log", "--project", "Player",
                                 "seed"], cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        lines = []

        def read_out():
            for line in proc.stdout:
                lines.append(line)
        self.reader = threading.Thread(target=read_out, daemon=True)
        self.reader.start()

        def kill():
            if proc.poll() is None:
                proc.kill()
                proc.wait(10)
            for f in (proc.stdin, proc.stdout):
                try:
                    f.close()
                except (OSError, ValueError):
                    pass
        self.cleanups.append(kill)
        return proc, lines

    def events(self, lines):
        out = []
        for line in list(lines):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def wait_for(self, lines, pred, seconds=60):
        end = time.time() + seconds
        while time.time() < end:
            if any(pred(e) for e in self.events(lines)):
                return True
            time.sleep(0.1)
        return False

    def ended(self, proc, lines, t0, what):
        """The seed run's end, after its stop was asked at t0: the run's own shutdown, from the stop it read to its
        session closed (its result's stopped_in), is the 2 second guarantee; the whole process, with the start and
        end of Python itself, gets a slow computer's margin."""
        proc.wait(30)
        took = time.time() - t0
        self.reader.join(10)
        result = [e for e in self.events(lines) if e.get("event") == "result"]
        self.assertTrue(result, "".join(lines[-20:]))
        stopped_in = result[-1]["data"]["stopped_in"]
        timing("%s: the run's own shutdown" % what, stopped_in)
        timing("%s: the whole process" % what, took)
        self.assertLess(stopped_in, 2.0)
        self.assertLess(took, 10.0)
        self.assertEqual(proc.returncode, 0)
        return result[-1]

    def test_an_update_while_another_process_seeds_the_folder(self):
        pub, p = self.setup_player(copy=False)
        proc, lines = self.seed_process("127.0.0.61")
        self.assertTrue(self.wait_for(lines, lambda e: e.get("event") == "seeding" and e.get("state") == "seeding"),
                        "".join(lines))
        port = None
        for e in self.events(lines):
            if e.get("event") == "log" and e["text"].startswith("seeding on port "):
                port = int(e["text"].split()[3])
        got = self.downloader("127.0.0.62", read(self.p("game", ".deltapatcher", "release", "release.torrent")),
                              ("127.0.0.61", port), self.p("peer"))
        man = Manifest.from_json(read(self.p("game", ".deltapatcher", "release", "manifest.json")))
        mine = "blobs/" + man.files["data/user.cfg"].sha256
        self.assertTrue(all(v > 0 for k, v in got.items() if k.startswith("blobs/") and k != mine), got)
        write(self.p("src", "data", "pak0.bin"), os.urandom(600000))              # files the seed run has open
        write(self.p("src", "maps", "a.bsp"), os.urandom(300000))
        self.publish("Mod", "2.0")
        with self.slow_margin():
            rc, ev = run_json(["--project", "Player", "update"])
        self.assertEqual(rc, 0, ev[-1])
        texts = [e.get("text", "") for e in ev if e["event"] == "log"]
        self.assertIn("seeding of this folder pauses while it changes", texts)
        self.assertEqual(read(self.p("game", "maps", "a.bsp")), read(self.p("src", "maps", "a.bsp")))
        self.assertTrue(self.wait_for(lines, lambda e: e.get("event") == "seeding" and e.get("release") == "Mod 2.0"),
                        "".join(lines[-20:]))                                       # and it goes on, with the new release
        t0 = time.time()
        proc.stdin.write("cancel\n")
        proc.stdin.flush()
        self.ended(proc, lines, t0, "cancel")

    def test_a_seed_run_ends_within_two_seconds_when_its_input_ends(self):
        self.setup_player(copy=False)
        proc, lines = self.seed_process("127.0.0.65")
        self.assertTrue(self.wait_for(lines, lambda e: e.get("event") == "seeding"), "".join(lines))
        t0 = time.time()
        proc.stdin.close()
        self.ended(proc, lines, t0, "end of input")
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(config.path()), "seeding.json")))
        self.assertEqual(self.events(lines)[-1]["event"], "result")

    def test_a_seed_run_from_a_copy_ends_within_two_seconds_when_its_input_ends(self):
        self.setup_player()
        proc, lines = self.seed_process("127.0.0.23")
        self.assertTrue(self.wait_for(lines, lambda e: e.get("event") == "seeding" and e.get("kind") == "copy" and
                                      e.get("state") == "seeding"), "".join(lines))
        t0 = time.time()
        proc.stdin.close()
        self.ended(proc, lines, t0, "end of input, seeding from a copy")
        self.assertEqual(self.events(lines)[-1]["event"], "result")

    def test_an_update_while_another_process_seeds_the_folder_from_a_copy(self):
        _pub, p = self.setup_player()
        proc, lines = self.seed_process("127.0.0.24")
        self.assertTrue(self.wait_for(lines, lambda e: e.get("event") == "seeding" and e.get("kind") == "copy" and
                                      e.get("state") == "seeding"), "".join(lines))
        port = None
        for e in self.events(lines):
            if e.get("event") == "log" and e["text"].startswith("seeding on port "):
                port = int(e["text"].split()[3])
        copy = self.copy_dir(p)
        got = self.downloader("127.0.0.25", read(os.path.join(copy, "release.torrent")), ("127.0.0.24", port),
                              self.p("peer"))
        man = Manifest.from_json(read(self.p("game", ".deltapatcher", "release", "manifest.json")))
        mine = "blobs/" + man.files["data/user.cfg"].sha256
        self.assertTrue(all(v > 0 for k, v in got.items() if k.startswith("blobs/") and k != mine), got)
        write(self.p("src", "data", "pak0.bin"), os.urandom(600000))              # files the copy has as they were
        write(self.p("src", "maps", "a.bsp"), os.urandom(300000))
        self.publish("Mod", "2.0")
        with self.slow_margin():
            t0 = time.time()
            rc, ev = run_json(["--project", "Player", "update"])
            took = time.time() - t0
        self.assertEqual(rc, 0, ev[-1])
        timing("an update while another process seeds the folder from a copy", took)
        texts = [e.get("text", "") for e in ev if e["event"] == "log"]
        self.assertIn("seeding of this folder goes on from its copy", texts)
        self.assertNotIn("seeding of this folder pauses while it changes", texts)
        self.assertLess(took, 15.0)                     # a pause not answered would hold it 30 seconds (slow_margin)
        self.assertEqual(read(self.p("game", "maps", "a.bsp")), read(self.p("src", "maps", "a.bsp")))
        self.assertTrue(self.wait_for(lines, lambda e: e.get("event") == "seeding" and e.get("release") == "Mod 2.0"),
                        "".join(lines[-20:]))                                       # the copy takes the new release
        new = Manifest.from_json(read(self.p("store", "manifest.json")))
        self.assertEqual(sorted(os.listdir(os.path.join(copy, "blobs"))),
                         sorted(set(new.blobs()) - {new.files["data/user.cfg"].sha256}))
        t0 = time.time()
        proc.stdin.write("cancel\n")
        proc.stdin.flush()
        self.ended(proc, lines, t0, "cancel, seeding from a copy")


class Loopback(Base):
    def test_an_update_that_listens_on_127_0_0_1_only_downloads_from_a_seed_on_127_0_0_2(self):
        """The real download path of a project with an address: a session on 127.0.0.1:0 with uTP off (no test
        address), connecting out to a seed elsewhere."""
        pub = self.publisher(torrent_name="mod")
        ti, files = self.S.read_torrent(read(self.p("store", "release.torrent")), "mod")
        from deltapatcher.torrent import folder
        seed = self.S.Swarm("loopback", ip="127.0.0.2")
        self.cleanups.append(seed.close)
        seed.add(ti, save_path=self.p("store"), seed=True,
                 mapping=folder.for_store(files, ti.num_pieces(), self.p("store"), folder.hash_cache(self.p("store"))))
        want = Manifest.from_json(read(self.p("store", "manifest.json"))).blobs()
        kept = torrent.Kept(self.p("game", ".deltapatcher"), torrent.info(config.load(pub))["id"])
        with test_ip(""):
            os.environ.pop(torrent.TEST_IP_ENV, None)
            sw = self.S.Swarm("loopback")
            try:
                got = sw.ses.get_settings()
                self.assertEqual((got["listen_interfaces"], got["enable_outgoing_utp"], got["outgoing_interfaces"]),
                                 ("127.0.0.1:0", False, ""))
                brought, by_none, by_race = self.S.get_blobs(sw, ti, files, want, self.p("cache"),
                                                             [("127.0.0.2", seed.port)], lambda s: None,
                                                             lambda d, t, l: None, None, kept)
            finally:
                sw.close()
        self.assertEqual((brought, by_none, by_race), (want, [], []))
        for sha in want:
            self.assertEqual(hashlib.sha256(read(self.p("cache", sha))).hexdigest(), sha)


class Switching(Base):
    def run_of(self, p, ip):
        """A seed run of one project with its session, driven by the test itself (no loop)."""
        from deltapatcher.torrent import seed
        said = []
        run = seed.Run([(p, config.load(p))], dict(config.load(), torrent_port=0), lambda t, s=None: said.append(t),
                       lambda rows: None, threading.Event(), ip=ip)
        run.swarm = self.S.Swarm("all", ip=ip)
        self.cleanups.append(run.swarm.close)
        return run, said

    def store_files(self):
        raw = read(self.p("store", "manifest.json"))
        sha = hashlib.sha256(raw).hexdigest()
        return {"manifest.json": raw, "sigs/%s.sig" % sha: read(self.p("store", "sigs", sha + ".sig")),
                "release.torrent": read(self.p("store", "release.torrent")),
                "release.item": read(self.p("store", "release.item"))}

    def test_a_second_switch_while_next_is_seeded(self):
        _pub, p = self.setup_player(copy=False)
        write(self.p("src", "readme.txt"), "two\n")
        self.publish("Mod", "2.0")
        v2 = self.store_files()
        write(self.p("src", "readme.txt"), "three\n")
        self.publish("Mod", "3.0")
        v3 = self.store_files()
        run, said = self.run_of(p, "127.0.0.71")
        s = run.seeded[0]
        self.assertTrue(s.start(), s.why)
        s._switch(v2)
        self.assertEqual((s.state, s.title_of_release), ("seeding", "Mod 2.0"), said)
        got = self.downloader("127.0.0.72", v2["release.torrent"], ("127.0.0.71", run.swarm.port), self.p("peer"))
        self.assertEqual(got["manifest.json"], len(v2["manifest.json"]))          # next/'s manifest is open now
        s._switch(v3)
        self.assertEqual((s.state, s.title_of_release), ("seeding", "Mod 3.0"), said)
        nxt = self.p("game", ".deltapatcher", "seeding", "next", "manifest.json")
        self.assertEqual(read(nxt), v3["manifest.json"])


class Renamed(Base):
    def test_a_player_with_no_address_after_the_publisher_renames_the_torrent(self):
        self.renamed(True, "127.0.0.75")

    def test_a_player_seeding_from_its_folder_after_the_publisher_renames_the_torrent(self):
        self.renamed(False, "127.0.0.39")

    def renamed(self, seed_copy, ip):
        """The player's project seeds from a copy (seed_copy) or from its folder, on the address `ip`."""
        pub = self.publisher("Only", public_url="", torrent_name="only")
        config.save({"torrent_name": "renamed"}, pub)
        self.publish("Only", "2.0")
        tor = read(self.p("store", "release.torrent"))
        key = signing.public_key_text(signing.load_key(self.key()))
        tid = config.load(pub)["torrent_id"]
        it = item.read(read(self.p("store", "release.item")), key, tid)
        # the player's project still names it as its project file did: the signed item names the torrent
        src = self.S.TorrentSource({"id": tid, "name": "only", "seeds": []}, key, self.p("game"), {})
        src.item = it
        try:
            src._metadata(tor)
        finally:
            src.close()
        self.assertEqual(src.ti.name(), "renamed")
        with self.assertRaises(torrent.TorrentError):
            self.S.read_torrent(tor, "only")                    # an address's release.torrent is still checked by name
        # and it seeds what it keeps, under the new name
        raw = read(self.p("store", "manifest.json"))
        sha = hashlib.sha256(raw).hexdigest()
        for rel, data in (("manifest.json", raw), ("sigs/%s.sig" % sha, read(self.p("store", "sigs", sha + ".sig"))),
                          ("release.torrent", tor), ("release.item", read(self.p("store", "release.item")))):
            write(self.p("game2", ".deltapatcher", "release", *rel.split("/")), data)
        p = projects.new("Player")
        config.save({"install_dir": self.p("game2"), "trust_key": key, "torrent": True, "torrent_id": tid,
                     "torrent_name": "only", "seed_copy": seed_copy}, p)
        from deltapatcher.torrent import seed
        run = seed.Run([(p, config.load(p))], dict(config.load(), torrent_port=0), lambda t, s=None: None,
                       lambda rows: None, threading.Event(), ip=ip)
        run.swarm = self.S.Swarm("all", ip=ip)
        self.cleanups.append(run.swarm.close)
        self.assertTrue(run.seeded[0].start(), run.seeded[0].why)
        self.assertEqual(run.seeded[0].kind, "copy" if seed_copy else "folder")


class PauseRaces(Base):
    def test_a_pause_asked_while_the_files_are_looked_at(self):
        _pub, p = self.setup_player(copy=False)
        from deltapatcher.torrent import folder, pause, seed
        run = seed.Run([(p, config.load(p))], dict(config.load(), torrent_port=0), lambda t, s=None: None,
                       lambda rows: None, threading.Event(), ip="127.0.0.81")
        run.swarm = self.S.Swarm("all", ip="127.0.0.81")
        self.cleanups.append(run.swarm.close)
        s = run.seeded[0]
        d = pause.folder_dir(self.p("game"))
        request = os.path.join(d, "%d.pause" % s.seat.pid)
        real, asked = folder.for_folder, []

        def looked_at(*a, **k):
            asked.append(time.time())
            pause._write(request, {"by": os.getpid(), "at": asked[0]})          # an update asks now
            return real(*a, **k)
        with mock.patch.object(folder, "for_folder", side_effect=looked_at):
            self.assertFalse(s.start())
        self.assertTrue(s.paused)
        self.assertEqual((s.handle, list(run.swarm.ses.get_torrents())), (None, []))
        self.assertTrue(pause._answered(d, s.seat.pid, asked[0]))
        self.assertIn(s.seat.pid, pause.seeders(self.p("game")))                 # it said so before looking
        os.remove(request)                                                        # the update ends
        s.tick_pause()
        self.assertEqual(s.state, "seeding")

    def test_a_rollback_while_the_folder_is_seeded(self):
        _pub, p = self.setup_player(copy=False)
        write(self.p("src", "data", "pak0.bin"), os.urandom(600000))
        self.publish("Mod", "2.0")
        rc, ev = run_json(["--project", "Player", "update"])
        self.assertEqual(rc, 0, ev[-1])
        run, _said = self.seed_run(p, "127.0.0.83")
        got = self.downloader("127.0.0.84", read(self.p("game", ".deltapatcher", "release", "release.torrent")),
                              ("127.0.0.83", run.swarm.port), self.p("peer"))
        self.assertTrue(any(v for k, v in got.items() if k.startswith("blobs/")), got)   # its files are open
        with self.slow_margin():
            rc, ev = run_json(["--project", "Player", "rollback"])
        self.assertEqual(rc, 0, ev[-1])
        texts = [e.get("text", "") for e in ev if e["event"] == "log"]
        self.assertIn("seeding of this folder pauses while it changes", texts)

    def test_a_publish_while_its_store_is_seeded(self):
        pub = self.publisher(torrent_name="mod")
        run, _said = self.seed_run(pub, "127.0.0.85")
        got = self.downloader("127.0.0.86", read(self.p("store", "release.torrent")), ("127.0.0.85", run.swarm.port),
                              self.p("peer"))
        self.assertTrue(got.get("manifest.json"), got)                           # the store's manifest is open
        write(self.p("src", "readme.txt"), "two\n")
        with self.slow_margin():
            rc, ev = run_json(["--project", "Mod", "publish", "--sign", "--version", "2.0"])
        self.assertEqual(rc, 0, ev[-1])
        texts = [e.get("text", "") for e in ev if e["event"] == "log"]
        self.assertIn("seeding of this folder pauses while it changes", texts)


class SlowSwarm(Base):
    def test_the_address_is_a_floor_in_speed_too(self):
        pub = self.publisher(torrent_name="mod")
        srv, _t = serve.serve_in_background(self.p("store"), "127.0.0.1", 0)
        self.cleanups.append(lambda: (srv.shutdown(), srv.server_close()))
        address = "http://127.0.0.1:%d/mod/" % srv.server_address[1]
        run, _said = self.seed_run(pub, "127.0.0.91", upload_kbps=16)            # a seed that sends 16 KB a second
        self.player("Player", address, config.load(pub), self.p("game"), seeds="127.0.0.91:%d" % run.swarm.port)
        t0 = time.time()
        with test_ip("127.0.0.92"), mock.patch.object(self.S, "RACE_AFTER", 2.0):
            rc, ev = run_json(["--project", "Player", "update"])
        took = time.time() - t0
        timing("an update of 1.8 MB from a seed at 16 KB/s, with the address racing it", took)
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(snapshot(self.p("game")), snapshot(self.p("src")))
        texts = " ".join(e.get("text", "") for e in ev if e["event"] == "log")
        self.assertIn("the swarm is slow", texts)
        self.assertIn("first to finish them", texts)
        self.assertLess(took, 60)          # about 6 seconds here; the swarm alone: 1.8 MB at 16 KB/s, two minutes


class TwoInstalls(Base):
    def test_two_installs_of_one_release_in_one_run(self):
        self.two_installs(True, "127.0.0.95")

    def test_two_installs_of_one_release_seeding_from_their_folders_in_one_run(self):
        self.two_installs(False, "127.0.0.35")

    def two_installs(self, seed_copy, ip):
        """Two players' projects of one release seeded from copies (seed_copy) or from their folders, on `ip`."""
        pub, p = self.setup_player(copy=seed_copy)
        p2 = self.player("Player 2", config.load(p)["source_url"], config.load(pub), self.p("game2"),
                         seed_copy=seed_copy)
        rc, ev = run_json(["--project", "Player 2", "update"])
        self.assertEqual(rc, 0, ev[-1])
        from deltapatcher.torrent import seed
        said, stop = [], threading.Event()
        run = seed.Run([(p, config.load(p)), (p2, config.load(p2))], dict(config.load(), torrent_port=0),
                       lambda t, s=None: said.append(t), lambda rows: None, stop, ip=ip)
        t = threading.Thread(target=run.run, daemon=True)
        t.start()
        self.cleanups.append(lambda: (stop.set(), t.join(10)))
        end = time.time() + 60
        while time.time() < end and not any("seeds the same release in this run" in x for x in said):
            time.sleep(0.1)
        self.assertIn("Player 2: not seeded: Player seeds the same release in this run", said)
        time.sleep(1)
        self.assertTrue(t.is_alive())                                              # the run goes on
        self.assertEqual(sum(1 for s in run.seeded if s.handle is not None), 1)


class Damaged(Base):
    def test_a_kept_torrent_with_a_huge_number_never_ends_the_run(self):
        self.huge_number(True, "127.0.0.97")

    def test_a_kept_torrent_with_a_huge_number_never_ends_a_run_seeding_from_folders(self):
        self.huge_number(False, "127.0.0.38")

    def huge_number(self, seed_copy, ip):
        """A damaged kept torrent beside a good project, both seeded from copies (seed_copy) or folders, on `ip`."""
        pub, p = self.setup_player(copy=seed_copy)
        shutil.copytree(self.p("game"), self.p("game2"))
        kept = self.p("game2", ".deltapatcher", "release", "release.torrent")
        write(kept, read(kept)[:-1] + b"3:zzzi" + b"9" * 5000 + b"ee")          # after its info, as kept by hand
        damaged = self.player("Damaged", config.load(p)["source_url"], config.load(pub), self.p("game2"),
                              seed_copy=seed_copy)
        from deltapatcher.torrent import seed
        said, stop = [], threading.Event()
        run = seed.Run([(damaged, config.load(damaged)), (p, config.load(p))], dict(config.load(), torrent_port=0),
                       lambda t, s=None: said.append(t), lambda rows: None, stop, ip=ip)
        t = threading.Thread(target=run.run, daemon=True)
        t.start()
        self.cleanups.append(lambda: (stop.set(), t.join(10)))
        end = time.time() + 60
        while time.time() < end and not any(s.handle is not None for s in run.seeded):
            time.sleep(0.1)
        self.assertIn("Damaged: not seeded: the release's torrent is not readable", said)
        self.assertEqual([s.project.title for s in run.seeded if s.handle is not None], ["Player"])   # the rest seeds
        self.assertTrue(t.is_alive())


class _Crawl(object):
    """hashlib as folder.py sees it, hashing as slowly as a big folder would: SLEEP seconds a piece."""

    SLEEP = 0.2

    @staticmethod
    def sha256():
        import hashlib as real

        class Slow(object):
            def __init__(self):
                self.h = real.sha256()

            def update(self, b):
                time.sleep(_Crawl.SLEEP)
                self.h.update(b)

            def hexdigest(self):
                return self.h.hexdigest()
        return Slow()


class SlowMapping(Base):
    def test_a_pause_asked_while_a_big_folder_is_looked_at(self):
        _pub, p = self.setup_player(copy=False)
        os.remove(self.p("game", ".deltapatcher", "hashes.json"))                # every file is hashed again
        from deltapatcher.torrent import folder, pause, seed
        run = seed.Run([(p, config.load(p))], dict(config.load(), torrent_port=0), lambda t, s=None: None,
                       lambda rows: None, threading.Event(), ip="127.0.0.87")
        run.swarm = self.S.Swarm("all", ip="127.0.0.87")
        self.cleanups.append(run.swarm.close)
        s = run.seeded[0]
        d = pause.folder_dir(self.p("game"))
        with mock.patch.object(folder, "hashlib", _Crawl), mock.patch.object(folder, "PIECE", 64 * 1024):
            t = threading.Thread(target=s.start, daemon=True)                # about 6 seconds of hashing
            t.start()
            time.sleep(1.0)
            asked_at = time.time()
            pause._write(s.seat._p("pause"), {"by": os.getpid(), "at": asked_at})    # an update asks
            while time.time() - asked_at < 10 and not pause._answered(d, s.seat.pid, asked_at):
                time.sleep(0.02)
            answered_in = time.time() - asked_at
            t.join(30)
        timing("the answer to a pause asked while the files are hashed", answered_in)
        self.assertLess(answered_in, 2.0)
        self.assertEqual((s.paused, s.handle), (True, None))

    def test_an_update_from_another_process_while_a_big_folder_is_looked_at(self):
        _pub, p = self.setup_player(copy=False)
        os.remove(self.p("game", ".deltapatcher", "hashes.json"))
        write(self.p("src", "readme.txt"), "two\n")
        self.publish("Mod", "2.0")
        from deltapatcher.torrent import folder, seed
        stop = threading.Event()
        run = seed.Run([(p, config.load(p))], dict(config.load(), torrent_port=0), lambda t, s=None: None,
                       lambda rows: None, stop, ip="127.0.0.89")
        # about 30 seconds of hashing: the update asks while it goes on, and waits at most 10 (pause.WAIT)
        with mock.patch.object(folder, "hashlib", _Crawl), mock.patch.object(folder, "PIECE", 64 * 1024), \
                mock.patch.object(_Crawl, "SLEEP", 1.0):
            t = threading.Thread(target=run.run, daemon=True)
            t.start()
            self.cleanups.append(lambda: (stop.set(), t.join(30)))
            time.sleep(1.0)
            env = dict(os.environ, PYTHONPATH=ROOT, PYTHONUNBUFFERED="1")
            env[torrent.TEST_IP_ENV] = "127.0.0.88"
            done = subprocess.run([sys.executable, "-m", "deltapatcher", "--json", "--no-log", "--project", "Player",
                                   "update"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
            stop.set()
            t.join(30)
        self.assertEqual(done.returncode, 0, done.stdout[-2000:])
        self.assertIn("seeding of this folder pauses while it changes", done.stdout)
        self.assertNotIn("did not pause", done.stdout)
        self.assertEqual(read(self.p("game", "readme.txt")), b"two\n")

    def test_an_update_from_another_process_while_a_copy_fills(self):
        _pub, p = self.setup_player(seed=True)                                   # its copy stays after the run
        write(self.p("src", "readme.txt"), "two\n")
        self.publish("Mod", "2.0")
        from deltapatcher.torrent import seed, seedcopy
        stop = threading.Event()
        run = seed.Run([(p, config.load(p))], dict(config.load(), torrent_port=0), lambda t, s=None: None,
                       lambda rows: None, stop, ip="127.0.0.26")
        # about 30 seconds of copying: the update asks while it goes on and is answered at once; with no torrent in
        # yet (the copy's first filling), the answer is a folder seed's (nothing is seeded meanwhile)
        with mock.patch.object(seedcopy, "hashlib", _Crawl), mock.patch.object(seedcopy, "PIECE", 64 * 1024), \
                mock.patch.object(_Crawl, "SLEEP", 1.0):
            t = threading.Thread(target=run.run, daemon=True)
            t.start()
            self.cleanups.append(lambda: (stop.set(), t.join(30)))
            time.sleep(1.0)
            env = dict(os.environ, PYTHONPATH=ROOT, PYTHONUNBUFFERED="1")
            env[torrent.TEST_IP_ENV] = "127.0.0.27"
            t0 = time.time()
            done = subprocess.run([sys.executable, "-m", "deltapatcher", "--json", "--no-log", "--project", "Player",
                                   "update"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
            took = time.time() - t0
            stop.set()
            t.join(30)
        timing("an update from another process while a copy fills", took)
        self.assertEqual(done.returncode, 0, done.stdout[-2000:])
        self.assertIn("seeding of this folder pauses while it changes", done.stdout)
        self.assertNotIn("goes on from its copy", done.stdout)
        self.assertNotIn("did not pause", done.stdout)
        self.assertEqual(read(self.p("game", "readme.txt")), b"two\n")
        self.assertFalse([n for n in os.listdir(os.path.join(self.copy_dir(p), "blobs"))
                          if n.endswith(".part")])


class MeasuredAddress(Base):
    def test_the_address_measured_is_the_floor(self):
        files = {"readme.txt": b"one\n"}
        for i in range(4):
            files["data/pak%d.bin" % i] = os.urandom(8000000)
        pub = self.publisher(torrent_name="mod", files=files)
        srv, _t = serve.serve_in_background(self.p("store"), "127.0.0.1", 0)
        self.cleanups.append(lambda: (srv.shutdown(), srv.server_close()))
        address = "http://127.0.0.1:%d/mod/" % srv.server_address[1]
        run, _said = self.seed_run(pub, "127.0.0.93", upload_kbps=300)            # faster than RATE_FLOOR
        self.player("Player", address, config.load(pub), self.p("game"), seeds="127.0.0.93:%d" % run.swarm.port)
        t0 = time.time()
        # RATE_FLOOR as good as none: only the address's measured speed can make the swarm the slower
        with test_ip("127.0.0.94"), mock.patch.object(self.S, "RATE_FLOOR", 1024):
            rc, ev = run_json(["--project", "Player", "update"])
        took = time.time() - t0
        timing("an update of 32 MB from a seed at 300 KB/s and an address on this computer", took)
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(snapshot(self.p("game")), snapshot(self.p("src")))
        texts = " ".join(e.get("text", "") for e in ev if e["event"] == "log")
        self.assertIn(", the address ", texts)                                    # it was measured
        self.assertIn("first to finish them", texts)
        self.assertLess(took, 30)      # about RACE_AFTER (10 s) and the address's own time; the swarm alone: 2 minutes


class SlowName(Base):
    def test_a_seed_run_ends_within_two_seconds_while_this_computers_name_is_looked_up(self):
        """A resolver can take half a minute for this computer's own name (macOS asks mDNS when the name is not in
        DNS): a seed run's stop never waits for it."""
        self.slow_name(True, "127.0.0.96")

    def test_a_folder_seed_run_ends_within_two_seconds_while_this_computers_name_is_looked_up(self):
        self.slow_name(False, "127.0.0.40")

    def slow_name(self, seed_copy, ip):
        """The stop of a run seeding from a copy (seed_copy) or from the folder, on `ip`, while the name hangs."""
        _pub, p = self.setup_player(copy=seed_copy)
        from deltapatcher.torrent import seed
        name, real = socket.gethostname(), socket.getaddrinfo
        answer = threading.Event()
        self.cleanups.append(answer.set)

        def lookup(host, *a, **k):
            if host == name:
                answer.wait(30)
            return real(host, *a, **k)
        stop = threading.Event()
        with mock.patch.object(self.S, "_HOST", {"addresses": [], "started": None, "thread": None}), \
                mock.patch.object(self.S.socket, "getaddrinfo", side_effect=lookup):
            run = seed.Run([(p, config.load(p))], dict(config.load(), torrent_port=0), lambda t, s=None: None,
                           lambda rows: None, stop, ip=ip)
            t = threading.Thread(target=run.run, daemon=True)
            t.start()
            self.cleanups.append(lambda: (stop.set(), t.join(30)))
            end = time.time() + 60
            while time.time() < end and not any(s.handle is not None for s in run.seeded):
                time.sleep(0.05)
            self.assertTrue(any(s.handle is not None for s in run.seeded))
            self.assertEqual(run.seeded[0].kind, "copy" if seed_copy else "folder")
            t0 = time.time()
            stop.set()
            t.join(30)
            took = time.time() - t0
        timing("a seed run's stop while this computer's name is looked up", took)
        self.assertFalse(t.is_alive())
        self.assertLess(took, 2.0)


class SeedCopy(Base):
    """A player seeds from a copy of its release by default (seedcopy.py): the copy holds exactly the release (never the
    player's own file), the game folder is never read while it seeds, an update never waits for it and the copy then
    takes the new release, a newer release found by a look switches it, a full drive stops the copying and says so, a
    stop while it copies ends within two seconds and keeps what was copied, and the copy goes as soon as its project
    stops seeding from it (seed_copy = no, seed = no) or moves with seed_copies."""

    def copy_files(self, copy):
        """Every file in the copy, relative, with forward slashes."""
        out = []
        for top, _dirs, names in os.walk(copy):
            for n in names:
                out.append(os.path.relpath(os.path.join(top, n), copy).replace("\\", "/"))
        return sorted(out)

    def wait(self, pred, seconds=60):
        """pred() holds within `seconds` (a slow computer's margin: a second or two here)."""
        end = time.time() + seconds
        while time.time() < end and not pred():
            time.sleep(0.05)
        return pred()

    def test_a_player_seeds_from_a_copy_of_its_release_by_default(self):
        _pub, p = self.setup_player()
        before = snapshot(self.p("game"))
        run, said = self.seed_run(p, "127.0.0.3")
        s = run.seeded[0]
        copy = self.copy_dir(p)
        self.assertEqual((s.kind, s.root, s.state), ("copy", copy, "seeding"), (s.why, said))
        kept = self.p("game", ".deltapatcher", "release")
        raw = read(os.path.join(kept, "manifest.json"))
        sha = hashlib.sha256(raw).hexdigest()
        man = Manifest.from_json(raw)
        mine = man.files["data/user.cfg"].sha256                                  # protected: the player's own
        blobs = set(man.blobs()) - {mine}
        small = ["manifest.json", "sigs/%s.sig" % sha, "release.torrent", "release.item"]
        self.assertEqual(self.copy_files(copy), sorted(small + ["blobs/" + b for b in blobs] +
                                                       ["seedstore.json", ".deltapatcher/hashes.json"]))
        for name in small:
            self.assertEqual(read(os.path.join(copy, *name.split("/"))), read(os.path.join(kept, *name.split("/"))), name)
        for b in blobs:
            self.assertEqual(hashlib.sha256(read(os.path.join(copy, "blobs", b))).hexdigest(), b)
        got = self.downloader("127.0.0.4", read(os.path.join(copy, "release.torrent")), ("127.0.0.3", run.swarm.port),
                              self.p("peer"))
        for sha_, size in man.blobs().items():
            self.assertEqual(got["blobs/" + sha_], 0 if sha_ == mine else size, sha_)
        self.assertEqual(snapshot(self.p("game")), before)                        # the game folder as it was
        row = s.status()
        self.assertEqual((row["kind"], row["folder"], row["copy"]["path"], row["copy"]["state"]),
                         ("copy", self.p("game"), copy, "ready"))
        self.assertEqual(row["copy"]["bytes"], sum(os.path.getsize(os.path.join(copy, *f.split("/")))
                                                   for f in self.copy_files(copy)))
        total = sum(man.blobs().values())
        self.assertEqual((row["copy"]["done"], row["copy"]["total"]), (total - man.blobs()[mine], total))
        self.assertTrue(cli.seeding_words(row).endswith(", from a copy"), cli.seeding_words(row))

    def test_the_game_folder_is_never_read_while_seeding_from_a_copy(self):
        _pub, p = self.setup_player()
        from deltapatcher.torrent import seed
        man = Manifest.from_json(read(self.p("game", ".deltapatcher", "release", "manifest.json")))
        with mock.patch.object(seed, "WATCH", 0.3):
            run, said = self.seed_run(p, "127.0.0.5")
            s = run.seeded[0]
            handle = s.handle
            pak = self.p("game", "data", "pak0.bin")
            os.rename(pak, pak + ".away")                       # Windows refuses this for a file a seed has open
            os.rename(pak + ".away", pak)
            bsp = self.p("game", "maps", "a.bsp")
            data = bytearray(read(bsp))
            data[0] ^= 0xFF
            write(bsp, bytes(data))                                                 # changed in place, same size
            time.sleep(1.5)                                                         # a few looks at what it offers
            self.assertIs(s.handle, handle)
            self.assertFalse([x for x in said if "looking at them again" in str(x)], said)
            got = self.downloader("127.0.0.6", read(os.path.join(self.copy_dir(p), "release.torrent")),
                                  ("127.0.0.5", run.swarm.port), self.p("peer"))
        for path in ("data/pak0.bin", "maps/a.bsp"):
            e = man.files[path]
            self.assertEqual(got["blobs/" + e.sha256], e.size, path)               # the copy still offers both

    def test_an_update_while_seeding_from_a_copy_never_waits_and_the_copy_takes_the_new_release(self):
        _pub, p = self.setup_player()
        from deltapatcher.torrent import pause
        run, said = self.seed_run(p, "127.0.0.7")
        s = run.seeded[0]
        handle = s.handle
        write(self.p("src", "data", "pak0.bin"), os.urandom(600000))
        write(self.p("src", "maps", "a.bsp"), os.urandom(300000))
        self.publish("Mod", "2.0")
        real, seen, samples = pause.paused, [], []

        @contextlib.contextmanager
        def watched(root, log=None, wait=None):
            t0 = time.time()
            with real(root, log, wait):
                seen.append(time.time() - t0)
                done = threading.Event()

                def sample():                                    # the torrent, every 10 ms while the folder changes
                    while not done.is_set():
                        samples.append(s.handle is handle)
                        time.sleep(0.01)
                sampler = threading.Thread(target=sample, daemon=True)
                sampler.start()
                try:
                    yield
                finally:
                    done.set()
                    sampler.join(5)
                    samples.append(s.handle is handle)
        with mock.patch.object(pause, "paused", watched):
            rc, ev = run_json(["--project", "Player", "update"])
        self.assertEqual(rc, 0, ev[-1])
        texts = [e.get("text", "") for e in ev if e["event"] == "log"]
        self.assertIn("seeding of this folder goes on from its copy", texts)
        self.assertTrue(seen)
        for waited in seen:
            timing("an update's ask of a seed run from a copy", waited)
            self.assertLess(waited, pause.WAIT / 2)                                # answered at once: no pause
        self.assertGreaterEqual(len(samples), 2)
        self.assertTrue(all(samples), samples)                                     # its torrent stayed in all along
        self.assertFalse([x for x in said if "seeding paused" in str(x)], said)
        self.assertTrue(self.wait(lambda: s.title_of_release == "Mod 2.0" and s.handle is not None), said)
        new = Manifest.from_json(read(self.p("store", "manifest.json")))
        copy = self.copy_dir(p)
        self.assertEqual(sorted(os.listdir(os.path.join(copy, "blobs"))),
                         sorted(set(new.blobs()) - {new.files["data/user.cfg"].sha256}))      # its blobs, only those
        self.assertEqual(read(os.path.join(copy, "manifest.json")), read(self.p("store", "manifest.json")))
        self.assertEqual(s.status()["release"], "Mod 2.0")
        got = self.downloader("127.0.0.8", read(os.path.join(copy, "release.torrent")), ("127.0.0.7", run.swarm.port),
                              self.p("peer"))
        self.assertEqual(got["blobs/" + new.files["maps/a.bsp"].sha256], 300000)

    def test_seed_copy_no_seeds_from_the_folder_and_deletes_the_copy(self):
        _pub, p = self.setup_player()
        from deltapatcher.torrent import seed
        with mock.patch.object(seed, "SETTINGS", 0.3):
            run, said = self.seed_run(p, "127.0.0.10")
            copy = run.seeded[0].root
            self.assertTrue(os.path.isdir(copy))
            rc, ev = run_json(["--project", "Player", "config", "set", "seed_copy=no"])
            self.assertEqual(rc, 0, ev[-1])
            self.assertTrue(self.wait(lambda: run.seeded[0].kind == "folder" and run.seeded[0].handle is not None), said)
            self.assertTrue(self.wait(lambda: not os.path.exists(copy)), said)     # once a second read says so too
        self.assertIn("Player seeds from its folder now (seed_copy = no)", said)
        got = self.downloader("127.0.0.11", read(self.p("game", ".deltapatcher", "release", "release.torrent")),
                              ("127.0.0.10", run.swarm.port), self.p("peer"))
        man = Manifest.from_json(read(self.p("game", ".deltapatcher", "release", "manifest.json")))
        self.assertEqual(got["blobs/" + man.files["data/pak1.bin"].sha256], 900000)   # now from the folder

    def test_seed_copy_turned_on_moves_a_folder_seed_to_a_copy(self):
        _pub, p = self.setup_player(copy=False)
        from deltapatcher.torrent import seed
        with mock.patch.object(seed, "SETTINGS", 0.3):
            run, said = self.seed_run(p, "127.0.0.28")
            self.assertEqual(run.seeded[0].kind, "folder")
            rc, ev = run_json(["--project", "Player", "config", "set", "seed_copy=yes"])
            self.assertEqual(rc, 0, ev[-1])
            self.assertTrue(self.wait(lambda: run.seeded[0].kind == "copy" and run.seeded[0].handle is not None), said)
        copy = self.copy_dir(p)
        self.assertEqual(run.seeded[0].root, copy)
        self.assertIn("Player seeds from a copy of its release now (seed_copy = yes): %s" % copy, said)
        pak = self.p("game", "data", "pak0.bin")
        os.rename(pak, pak + ".away")                     # the folder's files are not open any more (Windows)
        os.rename(pak + ".away", pak)
        got = self.downloader("127.0.0.29", read(os.path.join(copy, "release.torrent")), ("127.0.0.28", run.swarm.port),
                              self.p("peer"))
        man = Manifest.from_json(read(self.p("game", ".deltapatcher", "release", "manifest.json")))
        self.assertEqual(got["blobs/" + man.files["data/pak0.bin"].sha256], 600000)

    def test_seed_no_while_a_run_seeds_from_a_copy_deletes_it(self):
        _pub, p = self.setup_player(seed=True)
        from deltapatcher.torrent import seed
        with mock.patch.object(seed, "SETTINGS", 0.3):
            run, said = self.seed_run(p, "127.0.0.12")
            copy = run.seeded[0].root
            self.assertTrue(os.path.isdir(copy))
            rc, ev = run_json(["--project", "Player", "config", "set", "seed=no"])
            self.assertEqual(rc, 0, ev[-1])
            self.assertTrue(self.wait(lambda: not os.path.exists(copy)), said)
        self.assertIn("Player leaves this run: seed is off for it now", said)

    def test_seed_copies_moves_the_copy_and_never_a_folder_without_the_marker(self):
        _pub, p = self.setup_player(seed=True)
        from deltapatcher.torrent import seed, seedcopy
        a, b = self.p("copies-a"), self.p("copies-b")
        tid = config.load(p)["torrent_id"]
        decoys = {os.path.join(a, "decoy", "big.bin"): os.urandom(100000), os.path.join(b, "decoy", "x.txt"): b"mine",
                  os.path.join(a, "%s-00000000" % tid, "blobs", "x"): b"named like a copy",
                  os.path.join(b, "%s-ffffffff" % tid, "y.txt"): b"named like a copy too"}
        for path, data in decoys.items():
            write(path, data)
        config.save({"seed_copies": a})
        digest = hashlib.sha256(os.path.normcase(self.p("game")).encode("utf-8")).hexdigest()[:8]
        with mock.patch.object(seed, "SETTINGS", 0.3):
            run, said = self.seed_run(p, "127.0.0.13")
            at_a = run.seeded[0].root
            self.assertEqual(at_a, os.path.join(a, "%s-%s" % (tid, digest)))
            self.assertTrue(seedcopy.is_copy(at_a))
            self.assertFalse(os.path.exists(seedcopy.default_place(self.p("game"), tid)))
            rc, ev = run_json(["config", "set", "seed_copies=" + b])
            self.assertEqual(rc, 0, ev[-1])
            at_b = os.path.join(b, "%s-%s" % (tid, digest))
            self.assertTrue(self.wait(lambda: run.seeded[0].root == at_b and run.seeded[0].handle is not None), said)
            self.assertTrue(self.wait(lambda: not os.path.exists(at_a)), said)     # once a second read says so too
        self.assertIn("Player: its seed copy moves to %s" % at_b, said)
        self.assertEqual(len(os.listdir(os.path.join(at_b, "blobs"))), 4)
        for path, data in decoys.items():
            self.assertEqual(read(path), data, path)                               # untouched

    def test_a_drive_without_room_stops_the_copying_and_the_row_says_why(self):
        _pub, p = self.setup_player()
        from deltapatcher.torrent import seedcopy
        real = shutil.disk_usage(self.tmp)

        def usage(d):                                     # 1 MB of room past the 5% kept, taken as the copy grows
            return type(real)(100 << 30, 0, (5 << 30) + 1000000 - seedcopy.size_of(d))
        with mock.patch.object(seedcopy.shutil, "disk_usage", side_effect=usage), \
                mock.patch.object(seedcopy, "_clone", return_value=False):
            run, said = self.seed_run(p, "127.0.0.15")
        s = run.seeded[0]
        row = s.status()
        self.assertEqual((row["state"], row["copy"]["state"]), ("seeding", "full"), said)
        self.assertIn("seed from the folder with seed_copy = no, or move seed copies with seed_copies", row["why"])
        man = Manifest.from_json(read(self.p("game", ".deltapatcher", "release", "manifest.json")))
        big = {man.files[f].sha256 for f in ("data/pak0.bin", "data/pak1.bin", "maps/a.bsp")}
        held = os.listdir(os.path.join(s.root, "blobs"))
        self.assertTrue(big & set(held), held)                                    # some went in
        self.assertTrue(big - set(held), held)                                    # and some would not fit
        self.assertLessEqual(sum(os.path.getsize(os.path.join(s.root, "blobs", n)) for n in held), 1000000)
        self.assertIn(row["why"], cli.seeding_words(row))

    def test_a_run_stopped_while_it_copies_ends_within_two_seconds_and_keeps_what_it_copied(self):
        _pub, p = self.setup_player(seed=True)
        from deltapatcher.torrent import seed, seedcopy
        blobs = os.path.join(self.copy_dir(p), "blobs")

        def names():
            try:
                return os.listdir(blobs)
            except OSError:
                return []
        stop = threading.Event()
        with mock.patch.object(seedcopy, "hashlib", _Crawl), mock.patch.object(seedcopy, "PIECE", 64 * 1024):
            run = seed.Run([(p, config.load(p))], dict(config.load(), torrent_port=0), lambda t, s=None: None,
                           lambda rows: None, stop, ip="127.0.0.16")
            t = threading.Thread(target=run.run, daemon=True)                      # about 6 seconds of copying
            t.start()
            self.cleanups.append(lambda: (stop.set(), t.join(30)))
            self.assertTrue(self.wait(lambda: any(n.endswith(".part") for n in names()) and
                                      any(not n.endswith(".part") for n in names())), names())
            t0 = time.time()
            stop.set()
            t.join(30)
            took = time.time() - t0
        timing("a seed run's stop while it copies", took)
        self.assertFalse(t.is_alive())
        self.assertLess(took, 2.0)
        done = names()
        self.assertFalse([n for n in done if n.endswith(".part")], done)          # the one going in went
        self.assertTrue(done)                                                      # the ones copied stay
        copied, put = [], seedcopy._put

        def counted(src, part, *a):
            copied.append(os.path.basename(part).split(".")[0])
            return put(src, part, *a)
        with mock.patch.object(seedcopy, "_put", side_effect=counted):
            run2, _said = self.seed_run(p, "127.0.0.17")
        self.assertEqual(run2.seeded[0].state, "seeding")
        self.assertFalse(set(copied) & set(done), (copied, done))                  # never copied again
        man = Manifest.from_json(read(self.p("game", ".deltapatcher", "release", "manifest.json")))
        self.assertEqual(sorted(names()), sorted(set(man.blobs()) - {man.files["data/user.cfg"].sha256}))

    def test_a_newer_release_found_by_a_look_switches_the_copy(self):
        _pub, p = self.setup_player()
        old = Manifest.from_json(read(self.p("game", ".deltapatcher", "release", "manifest.json")))
        write(self.p("src", "readme.txt"), "two\n")
        write(self.p("src", "data", "pak1.bin"), os.urandom(900000))
        self.publish("Mod", "2.0")
        raw = read(self.p("store", "manifest.json"))
        sha = hashlib.sha256(raw).hexdigest()
        v2 = {"manifest.json": raw, "sigs/%s.sig" % sha: read(self.p("store", "sigs", sha + ".sig")),
              "release.torrent": read(self.p("store", "release.torrent")),
              "release.item": read(self.p("store", "release.item"))}
        from deltapatcher.torrent import seed
        said = []
        run = seed.Run([(p, config.load(p))], dict(config.load(), torrent_port=0), lambda t, s=None: said.append(t),
                       lambda rows: None, threading.Event(), ip="127.0.0.18")
        run.swarm = self.S.Swarm("all", ip="127.0.0.18")
        self.cleanups.append(run.swarm.close)
        s = run.seeded[0]
        self.assertTrue(s.start(), s.why)
        s._switch(v2)
        self.assertEqual((s.kind, s.state, s.title_of_release), ("copy", "seeding", "Mod 2.0"), said)
        new = Manifest.from_json(raw)
        mine = new.files["data/user.cfg"].sha256
        shared = (set(new.blobs()) & set(old.blobs())) - {mine}
        copy = self.copy_dir(p)
        self.assertEqual(sorted(os.listdir(os.path.join(copy, "blobs"))), sorted(shared))     # the others went
        self.assertEqual(read(os.path.join(copy, "manifest.json")), raw)
        got = self.downloader("127.0.0.19", v2["release.torrent"], ("127.0.0.18", run.swarm.port), self.p("peer"))
        for sha_, size in new.blobs().items():
            self.assertEqual(got["blobs/" + sha_], size if sha_ in shared else 0, sha_)

    def test_two_projects_of_one_folder_and_torrent_in_one_run_share_their_copy(self):
        pub, p = self.setup_player(seed=True)
        p2 = self.player("Player 2", config.load(p)["source_url"], config.load(pub), self.p("game"), seed=True)
        copy = self.copy_dir(p)
        self.assertEqual(self.copy_dir(p2), copy)
        from deltapatcher.torrent import seed, seedcopy
        said, stop = [], threading.Event()
        with mock.patch.object(seed, "SETTINGS", 0.3), mock.patch.object(seed, "WATCH", 0.3):
            run = seed.Run([(p, config.load(p)), (p2, config.load(p2))], dict(config.load(), torrent_port=0),
                           lambda t, s=None: said.append(t), lambda rows: None, stop, ip="127.0.0.14")
            t = threading.Thread(target=run.run, daemon=True)
            t.start()
            self.cleanups.append(lambda: (stop.set(), t.join(10)))
            self.assertTrue(self.wait(lambda: run.seeded[0].handle is not None), said)
            blobs = sorted(os.listdir(os.path.join(copy, "blobs")))
            rc, ev = run_json(["--project", "Player", "config", "set", "seed=no"])
            self.assertEqual(rc, 0, ev[-1])
            self.assertTrue(self.wait(lambda: [s.project.title for s in run.seeded if s.handle is not None] ==
                                      ["Player 2"]), said)
        self.assertIn("Player leaves this run: seed is off for it now", said)
        self.assertTrue(seedcopy.is_copy(copy))                                    # Player 2 still needs it: whole
        self.assertEqual(sorted(os.listdir(os.path.join(copy, "blobs"))), blobs)
        self.assertEqual(run.seeded[0].root, copy)

    def test_a_copy_deleted_while_it_fills_is_made_again_never_left_without_its_marker(self):
        _pub, p = self.setup_player(seed=True)
        from deltapatcher.torrent import seed, seedcopy
        copy = self.copy_dir(p)
        blobs = os.path.join(copy, "blobs")

        def parts():
            try:
                return [n for n in os.listdir(blobs) if n.endswith(".part")]
            except OSError:
                return []
        said, stop = [], threading.Event()
        gone = "Player: not seeded: its seed copy was deleted: it is made again"
        with mock.patch.object(seedcopy, "hashlib", _Crawl), mock.patch.object(seedcopy, "PIECE", 64 * 1024), \
                mock.patch.object(seed, "WATCH", 3.0):
            run = seed.Run([(p, config.load(p))], dict(config.load(), torrent_port=0),
                           lambda t, s=None: said.append(t), lambda rows: None, stop, ip="127.0.0.20")
            t = threading.Thread(target=run.run, daemon=True)
            t.start()
            self.cleanups.append(lambda: (stop.set(), t.join(30)))
            self.assertTrue(self.wait(lambda: parts()), said)                     # a blob going in
            end = time.time() + 30
            while os.path.exists(copy) and time.time() < end:                    # by hand, marker and all, again
                shutil.rmtree(copy, ignore_errors=True)                           # while an antivirus holds a file
                time.sleep(0.05)
            self.assertFalse(os.path.exists(copy))
            self.assertTrue(self.wait(lambda: gone in said), said)
            time.sleep(0.5)                                                       # its next start is WATCH away
            self.assertFalse(os.path.exists(copy))                                # nothing written there meanwhile
        self.assertTrue(self.wait(lambda: run.seeded[0].handle is not None), said)   # the next start: a fresh copy
        self.assertTrue(seedcopy.is_copy(copy))
        man = Manifest.from_json(read(self.p("game", ".deltapatcher", "release", "manifest.json")))
        self.assertEqual(sorted(os.listdir(blobs)), sorted(set(man.blobs()) - {man.files["data/user.cfg"].sha256}))

    def test_a_stop_while_a_big_copy_is_deleted_ends_the_run_within_two_seconds(self):
        pub, p = self.setup_player(seed=True)
        from deltapatcher.torrent import seed, seedcopy
        old = self.player("Old", config.load(p)["source_url"], config.load(pub), self.p("old"))      # seed = no
        os.makedirs(self.p("old", ".deltapatcher"))
        place = self.copy_dir(old)
        seedcopy.prepare(place, config.load(old)["torrent_id"], self.p("old"), old.path)
        os.makedirs(os.path.join(place, "blobs"))
        for i in range(3000):                                                     # thousands of small files
            with open(os.path.join(place, "blobs", "%064x" % i), "wb") as f:
                f.write(b"x")
        real, removed = seedcopy._remove, []

        def slow(path):                                                           # a slow drive
            time.sleep(0.002)
            removed.append(path)
            real(path)
        stop = threading.Event()
        with mock.patch.object(seedcopy, "_remove", side_effect=slow):
            run = seed.Run([(p, config.load(p))], dict(config.load(), torrent_port=0), lambda t, s=None: None,
                           lambda rows: None, stop, ip="127.0.0.30")
            t = threading.Thread(target=run.run, daemon=True)                    # its 2nd read deletes: about 6 s
            t.start()
            self.cleanups.append(lambda: (stop.set(), t.join(30)))
            self.assertTrue(self.wait(lambda: len(removed) > 100))
            t0 = time.time()
            stop.set()
            t.join(30)
            took = time.time() - t0
        timing("a seed run's stop while a copy of 3000 files is deleted", took)
        self.assertFalse(t.is_alive())
        self.assertLess(took, 2.0)
        self.assertTrue(seedcopy.is_copy(place))                                 # cut short: its marker stays
        self.assertLess(len(removed), 3000)
        self.assertEqual(seedcopy.clean(config.load()), [place])                  # the next clean-up finishes it
        self.assertFalse(os.path.exists(place))

    def test_a_blob_that_cannot_be_copied_now_is_tried_again_and_the_row_says_so(self):
        _pub, p = self.setup_player()
        from deltapatcher.torrent import seed, seedcopy
        real, held = seedcopy._copy, [True]

        def copy(src, dst, check, tick):                                          # pak0.bin held open by the game
            if held[0] and src.endswith("pak0.bin"):
                raise PermissionError(13, "the file is in use")
            return real(src, dst, check, tick)
        with mock.patch.object(seedcopy, "_copy", side_effect=copy), \
                mock.patch.object(seedcopy, "_clone", return_value=False), \
                mock.patch.object(seed, "RETRY", 0.5), mock.patch.object(seed, "WATCH", 0.3):
            run, said = self.seed_run(p, "127.0.0.44")
            s = run.seeded[0]
            row = s.status()
            self.assertEqual((row["state"], row["copy"]["state"]), ("seeding", "copying"), said)
            self.assertIn("pak0.bin: the file is in use", row["why"])
            self.assertIn("tried again in a minute", row["why"])
            time.sleep(1.5)                                                       # tried again: still held
            self.assertEqual(s.status()["copy"]["state"], "copying")
            held[0] = False
            self.assertTrue(self.wait(lambda: s.status()["copy"]["state"] == "ready"), said)
        man = Manifest.from_json(read(self.p("game", ".deltapatcher", "release", "manifest.json")))
        self.assertIn(man.files["data/pak0.bin"].sha256, os.listdir(os.path.join(s.root, "blobs")))
        self.assertEqual(s.status()["why"], "")

    def test_blobs_a_retry_copies_before_the_drive_is_full_are_offered(self):
        _pub, p = self.setup_player()
        from deltapatcher.torrent import folder, seed, seedcopy
        release = self.p("game", ".deltapatcher", "release")
        man = Manifest.from_json(read(os.path.join(release, "manifest.json")))
        mine = man.files["data/user.cfg"].sha256
        _ti, files = self.S.read_torrent(read(os.path.join(release, "release.torrent")))
        order = []
        for f in files:                                                           # the order a fill takes them in
            sha = None if f.pad else folder.blob_of(f.path)
            if sha and sha != mine and sha not in order:
                order.append(sha)
        sizes = man.blobs()
        real = shutil.disk_usage(self.tmp)
        room = [0]

        def usage(d):                                     # room past the 5% kept, taken as the copy grows
            return type(real)(100 << 30, 0, (5 << 30) + room[0] - seedcopy.size_of(d))
        with mock.patch.object(seedcopy.shutil, "disk_usage", side_effect=usage), \
                mock.patch.object(seedcopy, "_clone", return_value=False), \
                mock.patch.object(seed, "RETRY", 0.5), mock.patch.object(seed, "WATCH", 0.3):
            run, said = self.seed_run(p, "127.0.0.46")
            s = run.seeded[0]
            self.assertEqual((s.state, s.copy_state), ("seeding", "full"), said)
            self.assertFalse([b for b in order if "blobs/" + b in s.mapping.offered])        # none went in
            room[0] = sizes[order[0]] + sizes[order[1]]                                      # room for two now
            self.assertTrue(self.wait(lambda: "blobs/" + order[1] in s.mapping.offered), said)
            self.assertIn("blobs/" + order[0], s.mapping.offered)                            # both offered
            self.assertNotIn("blobs/" + order[2], s.mapping.offered)
            self.assertEqual((s.state, s.copy_state), ("seeding", "full"))
            got = self.downloader("127.0.0.49", read(os.path.join(s.root, "release.torrent")),
                                  ("127.0.0.46", run.swarm.port), self.p("peer"))
        for sha in order[:2]:
            self.assertEqual(got["blobs/" + sha], sizes[sha], sha)

    def test_a_run_never_deletes_a_copy_on_settings_that_cannot_be_read(self):
        _pub, p = self.setup_player(seed=True)
        from deltapatcher.torrent import seed, seedcopy
        good, main = read(p.path), read(config.path())
        said, stop = [], threading.Event()
        with mock.patch.object(seed, "SETTINGS", 0.3):
            run = seed.Run([(p, config.load(p))], dict(config.load(), torrent_port=0),
                           lambda t, s=None: said.append(t), lambda rows: None, stop, ip="127.0.0.47")
            t = threading.Thread(target=run.run, daemon=True)
            t.start()
            self.cleanups.append(lambda: (stop.set(), t.join(30)))
            self.assertTrue(self.wait(lambda: run.seeded and run.seeded[0].handle is not None), said)
            s = run.seeded[0]
            copy, handle = s.root, s.handle
            write(p.path, b"[torrent\nseed = no\n")                                  # cannot be read whole now
            self.assertTrue(config.unreadable(p.path))
            time.sleep(1.5)                                                          # a few reads of the settings
            self.assertEqual(run.seeded, [s])
            self.assertIs(s.handle, handle)                                          # seeded as it was
            self.assertTrue(seedcopy.is_copy(copy))
            self.assertTrue([x for x in said if "its settings file cannot be read whole" in str(x)], said)
            write(p.path, good)
            write(config.path(), main + b"torrents refuse\n")                       # the main file cannot be read
            self.assertTrue(config.unreadable(config.path()))
            t.join(30)                                                               # torrents refused: it ends
            self.assertFalse(t.is_alive())
        write(config.path(), main)
        self.assertIn("torrents are refused in the settings now: seeding stops", said)
        self.assertTrue(seedcopy.is_copy(copy))                                      # never deleted on them
        self.assertFalse([x for x in said if "deleted the seed copy" in str(x)], said)

    def test_a_stop_while_a_dropped_projects_copy_is_deleted_ends_the_run_in_time(self):
        _pub, p = self.setup_player(seed=True)
        from deltapatcher.torrent import seed, seedcopy
        said, stop = [], threading.Event()
        with mock.patch.object(seed, "SETTINGS", 0.3):
            run = seed.Run([(p, config.load(p))], dict(config.load(), torrent_port=0),
                           lambda t, s=None: said.append(t), lambda rows: None, stop, every_project=True,
                           ip="127.0.0.48")
            t = threading.Thread(target=run.run, daemon=True)
            t.start()
            self.cleanups.append(lambda: (stop.set(), t.join(30)))
            self.assertTrue(self.wait(lambda: run.seeded and run.seeded[0].handle is not None), said)
            copy = run.seeded[0].root
            for i in range(3000):                                                    # thousands of small files
                with open(os.path.join(copy, "blobs", "%064x" % i), "wb") as f:
                    f.write(b"x")
            real, removed = seedcopy._remove, []

            def slow(path):                                                          # a slow drive
                time.sleep(0.002)
                removed.append(path)
                real(path)
            with mock.patch.object(seedcopy, "_remove", side_effect=slow):
                config.save({"seed": False}, p)                                      # by hand: the run drops it
                self.assertTrue(self.wait(lambda: len(removed) > 100), said)
                t0 = time.time()
                stop.set()
                t.join(30)
                took = time.time() - t0
        timing("a seed run's stop while a dropped project's copy of 3000 files is deleted", took)
        self.assertFalse(t.is_alive())
        self.assertLess(took, 2.0)                                # its end's deletes stop LEAVE seconds after it
        self.assertIn("Player leaves this run: seed is off for it now", said)
        self.assertTrue(seedcopy.is_copy(copy))                                      # cut short: its marker stays
        self.assertLess(len(removed), 3000)
        self.assertEqual(seedcopy.clean(config.load()), [copy])                      # the next clean-up finishes it

    def test_torrents_refused_while_a_run_seeds_end_it_and_delete_its_copy(self):
        _pub, p = self.setup_player(seed=True)
        from deltapatcher.torrent import seed, seedcopy
        said, stop = [], threading.Event()
        with mock.patch.object(seed, "SETTINGS", 0.3):
            run = seed.Run([(p, config.load(p))], dict(config.load(), torrent_port=0),
                           lambda t, s=None: said.append(t), lambda rows: None, stop, ip="127.0.0.50")
            t = threading.Thread(target=run.run, daemon=True)
            t.start()
            self.cleanups.append(lambda: (stop.set(), t.join(30)))
            self.assertTrue(self.wait(lambda: run.seeded and run.seeded[0].handle is not None), said)
            copy = run.seeded[0].root
            rc, ev = run_json(["config", "set", "torrents=refuse"])
            self.assertEqual(rc, 0, ev[-1])
            self.assertTrue(seedcopy.is_copy(copy))                                  # in use: left to the run
            t.join(30)
            self.assertFalse(t.is_alive())
        self.assertIn("torrents are refused in the settings now: seeding stops", said)
        self.assertFalse(os.path.exists(copy))                                       # the person's no: at its end
        self.assertIn("deleted the seed copy %s: no project seeds from it any more" % copy, said)


if __name__ == "__main__":
    unittest.main()
