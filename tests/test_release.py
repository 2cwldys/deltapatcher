"""release: publish and send in one step, with the store kept out of sight; and shadow patching (the same
version published again with changed files reaches players like any other update)."""

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import cli, client, config  # noqa: E402
from deltapatcher.manifest import Manifest  # noqa: E402


def run_json(argv):
    """cli.main in --json mode: (exit code, the last event)."""
    out = io.StringIO()
    real = sys.stdout
    sys.stdout = out
    try:
        rc = cli.main(["--json", "--no-log"] + argv)
    finally:
        sys.stdout = real
    events = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    return rc, events[-1]


class Release(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        write(self.p("game", "game.exe"), b"v1 program")
        write(self.p("game", "maps", "a.bsp"), b"map a")

    def test_the_store_is_kept_out_of_sight(self):
        store = config.default_store(self.p("game"))
        self.assertEqual(os.path.dirname(os.path.dirname(store)), os.path.dirname(config.path()))  # beside the settings
        self.assertEqual(store, config.default_store(self.p("game") + os.sep))                    # the same folder
        self.assertNotEqual(store, config.default_store(self.p("other", "game")))                 # same name, apart
        self.assertTrue(os.path.basename(store).startswith("game-"))
        rc, last = run_json(["publish", self.p("game")])                                         # no store given
        self.assertEqual(rc, 0, last)
        self.assertEqual(last["data"]["store"], os.path.abspath(store))
        self.assertTrue(os.path.isfile(os.path.join(store, "manifest.json")))

    def test_release_to_a_folder(self):
        rc, _ = run_json(["config", "set", "publish_src=" + self.p("game"), "name=My Game", "version=1.0",
                          "deploy_mode=folder", "deploy_folder=" + self.p("www"),
                          "public_url=https://game.example.com/"])
        self.assertEqual(rc, 0)
        rc, last = run_json(["release"])                                                          # all from the settings
        self.assertEqual(rc, 0, last)
        data = last["data"]
        self.assertEqual(data["players_url"], "https://game.example.com/")
        self.assertEqual(data["publish"]["files"], 2)
        man = Manifest.from_json(read(self.p("www", "manifest.json")))
        self.assertEqual((man.name, man.version), ("My Game", "1.0"))
        self.assertEqual(sorted(man.files), ["game.exe", "maps/a.bsp"])

    def test_release_says_what_is_missing(self):
        rc, last = run_json(["release"])
        self.assertEqual((rc, last["code"]), (1, "no-folder"))
        rc, last = run_json(["release", self.p("game"), "--to", "sftp"])
        self.assertEqual((rc, last["code"]), (1, "no-target"))
        rc, last = run_json(["release", self.p("game"), "--to", "folder"])
        self.assertEqual((rc, last["code"]), (1, "no-target"))

    def test_shadow_patch_same_version_changed_files(self):
        # publish 1.0, a player installs it; publish 1.0 again with a fixed file: the player gets the fix
        for argv in (["config", "set", "deploy_mode=folder", "deploy_folder=" + self.p("www")],
                     ["release", self.p("game"), "--version", "1.0"]):
            self.assertEqual(run_json(argv)[0], 0)
        client.update(self.p("www"), self.p("player"))
        self.assertEqual(read(self.p("player", "game.exe")), b"v1 program")

        write(self.p("game", "game.exe"), b"v1 program, fixed")
        rc, last = run_json(["release", self.p("game"), "--version", "1.0"])
        self.assertEqual(rc, 0, last)
        self.assertEqual(last["data"]["publish"]["new_blobs"], 1)                  # only the changed file is added
        self.assertEqual(last["data"]["deploy"]["sent"], 1)                        # and only it is sent
        self.assertEqual(Manifest.from_json(read(self.p("www", "manifest.json"))).version, "1.0")
        client.update(self.p("www"), self.p("player"))
        self.assertEqual(read(self.p("player", "game.exe")), b"v1 program, fixed")

        rc, last = run_json(["release", self.p("game"), "--version", "1.0"])      # nothing changed: nothing new
        self.assertEqual((last["data"]["publish"]["new_blobs"], last["data"]["deploy"]["sent"]), (0, 0))


if __name__ == "__main__":
    unittest.main()
