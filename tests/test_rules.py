"""Picked files and folders: left out of a release, or kept by players (protect)."""

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import cli, publish  # noqa: E402
from deltapatcher.manifest import Manifest, covered, rule_matches  # noqa: E402


class Rules(unittest.TestCase):
    def test_a_picked_file_is_that_file_only(self):
        self.assertTrue(covered("config/user.cfg", ["/config/user.cfg"]))
        self.assertFalse(covered("user.cfg", ["/config/user.cfg"]))
        self.assertFalse(covered("other/config/user.cfg", ["/config/user.cfg"]))
        self.assertTrue(covered("user.cfg", ["/user.cfg"]))
        self.assertFalse(covered("config/user.cfg", ["/user.cfg"]))        # a picked top-level file is not every user.cfg

    def test_a_picked_folder_is_everything_in_it(self):
        for rule in ("/saves/", "/saves"):
            self.assertTrue(covered("saves", [rule], is_dir=True))
            self.assertTrue(covered("saves/slot1.sav", [rule]))
            self.assertTrue(covered("saves/deep/er/slot.sav", [rule]))
            self.assertFalse(covered("old/saves/slot1.sav", [rule]))
            self.assertFalse(covered("saves2/slot1.sav", [rule]))

    def test_case_does_not_matter_and_names_are_exact(self):
        self.assertTrue(covered("Config/User.CFG", ["/config/user.cfg"]))
        self.assertTrue(covered("maps/map[1].bsp", ["/maps/map[1].bsp"]))   # a picked name is not a pattern
        self.assertFalse(covered("maps/map1.bsp", ["/maps/map[1].bsp"]))

    def test_patterns(self):
        self.assertTrue(covered("a/b/debug.log", ["*.log"]))                # no / : a name in any folder
        self.assertTrue(covered("user.cfg", ["user.cfg"]))
        self.assertTrue(covered("config/user.cfg", ["user.cfg"]))
        self.assertTrue(covered("config/a/user.cfg", ["config/*.cfg"]))     # with a / : the path from the top
        self.assertFalse(covered("x/config/user.cfg", ["config/*.cfg"]))
        self.assertTrue(covered("x/cache/a.bin", ["cache/"]))               # a folder anywhere, and what is in it
        self.assertFalse(rule_matches("cache", False, "cache/"))            # a trailing / is folders only

    def test_the_manifest_protects_with_the_same_rules(self):
        m = Manifest(files=[], protect=["/saves/", "*.ini"])
        self.assertTrue(m.is_protected("saves/slot1.sav"))
        self.assertTrue(m.is_protected("config/game.INI"))
        self.assertFalse(m.is_protected("maps/a.bsp"))


class LeaveOut(TempCase):
    def make_release(self):
        for rel in ("game.exe", "maps/a.bsp", "maps/test.bsp", "screenshots/1.png", "screenshots/old/2.png",
                    "notes.txt", "debug.log", "Thumbs.db"):
            write(self.p("src", *rel.split("/")), rel.encode())

    def published(self):
        return sorted(Manifest.from_json(read(self.p("store", "manifest.json"))).files)

    def test_picked_files_and_folders_are_left_out(self):
        self.make_release()
        logged = []
        res = publish.publish(self.p("src"), self.p("store"), leave_out=["/screenshots/", "/maps/test.bsp", "*.log"],
                              log=logged.append)
        self.assertEqual(self.published(), ["game.exe", "maps/a.bsp", "notes.txt"])
        self.assertEqual(sorted(res.left_out), ["Thumbs.db", "debug.log", "maps/test.bsp", "screenshots/"])
        self.assertTrue(any(line.startswith("left out: ") and "screenshots/" in line for line in logged), logged)

    def test_deltaignore_still_works_alongside(self):
        self.make_release()
        write(self.p("src", ".deltaignore"), b"# notes stay home\nnotes.txt\n")
        publish.publish(self.p("src"), self.p("store"), leave_out=["/screenshots/"])
        self.assertEqual(self.published(), ["debug.log", "game.exe", "maps/a.bsp", "maps/test.bsp"])


class CommandLine(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        for rel in ("maps/a.bsp", "saves/slot1.sav", "user.cfg"):
            write(self.p("src", *rel.split("/")), b"x")

    def test_paths_become_exact_rules(self):
        src = self.p("src")
        self.assertEqual(cli.rule_for(src, "saves"), "/saves/")                    # a folder
        self.assertEqual(cli.rule_for(src, "maps\\a.bsp"), "/maps/a.bsp")          # Windows separators
        self.assertEqual(cli.rule_for(src, os.path.join(src, "user.cfg")), "/user.cfg")   # dragged in: absolute
        self.assertEqual(cli.rule_for(src, '"%s"' % os.path.join(src, "saves")), "/saves/")   # pasted with quotes
        self.assertEqual(cli.rule_for(src, "later/"), "/later/")                   # not there yet, said to be a folder
        self.assertEqual(cli.rule_for(src, "*.log"), "*.log")                      # a pattern stays a pattern
        self.assertIsNone(cli.rule_for(src, "  "))
        for outside in (self.p("elsewhere", "x.txt"), "..", src):
            with self.assertRaises(cli.CliError):
                cli.rule_for(src, outside)

    def run_json(self, argv):
        out = io.StringIO()
        real = sys.stdout
        sys.stdout = out
        try:
            rc = cli.main(["--json", "--no-log"] + argv)
        finally:
            sys.stdout = real
        events = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
        return rc, events[-1]

    def test_publish_leaves_out_given_or_saved_paths(self):
        rc, last = self.run_json(["publish", self.p("src"), self.p("store"), "--leave-out", "saves",
                                  "--protect", "user.cfg"])
        self.assertEqual(rc, 0, last)
        self.assertEqual(last["data"]["left_out"], ["saves/"])
        man = Manifest.from_json(read(self.p("store", "manifest.json")))
        self.assertEqual(man.protect, ["/user.cfg"])
        self.assertEqual(sorted(man.files), ["maps/a.bsp", "user.cfg"])
        # nothing given: the saved lists (separated by ;)
        self.assertEqual(self.run_json(["config", "set", "leave_out=maps/a.bsp; saves/", "protect=*.cfg"])[0], 0)
        rc, last = self.run_json(["publish", self.p("src"), self.p("store2")])
        self.assertEqual(sorted(last["data"]["left_out"]), ["maps/a.bsp", "saves/"])
        self.assertEqual(Manifest.from_json(read(self.p("store2", "manifest.json"))).protect, ["*.cfg"])
        # an empty --leave-out= means none, not the saved list (the app sends it when its list is empty)
        rc, last = self.run_json(["publish", self.p("src"), self.p("store3"), "--leave-out=", "--protect="])
        self.assertEqual(last["data"]["left_out"], [])


if __name__ == "__main__":
    unittest.main()
