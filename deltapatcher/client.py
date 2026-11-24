"""The client: bring a folder up to the source's manifest, downloading only what changed.

Everything the client keeps lives in <install>/.deltapatcher/:
    state.json        the files this program installed: {path: [size, sha256]}, and the source
    hashes.json       a cache of the folder's hashes (size + modification time)
    cache/<sha256>    downloaded blobs, until they are in place (a cut download resumes from here)
    rollback/         the last completed update: its journal and the files it replaced
    rollback.new/     the update in progress (it becomes rollback/ when it completes)
    lock              held (an operating-system lock) while an update or a rollback runs

Rules:
- A file is downloaded only when its SHA-256 differs from the manifest's; the same content under
  two names is fetched once.
- Every blob is checked against its SHA-256 before anything in the folder changes. A missing or
  damaged blob on the source stops the update with the folder untouched.
- A file the manifest no longer lists is removed only when this program installed it AND it is
  unchanged since. Files that were already there, and files added by hand, are never removed.
- A file matching one of the manifest's `protect` patterns is created when missing and otherwise
  left alone (player settings, for instance).
- The update is journalled. If it fails or is interrupted, it is undone (at once, or by the next
  run); `rollback` undoes the last completed one.
"""

import concurrent.futures
import json
import os
import shutil
import stat
import sys
import threading
import time

from . import hashing, logs, signing
from .manifest import Manifest, ManifestError
from .publish import human
from .source import Source, SourceError

STATE_DIR = ".deltapatcher"
ROLLBACK = "rollback"
ROLLBACK_NEW = "rollback.new"


class UpdateError(Exception):
    pass


def _xp(path):
    """A path Windows accepts past 260 characters."""
    if os.name == "nt":
        path = os.path.abspath(path)
        if len(path) > 240 and not path.startswith("\\\\?\\"):
            return "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path
    return path


def target_path(install, rel):
    """The file for `rel` inside `install`.

    Refuses a path that would land outside the folder (through '..' or a link or junction on the
    way) or inside this program's own .deltapatcher folder. The last part is not resolved, so a
    link there is seen as a link and not followed.
    """
    root = os.path.realpath(install)
    parts = rel.split("/")
    parent = os.path.realpath(os.path.join(root, *parts[:-1])) if len(parts) > 1 else root
    if os.path.commonpath([root, parent]) != root:
        raise UpdateError("%s would land outside the folder" % rel)
    full = os.path.join(parent, parts[-1])
    own = os.path.normcase(os.path.join(root, STATE_DIR))
    if os.path.normcase(full) == own or os.path.normcase(parent + os.sep).startswith(own + os.sep):
        raise UpdateError("%s is inside %s, which belongs to this program" % (rel, STATE_DIR))
    return full


def _writable(path):
    """Clear a read-only flag, so the file can be moved over or removed."""
    try:
        st = os.lstat(path)
        if not st.st_mode & stat.S_IWRITE:
            os.chmod(path, st.st_mode | stat.S_IWRITE)
    except OSError:
        pass


def _rmtree(path):
    """Remove a folder completely, read-only files included; UpdateError if anything is left."""
    if not os.path.lexists(path):
        return

    def again(func, p, _exc):
        _writable(p)
        try:
            func(p)
        except OSError:
            pass

    if sys.version_info >= (3, 12):
        shutil.rmtree(_xp(path), onexc=again)
    else:
        shutil.rmtree(_xp(path), onerror=again)
    if os.path.lexists(path):
        raise UpdateError("cannot clear %s (is a file in it open in another program?)" % path)


class Plan(object):
    def __init__(self):
        self.add = []            # [Entry] missing here
        self.replace = []        # [Entry] different here
        self.remove = []         # [(path, size, sha)] installed by us, no longer listed
        self.kept_changed = []   # paths no longer listed but edited here: left alone
        self.protected = []      # paths that differ but are protected: left alone
        self.same = 0

    def empty(self):
        return not (self.add or self.replace or self.remove)

    def download(self):
        """{sha: size} of the blobs this plan needs."""
        out = {}
        for e in self.add + self.replace:
            out[e.sha256] = e.size
        return out

    def summary(self):
        need = self.download()
        return ("new %d, changed %d, removed %d, unchanged %d%s; to download: %d blobs, %s"
                % (len(self.add), len(self.replace), len(self.remove), self.same,
                   (", protected %d" % len(self.protected)) if self.protected else "",
                   len(need), human(sum(need.values()))))


class State(object):
    def __init__(self, install):
        self.dir = os.path.join(install, STATE_DIR)
        self.path = os.path.join(self.dir, "state.json")
        self.files = {}
        self.source = ""
        self.manifest_sha = ""
        self.version = ""
        self.publisher_key = ""      # "ed25519:..." once a signed release was accepted: later ones must match
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            self.files = {k: v for k, v in doc.get("files", {}).items() if isinstance(v, list) and len(v) == 2}
            self.source = doc.get("source", "")
            self.manifest_sha = doc.get("manifest_sha", "")
            self.version = doc.get("version", "")
            self.publisher_key = doc.get("publisher_key", "") or ""
        except (OSError, ValueError, AttributeError):
            pass

    def doc(self):
        return {"source": self.source, "manifest_sha": self.manifest_sha, "version": self.version,
                "publisher_key": self.publisher_key, "files": self.files}

    def save(self):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.doc(), f, indent=0, sort_keys=True)
        os.replace(tmp, self.path)


class Lock(object):
    """An operating-system lock on <state>/lock. The system releases it when the process ends, however
    it ends, so a crash never leaves the folder locked."""

    def __init__(self, state_dir):
        self.path = os.path.join(state_dir, "lock")
        self.fh = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fh = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            self.fh = None
            raise UpdateError("another update or rollback is running in this folder")
        return self

    def __exit__(self, *a):
        if self.fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self.fh.close()
        self.fh = None


def make_plan(man, install, state, cache):
    plan = Plan()
    listed = {p.lower(): p for p in man.files}
    for path in sorted(man.files):
        e = man.files[path]
        x = _xp(target_path(install, path))
        if os.path.islink(x):
            raise UpdateError("%s is a link here; the release has a file there" % path)
        if not os.path.lexists(x):
            plan.add.append(e)
            continue
        if os.path.isdir(x):
            raise UpdateError("%s is a folder here; the release has a file there" % path)
        if cache.hash(path, x) == e.sha256:
            plan.same += 1
        elif man.is_protected(path):
            plan.protected.append(path)
        else:
            plan.replace.append(e)
    for path, (size, sha) in sorted(state.files.items()):
        if path in man.files:
            continue
        try:
            x = _xp(target_path(install, path))
        except UpdateError:
            continue
        if os.path.islink(x) or not os.path.isfile(x):
            continue
        other = listed.get(path.lower())
        if other is not None:
            # the release still lists it, with other letter case: on Windows that is the same file
            try:
                if os.path.samefile(x, _xp(target_path(install, other))):
                    continue
            except (OSError, UpdateError):
                pass
        if cache.hash(path, x) == sha:
            plan.remove.append((path, size, sha))
        else:
            plan.kept_changed.append(path)
    return plan


def _rb(state_dir, name=ROLLBACK):
    rb = os.path.join(state_dir, name)
    return rb, os.path.join(rb, "journal.jsonl"), os.path.join(rb, "files"), os.path.join(rb, "state.prev.json")


def _journal_write(fh, rec):
    fh.write(json.dumps(rec) + "\n")
    fh.flush()
    os.fsync(fh.fileno())


def _read_journal(journal):
    """(header, ops, done), or None when there is no journal. A torn last line (power loss) is
    skipped, not fatal."""
    try:
        with open(journal, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    header, ops, done = {}, [], False
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("done") is True:
            done = True
        elif rec.get("op") in ("add", "replace", "remove") and isinstance(rec.get("path"), str):
            ops.append(rec)
        elif "started" in rec and not header:
            header = rec
    return header, ops, done


def _undo(install, state_dir, name, log):
    """Undo the journal in <state>/<name>: newest operation first. Returns how many were undone."""
    rb, journal, files, prev_state = _rb(state_dir, name)
    got = _read_journal(journal)
    if got is None:
        _rmtree(rb)
        raise UpdateError("there is no update to roll back")
    header, ops, _done = got
    n = 0
    for op in reversed(ops):
        path = op["path"]
        try:
            x = _xp(target_path(install, path))
        except UpdateError:
            continue
        saved = _xp(os.path.join(files, *path.split("/")))
        if op["op"] == "add":
            if os.path.isfile(x) and not os.path.islink(x):
                _writable(x)
                os.remove(x)
        elif os.path.isfile(saved):
            os.makedirs(os.path.dirname(x), exist_ok=True)
            if os.path.lexists(x):
                _writable(x)
            os.replace(saved, x)
        n += 1
    state_json = os.path.join(state_dir, "state.json")
    if os.path.isfile(prev_state):
        os.replace(prev_state, state_json)
    elif header.get("had_state") is False and os.path.isfile(state_json):
        os.remove(state_json)
    _rmtree(rb)
    log("rolled back %d changes" % n)
    return n


def _recover(install, state_dir, log):
    """Finish or undo an update that stopped: a completed one becomes the rollback, a half one is undone."""
    rbn, journal, _f, _p = _rb(state_dir, ROLLBACK_NEW)
    if not os.path.lexists(rbn):
        return
    got = _read_journal(journal)
    if got is None:
        _rmtree(rbn)                     # stopped before it changed anything
    elif got[2]:
        _rmtree(_rb(state_dir)[0])       # completed: only the swap was left
        os.replace(rbn, _rb(state_dir)[0])
    else:
        log("the last update stopped halfway: undoing it first")
        _undo(install, state_dir, ROLLBACK_NEW, log)


def rollback(install, log=None):
    """Undo the last completed update."""
    log = log or (lambda s: None)
    state_dir = os.path.join(install, STATE_DIR)
    with Lock(state_dir):
        _recover(install, state_dir, log)
        n = _undo(install, state_dir, ROLLBACK, log)
        logs.folder_record(state_dir, ["rolled back the last update (%d changes)" % n])
        return n


def _prune_empty_dirs(install, paths):
    root = os.path.realpath(install)
    dirs = set()
    for x in paths:
        try:
            dirs.add(os.path.dirname(target_path(install, x)))
        except UpdateError:
            pass
    for p in sorted(dirs, key=len, reverse=True):
        while os.path.normcase(os.path.realpath(p)) != os.path.normcase(root):
            try:
                os.rmdir(_xp(p))
            except OSError:
                break
            p = os.path.dirname(p)


def check_signature(src, raw, state, trust_key, check_only, log):
    """Who signed this manifest, and is that the publisher this folder trusts? Returns the key text
    or None (unsigned). The trusted key is `trust_key` when given, else the one pinned in state.json
    by the first signed release; with neither, the first signed release's key is pinned now.
    trust_key "off" skips the check (signing is the publisher's choice; checking is the player's)."""
    if (trust_key or "").strip().lower() == "off":
        log("signature checks are off (trust_key = off)")
        return None
    try:
        trusted = signing.parse_key(trust_key) if trust_key else (
            signing.parse_key(state.publisher_key) if state.publisher_key else None)
    except signing.SignError as e:
        raise UpdateError(str(e))
    sig = src.get_signature(hashing.sha256_bytes(raw))
    if sig is None:
        if trusted is not None:
            raise UpdateError("the release is not signed, but this folder trusts publisher key %s: refused"
                              % signing.fingerprint(trusted))
        log("the release is not signed")
        return None
    try:
        key = signing.check_sig(sig, raw)
    except signing.SignError as e:
        raise UpdateError("the release's signature is refused: %s" % e)
    if trusted is not None and key != trusted:
        raise UpdateError("the release is signed by %s, but this folder trusts %s: refused. If the publisher really "
                          "changed keys, update once with --trust-key and their new key."
                          % (signing.fingerprint(key), signing.fingerprint(trusted)))
    text = signing.key_text(key)
    log("signed by publisher key %s" % signing.fingerprint(key))
    if not check_only and state.publisher_key != text:
        if trusted is None:
            log("trusting this publisher key from now on (the first signed release seen here)")
        state.publisher_key = text
        state.save()
    return text


def update(url, install, check_only=False, workers=4, headers=None, progress=None, log=None, cancel=None,
           keep_cache=False, trust_key=None):
    """Bring `install` up to the release at `url`. Returns the Plan.

    With check_only, nothing in the folder changes (an interrupted update is reported, not undone).
    progress(done, total, label) and log(text) are optional callbacks; `cancel` is an optional
    threading.Event that stops the download (nothing in the folder has changed then). `trust_key`
    ("ed25519:...") is the publisher key the release must be signed with (see signing.py).
    """
    log = log or (lambda s: None)
    progress = progress or (lambda d, t, l: None)
    cancel = cancel if cancel is not None else threading.Event()
    install = os.path.abspath(install)
    os.makedirs(install, exist_ok=True)
    state_dir = os.path.join(install, STATE_DIR)
    src = Source(url, headers=headers)
    with Lock(state_dir):
        rbn = _rb(state_dir, ROLLBACK_NEW)
        if check_only:
            if os.path.lexists(rbn[0]):
                log("an update stopped halfway here; the next update undoes it first")
        else:
            _recover(install, state_dir, log)

        log("reading %s" % src)
        raw = src.get_manifest()
        try:
            man = Manifest.from_json(raw)
        except ManifestError as e:
            raise UpdateError("the source's manifest is refused: %s" % e)
        log("release: %s %s (published %s), %d files, %s" % (man.name or "(unnamed)", man.version, man.published,
                                                            len(man.files), human(man.bytes)))
        state = State(install)
        signer = check_signature(src, raw, state, trust_key, check_only, log)
        cache = hashing.HashCache(os.path.join(state_dir, "hashes.json"))
        plan = make_plan(man, install, state, cache)
        cache.save()
        log(plan.summary())
        for p in plan.kept_changed:
            log("  kept (edited here, no longer in the release): %s" % p)
        for p in plan.protected:
            log("  kept (protected, edited here): %s" % p)
        if plan.empty():
            if not check_only and state.manifest_sha != hashing.sha256_bytes(raw):
                _record(state, src, man, raw, install, cache, set())
            log("up to date")
            return plan
        if check_only:
            log("check only: nothing was changed")
            return plan

        need = plan.download()
        total = sum(need.values())
        free = shutil.disk_usage(install).free
        if free < total * 2 + (64 << 20):
            raise UpdateError("not enough free space: %s needed while the update runs, %s free"
                              % (human(total * 2 + (64 << 20)), human(free)))

        # 1. download every blob into the cache and check it; the folder is not touched yet
        cdir = os.path.join(state_dir, "cache")
        os.makedirs(cdir, exist_ok=True)
        done = [0]
        lock = threading.Lock()

        def tick(n):
            with lock:
                done[0] += n
                progress(done[0], total, "downloading")

        def fetch(sha):
            if cancel.is_set():
                raise SourceError("stopped")
            dest = os.path.join(cdir, sha)
            if os.path.isfile(dest) and hashing.sha256_file(dest)[0] == sha:
                tick(need[sha])
                return
            src.fetch_blob(sha, dest, need[sha], tick, cancel)
            got, size = hashing.sha256_file(dest)
            if got != sha or size != need[sha]:
                os.remove(dest)
                raise UpdateError("blob %s from the source does not match its SHA-256 (got %s, %d bytes)"
                                  % (sha[:12], got[:12], size))

        progress(0, total, "downloading")
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futs = [pool.submit(fetch, sha) for sha in sorted(need)]
            try:
                for f in concurrent.futures.as_completed(futs):
                    f.result()
            except BaseException:
                cancel.set()       # the queued ones stop at once
                raise
        log("downloaded and checked %d blobs (%s)" % (len(need), human(total)))

        # 2. apply, journalled into rollback.new: the replaced and removed files are kept there.
        #    The last completed update's rollback/ stays until this one completes.
        rb, journal, rbfiles, prev_state = rbn
        _rmtree(rb)
        os.makedirs(rbfiles)
        had_state = os.path.isfile(state.path)
        if had_state:
            shutil.copyfile(state.path, prev_state)
        ops = [("add", e) for e in plan.add] + [("replace", e) for e in plan.replace] + \
              [("remove", r) for r in plan.remove]
        installed = set()
        with open(journal, "w", encoding="utf-8") as jf:
            _journal_write(jf, {"started": time.time(), "had_state": had_state, "source": str(src), "version": man.version})
            try:
                for i, (kind, item) in enumerate(ops):
                    path = item.path if kind != "remove" else item[0]
                    x = _xp(target_path(install, path))
                    progress(i, len(ops), "installing")
                    # the folder may have changed while the blobs downloaded: look again
                    if kind == "add" and os.path.lexists(x):
                        if os.path.isdir(x) or os.path.islink(x):
                            raise UpdateError("%s appeared here as a folder or a link during the update" % path)
                        if man.is_protected(path) or hashing.sha256_file(x)[0] == item.sha256:
                            continue
                        kind = "replace"
                    if kind == "remove" and (not os.path.isfile(x) or hashing.sha256_file(x)[0] != item[2]):
                        log("  kept (changed here during the update): %s" % path)
                        continue
                    if kind in ("replace", "remove"):
                        saved = _xp(os.path.join(rbfiles, *path.split("/")))
                        os.makedirs(os.path.dirname(saved), exist_ok=True)
                        _journal_write(jf, {"op": kind, "path": path})
                        _writable(x)
                        os.replace(x, saved)
                    else:
                        _journal_write(jf, {"op": "add", "path": path})
                    if kind != "remove":
                        os.makedirs(os.path.dirname(x), exist_ok=True)
                        tmp = x + ".dp-new"
                        if os.path.lexists(tmp):
                            _writable(tmp)
                            os.remove(tmp)
                        shutil.copyfile(os.path.join(cdir, item.sha256), tmp)
                        os.replace(tmp, x)
                        installed.add(path)
                _record(state, src, man, raw, install, cache, installed)
                _journal_write(jf, {"done": True})
            except BaseException as e:
                jf.close()
                log("the update failed while installing: undoing it")
                _undo(install, state_dir, ROLLBACK_NEW, log)
                logs.folder_record(state_dir, ["update to %s %s from %s FAILED and was undone: %s"
                                               % (man.name, man.version, src, e)])
                raise
        # complete: this update's journal becomes the one `rollback` undoes
        _rmtree(_rb(state_dir)[0])
        os.replace(rb, _rb(state_dir)[0])
        _prune_empty_dirs(install, [r[0] for r in plan.remove])
        if not keep_cache:
            for sha in need:
                try:
                    os.remove(os.path.join(cdir, sha))
                except OSError:
                    pass
        progress(len(ops), len(ops), "done")
        title = " ".join(x for x in (man.name, man.version) if x) or "the new release"
        record = ["updated to %s from %s (%s): %s" % (title, src, ("signed by " + signing.fingerprint(signing.parse_key(signer)))
                                                       if signer else "not signed", plan.summary())]
        record += ["  added    %s" % e.path for e in plan.add if e.path in installed]
        record += ["  replaced %s" % e.path for e in plan.replace if e.path in installed]
        record += ["  removed  %s" % r[0] for r in plan.remove]
        record += ["  kept     %s (edited here)" % p for p in plan.kept_changed + plan.protected]
        logs.folder_record(state_dir, record)
        log("updated to %s" % title)
        return plan


def _record(state, src, man, raw, install, cache, installed):
    """state.json lists what this program put here, so a later release removes only that.

    A file the release lists that was already here, identical, before this program first
    installed it is not claimed: it was not ours to begin with, and stays if a release drops it.
    """
    ours = {k.lower() for k in state.files}
    files = {}
    for p, e in man.files.items():
        if p not in installed and p.lower() not in ours:
            continue
        x = _xp(target_path(install, p))
        if os.path.isfile(x) and cache.hash(p, x) == e.sha256:
            files[p] = [e.size, e.sha256]
        elif p in state.files:
            files[p] = state.files[p]   # protected and edited here: still ours to remove later, only if unchanged
    state.files = files
    state.source = str(src)
    state.manifest_sha = hashing.sha256_bytes(raw)
    state.version = man.version
    state.save()
    cache.save()


def verify(url, install, headers=None, log=None):
    """Compare the folder with the source's manifest. Nothing in the folder changes (only this
    program's own hash cache may be refreshed). Returns the Plan."""
    return update(url, install, check_only=True, headers=headers, log=log)
