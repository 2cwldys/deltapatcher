"""Projects that follow their publisher (deltapatcher/publisher.py): what a release says about its project,
signed with it; a player asked before any of it applies (Apply, Not now); a new address or key only from
something signed with the key the player trusts; update capable project files, updated in place when opened
again; file names and release notes when the publisher shows them; and a container's project file."""

import contextlib
import io
import json
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, snapshot, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import (cli, config, deploy, hashing, presets, projects, publish, publisher, serve,  # noqa: E402
                          signing)
from deltapatcher.manifest import Manifest  # noqa: E402

PASSPHRASE = "a long passphrase for the test key"
REAL_GMTIME = time.gmtime
BOM = chr(0xFEFF)


class Clock(object):
    """time.gmtime() a second later each time it is asked: releases and project files a test makes one after
    another never share a second, as the ones people make never do."""

    def __init__(self):
        self.now = time.time()

    def gmtime(self, secs=None):
        if secs is not None:
            return REAL_GMTIME(secs)
        self.now += 1
        return REAL_GMTIME(self.now)


def run_json(argv, stdin=""):
    """cli.main in --json mode: (exit code, [events])."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(sys, "stdin", io.StringIO(stdin)):
        rc = cli.main(["--json", "--no-log"] + argv)
    return rc, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def run_text(argv):
    """cli.main as a terminal (not interactive) shows it: (exit code, its output)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
            mock.patch.object(cli, "window_of_its_own", return_value=False):
        rc = cli.main(["--no-log", "--no-color"] + argv)
    return rc, out.getvalue()


class Base(TempCase):
    """A publisher's computer and a player's (settings of their own), and a web server for a store."""

    def setUp(self):
        TempCase.setUp(self)
        clock = mock.patch("time.gmtime", Clock().gmtime)
        clock.start()
        self.addCleanup(clock.stop)
        self.publisher_settings = os.environ["DELTAPATCHER_CONFIG"]
        self.player_settings = self.p("player", "deltapatcher.ini")
        write(self.p("src", "readme.txt"), "release one\n")
        write(self.p("src", "maps", "a.map"), os.urandom(3000))

    def as_publisher(self):
        os.environ["DELTAPATCHER_CONFIG"] = self.publisher_settings

    def as_player(self):
        os.environ["DELTAPATCHER_CONFIG"] = self.player_settings

    def serve(self, store):
        """Serve a store (publishing a first release into it when it has none); returns its address."""
        if not os.path.isfile(os.path.join(store, "manifest.json")):
            publish.publish(self.p("src"), store, "Game", "0")
        srv = serve.make_server(store, "127.0.0.1", 0, quiet=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return "http://127.0.0.1:%d/" % srv.server_address[1]

    def ok(self, argv, stdin=""):
        rc, ev = run_json(argv, stdin)
        self.assertIn(rc, (0, 2), ev[-1])
        return ev[-1]["data"]

    def failed(self, argv, code, stdin=""):
        rc, ev = run_json(argv, stdin)
        self.assertEqual((rc, ev[-1]["event"], ev[-1].get("code")), (1, "error", code), ev[-1])
        return ev[-1]

    def publisher_project(self, store, url, **values):
        """The publisher's project Map Pack, releasing src into store, players updating from url."""
        self.as_publisher()
        if not any(p.title == "Map Pack" for p in projects.all_projects()):
            projects.new("Map Pack")
        settings = dict(publish_src=self.p("src"), store_dir=store, public_url=url, description="Twelve maps",
                        website="https://maps.example.com/", name="Maps", version="1")
        settings.update(values)
        config.save(settings, projects.resolve("Map Pack"))

    def release(self, *more, stdin=""):
        self.as_publisher()
        return self.ok(["--project", "Map Pack", "publish"] + list(more), stdin)

    def player_imports(self, file_name="maps.deltapatch", *export_args, **folder):
        """The publisher exports Map Pack, the player imports it into the folder game."""
        self.as_publisher()
        self.ok(["project", "export", "Map Pack", "--out", self.p(file_name)] + list(export_args))
        self.as_player()
        return self.ok(["project", "import", self.p(file_name), "--folder", folder.get("folder", self.p("game")), "--yes"])

    def manifest(self, store):
        return json.loads(read(os.path.join(store, "manifest.json")).decode("utf-8"))


class ReleaseObject(Base):
    def test_a_release_carries_its_project_and_the_signature_covers_it(self):
        store = self.p("store")
        url = self.serve(store)
        self.publisher_project(store, url, install_hint="Pick the game's folder.", install_folder="Mods")
        data = self.release("--notes", "Twelve new maps.\r\nOne removed.")
        self.assertEqual((data["details"], data["show_files"], data["notes"]), (True, True, "Twelve new maps.\nOne removed."))
        obj = self.manifest(store)["project"]
        self.assertEqual(obj, {"title": "Map Pack", "description": "Twelve maps", "website": "https://maps.example.com/",
                               "install_hint": "Pick the game's folder.", "install_folder": "Mods", "source_url": url,
                               "show_files": True, "notes": "Twelve new maps.\nOne removed."})
        man = Manifest.from_json(read(os.path.join(store, "manifest.json")))
        self.assertEqual(sorted(man.files), ["maps/a.map", "readme.txt"])                 # the files read as before
        self.assertEqual(publisher.read_object(man.project), obj)

    def test_the_client_parser_passes_over_it(self):
        """What a release of this DeltaPatcher holds beside the files is never needed to update: a manifest whose
        project object is damaged updates as one without it, as DeltaPatchers before this one read every manifest."""
        store = self.p("store")
        publish.publish(self.p("src"), store, "Game", "1", project={"title": "Map Pack", "show_files": True})
        doc = self.manifest(store)
        for hostile in ([1, 2], "text", {"title": 5}, {"source_url": "javascript:alert(1)"}, {"notes": "x" * 4001},
                        {"show_files": "yes"}, {"trust_key": "not a key"}, {"description": "a\x00b"}):
            doc["project"] = hostile
            write(os.path.join(store, "manifest.json"), json.dumps(doc))
            man = Manifest.from_json(read(os.path.join(store, "manifest.json")))
            self.assertEqual(sorted(man.files), ["maps/a.map", "readme.txt"], hostile)
            if isinstance(hostile, dict):
                with self.assertRaises(ValueError, msg=hostile):
                    publisher.read_object(hostile)
            rc, ev = run_json(["update", self.p("game-%d" % len(str(hostile))), "--source", store])
            self.assertEqual(rc, 0, ev[-1])
            self.assertFalse(ev[-1]["data"]["show_files"])                               # nothing of it is used
            self.assertNotIn("publisher_change", ev[-1]["data"])
        doc["project"] = {"title": "Map Pack", "show_files": True, "torrent": True, "seeds": ["203.0.113.10:6881"]}
        self.assertEqual(publisher.read_object(doc["project"]), {"title": "Map Pack", "show_files": True})  # later fields

    @unittest.skipUnless(signing.can_sign(), "needs cryptography")
    def test_signed_with_the_release(self):
        key = self.p("keys", "signing-key.pem")
        public = signing.generate_key(key, PASSPHRASE)
        store = self.p("store")
        url = self.serve(store)
        self.publisher_project(store, url, sign=True, signing_key=key)
        self.release("--sign", key, "--passphrase-stdin", stdin=PASSPHRASE + "\n")
        raw = read(os.path.join(store, "manifest.json"))
        obj = json.loads(raw.decode("utf-8"))["project"]
        self.assertEqual(obj["trust_key"], public)                                       # the key signing the release
        sig = read(os.path.join(store, "sigs", signing.sig_name(hashing.sha256_bytes(raw))))
        self.assertEqual(signing.key_text(signing.check_sig(sig, raw)), public)
        changed = raw.replace(b"Twelve maps", b"Twelve evil maps")
        with self.assertRaises(signing.SignError):                                       # one detail changed: refused
            signing.check_sig(sig, changed)

    def test_share_updates_off_keeps_the_notes_and_show_files_only(self):
        store = self.p("store")
        url = self.serve(store)
        self.publisher_project(store, url, share_updates=False, show_files=False)
        data = self.release("--notes", "Fixes.")
        self.assertEqual(data["details"], False)
        self.assertEqual(self.manifest(store)["project"], {"show_files": False, "notes": "Fixes."})

    def test_details_alone_make_a_new_manifest(self):
        store = self.p("store")
        url = self.serve(store)
        self.publisher_project(store, url)
        self.release()
        first = read(os.path.join(store, "manifest.json"))
        self.release()
        self.assertEqual(read(os.path.join(store, "manifest.json")), first)              # nothing changed: kept as it was
        config.save({"description": "Twelve arena maps"}, projects.resolve("Map Pack"))
        self.release()
        second = read(os.path.join(store, "manifest.json"))
        self.assertNotEqual(second, first)                                               # the same files, a new manifest
        self.assertEqual(read(os.path.join(store, "manifest.previous.json")), first)
        self.assertEqual(self.manifest(store)["project"]["description"], "Twelve arena maps")
        self.release("--notes", "Only the notes are new.")
        self.assertNotEqual(read(os.path.join(store, "manifest.json")), second)

    def test_release_notes_from_a_file_and_what_is_refused(self):
        store = self.p("store")
        url = self.serve(store)
        self.publisher_project(store, url)
        write(self.p("notes.txt"), BOM + "What is new:\r\n\r\n- twelve maps   \r\n- a faster start\r\n\r\n")
        data = self.release("--notes-file", self.p("notes.txt"))
        self.assertEqual(data["notes"], "What is new:\n\n- twelve maps\n- a faster start")
        self.assertEqual(self.manifest(store)["project"]["notes"], data["notes"])
        self.failed(["--project", "Map Pack", "publish", "--notes", "x" * 4001], "bad-notes")
        self.failed(["--project", "Map Pack", "publish", "--notes", "a\x07bell"], "bad-notes")
        self.failed(["--project", "Map Pack", "publish", "--notes", "a", "--notes-file", self.p("notes.txt")],
                    "bad-arguments")
        self.failed(["--project", "Map Pack", "publish", "--notes-file", self.p("none.txt")], "unreadable")
        write(self.p("long.txt"), "x" * 20000)
        self.failed(["--project", "Map Pack", "publish", "--notes-file", self.p("long.txt")], "bad-notes")
        self.assertEqual(self.manifest(store)["project"]["notes"], data["notes"])         # nothing published since


class ThePrompt(Base):
    def setUp(self):
        Base.setUp(self)
        self.store = self.p("store")
        self.url = self.serve(self.store)
        self.publisher_project(self.store, self.url)
        self.release("--notes", "The first release.")

    def check(self):
        self.as_player()
        return self.ok(["--project", "Map Pack", "update", "--check"])

    def test_nothing_changed_nothing_asked(self):
        self.player_imports()
        data = self.check()
        self.assertNotIn("publisher_change", data)
        self.assertEqual(data["notes"], "The first release.")
        self.ok(["--project", "Map Pack", "update"])
        self.assertNotIn("publisher_change", self.check())

    def test_a_change_is_asked_and_not_now_holds_until_the_next_change(self):
        self.player_imports()
        self.publisher_project(self.store, self.url, description="Twelve arena maps", website="")
        self.release()
        change = self.check()["publisher_change"]
        self.assertEqual(change["words"], "description, website")
        self.assertEqual([(i["field"], i["from"], i["to"]) for i in change["changes"]],
                         [("description", "Twelve maps", "Twelve arena maps"), ("website", "https://maps.example.com/", "")])
        self.assertEqual((change["moves"], change["warning"], change["project"]), ([], "", "Map Pack"))
        data = self.ok(["project", "accept", "Map Pack", "--not-now", "--expect", change["hash"]])
        self.assertTrue(data["not_now"])
        self.assertNotIn("publisher_change", self.check())                               # held
        self.assertNotIn("publisher_change", self.ok(["update", "--all", "--check"])["projects"][0]["data"])
        held = self.ok(["project", "changes", "Map Pack"])
        self.assertEqual((held["held"], held["change"]["hash"]), (True, change["hash"]))  # shown when asked for
        self.assertEqual(config.load(projects.resolve("Map Pack"))["description"], "Twelve maps")   # nothing applied
        self.publisher_project(self.store, self.url, description="Twenty arena maps", website="")
        self.release()
        again = self.check()["publisher_change"]                                          # changed again: asked again
        self.assertNotEqual(again["hash"], change["hash"])
        self.assertEqual(again["changes"][0]["to"], "Twenty arena maps")

    def test_apply_keeps_the_players_own_settings(self):
        self.player_imports()
        mine = projects.resolve("Map Pack")
        projects.rename(mine, "My Maps")
        config.save({"workers": 7, "website": "https://mine.example.com/", "auto_update": True, "check_every_hours": 5},
                    mine)
        before = config.load(mine)
        self.publisher_project(self.store, self.url, description="Twelve arena maps", install_folder="Maps")
        self.release()
        self.as_player()
        change = self.ok(["--project", "My Maps", "update", "--check"])["publisher_change"]
        self.failed(["project", "accept", "My Maps"], "confirm-needed")                   # asks first
        data = self.ok(["project", "accept", "My Maps", "--yes", "--expect", change["hash"]])
        self.assertEqual(data["applied"], {"description": "Twelve arena maps", "install_folder": "Maps"})
        after = config.load(projects.resolve("My Maps"))
        for key in ("title", "install_dir", "workers", "auto_update", "check_every_hours", "follow_publisher",
                    "source_url", "trust_key", "install_hint", "website"):
            self.assertEqual(after[key], before[key], key)                                # the player's own stay
        self.assertEqual((after["description"], after["install_folder"]), ("Twelve arena maps", "Maps"))
        self.assertEqual(after["install_dir"], self.p("game"))                            # the folder never moves
        self.assertNotIn("publisher_change", self.ok(["--project", "My Maps", "update", "--check"]))
        self.ok(["project", "accept", "My Maps", "--yes"])                                # nothing waits: nothing happens

    def test_a_change_shown_before_is_the_one_applied(self):
        self.player_imports()
        self.publisher_project(self.store, self.url, description="Twelve arena maps")
        self.release()
        shown = self.check()["publisher_change"]
        self.publisher_project(self.store, self.url, description="Something else entirely")
        self.release()
        self.as_player()
        self.failed(["project", "accept", "Map Pack", "--yes", "--expect", shown["hash"]], "changed-since")
        self.assertEqual(config.load(projects.resolve("Map Pack"))["description"], "Twelve maps")

    def test_follow_publisher_off_is_never_asked(self):
        self.player_imports()
        config.save({"follow_publisher": False}, projects.resolve("Map Pack"))
        self.publisher_project(self.store, self.url, description="Twelve arena maps")
        self.release()
        self.assertNotIn("publisher_change", self.check())
        self.assertFalse(self.ok(["project", "changes", "Map Pack"])["follow_publisher"])

    def test_a_one_off_file_does_not_follow(self):
        self.player_imports("maps.html", "--no-updates")
        self.assertFalse(config.load(projects.resolve("Map Pack"))["follow_publisher"])
        self.publisher_project(self.store, self.url, description="Twelve arena maps")
        self.release()
        self.assertNotIn("publisher_change", self.check())

    def test_another_source_on_the_command_line_is_not_the_project(self):
        self.player_imports()
        self.publisher_project(self.store, self.url, description="Twelve arena maps")
        self.release()
        self.as_player()
        data = self.ok(["--project", "Map Pack", "update", self.p("elsewhere"), "--source", self.store, "--check"])
        self.assertNotIn("publisher_change", data)

    def test_the_terminal_says_it_and_how_to_apply_it(self):
        self.player_imports()
        self.publisher_project(self.store, self.url, description="Twelve arena maps")
        self.release()
        self.as_player()
        rc, text = run_text(["--project", "Map Pack", "check"])
        self.assertEqual(rc, 2, text)
        self.assertIn("The publisher changed this project: description.", text)
        self.assertIn("description  Twelve maps  ->  Twelve arena maps", text)
        self.assertIn('project accept "Map Pack"', text)
        rc, text = run_text(["project", "changes", "Map Pack"])
        self.assertEqual(rc, 0, text)
        self.assertIn("The publisher changed Map Pack: description.", text)


class AddressAndKey(Base):
    def moved(self):
        """The publisher moves Map Pack from one store to another: (old store, old address, new store, new address)."""
        old, new = self.p("store"), self.p("store2")
        old_url, new_url = self.serve(old), self.serve(new)
        self.publisher_project(old, old_url)
        return old, old_url, new, new_url

    def test_unsigned_a_move_is_asked_with_the_stronger_warning(self):
        old, old_url, new, new_url = self.moved()
        self.release()
        self.player_imports()
        self.publisher_project(old, new_url)                       # the last release at the old address names the new one
        self.release()
        self.as_player()
        change = self.ok(["--project", "Map Pack", "update", "--check"])["publisher_change"]
        self.assertEqual((change["moves"], change["trust"]), (["source_url"], "unsigned"))
        self.assertIn("anyone who can change that server could have sent this", change["warning"])
        cfg = config.load(projects.resolve("Map Pack"))
        self.assertEqual(cfg["source_url"], old_url)                                      # never followed by itself
        self.ok(["--project", "Map Pack", "update"])
        self.assertEqual(config.load(projects.resolve("Map Pack"))["source_url"], old_url)
        self.ok(["project", "accept", "Map Pack", "--yes"])
        self.assertEqual(config.load(projects.resolve("Map Pack"))["source_url"], new_url)

    @unittest.skipUnless(signing.can_sign(), "needs cryptography")
    def test_a_new_key_comes_signed_with_the_old_one(self):
        old_key, new_key = self.p("keys", "old.pem"), self.p("keys", "new.pem")
        old_public, new_public = signing.generate_key(old_key, ""), signing.generate_key(new_key, "")
        old, old_url, new, new_url = self.moved()
        self.publisher_project(old, old_url, sign=True, signing_key=old_key)
        self.release("--sign", old_key)
        self.player_imports("maps.deltapatch", "--sign", old_key)
        self.assertEqual(config.load(projects.resolve("Map Pack"))["trust_key"], old_public)
        self.as_player()
        self.ok(["--project", "Map Pack", "update"])
        # the publisher announces the new key and address in a release signed with the old key
        self.publisher_project(old, new_url, trust_key=new_public)
        self.release("--sign", old_key)
        self.as_player()
        change = self.ok(["--project", "Map Pack", "update", "--check"])["publisher_change"]
        self.assertEqual((sorted(change["moves"]), change["trust"]), (["source_url", "trust_key"], "signed"))
        self.assertEqual((change["signer"], change["trusted"]), (old_public, old_public))
        self.assertIn("signed with the publisher key this project trusts", change["warning"])
        self.ok(["project", "accept", "Map Pack", "--yes", "--expect", change["hash"]])
        cfg = config.load(projects.resolve("Map Pack"))
        self.assertEqual((cfg["source_url"], cfg["trust_key"]), (new_url, new_public))
        # from now on the new address, signed with the new key
        self.publisher_project(new, new_url, trust_key="", sign=True, signing_key=new_key)
        write(self.p("src", "readme.txt"), "release two\n")
        self.release("--sign", new_key)
        self.as_player()
        self.ok(["--project", "Map Pack", "update"])
        self.assertEqual(read(self.p("game", "readme.txt")), b"release two\n")
        self.assertNotIn("publisher_change", self.ok(["--project", "Map Pack", "update", "--check"]))

    @unittest.skipUnless(signing.can_sign(), "needs cryptography")
    def test_not_signed_with_the_trusted_key_never_moves_it(self):
        key, other = self.p("keys", "old.pem"), self.p("keys", "other.pem")
        public = signing.generate_key(key, "")
        signing.generate_key(other, "")
        old, old_url, new, new_url = self.moved()
        self.publisher_project(old, old_url, sign=True, signing_key=key)
        self.release("--sign", key)
        self.player_imports("maps.deltapatch", "--sign", key)
        # a release at the old address, not signed with the key the player trusts: the check itself refuses it
        self.publisher_project(old, new_url)
        self.release("--sign", other)
        self.as_player()
        rc, ev = run_json(["--project", "Map Pack", "update", "--check"])
        self.assertEqual(rc, 1)
        self.assertIn("refused", ev[-1]["message"])
        rc, ev = run_json(["project", "accept", "Map Pack", "--yes"])
        self.assertEqual(rc, 1)
        self.assertEqual(config.load(projects.resolve("Map Pack"))["source_url"], old_url)
        # a project file not signed with that key, naming it and a new address: its details apply, the address not
        self.as_publisher()
        self.publisher_project(old, new_url, description="Twelve arena maps", trust_key=public)
        self.ok(["project", "export", "Map Pack", "--out", self.p("unsigned.deltapatch")])
        self.as_player()
        data = self.ok(["project", "import", self.p("unsigned.deltapatch"), "--dry-run"])
        row = data["projects"][0]
        self.assertEqual((row["updates"]["project"], row["change"]["trust"]), ("Map Pack", "refused"))
        data = self.ok(["project", "import", self.p("unsigned.deltapatch"), "--yes"])
        self.assertEqual(data["updated"][0]["applied"], {"description": "Twelve arena maps"})
        self.assertEqual(data["updated"][0]["refused"], ["the address"])
        cfg = config.load(projects.resolve("Map Pack"))
        self.assertEqual((cfg["source_url"], cfg["description"], cfg["trust_key"]), (old_url, "Twelve arena maps", public))
        # the same file signed with the trusted key: the address moves too
        self.as_publisher()
        self.ok(["project", "export", "Map Pack", "--out", self.p("signed.deltapatch"), "--sign", key])
        self.as_player()
        data = self.ok(["project", "import", self.p("signed.deltapatch"), "--yes"])
        self.assertEqual(data["updated"][0]["applied"], {"source_url": new_url})
        self.assertEqual(config.load(projects.resolve("Map Pack"))["source_url"], new_url)


class ProjectFiles(Base):
    def setUp(self):
        Base.setUp(self)
        self.store = self.p("store")
        self.url = self.serve(self.store)
        self.publisher_project(self.store, self.url)
        self.release()

    def test_update_capable_by_default_version_1_when_not(self):
        self.as_publisher()
        data = self.ok(["project", "export", "Map Pack", "--out", self.p("capable.deltapatch")])
        self.assertTrue(data["updates"])
        found = presets.read(self.p("capable.deltapatch"))
        self.assertEqual((found["version"], found["updates"]), (2, True))
        self.assertIn(b"Update capable: the publisher can change this project later", read(self.p("capable.deltapatch")))
        data = self.ok(["project", "export", "Map Pack", "--out", self.p("once.deltapatch"), "--no-updates"])
        self.assertFalse(data["updates"])
        found = presets.read(self.p("once.deltapatch"))
        self.assertEqual((found["version"], found["updates"]), (1, False))
        self.assertNotIn(b"Update capable", read(self.p("once.deltapatch")))
        config.save({"share_updates": False}, projects.resolve("Map Pack"))
        self.assertFalse(self.ok(["project", "export", "Map Pack", "--out", self.p("default.html")])["updates"])
        self.assertTrue(self.ok(["project", "export", "Map Pack", "--out", self.p("asked.html"), "--updates"])["updates"])
        # the DeltaPatcher before update capable files reads version 1 and asks to be updated for version 2
        with mock.patch.object(presets, "VERSION", 1):
            self.assertEqual(presets.read(self.p("once.deltapatch"))["projects"][0]["title"], "Map Pack")
            with self.assertRaises(presets.PresetError) as e:
                presets.read(self.p("capable.deltapatch"))
            self.assertEqual(e.exception.code, "newer")
            self.assertIn("update DeltaPatcher first", str(e.exception))

    def test_a_version_1_file_still_reads_and_adds(self):
        text = presets.build([{"title": "Old Pack", "source_url": "https://old.example.com/", "description": "Made before"}],
                             made="2026-11-20T10:00:00Z")
        self.assertIn('"version": 1', text)
        self.assertNotIn('"updates"', text)
        presets.write(self.p("old.deltapatch"), text)
        self.as_player()
        data = self.ok(["project", "import", self.p("old.deltapatch"), "--yes"])
        self.assertEqual(([a["project"] for a in data["added"]], data["updates"]), (["Old Pack"], False))
        cfg = config.load(projects.resolve("Old Pack"))
        self.assertEqual((cfg["description"], cfg["follow_publisher"]), ("Made before", False))
        self.assertEqual(publisher.accepted_of(cfg)["description"], "Made before")       # what a change is compared with

    def test_opening_a_newer_file_updates_in_place_and_as_new_adds_one(self):
        imported = self.player_imports()
        self.assertEqual([a["project"] for a in imported["added"]], ["Map Pack"])
        self.assertTrue(config.load(projects.resolve("Map Pack"))["follow_publisher"])
        self.publisher_project(self.store, self.url, description="Twelve arena maps")
        self.as_publisher()
        self.ok(["project", "export", "Map Pack", "--out", self.p("newer.deltapatch")])
        self.as_player()
        look = self.ok(["project", "import", self.p("newer.deltapatch"), "--dry-run"])
        row = look["projects"][0]
        self.assertEqual((row["updates"]["project"], row["change"]["words"]), ("Map Pack", "description"))
        data = self.ok(["project", "import", self.p("newer.deltapatch"), "--folder", self.p("other"), "--yes"])
        self.assertEqual((data["added"], data["updated"][0]["applied"]), ([], {"description": "Twelve arena maps"}))
        self.assertEqual([p.title for p in projects.all_projects()], ["Default", "Map Pack"])     # no second project
        cfg = config.load(projects.resolve("Map Pack"))
        self.assertEqual((cfg["description"], cfg["install_dir"]), ("Twelve arena maps", self.p("game")))
        self.assertNotIn("publisher_change", self.ok(["--project", "Map Pack", "update", "--check"]))
        same = self.ok(["project", "import", self.p("newer.deltapatch"), "--yes"])        # again: nothing changes
        self.assertEqual(same["updated"][0]["applied"], {})
        self.failed(["project", "import", self.p("newer.deltapatch"), "--name", "Maps 2", "--yes"], "bad-arguments")
        data = self.ok(["project", "import", self.p("newer.deltapatch"), "--as-new", "--name", "Maps 2",
                        "--folder", self.p("game2"), "--yes"])
        self.assertEqual([a["project"] for a in data["added"]], ["Maps 2"])
        self.assertEqual([p.title for p in projects.all_projects()], ["Default", "Map Pack", "Maps 2"])
        self.failed(["project", "import", self.p("newer.deltapatch"), "--as-new", "--yes"], "not-added")   # name taken

    def test_details_made_before_the_ones_accepted_are_never_offered(self):
        self.player_imports()
        self.publisher_project(self.store, self.url, description="Twelve arena maps")
        self.as_publisher()
        self.ok(["project", "export", "Map Pack", "--out", self.p("newer.deltapatch")])
        self.as_player()
        self.ok(["project", "import", self.p("newer.deltapatch"), "--yes"])
        # the last release was made before that file: its older description is not offered back
        self.assertNotIn("publisher_change", self.ok(["--project", "Map Pack", "update", "--check"]))
        self.assertIsNone(self.ok(["project", "changes", "Map Pack"])["change"])
        # nor an older file, opened again
        data = self.ok(["project", "import", self.p("maps.deltapatch"), "--yes"])
        self.assertEqual((data["updated"][0]["older"], data["updated"][0]["applied"]), (True, {}))
        self.assertEqual(config.load(projects.resolve("Map Pack"))["description"], "Twelve arena maps")
        # a release made after it is
        self.publisher_project(self.store, self.url, description="Twenty maps")
        self.release()
        self.as_player()
        change = self.ok(["--project", "Map Pack", "update", "--check"])["publisher_change"]
        self.assertEqual((change["changes"][0]["from"], change["changes"][0]["to"]), ("Twelve arena maps", "Twenty maps"))

    def test_a_terminal_asks_once_and_says_what_it_updates(self):
        self.player_imports()
        self.publisher_project(self.store, self.url, description="Twelve arena maps")
        self.as_publisher()
        self.ok(["project", "export", "Map Pack", "--out", self.p("newer.deltapatch")])
        self.as_player()
        out = io.StringIO()
        with mock.patch.object(cli, "interactive", return_value=True), contextlib.redirect_stdout(out), \
                mock.patch("builtins.input", side_effect=["y"]) as keys:
            rc = cli.main(["--no-log", "--no-color", "project", "import", self.p("newer.deltapatch")])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn("(updates the project Map Pack: description)", out.getvalue())
        self.assertIn("Update it", keys.call_args_list[0][0][0])
        self.assertIn("Updated the project Map Pack: description", out.getvalue())


class ShowFiles(Base):
    def setUp(self):
        Base.setUp(self)
        self.store = self.p("store")
        self.url = self.serve(self.store)

    def released(self, show):
        self.publisher_project(self.store, self.url, show_files=show)
        self.release("--notes", "Notes for players.")
        self.player_imports()
        write(self.p("src", "readme.txt"), "release two\n")
        write(self.p("src", "maps", "b.map"), "new map\n")
        os.remove(self.p("src", "maps", "a.map"))
        self.as_player()
        self.ok(["--project", "Map Pack", "update"])
        write(self.p("game", "mine.txt"), "the player's own\n")
        self.as_publisher()
        self.release("--notes", "Map b replaces map a.")
        self.as_player()

    def test_on_the_files_by_name(self):
        self.released(True)
        data = self.ok(["--project", "Map Pack", "update", "--check", "--files"])
        self.assertEqual(data["changes"], {"new": ["maps/b.map"], "changed": ["readme.txt"], "removed": ["maps/a.map"],
                                           "kept": []})
        self.assertEqual([f["path"] for f in data["files"]], ["maps/b.map", "readme.txt"])
        self.assertEqual(data["notes"], "Map b replaces map a.")
        self.assertEqual(data["release"]["version"], "1")
        self.assertNotIn("files", self.ok(["--project", "Map Pack", "update", "--check"]))   # the whole list when asked
        rc, text = run_text(["--project", "Map Pack", "check"])
        self.assertIn("new           maps/b.map", text)
        self.assertIn("removed       maps/a.map", text)
        self.assertIn("Map b replaces map a.", text)
        data = self.ok(["--project", "Map Pack", "update"])
        self.assertEqual(data["changes"]["new"], ["maps/b.map"])

    def test_off_counts_only(self):
        self.released(False)
        data = self.ok(["--project", "Map Pack", "update", "--check", "--files"])
        self.assertEqual((data["show_files"], data["add"], data["replace"], data["remove"]), (False, 1, 1, 1))
        self.assertNotIn("changes", data)
        self.assertNotIn("files", data)
        self.assertEqual(data["notes"], "Map b replaces map a.")                         # the notes are the publisher's
        rc, text = run_text(["--project", "Map Pack", "check", "--files"])
        for name in ("maps/b.map", "maps/a.map", "b.map"):
            self.assertNotIn(name, text)
        data = self.ok(["update", "--all", "--check", "--files"])["projects"][0]["data"]
        self.assertNotIn("changes", data)


class Containers(Base):
    """update FOLDER --project-file FILE: the address and key from a project file; nothing added or saved."""

    def setUp(self):
        Base.setUp(self)
        self.store = self.p("store")
        self.url = self.serve(self.store)
        self.publisher_project(self.store, self.url)
        self.release()
        self.as_publisher()
        self.ok(["project", "export", "Map Pack", "--out", self.p("maps.deltapatch")])
        self.as_player()
        config.ensure()

    def settings_now(self):
        path = os.environ["DELTAPATCHER_CONFIG"]
        return read(path) if os.path.isfile(path) else b""

    def test_unsigned_file(self):
        before = self.settings_now()
        rc, ev = run_json(["update", self.p("server"), "--project-file", self.p("maps.deltapatch")])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(snapshot(self.p("server")), snapshot(self.p("src")))
        self.assertTrue(any("not signed" in e.get("text", "") for e in ev if e["event"] == "log"))
        self.assertEqual([p.title for p in projects.all_projects()], ["Default"])        # nothing added
        self.assertEqual(self.settings_now(), before)                                     # nothing saved

    @unittest.skipUnless(signing.can_sign(), "needs cryptography")
    def test_signed_file_and_its_key(self):
        key = self.p("keys", "signing-key.pem")
        public = signing.generate_key(key, "")
        self.publisher_project(self.store, self.url, sign=True, signing_key=key)
        self.release("--sign", key)
        self.as_publisher()
        self.ok(["project", "export", "Map Pack", "--out", self.p("signed.html"), "--sign", key])
        self.as_player()
        data = self.ok(["update", self.p("server"), "--project-file", self.p("signed.html")])
        self.assertEqual(data["publisher_key"], public)
        other = self.p("keys", "other.pem")                                     # releases signed by someone else: refused
        signing.generate_key(other, "")
        self.publisher_project(self.store, self.url, sign=True, signing_key=other)
        self.release("--sign", other)
        self.as_player()
        rc, ev = run_json(["update", self.p("server"), "--project-file", self.p("signed.html")])
        self.assertEqual(rc, 1)
        self.assertIn("refused", ev[-1]["message"])

    def test_two_projects_and_only(self):
        self.as_publisher()
        projects.new("Sounds")
        config.save({"public_url": self.url, "description": "Sounds"}, projects.resolve("Sounds"))
        self.ok(["project", "export", "Map Pack", "Sounds", "--out", self.p("both.deltapatch")])
        self.as_player()
        self.failed(["update", self.p("server"), "--project-file", self.p("both.deltapatch")], "bad-arguments")
        self.failed(["update", self.p("server"), "--project-file", self.p("both.deltapatch"), "--only", "Nothing"],
                    "no-such-project")
        self.ok(["update", self.p("server"), "--project-file", self.p("both.deltapatch"), "--only", "map pack"])
        self.assertEqual(snapshot(self.p("server")), snapshot(self.p("src")))
        self.failed(["update", self.p("server"), "--project-file", self.p("both.deltapatch"), "--source", self.url],
                    "bad-arguments")
        self.failed(["update", "--all", "--project-file", self.p("both.deltapatch")], "all-with-folder")
        self.failed(["update", self.p("server"), "--only", "Sounds"], "bad-arguments")

    def test_a_damaged_file_is_refused(self):
        raw = read(self.p("maps.deltapatch"))
        write(self.p("broken.deltapatch"), raw.replace(b'"source_url": "', b'"source_url": "javascript:'))
        self.failed(["update", self.p("server"), "--project-file", self.p("broken.deltapatch")], "not-a-project-file")
        write(self.p("cut.deltapatch"), raw[:200])
        self.failed(["update", self.p("server"), "--project-file", self.p("cut.deltapatch")], "not-a-project-file")
        self.assertFalse(os.path.exists(self.p("server")))

    def test_a_move_is_a_warning_and_a_newer_file_is_used(self):
        self.ok(["update", self.p("server"), "--project-file", self.p("maps.deltapatch")])
        new = self.p("store2")
        new_url = self.serve(new)
        self.publisher_project(self.store, new_url)
        self.release()
        self.as_player()
        rc, ev = run_json(["update", self.p("server"), "--project-file", self.p("maps.deltapatch")])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(ev[-1]["data"]["publisher_moved"], ["the publisher moved this project to %s" % new_url])
        self.assertTrue(any("hand this container the new project file" in e.get("text", "") for e in ev))
        deploy.deploy(self.store, deploy.FolderTarget(new))                    # the release at its new address
        self.publisher_project(new, new_url)
        self.as_publisher()
        self.ok(["project", "export", "Map Pack", "--out", self.p("maps.deltapatch")])   # the container gets the new file
        write(self.p("src", "readme.txt"), "release two\n")
        self.release()
        self.as_player()
        rc, ev = run_json(["update", self.p("server"), "--project-file", self.p("maps.deltapatch")])
        self.assertEqual(rc, 0, ev[-1])
        self.assertNotIn("publisher_moved", ev[-1]["data"])
        self.assertEqual(read(self.p("server", "readme.txt")), b"release two\n")


if __name__ == "__main__":
    unittest.main()
