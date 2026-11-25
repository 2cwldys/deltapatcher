"""Backups of a project's live release: what players download now, read from where it is deployed (the
project's server over SFTP, or its deploy folder) and kept on this computer. A backup only reads at the
destination. A restore is a release of the backup, its blobs first and its manifest last: it removes nothing
there, except a file of the same name and the wrong size, which it replaces.

    <base>/<its settings file's name>-<project id>/
                                     a project's backup folder, one for every project, Default too; <base> is
                                     backup_dir (a relative one is read from the main settings file's folder),
                                     else backups beside the main settings file
        project.json                 the project the folder belongs to
        blobs/<sha256>               the pool: each blob once, however many backups name it, checked against
                                     its SHA-256 as it arrives
        parts/<sha256>               the parts indexes the backups' releases name, each once, checked the same
                                     way; one the destination lacks is left out (players then download its
                                     file whole when it changes)
        <YYYY-MM-DD>/                one backup a day (UTC): manifest.json, manifest.previous.json and
                                     .htaccess when the destination has them, sigs/<sha256>.sig,
                                     release.torrent and release.item of a release shared by torrent, and
                                     backup.json (the project, when, from where)
        scheduled.json               the system scheduler's last run: when, and what it did

A folder, or a backup in it, that belongs to another project is never written, pruned or restored. The
backup folder never shares a folder with the project's store, the folder it releases, the folder releases
are copied to or the folder it keeps up to date (folder_problem()).

A backup of a release unchanged since the newest backup makes no new one, but tops that one up when the pool
lacks some of its blobs; a second backup the same day replaces that day's. After each backup the limits are
applied (limits(), apply_limits()) and blobs and parts indexes no backup names any more leave the pools. The
backup just made always stays. Nothing is removed while a dated folder cannot be read, and no backup is removed
by date when the one just made is dated before another (the computer's clock is behind, or was ahead).

schedule() has the operating system run `deltapatcher-cli --unattended --project FILE backup now --scheduled
daily|hourly` every hour: on Windows a Task Scheduler task that starts it through conhost --headless, so no
console window opens over a game; elsewhere a line in the user's crontab. The run skips while the newest
backup, or the scheduler's last run that worked, is younger than a day (daily) or an hour (hourly), so a
computer that was off or asleep catches up within the hour. It notes how it went in scheduled.json.
"""

import calendar
import codecs
import contextlib
import datetime
import hashlib
import json
import locale
import ntpath
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from xml.sax.saxutils import escape

from . import client, config, deploy, hashing, ssh
from .manifest import BLOB_DIR, HEX64, MANIFEST_NAME, PARTS_DIR, PREVIOUS_NAME, Manifest, ManifestError
from .publish import HTACCESS, human
from .signing import SIG_DIR, sig_name

FORMAT = "deltapatcher-backup"
VERSION = 1
INFO_NAME = "backup.json"
MARKER_NAME = "project.json"
MARKER_FORMAT = "deltapatcher-backup-folder"
SCHEDULED_NAME = "scheduled.json"
NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OLD_RE = re.compile(r"^\.old-(\d{4}-\d{2}-\d{2})-\d+$")
TASK = "DeltaPatcher backup"          # Windows: a project's task is "DeltaPatcher backup <project id>"
CRON_MARK = "# deltapatcher-backup "  # the user's crontab: the line this makes ends with it and the project's id
EVERY = {"daily": 24 * 3600, "hourly": 3600}     # a scheduled run backs up once in this many seconds
SLACK = 15 * 60                       # an hourly start comes a little early or late: never skipped for that
TASK_TIME_LIMIT = "PT23H"             # Task Scheduler ends a run that takes longer
RENAME_TRIES = 20 if os.name == "nt" else 1      # Windows: a virus scanner or the indexer can hold a folder a moment
RENAME_WAIT = 0.1
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(bytes|byte|b|kb|mb|gb|tb)\s*$", re.I)
_UNITS = {"b": 1, "byte": 1, "bytes": 1, "kb": 1 << 10, "mb": 1 << 20, "gb": 1 << 30, "tb": 1 << 40}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TIME = "%Y-%m-%dT%H:%M:%SZ"


class BackupError(Exception):
    """A backup, restore or schedule that cannot be done; `code` says why, for the app."""

    def __init__(self, message, code="failed", data=None):
        Exception.__init__(self, message)
        self.code = code
        self.data = data or {}


def now():
    """The time now, in seconds since the epoch (a test stands in for it)."""
    return time.time()


def _stamp(t):
    return time.strftime(_TIME, time.gmtime(t))


def _seconds(text):
    """A time as _stamp() writes it, in seconds since the epoch; None when it is not one."""
    try:
        return calendar.timegm(time.strptime(str(text), _TIME))
    except (ValueError, TypeError, OverflowError):
        return None


def project_id(project):
    """A short id of the project's settings file, the same as long as the file stays where it is."""
    return hashlib.sha256(os.path.normcase(os.path.abspath(project.path)).encode("utf-8")).hexdigest()[:8]


# ---- where a project's backups are --------------------------------------------------------------------

def base_of(cfg):
    """The folder the projects' backup folders are in: backup_dir (a relative one is read from the main
    settings file's folder), else backups beside the main settings file."""
    here = os.path.dirname(config.path())
    given = str(cfg.get("backup_dir") or "").strip()
    if not given:
        return os.path.join(here, "backups")
    return os.path.abspath(os.path.join(here, os.path.expanduser(given)))


def _project_folders(cfg):
    """[(what it is, its path)]: the project's folders its backups stay out of."""
    out = []
    src = str(cfg.get("publish_src") or "").strip()
    store = str(cfg.get("store_dir") or "").strip()
    if store:
        out.append(("the store of its releases (store_dir)", store))
    elif src:
        out.append(("the store of its releases", config.default_store(src)))
    if src:
        out.append(("the folder it releases (publish_src)", src))
    folder = str(cfg.get("deploy_folder") or "").strip()
    if folder and (cfg.get("deploy_mode") or "sftp") == "folder":
        out.append(("the folder its releases are copied to (deploy_folder)", folder))
    install = str(cfg.get("install_dir") or "").strip()
    if install:
        out.append(("the folder it keeps up to date (install_dir)", install))
    return out


def _real(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(path))))


def _within(inner, outer):
    """inner is outer or a folder inside it (both as _real() gives them)."""
    try:
        return os.path.commonpath([inner, outer]) == outer
    except ValueError:                  # on another drive
        return False


def folder_problem(cfg):
    """What is wrong with where the project's backups would go, '' when nothing: the folder they go in
    (backup_dir, else the default) must not be, hold, or be inside the project's store, the folder it
    releases, the folder its releases are copied to, or the folder it keeps up to date."""
    base = base_of(cfg)
    b = _real(base)
    for what, path in _project_folders(cfg):
        p = _real(path)
        where = os.path.abspath(os.path.expanduser(path))
        if b == p:
            how = "is %s" % what
        elif _within(b, p):
            how = "is inside %s, %s" % (what, where)
        elif _within(p, b):
            how = "holds %s, %s" % (what, where)
        else:
            continue
        return "backups would go in %s, which %s: give them a folder of their own (backup_dir)" % (base, how)
    return ""


def folder_of(project, cfg):
    """The project's backup folder: <base_of(cfg)>/<its settings file's name>-<project id>, one for every
    project, Default too. BackupError when it would share a folder with the project's other folders."""
    problem = folder_problem(cfg)
    if problem:
        raise BackupError(problem, "bad-setting")
    name = os.path.splitext(os.path.basename(project.path))[0]
    return os.path.join(base_of(cfg), "%s-%s" % (name, project_id(project)))


def parse_size(text):
    """backup_max_size in bytes ("10 GB", "500 MB"); None when it is empty (no limit); ValueError otherwise."""
    text = str(text or "").strip()
    if not text:
        return None
    m = _SIZE.match(text)
    if not m:
        raise ValueError("backup_max_size is a size with its unit, such as 10 GB or 500 MB: %r" % text)
    return int(float(m.group(1)) * _UNITS[m.group(2).lower()])


def limits(cfg):
    """The project's limits: backups kept a day, a week and a month back, and the folder's largest size."""
    try:
        size = parse_size(cfg.get("backup_max_size"))
    except ValueError as e:
        raise BackupError(str(e), "bad-setting")
    return {"daily": max(0, int(cfg.get("backup_keep_daily") or 0)),
            "weekly": max(0, int(cfg.get("backup_keep_weekly") or 0)),
            "monthly": max(0, int(cfg.get("backup_keep_monthly") or 0)), "max_size": size}


@contextlib.contextmanager
def _locked(folder):
    """One backup, prune or restore of a project at a time: the app's and the system scheduler's alike."""
    lock = client.Lock(folder)
    try:
        lock.__enter__()
    except client.UpdateError:
        raise BackupError("another backup of this project is running: try again when it is done", "busy")
    try:
        yield
    finally:
        lock.__exit__(None, None, None)


def _read_json(path):
    """The JSON object in a file; None when there is none, it cannot be read, or it is not an object."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_json(path, data):
    """A small JSON file, written beside and then put in place."""
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    _rename(tmp, path)


def _rename(src, dst):
    """os.replace, tried again for a moment (RENAME_TRIES, on Windows), as a virus scanner or the search indexer
    can hold a file or folder briefly."""
    for attempt in range(RENAME_TRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt >= RENAME_TRIES - 1:
                raise
            time.sleep(RENAME_WAIT)


def _check_owner(folder, project):
    """BackupError when the folder, or a backup in it, belongs to another project."""
    pid = project_id(project)
    marker = _read_json(os.path.join(folder, MARKER_NAME))
    if marker is not None and marker.get("project_id") not in (None, pid):
        raise BackupError("%s holds the backups of another project (its settings file: %s): nothing is done "
                          "there" % (folder, marker.get("file") or "not known"), "not-ours")
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for n in sorted(names):
        if NAME_RE.match(n):
            info = _read_json(os.path.join(folder, n, INFO_NAME))
            if info is not None and info.get("project_id") not in (None, pid):
                raise BackupError("the backup %s in %s is another project's: nothing is done there" % (n, folder),
                                  "not-ours")


def _mark(folder, project):
    """The folder's note of the project it belongs to, written once."""
    p = os.path.join(folder, MARKER_NAME)
    if not os.path.isfile(p):
        _write_json(p, {"format": MARKER_FORMAT, "version": 1, "project_id": project_id(project),
                        "file": project.path})


# ---- the backups in a folder ------------------------------------------------------------------------

class Backup(object):
    """One dated backup: its manifests and signatures are in its folder, its blobs in the pool. `problem`
    says why it cannot be read ('' when it can)."""

    def __init__(self, folder, name):
        self.name = name
        self.path = os.path.join(folder, name)
        self.problem = ""
        try:
            _week(name)
        except ValueError:
            self.problem = "its name is not a date"
        self.info = _read_json(os.path.join(self.path, INFO_NAME)) or {}
        self.manifest = self._manifest(MANIFEST_NAME, True)
        self.previous = self._manifest(PREVIOUS_NAME, False)

    def _manifest(self, name, needed):
        try:
            with open(os.path.join(self.path, name), "rb") as f:
                data = f.read()
        except FileNotFoundError:
            if needed and not self.problem:
                self.problem = "it has no %s" % name
            return None
        except OSError as e:
            if not self.problem:
                self.problem = ("its %s cannot be read: %s" % (name, e.strerror)) if e.strerror else \
                    "its %s cannot be read" % name
            return None
        try:
            return Manifest.from_json(data)
        except ManifestError:
            if not self.problem:
                self.problem = "its %s is damaged" % name
            return None

    def raw(self, name):
        try:
            with open(os.path.join(self.path, name), "rb") as f:
                return f.read()
        except OSError:
            return None

    def blobs(self):
        """{sha256: size} of every blob it needs: the two releases' together."""
        out = dict(self.previous.blobs()) if self.previous is not None else {}
        out.update(self.manifest.blobs())
        return out

    def parts(self):
        """The SHA-256 of every parts index its two releases name."""
        out = set(self.previous.parts()) if self.previous is not None else set()
        out.update(self.manifest.parts())
        return out

    def own_bytes(self):
        """What its own folder holds (manifests, signatures, backup.json)."""
        total = 0
        for top, _dirs, files in os.walk(self.path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(top, f))
                except OSError:
                    pass
        return total

    def describe(self):
        m = self.manifest
        return {"name": self.name, "made": str(self.info.get("made") or ""), "from": str(self.info.get("from") or ""),
                "release": {"name": m.name, "version": m.version, "published": m.published, "files": len(m.files),
                            "bytes": m.bytes},
                "previous": None if self.previous is None else {"name": self.previous.name, "version": self.previous.version}}


def scan(folder):
    """(the backups in a folder, the newest first; [(name, why)] of the dated folders that cannot be read)."""
    try:
        names = sorted(os.listdir(folder), reverse=True)
    except OSError:
        return [], []
    good, bad = [], []
    for n in names:
        if NAME_RE.match(n) and os.path.isdir(os.path.join(folder, n)):
            b = Backup(folder, n)
            if b.problem:
                bad.append((n, b.problem))
            else:
                good.append(b)
    return good, bad


def list_backups(folder):
    """The backups in a folder that can be read, the newest first."""
    return scan(folder)[0]


def _pool(folder):
    return os.path.join(folder, BLOB_DIR)


def _parts_pool(folder):
    return os.path.join(folder, PARTS_DIR)


def _recover(folder):
    """A same-day replacement that stopped between its two renames left that day's backup as .old-<day>-<pid>:
    it goes back in place."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    olds = []
    for n in names:
        m = OLD_RE.match(n)
        if m:
            p = os.path.join(folder, n)
            try:
                olds.append((os.path.getmtime(p), m.group(1), p))
            except OSError:
                pass
    for _t, day, p in sorted(olds, reverse=True):          # a day left twice: the one written last
        if not os.path.exists(os.path.join(folder, day)):
            try:
                _rename(p, os.path.join(folder, day))
            except OSError:
                pass                                        # tried again next time; never removed while missed


def _clean(folder):
    """What a backup that stopped halfway left: a day's backup it was replacing goes back in place; unfinished
    folders, a replaced day's old folder and blobs half downloaded go."""
    _recover(folder)
    for n in os.listdir(folder):
        m = OLD_RE.match(n)
        if n.startswith((".new-", ".incoming-")) or (m and os.path.isdir(os.path.join(folder, m.group(1)))):
            shutil.rmtree(os.path.join(folder, n), ignore_errors=True)
    pool = _pool(folder)
    for n in os.listdir(pool):
        if n.endswith(".part"):
            try:
                os.remove(os.path.join(pool, n))
            except OSError:
                pass
    parts_pool = _parts_pool(folder)
    for n in (os.listdir(parts_pool) if os.path.isdir(parts_pool) else []):
        if not HEX64.match(n):
            try:
                os.remove(os.path.join(parts_pool, n))
            except OSError:
                pass


def listing(project, cfg):
    """The backups of a project, newest first, with what the folder holds, the dated folders that cannot be
    read, the limits, and the system scheduler's last run."""
    folder = folder_of(project, cfg)
    if os.path.isdir(folder):
        _check_owner(folder, project)
    backups, bad = scan(folder)
    return {"folder": folder, "base": base_of(cfg), "backups": [b.describe() for b in backups],
            "unreadable": [{"name": n, "why": why} for n, why in bad], "bytes": _folder_bytes(folder),
            "limits": limits(cfg), "scheduled": last_scheduled(folder)}


def newest(project, cfg):
    """When the newest backup was made ("YYYY-MM-DDTHH:MM:SSZ"), '' when there is none: the latest time a
    backup.json gives that is not still to come (one made while the clock was ahead). Only backup.json files
    are read, so a listing of every project stays quick."""
    try:
        folder = folder_of(project, cfg)
        names = [n for n in os.listdir(folder) if NAME_RE.match(n)]
    except (BackupError, OSError):
        return ""
    at, best = now(), None
    for n in names:
        info = _read_json(os.path.join(folder, n, INFO_NAME))
        t = _seconds(info.get("made")) if info is not None else None
        if t is not None and t <= at and (best is None or t > best):
            best = t
    return _stamp(best) if best is not None else ""


def _folder_bytes(folder):
    total = 0
    for top, _dirs, files in os.walk(folder):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(top, f))
            except OSError:
                pass
    return total


# ---- taking one ---------------------------------------------------------------------------------------

def take(project, cfg, target, log=None, progress=None):
    """One backup now of what `target` (deploy.FolderTarget or deploy.SftpTarget) serves. Nothing changes there.
    Returns what was done; BackupError when it cannot be done, and then nothing half-made is kept."""
    log = log or (lambda s: None)
    progress = progress or (lambda d, t, l: None)
    lim = limits(cfg)
    folder = folder_of(project, cfg)
    if os.path.isdir(folder):
        _check_owner(folder, project)               # before anything is written there
    pool = _pool(folder)
    os.makedirs(pool, exist_ok=True)
    with _locked(folder):
        _check_owner(folder, project)
        _mark(folder, project)
        return _take(project, target, log, progress, lim, folder, pool, shared_by_torrent=bool(cfg.get("torrent")))


def _take(project, target, log, progress, lim, folder, pool, shared_by_torrent=False):
    """shared_by_torrent: the project's releases are shared by torrent (torrent = yes), so the release's torrent and
    item are backed up with it; for any other project they are never read."""
    _clean(folder)
    try:
        raw = target.get_file(MANIFEST_NAME)
        prev_raw = target.get_file(PREVIOUS_NAME)
        htaccess = target.get_file(HTACCESS)
        shared = {n: target.get_file(n) for n in deploy.TORRENT_FILES} if shared_by_torrent else {}
    except deploy.DeployError as e:
        raise BackupError(str(e), "read-failed")
    if raw is None:
        raise BackupError("nothing is released at %s yet: there is nothing to back up" % target, "nothing-released")
    try:
        man = Manifest.from_json(raw)
    except ManifestError:
        raise BackupError("the manifest at %s is damaged: there is no release there to back up" % target, "bad-manifest")
    prev = None
    if prev_raw is not None:
        try:
            prev = Manifest.from_json(prev_raw)
        except ManifestError:
            log("the previous manifest at %s cannot be read: the backup leaves it out" % target)
            prev_raw = None
    before = list_backups(folder)
    result = {"folder": folder, "from": str(target), "release": {"name": man.name, "version": man.version}}
    if before and before[0].raw(MANIFEST_NAME) == raw:
        last = before[0]
        want = dict(man.blobs())
        if prev is not None and prev_raw == last.raw(PREVIOUS_NAME):
            want.update(prev.blobs())
        need = sorted(sha for sha, size in want.items() if not _have(pool, sha, size))
        total = sum(want[s] for s in need)
        if need:                            # the pool lost some of its blobs: read them again
            log("the backup of %s lacks %d blobs in its pool (%s): reading them again from %s" %
                (last.name, len(need), human(total), target))
            _fetch(target, need, want, folder, pool, progress, "the backup of %s was not topped up" % last.name)
            log("the backup of %s is whole again" % last.name)
        else:
            log("the live release is the one the backup of %s holds: no new backup" % last.name)
        want_parts = set(man.parts())
        if prev is not None and prev_raw == last.raw(PREVIOUS_NAME):
            want_parts.update(prev.parts())
        _fetch_parts(target, sorted(s for s in want_parts if not _have_part(folder, s)), folder, log)
        result.update({"made": False, "name": last.name, "fetched": len(need), "fetched_bytes": total})
        result["pruned"] = _apply_limits(folder, lim, log)
        return result

    name = time.strftime("%Y-%m-%d", time.gmtime(now()))
    want = dict(prev.blobs()) if prev is not None else {}
    want.update(man.blobs())
    need = sorted(sha for sha, size in want.items() if not _have(pool, sha, size))
    total = sum(want[s] for s in need)
    log("%d of %d blobs to read from %s (%s)" % (len(need), len(want), target, human(total)))
    added = _fetch(target, need, want, folder, pool, progress, "no backup was made")
    parts_added = []
    try:
        want_parts = set(man.parts())
        want_parts.update(prev.parts() if prev is not None else {})
        parts_added = _fetch_parts(target, sorted(s for s in want_parts if not _have_part(folder, s)), folder, log)
        sigs = {}
        for data in (raw, prev_raw):
            if data is not None:
                sname = sig_name(hashing.sha256_bytes(data))
                try:
                    sig = target.get_file(SIG_DIR + "/" + sname)
                except deploy.DeployError as e:
                    raise BackupError(str(e), "read-failed")
                if sig is not None:
                    sigs[sname] = sig
        info = {"format": FORMAT, "version": VERSION, "project_id": project_id(project), "made": _stamp(now()),
                "from": str(target)}
        replaced = os.path.isdir(os.path.join(folder, name))
        _write_backup(folder, name, raw, prev_raw, htaccess, sigs, info, _torrent_files(raw, shared))
    except BaseException:
        _remove(pool, added)                # nothing half-made is kept: blobs only this backup brought go too
        _remove(_parts_pool(folder), parts_added)
        raise
    log("backed up %s %s from %s as %s%s: %d new blobs (%s)" % (man.name or "(unnamed)", man.version, target, name,
                                                                " (it replaces that day's)" if replaced else "",
                                                                len(need), human(total)))
    result.update({"made": True, "name": name, "replaced": replaced, "fetched": len(need), "fetched_bytes": total})
    result["pruned"] = _apply_limits(folder, lim, log, made=name)
    return result


def _have(pool, sha, size):
    p = os.path.join(pool, sha)
    return os.path.isfile(p) and os.path.getsize(p) == size


def _have_part(folder, sha):
    """The parts index `sha` is in the folder's parts pool, whole: its SHA-256 is checked (indexes are small, and no
    manifest gives their size). A damaged one leaves the pool, so the backup reads it again."""
    p = os.path.join(_parts_pool(folder), sha)
    try:
        if hashing.sha256_file(p)[0] == sha:
            return True
    except OSError:
        return False
    _remove(_parts_pool(folder), [sha])
    return False


def _fetch_parts(target, need, folder, log):
    """Read the parts indexes `need` from `target` into the parts pool, each checked against its SHA-256 as it arrives.
    One the destination lacks, or that is damaged, is left out and said: an index is never needed (players then
    download its file whole when it changes). Returns the ones added; BackupError when the destination cannot be read,
    and then none of them stays."""
    if not need:
        return []
    pool = _parts_pool(folder)
    os.makedirs(pool, exist_ok=True)
    incoming = os.path.join(folder, ".incoming-parts-%d" % os.getpid())
    os.makedirs(incoming)
    added = []
    try:
        try:
            target.get_parts(need, incoming, lambda sha: None)
        except deploy.DeployError as e:
            raise BackupError("the parts indexes could not be read from %s: %s" % (target, e), "read-failed")
        for sha in need:
            got = os.path.join(incoming, sha)
            try:
                good = hashing.sha256_file(got)[0] == sha
            except OSError:
                good = None
            if not good:
                log("the parts index %s at %s is %s: the backup leaves it out" %
                    (sha[:12], target, "missing" if good is None else "damaged"))
                continue
            _rename(got, os.path.join(pool, sha))
            added.append(sha)
    except BaseException:
        _remove(pool, added)
        raise
    finally:
        shutil.rmtree(incoming, ignore_errors=True)
    return added


def _remove(pool, shas):
    for sha in shas:
        try:
            os.remove(os.path.join(pool, sha))
        except OSError:
            pass


def _fetch(target, need, want, folder, pool, progress, unless):
    """Read the blobs `need` from `target` into the pool, each checked against its SHA-256 as it arrives.
    Returns the ones added. BackupError when one cannot be read or is damaged (`unless` says what was then
    not done), and then none of them stays."""
    if not need:
        return []
    total = sum(want[s] for s in need)
    incoming = os.path.join(folder, ".incoming-%d" % os.getpid())
    os.makedirs(incoming)
    added = []
    done = [0]

    def tick(sha):
        done[0] += want[sha]
        progress(done[0], total, "downloading")

    try:
        progress(0, total, "downloading")
        try:
            target.get_blobs(need, want, incoming, tick)
        except deploy.DeployError as e:
            raise BackupError("the blobs could not be read from %s: %s" % (target, e), "read-failed")
        for sha in need:
            got = os.path.join(incoming, sha)
            if not os.path.isfile(got) or hashing.sha256_file(got) != (sha, want[sha]):
                raise BackupError("the blob %s at %s is damaged (its content is not what its name says): %s"
                                  % (sha[:12], target, unless), "damaged")
            _rename(got, os.path.join(pool, sha))
            added.append(sha)
    except BaseException:
        _remove(pool, added)
        raise
    finally:
        shutil.rmtree(incoming, ignore_errors=True)
    return added


def _torrent_files(raw, shared):
    """The release's torrent and item as the destination has them, when they are the backed-up release's (its
    item names this manifest): a release going up at that moment has newer ones, and they are left out."""
    from .torrent import bencode
    tor, it = shared.get("release.torrent"), shared.get("release.item")
    if not tor or not it or len(it) > 4096:             # an item is a few hundred bytes: anything longer is none
        return {}
    try:
        v = bencode.decode(it)[b"v"]
        if v[b"m"].decode("ascii") != hashing.sha256_bytes(raw):
            return {}
    except (bencode.BencodeError, ValueError, KeyError, TypeError, AttributeError, UnicodeDecodeError):
        return {}
    return {"release.torrent": tor, "release.item": it}


def _write_backup(folder, name, raw, prev_raw, htaccess, sigs, info, extras=None):
    """The dated folder, written beside and then put in place. A backup the same day replaces that day's: the
    old folder steps aside, the new one takes its place, and when that fails the old one goes back (else the
    next run puts it back). extras: {name: bytes} beside the manifest (release.torrent, release.item)."""
    tmp = os.path.join(folder, ".new-%s-%d" % (name, os.getpid()))
    os.makedirs(tmp)
    try:
        with open(os.path.join(tmp, MANIFEST_NAME), "wb") as f:
            f.write(raw)
        if prev_raw is not None:
            with open(os.path.join(tmp, PREVIOUS_NAME), "wb") as f:
                f.write(prev_raw)
        if htaccess is not None:
            with open(os.path.join(tmp, HTACCESS), "wb") as f:
                f.write(htaccess)
        for n, data in (extras or {}).items():
            with open(os.path.join(tmp, n), "wb") as f:
                f.write(data)
        if sigs:
            os.makedirs(os.path.join(tmp, SIG_DIR))
            for sname, data in sigs.items():
                with open(os.path.join(tmp, SIG_DIR, sname), "wb") as f:
                    f.write(data)
        with open(os.path.join(tmp, INFO_NAME), "w", encoding="utf-8", newline="\n") as f:
            json.dump(info, f, indent=1, sort_keys=True)
        final = os.path.join(folder, name)
        if os.path.isdir(final):
            old = os.path.join(folder, ".old-%s-%d" % (name, os.getpid()))
            _rename(final, old)
            try:
                _rename(tmp, final)
            except BaseException:
                try:
                    _rename(old, final)
                except OSError:
                    pass                    # _recover() puts it back on the next run
                raise
            shutil.rmtree(old, ignore_errors=True)
        else:
            _rename(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


# ---- the limits -----------------------------------------------------------------------------------------

def _week(name):
    y, m, d = (int(x) for x in name.split("-"))
    iso = datetime.date(y, m, d).isocalendar()
    return iso[0], iso[1]


def keep_names(names, daily, weekly, monthly):
    """The backups the limits keep, by name (YYYY-MM-DD): the newest of each of the last `daily` days that have
    one, of the last `weekly` weeks that have one, and of the last `monthly` months; the newest always."""
    names = sorted(names, reverse=True)
    kept = set(names[:1])
    for count, period in ((daily, lambda n: n), (weekly, _week), (monthly, lambda n: n[:7])):
        seen = set()
        for n in names:
            key = period(n)
            if key in seen:
                continue
            if len(seen) >= count:
                break
            seen.add(key)
            kept.add(n)
    return kept


def _size_of(backups):
    blobs = {}
    parts = set()
    for b in backups:
        blobs.update(b.blobs())
        parts.update(b.parts())
    size = sum(blobs.values()) + sum(b.own_bytes() for b in backups)
    if parts:
        pool = _parts_pool(os.path.dirname(backups[0].path))
        for sha in parts:
            try:
                size += os.path.getsize(os.path.join(pool, sha))
            except OSError:
                pass
    return size


def apply_limits(project, cfg, log=None):
    """Keep what the limits keep, remove the other backups, then the blobs and parts indexes no backup names.
    Returns what went."""
    log = log or (lambda s: None)
    lim = limits(cfg)
    folder = folder_of(project, cfg)
    if not os.path.isdir(folder):
        return {"removed": [], "kept": [], "blobs_removed": 0, "bytes": 0, "over": False}
    with _locked(folder):
        _recover(folder)
        _check_owner(folder, project)
        return _apply_limits(folder, lim, log)


def _apply_limits(folder, lim, log, made=None):
    """The limits, applied; `made` is the backup just made, which always stays. Nothing is removed while a
    dated folder cannot be read (the blobs it names are not known), and no backup is removed by date when
    `made` is dated before another backup."""
    backups, bad = scan(folder)
    out = {"removed": [], "kept": [b.name for b in backups], "blobs_removed": 0, "bytes": 0, "over": False}
    if bad:
        for n, why in bad:
            log("the backup %s cannot be read: %s" % (n, why))
        log("no backup or blob was removed this time: one that cannot be read may need them")
        out["unreadable"] = [{"name": n, "why": why} for n, why in bad]
        out["bytes"] = _size_of(backups)
        return out
    if not backups:
        return out
    names = [b.name for b in backups]
    later = [n for n in names if made is not None and n > made]
    if later:
        log("the backup just made, %s, is dated before %s: this computer's clock is behind, or it was ahead when "
            "%s made. No backup is removed by date while that is so" % (made, ", ".join(sorted(later)),
                                                                         "that one was" if len(later) == 1 else
                                                                         "those were"))
        out["dated_after"] = sorted(later)
        kept = set(names)
    else:
        kept = keep_names(names, lim["daily"], lim["weekly"], lim["monthly"])
        if made is not None:
            kept.add(made)
    remaining = [b for b in backups if b.name in kept]
    size = _size_of(remaining)
    over = False
    if lim["max_size"] is not None and not later:
        while len(remaining) > 1 and size > lim["max_size"]:
            remaining.pop()                                       # the oldest goes first; the newest is `made`
            size = _size_of(remaining)
        if size > lim["max_size"]:
            over = True
            log("backup_max_size (%s) is smaller than the newest backup alone (%s): it is kept; raise the limit" %
                (human(lim["max_size"]), human(size)))
    removed = [b for b in backups if b not in remaining]
    for b in removed:
        shutil.rmtree(b.path, ignore_errors=True)
    if removed:
        log("removed %d backups the limits do not keep: %s" % (len(removed), ", ".join(b.name for b in removed)))
    named = set()
    named_parts = set()
    for b in remaining:
        named.update(b.blobs())
        named_parts.update(b.parts())
    gone = 0
    pool = _pool(folder)
    if os.path.isdir(pool):
        for n in os.listdir(pool):
            if (HEX64.match(n) and n not in named) or n.endswith(".part"):
                try:
                    os.remove(os.path.join(pool, n))
                    gone += 1 if not n.endswith(".part") else 0
                except OSError:
                    pass
    parts_pool = _parts_pool(folder)
    if os.path.isdir(parts_pool):
        for n in os.listdir(parts_pool):
            if n not in named_parts:
                try:
                    os.remove(os.path.join(parts_pool, n))
                except OSError:
                    pass
    out.update({"removed": [b.name for b in removed], "kept": [b.name for b in remaining], "blobs_removed": gone,
                "bytes": size, "over": over})
    return out


# ---- restoring one --------------------------------------------------------------------------------------

def _pick(backups, bad, name):
    for b in backups:
        if b.name == name:
            return b
    for n, why in bad:
        if n == name:
            raise BackupError("the backup %s cannot be read: %s" % (name, why), "unreadable")
    raise BackupError("there is no backup %s: %s" % (name, ", ".join(b.name for b in backups) or "there are none yet"),
                      "no-such-backup")


def find(project, cfg, name):
    """The backup `name` of a project; BackupError when there is none, or it cannot be read."""
    backups, bad = scan(folder_of(project, cfg))
    return _pick(backups, bad, name)


def _check_blobs(b, pool, progress):
    """Every blob the backup's release names is in the pool and whole: each one is hashed. A damaged one leaves
    the pool, so the next backup reads it again while the live release has it. BackupError when one is
    missing, damaged or cannot be read."""
    want = b.manifest.blobs()
    total = sum(want.values())
    done = 0
    missing, damaged, unread = [], [], []
    progress(0, total, "checking")
    for sha in sorted(want):
        p = os.path.join(pool, sha)
        if not _have(pool, sha, want[sha]):
            missing.append(sha)
        else:
            try:
                if hashing.sha256_file(p) != (sha, want[sha]):
                    damaged.append(sha)
            except OSError:
                unread.append(sha)
        done += want[sha]
        progress(done, total, "checking")
    _remove(pool, damaged)
    if missing or damaged or unread:
        parts = (["%d missing" % len(missing)] if missing else []) + \
                (["%d damaged (taken out of the pool: the next backup reads them again while the live release "
                  "has them)" % len(damaged)] if damaged else []) + \
                (["%d that could not be read" % len(unread)] if unread else [])
        raise BackupError("the backup %s cannot be put back: of the %d blobs of its release in %s, %s" %
                          (b.name, len(want), pool, "; ".join(parts)), "damaged" if damaged else "incomplete")


def restore(project, cfg, name, target, dry_run=False, log=None, progress=None, cancel=None):
    """Put the backup `name` back at `target`, as a release goes: every blob of it checked against its SHA-256
    first (with dry_run too), then the blobs and parts indexes the destination lacks, the manifest last. Nothing at
    the destination is removed, except a file of the same name and the wrong size, which is replaced. `cancel` (a
    threading.Event) stops it before the manifest goes up. Returns the deploy.DeployResult."""
    progress = progress or (lambda d, t, l: None)
    folder = folder_of(project, cfg)
    if not os.path.isdir(folder):
        raise BackupError("there is no backup %s: there are none yet" % name, "no-such-backup")
    pool = _pool(folder)
    with _locked(folder):
        _recover(folder)
        _check_owner(folder, project)
        backups, bad = scan(folder)
        b = _pick(backups, bad, name)
        _check_blobs(b, pool, progress)
        try:
            return deploy.deploy(b.path, target, prune=False, dry_run=dry_run, progress=progress, log=log, blob_dir=pool,
                                 cancel=cancel, parts_dir=_parts_pool(folder))
        except deploy.DeployError as e:
            raise BackupError(str(e), "send-failed")


# ---- the operating system's scheduler ------------------------------------------------------------------

def last_scheduled(folder):
    """The system scheduler's last run of a project's backups, as record_scheduled() noted it; None when none
    was noted."""
    return _read_json(os.path.join(folder, SCHEDULED_NAME))


def record_scheduled(project, cfg, how, ok, made=False, name="", code="", message=""):
    """Note a scheduled run in the project's backup folder: when, and what it did or why it failed (backup
    list and the app show it). A run that cannot be noted is not a failure of its own."""
    try:
        folder = folder_of(project, cfg)
        if os.path.isdir(folder):
            _check_owner(folder, project)
        os.makedirs(folder, exist_ok=True)
        _mark(folder, project)
        _write_json(os.path.join(folder, SCHEDULED_NAME),
                    {"time": _stamp(now()), "schedule": how, "ok": bool(ok), "made": bool(made), "name": name,
                     "code": code, "message": message})
    except (BackupError, OSError):
        pass


def scheduled_due(project, cfg, how):
    """(due, why not): a scheduled run backs up only when neither the newest backup nor the scheduler's last
    run that worked is younger than a day (daily) or an hour (hourly). A time still to come (a clock put
    back) does not count."""
    folder = folder_of(project, cfg)
    at = now()
    last = last_scheduled(folder) or {}
    times = [t for t in (_seconds(newest(project, cfg)), _seconds(last.get("time")) if last.get("ok") else None)
             if t is not None and 0 <= at - t]
    if times and at - max(times) < EVERY[how] - SLACK:
        return False, ("backed up or checked at %s: the next scheduled backup is due %s after that" %
                       (_stamp(max(times)).replace("T", " ").replace("Z", " UTC"), "a day" if how == "daily" else "an hour"))
    return True, ""


def _console_encoding():
    """The code page Windows' console programs write in when their output is not a console: the OEM one."""
    try:
        import ctypes
        name = "cp%d" % ctypes.windll.kernel32.GetOEMCP()
        codecs.lookup(name)
        return name
    except (AttributeError, OSError, LookupError, ValueError):
        return "utf-8"


def run_tool(argv, stdin_text=None):
    """Run the system's scheduler tool (schtasks, crontab): the one place the system is touched, which a test
    stands in for. Returns a CompletedProcess with text: schtasks' read in the console's code page, what does
    not fit replaced; a crontab byte for byte (what is not text in it goes back as it came). BackupError when
    the tool cannot be run or does not answer within a minute."""
    if argv[0] == "schtasks":
        encoding, errors = _console_encoding(), "replace"
    else:
        encoding, errors = locale.getpreferredencoding(False) or "utf-8", "surrogateescape"
    feed = {"stdin": subprocess.DEVNULL} if stdin_text is None else {"input": stdin_text.encode(encoding, errors)}
    try:
        r = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
                           **feed)
    except subprocess.TimeoutExpired:
        raise BackupError("%s did not answer within a minute" % argv[0], "schedule-failed")
    except OSError as e:
        raise BackupError("%s could not be run: %s" % (argv[0], e.strerror or "it is not on this system"),
                          "schedule-failed")
    return subprocess.CompletedProcess(argv, r.returncode, (r.stdout or b"").decode(encoding, errors),
                                       (r.stderr or b"").decode(encoding, errors))


def program():
    """The command that runs this DeltaPatcher: the built program, else the repository's cli script, else
    this Python with -m deltapatcher."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(repo, "cli", "deltapatcher.bat" if os.name == "nt" else "deltapatcher.sh")
    if os.path.isfile(script):
        return [script]
    return [sys.executable, "-m", "deltapatcher"]


def _check_command(command, windows):
    """A path the scheduler cannot carry safely is refused: a line break or another control character (it
    would end the crontab line, or the task's), and on Windows a %, which Task Scheduler reads as the start of
    an environment variable."""
    for part in command:
        if _CONTROL.search(part):
            raise BackupError("%r has a line break or another control character in it: the system's scheduler cannot "
                              "run it safely; move it to a folder whose name has none" % part, "bad-path")
        if windows and "%" in part:
            raise BackupError("%s has a %% in it, which Task Scheduler reads as the start of a variable: move it to a "
                              "folder whose name has none" % part, "bad-path")


def schedule(project, how, platform=None):
    """Have the operating system back the project up daily or hourly, or stop it (off: only DeltaPatcher's own
    entry for this project goes). Either way the scheduler starts a run every hour, with --project naming the
    project's settings file (Default's too); the run itself decides whether a backup is due. Returns what is
    in place."""
    if how not in ("daily", "hourly", "off"):
        raise BackupError("schedule daily, hourly or off", "bad-arguments")
    pid = project_id(project)
    windows = (platform or sys.platform) == "win32"
    command = program() + ["--unattended", "--project", project.path, "backup", "now", "--scheduled", how]
    if how != "off":
        _check_command(command, windows)
    minute = int(pid[:2], 16) % 60          # projects do not all start at the same minute
    if windows:
        return _schtasks(pid, project.path, how, command, minute)
    return _crontab(pid, how, command, minute)


def unschedule(project, platform=None):
    """A project that goes away: its entry in the system's scheduler goes too. Returns (how many went, what
    went wrong: '' when nothing did)."""
    try:
        return schedule(project, "off", platform)["removed"], ""
    except BackupError as e:
        return 0, str(e)


def _not_found(r):
    """schtasks failed because there is no such task: it says the system's own words for "the file was not
    found" (in the system's language, or in English)."""
    texts = ["The system cannot find the file specified."]
    try:
        import ctypes
        texts.append(ctypes.FormatError(2))
    except (AttributeError, OSError, ValueError):
        pass
    said = " ".join(("%s %s" % (r.stderr or "", r.stdout or "")).split()).lower()
    return r.returncode != 0 and any(" ".join(t.split()).strip(" .").lower() in said for t in texts if t.strip())


def _said(r):
    return " ".join((r.stderr or r.stdout or "").split()) or "exit %d" % r.returncode


def _schtasks(pid, settings_file, how, command, minute):
    name = "%s %s" % (TASK, pid)
    q = run_tool(["schtasks", "/Query", "/TN", name])
    if q.returncode != 0 and not _not_found(q):
        raise BackupError("schtasks could not look for the task %s: %s" % (name, _said(q)), "schedule-failed")
    there = q.returncode == 0
    if how == "off":
        if there:
            d = run_tool(["schtasks", "/Delete", "/TN", name, "/F"])
            if d.returncode != 0 and not _not_found(d):
                raise BackupError("schtasks could not remove the task %s: %s" % (name, _said(d)), "schedule-failed")
        return {"schedule": "off", "task": name, "removed": 1 if there else 0}
    fd, path = tempfile.mkstemp(prefix="dp-task-", suffix=".xml")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(task_xml(command, minute, settings_file, _user_sid()).encode("utf-16"))
        c = run_tool(["schtasks", "/Create", "/TN", name, "/XML", path, "/F"])
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if c.returncode != 0:
        raise BackupError("schtasks refused the task %s: %s" % (name, _said(c)), "schedule-failed")
    return {"schedule": how, "task": name, "command": subprocess.list2cmdline(command), "replaced": there,
            "removed": 0}


def task_xml(command, minute, settings_file, user=""):
    """Task Scheduler's definition of a project's task: every hour from <minute> past midnight, as the user who
    made it, only while that user is logged on (no password is kept anywhere), started through conhost
    --headless so no console window opens. A computer on battery runs it too; a start that was missed runs
    when the computer can; one that is still going makes the next wait; and a run is ended after
    TASK_TIME_LIMIT."""
    conhost = ntpath.join(os.environ.get("SystemRoot") or "C:\\Windows", "System32", "conhost.exe")
    start = "%sT00:%02d:00" % (time.strftime("%Y-%m-%d"), minute)
    principal = ("      <UserId>%s</UserId>\n" % escape(user)) if user else ""
    return """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>%(description)s</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <Repetition>
        <Interval>PT1H</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>%(start)s</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
%(principal)s      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>%(limit)s</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>%(command)s</Command>
      <Arguments>%(arguments)s</Arguments>
    </Exec>
  </Actions>
</Task>
""" % {"description": escape("DeltaPatcher backs up the live release of the project %s when a backup is due. "
                             "Stop it with: deltapatcher --project \"%s\" backup schedule off"
                             % (settings_file, settings_file)),
       "start": start, "principal": principal, "limit": TASK_TIME_LIMIT, "command": escape(conhost),
       "arguments": escape("--headless " + subprocess.list2cmdline(command))}


def _user_sid():
    """The SID of the account this runs as ("S-1-5-21-..."), for the task's principal; '' when it is not
    known (the task is then the account's that makes it)."""
    if os.name != "nt":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                 ctypes.POINTER(wintypes.DWORD)]
        advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):   # TOKEN_QUERY
            return ""
        try:
            size = wintypes.DWORD()
            advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))                     # TokenUser
            buf = ctypes.create_string_buffer(size.value)
            if not size.value or not advapi32.GetTokenInformation(token, 1, buf, size, ctypes.byref(size)):
                return ""
            sid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]                             # TOKEN_USER.User.Sid
            text = wintypes.LPWSTR()
            if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                return ""
            try:
                return text.value or ""
            finally:
                kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        finally:
            kernel32.CloseHandle(token)
    except (AttributeError, OSError, ValueError):
        return ""


def _cron_lines(text):
    """A crontab's lines, split at its line ends only (a carriage return or another character inside a line
    stays in it)."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _crontab(pid, how, command, minute):
    r = run_tool(["crontab", "-l"])
    if r.returncode == 0:
        lines = _cron_lines(r.stdout or "")
    elif "no crontab" in (r.stderr or "").lower():
        lines = []
    else:                                   # never write a crontab that could not be read whole
        raise BackupError("crontab -l failed: %s" % _said(r), "schedule-failed")
    mark = CRON_MARK + pid
    keep = [l for l in lines if not l.rstrip().endswith(mark)]
    removed = len(lines) - len(keep)
    entry = None
    if how != "off":
        quoted = " ".join(shlex.quote(c) for c in command).replace("%", "\\%")
        entry = "%d * * * * %s %s" % (minute, quoted, mark)
        keep.append(entry)
    elif not removed:
        return {"schedule": "off", "entry": None, "removed": 0}      # nothing of ours there: nothing is written
    w = run_tool(["crontab", "-"], "\n".join(keep) + "\n" if keep else "")
    if w.returncode != 0:
        raise BackupError("crontab refused the change: %s" % _said(w), "schedule-failed")
    if how == "off":
        return {"schedule": "off", "entry": None, "removed": removed}
    return {"schedule": how, "entry": entry, "command": " ".join(shlex.quote(c) for c in command),
            "replaced": bool(removed), "removed": 0}


def agent_warning(cfg):
    """Linux and macOS: why every scheduled backup would fail because cron cannot reach ssh-agent ('' when it
    would not): the project's SSH key has a passphrase."""
    if (cfg.get("deploy_mode") or "sftp") != "sftp":
        return ""
    key = str(cfg.get("ssh_key") or "").strip()
    key = os.path.abspath(os.path.expanduser(key)) if key else ""
    if not key or not os.path.isfile(key):
        return ""
    try:
        if not ssh.key_has_passphrase(key):
            return ""
    except ssh.SshError:
        return ""
    return ("the SSH key %s has a passphrase, and cron cannot reach ssh-agent: every scheduled backup would stop at "
            "the locked key. Let the app back up while it runs instead (backup_every_hours), or give the backups a "
            "key without a passphrase" % key)
