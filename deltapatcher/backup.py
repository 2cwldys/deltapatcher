"""Backups of a project's live release: what players download now, read from where it is deployed (the
project's server over SFTP, or its deploy folder) and kept on this computer. A backup only reads at the
destination; a restore is a release of the backup, its blobs first and its manifest last, that removes
nothing there.

    <backup folder>/blobs/<sha256>     the pool: each blob once, however many backups name it, checked
                                       against its SHA-256 as it arrives
    <backup folder>/<YYYY-MM-DD>/      one backup a day (UTC): manifest.json, manifest.previous.json when the
                                       destination has one, sigs/<sha256>.sig, backup.json

The backup folder is backup_dir, else backups/<the project's file name> beside the main settings file. A
backup of a release unchanged since the last backup makes no new one, and a second backup the same day
replaces that day's. After each backup the limits are applied (limits(), apply_limits()), and blobs no backup
names any more leave the pool.

schedule() has the operating system run `deltapatcher-cli --close backup` daily or hourly (Windows: Task
Scheduler through schtasks; elsewhere the user's crontab), for a computer that does not keep the app open.
"""

import contextlib
import csv
import datetime
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

from . import client, config, deploy, hashing
from .manifest import BLOB_DIR, HEX64, MANIFEST_NAME, PREVIOUS_NAME, Manifest, ManifestError
from .publish import human
from .signing import SIG_DIR, sig_name

FORMAT = "deltapatcher-backup"
VERSION = 1
INFO_NAME = "backup.json"
NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TASK = "DeltaPatcher backup"          # Windows: every task this makes starts with it
CRON_MARK = "# deltapatcher-backup "  # the user's crontab: every line this makes ends with it and the project's id
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(bytes|byte|b|kb|mb|gb|tb)\s*$", re.I)
_UNITS = {"b": 1, "byte": 1, "bytes": 1, "kb": 1 << 10, "mb": 1 << 20, "gb": 1 << 30, "tb": 1 << 40}


class BackupError(Exception):
    """A backup, restore or schedule that cannot be done; `code` says why, for the app."""

    def __init__(self, message, code="failed", data=None):
        Exception.__init__(self, message)
        self.code = code
        self.data = data or {}


def now():
    """The time now, in seconds since the epoch (a test stands in for it)."""
    return time.time()


def folder_of(project, cfg):
    """The project's backup folder: backup_dir, else backups/<its file's name> beside the main settings file.
    The name of a project's file kept elsewhere gets a short hash of its path, so two files of one name stay
    apart."""
    given = str(cfg.get("backup_dir") or "").strip()
    if given:
        return os.path.abspath(os.path.expanduser(given))
    name = os.path.splitext(os.path.basename(project.path))[0]
    if project.kind not in ("main", "folder"):
        name += "-" + project_id(project)
    return os.path.join(os.path.dirname(config.path()), "backups", name)


def project_id(project):
    """A short id of the project's settings file, the same as long as the file stays where it is."""
    return hashlib.sha256(os.path.normcase(os.path.abspath(project.path)).encode("utf-8")).hexdigest()[:8]


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


# ---- the backups in a folder ------------------------------------------------------------------------

class Backup(object):
    """One dated backup: its manifests and signatures are in its folder, its blobs in the pool."""

    def __init__(self, folder, name):
        self.name = name
        self.path = os.path.join(folder, name)
        try:
            with open(os.path.join(self.path, INFO_NAME), "r", encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            info = {}
        self.info = info if isinstance(info, dict) else {}
        self.manifest = self._manifest(MANIFEST_NAME)
        self.previous = self._manifest(PREVIOUS_NAME)

    def _manifest(self, name):
        try:
            with open(os.path.join(self.path, name), "rb") as f:
                return Manifest.from_json(f.read())
        except (OSError, ManifestError):
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


def list_backups(folder):
    """The backups in a folder, the newest first; a folder that is not a readable backup is left out."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    out = [Backup(folder, n) for n in sorted(names, reverse=True) if NAME_RE.match(n) and os.path.isdir(os.path.join(folder, n))]
    return [b for b in out if b.manifest is not None]


def _pool(folder):
    return os.path.join(folder, BLOB_DIR)


def _clean(folder):
    """What a backup that stopped halfway left: unfinished folders, and blobs half downloaded."""
    for n in os.listdir(folder):
        if n.startswith((".new-", ".old-", ".incoming-")):
            shutil.rmtree(os.path.join(folder, n), ignore_errors=True)
    pool = _pool(folder)
    for n in os.listdir(pool):
        if n.endswith(".part"):
            try:
                os.remove(os.path.join(pool, n))
            except OSError:
                pass


def listing(project, cfg):
    """The backups of a project, newest first, with what the folder holds."""
    folder = folder_of(project, cfg)
    backups = list_backups(folder)
    return {"folder": folder, "backups": [b.describe() for b in backups], "bytes": _folder_bytes(folder),
            "limits": limits(cfg)}


def newest(project, cfg):
    """When the newest backup was made ("YYYY-MM-DDTHH:MM:SSZ"), '' when there is none. Only backup.json files are
    read, so a listing of every project stays quick."""
    folder = folder_of(project, cfg)
    try:
        names = sorted((n for n in os.listdir(folder) if NAME_RE.match(n)), reverse=True)
    except OSError:
        return ""
    for n in names:
        try:
            with open(os.path.join(folder, n, INFO_NAME), "r", encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(info, dict) and info.get("made"):
            return str(info["made"])
    return ""


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
    pool = _pool(folder)
    os.makedirs(pool, exist_ok=True)
    with _locked(folder):
        return _take(cfg, target, log, progress, lim, folder, pool)


def _take(cfg, target, log, progress, lim, folder, pool):
    _clean(folder)
    try:
        raw = target.get_file(MANIFEST_NAME)
        prev_raw = target.get_file(PREVIOUS_NAME)
    except deploy.DeployError as e:
        raise BackupError(str(e), "read-failed")
    if raw is None:
        raise BackupError("nothing is released at %s yet: there is nothing to back up" % target, "nothing-released")
    try:
        man = Manifest.from_json(raw)
    except ManifestError as e:
        raise BackupError("the manifest at %s cannot be read: %s" % (target, e), "bad-manifest")
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
        log("the live release is the one the backup of %s holds: no new backup" % before[0].name)
        result.update({"made": False, "name": before[0].name, "fetched": 0, "fetched_bytes": 0})
        result["pruned"] = _apply_limits(folder, lim, log)
        return result

    name = time.strftime("%Y-%m-%d", time.gmtime(now()))
    want = dict(prev.blobs()) if prev is not None else {}
    want.update(man.blobs())
    need = sorted(sha for sha, size in want.items() if not _have(pool, sha, size))
    total = sum(want[s] for s in need)
    log("%d of %d blobs to read from %s (%s)" % (len(need), len(want), target, human(total)))
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
                raise BackupError("the blob %s at %s is damaged (its content is not what its name says): no backup "
                                  "was made" % (sha[:12], target), "damaged")
            os.replace(got, os.path.join(pool, sha))
            added.append(sha)
        sigs = {}
        for data in (raw, prev_raw):
            if data is not None:
                sname = sig_name(hashing.sha256_bytes(data))
                sig = target.get_file(SIG_DIR + "/" + sname)
                if sig is not None:
                    sigs[sname] = sig
        info = {"format": FORMAT, "version": VERSION, "made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now())),
                "from": str(target)}
        _write_backup(folder, name, raw, prev_raw, sigs, info)
    except BaseException:
        for sha in added:                     # nothing half-made is kept: blobs only this backup brought go too
            try:
                os.remove(os.path.join(pool, sha))
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(incoming, ignore_errors=True)
    replaced = bool(before) and before[0].name == name
    log("backed up %s %s from %s as %s%s: %d new blobs (%s)" % (man.name or "(unnamed)", man.version, target, name,
                                                                " (it replaces that day's)" if replaced else "",
                                                                len(need), human(total)))
    result.update({"made": True, "name": name, "replaced": replaced, "fetched": len(need), "fetched_bytes": total})
    result["pruned"] = _apply_limits(folder, lim, log)
    return result


def _have(pool, sha, size):
    p = os.path.join(pool, sha)
    return os.path.isfile(p) and os.path.getsize(p) == size


def _write_backup(folder, name, raw, prev_raw, sigs, info):
    """The dated folder, written beside and then put in place; a backup the same day is replaced."""
    tmp = os.path.join(folder, ".new-%s-%d" % (name, os.getpid()))
    os.makedirs(tmp)
    with open(os.path.join(tmp, MANIFEST_NAME), "wb") as f:
        f.write(raw)
    if prev_raw is not None:
        with open(os.path.join(tmp, PREVIOUS_NAME), "wb") as f:
            f.write(prev_raw)
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
        os.replace(final, old)
        os.replace(tmp, final)
        shutil.rmtree(old, ignore_errors=True)
    else:
        os.replace(tmp, final)


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
    for b in backups:
        blobs.update(b.blobs())
    return sum(blobs.values()) + sum(b.own_bytes() for b in backups)


def apply_limits(project, cfg, log=None):
    """Keep what the limits keep, remove the other backups, then the blobs no backup names. Returns what went."""
    log = log or (lambda s: None)
    lim = limits(cfg)
    folder = folder_of(project, cfg)
    if not os.path.isdir(folder):
        return {"removed": [], "kept": [], "blobs_removed": 0, "bytes": 0, "over": False}
    with _locked(folder):
        return _apply_limits(folder, lim, log)


def _apply_limits(folder, lim, log):
    backups = list_backups(folder)
    if not backups:
        return {"removed": [], "kept": [], "blobs_removed": 0, "bytes": 0, "over": False}
    kept = keep_names([b.name for b in backups], lim["daily"], lim["weekly"], lim["monthly"])
    remaining = [b for b in backups if b.name in kept]
    size = _size_of(remaining)
    over = False
    if lim["max_size"] is not None:
        while len(remaining) > 1 and size > lim["max_size"]:
            remaining.pop()                                       # the oldest goes first
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
    for b in remaining:
        named.update(b.blobs())
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
    return {"removed": [b.name for b in removed], "kept": [b.name for b in remaining], "blobs_removed": gone,
            "bytes": size, "over": over}


# ---- restoring one --------------------------------------------------------------------------------------

def find(project, cfg, name):
    backups = list_backups(folder_of(project, cfg))
    for b in backups:
        if b.name == name:
            return b
    raise BackupError("there is no backup %s: %s" % (name, ", ".join(b.name for b in backups) or "there are none yet"),
                      "no-such-backup")


def restore(project, cfg, name, target, dry_run=False, log=None, progress=None):
    """Put the backup `name` back at `target`, as a release goes: the blobs it lacks first, the manifest last.
    Nothing at the destination is removed. Returns the deploy.DeployResult."""
    b = find(project, cfg, name)
    folder = folder_of(project, cfg)
    pool = _pool(folder)
    with _locked(folder):
        short = [sha for sha, size in b.manifest.blobs().items() if not _have(pool, sha, size)]
        if short:
            raise BackupError("the backup %s lacks %d of its blobs in %s: it cannot be put back" % (name, len(short), pool),
                              "incomplete")
        try:
            return deploy.deploy(b.path, target, prune=False, dry_run=dry_run, progress=progress, log=log, blob_dir=pool)
        except deploy.DeployError as e:
            raise BackupError(str(e), "send-failed")


# ---- the operating system's scheduler ------------------------------------------------------------------

def run_tool(argv, stdin_text=None):
    """Run the system's scheduler tool (schtasks, crontab). The one place the system is touched: a test stands
    in for it."""
    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return subprocess.run(argv, input=stdin_text, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True, timeout=60, creationflags=creation)


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


def schedule(project, how, platform=None):
    """Have the operating system back the project up daily or hourly, or stop it (off: only DeltaPatcher's own
    entry for this project goes). Returns what is in place."""
    if how not in ("daily", "hourly", "off"):
        raise BackupError("schedule daily, hourly or off", "bad-arguments")
    pid = project_id(project)
    command = program() + ["--close"] + ([] if project.main else ["--project", project.path]) + ["backup"]
    minute = int(pid[:2], 16) % 60          # projects do not all start at the same minute
    if (platform or sys.platform) == "win32":
        return _schtasks(pid, project.title, how, command, minute)
    return _crontab(pid, how, command, minute)


def _schtasks(pid, title, how, command, minute):
    r = run_tool(["schtasks", "/Query", "/FO", "CSV", "/NH"])
    names = []
    if r.returncode == 0:
        for row in csv.reader(io.StringIO(r.stdout or "")):
            if row and TASK in row[0] and row[0].rstrip().endswith(" " + pid) and row[0] not in names:
                names.append(row[0])
    for n in names:
        d = run_tool(["schtasks", "/Delete", "/TN", n, "/F"])
        if d.returncode != 0:
            raise BackupError("schtasks could not remove %s: %s" % (n, (d.stderr or d.stdout or "").strip()), "schedule-failed")
    if how == "off":
        return {"schedule": "off", "removed": len(names)}
    name = "%s %s %s" % (TASK, title, pid)
    line = subprocess.list2cmdline(command)
    if len(line) > 261:
        raise BackupError("the command is longer than Task Scheduler takes (261 characters): %s" % line, "too-long")
    when = ["/SC", "DAILY", "/ST", "03:%02d" % minute] if how == "daily" else ["/SC", "HOURLY", "/ST", "00:%02d" % minute]
    c = run_tool(["schtasks", "/Create", "/TN", name, "/TR", line, "/F"] + when)
    if c.returncode != 0:
        raise BackupError("schtasks refused the task: %s" % (c.stderr or c.stdout or "").strip(), "schedule-failed")
    return {"schedule": how, "task": name, "command": line, "removed": len(names)}


def _crontab(pid, how, command, minute):
    try:
        r = run_tool(["crontab", "-l"])
    except OSError as e:
        raise BackupError("this system has no crontab: %s" % e, "schedule-failed")
    if r.returncode == 0:
        lines = (r.stdout or "").splitlines()
    elif "no crontab" in (r.stderr or "").lower():
        lines = []
    else:                                   # never write a crontab that could not be read whole
        raise BackupError("crontab -l failed: %s" % (r.stderr or r.stdout or "").strip(), "schedule-failed")
    mark = CRON_MARK + pid
    keep = [l for l in lines if not l.rstrip().endswith(mark)]
    removed = len(lines) - len(keep)
    entry = None
    if how != "off":
        quoted = " ".join(shlex.quote(c) for c in command).replace("%", "\\%")
        entry = "%d %s * * * %s %s" % (minute, "3" if how == "daily" else "*", quoted, mark)
        keep.append(entry)
    w = run_tool(["crontab", "-"], "\n".join(keep) + "\n" if keep else "")
    if w.returncode != 0:
        raise BackupError("crontab refused the change: %s" % (w.stderr or w.stdout or "").strip(), "schedule-failed")
    return {"schedule": how, "entry": entry, "removed": removed}
