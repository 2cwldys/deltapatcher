"""DeltaPatcher's own updates: is a newer release out, and the file to download for this system.

The releases are the GitHub repository's (REPO). Versions are compared by number (0.2.0 < 0.3.0 < 0.10.0),
never by file. A downloaded file is checked against the SHA-256 GitHub gives for it, and its size, before
anything uses it.

What each system gets:
    the app, Windows        DeltaPatcher-<version>-setup.exe   (run it: it installs over the old one)
    the app, Windows zip    DeltaPatcher-<version>-windows-x64.zip
    the app, Linux          DeltaPatcher-<version>.AppImage    (it replaces the old AppImage)
    the app, macOS          DeltaPatcher-<version>-arm64.dmg   (opened: the app is dragged over the old one)
    the command line        deltapatcher-cli-windows-x64.exe, -linux-x64.tar.gz, -macos-arm64.tar.gz
"""

import hashlib
import json
import os
import platform
import re
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

from . import REPO, USER_AGENT, __version__

API = os.environ.get("DELTAPATCHER_UPDATE_API", "https://api.github.com")      # tests point it elsewhere
CHECK_FILE = "update-check.json"
CHECK_EVERY = 24 * 3600


class UpdateCheckError(Exception):
    pass


def numbers(version):
    """(major, minor, patch) of 'v1.2.3' or '1.2.3-beta'; None when it is not a version."""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", str(version or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def is_newer(latest, current=__version__):
    a, b = numbers(latest), numbers(current)
    return bool(a and b and a > b)


def system():
    """('windows' | 'linux' | 'macos', 'x64' | 'arm64')"""
    name = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    return name, {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(machine, machine)


def latest(timeout=15):
    """The newest release: {version, tag, url, notes, assets: [{name, url, size, sha256}]}, or None when the
    repository has no release yet."""
    req = urllib.request.Request("%s/repos/%s/releases/latest" % (API, REPO),
                                 headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            doc = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise UpdateCheckError("GitHub answered %d %s" % (e.code, e.reason))
    except (OSError, ValueError) as e:
        raise UpdateCheckError("could not ask GitHub: %s" % e)
    assets = []
    for a in doc.get("assets") or []:
        digest = str(a.get("digest") or "")
        assets.append({"name": a.get("name", ""), "url": a.get("browser_download_url", ""), "size": a.get("size", 0),
                       "sha256": digest[7:] if digest.startswith("sha256:") else ""})
    tag = str(doc.get("tag_name") or "")
    return {"version": tag.lstrip("v"), "tag": tag, "url": doc.get("html_url", ""), "notes": doc.get("body") or "",
            "assets": assets}


def pick(assets, kind, version=None):
    """The asset for `kind`: 'cli', or the app as 'setup', 'zip', 'appimage' or 'dmg'; None when the release
    has none for this system."""
    name, arch = system()
    wanted = {
        "cli": ("deltapatcher-cli-%s-%s%s" % (name, arch, ".exe" if name == "windows" else ".tar.gz"),),
        "setup": (r"DeltaPatcher-[\d.]+-setup\.exe",),
        "zip": (r"DeltaPatcher-[\d.]+-windows-%s\.zip" % arch,),
        "appimage": (r"DeltaPatcher-[\d.]+\.AppImage",),
        "dmg": (r"DeltaPatcher-[\d.]+-%s\.dmg" % arch,),
    }[kind]
    for a in assets:
        for w in wanted:
            if (kind == "cli" and a["name"] == w) or (kind != "cli" and re.match("^" + w + "$", a["name"])):
                return a
    return None


def download(asset, folder=None, progress=None):
    """The asset, downloaded into `folder` (a temp folder by default) and checked; its path."""
    folder = folder or os.path.join(tempfile.gettempdir(), "deltapatcher-update")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, os.path.basename(asset["name"]))
    req = urllib.request.Request(asset["url"], headers={"User-Agent": USER_AGENT})
    h = hashlib.sha256()
    got = 0
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(path + ".part", "wb") as f:
            total = int(asset.get("size") or r.headers.get("Content-Length") or 0)
            while True:
                block = r.read(1 << 16)
                if not block:
                    break
                f.write(block)
                h.update(block)
                got += len(block)
                if progress:
                    progress(got, total, asset["name"])
    except OSError as e:
        raise UpdateCheckError("the download of %s stopped: %s" % (asset["name"], e))
    if asset.get("size") and got != int(asset["size"]):
        os.remove(path + ".part")
        raise UpdateCheckError("%s arrived with %d bytes, not %d: not used" % (asset["name"], got, asset["size"]))
    if asset.get("sha256") and h.hexdigest() != asset["sha256"].lower():
        os.remove(path + ".part")
        raise UpdateCheckError("%s does not match the SHA-256 GitHub gives for it: not used" % asset["name"])
    os.replace(path + ".part", path)
    return path


def replace_running_program(new_file, program=None):
    """Put `new_file` where this program is. It is copied beside the program first (the download may be on
    another drive). Windows lets a running program be renamed but not replaced, so the old one becomes
    <name>.old, removed by the next start; elsewhere the file is simply replaced."""
    program = program or sys.executable
    beside = program + ".new"
    shutil.copyfile(new_file, beside)
    if os.name != "nt":
        os.chmod(beside, 0o755)
        os.replace(beside, program)
        return
    old = program + ".old"
    if os.path.exists(old):
        os.remove(old)
    os.replace(program, old)
    try:
        os.replace(beside, program)
    except OSError:
        os.replace(old, program)                           # put the running one back
        raise


def remove_leftover(program=None):
    """The <name>.old a Windows update left behind, now that it no longer runs."""
    old = (program or sys.executable) + ".old"
    if os.path.exists(old):
        try:
            os.remove(old)
        except OSError:
            pass


def cli_from(downloaded):
    """The command-line program itself: the .exe as downloaded, or deltapatcher-cli out of the .tar.gz."""
    if not downloaded.endswith(".tar.gz"):
        return downloaded
    with tarfile.open(downloaded, "r:gz") as tar:
        member = tar.getmember("deltapatcher-cli")
        data = tar.extractfile(member).read()
    out = downloaded[:-len(".tar.gz")] + ".new"
    with open(out, "wb") as f:
        f.write(data)
    return out


def cached_check(folder, enabled=True, timeout=4):
    """The newest version, asked of GitHub at most once a day (the answer is kept in `folder`); '' when
    unknown, switched off or offline. Never raises: a notice must not stop anything."""
    if not enabled:
        return ""
    path = os.path.join(folder, CHECK_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            kept = json.load(f)
        if time.time() - float(kept.get("checked", 0)) < CHECK_EVERY:
            return str(kept.get("latest") or "")
    except (OSError, ValueError):
        pass
    try:
        found = latest(timeout=timeout)
        version = found["version"] if found else ""
    except UpdateCheckError:
        return ""
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"checked": time.time(), "latest": version}, f)
    except OSError:
        pass
    return version
