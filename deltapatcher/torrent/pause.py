"""Updates while seeding: an update, a rollback or a publish asks every seed run seeding its folder to let go of the
folder's files first (Windows refuses to replace a file a seed run has open), and the seed runs go on after it.

In <folder>/.deltapatcher/seeding/ (for a store: <store>/.deltapatcher/seeding/):
    <pid>.seeding   a seed run seeds this folder, or is about to, or waits to: written again every BEAT seconds,
                    from before it looks at the folder's files, also while it is paused or waiting
    <pid>.pause     an update asks that seed run to pause: {"by": the update's process id, "at": when, "lock": the
                    name of the update's <pid>.<random>.asking file}
    <pid>.paused    the seed run's answer: {"pid", "at": when}: its torrent of this folder is out, its files closed
    <pid>.<random>.asking   the update holds an operating-system lock on it while it asks; the system releases the
                    lock when the update's process ends, however it ends
The update waits for an answer written after its own request (an older answer is one to an update before), or
WAIT seconds, then runs; at its end it takes its request back, and the seed run looks at the folder again and goes
on. A request whose lock can be taken is one of an update that ended, so a process id the system gave again never
holds a seed run paused. A seed run that is gone (its file not written for STALE seconds, or its process ended) is
not asked, so it never holds anything up. This works the same between the window and a terminal, or two terminals:
it is files in the folder and nothing else.
"""

import contextlib
import json
import os
import secrets
import threading
import time

from . import pid_alive

DIR = "seeding"
NEXT = "next"                # <folder>/.deltapatcher/seeding/next/: a release newer than the folder's, while seeded
WAIT = 10.0
BEAT = 2.0
STALE = 15.0


def folder_dir(root):
    return os.path.join(root, ".deltapatcher", DIR)


# On Windows a file the other side is reading at that moment can be neither replaced nor removed (PermissionError):
# both are tried again for a moment, or a request would stay and hold a seed run paused until the update's process
# ends. Reads are short, so the next try succeeds.
TRIES = 40
TRY_EVERY = 0.025


def _write(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    for attempt in range(TRIES):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == TRIES - 1:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(TRY_EVERY)


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return None


def _remove(path):
    for _attempt in range(TRIES):
        try:
            os.remove(path)
            return
        except PermissionError:
            time.sleep(TRY_EVERY)
        except OSError:
            return


def _lock(fh):
    """An operating-system lock on the open file fh, without waiting: True when it is had."""
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh):
    try:
        if os.name == "nt":
            import msvcrt
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _held(path):
    """True while the update that made the lock file `path` still holds its lock (it still runs)."""
    try:
        fh = open(path, "a+b")
    except OSError:
        return False
    try:
        if _lock(fh):
            _unlock(fh)
            return False
        return True
    finally:
        fh.close()


def seeders(root):
    """The process ids of the seed runs seeding this folder now."""
    d = folder_dir(root)
    try:
        names = os.listdir(d)
    except OSError:
        return []
    out = []
    now = time.time()
    for n in names:
        digits = n[:-len(".seeding")]
        if not n.endswith(".seeding") or not (digits.isascii() and digits.isdigit()):
            continue
        pid = int(digits)
        try:
            fresh = now - os.path.getmtime(os.path.join(d, n)) < STALE
        except OSError:
            continue
        if fresh and pid_alive(pid):            # this process too: a seed run in a thread of its own pauses as well
            out.append(pid)
    return sorted(out)


def _answered(d, pid, at):
    """The seed run `pid` answered a request made at `at`: its .paused file was written after it."""
    doc = _read(os.path.join(d, "%d.paused" % pid))
    when = doc.get("at") if doc else None
    return isinstance(when, (int, float)) and not isinstance(when, bool) and when >= at


@contextlib.contextmanager
def paused(root, log=None, wait=None):
    """Around an update of the folder `root`: every seed run seeding it pauses first (it answers this request, or
    WAIT seconds pass), and goes on once this ends."""
    log = log or (lambda s: None)
    pids = seeders(root)
    if not pids:
        yield
        return
    d = folder_dir(root)
    me = os.path.join(d, "%d.%s.asking" % (os.getpid(), secrets.token_hex(4)))
    holder = None
    try:
        holder = open(me, "a+b")
        if not _lock(holder):
            holder.close()
            holder = None
    except OSError:
        holder = None
    asked = {}
    for pid in pids:
        at = time.time()
        doc = {"by": os.getpid(), "at": at}
        if holder is not None:
            doc["lock"] = os.path.basename(me)
        try:
            _write(os.path.join(d, "%d.pause" % pid), doc)
            asked[pid] = at
        except OSError:
            pass
    try:
        end = time.time() + (WAIT if wait is None else wait)
        waiting = list(asked)
        while waiting and time.time() < end:
            waiting = [pid for pid in waiting if not _answered(d, pid, asked[pid]) and pid in seeders(root)]
            if waiting:
                time.sleep(0.1)
        if waiting:
            log("Warning: a seed run of this folder did not pause within %d seconds: going on" % int(WAIT if wait is None else wait))
        elif asked:
            log("seeding of this folder pauses while it changes")
        yield
    finally:
        for pid in asked:
            _remove(os.path.join(d, "%d.pause" % pid))
        if holder is not None:
            _unlock(holder)
            holder.close()
        _remove(me)


class Seat(object):
    """A seed run's side of the handshake for one folder."""

    def __init__(self, root):
        self.root = root
        self.dir = folder_dir(root)
        self.pid = os.getpid()
        self.last = 0.0
        self.answered = 0.0                     # when this run last answered a request

    def _p(self, ext):
        return os.path.join(self.dir, "%d.%s" % (self.pid, ext))

    def beat(self, force=False):
        """Say again that this run seeds the folder, or is about to, or waits to (every BEAT seconds is enough);
        never for a folder that is not there (it is not made)."""
        if not force and time.time() - self.last < BEAT:
            return
        if not os.path.isdir(self.root):
            return
        try:
            _write(self._p("seeding"), {"pid": self.pid, "at": time.time()})
            self.last = time.time()
        except OSError:
            pass

    def request(self):
        """When an update asked this run to pause, while it asks and still runs (0.0 for a request that does not say
        when); None when nothing asks."""
        doc = _read(self._p("pause"))
        if doc is None:
            return None
        lock = doc.get("lock")
        if isinstance(lock, str) and lock.endswith(".asking") and os.path.basename(lock) == lock:
            if not _held(os.path.join(self.dir, lock)):
                # the update that asked ended (its lock is free), whatever process has its id now
                _remove(self._p("pause"))
                _remove(os.path.join(self.dir, lock))
                return None
        else:
            by = doc.get("by")
            if isinstance(by, int) and not pid_alive(by):
                _remove(self._p("pause"))       # the update that asked is gone
                return None
        at = doc.get("at")
        return float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else 0.0

    def asked(self):
        """True while an update of the folder asks this run to pause, and the update still runs."""
        return self.request() is not None

    def answer(self):
        """This run's torrent of the folder is out and its files are closed: said with the time, which the update
        compares with its request's."""
        now = time.time()
        try:
            _write(self._p("paused"), {"pid": self.pid, "at": now})
            self.answered = now
        except OSError:
            pass

    def resumed(self):
        _remove(self._p("paused"))

    def leave(self):
        for ext in ("seeding", "paused"):
            _remove(self._p(ext))
