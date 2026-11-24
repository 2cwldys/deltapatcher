"""This system's release files, gathered into dist/release/ and named for a GitHub release.

    python tools/release_files.py          (build.ps1 / build.sh run it last)

dist/release/ then holds, for this system:
    DeltaPatcher-<version>-...                        the app: .exe (Windows), .AppImage and .tar.gz
                                                      (Linux), .dmg and .zip (macOS)
    deltapatcher-cli-<version>-windows-x64.exe        the command line on Windows
    deltapatcher-cli-<version>-<system>-<arch>.tar.gz the command line on Linux and macOS: deltapatcher-cli
                                                      inside, runnable as soon as it is unpacked. A bare
                                                      program there has no extension, and Windows would read
                                                      everything after the version's last dot as one.
Files another system put there are left alone, so one folder can gather every system's files.
Each system's files are built on that system; the Build workflow uploads each one's dist/release/.
"""

import glob
import json
import os
import platform
import shutil
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(DIST, "release")
APP_FILES = {"windows": (".exe",), "linux": (".AppImage", ".tar.gz"), "macos": (".dmg", ".zip")}


def cli_archive(cli, dest):
    """deltapatcher-cli alone in a .tar.gz, executable for everyone once unpacked."""
    def runnable(info):
        info.mode = 0o755
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        return info
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(cli, arcname="deltapatcher-cli", filter=runnable)


def main():
    with open(os.path.join(ROOT, "app", "package.json"), encoding="utf-8") as f:
        version = json.load(f)["version"]
    system = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)
    exe = ".exe" if system == "windows" else ""

    cli = os.path.join(DIST, "deltapatcher-cli" + exe)
    if not os.path.isfile(cli):
        sys.exit("no %s: build first (build.ps1 or build.sh)" % cli)
    app = sorted(p for p in glob.glob(os.path.join(DIST, "app", "*"))
                 if os.path.isfile(p) and os.path.basename(p).lower().startswith("deltapatcher-")
                 and p.endswith(APP_FILES[system]))
    if not app:
        print("no app files in dist/app/ (an engine-only build): only the command line is gathered")

    os.makedirs(OUT, exist_ok=True)
    # this system's files from an earlier run go; another system's stay
    for name in os.listdir(OUT):
        mine = ("-%s-" % system in name) or (name.lower().startswith("deltapatcher-") and name.endswith(APP_FILES[system])
                                              and not name.startswith("deltapatcher-cli-"))
        if mine:
            os.remove(os.path.join(OUT, name))

    base = "deltapatcher-cli-%s-%s-%s" % (version, system, arch)
    if system == "windows":
        names = [base + ".exe"]
        shutil.copy2(cli, os.path.join(OUT, names[0]))
    else:
        names = [base + ".tar.gz"]
        cli_archive(cli, os.path.join(OUT, names[0]))
    for p in app:
        shutil.copy2(p, os.path.join(OUT, os.path.basename(p)))
        names.append(os.path.basename(p))
    for name in sorted(names):
        print("%10.1f MB  %s" % (os.path.getsize(os.path.join(OUT, name)) / 1048576.0, name))
    print("in", OUT)


if __name__ == "__main__":
    main()
