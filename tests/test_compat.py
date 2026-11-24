"""What an earlier DeltaPatcher wrote keeps working. Each folder in tests/fixtures/compat/ is the settings,
projects, release and updated game folder one version wrote (tests/fixtures/compat/make_fixture.py made
it, and it is never changed). The current code must read those settings as they are, list the same
projects, keep a person's own lines when it saves, recognise the folder that version updated, roll that
update back with that version's rollback copy, and update again from that version's release.

A new release adds its own fixture: python tests/fixtures/compat/make_fixture.py <its code> <its version>.
"""

import hashlib
import io
import json
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from helpers import TempCase  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import cli, config, projects  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures", "compat")
RENAMED = {"main.ini": "deltapatcher.ini", "dot-deltapatcher": ".deltapatcher"}
TEXT = (".ini", ".json", ".jsonl", ".log", ".txt", ".cfg", ".map")


def versions():
    return sorted(n for n in os.listdir(FIXTURES) if os.path.isdir(os.path.join(FIXTURES, n)))


def unpack(name, dest):
    """A copy of the fixture at dest, its names and @FIXTURE@ paths as on the computer that wrote it."""
    root = dest.replace("\\", "/")
    src = os.path.join(FIXTURES, name)
    for top, _dirs, files in os.walk(src):
        rel = os.path.relpath(top, src)
        parts = [] if rel == "." else [RENAMED.get(p, p) for p in rel.split(os.sep)]
        for n in files:
            with open(os.path.join(top, n), "rb") as f:
                data = f.read()
            if n.lower().endswith(TEXT) or (parts and parts[-1] == ".deltapatcher"):
                data = data.decode("utf-8").replace("@FIXTURE@", root).encode("utf-8")
            out = os.path.join(dest, *(parts + [RENAMED.get(n, n)]))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as f:
                f.write(data)
    with open(os.path.join(src, "expect.json"), encoding="utf-8") as f:
        return json.load(f)


def tree(root):
    out = {}
    for top, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".deltapatcher"]
        for n in files:
            p = os.path.join(top, n)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if rel == "config/user.cfg":
                continue                                         # protected: the player's own copy
            with open(p, "rb") as f:
                out[rel] = hashlib.sha256(f.read()).hexdigest()
    return out


def run(*argv):
    """The command line as the app runs it; (exit code, the last event)."""
    out = io.StringIO()
    real = sys.stdout
    sys.stdout = out
    try:
        rc = cli.main(["--json", "--no-log"] + list(argv))
    finally:
        sys.stdout = real
    events = [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]
    return rc, events[-1] if events else {}


def same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def text_of(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class Compat(TempCase):
    def test_every_version_keeps_working(self):
        self.assertIn("0.3.0", versions())
        for name in versions():
            with self.subTest(version=name):
                self.check_version(name)

    def check_version(self, name):
        dest = self.p("v-" + name)
        want = unpack(name, dest)
        settings = os.path.join(dest, "settings", "deltapatcher.ini")
        old = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))
        os.environ["DELTAPATCHER_CONFIG"] = settings
        os.environ.pop("DELTAPATCHER_PROJECT", None)
        game = os.path.join(dest, "game")

        cfg = config.load()                                              # its settings, read as they are
        self.assertTrue(same_path(cfg["install_dir"], game), cfg["install_dir"])
        self.assertTrue(same_path(cfg["source_url"], os.path.join(dest, "release")), cfg["source_url"])

        every = projects.all_projects()                                  # its projects, all of them
        if want["projects"]:
            self.assertEqual([p.title for p in every], ["Default", "Dedicated Server", "Map Pack"])
            self.assertEqual([p.kind for p in every], ["main", "added", "folder"])
            self.assertEqual(projects.current().title, "Map Pack")
            self.assertEqual(cfg["parallel_updates"], 2)
            maps = config.load(projects.resolve("Map Pack"))
            self.assertTrue(same_path(maps["install_dir"], os.path.join(dest, "maps")))
        else:
            self.assertEqual([p.title for p in every], ["Default"])

        rc, last = run("config", "set", "workers=6")                    # saving keeps a person's own lines
        self.assertEqual(rc, 0, last)
        text = text_of(settings)
        self.assertIn("; a note the user wrote, which every later version must keep", text)
        self.assertIn("[mine]\nnote = kept", text)
        self.assertEqual(config.load()["workers"], 6)

        rc, last = run("rollback")                                       # with that version's rollback copy
        self.assertEqual(rc, 0, last)
        self.assertEqual(tree(game), want["release1"])
        self.assertEqual(text_of(os.path.join(game, "config", "user.cfg")), want["user_cfg"])

        rc, last = run("update", game, "--source", os.path.join(dest, "release"))   # from that version's release
        self.assertEqual(rc, 0, last)
        self.assertEqual(tree(game), want["release2"])
        self.assertEqual(text_of(os.path.join(game, "config", "user.cfg")), want["user_cfg"])
        rc, last = run("check", game, "--source", os.path.join(dest, "release"))
        self.assertEqual(rc, 0, last)
        self.assertTrue(last["data"]["empty"])

        if want["projects"]:                                             # and every project at once
            rc, last = run("update", "--all")
            self.assertEqual(rc, 0, last)
            self.assertEqual(tree(os.path.join(dest, "maps")), want["release2"])
            self.assertEqual(tree(os.path.join(dest, "server")), want["release2"])


if __name__ == "__main__":
    unittest.main()
