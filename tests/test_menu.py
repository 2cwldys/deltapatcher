"""The guided menu: plain questions, answers saved, the same commands underneath."""

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import cli, config  # noqa: E402
from deltapatcher.manifest import Manifest  # noqa: E402


class Menu(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        write(self.p("game", "game.exe"), b"the program")
        write(self.p("game", "maps", "a.bsp"), b"a map")

    def menu(self, typed):
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=typed) as keys, contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main(["--no-log", "menu"])
        return rc, out.getvalue(), keys.call_count

    def test_set_up_a_folder_release_and_update(self):
        rc, text, _ = self.menu([
            "2", "2", self.p("www"), "https://game.example.com/", "",          # set up: a folder, its address
            "1", '"%s"' % self.p("game"), "1.0", "", "",                         # release: dragged in; yes; back
            "3", self.p("player"), self.p("www"), "",                           # update a folder from it
            "0",
        ])
        self.assertEqual(rc, 0)
        man = Manifest.from_json(read(self.p("www", "manifest.json")))
        self.assertEqual((man.version, sorted(man.files)), ("1.0", ["game.exe", "maps/a.bsp"]))
        self.assertEqual(read(self.p("player", "maps", "a.bsp")), b"a map")
        cfg = config.load()                                                         # every answer was saved
        self.assertEqual((cfg["deploy_mode"], cfg["deploy_folder"], cfg["publish_src"], cfg["install_dir"]),
                         ("folder", self.p("www"), self.p("game"), self.p("player")))
        self.assertIn("Players update from", text)
        self.assertIn("It goes to", text)

        # the second time it is mostly Enter: the same folder and version again, a fixed file (a shadow patch)
        write(self.p("game", "game.exe"), b"the program, fixed")
        rc, text, _ = self.menu(["1", "", "", "", "", "3", "", "", "", "0"])
        self.assertEqual(rc, 0)
        self.assertEqual(read(self.p("player", "game.exe")), b"the program, fixed")

    def test_release_before_setting_up_says_so(self):
        rc, text, _ = self.menu(["1", self.p("game"), "1.0", "", "0"])
        self.assertEqual(rc, 0)
        self.assertIn("not set up yet", text)
        self.assertFalse(os.path.exists(self.p("www")))

    def test_ctrl_c_in_a_question_goes_back_to_the_menu(self):
        rc, text, keys = self.menu(["1", KeyboardInterrupt(), "0"])
        self.assertEqual((rc, keys), (0, 3))
        self.assertEqual(text.count("What do you want to do?"), 2)

    def test_a_wrong_choice_is_explained(self):
        rc, text, _ = self.menu(["9", "0"])
        self.assertIn("Type one of the numbers above.", text)


if __name__ == "__main__":
    unittest.main()
