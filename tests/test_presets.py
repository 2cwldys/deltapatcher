"""Project files (presets): one or more projects in an HTML page, exported, signed, imported; and their reader
against hostile files."""

import contextlib
import io
import json
import os
import re
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, snapshot, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import cli, config, presets, projects, publish, serve, signing  # noqa: E402
from deltapatcher.presets import PresetError  # noqa: E402

PASSPHRASE = "a long passphrase for the test key"


def run_json(argv, stdin=""):
    """cli.main in --json mode: (exit code, [events])."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(sys, "stdin", io.StringIO(stdin)):
        rc = cli.main(["--json", "--no-log"] + argv)
    return rc, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


class Base(TempCase):
    def fresh_settings(self, name):
        """Settings of their own, as on another computer."""
        os.environ["DELTAPATCHER_CONFIG"] = self.p(name, "deltapatcher.ini")

    def make(self, name, **values):
        p = projects.new(name)
        config.save(values, p)
        return p


class RoundTrip(Base):
    def test_export_then_import_on_another_computer(self):
        self.make("Map Pack", public_url="https://maps.example.com/", description="Twelve arena maps",
                  website="https://example.com/maps", publish_src=self.p("maps-src"))
        self.make("Server", source_url="https://server.example.com/files/", install_dir=self.p("server"))
        for out in (self.p("out", "pack.deltapatch"), self.p("out", "pack.html")):
            rc, ev = run_json(["project", "export", "Map Pack", "Server", "--out", out])
            self.assertEqual(rc, 0, ev[-1])
            self.assertEqual((ev[-1]["data"]["projects"], ev[-1]["data"]["signed"]), (["Map Pack", "Server"], False))
        made = re.compile(rb"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(Z| UTC)")                     # when each was made
        self.assertEqual(made.sub(b"", read(self.p("out", "pack.deltapatch"))),
                         made.sub(b"", read(self.p("out", "pack.html"))))                   # the same text

        self.fresh_settings("player")
        rc, ev = run_json(["project", "import", self.p("out", "pack.html"), "--dry-run"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual([p.title for p in projects.all_projects()], ["Default"])           # a dry run adds nothing
        rc, ev = run_json(["project", "import", self.p("out", "pack.html"), "--folder", self.p("games"), "--yes"])
        self.assertEqual(rc, 0, ev[-1])
        every = {p.title: config.load(p) for p in projects.all_projects()}
        self.assertEqual(sorted(every), ["Default", "Map Pack", "Server"])
        self.assertEqual((every["Map Pack"]["source_url"], every["Map Pack"]["description"], every["Map Pack"]["website"]),
                         ("https://maps.example.com/", "Twelve arena maps", "https://example.com/maps"))
        self.assertEqual(every["Server"]["source_url"], "https://server.example.com/files/")
        self.assertEqual(every["Map Pack"]["install_dir"], self.p("games", "Map Pack"))     # a folder each inside
        self.assertEqual(every["Server"]["install_dir"], self.p("games", "Server"))
        self.assertEqual(every["Map Pack"]["publish_src"], "")                             # never the maker's folders

    def test_one_project_its_folder_its_name_and_the_names_taken(self):
        self.make("Mod", public_url="https://mod.example.com/")
        rc, _ev = run_json(["project", "export", "Mod", "--out", self.p("mod.deltapatch")])
        self.assertEqual(rc, 0)
        rc, ev = run_json(["project", "import", self.p("mod.deltapatch"), "--dry-run"])
        self.assertTrue(ev[-1]["data"]["projects"][0]["taken"])                            # this computer has a Mod
        rc, ev = run_json(["project", "import", self.p("mod.deltapatch"), "--yes"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "not-added"))
        rc, ev = run_json(["project", "import", self.p("mod.deltapatch"), "--name", "Mod 2", "--folder",
                           self.p("mod2"), "--yes"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(config.load(projects.resolve("Mod 2"))["install_dir"], self.p("mod2"))  # one: that folder
        rc, ev = run_json(["project", "import", self.p("mod.deltapatch"), "--only", "Nothing", "--yes"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "no-such-project"))

    def test_wrong_kinds_of_file(self):
        self.make("Mod", public_url="https://mod.example.com/")
        rc, ev = run_json(["project", "export", "Mod", "--out", self.p("mod.txt")])
        self.assertEqual((rc, ev[-1]["code"]), (1, "bad-name"))
        write(self.p("settings.ini"), "[update]\ninstall_dir =\n")
        rc, ev = run_json(["project", "import", self.p("settings.ini"), "--yes"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "settings-file"))                        # add, not import
        run_json(["project", "export", "Mod", "--out", self.p("mod.html")])
        rc, ev = run_json(["project", "add", self.p("mod.html")])
        self.assertEqual((rc, ev[-1]["code"]), (1, "project-file"))                         # import, not add

    def test_the_unsigned_warning(self):
        self.make("Mod", public_url="https://mod.example.com/")
        run_json(["project", "export", "Mod", "--out", self.p("mod.html")])
        text = read(self.p("mod.html")).decode()
        self.assertIn("Not signed: anyone could have made or changed this file.", text)    # the page says it too
        self.fresh_settings("player")
        rc, ev = run_json(["project", "import", self.p("mod.html"), "--yes"])
        self.assertEqual(rc, 0, ev[-1])
        warned = [e for e in ev if e["event"] == "log" and e["style"] == "warn"]
        self.assertTrue(any("not signed" in e["text"] for e in warned), warned)
        self.assertEqual(ev[-1]["data"]["signed"], False)
        self.assertEqual(config.load(projects.resolve("Mod"))["trust_key"], "")

    def test_where_it_goes_travels_with_the_project(self):
        hint = "Pick the game's folder; this goes into its Mods folder."
        self.make("Map Pack", public_url="https://maps.example.com/", install_hint=hint, install_folder="Mods")
        rc, ev = run_json(["project", "export", "Map Pack", "--out", self.p("maps.html")])
        self.assertEqual(rc, 0, ev[-1])
        text = read(self.p("maps.html")).decode()
        self.assertIn("<span>Where it goes</span>Pick the game&#x27;s folder; this goes into its Mods folder.</p>", text)
        self.assertIn("<span>Its folder</span><code>Mods</code> inside the folder you pick</p>", text)
        self.fresh_settings("player")
        rc, ev = run_json(["project", "import", self.p("maps.html"), "--dry-run"])
        found = ev[-1]["data"]["projects"][0]
        self.assertEqual((found["install_hint"], found["install_folder"]), (hint, "Mods"))
        rc, ev = run_json(["project", "import", self.p("maps.html"), "--folder", self.p("game"), "--yes"])
        self.assertEqual(rc, 0, ev[-1])
        cfg = config.load(projects.resolve("Map Pack"))
        self.assertEqual((cfg["install_dir"], cfg["install_hint"], cfg["install_folder"]), (self.p("game", "Mods"), hint, "Mods"))
        row = [r for r in run_json(["projects"])[1][-1]["data"]["projects"] if r["title"] == "Map Pack"][0]
        self.assertEqual((row["install_hint"], row["install_folder"]), (hint, "Mods"))
        rc, ev = run_json(["project", "export", "Map Pack", "--out", self.p("again.deltapatch")])    # shared on by a player
        self.assertEqual(rc, 0, ev[-1])
        again = presets.read(self.p("again.deltapatch"))["projects"][0]
        self.assertEqual((again["install_hint"], again["install_folder"]), (hint, "Mods"))

    def test_the_folder_given_and_the_folder_the_file_names(self):
        self.make("Map Pack", public_url="https://maps.example.com/", install_folder="Mods")
        self.make("Sound Pack", public_url="https://sounds.example.com/")
        rc, ev = run_json(["project", "export", "Map Pack", "Sound Pack", "--out", self.p("packs.deltapatch")])
        self.assertEqual(rc, 0, ev[-1])
        self.fresh_settings("player")

        def imported(*args):
            rc, ev = run_json(["project", "import", self.p("packs.deltapatch")] + list(args) + ["--yes"])
            self.assertEqual(rc, 0, ev[-1])
            projects.remove_all()
            return {a["project"]: a["install_dir"] for a in ev[-1]["data"]["added"]}

        self.assertEqual(imported("--folder", self.p("games")),                          # several: a folder each
                         {"Map Pack": self.p("games", "Mods"), "Sound Pack": self.p("games", "Sound Pack")})
        self.assertEqual(imported("--only", "Map Pack", "--folder", self.p("game")), {"Map Pack": self.p("game", "Mods")})
        self.assertEqual(imported("--only", "Map Pack", "--folder", self.p("game", "Mods") + os.sep),
                         {"Map Pack": self.p("game", "Mods")})                           # already that folder
        self.assertEqual(imported("--only", "Map Pack", "--folder", self.p("elsewhere"), "--as-is"),
                         {"Map Pack": self.p("elsewhere")})                              # the folder as it is given
        self.assertEqual(imported("--only", "Sound Pack", "--folder", self.p("sounds")), {"Sound Pack": self.p("sounds")})
        rc, ev = run_json(["project", "import", self.p("packs.deltapatch"), "--folder", self.p("x"), "--as-is", "--yes"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "bad-arguments"))

    def test_a_terminal_says_where_it_goes(self):
        self.make("Map Pack", public_url="https://maps.example.com/",
                  install_hint="Pick the game's folder; this goes into its Mods folder.", install_folder="Mods")
        rc, ev = run_json(["project", "export", "Map Pack", "--out", self.p("maps.deltapatch")])
        self.assertEqual(rc, 0, ev[-1])
        self.fresh_settings("player")
        out = io.StringIO()
        with mock.patch.object(cli, "interactive", return_value=True), contextlib.redirect_stdout(out), \
                mock.patch("builtins.input", side_effect=["y", self.p("game")]) as keys:
            rc = cli.main(["--no-log", "project", "import", self.p("maps.deltapatch")])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("Where Map Pack goes: Pick the game's folder; this goes into its Mods folder.", out.getvalue())
        self.assertIn("it goes into Mods inside it", keys.call_args_list[-1][0][0])
        self.assertEqual(config.load(projects.resolve("Map Pack"))["install_dir"], self.p("game", "Mods"))

    def test_the_folder_name_follows_a_projects_name_rules(self):
        mod = self.make("Mod")
        for bad in ("a/b", "a\\b", "..", ".", "CON", "lpt1.txt", "-x", "x" * 61, "caf\u00e9", "a:b", "@x"):
            rc, ev = run_json(["--project", "Mod", "config", "set", "install_folder=" + bad])
            self.assertEqual((rc, ev[-1]["code"]), (1, "bad-name"), bad)
        self.assertEqual(config.load(mod)["install_folder"], "")
        rc, ev = run_json(["--project", "Mod", "config", "set", "install_folder=Mods (2)"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(config.load(mod)["install_folder"], "Mods (2)")
        write(mod.path, read(mod.path).replace(b"install_folder = Mods (2)", b"install_folder = a/b"))    # typed by hand
        config.save({"public_url": "https://mod.example.com/"}, mod)
        rc, ev = run_json(["project", "export", "Mod", "--out", self.p("mod.deltapatch")])
        self.assertEqual(rc, 1)
        self.assertIn("the folder of Mod", ev[-1]["message"])
        self.assertFalse(os.path.exists(self.p("mod.deltapatch")))

    def test_a_project_file_dropped_onto_the_program_is_imported(self):
        self.make("Mod", public_url="https://mod.example.com/")
        rc, ev = run_json(["project", "export", "Mod", "--out", self.p("Mod.deltapatch")])
        self.assertEqual(rc, 0, ev[-1])
        self.fresh_settings("player")
        out = io.StringIO()
        with mock.patch.object(cli, "interactive", return_value=True), \
                mock.patch("builtins.input", side_effect=["y", self.p("game")]), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main([self.p("Mod.deltapatch")])                     # its path the only argument
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("Mod.deltapatch holds 1 project", out.getvalue())
        self.assertIn("Added the project Mod", out.getvalue())
        cfg = config.load(projects.resolve("Mod"))
        self.assertEqual((cfg["source_url"], cfg["install_dir"]), ("https://mod.example.com/", self.p("game")))
        for other in ([self.p("gone.deltapatch")], [self.p("Mod.deltapatch"), "--no-log"]):
            with self.assertRaises(SystemExit, msg=other), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                cli.main(other)                                           # no such file; not the only argument


class NeverTheMakersComputer(Base):
    def test_no_folder_login_or_key_path(self):
        secrets_of_this_computer = {
            "install_dir": self.p("games", "MyMod"), "publish_src": self.p("dev", "MyMod"),
            "store_dir": self.p("stores", "mymod"), "deploy_folder": self.p("www", "mymod"),
            "ssh_host": "203.0.113.10", "ssh_user": "deployer", "ssh_port": 2222,
            "ssh_key": self.p("keys", "id_ed25519"), "remote_root": "/srv/www/mymod",
            "signing_key": self.p("keys", "signing-key.pem"), "sign": True,
        }
        self.make("Mod", public_url="https://mod.example.com/", **secrets_of_this_computer)
        rc, ev = run_json(["project", "export", "Mod", "--out", self.p("out", "mod.deltapatch")])
        self.assertEqual(rc, 0, ev[-1])
        text = read(self.p("out", "mod.deltapatch")).decode()
        for value in secrets_of_this_computer.values():
            if isinstance(value, str):
                self.assertNotIn(value, text)
                self.assertNotIn(value.replace("\\", "\\\\"), text)                       # as JSON would write it
        self.assertNotIn(self.tmp, text)
        self.assertNotIn(self.tmp.replace("\\", "\\\\"), text)
        self.assertIn("https://mod.example.com/", text)

    def test_the_default_address_is_no_address(self):
        self.make("Fresh")                                     # its source is still the default http://localhost:8000/
        rc, ev = run_json(["project", "export", "Fresh", "--out", self.p("fresh.html")])
        self.assertEqual((rc, ev[-1]["code"]), (1, "no-address"))
        self.assertEqual(projects.describe(projects.resolve("Fresh"))["address"], "")

    def test_a_folder_source_is_never_shared(self):
        self.make("Local", source_url=self.p("store"))
        rc, ev = run_json(["project", "export", "Local", "--out", self.p("local.html")])
        self.assertEqual((rc, ev[-1]["code"]), (1, "no-address"))
        self.assertFalse(os.path.exists(self.p("local.html")))
        with self.assertRaises(PresetError):                              # nor read from a file that has one
            presets.check_data({"format": presets.FORMAT, "version": 1, "made": "2026-11-24T10:00:00Z",
                                "projects": [{"title": "Local", "source_url": self.p("store")}]})


@unittest.skipUnless(signing.can_sign(), "needs cryptography")
class Signed(Base):
    def setUp(self):
        Base.setUp(self)
        self.key = self.p("keys", "signing-key.pem")
        self.public = signing.generate_key(self.key, PASSPHRASE)

    def export_signed(self, out, *names):
        rc, ev = run_json(["project", "export"] + list(names) + ["--out", out, "--sign", self.key, "--passphrase-stdin"],
                          PASSPHRASE + "\n")
        self.assertEqual(rc, 0, ev[-1])
        return ev[-1]["data"]

    def test_signed_round_trip_and_the_publisher_key(self):
        self.make("Signed Pack", public_url="https://pack.example.com/", sign=True, signing_key=self.key)
        self.make("Other Pack", public_url="https://other.example.com/")                 # releases not signed
        data = self.export_signed(self.p("packs.deltapatch"), "Signed Pack", "Other Pack")
        self.assertEqual((data["signed"], data["signer"]), (True, self.public))
        self.fresh_settings("player")
        rc, ev = run_json(["project", "import", self.p("packs.deltapatch"), "--yes"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual((ev[-1]["data"]["signer"], ev[-1]["data"]["fingerprint"]),
                         (self.public, signing.fingerprint(signing.parse_key(self.public))))
        self.assertEqual(config.load(projects.resolve("Signed Pack"))["trust_key"], self.public)   # no first-use gap
        self.assertEqual(config.load(projects.resolve("Other Pack"))["trust_key"], "")

    def test_one_byte_changed_anywhere_is_refused(self):
        self.make("Mod", public_url="https://mod.example.com/", sign=True, signing_key=self.key,
                  description="Maps and more")
        self.export_signed(self.p("mod.html"), "Mod")
        raw = read(self.p("mod.html"))
        self.assertTrue(presets.parse(raw)["signer"])
        for marker in (b"<h1>Mod</h1>", b"Maps and more</p>", b'"https://mod.example.com/"', b"</main>"):
            at = raw.index(marker) + 2
            changed = raw[:at] + bytes([raw[at] ^ 1]) + raw[at + 1:]
            with self.assertRaises(PresetError, msg=marker) as e:
                presets.parse(changed)
            self.assertEqual(e.exception.code, "tampered", marker)
        sig = raw.index(b'"signature": "') + 20
        with self.assertRaises(PresetError):
            presets.parse(raw[:sig] + (b"A" if raw[sig:sig + 1] != b"A" else b"B") + raw[sig + 1:])
        self.assertTrue(presets.parse(raw.replace(b"\n", b"\r\n"))["signer"])          # line endings aside
        self.assertTrue(presets.parse(b"\xef\xbb\xbf" + raw)["signer"])                 # a byte order mark aside
        rc, ev = run_json(["project", "import", self.p("mod.html"), "--dry-run"])
        self.assertEqual(rc, 0)
        write(self.p("mod.html"), raw.replace(b"https://mod.example.com/", b"https://evil.example.com/"))
        rc, ev = run_json(["project", "import", self.p("mod.html"), "--yes"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "tampered"))
        self.assertEqual([p.title for p in projects.all_projects()], ["Default", "Mod"])  # nothing added

    def test_the_first_update_already_checks_the_publisher(self):
        write(self.p("release", "game.pk3"), os.urandom(4000))
        publish.publish(self.p("release"), self.p("store"), "Game", "1.0", signer=signing.load_key(self.key, PASSPHRASE))
        srv = serve.make_server(self.p("store"), "127.0.0.1", 0, quiet=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        url = "http://127.0.0.1:%d/" % srv.server_address[1]
        self.make("Game", public_url=url, sign=True, signing_key=self.key)
        self.export_signed(self.p("game.deltapatch"), "Game")
        self.fresh_settings("player")
        rc, ev = run_json(["project", "import", self.p("game.deltapatch"), "--folder", self.p("player-game"), "--yes"])
        self.assertEqual(rc, 0, ev[-1])
        rc, ev = run_json(["--project", "Game", "update"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(snapshot(self.p("player-game")), snapshot(self.p("release")))
        other = self.p("keys", "other.pem")                    # the store now signed by someone else: refused
        signing.generate_key(other, "")
        write(self.p("release", "game.pk3"), os.urandom(4000))
        publish.publish(self.p("release"), self.p("store"), "Game", "1.1", signer=signing.load_key(other, None))
        rc, ev = run_json(["--project", "Game", "update"])
        self.assertEqual(rc, 1)
        self.assertIn("refused", ev[-1]["message"])


class Hostile(Base):
    def page(self, data_json, extra=""):
        return ("<!doctype html><html><body>%s%s%s%s</body></html>"
                % (extra, presets.DATA_OPEN, data_json, presets.CLOSE)).encode()

    def good(self, **change):
        d = {"format": presets.FORMAT, "version": 1, "made": "2026-11-24T10:00:00Z",
             "projects": [{"title": "Mod", "source_url": "https://mod.example.com/"}]}
        d.update(change)
        return d

    def refused(self, raw, code=None):
        start = time.time()
        with self.assertRaises(PresetError) as e:
            presets.parse(raw)
        self.assertLess(time.time() - start, 5)
        if code:
            self.assertEqual(e.exception.code, code)
        return e.exception

    def test_only_the_embedded_data_is_taken(self):
        found = presets.parse(self.page(json.dumps(self.good()), extra="<h1>Anything at all</h1><script>alert(1)</script>"))
        self.assertEqual(found["projects"][0]["title"], "Mod")
        self.assertEqual(found["signer"], "")

    def test_huge_broken_and_nested_files(self):
        self.refused(b"x" * (presets.LIMIT + 1), "too-big")
        write(self.p("huge.html"), b"<" * (presets.LIMIT + 10))
        with self.assertRaises(PresetError) as e:
            presets.read(self.p("huge.html"))
        self.assertEqual(e.exception.code, "too-big")
        self.refused(b"\xff\xfe\x00garbage" * 100)                                        # not text
        self.refused(b"<html>no projects here</html>")
        self.refused(self.page(json.dumps(self.good()))[:-30])                           # cut short
        self.refused(presets.DATA_OPEN.encode() + json.dumps(self.good()).encode())      # never closed
        self.refused(self.page("[" * 200000 + "]" * 200000))                             # nested past any limit
        self.refused(self.page('{"a":' * 50000 + "1" + "}" * 50000))
        self.refused(self.page(json.dumps(self.good()) + presets.CLOSE + presets.DATA_OPEN + "{}"))   # two
        self.refused(self.page('{"format": "deltapatcher-preset", "x": "</script><script>alert(1)"}'))
        self.refused((presets.DATA_OPEN * 20000 + "{}" + presets.CLOSE).encode())
        self.refused(("<script " * 150000).encode())                                      # no element at all
        self.refused(self.page(json.dumps(self.good()), extra=presets.SIG_OPEN + '{"signer": 1}'))  # overlapping
        self.refused(self.page("9" * 100000))

    def test_strict_fields(self):
        def bad(data, code=None):
            return self.refused(self.page(json.dumps(data)), code)
        good = self.good()
        self.assertEqual(presets.parse(self.page(json.dumps(good)))["projects"], good["projects"])
        bad(self.good(version=2), "newer")
        bad(self.good(version=0))
        bad(self.good(format="something-else"))
        bad(dict(self.good(), extra="field"))
        bad(self.good(made="yesterday"))
        bad(self.good(projects=[]))
        bad(self.good(projects=[{"title": "M%d" % i, "source_url": "https://x.example.com/"} for i in range(101)]))
        for p in ({"title": "Mod"}, {"source_url": "https://x.example.com/"},
                  {"title": "../../evil", "source_url": "https://x.example.com/"},
                  {"title": "CON", "source_url": "https://x.example.com/"},
                  {"title": "Mod", "source_url": "javascript:alert(1)"},
                  {"title": "Mod", "source_url": "file:///C:/Windows"},
                  {"title": "Mod", "source_url": "C:\\games\\store"},
                  {"title": "Mod", "source_url": "https://x.example.com/ with space"},
                  {"title": "Mod", "source_url": 'https://x.example.com/"onmouseover="x'},
                  {"title": "Mod", "source_url": "https://x.example.com/", "trust_key": "not a key"},
                  {"title": "Mod", "source_url": "https://x.example.com/", "description": "line\nbreak"},
                  {"title": "Mod", "source_url": "https://x.example.com/", "description": "x" * 501},
                  {"title": "Mod", "source_url": "https://x.example.com/", "website": "ftp://x.example.com/"},
                  {"title": "Mod", "source_url": "https://x.example.com/", "install_dir": "C:\\games"},
                  {"title": "Mod", "source_url": "https://x.example.com/", "ssh_host": "203.0.113.10"}):
            bad(self.good(projects=[p]))
        bad(self.good(projects=[{"title": "Mod", "source_url": "https://a.example.com/"},
                                {"title": "MOD", "source_url": "https://b.example.com/"}]))   # one name twice
        mod = {"title": "Mod", "source_url": "https://x.example.com/"}
        for folder in ("a/b", "a\\b", "..", ".", "", " Mods", "Mods ", "CON", "aux.txt", "-x", "x" * 61, "caf\u00e9",
                       "a:b", "@x", "C:", 5, None, ["Mods"]):
            bad(self.good(projects=[dict(mod, install_folder=folder)]))
        for hint in ("x" * 301, "two\nlines", "a\ttab", "a\x00b", "right\u202eleft", 5, None):
            bad(self.good(projects=[dict(mod, install_hint=hint)]))
        fine = dict(mod, install_hint="x" * 300, install_folder="Mods (2).x")
        self.assertEqual(presets.parse(self.page(json.dumps(self.good(projects=[fine]))))["projects"], [fine])
        self.refused(self.page(json.dumps(self.good()), extra=presets.SIG_OPEN + '{"signer": "x", "signature": "y"}' +
                               presets.CLOSE), "tampered")


if __name__ == "__main__":
    unittest.main()
