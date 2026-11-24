"""Settings: deltapatcher.ini, a file anyone can open and edit with a text editor.

The first time DeltaPatcher runs it writes the file with every setting, its default and a line
saying what it does. The window saves its fields into the same file; values change in place and
comments and the order stay as they are.

Which file:
  1. the file named by the environment variable DELTAPATCHER_CONFIG, when it is set;
  2. deltapatcher.ini beside DeltaPatcher.exe, when there is one (a "portable" copy: a game maker can
     ship DeltaPatcher with its source address and publisher key already filled in);
  3. otherwise the user's config folder: %APPDATA%\\DeltaPatcher\\deltapatcher.ini on Windows,
     ~/.config/deltapatcher/deltapatcher.ini elsewhere.

Only addresses, folders, names and public keys are kept. A passphrase or private key never goes in
here: keys are named by their file path.
"""

import configparser
import os
import re
import sys
import time

from .signing import default_key_path as default_signing_key
from .ssh import config_dir, default_key_path
from .source import DEFAULT_URL

FILE_NAME = "deltapatcher.ini"
ENV = "DELTAPATCHER_CONFIG"

# (section, key, default, what it does)
SCHEMA = [
    ("update", "source_url", DEFAULT_URL, "Where releases come from: an http(s) address or a folder."),
    ("update", "install_dir", "", "The folder to keep up to date (a game folder, for instance)."),
    ("update", "trust_key", "", "The publisher's public key (ed25519:...). Empty: unsigned releases are fine, and the key "
                                "of the first signed release is trusted, and only that key after it. off: never check."),
    ("update", "workers", 4, "How many files download at the same time."),
    ("publish", "publish_src", "", "The folder to release."),
    ("publish", "store_dir", "", "Where the release is written (manifest.json, blobs/, sigs/): what gets served."),
    ("publish", "name", "", "The release's name, shown to players."),
    ("publish", "version", "", "The release's version, shown to players."),
    ("publish", "protect", "", "Files players keep once they have them, comma-separated globs (config/*.cfg)."),
    ("publish", "sign", False, "Sign each release (yes/no)."),
    ("publish", "signing_key", default_signing_key(), "The signing key's file (the file's path, never the key)."),
    ("publish", "htaccess", True, "Write a .htaccess for Apache into the store (yes/no)."),
    ("publish", "serve_port", 8000, "The port of 'Serve locally'."),
    ("server", "deploy_mode", "sftp", "Where Deploy sends the release: sftp (a server over SSH) or folder."),
    ("server", "ssh_host", "", "The server's address."),
    ("server", "ssh_user", "", "The user deploys log in as."),
    ("server", "ssh_port", 22, "The server's SSH port."),
    ("server", "ssh_key", default_key_path(), "The SSH key's file (the file's path, never the key)."),
    ("server", "remote_root", "", "The folder on the server its web server serves, with forward slashes."),
    ("server", "deploy_folder", "", "The folder Deploy copies the release to, in folder mode."),
    ("window", "close_to_tray", False, "Closing the window keeps DeltaPatcher in the tray (yes/no)."),
]
DEFAULTS = {key: default for _s, key, default, _w in SCHEMA}
SECTION = {key: section for section, key, _d, _w in SCHEMA}
FORBIDDEN = ("pass", "secret", "token", "private")
HEADER = """; DeltaPatcher settings. Edit with any text editor; a line starting with ; is a comment.
; The window saves its fields here too (comments and order stay). An empty value means the default.
; Never put a passphrase or a private key in this file: keys are named by their file path only.
"""


def path():
    env = os.environ.get(ENV)
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if getattr(sys, "frozen", False):
        portable = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), FILE_NAME)
        if os.path.isfile(portable):
            return portable
    return os.path.join(config_dir(), FILE_NAME)


def _text(value):
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def placeholder():
    """The text of a new settings file: every setting with its default and what it does."""
    lines = [HEADER.rstrip("\n")]
    current = None
    for section, key, default, what in SCHEMA:
        if section != current:
            lines += ["", "[%s]" % section]
            current = section
        lines += ["; " + what, "%s = %s" % (key, _text(default))]
    return "\n".join(lines) + "\n"


def ensure():
    """The settings file's path, writing the placeholder first when there is none."""
    p = path()
    if not os.path.isfile(p):
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(placeholder())
        except OSError:
            pass
    return p


CHOICES = {"deploy_mode": ("sftp", "folder")}


def _convert(key, raw):
    default = DEFAULTS[key]
    raw = raw.strip()
    if raw == "":
        return default
    if key in CHOICES:
        return raw.lower() if raw.lower() in CHOICES[key] else default
    if isinstance(default, bool):
        low = raw.lower()
        if low in ("1", "yes", "true", "on"):
            return True
        if low in ("0", "no", "false", "off"):
            return False
        return default
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            return default
    return raw


def load():
    cfg = dict(DEFAULTS)
    p = ensure()
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        with open(p, "r", encoding="utf-8") as f:
            parser.read_file(f)
    except (OSError, configparser.Error, UnicodeDecodeError):
        return cfg
    for key in DEFAULTS:
        section = SECTION[key]
        if parser.has_option(section, key):
            cfg[key] = _convert(key, parser.get(section, key))
    return cfg


_LINE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=")
_SECT = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def save(cfg):
    """Write the known settings into the file, changing only their values (comments and order stay)."""
    values = {}
    for k, v in cfg.items():
        if k not in DEFAULTS or any(f in k.lower() for f in FORBIDDEN):
            continue
        text = _text(v)
        if "\n" in text or "\r" in text:
            continue
        values[k] = text
    p = ensure()
    try:
        with open(p, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = placeholder().splitlines()
    done, section = set(), None
    for i, line in enumerate(lines):
        m = _SECT.match(line)
        if m:
            section = m.group(1).strip()
            continue
        m = _LINE.match(line)
        if m and m.group(1) in values and SECTION[m.group(1)] == section and m.group(1) not in done:
            lines[i] = "%s = %s" % (m.group(1), values[m.group(1)])
            done.add(m.group(1))
    for key in [k for k in values if k not in done]:       # keys the file lacks: added under their section
        sect = SECTION[key]
        at = None
        for i, line in enumerate(lines):
            m = _SECT.match(line)
            if m and m.group(1).strip() == sect:
                at = i + 1
                while at < len(lines) and not _SECT.match(lines[at]):
                    at += 1
                break
        if at is None:
            lines += ["", "[%s]" % sect]
            at = len(lines)
        lines.insert(at, "%s = %s" % (key, values[key]))
    # a temporary file of its own, so two writers never share one; on Windows the replace can be refused
    # for a moment while another DeltaPatcher reads the file, so it is tried again
    tmp = "%s.%d.%d.tmp" % (p, os.getpid(), int(time.time() * 1000000) % 1000000)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    for attempt in range(50):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            if attempt == 49:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(0.05)
