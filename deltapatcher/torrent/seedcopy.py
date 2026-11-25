"""Seed copies: a copy of the release a player's project seeds from, so its seeding never reads the game folder.

A player's project set to seed seeds from a copy of its release kept for seeding when seed_copy = yes, the default
(seed_copy = no: straight from its folder, folder.for_folder). The copy is laid out like the publisher's store:
manifest.json, sigs/<sha256 of the manifest>.sig, release.torrent, release.item and blobs/<sha256>, so a seed run maps
it as a store (folder.for_store) and libtorrent reads only the copy. Filling the copy (fill) reads the game folder,
and stops at once when an update asks. Where a copy is:
  - general.seed_copies empty: <install_dir>/.deltapatcher/seedstore/<torrent id> (the game's own drive, gone with its
    folder; .deltapatcher is never part of a release, so a copy is never published, and no update removes it);
  - general.seed_copies set: <seed_copies>/<torrent id>-<first 8 hex of the SHA-256 of the folder's full path>; a
    relative seed_copies starts at the main settings file's folder, and it is a folder that is there
    (copies_problem).
Two projects of one torrent and one folder share a copy, deleted only when neither needs it.

Every copy holds seedstore.json, {"deltapatcher": "seed copy", "torrent_id", "folder", "project"}, and is on the list
of the copies this DeltaPatcher made, seed-copies.json beside the main settings file. A folder is a copy only with
that marker, and only when neither it nor its blobs/, sigs/ and .deltapatcher/ are links (symbolic links, or junctions
on Windows: publish.is_link; other reparse points, such as cloud placeholders, are ordinary folders): anything else is
never written into, pruned or deleted. Only prepare() makes a copy's folder: a copy that goes while something writes
into it stops the writing (CopyGone), and the next start makes a fresh one. A copy is deleted (its marker last, so a
deletion cut short is finished by the next one) only when it is on the list (wherever it is: a copy that is not is
another settings file's, never taken), no project needs it, no seed run uses it and every settings file was read
whole (Needs). A clean-up puts back on the list a marked copy at a current project's own place whose marker names
this installation's settings file of that project, and makes a list that cannot be read again (_tend), so a lost list
leaves no copy where nothing finds it. Its hash cache is <copy>/.deltapatcher/hashes.json, as a store's.

A copy holds only the release seeded now (fill, write_release, prune), and only what the folder had and the engine
checked: each blob is a space-sharing clone of a folder file where the drive makes one (macOS clonefile, Linux
FICLONE; never on Windows), hashed in full, else a byte copy hashed as it is copied, with room kept on its drive
(_room). It is written as blobs/<sha>.<process id>.part and renamed to blobs/<sha> only when its SHA-256 is the
blob's. Nothing is ever downloaded into a copy.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import threading
import time
from hashlib import sha256 as _sha256       # names and paths: never slowed down by a test's hashlib

from .. import config, hashing
from ..publish import is_link
from . import (ID_RE, ITEM_FILE, TORRENT_FILE, _seeding_lock, _write_json, folder, info, pause, pid_alive, refused,
               seeding_runs)

MARKER = "seedstore.json"
WHAT = "seed copy"                          # what a copy's marker says it is
DEFAULT = "seedstore"                       # <install_dir>/.deltapatcher/seedstore/<torrent id>
LIST_NAME = "seed-copies.json"              # beside the main settings file: every copy this DeltaPatcher made
SUBFOLDERS = ("blobs", "sigs", ".deltapatcher")
PART_RE = re.compile(r"^[0-9a-f]{64}\.(\d+)\.part$")      # blobs/<sha>.<process id>.part: a blob going in
PIECE = 1 << 20                             # bytes copied or hashed between two checks
MARGIN = 1 << 30                            # what a byte copy leaves free on its drive: 1 GiB,
SHARE = 0.05                                # or 5% of the drive when that is more,
CAP = 20 << 30                              # but never more than 20 GiB
LOOK_ROOT = 0.2                             # seconds between looks, while a copy fills, that it is still one
QUICK = 2.0                                 # seconds the deletes of a command, or of a seed run's end, may take
FICLONE = 0x40049409                        # Linux: ioctl(destination, FICLONE, source) shares the source's blocks
CLONE_NOFOLLOW = 0x0001                     # macOS clonefile(): never through a symbolic link
GONE = "its seed copy was deleted: it is made again"
_NEWS = []                                  # what is said once in a process (news()), not said yet
_SAID = set()


class CopyError(Exception):
    """A seed copy that cannot be made where it should be."""


class CopyGone(Exception):
    """The copy's folder or its marker went while something was written into it: nothing more is written (only
    prepare() makes a copy's folder), and the next start makes a fresh copy."""

    def __init__(self):
        Exception.__init__(self, GONE)


class Full(Exception):
    """The drive of a copy has no room for its next byte copy: `needs` bytes are still to copy, `free` are free;
    `added`: the blobs the fill put in before (fill sets it)."""

    def __init__(self, needs, free, kept):
        from ..publish import human
        Exception.__init__(self, "the copy needs %s more and its drive has %s free, but %s always stays free there: "
                                 "seed from the folder with seed_copy = no, or move seed copies with seed_copies"
                           % (human(needs), human(free), human(kept)))
        self.needs, self.free = needs, free
        self.added = 0


def _key(path):
    return os.path.normcase(os.path.abspath(path)) if path else ""


def _note(what, text):
    """`text` said once in this process (news()): the first time `what` happens."""
    if what not in _SAID:
        _SAID.add(what)
        _NEWS.append(text)


def news():
    """What seed copies have to say that was not said yet (each once in a process): the caller says it."""
    out = list(_NEWS)
    del _NEWS[:]
    return out


# ---- where a copy is ------------------------------------------------------------------------------------

def copies_dir(general):
    """The folder general.seed_copies names (a relative one starts at the main settings file's folder); '' when it is
    empty: each copy is then inside its project's folder."""
    given = str((general or {}).get("seed_copies") or "").strip()
    if not given:
        return ""
    return os.path.abspath(os.path.join(os.path.dirname(config.path()), os.path.expanduser(given)))


def default_place(install_dir, torrent_id):
    """A copy inside its project's folder: <install_dir>/.deltapatcher/seedstore/<torrent id>, one for each torrent."""
    return os.path.join(os.path.abspath(install_dir), ".deltapatcher", DEFAULT, torrent_id)


def shared_place(base, torrent_id, install_dir):
    """A copy in the seed copies folder `base`: <base>/<torrent id>-<first 8 hex of the SHA-256 of the folder's path,
    in the system's letter case>, so two folders of one release keep a copy each."""
    digest = _sha256(os.path.normcase(os.path.abspath(install_dir)).encode("utf-8")).hexdigest()[:8]
    return os.path.join(base, "%s-%s" % (torrent_id, digest))


def place(cfg, general):
    """Where a project's copy is, as its settings and general.seed_copies say now; '' when it has no folder or no
    torrent."""
    install = str(cfg.get("install_dir") or "").strip()
    t = info(cfg)
    if not install or t is None:
        return ""
    base = copies_dir(general)
    return shared_place(base, t["id"], install) if base else default_place(install, t["id"])


def marker(path):
    """A copy's marker (seedstore.json) as a dict; None when it has none that can be read."""
    try:
        with open(os.path.join(path, MARKER), "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _linked(path):
    """`path` is a link: a symbolic link, or on Windows a junction (publish.is_link, which publishing uses too); never a
    copy's folder, never written, pruned or deleted through. Any other reparse point (a OneDrive or Dropbox
    placeholder, a deduplicated file) is an ordinary file or folder."""
    return is_link(path)


def _in_project(path):
    """`path` is a copy's place inside a project's folder, <install_dir>/.deltapatcher/seedstore/<torrent id>
    (default_place); never one in a seed copies folder, named <torrent id>-<8 hex>, whatever that folder is called."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    return bool(ID_RE.match(os.path.basename(path))) and os.path.basename(parent) == DEFAULT and \
        os.path.basename(os.path.dirname(parent)) == ".deltapatcher"


def is_copy(path):
    """The folder `path` is a seed copy: a folder (not a link), with its blobs/, sigs/ and .deltapatcher/ folders (when
    there) not links either, and its marker says so."""
    if not path or _linked(path) or not os.path.isdir(path):
        return False
    if any(_linked(os.path.join(path, n)) for n in SUBFOLDERS):
        return False
    doc = marker(path)
    return doc is not None and doc.get("deltapatcher") == WHAT


# ---- the list of the copies this DeltaPatcher made --------------------------------------------------------

def list_path():
    """seed-copies.json beside the main settings file: {path: {"torrent_id", "folder", "project"}}."""
    return os.path.join(os.path.dirname(config.path()), LIST_NAME)


def _read_list():
    """(the list, '') as it is, {} when it is not there; (None, why) when it is there but cannot be read or understood
    (then it is never written over: _listed; a clean-up sets it aside and makes it again: _set_aside). A read Windows
    refuses for a moment (another process replacing the file) is tried again for a moment first."""
    for attempt in range(pause.TRIES):
        try:
            with open(list_path(), "r", encoding="utf-8") as f:
                doc = json.load(f)
            break
        except FileNotFoundError:
            return {}, ""
        except PermissionError as e:
            if attempt == pause.TRIES - 1:
                return None, (str(e).splitlines() or [type(e).__name__])[0]
            time.sleep(pause.TRY_EVERY)
        except (OSError, ValueError) as e:
            return None, (str(e).splitlines() or [type(e).__name__])[0]
    if not isinstance(doc, dict):
        return None, "it is not a JSON object"
    return {p: v for p, v in doc.items() if isinstance(p, str) and isinstance(v, dict)}, ""


def made():
    """{path: {"torrent_id", "folder", "project"}} of every copy this DeltaPatcher made and has not deleted yet ({}
    while the list cannot be read: no copy is then deleted, since only one on the list ever is, until a clean-up makes
    it again)."""
    return _read_list()[0] or {}


def _entry(path):
    for p, v in made().items():
        if _key(p) == _key(path):
            return v
    return None


def _listed(change):
    """Change the list (change(doc) edits it in place), only while its lock is held, so two processes never lose each
    other's entries: when the lock cannot be had, the change waits for the next time (prepare() adds a copy again, a
    clean-up takes one off again). A list that cannot be read or understood is never written over (said once:
    news()): the next clean-up sets it aside and makes it again (_set_aside, _tend). A failure to write it changes
    nothing else."""
    path = list_path()
    with _seeding_lock(path=path + ".lock") as held:
        if not held:
            return
        doc, why = _read_list()
        if doc is None:
            _note(("list", _key(path)), "the list of seed copies %s cannot be read (%s): nothing is added to it or "
                                        "taken off it until the next clean-up sets it aside and makes it again"
                                        % (path, why))
            return
        before = json.dumps(doc, sort_keys=True)
        change(doc)
        if json.dumps(doc, sort_keys=True) == before:
            return
        try:
            if doc:
                _write_json(path, doc)
            elif os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def _list_add(path, entry):
    def change(doc):
        for p in [p for p in doc if _key(p) == _key(path)]:
            del doc[p]
        doc[os.path.abspath(path)] = entry
    _listed(change)


def _list_remove(path):
    def change(doc):
        for p in [p for p in doc if _key(p) == _key(path)]:
            del doc[p]
    _listed(change)


def place_problem(path):
    """Why a copy cannot be made at `path` now ('' when it can): the folder it goes in (a project's .deltapatcher, or
    the seed copies folder: neither is ever made) is not there or is a link, or something that is not a seed copy is
    where it goes (never used, never deleted)."""
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    inside = _in_project(path)
    base = os.path.dirname(parent) if inside else parent
    if _linked(base):
        return "%s is a symlink or junction: seed copies go only into a real folder (seed_copies)" % base
    if not os.path.isdir(base):
        return "the folder %s is not there: seed copies go only into a folder that is there" % base
    if inside and os.path.lexists(parent) and (_linked(parent) or not os.path.isdir(parent)):
        return "%s is there and is not a folder for seed copies: delete it or move seed copies (seed_copies)" % parent
    if os.path.lexists(path) and not is_copy(path):
        if _linked(path) or not os.path.isdir(path) or _names(path):
            return "%s is there and is not a seed copy: delete it or move seed copies (seed_copies)" % path
    return ""


def prepare(path, torrent_id, install_dir, project):
    """The copy's folder at `path`, made when it is not there (the only place a copy's folder is made), with its
    marker (written again when it names another folder or project), and on the list of copies made here. CopyError
    when it cannot be made there (place_problem): what is there is never used, and never deleted."""
    problem = place_problem(path)
    if problem:
        raise CopyError(problem)
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    inside = _in_project(path)
    if inside and not os.path.isdir(parent):
        os.mkdir(parent)
    if not os.path.isdir(path):
        os.mkdir(path)
    doc = {"deltapatcher": WHAT, "torrent_id": torrent_id, "folder": os.path.abspath(install_dir), "project": project}
    if marker(path) != doc:
        _write_json(os.path.join(path, MARKER), doc)
    if _entry(path) != {"torrent_id": torrent_id, "folder": doc["folder"], "project": project}:
        _list_add(path, {"torrent_id": torrent_id, "folder": doc["folder"], "project": project})


def _real(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(path))))


def _within(inner, outer):
    """inner is outer or a folder inside it (both as _real() gives them)."""
    try:
        return os.path.commonpath([inner, outer]) == outer
    except ValueError:                  # on another drive
        return False


def copies_problem(value):
    """What is wrong with `value` as seed_copies ('' when nothing; empty is right: each copy inside its project's
    folder): a folder that is there, not a drive's root, and not the folder a project keeps up to date, releases or
    prepares its releases in, nor inside one or holding one."""
    from .. import projects
    from .seed import store_of
    base = copies_dir({"seed_copies": value})
    if not base:
        return ""
    if _linked(base):
        return "seed_copies: %s is a symlink or junction: pick a real folder" % base
    if not os.path.isdir(base):
        return "seed_copies: %s is not a folder that is there: give a folder that is there" % base
    if os.path.dirname(base.rstrip("\\/")) == base.rstrip("\\/") or os.path.dirname(base) == base:
        return "seed_copies: %s is a drive's root: give a folder of its own" % base
    b = _real(base)
    for p in projects.all_projects():
        if p.missing:
            continue
        cfg = config.load(p)
        for what, folder_path in (("the folder %s keeps up to date (install_dir)" % p.title, cfg.get("install_dir")),
                                  ("the folder %s releases (publish_src)" % p.title, cfg.get("publish_src")),
                                  ("the store of %s's releases" % p.title, store_of(cfg))):
            folder_path = str(folder_path or "").strip()
            if not folder_path:
                continue
            f = _real(folder_path)
            if b == f:
                how = "is %s" % what
            elif _within(b, f):
                how = "is inside %s, %s" % (what, folder_path)
            elif _within(f, b):
                how = "holds %s, %s" % (what, folder_path)
            else:
                continue
            return "seed copies would go in %s, which %s: give them a folder of their own (seed_copies)" % (base, how)
    return ""


# ---- deleting a copy --------------------------------------------------------------------------------------

def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _unlink(path):
    """A link itself, never what it points to (a link to a folder on Windows goes with rmdir)."""
    try:
        os.remove(path)
    except OSError:
        try:
            os.rmdir(path)
        except OSError:
            pass


def _names(d):
    try:
        return os.listdir(d)
    except OSError:
        return []


def _empty(d, stop):
    """Delete what the folder d holds, file by file, never through a link: True when it is empty (False when stop()
    said to stop, or a file could not go)."""
    for n in _names(d):
        if stop():
            return False
        full = os.path.join(d, n)
        if _linked(full):
            _unlink(full)
        elif os.path.isdir(full):
            if not _empty(full, stop):
                return False
            try:
                os.rmdir(full)
            except OSError:
                pass
        else:
            _remove(full)
    return not _names(d)


def _rmdir_store(path):
    """After the delete of the copy at `path`: when it was inside a project's folder (_in_project), that folder's
    .deltapatcher/seedstore goes once it is empty. Never a seed copies folder, whatever it is called."""
    if _in_project(path):
        try:
            os.rmdir(os.path.dirname(os.path.abspath(path)))
        except OSError:
            pass


def _vanished(path):
    """The copy at `path` is not there while the folder it goes in is (the seed copies folder, or for one inside a
    project's folder that folder's .deltapatcher, as seedstore/ goes with its last copy): it was deleted, and its
    entry can leave the list. When that folder is missing too (its drive is away: unplugged, not mounted), nothing is
    known: its entry stays, and the copy is judged once it is back."""
    if not path or os.path.lexists(path):
        return False
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    return os.path.isdir(os.path.dirname(parent) if _in_project(path) else parent)


def delete(path, stop=None):
    """Delete the seed copy at `path`, only when it is one (is_copy: a folder without the marker, or one that is or
    holds a link, is never touched, whatever it is called): its files one by one, then its folders, the marker last,
    so a deletion cut short (stop() said to stop, or a file is still open) leaves a marked folder the next one
    finishes. It then leaves the list of copies made here. True when it is gone."""
    if not is_copy(path):
        if _vanished(path) and _entry(path) is not None:
            _list_remove(path)                          # deleted by hand: off the list too
        return False
    stop = stop or (lambda: False)
    for n in _names(path):
        if n == MARKER:
            continue
        if stop():
            return False
        full = os.path.join(path, n)
        if _linked(full):
            _unlink(full)
        elif os.path.isdir(full):
            if not _empty(full, stop):
                return False
            try:
                os.rmdir(full)
            except OSError:
                pass
        else:
            _remove(full)
    if _names(path) != [MARKER]:
        return False                                    # a file still open (Windows): the marker stays for the next
    try:
        os.remove(os.path.join(path, MARKER))
        os.rmdir(path)
    except OSError:
        return False
    _rmdir_store(path)
    _list_remove(path)
    return True


def _tidy(path):
    """What is left of a copy that went while something wrote into it: its empty folders go (never one with anything
    in it, never one that is a copy again), so no folder without the marker stays behind."""
    if is_copy(path):
        return
    for n in SUBFOLDERS:
        full = os.path.join(path, n)
        if os.path.isdir(full) and not _linked(full):
            try:
                os.rmdir(full)
            except OSError:
                pass
    try:
        os.rmdir(path)
    except OSError:
        pass
    _rmdir_store(path)


def size_of(path):
    """What the copy holds, as its files' sizes add up."""
    total = 0
    for top, _dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(top, n))
            except OSError:
                pass
    return total


def bytes_on_drive(path):
    """What the seed copies this DeltaPatcher made take on the drive of `path`."""
    try:
        dev = os.stat(path).st_dev
    except OSError:
        return 0
    total = 0
    for p in made():
        try:
            if os.stat(p).st_dev == dev and is_copy(p):
                total += size_of(p)
        except OSError:
            pass
    return total


# ---- which copies are needed ------------------------------------------------------------------------------

def needed(project, cfg, general):
    """The project seeds from a copy as the settings are now: torrents allowed, seed = yes, seed_copy = yes, shared by
    torrent, with a folder to keep up to date, and not seeded as a store (the publisher's own store is never
    copied)."""
    if refused(general) or not cfg.get("seed") or not cfg.get("seed_copy") or info(cfg) is None:
        return False
    if not str(cfg.get("install_dir") or "").strip() or (project is not None and project.missing):
        return False
    from .seed import store_of
    store = store_of(cfg)
    return not (store and os.path.isfile(os.path.join(store, TORRENT_FILE)))


def needed_at(project, cfg, general):
    """Where the project needs its copy now; '' when it needs none."""
    return place(cfg, general) if needed(project, cfg, general) else ""


def in_use(but=None):
    """The copies the seed runs going on now seed from (their lines in seeding.json, fresh ones only); but: the
    process id of a seed run whose own lines do not count (it says what it keeps itself)."""
    out = set()
    for run in seeding_runs():
        if but is not None and run.get("pid") == but:
            continue
        for row in run.get("projects") or []:
            c = row.get("copy") if isinstance(row, dict) else None
            if isinstance(c, dict) and isinstance(c.get("path"), str) and c["path"]:
                out.add(_key(c["path"]))
    return out


class Needs(object):
    """What the settings say of seed copies now. ok: the main settings file was read whole (otherwise no copy is
    deleted at all); want: the copies a project needs as its settings are, a seed run going on uses, or the caller
    keeps (a seed run's own projects: `but`, its process id, so its own lines in seeding.json do not count); blind: the
    projects whose settings file cannot be read whole (where their copies are, is not known); stay: (torrent id,
    folder) of the projects that need a copy where one cannot be made (seed_copies set by hand to a folder that cannot
    be used: copies_problem, place_problem), whose copy elsewhere stays; mine: the copies on this installation's
    list."""

    def __init__(self, general, keep=(), but=None):
        from .. import projects
        general = general or {}
        self.want = {_key(p) for p in keep if p}
        self.blind = []
        self.stay = set()
        self.mine = set()
        self.ok = not config.unreadable(config.path())
        if not self.ok:
            return
        self.mine = {_key(p) for p in made()}
        unusable = copies_problem(general.get("seed_copies", "")) if copies_dir(general) else ""
        for p in projects.all_projects():
            if p.missing:
                continue
            why = config.unreadable(p.path)
            if why:
                self.blind.append(p.path)
                _note(("blind", _key(p.path)), "the settings file %s cannot be read whole (%s): no seed copy is "
                                               "deleted until it can be read again" % (p.path, why))
                continue
            cfg = config.load(p)
            at = needed_at(p, cfg, general)
            if at:
                self.want.add(_key(at))
                if unusable or place_problem(at):
                    self.stay.add((info(cfg)["id"], _key(str(cfg["install_dir"]).strip())))
        self.want |= in_use(but)

    def spares(self, path):
        """The copy at `path` stays: the main settings could not be read; something needs it; a project's settings
        file cannot be read whole (every project whose place it may be counts, and that one's place is not known);
        the project its marker or list entry names has one that cannot be; it is the copy of a torrent and folder
        whose copy cannot be made where the settings put it now (stay); or it is not on this installation's list
        (another settings file's copy, at a project's own place or in a seed copies folder: left alone)."""
        if not self.ok or self.blind or _key(path) in self.want or _key(path) not in self.mine:
            return True
        for doc in (marker(path) or {}, _entry(path) or {}):
            proj = doc.get("project")
            if isinstance(proj, str) and proj and config.unreadable(proj):
                return True
            if (doc.get("torrent_id"), _key(str(doc.get("folder") or ""))) in self.stay:
                return True
        return False


def discard(path, general, keep=(), stop=None, but=None):
    """Delete the copy at `path` unless something still needs it (Needs.spares). True when it went."""
    if not path or Needs(general, keep, but).spares(path):
        return False
    return delete(path, stop)


def _set_aside():
    """A list that cannot be read or understood is set aside as seed-copies.json.bad (over an older one), said once,
    so the clean-up makes a new one from the copies it finds (_tend) instead of leaving it in the way for good."""
    path = list_path()
    with _seeding_lock(path=path + ".lock") as held:
        if not held:
            return
        doc, why = _read_list()
        if doc is not None:
            return
        try:
            _replace(path, path + ".bad")
        except OSError:
            return
    _note(("bad", _key(path)), "the list of seed copies %s could not be read (%s): it is kept as %s.bad, and a new one "
                               "is made from the seed copies found at the projects' places" % (path, why, path))


def _tend(general):
    """The list looked after before a clean-up judges it, so no copy of this installation is left where nothing finds
    it: a list that cannot be read is set aside (_set_aside), and a marked copy at a current project's own place (its
    default place in its folder, or its place in the seed copies folder) is put on it (adopted) when its marker names
    this installation's settings file of that project and that project's torrent id. A copy no current project of this
    installation places there is never adopted: another installation's copies stay untouched."""
    from .. import projects
    _set_aside()
    listed = {_key(p) for p in made()}
    base = copies_dir(general)
    for p in projects.all_projects():
        if p.missing or config.unreadable(p.path):
            continue
        cfg = config.load(p)
        install = str(cfg.get("install_dir") or "").strip()
        t = info(cfg)
        if not install or t is None:
            continue
        for at in [default_place(install, t["id"])] + ([shared_place(base, t["id"], install)] if base else []):
            if _key(at) in listed or not is_copy(at):
                continue
            doc = marker(at) or {}
            if doc.get("torrent_id") == t["id"] and _key(str(doc.get("project") or "")) == _key(p.path):
                _list_add(at, {"torrent_id": t["id"], "folder": os.path.abspath(install), "project": p.path})
                listed.add(_key(at))


def unneeded(general, keep=(), but=None, stop=None):
    """The copies on the list of copies made here that nothing needs now (Needs: none on settings that cannot be
    read), after the list is looked after (_tend), wherever they are: inside a project's folder, in the seed copies
    folder, in the one before a change of seed_copies, or of a project that left or changed folder. A copy that is not
    on the list is another settings file's and never goes. An entry whose copy is gone from a folder that is there
    leaves the list; one whose drive or folder is away stays until it is back (_vanished). A copy a seed run going on
    now uses is left to that run; `keep`: copies the caller is about to use (but: a seed run's process id, whose own
    lines do not count). stop(): asked between copies."""
    if config.unreadable(config.path()):
        return []
    _tend(general)
    needs = Needs(general, keep, but)
    if not needs.ok:
        return []
    out = []
    for path in sorted(made()):
        if stop is not None and stop():
            break
        if not os.path.lexists(path):
            if _vanished(path):
                _list_remove(path)
            continue
        if not needs.spares(path):
            out.append(path)
    return out


def clean(general, keep=(), stop=None, but=None):
    """Delete every seed copy nothing needs now (unneeded), each its marker last; a copy a seed run going on now uses is
    left to that run, which deletes it itself once its files are closed. stop(): asked between files. Returns the
    copies deleted."""
    deleted = []
    for path in unneeded(general, keep, but, stop):
        if stop is not None and stop():
            break
        if delete(path, stop):
            deleted.append(path)
    return deleted


# ---- what is in a copy ------------------------------------------------------------------------------------

def _replace(src, dst):
    """os.replace, tried again for a moment when Windows refuses it (an antivirus or an indexer reading the file)."""
    for attempt in range(pause.TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == pause.TRIES - 1:
                raise
            time.sleep(pause.TRY_EVERY)


class CopyCache(hashing.HashCache):
    """A copy's hash cache, <copy>/.deltapatcher/hashes.json: written only while the copy is one (is_copy), and never
    making the copy's folder again (only prepare() makes it)."""

    def __init__(self, root):
        self.root = root
        hashing.HashCache.__init__(self, os.path.join(root, ".deltapatcher", "hashes.json"))

    def save(self):
        if not self.dirty or not is_copy(self.root):
            return
        d = os.path.dirname(self.path)
        if not os.path.isdir(d):
            os.mkdir(d)                                 # only this folder: an OSError when the copy itself went
        tmp = "%s.%d.%d.tmp" % (self.path, os.getpid(), threading.get_ident())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, separators=(",", ":"))
        try:
            _replace(tmp, self.path)
        except OSError:
            _remove(tmp)
            raise
        self.dirty = False


def hash_cache(root):
    """The hash cache of the copy at `root` (CopyCache)."""
    return CopyCache(root)


def _sources(manifests):
    """{sha256: [paths]} of the files the manifests name that may be copied: never a protected one (the player's own
    copy), as folder seeding never offers one."""
    by_sha, protected = {}, set()
    for man in manifests:
        for p in sorted(man.files):
            if man.is_protected(p):
                protected.add(p.lower())
            else:
                paths = by_sha.setdefault(man.files[p].sha256, [])
                if p not in paths:
                    paths.append(p)
    return {sha: [p for p in paths if p.lower() not in protected] for sha, paths in by_sha.items()}


def _drop_parts(path):
    """Blobs half written by a process that ended go from the copy at `path` (never through a link)."""
    if not is_copy(path):
        return
    bdir = os.path.join(path, "blobs")
    for n in _names(bdir):
        m = PART_RE.match(n)
        if (m and not pid_alive(int(m.group(1)))) or (n.endswith(".part") and not m):
            _remove(os.path.join(bdir, n))


def _hash_file(path, check, tick=None):
    """The SHA-256 of a file, check() asked between its PIECE pieces (folder.Interrupted when it says to stop)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if check():
                raise folder.Interrupted()
            b = f.read(PIECE)
            if not b:
                break
            h.update(b)
            if tick is not None:
                tick(len(b))
    return h.hexdigest()


def _copy(src, dst, check, tick):
    """A byte copy of src into dst, hashed as it is copied (one read of the source, one write); its SHA-256. The
    source (a file of the game folder) is closed before the copy is flushed to the disk, and check() is asked once
    more before that: an update or a stop then drops the copy instead of waiting for its flush."""
    h = hashlib.sha256()
    with open(dst, "wb") as fo:
        with open(src, "rb") as fi:
            while True:
                if check():
                    raise folder.Interrupted()
                b = fi.read(PIECE)
                if not b:
                    break
                h.update(b)
                fo.write(b)
                tick(len(b))
        if check():
            raise folder.Interrupted()
        fo.flush()
        os.fsync(fo.fileno())
    return h.hexdigest()


_LIB = []


def _clonefile(src, dst):
    try:
        if not _LIB:
            import ctypes
            import ctypes.util
            try:
                lib = ctypes.CDLL(ctypes.util.find_library("System") or "/usr/lib/libSystem.B.dylib", use_errno=True)
                fn = lib.clonefile
                fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint32]
                fn.restype = ctypes.c_int
            except (OSError, AttributeError):
                fn = None
            _LIB.append(fn)
        return _LIB[0] is not None and _LIB[0](os.fsencode(src), os.fsencode(dst), CLONE_NOFOLLOW) == 0
    except (OSError, ValueError, TypeError):
        return False


def _ficlone(src, dst):
    try:
        import fcntl
        with open(src, "rb") as fi, open(dst, "xb") as fo:
            fcntl.ioctl(fo.fileno(), FICLONE, fi.fileno())
        return True
    except (OSError, ImportError, ValueError):
        _remove(dst)
        return False


def _clone(src, dst):
    """Make dst (not there yet) a space-sharing clone of src: True when the drive made one. macOS: clonefile() (APFS);
    Linux: the FICLONE ioctl (Btrfs, XFS); Windows: never."""
    if sys.platform == "darwin":
        return _clonefile(src, dst)
    if sys.platform.startswith("linux"):
        return _ficlone(src, dst)
    return False


def _room(d, size, needs):
    """Full when a byte copy of `size` bytes into the folder d would leave its drive less free than it keeps: the
    larger of MARGIN and SHARE of the drive, never more than CAP. `needs`: what the copy still needs, for the
    message."""
    usage = shutil.disk_usage(d)
    kept = min(max(MARGIN, int(usage.total * SHARE)), CAP)
    if usage.free - size < kept:
        raise Full(needs, usage.free, kept)


def _put(src, part, size, needs, check, tick):
    """A folder file into `part`: a clone when the drive makes one (hashed in full), else a byte copy (with room for
    it on the drive); the SHA-256 of what was written."""
    _remove(part)
    try:
        cloned = _clone(src, part)
    except OSError:
        cloned = False
    if cloned:
        return _hash_file(part, check, tick)
    _remove(part)
    _room(os.path.dirname(part), size, needs)
    return _copy(src, part, check, tick)


def _holds(cache, f, sha, full, check):
    """The copy holds the blob `sha` (TFile f) as it should: from its hash cache, else hashed; one that holds other
    bytes goes."""
    st = folder._stat(full)
    if st is None:
        return False
    key = "blobs/" + sha
    if st.st_size == f.size:
        if cache.get(key, st) == sha:
            return True
        try:
            got = _hash_file(full, check)
        except OSError:
            got = None                                  # it cannot be read now: it goes, and comes again
        if got == sha:
            st2 = folder._stat(full)
            if st2 is not None and (st2.st_size, st2.st_mtime_ns) == (st.st_size, st.st_mtime_ns):
                cache.put(key, st, sha)
            return True
    _remove(full)
    return False


def _missing(path):
    """The copy at `path` is not there: its folder is gone (or is a link now), or its marker is. A marker there that
    cannot be read for a moment is not missing."""
    return not os.path.isdir(path) or _linked(path) or not os.path.lexists(os.path.join(path, MARKER))


def _watcher(path):
    """A look, at most every LOOK_ROOT seconds, whether the copy at `path` went: True from then on, once two looks in
    a row found its folder or its marker missing (never on one read error)."""
    state = {"at": 0.0, "misses": 0}

    def lost():
        now = time.time()
        if state["misses"] < 2 and now - state["at"] >= LOOK_ROOT:
            state["at"] = now
            state["misses"] = state["misses"] + 1 if _missing(path) else 0
        return state["misses"] >= 2
    return lost


def fill(path, files, install_dir, manifests, check=None, progress=None):
    """Put into the copy at `path` each blob of the torrent (`files`: folder.TFile) that it lacks or holds wrong, from
    the folder `install_dir`: a file there the manifests name with that SHA-256 (never a protected one, the player's
    own copy), copied only when it has those bytes now (a file the player changed is never copied). check() is asked
    between files and PIECE bytes (folder.Interrupted when it says to stop: the blobs done stay, the one going in
    goes); progress(done, total, written): the bytes of the torrent's blobs the copy holds, of all of them, and the
    bytes this fill wrote into it. Full when the drive has no room for the next byte copy (the blobs done stay);
    CopyGone when the copy went meanwhile (nothing more is written into it, and the empty folders left of it go).
    Returns (added, failed, why): how many blobs went in, how many could not for now (a file held open, a rename
    refused: they are tried again later), and the first reason of those. A Full or an Interrupted carries `added`
    too: the blobs that went in before it, so they are offered whatever ended the fill."""
    check = check or (lambda: False)
    progress = progress or (lambda done, total, written: None)
    if not is_copy(path):
        raise CopyGone()
    lost = _watcher(path)

    def guard():
        if lost():
            raise CopyGone()
        return check()
    blobs = []
    for f in files:
        sha = None if f.pad else folder.blob_of(f.path)
        if sha:
            blobs.append((f, sha))
    total = sum(f.size for f, _sha in blobs)
    bdir = os.path.join(path, "blobs")
    cache = hash_cache(path)
    done, added, written, failed, why, missing = 0, 0, 0, 0, "", []
    try:
        if not os.path.isdir(bdir):
            os.mkdir(bdir)                              # never its parents: only prepare() makes a copy's folder
        _drop_parts(path)
        progress(done, total, written)
        for f, sha in blobs:                            # what the copy holds already, checked
            if guard():
                raise folder.Interrupted()
            if _holds(cache, f, sha, os.path.join(bdir, sha), guard):
                done += f.size
                progress(done, total, written)
            else:
                missing.append((f, sha))
        if missing:
            sources = _sources(manifests)
            fcache = folder.hash_cache(install_dir)     # the folder's: a file known to be changed is never copied
            needs = sum(f.size for f, _sha in missing)
            try:
                for f, sha in missing:
                    if guard():
                        raise folder.Interrupted()
                    partly = [0]

                    def tick(n, size=f.size, done=done, written=written, partly=partly):
                        partly[0] = min(partly[0] + n, size)
                        progress(done + partly[0], total, written + partly[0])
                    brought, error = _bring(path, f, sha, sources.get(sha, []), install_dir, cache, fcache, guard,
                                            needs, tick)
                    if brought:
                        done += f.size
                        written += f.size
                        added += 1
                    elif error:
                        failed += 1
                        why = why or error
                    needs -= f.size
                    progress(done, total, written)
            finally:
                folder._save(fcache)
    except CopyGone:
        _tidy(path)
        raise
    except (Full, folder.Interrupted) as e:
        e.added = added
        raise
    except OSError:
        if not is_copy(path):                           # blobs/ could not be made: the copy went
            _tidy(path)
            raise CopyGone()
        raise
    finally:
        folder._save(cache)
    return added, failed, why


def _bring(root, f, sha, paths, install_dir, cache, fcache, check, needs, tick):
    """The blob `sha` into the copy at `root` from the first of the folder's files `paths` that has its bytes now:
    (True, '') when it went in; (False, why) when a file could not be read or written now (tried again later);
    (False, '') when no file of the folder has it now (missing, changed or the player's own)."""
    target = os.path.join(root, "blobs", sha)
    part = "%s.%d.part" % (target, os.getpid())
    why = ""
    for p in paths:
        src = os.path.join(install_dir, *p.split("/"))
        st = folder._stat(src)
        if st is None or st.st_size != f.size:
            continue                                    # missing here, or changed
        known = fcache.get(p, st)
        if known is not None and known != sha:
            continue                                    # changed here: never copied
        if not is_copy(root):
            raise CopyGone()                            # nothing is written into a folder that is not a copy
        try:
            got = _put(src, part, f.size, needs, check, tick)
            st2 = folder._stat(src)
            if st2 is not None and (st2.st_size, st2.st_mtime_ns) == (st.st_size, st.st_mtime_ns):
                fcache.put(p, st, got)
            if got != sha:
                continue                                # it changed while it was copied: not offered
            if not is_copy(root):
                raise CopyGone()
            _replace(part, target)
            cache.put("blobs/" + sha, os.stat(target), sha)
            return True, ""
        except OSError as e:
            why = why or "%s: %s" % (p, e.strerror or e)
            continue                                    # a file held open, a rename refused: tried again later
        finally:
            _remove(part)                               # gone, unless it became the blob
    return False, why


def write_release(path, raw, sig, tor, item_bytes):
    """The release's own files into the copy: manifest.json, sigs/<sha256 of it>.sig, release.torrent and
    release.item, each through a temporary file of this process (no torrent has them open: the one of the release
    before is out). CopyGone when the copy is not one (any more)."""
    if not is_copy(path):
        raise CopyGone()
    files = {"manifest.json": raw, TORRENT_FILE: tor, ITEM_FILE: item_bytes}
    if sig:
        files["sigs/%s.sig" % _sha256(raw).hexdigest()] = sig
    try:
        sdir = os.path.join(path, "sigs")
        if not os.path.isdir(sdir):
            os.mkdir(sdir)                              # never its parents
        for rel, data in files.items():
            if data is None:
                continue
            p = os.path.join(path, *rel.split("/"))
            tmp = "%s.%d.tmp" % (p, os.getpid())
            with open(tmp, "wb") as fh:
                fh.write(data)
            _replace(tmp, p)
    except OSError:
        if not is_copy(path):
            _tidy(path)
            raise CopyGone()
        raise


def prune(path, files, raw, stop=None):
    """What no release seeded now needs goes from the copy (no torrent has it open any more): blobs not in the
    torrent's `files`, signatures of other manifests than `raw`, temporary files a write left; its hash cache keeps
    only the blobs it holds. Never in a folder that is not a copy (or holds a link); stop() is asked between files."""
    if not is_copy(path):
        return
    stop = stop or (lambda: False)
    wanted = set()
    for f in files:
        sha = None if f.pad else folder.blob_of(f.path)
        if sha:
            wanted.add(sha)
    bdir = os.path.join(path, "blobs")
    for n in _names(bdir):
        m = PART_RE.match(n)
        if n in wanted or (m and pid_alive(int(m.group(1)))):
            continue
        if stop():
            return
        _remove(os.path.join(bdir, n))
    sig = "%s.sig" % _sha256(raw).hexdigest()
    for n in _names(os.path.join(path, "sigs")):
        if n != sig:
            _remove(os.path.join(path, "sigs", n))
    for n in _names(path):
        if n.endswith(".tmp"):
            _remove(os.path.join(path, n))
    cache = hash_cache(path)
    keep = {"blobs/" + s for s in wanted}
    for k in [k for k in cache.data if k not in keep]:
        del cache.data[k]
        cache.dirty = True
    folder._save(cache)
