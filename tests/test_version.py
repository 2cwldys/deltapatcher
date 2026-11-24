"""tools/version.py: every copy of the version agrees, and setting it writes them all and nothing else."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import version as tool  # noqa: E402  (tools/version.py)


class Version(unittest.TestCase):
    def test_every_copy_agrees(self):
        found = tool.copies()
        self.assertEqual(len(set(found.values())), 1, found)
        self.assertRegex(found["deltapatcher/__init__.py"], r"^\d+\.\d+\.\d+$")
        # pyproject.toml has no copy of its own: it reads the package's
        with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn('dynamic = ["version"]', text)
        self.assertIn('attr = "deltapatcher.__version__"', text)

    def test_the_next_version(self):
        self.assertEqual(tool.bumped("0.2.0", "1.4.2"), "1.4.2")
        self.assertEqual(tool.bumped("0.2.0", "v1.4.2"), "1.4.2")
        self.assertEqual(tool.bumped("0.2.0", "patch"), "0.2.1")
        self.assertEqual(tool.bumped("0.2.9", "minor"), "0.3.0")
        self.assertEqual(tool.bumped("0.3.4", "major"), "1.0.0")
        for bad in ("1.4", "1.4.2-beta", "latest"):
            with self.assertRaises(SystemExit):
                tool.bumped("0.2.0", bad)

    def test_setting_it_writes_every_copy_and_nothing_else(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        paths = {}
        for name in ("INIT", "PACKAGE", "LOCK"):
            real = getattr(tool, name)
            paths[name] = os.path.join(tmp, os.path.basename(real))
            shutil.copyfile(real, paths[name])
        with mock.patch.multiple(tool, **paths):
            before = {k: tool.read(p) for k, p in paths.items()}
            tool.set_version("9.8.7")
            self.assertEqual(set(tool.copies().values()), {"9.8.7"})
            for k, p in paths.items():
                old, new = before[k].splitlines(), tool.read(p).splitlines()
                self.assertEqual(len(old), len(new), k)
                changed = [a for a, b in zip(old, new) if a != b]
                self.assertEqual(len(changed), 2 if k == "LOCK" else 1, (k, changed))
            json.loads(tool.read(paths["LOCK"]))
            self.assertEqual(tool.check("v9.8.7"), "9.8.7")
            with self.assertRaises(SystemExit):
                tool.check("v9.8.8")                    # a release tag that names another version


if __name__ == "__main__":
    unittest.main()
