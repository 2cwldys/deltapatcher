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

That file is also the Default project. Other projects are files of their own with only a project's
sections (project, update, publish, server; see projects.py), while DeltaPatcher's own settings (window,
general) stay in the main file. load() and save() take the project: its own settings come from and go
to its file, DeltaPatcher's own from and to the main file.

Only addresses, folders, names and public keys are kept. A passphrase or private key never goes in
here: keys are named by their file path.
"""

import configparser
import hashlib
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
    ("project", "title", "", "This project's name in DeltaPatcher's lists. Empty: the file's name (Default for the "
                             "main settings file)."),
    ("project", "description", "", "A line about this project, shown with it and in the project files it is shared in."),
    ("project", "website", "", "The project's web page (https://...), shown with it and in the project files it is "
                               "shared in."),
    ("project", "install_hint", "", "Where this project goes, shown with it, in the project files it is shared in and to "
                                    "players as they pick its folder, for example: Pick the game's folder; this goes "
                                    "into its Mods folder."),
    ("project", "install_folder", "", "The folder this project goes into inside the folder a player picks, for example "
                                      "Mods: one name, by the rules of a project's name. Empty: the folder the player "
                                      "picks."),
    ("project", "follow_publisher", True, "Ask me when the publisher changes this project: its description, website, "
                                          "where it goes, its address or its publisher key, as its releases give them. "
                                          "Nothing changes until you apply it, and your folder, its name here and your "
                                          "own settings never do (yes/no). no: never asked."),
    ("project", "publisher_accepted", "", "Kept by DeltaPatcher: the publisher's details of this project as you last "
                                          "accepted them, so a check sees what the publisher changed since."),
    ("project", "publisher_not_now", "", "Kept by DeltaPatcher: the publisher's change you answered Not now, by its "
                                         "hash. It is asked again once the publisher changes the project again."),
    ("update", "source_url", DEFAULT_URL, "Where releases come from: an http(s) address or a folder."),
    ("update", "install_dir", "", "The folder to keep up to date (a game folder, for instance)."),
    ("update", "trust_key", "", "The publisher's public key (ed25519:...). Empty: unsigned releases are fine, and the key "
                                "of the first signed release is trusted, and only that key after it. off: never check."),
    ("update", "workers", 4, "How many files download at the same time."),
    ("publish", "publish_src", "", "The folder to release."),
    ("publish", "store_dir", "", "Where the release is prepared before it is sent (manifest.json, blobs/, sigs/). Empty: "
                                 "DeltaPatcher's own folder, one for each folder released."),
    ("publish", "name", "", "The release's name, shown to players."),
    ("publish", "version", "", "The release's version, shown to players."),
    ("publish", "protect", "", "Files and folders players keep once they have them (their settings): paths inside the "
                               "folder you release, separated by ; (config/user.cfg; saves/). Patterns like *.ini work too."),
    ("publish", "leave_out", "", "Files and folders left out of the release: paths inside the folder you release, "
                                 "separated by ; (screenshots/; notes.txt). Patterns like *.log work too."),
    ("publish", "sign", False, "Sign each release (yes/no)."),
    ("publish", "signing_key", default_signing_key(), "The signing key's file (the file's path, never the key)."),
    ("publish", "htaccess", True, "Write a .htaccess for Apache into the store (yes/no)."),
    ("publish", "serve_port", 8000, "The port of 'Serve locally'."),
    ("publish", "share_updates", True, "Each release carries this project's details (its name, description, website, "
                                       "where it goes, the address players update from and its publisher key), and "
                                       "players who follow it are asked when you change them; project files are update "
                                       "capable unless you choose otherwise (yes/no). no: releases carry none, and each "
                                       "project file sets up a project of its own."),
    ("publish", "show_files", True, "Players see the names of the files a release adds, changes and removes, and its "
                                    "whole list (yes/no). The manifest is public either way: this decides what "
                                    "DeltaPatcher shows them. no: only how many."),
    ("server", "deploy_mode", "sftp", "Where Release sends the release: sftp (a server over SSH), folder, or local "
                                      "(served from this computer, for testing)."),
    ("server", "ssh_host", "", "The server's address."),
    ("server", "ssh_user", "", "The user deploys log in as."),
    ("server", "ssh_port", 22, "The server's SSH port."),
    ("server", "ssh_key", default_key_path(), "The SSH key's file (the file's path, never the key)."),
    ("server", "remote_root", "", "The folder on the server its web server serves, with forward slashes."),
    ("server", "deploy_folder", "", "The folder Release copies the release to, in folder mode."),
    ("server", "public_url", "", "The address players update from: what the web server gives the remote folder or the "
                                 "folder (https://game.example.com/). Shown after each release."),
    ("server", "access_log", "", "The web server's access log, for statistics (the Combined Log Format of nginx and "
                                 "Apache, or Caddy's JSON log): its path on the server, read over SFTP like a "
                                 "release is sent, or for a folder or local release a path on this computer. Only "
                                 "counts are kept, never an address."),
    ("backup", "backup_dir", "", "The folder backups of the live release go in, a folder for each project inside it. "
                                 "A relative path starts at the main settings file's folder. Empty: backups beside "
                                 "the main settings file. Never a project's store or a folder it releases, copies to "
                                 "or keeps up to date."),
    ("backup", "backup_every_hours", 0, "The app backs up the live release every this many hours while it runs, window "
                                        "shown or in the tray (0: never; 24: once a day). For a computer without the app "
                                        "open: deltapatcher backup schedule daily."),
    ("backup", "backup_keep_daily", 7, "Backups kept one a day: the newest of each of the last this many days that have "
                                       "one."),
    ("backup", "backup_keep_weekly", 4, "Backups kept one a week: the newest of each of the last this many weeks that "
                                        "have one."),
    ("backup", "backup_keep_monthly", 0, "Backups kept one a month: the newest of each of the last this many months that "
                                         "have one."),
    ("backup", "backup_max_size", "", "The most the project's backup folder may hold, for example 10 GB: the oldest "
                                      "backups go first; the one just made always stays. Empty: no limit."),
    ("window", "close_to_tray", False, "Closing the window keeps DeltaPatcher in the tray (yes/no)."),
    ("window", "minimize_to_tray", False, "Minimizing the window hides it in the tray, off the taskbar; a click on the "
                                          "tray icon brings it back (yes/no)."),
    ("window", "start_with_system", False, "Start DeltaPatcher with the computer, straight into the tray (yes/no). Only an "
                                           "installed copy can: one run from source cannot."),
    ("window", "check_updates", True, "Look for a newer DeltaPatcher once a day and say so (yes/no). Nothing is "
                                      "downloaded without asking."),
    ("general", "parallel_updates", 3, "How many projects Update all updates at the same time."),
    ("general", "check_every_hours", 1, "The window checks every project for an update when it starts, and again every "
                                        "this many hours while it runs (0: only when it starts). A check changes nothing "
                                        "unless auto_update is on."),
    ("general", "auto_update", False, "Projects with an update are updated by themselves when the window checks them, "
                                      "and a notification says what was done (yes/no)."),
    ("general", "project_files", "", "The settings files of projects kept elsewhere (a game's folder, a share), listed "
                                     "with the projects: their paths, separated by ;. The projects folder beside this "
                                     "file is always listed."),
    ("general", "current_project", "", "The project the app shows: its file's name in the projects folder, or its full "
                                       "path. Empty: Default."),
]
DEFAULTS = {key: default for _s, key, default, _w in SCHEMA}
SECTION = {key: section for section, key, _d, _w in SCHEMA}
APP_SECTIONS = ("window", "general")        # DeltaPatcher's own settings: in the main file only
PROJECT_KEYS = [key for section, key, _d, _w in SCHEMA if section not in APP_SECTIONS]
APP_KEYS = [key for section, key, _d, _w in SCHEMA if section in APP_SECTIONS]
FORBIDDEN = ("pass", "secret", "token", "private")
HEADER = """; DeltaPatcher settings. Edit with any text editor; a line starting with ; is a comment.
; The window saves its fields here too (comments and order stay). An empty value means the default.
; Never put a passphrase or a private key in this file: keys are named by their file path only.
"""
PROJECT_HEADER = """; A DeltaPatcher project: the folder it keeps up to date, the folder it releases, and where that goes.
; Edit with any text editor; a line starting with ; is a comment. The window saves its fields here too
; (comments and order stay). An empty value means the default.
; Never put a passphrase or a private key in this file: keys are named by their file path only.
"""


def default_store(src):
    """Where a folder's release is prepared when store_dir is empty: a stores folder beside the settings
    file (DeltaPatcher's own folder, or a portable copy's), one store for each folder released, named
    after it with a short hash of its full path so two folders of the same name stay apart."""
    full = os.path.normcase(os.path.abspath(src)).rstrip("\\/")
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", os.path.basename(full)).strip("-.") or "release"
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()[:8]
    return os.path.join(os.path.dirname(path()), "stores", "%s-%s" % (name[:40], digest))


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


def placeholder(project=False, title=""):
    """The text of a new settings file: every setting with its default and what it does. project=True: a
    project's own file, with a project's settings only (and its title)."""
    lines = [(PROJECT_HEADER if project else HEADER).rstrip("\n")]
    current = None
    for section, key, default, what in SCHEMA:
        if project and section in APP_SECTIONS:
            continue
        if section != current:
            lines += ["", "[%s]" % section]
            current = section
        lines += ["; " + what, "%s = %s" % (key, title if key == "title" and title else _text(default))]
    return "\n".join(lines) + "\n"


def own_file(project):
    """The file a project's own settings live in; None for Default (the main file) or no project."""
    if project is None or getattr(project, "main", False):
        return None
    return project.path


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


CHOICES = {"deploy_mode": ("sftp", "folder", "local")}


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


def file_values(path, keys=None):
    """{key: value} of the settings the file has, of `keys` (default: all), each read like load() reads it;
    {} when the file cannot be read."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        with open(path, "r", encoding="utf-8") as f:
            parser.read_file(f)
    except (OSError, configparser.Error, UnicodeDecodeError):
        return {}
    out = {}
    for key in (DEFAULTS if keys is None else keys):
        if parser.has_option(SECTION[key], key):
            out[key] = _convert(key, parser.get(SECTION[key], key))
    return out


def load(project=None):
    """Every setting, its default where the file has none. project (a projects.Project; None: Default): its
    own settings from its file, DeltaPatcher's own from the main file."""
    cfg = dict(DEFAULTS)
    main = ensure()
    own = own_file(project)
    if own is None:
        cfg.update(file_values(main))
    else:
        cfg.update(file_values(main, APP_KEYS))
        cfg.update(file_values(own, PROJECT_KEYS))
    return cfg


_LINE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*=")
_SECT = re.compile(r"^\s*\[([^\]]+)\]\s*$")


def save(cfg, project=None):
    """Write the known settings, changing only their values (comments and order stay). project (a
    projects.Project; None: Default): its own settings go to its file, DeltaPatcher's own to the main file."""
    values = {}
    for k, v in cfg.items():
        if k not in DEFAULTS or any(f in k.lower() for f in FORBIDDEN):
            continue
        text = _text(v)
        if "\n" in text or "\r" in text:
            continue
        values[k] = text
    own = own_file(project)
    if own is None:
        _write(ensure(), values, placeholder())
        return
    mine = {k: v for k, v in values.items() if SECTION[k] not in APP_SECTIONS}
    app = {k: v for k, v in values.items() if SECTION[k] in APP_SECTIONS}
    if mine:
        _write(own, mine, placeholder(project=True))
    if app:
        _write(ensure(), app, placeholder())


def _write(p, values, template):
    """Put `values` ({key: text}) into the file p, in place; a file that is not there starts as `template`."""
    try:
        with open(p, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = template.splitlines()
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
    _replace(p, "\n".join(lines) + "\n")


def write_defaults():
    """The main settings file back to every default, as a new copy of DeltaPatcher writes it (Reset)."""
    _replace(path(), placeholder())


def _replace(p, text):
    """Write the file p whole. A temporary file of its own, so two writers never share one; on Windows the
    replace can be refused for a moment while another DeltaPatcher reads the file, so it is tried again."""
    tmp = "%s.%d.%d.tmp" % (p, os.getpid(), int(time.time() * 1000000) % 1000000)
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
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
