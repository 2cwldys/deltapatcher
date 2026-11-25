"""Projects: one DeltaPatcher keeps several games, mods or servers apart, each in a settings file of its own.

A project is a settings file with a project's sections: project (its name), update (the folder it keeps
up to date and where that comes from), publish (the folder it releases) and server (where that goes).
  - Default is the main settings file (config.path()). It also keeps DeltaPatcher's own settings, and it
    is the project every command uses unless --project names another.
  - Every .ini file in the projects folder beside the main file is a project; `project new` makes one.
    Removing one of these deletes its file.
  - A project's settings file kept anywhere else (a game's folder, a share) can be added: the main file
    lists its path in project_files. Removing one only takes it off the list; the file stays where it is.
  - A project file (presets.py) shares projects with other people: importing one makes projects here.

A project's name is its title, else its file's name ("Default" for the main file). Names are unique,
letter case aside, so a name always says which project is meant.
"""

import configparser
import os
import re

from . import config

DEFAULT_TITLE = "Default"
FOLDER = "projects"
EXT = ".ini"
NAME_MAX = 60
_NAME = re.compile(r"^[A-Za-z0-9 _.()-]+$")
RESERVED = set(["CON", "PRN", "AUX", "NUL"] + ["COM%d" % i for i in range(1, 10)] + ["LPT%d" % i for i in range(1, 10)])
PROJECT_SECTIONS = ("project", "update", "publish", "server")


class ProjectError(Exception):
    """A project that cannot be found, made, added, renamed or removed; `code` says why, for the app."""

    def __init__(self, message, code="failed", data=None):
        Exception.__init__(self, message)
        self.code = code
        self.data = data or {}


def _key(path):
    """A path as it is compared: absolute, and in the system's letter case."""
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def _list(text):
    return [x for x in (i.strip() for i in str(text or "").split(";")) if x]


def same(a, b):
    """Two projects (or None for Default) are the same settings file."""
    pa = a.path if a is not None else config.path()
    pb = b.path if b is not None else config.path()
    return _key(pa) == _key(pb)


class Project(object):
    """One project: its settings file, its name, and its kind: main (Default), folder (in the projects
    folder), added (listed in project_files), or given (a file named for one command, not listed)."""

    def __init__(self, path, kind, title=None):
        self.path = os.path.abspath(path)
        self.kind = kind
        self.title = title if title is not None else read_title(self.path, kind)

    @property
    def main(self):
        return self.kind == "main"

    @property
    def missing(self):
        return not os.path.isfile(self.path)

    def ref(self):
        """How current_project names it: empty for Default, the file's name for one in the projects folder,
        the full path for any other."""
        if self.main:
            return ""
        if self.kind == "folder":
            return os.path.basename(self.path)
        return self.path

    def tag(self):
        """What the output and the app know it by."""
        return {"project": self.title, "file": self.path}

    def __repr__(self):
        return "Project(%r, %r, %r)" % (self.path, self.kind, self.title)


def read_title(path, kind):
    """The name a settings file gives itself: its [project] title, else the file's name (Default for the main file)."""
    title = str(config.file_values(path, ["title"]).get("title", "")).strip()
    if title:
        return title
    return DEFAULT_TITLE if kind == "main" else os.path.splitext(os.path.basename(path))[0]


def folder():
    """The projects folder: beside the main settings file (a portable copy's too)."""
    return os.path.join(os.path.dirname(config.path()), FOLDER)


def main_project():
    return Project(config.ensure(), "main")


def added_files():
    """The settings files of projects kept elsewhere, as the main file lists them."""
    listed = config.file_values(config.ensure(), ["project_files"]).get("project_files", "")
    return [os.path.abspath(os.path.expanduser(p)) for p in _list(listed)]


def all_projects():
    """Every project: Default first, then the others by name."""
    main = main_project()
    others = []
    d = folder()
    try:
        names = sorted(os.listdir(d))
    except OSError:
        names = []
    for n in names:
        p = os.path.join(d, n)
        if n.lower().endswith(EXT) and os.path.isfile(p):
            others.append(Project(p, "folder"))
    seen = set(_key(p.path) for p in [main] + others)
    for p in added_files():
        if _key(p) not in seen:
            seen.add(_key(p))
            others.append(Project(p, "added"))
    others.sort(key=lambda p: (p.title.lower(), _key(p.path)))
    return [main] + others


def check_file(path):
    """A file that can be a project: it reads as a settings file (as config.read_file() reads one), and has a
    project's sections or none."""
    try:
        parser = config.read_file(path)                              # a byte order mark at its start is fine
    except (OSError, UnicodeDecodeError) as e:
        raise ProjectError("cannot read %s: %s" % (path, e), "unreadable")
    except configparser.Error as e:
        raise ProjectError("%s is not a settings file: %s" % (path, str(e).splitlines()[0]), "not-a-project")
    sections = parser.sections()
    if sections and not set(s.strip().lower() for s in sections) & set(PROJECT_SECTIONS):
        raise ProjectError("%s is not a DeltaPatcher project: it has none of the sections [%s]"
                           % (path, "], [".join(PROJECT_SECTIONS)), "not-a-project")


def resolve(text, allow_missing=False):
    """The project `text` names: a project's name (letter case aside), its settings file, or a file's name in
    the projects folder; empty: Default. A settings file that is not listed is used as it is (a launcher can
    name a settings file anywhere). allow_missing: a listed project whose file is gone, to take it off the list."""
    every = all_projects()
    text = str(text or "").strip()
    if not text:
        return every[0]
    full = _key(text)
    found = [p for p in every if _key(p.path) == full]
    if not found:
        found = [p for p in every if p.title.lower() == text.lower()]
    if not found:
        names = (text.lower(), (text + EXT).lower())
        found = [p for p in every if p.kind == "folder" and os.path.basename(p.path).lower() in names]
    if not found and text.lower() == DEFAULT_TITLE.lower():
        found = [every[0]]
    if len(found) > 1:
        raise ProjectError("%d projects are named %s: give the settings file of the one you mean (%s)"
                           % (len(found), text, "; ".join(p.path for p in found)), "ambiguous")
    if found:
        p = found[0]
        if p.missing and not allow_missing:
            raise ProjectError("the settings file of the project %s is gone: %s. Put it back, or take the project "
                               "off the list:  deltapatcher project remove \"%s\"" % (p.title, p.path, p.title), "missing",
                               p.tag())
        return p
    path = os.path.abspath(os.path.expanduser(text))
    if os.path.isfile(path):
        check_file(path)
        return Project(path, "given")
    raise ProjectError("there is no project %s:  deltapatcher projects  lists them" % text, "no-such-project")


def name_problem(name):
    """What is wrong with a project's name by the rules alone ('' when nothing): letters, digits, spaces and
    - _ . ( ), at most 60 characters, not starting with a dash (a command line would take it for an option),
    not ending with a dot, and not a name Windows keeps for its devices (CON, NUL, COM1, ...)."""
    name = str(name or "").strip()
    if not name:
        return "give the project a name"
    return _name_rules(name, "a project's name")


def folder_name_problem(name):
    """What is wrong with the name of the folder a project goes into inside the folder a player picks
    (install_folder), '' when nothing: one folder's name, by the rules of a project's name, and no space at
    either end."""
    name = str(name or "")
    if not name.strip():
        return "give the folder a name"
    if name != name.strip():
        return "a folder's name cannot start or end with a space"
    return _name_rules(name, "a folder's name")


def _name_rules(name, what):
    if len(name) > NAME_MAX:
        return "%s is at most %d characters" % (what, NAME_MAX)
    if not _NAME.match(name):
        return "%s has letters, digits, spaces and - _ . ( ) only: %s" % (what, name)
    if name.startswith("-"):
        return "%s cannot start with a dash: %s" % (what, name)
    if name.endswith("."):
        return "%s cannot end with a dot: %s" % (what, name)
    if name.split(".")[0].strip().upper() in RESERVED:
        return "%s is a name Windows keeps for a device: choose another" % name
    return ""


def check_name(name, project=None):
    """A project's name as it will be kept, or ProjectError: the rules of name_problem(), and no other
    project's name (letter case aside). `project`: the one being renamed, which may keep its own."""
    name = str(name or "").strip()
    problem = name_problem(name)
    if problem:
        raise ProjectError(problem, "bad-name")
    for p in all_projects():
        if p.title.lower() == name.lower() and not (project is not None and same(p, project)):
            raise ProjectError("there is a project named %s already" % p.title, "name-taken", {"name": name})
    return name


def new(name):
    """A new project in the projects folder, its file named after it; returns it."""
    name = check_name(name)
    d = folder()
    os.makedirs(d, exist_ok=True)
    path, n = os.path.join(d, name + EXT), 1
    while True:
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as f:      # never over another file
                f.write(config.placeholder(project=True, title=name))
            break
        except FileExistsError:
            n += 1
            path = os.path.join(d, "%s (%d)%s" % (name, n, EXT))
    return Project(path, "folder", name)


def add(path, name=None):
    """List a project's settings file kept elsewhere; `name` gives it that name (written into the file).
    Returns it."""
    full = os.path.abspath(os.path.expanduser(str(path or "").strip().strip('"')))
    if not os.path.isfile(full):
        raise ProjectError("there is no file %s" % full, "not-found")
    if ";" in full:
        raise ProjectError("a settings file's path cannot have a ; in it: %s" % full, "bad-path")
    for p in all_projects():
        if _key(p.path) == _key(full):
            raise ProjectError("%s is the project %s already" % (full, p.title), "listed", p.tag())
    check_file(full)
    project = Project(full, "added")
    if name is not None and str(name).strip():
        name = check_name(name)
        if name != project.title:
            config.save({"title": name}, project)
            project.title = name
    elif any(p.title.lower() == project.title.lower() for p in all_projects()):
        raise ProjectError("there is a project named %s already: give this one another name" % project.title,
                           "name-taken", {"name": project.title, "file": full})
    listed = _list(config.file_values(config.ensure(), ["project_files"]).get("project_files", ""))
    config.save({"project_files": "; ".join(listed + [full])})
    return project


def check_removable(project):
    """ProjectError when the project cannot be removed: Default, or a file that is not on the list."""
    if project.main:
        raise ProjectError("Default is DeltaPatcher's own settings file: it cannot be removed", "default")
    if project.kind == "given":
        raise ProjectError("%s is not one of the projects" % project.path, "not-listed")


def remove(project):
    """Delete a projects-folder project's file, or take an added one off the list (its file stays).
    Returns "deleted" or "unlisted"."""
    check_removable(project)
    chosen = _current_path()
    if project.kind == "folder":
        try:
            os.remove(project.path)
        except FileNotFoundError:
            pass
        done = "deleted"
    else:
        listed = _list(config.file_values(config.ensure(), ["project_files"]).get("project_files", ""))
        config.save({"project_files": "; ".join(p for p in listed if _key(p) != _key(project.path))})
        done = "unlisted"
    if chosen and _key(chosen) == _key(project.path):
        config.save({"current_project": ""})       # the app opens on Default next time
    return done


def remove_all():
    """Every project but Default leaves, as remove() does each. Returns [(project, "deleted" or "unlisted")]."""
    return [(p, remove(p)) for p in all_projects()[1:]]


def rename(project, name):
    """Give a project a new name: its title changes; its file stays where and what it is."""
    name = check_name(name, project)
    if project.missing:
        raise ProjectError("the settings file of the project %s is gone: %s" % (project.title, project.path), "missing")
    config.save({"title": name}, project)
    project.title = name
    return project


def _current_path():
    """The file current_project names; '' for Default."""
    ref = str(config.file_values(config.ensure(), ["current_project"]).get("current_project", "")).strip()
    if not ref:
        return ""
    return ref if os.path.isabs(ref) else os.path.join(folder(), ref)


def current():
    """The project the app shows (current_project); Default when none is set, or it is no longer there."""
    every = all_projects()
    path = _current_path()
    if path:
        for p in every:
            if _key(p.path) == _key(path) and not p.missing:
                return p
    return every[0]


def where(cfg):
    """Where a project's releases go, in words; None while that is not set up."""
    mode = cfg["deploy_mode"]
    if mode == "local":
        return "this computer, at http://localhost:%s/ (to test)" % cfg["serve_port"]
    if mode == "folder":
        return ("the folder %s" % cfg["deploy_folder"]) if cfg["deploy_folder"] else None
    if cfg["ssh_host"] and cfg["remote_root"]:
        user = (cfg["ssh_user"] + "@") if cfg["ssh_user"] else ""
        return "your server, %s%s:%s" % (user, cfg["ssh_host"], cfg["remote_root"])
    return None


def describe(project, cfg=None):
    """A project for listings: its name and file, and what is set up: the folder it updates and its source,
    the folder it releases and where that goes; its description, website and where it goes for players; the
    http(s) address a project file would give for it ('' when it has none); how often the app backs it up and
    when its newest backup was made; whether it asks about its publisher's changes, and whether its releases
    share its details and show file names; whether it is shared by torrent (by torrent only, with no address) and
    is set to seed; whether its releases are signed (sign: a private project file needs it)."""
    from .backup import newest
    from .presets import address
    from . import torrent
    if cfg is None:
        cfg = dict(config.DEFAULTS) if project.missing else config.load(project)
    goes = where(cfg)
    return {"title": project.title, "file": project.path, "kind": project.kind, "ref": project.ref(),
            "missing": project.missing,
            "install_dir": cfg["install_dir"], "source_url": cfg["source_url"], "publish_src": cfg["publish_src"],
            "deploy_mode": cfg["deploy_mode"], "where": goes or "",
            "description": cfg["description"], "website": cfg["website"], "install_hint": cfg["install_hint"],
            "install_folder": cfg["install_folder"], "address": address(cfg),
            "update_ready": bool(cfg["install_dir"]) and bool(cfg["source_url"]) and not project.missing,
            "release_ready": bool(cfg["publish_src"]) and bool(goes) and not project.missing,
            "backup_every_hours": cfg["backup_every_hours"], "backup_last": "" if project.missing else newest(project, cfg),
            "backup_ready": not project.missing and (
                (cfg["deploy_mode"] == "folder" and bool(cfg["deploy_folder"])) or
                (cfg["deploy_mode"] == "sftp" and bool(cfg["ssh_host"]) and bool(cfg["remote_root"]))),
            "follow_publisher": bool(cfg["follow_publisher"]), "share_updates": bool(cfg["share_updates"]),
            "show_files": bool(cfg["show_files"]), "torrent": torrent.info(cfg) is not None,
            "torrent_only": torrent.torrent_only(cfg), "seed": bool(cfg["seed"]), "sign": bool(cfg["sign"])}
