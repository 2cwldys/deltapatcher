"""BitTorrent beside HTTP: a project's releases shared between players, set by its publisher, off by default.

A project shared by torrent ([torrent] torrent = yes, which needs signed releases) has an id made at random once
(torrent_id), a torrent name and a list of seeds (host:port) its publisher keeps reachable. Publishing it also
makes release.torrent (the store's own files as a BitTorrent v2 torrent: make.py) and release.item (a signed
pointer to that torrent, kept in the DHT for projects with no address: item.py). Players' DeltaPatchers then
download from each other as well as from the address; a project with no address at all updates by torrent only.

Every peer is a DeltaPatcher and nothing outside is needed: peers come from the project's seeds, peer exchange, a
peer cache of each folder, the local network while seeding, and for projects with no address a DHT that starts
only from those. No tracker, no public DHT router, no port mapping unless the person turns it on (session.py).

Peers never decide which file is what: every file of the torrent is named by its SHA-256 or is the publisher's
signed manifest, and every blob is checked against its SHA-256 before it is used, as over HTTP.

The person decides: general.torrents = refuse means no BitTorrent at all, whatever a project says; seeding needs
their yes for each project (seed = yes, or `deltapatcher seed --project NAME`).

Only session.py imports libtorrent, and only when it is used: without it everything else works, and anything
that needs it says NO_SUPPORT.
"""

import contextlib
import json
import os
import re
import secrets
import threading
import time
import urllib.parse

NO_SUPPORT = "This DeltaPatcher has no BitTorrent support (libtorrent is not installed)"
REFUSED_ONLY = "This project updates only by torrent, and torrents are refused in your settings (Settings, Torrents)."
REFUSED = "Torrents are refused in your settings (Settings, Torrents): nothing is seeded."
TORRENT_FILE = "release.torrent"
ITEM_FILE = "release.item"
RELEASE_DIR = "release"             # <folder>/.deltapatcher/release/: the release the folder has, as a seed offers it
STATE_NAME = "torrent.json"         # <folder>/.deltapatcher/torrent.json: the newest item accepted, the peers met
SEEDING_NAME = "seeding.json"       # beside the main settings file: what the seed runs are seeding
DEFAULT_NAME = "release"
MAX_SEEDS = 20
MAX_PEERS = 50
ID_RE = re.compile(r"^[0-9a-f]{32}\Z")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_LABEL = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\Z")
_DEVICES = set(["con", "prn", "aux", "nul"] + ["com%d" % i for i in range(1, 10)] + ["lpt%d" % i for i in range(1, 10)])
# the names of a store's own files and folders: a torrent named one of them would hide them from its web seed
# (deltapatcher serve answers <address>/<name>/... as <address>/...)
RESERVED_NAMES = ("blobs", "sigs", "manifest.json", "manifest.previous.json", "release.torrent", "release.item")
TEST_IP_ENV = "DELTAPATCHER_TORRENT_TEST_IP"


class TorrentError(Exception):
    """A torrent that cannot be made, read, downloaded or seeded; `code` says why, for the app."""

    def __init__(self, message, code="torrent-failed", data=None):
        Exception.__init__(self, message)
        self.code = code
        self.data = data or {}


_AVAILABLE = []


def available():
    """True when libtorrent can be loaded (it is inside the built DeltaPatcher; from source it is optional)."""
    if not _AVAILABLE:
        try:
            from . import session
            session.lt()
            _AVAILABLE.append(True)
        except (TorrentError, ImportError, OSError):
            _AVAILABLE.append(False)
    return _AVAILABLE[0]


def require():
    if not available():
        raise TorrentError(NO_SUPPORT, "no-torrent-support")


# ---- a project's torrent: its id, its name, its seeds -----------------------------------------------------

def new_id():
    """A torrent id: 16 random bytes as hex. Made once, when torrents are first turned on; it never changes."""
    return secrets.token_hex(16)


def id_problem(text):
    return "" if isinstance(text, str) and ID_RE.match(text) else "32 hexadecimal characters"


def name_problem(name):
    """What is wrong with a torrent's name ('' when nothing): letters, digits and . _ - only, starting with a letter
    or digit, at most 60 characters, not ending with a dot, not a name Windows keeps for a device, not the name of
    a store's own file or folder (RESERVED_NAMES, in any case)."""
    if not isinstance(name, str) or not name:
        return "give the torrent a name"
    if len(name) > 60:
        return "a torrent's name is at most 60 characters"
    if not _NAME_RE.match(name) or name.endswith("."):
        return "a torrent's name has letters, digits and . _ - only (not at its start, and no dot at its end): %s" % name
    if name.split(".")[0].lower() in _DEVICES:
        return "%s is a name Windows keeps for a device: choose another" % name
    if name.lower() in RESERVED_NAMES:
        return "%s is the name of a file or folder of the store: choose another" % name
    return ""


def default_name(address):
    """The torrent's name when the publisher gives none: the last part of the address's path when it is a name
    a torrent may have, else "release"."""
    path = urllib.parse.urlsplit(address or "").path if address else ""
    last = urllib.parse.unquote(path.rstrip("/").rsplit("/", 1)[-1]) if path else ""
    return last if last and not name_problem(last) else DEFAULT_NAME


def _host_problem(host):
    if host.startswith("[") and host.endswith("]"):
        inner = host[1:-1]
        return "" if inner and all(c in "0123456789abcdefABCDEF:." for c in inner) and ":" in inner else "an address"
    if len(host) > 253 or not host:
        return "an address"
    if all(_LABEL.match(part) for part in host.split(".")):
        return ""
    return "an address"


def seed_problem(text):
    """What is wrong with one seed ('' when nothing): host:port, the host a name or an IPv4 address or [an IPv6
    address], the port 1 to 65535."""
    if not isinstance(text, str) or ":" not in text or len(text) > 300:
        return "host:port"
    host, _sep, port = text.rpartition(":")
    if not (port.isascii() and port.isdigit()) or not 1 <= int(port) <= 65535 or port != str(int(port)):
        return "a port from 1 to 65535 after the :"
    if _host_problem(host):
        return "a host name or address before the :"
    return ""


def seeds_of(text, strict=False):
    """The seeds a setting lists (host:port, separated by , ; or spaces), in order, each once. strict: ValueError
    at the first one that is not host:port; otherwise those are left out."""
    out = []
    for item in re.split(r"[,;\s]+", str(text or "")):
        item = item.strip()
        if not item:
            continue
        problem = seed_problem(item)
        if problem:
            if strict:
                raise ValueError("the seed %s: %s" % (item, problem))
            continue
        if item not in out:
            out.append(item)
    if strict and len(out) > MAX_SEEDS:
        raise ValueError("at most %d seeds" % MAX_SEEDS)
    return out[:MAX_SEEDS]


def address_of(cfg):
    """The address a project's players update from, for torrents: its source_url unless that is empty or the
    default every new settings file has (http://localhost:8000/); else its public_url; '' when it has neither. A
    project shared by torrent with no address updates by torrent only."""
    from ..source import DEFAULT_URL
    source = str(cfg.get("source_url") or "").strip()
    if source and source != DEFAULT_URL:
        return source
    return str(cfg.get("public_url") or "").strip()


def publisher_address(cfg):
    """The http(s) address a project file or a release gives players: presets.address()."""
    from ..presets import address
    return address(cfg)


def info(cfg):
    """A project's torrent, {"id", "name", "seeds"}, when its releases are shared by torrent (torrent = yes, and an
    id); None otherwise. The name is torrent_name, else default_name() of its address."""
    if not cfg.get("torrent"):
        return None
    tid = str(cfg.get("torrent_id") or "").strip().lower()
    if not ID_RE.match(tid):
        return None
    name = str(cfg.get("torrent_name") or "").strip()
    if not name or name_problem(name):
        name = default_name(publisher_address(cfg))
    return {"id": tid, "name": name, "seeds": seeds_of(cfg.get("seeds"))}


def torrent_only(cfg):
    """A project shared by torrent with no address: it updates by torrent only."""
    return info(cfg) is not None and not address_of(cfg)


def refused(cfg):
    """The person refuses BitTorrent: no session is ever made. Only general.torrents = allow allows it; refuse, or
    anything else written there, refuses it (a mistyped setting never lets BitTorrent in)."""
    return str(cfg.get("torrents", "allow")).strip().lower() != "allow"


def object_problem(obj):
    """What is wrong with a torrent object of a project file or a release ('' when nothing): exactly an id, a
    name and a list of seeds."""
    if not isinstance(obj, dict) or set(obj) != {"id", "name", "seeds"}:
        return "an id, a name and a list of seeds"
    if id_problem(obj["id"]):
        return "its id: %s" % id_problem(obj["id"])
    if name_problem(obj["name"]):
        return "its name: %s" % name_problem(obj["name"])
    seeds = obj["seeds"]
    if not isinstance(seeds, list) or len(seeds) > MAX_SEEDS:
        return "a list of at most %d seeds" % MAX_SEEDS
    for s in seeds:
        if seed_problem(s):
            return "the seed %r: %s" % (s, seed_problem(s))
    if len(set(seeds)) != len(seeds):
        return "a seed listed twice"
    return ""


def text_of(obj):
    """A torrent object in one line, as a change to it is shown and compared: '' for none."""
    if not obj:
        return ""
    seeds = ", ".join(obj.get("seeds") or []) or "none"
    return "%s, seeds %s, id %s" % (obj.get("name", ""), seeds, obj.get("id", ""))


def settings_of(obj):
    """The settings a torrent object of a project file or a release sets in a project."""
    return {"torrent": True, "torrent_id": obj["id"], "torrent_name": obj["name"], "seeds": ", ".join(obj["seeds"])}


def web_seed(address, name):
    """The web seed of a torrent named `name` whose store is at `address`: the address's parent folder when the
    address's last part is that name (a web seed is asked for <it>/<name>/blobs/<sha>); '' otherwise."""
    if not address or not address.lower().startswith(("http://", "https://")):
        return ""
    parts = urllib.parse.urlsplit(address)
    path = parts.path.rstrip("/")
    if not path or path.rsplit("/", 1)[-1] != name or parts.query or parts.fragment:
        return ""
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path.rsplit("/", 1)[0] + "/", "", ""))


def test_ip():
    """The address every session listens on in DeltaPatcher's own tests on one computer (DELTAPATCHER_TORRENT_TEST_IP,
    a 127.x.x.x address); '' otherwise."""
    ip = os.environ.get(TEST_IP_ENV, "").strip()
    return ip if re.match(r"^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip) else ""


# ---- what a folder keeps: the newest item accepted, the peers met ----------------------------------------

def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_json(path, doc):
    """Write the file whole, through a temporary file of this process and thread (a seed run's look writes from a
    thread of its own); on Windows the replace can be refused for a moment while another process reads the file,
    so it is tried again."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.%d.tmp" % (path, os.getpid(), threading.get_ident())
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    for attempt in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 39:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(0.05)


class Kept(object):
    """<state dir>/torrent.json of a folder (or a store): the highest sequence number of a release item accepted
    for the torrent (an item numbered lower is refused, from anyone), and the peer cache: peers met while
    connected, tried first next time."""

    def __init__(self, state_dir, torrent_id):
        self.path = os.path.join(state_dir, STATE_NAME)
        self.id = torrent_id
        doc = _read_json(self.path)
        mine = doc.get(torrent_id) if isinstance(doc.get(torrent_id), dict) else {}
        seq = mine.get("seq", 0)
        self.seq = seq if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0 else 0
        self.peers = [p for p in mine.get("peers", []) if isinstance(p, str) and not seed_problem(p)][:MAX_PEERS]

    def accept(self, seq):
        """An item numbered `seq` was accepted: none lower is accepted from now on."""
        if seq > self.seq:
            self.seq = seq

    def met(self, peers, own=()):
        """Peers met (host:port), newest first, ahead of the ones kept; this computer's own addresses left out."""
        fresh = []
        for p in peers:
            if p not in own and p not in fresh and not seed_problem(p):
                fresh.append(p)
        self.peers = (fresh + [p for p in self.peers if p not in fresh])[:MAX_PEERS]

    def save(self):
        """Write it, never with a lower number than the file has now: another run of this folder (an update, a seed
        run's look) may have accepted a higher one meanwhile."""
        doc = _read_json(self.path)
        there = doc.get(self.id) if isinstance(doc.get(self.id), dict) else {}
        seq = there.get("seq", 0)
        if isinstance(seq, int) and not isinstance(seq, bool) and seq > self.seq:
            self.seq = seq
        doc[self.id] = {"seq": self.seq, "peers": self.peers, "saved": _iso(time.time())}
        try:
            _write_json(self.path, doc)
        except OSError:
            pass


# ---- seeding.json: what the seed runs are seeding, for the window, the MCP server and `projects` -----------

FRESH = 30           # a seed run writes its line every 5 seconds: one older than this is a run that is gone


def seeding_path():
    from .. import config
    return os.path.join(os.path.dirname(config.path()), SEEDING_NAME)


def pid_alive(pid):
    """True when a process with this id runs (on Windows: it has not exited)."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5               # access denied: it runs, as another user or protected
        try:
            code = wintypes.DWORD()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259    # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _when(text):
    import calendar
    try:
        return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return 0


def seeding_runs():
    """The seed runs going on now: [{"pid", "started", "updated", "port", "projects": [...]}], each with a live pid
    and a line written in the last FRESH seconds."""
    doc = _read_json(seeding_path())
    runs = doc.get("runs") if isinstance(doc.get("runs"), list) else []
    now = time.time()
    return [r for r in runs if isinstance(r, dict) and pid_alive(r.get("pid")) and now - _when(r.get("updated")) < FRESH]


def seeding_now():
    """{settings file (as compared): what its seed run says of it} for every project being seeded now."""
    out = {}
    for run in seeding_runs():
        for p in run.get("projects") or []:
            if isinstance(p, dict) and isinstance(p.get("file"), str):
                out[os.path.normcase(os.path.abspath(p["file"]))] = dict(p, pid=run["pid"])
    return out


@contextlib.contextmanager
def _seeding_lock(seconds=5.0, path=None):
    """The seed runs' lock on seeding.json, so two runs never write it at once (an operating-system lock on
    seeding.json.lock beside it, which the system releases when the process ends, however it ends). Yields True
    while it is held, False when it could not be had within `seconds`. path: another lock file of the same kind
    (seed-copies.json.lock, for the list of seed copies)."""
    path = path or (seeding_path() + ".lock")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = open(path, "a+b")
    except OSError:
        yield False
        return
    held = False
    try:
        end = time.time() + seconds
        while not held:
            try:
                if os.name == "nt":
                    import msvcrt
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                held = True
            except OSError:
                if time.time() >= end:
                    break
                time.sleep(0.02)
        yield held
    finally:
        if held:
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
        fh.close()


def write_seeding(run):
    """This seed run's line in seeding.json (None: take it out), keeping the lines of the other runs alive. The
    runs take turns (_seeding_lock); one that cannot have the lock in time leaves this write out (its next one
    comes a few seconds later, and every reader passes over the line of a process that ended)."""
    path = seeding_path()
    pid = os.getpid()
    with _seeding_lock() as held:
        if not held:
            return
        doc = _read_json(path)
        runs = [r for r in (doc.get("runs") if isinstance(doc.get("runs"), list) else [])
                if isinstance(r, dict) and r.get("pid") != pid and pid_alive(r.get("pid"))]
        if run is not None:
            runs.append(dict(run, pid=pid, updated=_iso(time.time())))
        try:
            if runs:
                _write_json(path, {"format": "deltapatcher-seeding", "version": 1, "runs": runs})
            elif os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
