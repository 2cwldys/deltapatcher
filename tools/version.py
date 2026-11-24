"""DeltaPatcher's version: set it, show it, check it.

    python tools/version.py 1.4.2            set the version to 1.4.2, everywhere it is written
    python tools/version.py                  the version, and a check that every place agrees
    python tools/version.py patch            or minor, or major: the next one (0.2.0 -> 0.2.1, 0.3.0, 1.0.0)
    python tools/version.py --tag v1.4.2     the check, and the tag must name this version (the Builds workflow)

The version lives in deltapatcher/__init__.py (__version__); pyproject.toml reads it from there. The app's
app/package.json and app/package-lock.json carry a copy, because electron-builder names the app's files
after it: a bump writes all three, and the check fails when they differ.
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INIT = os.path.join(ROOT, "deltapatcher", "__init__.py")
PACKAGE = os.path.join(ROOT, "app", "package.json")
LOCK = os.path.join(ROOT, "app", "package-lock.json")
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
INIT_LINE = re.compile(r'^(__version__ = ")([^"]*)(")', re.M)
# package.json's own version, and package-lock.json's two copies of it (the top, and packages[""])
PACKAGE_LINE = re.compile(r'^(  "version": ")([^"]*)(")', re.M)
LOCK_ROOT_PACKAGE = re.compile(r'^(    "": \{\r?\n(?:      .*\r?\n)*?      "version": ")([^"]*)(")', re.M)


def read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8")


def write(path, text):
    with open(path, "wb") as f:           # bytes: each file keeps its own line endings
        f.write(text.encode("utf-8"))


def current():
    m = INIT_LINE.search(read(INIT))
    if not m:
        sys.exit("no __version__ = \"...\" line in %s" % INIT)
    return m.group(2)


def copies():
    """Every place that carries the version, and what it says."""
    lock = json.loads(read(LOCK))
    return {
        "deltapatcher/__init__.py": current(),
        "app/package.json": json.loads(read(PACKAGE)).get("version"),
        "app/package-lock.json": lock.get("version"),
        "app/package-lock.json (packages \"\")": lock.get("packages", {}).get("", {}).get("version"),
    }


def bumped(version, how):
    m = VERSION.match(version)
    if not m:
        sys.exit("%s is not a version like 1.2.3" % version)
    major, minor, patch = (int(x) for x in m.groups())
    if how == "major":
        return "%d.0.0" % (major + 1)
    if how == "minor":
        return "%d.%d.0" % (major, minor + 1)
    if how == "patch":
        return "%d.%d.%d" % (major, minor, patch + 1)
    new = how[1:] if how.startswith("v") else how
    if not VERSION.match(new):
        sys.exit("give patch, minor, major or a version like 1.2.3 (not %s)" % how)
    return new


def set_version(new):
    for path, pattern, count in ((INIT, INIT_LINE, 1), (PACKAGE, PACKAGE_LINE, 1), (LOCK, PACKAGE_LINE, 1),
                                 (LOCK, LOCK_ROOT_PACKAGE, 1)):
        text = read(path)
        text, n = pattern.subn(lambda m: m.group(1) + new + m.group(3), text, count=count)
        if n != 1:
            sys.exit("could not find the version in %s" % path)
        write(path, text)


def check(tag=""):
    found = copies()
    version = found["deltapatcher/__init__.py"]
    wrong = {k: v for k, v in found.items() if v != version}
    for k, v in sorted(wrong.items()):
        print("  %s says %s" % (k, v))
    if wrong:
        sys.exit("the version differs from deltapatcher/__init__.py (%s): run  python tools/version.py %s"
                 % (version, version))
    if tag and tag.lstrip("v") != version:
        sys.exit("the tag %s does not name this version (%s): bump it first (python tools/version.py %s)"
                 % (tag, version, tag.lstrip("v")))
    return version


def main(argv):
    if argv[:1] == ["--tag"]:
        print("DeltaPatcher %s" % check(argv[1] if len(argv) > 1 else ""))
        return
    if not argv:
        print("DeltaPatcher %s (every copy agrees)" % check())
        return
    old = check()
    new = bumped(old, argv[0])
    set_version(new)
    check()
    print("DeltaPatcher %s -> %s: deltapatcher/__init__.py, app/package.json, app/package-lock.json" % (old, new))
    print("Commit and push: the Build run's files carry %s. Tag the release v%s." % (new, new))


if __name__ == "__main__":
    main(sys.argv[1:])
