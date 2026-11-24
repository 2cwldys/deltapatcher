"""tools/release_files.py: this version's files, named for a GitHub release; never an older build's."""

import contextlib
import io
import json
import os
import sys
import tarfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, write  # noqa: E402
import release_files  # noqa: E402  (tools/release_files.py)


EVERY_SYSTEM = {
    "windows": ["DeltaPatcher-9.9.9-setup.exe", "DeltaPatcher-9.9.9-windows-x64.zip", "DeltaPatcher-9.9.9-portable.exe",
                "deltapatcher-cli-windows-x64.exe"],
    "linux": ["DeltaPatcher-9.9.9.AppImage", "DeltaPatcher-9.9.9-linux-x64.tar.gz", "deltapatcher-cli-linux-x64.tar.gz"],
    "macos": ["DeltaPatcher-9.9.9-arm64.dmg", "DeltaPatcher-9.9.9-arm64-mac.zip", "deltapatcher-cli-macos-arm64.tar.gz"],
}


class ReleaseFiles(TempCase):
    def test_only_this_version_and_the_command_line(self):
        with open(os.path.join(ROOT, "app", "package.json"), encoding="utf-8") as f:
            version = json.load(f)["version"]
        system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
        app = {"windows": "DeltaPatcher-%s-setup.exe", "linux": "DeltaPatcher-%s.AppImage",
               "macos": "DeltaPatcher-%s-arm64.dmg"}[system]
        dist, out = self.p("dist"), self.p("dist", "release")
        exe = ".exe" if system == "windows" else ""
        write(os.path.join(dist, "deltapatcher-cli" + exe), b"the command line")
        write(os.path.join(dist, "app", app % version), b"this version")
        write(os.path.join(dist, "app", app % "0.0.1"), b"an older build")             # left by an earlier build
        write(os.path.join(out, app % "0.0.1"), b"gathered before")                     # and gathered back then
        others = [n for s, files in EVERY_SYSTEM.items() if s != system for n in files]
        for name in others:                                                              # gathered on the other systems
            write(os.path.join(out, name), b"another system's")
        with mock.patch.object(release_files, "DIST", dist), mock.patch.object(release_files, "OUT", out), \
                contextlib.redirect_stdout(io.StringIO()):
            release_files.main()
        names = sorted(os.listdir(out))
        self.assertIn(app % version, names)
        self.assertNotIn(app % "0.0.1", names)                                           # older builds stay out
        for name in others:
            self.assertIn(name, names)                                                   # another system's files stay
        cli = [n for n in names if n.startswith("deltapatcher-cli-%s-" % system)]
        self.assertEqual(len(cli), 1)
        self.assertNotIn(version, cli[0])                                                # no version in its name
        if system == "windows":
            self.assertEqual(read(os.path.join(out, cli[0])), b"the command line")
        else:
            with tarfile.open(os.path.join(out, cli[0])) as tar:
                member = tar.getmember("deltapatcher-cli")
                self.assertEqual(member.mode & 0o777, 0o755)                              # runnable once unpacked
                self.assertEqual(tar.extractfile(member).read(), b"the command line")


if __name__ == "__main__":
    unittest.main()
