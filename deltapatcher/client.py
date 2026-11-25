"""The client: bring a folder up to the source's manifest, downloading only what changed.

Everything the client keeps lives in <install>/.deltapatcher/:
    state.json        the files this program installed: {path: [size, sha256]}, and the source
    hashes.json       a cache of the folder's hashes (size + modification time)
    cache/<sha256>    downloaded blobs, until they are in place (a cut download resumes from here); an archive being
                      rebuilt from what the folder has is cache/<sha256>.rebuild until it is checked (rebuild.py)
    parts/<sha256>    the parts indexes the current plan needs, so a check and the update after it fetch each once
                      (<sha256>.refused: one that was refused, never fetched again while a release names it)
    members.json      the members of the folder's zip-structured files, for rebuilding archives (rebuild.py)
    rollback/         the last completed update: its journal and the files it replaced
    rollback.new/     the update in progress (it becomes rollback/ when it completes)
    lock              held (an operating-system lock) while an update or a rollback runs

Rules:
- A file is downloaded only when its SHA-256 differs from the manifest's; the same content under
  two names is fetched once.
- Every blob is checked against its SHA-256 before anything in the folder changes. A missing or
  damaged blob on the source stops the update with the folder untouched.
- A changed archive with a parts index, read from an http(s) address, is rebuilt from the pieces the
  folder has and the pieces it lacks, fetched with Range requests (rebuild.py); it is used only when it
  is the manifest's SHA-256, and anything that does not work out downloads it whole. One whose cut whole download
  lacks no more than the rebuild would fetch has that download resumed instead.
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

from . import hashing, logs, rebuild, signing
from .manifest import Manifest, ManifestError
from .publish import human
from .source import Source, SourceError
from .torrent import pause

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
        self.manifest = None     # the release it was made for
        self.signer = ""         # the publisher key that signed it ("ed25519:..."), '' when not signed or not checked
        self.rebuilds = {}       # {sha: rebuild.Rebuild}: the archives rebuilt from what the folder has
        self.resumes = {}        # {sha: bytes it lacks}: archives a cut download of which is resumed, not rebuilt

    def empty(self):
        return not (self.add or self.replace or self.remove)

    def download(self):
        """{sha: size} of the blobs this plan needs."""
        out = {}
        for e in self.add + self.replace:
            out[e.sha256] = e.size
        return out

    def amounts(self):
        """{sha: the bytes it takes to fetch} of the blobs this plan needs: an archive rebuilt from what the folder has
        takes what it fetches, one whose cut download is resumed instead what that download lacks, any other blob its
        size."""
        return {sha: self.rebuilds[sha].fetch_bytes if sha in self.rebuilds else self.resumes.get(sha, size)
                for sha, size in self.download().items()}

    def fetch_bytes(self):
        """What this plan downloads: download() as a whole, less what the folder has of the archives it rebuilds and
        what the cut downloads it resumes have."""
        return sum(self.amounts().values())

    def summary(self):
        need = self.download()
        whole = sum(need.values())
        n, m = len(self.rebuilds), len(self.resumes)
        why = (["%d archive%s rebuilt from what is here" % (n, "" if n == 1 else "s")] if n else []) + \
              (["%d cut download%s resumed" % (m, "" if m == 1 else "s")] if m else [])
        return ("new %d, changed %d, removed %d, unchanged %d%s; to download: %d blobs, %s%s"
                % (len(self.add), len(self.replace), len(self.remove), self.same,
                   (", protected %d" % len(self.protected)) if self.protected else "",
                   len(need), human(self.fetch_bytes()),
                   " (%s: %s whole)" % (", ".join(why), human(whole)) if why else ""))


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
    """Undo the last completed update. A seed run seeding the folder pauses while it runs (torrent/pause.py)."""
    log = log or (lambda s: None)
    state_dir = os.path.join(install, STATE_DIR)
    with Lock(state_dir):
        with pause.paused(install, log):
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


def check_signature(src, raw, state, trust_key, check_only, log, keep=None):
    """Who signed this manifest, and is that the publisher this folder trusts? Returns the key text
    or None (unsigned). The trusted key is `trust_key` when given, else the one pinned in state.json
    by the first signed release; with neither, the first signed release's key is pinned now.
    trust_key "off" skips the check (signing is the publisher's choice; checking is the player's).
    `keep`: a list that gets the signature file's bytes, when there is one."""
    if (trust_key or "").strip().lower() == "off":
        log("signature checks are off (trust_key = off)")
        return None
    try:
        trusted = signing.parse_key(trust_key) if trust_key else (
            signing.parse_key(state.publisher_key) if state.publisher_key else None)
    except signing.SignError as e:
        raise UpdateError(str(e))
    sig = src.get_signature(hashing.sha256_bytes(raw))
    if sig is not None and keep is not None:
        keep.append(sig)
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
           keep_cache=False, trust_key=None, source=None, torrent=None):
    """Bring `install` up to the release at `url`. Returns the Plan.

    With check_only, nothing in the folder changes (an interrupted update is reported, not undone); its
    plan still says what an update would fetch, archives rebuilt from what the folder has included.
    progress(done, total, label) and log(text) are optional callbacks (progress counts the bytes fetched, against
    what the plan fetches in all); `cancel` is an optional threading.Event that stops the update before anything
    in the folder changes: during the download, or once every blob is here and before the first file is put in
    place. `trust_key` ("ed25519:...") is the publisher key the release must be signed with (see signing.py).
    `source`: where the release is read, instead of Source(url) (a project with no address: the swarm); one with a
    prepare() finds its release with it before the folder is locked.
    `torrent`: the update's BitTorrent side (torrent/session.py): before the download, after the archives rebuilt
    from what the folder has, fetch() fills the cache with what the swarm gives of the blobs the cache still lacks,
    told the manifest and the key that signed it (each blob is still checked against its SHA-256 before it is used,
    and what is missing comes from the source); once the folder has the release, keep() keeps its manifest,
    signature, torrent and item in .deltapatcher/release/ for seeding. A seed run of the folder pauses while it
    changes.
    """
    log = log or (lambda s: None)
    progress = progress or (lambda d, t, l: None)
    cancel = cancel if cancel is not None else threading.Event()
    install = os.path.abspath(install)
    os.makedirs(install, exist_ok=True)
    state_dir = os.path.join(install, STATE_DIR)
    src = source if source is not None else Source(url, headers=headers)
    prepare = getattr(src, "prepare", None)
    if prepare is not None:
        # a source that waits on the network to find its release (the swarm of a project with no address, in a
        # work folder of its own) finds it before the folder is locked: another update or check of the folder is
        # not refused all that time
        log("reading %s" % src)
        prepare()
    with Lock(state_dir):
        rbn = _rb(state_dir, ROLLBACK_NEW)
        if check_only:
            if os.path.lexists(rbn[0]):
                log("an update stopped halfway here; the next update undoes it first")
        else:
            _recover(install, state_dir, log)

        state = State(install)
        src.for_release(state.version)          # the User-Agent says which release this folder has
        if prepare is None:
            log("reading %s" % src)
        else:
            confirm = getattr(src, "confirm", None)
            if confirm is not None:
                confirm()                       # what it found before the lock still holds now that it is held
        raw = src.get_manifest()
        try:
            man = Manifest.from_json(raw)
        except ManifestError as e:
            raise UpdateError("the source's manifest is refused: %s" % e)
        log("release: %s %s (published %s), %d files, %s" % (man.name or "(unnamed)", man.version, man.published,
                                                            len(man.files), human(man.bytes)))
        sig_kept = []
        signer = check_signature(src, raw, state, trust_key, check_only, log, sig_kept)
        cache = hashing.HashCache(os.path.join(state_dir, "hashes.json"))
        plan = make_plan(man, install, state, cache)
        plan.manifest, plan.signer = man, signer or ""
        _save_cache(cache)
        plan.rebuilds = _rebuilds(src, plan, state, install, state_dir, log, cancel,
                                  _space_needed(sum(plan.download().values())), check_only)
        plan.resumes = _resumes(plan, state_dir, log)
        log(plan.summary())
        for p in plan.kept_changed:
            log("  kept (edited here, no longer in the release): %s" % p)
        for p in plan.protected:
            log("  kept (protected, edited here): %s" % p)
        if plan.empty():
            if not check_only and state.manifest_sha != hashing.sha256_bytes(raw):
                _record(state, src, man, raw, install, cache, set())
            if not check_only and torrent is not None and torrent.needs_keep(raw):
                with pause.paused(install, log):
                    _keep(torrent, src, raw, sig_kept, signer, log)
            log("up to date")
            return plan
        if check_only:
            log("check only: nothing was changed")
            return plan

        need = plan.download()
        total = sum(need.values())
        # a half-built archive a stopped update left goes before the free space is measured: it is never resumed, and
        # on a nearly full drive it alone could refuse every update after it
        cdir = os.path.join(state_dir, "cache")
        os.makedirs(cdir, exist_ok=True)
        rebuild.clear(cdir)
        free = shutil.disk_usage(install).free
        if free < _space_needed(total):
            raise UpdateError("not enough free space: %s needed while the update runs, %s free%s"
                              % (human(_space_needed(total)), human(free), _copies_note(install)))

        # 1. download every blob into the cache and check it; the folder is not touched yet. An archive the folder can
        #    rebuild comes first, from what the folder has and the pieces it lacks (rebuild.py); one that does not work
        #    out is downloaded whole with the rest
        amounts = plan.amounts()
        planned = [sum(amounts.values())]       # what is fetched in all: progress counts against it
        done = [0]
        lock = threading.Lock()

        def tick(n):
            with lock:
                done[0] += n
                progress(done[0], planned[0], "downloading")

        rebuilt = set()
        locate = lambda rel: _xp(target_path(install, rel))      # noqa: E731
        for sha in sorted(plan.rebuilds, key=lambda s: plan.rebuilds[s].entry.path):
            if cancel.is_set():
                raise SourceError("stopped")
            rb = plan.rebuilds[sha]
            ok = rebuild.run(rb, src, cdir, locate, workers, tick, cancel, log)
            with lock:
                # what it fetched (more when a piece here no longer matched), and the whole blob when it did not
                # work out
                planned[0] += rb.fetched + (0 if ok else need[sha]) - amounts[sha]
            if ok:
                rebuilt.add(sha)
            else:
                del plan.rebuilds[sha]

        paths = {e.sha256: e.path for e in plan.add + plan.replace}

        def fetch(sha):
            if cancel.is_set():
                raise SourceError("stopped")
            if sha in rebuilt:
                return                  # rebuilt here and checked: its fetched bytes were counted as they came
            count = tick
            if sha in plan.resumes:
                # what its cut download had is not part of what the plan fetches: progress hears of the rest
                count = _less(tick, need[sha] - plan.resumes[sha])
            dest = os.path.join(cdir, sha)
            if os.path.isfile(dest) and hashing.sha256_file(dest)[0] == sha:
                count(need[sha])
                return
            cut = os.path.isfile(dest + ".part")        # a cut download of it, which fetch_blob resumes
            src.fetch_blob(sha, dest, need[sha], count, cancel)
            got, size = hashing.sha256_file(dest)
            if (got != sha or size != need[sha]) and cut:
                # what the cut download had was damaged: once more, whole, before the source is blamed
                log("the cut download of %s was damaged: it is downloaded again, whole" % paths.get(sha, sha[:12]))
                os.remove(dest)
                with lock:
                    planned[0] += need[sha]
                src.fetch_blob(sha, dest, need[sha], tick, cancel)
                got, size = hashing.sha256_file(dest)
            if got != sha or size != need[sha]:
                os.remove(dest)
                raise UpdateError("blob %s from the source does not match its SHA-256 (got %s, %d bytes)"
                                  % (sha[:12], got[:12], size))

        if torrent is not None:
            # only what the cache lacks (a stopped update left the rest there; every blob is still checked below): a
            # cached one of the right size that fails its SHA-256 goes, and the swarm brings it again (a project with
            # no address has no other source)
            missing = {}
            for sha, size in need.items():
                # the swarm gets only what is still missing: not a rebuilt archive, nor one whose cut download
                # resumes from the address
                if sha in rebuilt or sha in plan.resumes:
                    continue
                cached = os.path.join(cdir, sha)
                if os.path.isfile(cached) and os.path.getsize(cached) == size:
                    if hashing.sha256_file(cached)[0] == sha:
                        continue
                    os.remove(cached)
                missing[sha] = size
            try:
                if missing:
                    torrent.fetch(src, missing, cdir, progress, cancel, raw=raw, signer=signer)
            except Exception as e:          # the swarm is an extra: what it did not bring comes from the source
                if cancel.is_set():
                    raise SourceError("stopped")
                log("Warning: the torrent stopped (%s): the rest comes from the source" % e)
        progress(done[0], planned[0], "downloading")
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futs = [pool.submit(fetch, sha) for sha in sorted(need)]
            try:
                for f in concurrent.futures.as_completed(futs):
                    f.result()
            except BaseException:
                cancel.set()       # the queued ones stop at once
                raise
        if rebuilt:
            log("downloaded and checked %d blobs (%s fetched; %d archive%s rebuilt from what is here, %s whole)"
                % (len(need), human(plan.fetch_bytes()), len(rebuilt), "" if len(rebuilt) == 1 else "s", human(total)))
        else:
            log("downloaded and checked %d blobs (%s)" % (len(need), human(total)))
        # a stop asked for now still finds the folder as it was
        if cancel.is_set():
            raise SourceError("stopped")

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
        with pause.paused(install, log):
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
                            # hashed as it is copied: what goes in place is what was checked, whatever happened
                            # to the cache since
                            got, _size = hashing.copy_hashing(os.path.join(cdir, item.sha256), tmp)
                            if got != item.sha256:
                                os.remove(tmp)
                                raise UpdateError("blob %s in the cache no longer matches its SHA-256 (got %s)"
                                                  % (item.sha256[:12], got[:12]))
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
            if torrent is not None:
                _keep(torrent, src, raw, sig_kept, signer, log)
        if not keep_cache:
            for sha in need:
                try:
                    os.remove(os.path.join(cdir, sha))
                except OSError:
                    pass
        rebuild.tidy(state_dir)          # the parts indexes it used: the folder has the release, no plan needs them
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


def _space_needed(total):
    """The free space an update that downloads `total` bytes needs while it runs: the blobs in the cache, the files
    they replace kept for rollback, and a margin."""
    return total * 2 + (64 << 20)


def _rebuilds(src, plan, state, install, state_dir, log, cancel, space=0, check=False):
    """The archives `plan` rebuilds from what the folder has (rebuild.prepare); {} when it rebuilds none, or when
    working that out fails: a rebuild only ever saves downloading, so every file then comes whole. `space`: the free
    space the update needs (_space_needed()); an index is kept on disk only when that much stays free after it, and a
    check (`check`) fetches none it could not keep."""
    try:
        return rebuild.prepare(src, plan, state, state_dir, lambda rel: _xp(target_path(install, rel)), log, cancel,
                               space, check)
    except Exception as e:
        if cancel is not None and cancel.is_set():
            raise SourceError("stopped")
        log("Warning: the archives that changed come whole: what is here could not be read (%s: %s)"
            % (type(e).__name__, e))
        return {}


def _resumes(plan, state_dir, log):
    """{sha: the bytes it lacks} of the archives `plan` would rebuild whose cut whole download (cache/<sha>.part) lacks
    no more than the rebuild would fetch: they leave plan.rebuilds, and fetch_blob resumes their download instead."""
    out = {}
    for sha, rb in sorted(plan.rebuilds.items(), key=lambda kv: kv[1].entry.path):
        try:
            have = os.path.getsize(os.path.join(state_dir, "cache", sha + ".part"))
        except OSError:
            continue
        left = rb.entry.size - have
        if 0 <= left <= rb.fetch_bytes:
            log("%s: a cut download of it lacks only %s: it is resumed, not rebuilt (a rebuild would fetch %s)"
                % (rb.entry.path, human(left), human(rb.fetch_bytes)))
            out[sha] = left
    for sha in out:
        del plan.rebuilds[sha]
    return out


def _less(tick, n):
    """tick(), without the first `n` bytes it hears of."""
    left = [n]
    lock = threading.Lock()

    def count(k):
        with lock:
            drop = min(left[0], k)
            left[0] -= drop
        if k > drop:
            tick(k - drop)
    return count


def _copies_note(install):
    """For a message about free space: what the seed copies take on the drive of `install` and how to free it; ''
    when none of them is there."""
    try:
        from .torrent import seedcopy
        taken = seedcopy.bytes_on_drive(install)
    except (OSError, ValueError):
        return ""
    if not taken:
        return ""
    return ("; seed copies take %s on this drive: seed from the folder with seed_copy = no, or move seed copies with "
            "seed_copies" % human(taken))


def _keep(torrent, src, raw, sig_kept, signer, log):
    """The release kept for seeding; a failure here is said and never fails the update."""
    try:
        torrent.keep(src, raw, sig_kept[0] if sig_kept else None, signer or "")
    except Exception as e:
        log("Warning: the release could not be kept for seeding: %s" % e)


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
    _save_cache(cache)


def _save_cache(cache):
    """The folder's hash cache written; it only saves time later, so a failure to write it never fails or undoes an
    update."""
    try:
        cache.save()
    except OSError:
        pass


def verify(url, install, headers=None, log=None):
    """Compare the folder with the source's manifest. Nothing in the folder changes (only this
    program's own caches in .deltapatcher may be refreshed). Returns the Plan."""
    return update(url, install, check_only=True, headers=headers, log=log)


class _NoFolder(object):
    """The state of a release read for no folder: nothing pinned."""
    publisher_key = ""
    version = ""


def read_release(url, install=None, trust_key=None, headers=None, log=None):
    """The release at `url` without looking at any folder: (its Manifest, the key text that signed it or ''). Its
    signature is checked as an update checks it, against `trust_key`, else the key `install` pinned; nothing is
    pinned or written. UpdateError or SourceError when it cannot be read or is refused."""
    log = log or (lambda s: None)
    src = Source(url, headers=headers)
    state = State(os.path.abspath(install)) if install else _NoFolder()
    src.for_release(state.version)
    raw = src.get_manifest()
    try:
        man = Manifest.from_json(raw)
    except ManifestError as e:
        raise UpdateError("the source's manifest is refused: %s" % e)
    signer = check_signature(src, raw, state, trust_key, True, log)
    return man, signer or ""
