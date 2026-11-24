"""Statistics for publishers: one document per project, made from what the publisher already has.

Releases stay plain static files and nothing runs on the publisher's server, so the numbers come from the
project's own store (its releases) and, when the project names it, the web server's access log (update
checks, downloads, players). An updater tells nothing about itself beyond its User-Agent line,
"DeltaPatcher/<version> release/<version of the release it has>" (source.user_agent).

The document keeps its shape: a new section or field never changes an old one, and a change in what a
field means gets a new version.

    {"format": "deltapatcher-stats", "version": 1,
     "project": {"project": "Default", "file": "<its settings file>"},
     "made": "2026-11-24T12:00:00Z", "days": 30,
     "sections": {"releases": {...}, "downloads": {...}},
     "problems": {"downloads": "access_log is not set: ..."}}

Adding a section, the API that can grow: write collect(project, cfg, days), which returns the section as
plain JSON values (str, int, float, bool, None, and lists and dicts of them) or raises StatsUnavailable
with a reason the person can act on; then call register("name", collect). Collectors run in the order they
were registered, and one that fails never stops the others: its reason goes to "problems".

Privacy: counts only. No address, user agent or line of a log is stored, logged, returned or sent
anywhere. A log on the server is read from a temporary copy that is removed as soon as it is read; players
are told apart in memory only, by a salted hash that exists while the log is read, and only how many there
were is kept. Of a User-Agent only the release version is used.
"""

import calendar
import hashlib
import json
import math
import os
import posixpath
import re
import shutil
import tempfile
import time
import urllib.parse

from . import config, ssh
from .manifest import BLOB_DIR, HEX64, MANIFEST_NAME, PREVIOUS_NAME
from .publish import HISTORY_NAME, human, load_manifest_file
from .signing import SIG_DIR

FORMAT = "deltapatcher-stats"
VERSION = 1
DAYS = 30                   # the period when none is given
MAX_DAYS = 366
HISTORY_SHOWN = 50          # the newest releases of the history the document lists
HISTORY_LIMIT = 16 << 20    # a history.jsonl is read up to this size
LOG_LIMIT = 512 << 20       # an access log bigger than this is not read
MAX_LINE = 16 << 10         # a longer line of a log is passed over, as one it cannot read
MAX_RELEASES = 20           # the releases players are on that are listed, the most used first
DAY = 86400

COLLECTORS = []             # [(name, collect)], in the order they run
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class StatsUnavailable(Exception):
    """A section that cannot be made now, and what the person can do about it."""


def register(name, collect):
    """Add a section to every document: collect(project, cfg, days) returns it, or raises StatsUnavailable."""
    if not _NAME.match(str(name)) or any(n == name for n, _c in COLLECTORS):
        raise ValueError("a collector's name is lowercase letters, digits and _, and each is used once: %r" % (name,))
    COLLECTORS.append((name, collect))


def now():
    """The time now, in seconds since the epoch (a test stands in for it)."""
    return time.time()


def build(project, cfg, days=DAYS):
    """The statistics of `project` (a projects.Project, with its settings `cfg`) over the last `days` days."""
    days = max(1, min(MAX_DAYS, int(days)))
    doc = {"format": FORMAT, "version": VERSION, "project": project.tag(),
           "made": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now())), "days": days,
           "sections": {}, "problems": {}}
    for name, collect in COLLECTORS:
        try:
            doc["sections"][name] = collect(project, cfg, days)
        except StatsUnavailable as e:
            doc["problems"][name] = str(e)
        except Exception as e:          # a collector's own failure: the others still run
            doc["problems"][name] = "%s failed: %s: %s" % (name, type(e).__name__, e)
    return doc


# ---- releases: the project's own store -------------------------------------------------------------

def store_of(cfg):
    """The project's store on this computer: store_dir, else the one DeltaPatcher keeps for publish_src."""
    if str(cfg.get("store_dir") or "").strip():
        return os.path.abspath(os.path.expanduser(cfg["store_dir"]))
    if str(cfg.get("publish_src") or "").strip():
        return config.default_store(cfg["publish_src"])
    raise StatsUnavailable("this project releases nothing yet: choose the folder to release (publish_src), "
                           "then release it once")


def release_of(man):
    return {"name": man.name, "version": man.version, "published": man.published, "files": len(man.files),
            "bytes": man.bytes, "blobs": len(man.blobs())}


def change(old, new):
    """What `new` changed from `old`: files new, changed and removed, and what a player on `old` downloads."""
    have = old.blobs()
    return {"from": {"name": old.name, "version": old.version, "published": old.published},
            "new": sum(1 for p in new.files if p not in old.files),
            "changed": sum(1 for p, e in new.files.items() if p in old.files and old.files[p].sha256 != e.sha256),
            "removed": sum(1 for p in old.files if p not in new.files),
            "download_bytes": sum(size for sha, size in new.blobs().items() if sha not in have)}


def store_size(store):
    files = size = 0
    for top, _dirs, names in os.walk(store):
        for n in names:
            try:
                size += os.path.getsize(os.path.join(top, n))
                files += 1
            except OSError:
                pass
    return files, size


_HISTORY_TYPES = (("time", str), ("name", str), ("version", str), ("files", int), ("bytes", int),
                  ("new_blobs", int), ("download_bytes", (int, type(None))))


def read_history(store):
    """(the releases in <store>/history.jsonl, newest first, at most HISTORY_SHOWN; how many it holds). A line
    that is not one of publish's is passed over."""
    try:
        with open(os.path.join(store, HISTORY_NAME), "rb") as f:
            raw = f.read(HISTORY_LIMIT)
    except OSError:
        return [], 0
    out = []
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            doc = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(doc, dict):
            continue
        entry = {k: doc.get(k) for k, _t in _HISTORY_TYPES}
        if all(isinstance(entry[k], t) and not isinstance(entry[k], bool) for k, t in _HISTORY_TYPES):
            out.append(entry)
    return out[::-1][:HISTORY_SHOWN], len(out)


def collect_releases(project, cfg, days):
    """The current release, what it changed from the one before, the store's size and the release history."""
    store = store_of(cfg)
    current = load_manifest_file(os.path.join(store, MANIFEST_NAME))
    if current is None:
        raise StatsUnavailable("nothing is published in %s yet: release once" % store)
    previous = load_manifest_file(os.path.join(store, PREVIOUS_NAME))
    files, size = store_size(store)
    history, total = read_history(store)
    return {"store": store, "current": release_of(current),
            "change": change(previous, current) if previous is not None else None,
            "store_files": files, "store_bytes": size, "history": history, "history_total": total}


# ---- downloads: the web server's access log ----------------------------------------------------------

_MONTHS = dict((m, i) for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1))
# the Combined Log Format (nginx, Apache): the referer and the user agent are left out in the Common one
_QUOTED = r'"((?:[^"\\]|\\.){0,8192})"'
_COMBINED = re.compile(r'^(\S{1,256}) \S{1,256} \S{1,256} \[([^\]]{1,64})\] ' + _QUOTED + r' (\d{3}) (\d{1,20}|-)'
                       r'(?: ' + _QUOTED + ' ' + _QUOTED + ')?')
_CLF_TIME = re.compile(r"^(\d{2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2}) ([+-])(\d{2})(\d{2})$")
_ISO_TIME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.\d{1,9})?(Z|[+-]\d{2}:?\d{2})$")
# the release in a DeltaPatcher User-Agent, as source.user_agent writes it
_AGENT = re.compile(r"^DeltaPatcher/\S{1,40}(?: release/([A-Za-z0-9._+-]{1,40}))?(?:\s|$)")


def _seconds(y, mo, d, h, mi, s, offset):
    """Seconds since the epoch, UTC; None for a date that is not one."""
    if not (1970 <= y <= 9999 and 1 <= mo <= 12 and h < 24 and mi < 60 and s < 61):
        return None
    if not 1 <= d <= calendar.monthrange(y, mo)[1]:
        return None
    return calendar.timegm((y, mo, d, h, mi, min(s, 59), 0, 0, 0)) - offset


def _clf_time(text):
    m = _CLF_TIME.match(text)
    if not m or m.group(2).title() not in _MONTHS:
        return None
    offset = (int(m.group(8)) * 3600 + int(m.group(9)) * 60) * (1 if m.group(7) == "+" else -1)
    return _seconds(int(m.group(3)), _MONTHS[m.group(2).title()], int(m.group(1)), int(m.group(4)), int(m.group(5)),
                    int(m.group(6)), offset)


def _iso_time(text):
    m = _ISO_TIME.match(text)
    if not m:
        return None
    zone = m.group(7)
    offset = 0
    if zone != "Z":
        digits = zone[1:].replace(":", "")
        offset = (int(digits[:2]) * 3600 + int(digits[2:]) * 60) * (1 if zone[0] == "+" else -1)
    return _seconds(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)),
                    int(m.group(6)), offset)


def _path_of(target):
    """The decoded path a request asked for, without its query; None when it is not a path."""
    if target.startswith(("http://", "https://")):
        target = urllib.parse.urlsplit(target).path or "/"
    if not target.startswith("/"):
        return None
    return urllib.parse.unquote(target.split("?", 1)[0].split("#", 1)[0])


def _combined(text):
    m = _COMBINED.match(text)
    if not m:
        return None
    when = _clf_time(m.group(2))
    request = m.group(3).split(" ")
    if when is None or len(request) not in (2, 3):
        return None
    path = _path_of(request[1])
    if path is None:
        return None
    size = 0 if m.group(5) == "-" else int(m.group(5))
    return when, request[0], path, int(m.group(4)), size, m.group(1), m.group(7), None


def _caddy(text):
    """A line of Caddy's JSON access log, read like a line of the Combined Log Format."""
    doc = json.loads(text)
    req = doc.get("request") if isinstance(doc, dict) else None
    if not isinstance(req, dict):
        return None
    ts = doc.get("ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool) and math.isfinite(ts):
        when = float(ts)
    elif isinstance(ts, str):
        when = _iso_time(ts)
    else:
        when = None
    method, uri, status, size = req.get("method"), req.get("uri"), doc.get("status"), doc.get("size", 0)
    if when is None or not isinstance(method, str) or not isinstance(uri, str) or not isinstance(status, int):
        return None
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        size = 0
    address = req.get("client_ip") or req.get("remote_ip") or req.get("remote_addr")
    if not isinstance(address, str):
        return None
    if req.get("remote_addr") == address and not req.get("remote_ip"):
        address = address.rsplit(":", 1)[0] if address.count(":") == 1 else address.split("]")[0].strip("[")
    headers = req.get("headers") if isinstance(req.get("headers"), dict) else {}
    agent = next((v for k, v in headers.items() if str(k).lower() == "user-agent"), None)
    if isinstance(agent, list):
        agent = agent[0] if agent else None
    path = _path_of(uri)
    if path is None:
        return None
    host = req.get("host") if isinstance(req.get("host"), str) else None
    return when, method, path, status, size, address, agent if isinstance(agent, str) else None, host


def read_line(raw):
    """One line of an access log as (time, method, path, status, bytes, address, user agent, host or None);
    None when it is not a request this can read. Nothing of it is kept."""
    try:
        text = raw.decode("utf-8", "replace").strip()
        if text.startswith("{"):
            return _caddy(text)
        return _combined(text)
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError, RecursionError):
        return None


def release_in(agent):
    """The release a DeltaPatcher User-Agent names; None when it names none (an older updater, or not one)."""
    m = _AGENT.match(agent or "")
    return m.group(1) if m else None


def _host_name(host):
    """A Host header's name: no port, no brackets, lower case."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class Counter(object):
    """One project's requests in an access log, counted per day for the last `days` days. A player is a
    salted hash of an address, in memory only, while the log is read; no address is ever kept."""

    def __init__(self, prefix, host, days, at):
        self.prefix, self.host = prefix, host
        self.last = int(at // DAY)
        self.first = self.last - days + 1
        self.checks, self.downloads, self.bytes = [0] * days, [0] * days, [0] * days
        self.day_players = [set() for _ in range(days)]
        self.players = set()
        self.last_check = {}              # player -> (time, release) of its last update check
        self._salt = os.urandom(16)
        self.lines = self.unreadable = self.counted = 0

    def _player(self, address):
        return int.from_bytes(hashlib.sha256(self._salt + address.encode("utf-8", "replace")).digest()[:8], "big")

    def kind(self, path):
        """What a request for `path` is to this project: check, download, file (a signature, the previous
        manifest), or None when it is not one of its files."""
        if not path.startswith(self.prefix):
            return None
        rest = path[len(self.prefix):]
        if rest == MANIFEST_NAME:
            return "check"
        if rest.startswith(BLOB_DIR + "/") and HEX64.match(rest[len(BLOB_DIR) + 1:]):
            return "download"
        if rest == PREVIOUS_NAME or (rest.startswith(SIG_DIR + "/") and rest.endswith(".sig")
                                     and HEX64.match(rest[len(SIG_DIR) + 1:-4])):
            return "file"
        return None

    def add(self, request):
        when, method, path, status, size, address, agent, host = request
        if method != "GET" or (host and _host_name(host) != self.host):
            return
        kind = self.kind(path)
        if kind is None or not (200 <= status < 300 or (status == 304 and kind == "check")):
            return
        day = int(when // DAY)
        if not self.first <= day <= self.last:
            return
        i = day - self.first
        who = self._player(address)
        if kind == "check":
            self.checks[i] += 1
            before = self.last_check.get(who)
            if before is None or when >= before[0]:
                self.last_check[who] = (when, release_in(agent))
        elif kind == "download":
            self.downloads[i] += 1
        self.bytes[i] += size
        self.day_players[i].add(who)
        self.players.add(who)
        self.counted += 1

    def read(self, f):
        """Count the requests in the open (binary) file f, line by line."""
        head = f.read(2)
        if head == b"\x1f\x8b":
            raise StatsUnavailable("the access log is compressed: name the log the web server writes now, not a "
                                   "rotated .gz")
        f.seek(0)
        for raw in _lines(f):
            if raw is not None and not raw.strip():
                continue
            self.lines += 1
            request = read_line(raw) if raw is not None else None
            if request is None:
                self.unreadable += 1
                continue
            self.add(request)

    def section(self):
        days = [{"date": time.strftime("%Y-%m-%d", time.gmtime((self.first + i) * DAY)), "checks": self.checks[i],
                 "downloads": self.downloads[i], "bytes": self.bytes[i], "players": len(self.day_players[i])}
                for i in range(len(self.checks))]
        on = {}
        for _when, release in self.last_check.values():
            on[release] = on.get(release, 0) + 1
        ranked = sorted(on.items(), key=lambda kv: (-kv[1], kv[0] is None, kv[0] or ""))
        return {"days": days,
                "total": {"checks": sum(self.checks), "downloads": sum(self.downloads), "bytes": sum(self.bytes),
                          "players": len(self.players)},
                "releases": [{"release": r, "players": n} for r, n in ranked[:MAX_RELEASES]],
                "releases_more": sum(n for _r, n in ranked[MAX_RELEASES:]),
                "lines": self.lines, "unreadable": self.unreadable, "counted": self.counted}


def _lines(f):
    """The lines of a binary file; a line longer than MAX_LINE is passed over, a piece at a time, as None."""
    while True:
        line = f.readline(MAX_LINE + 1)
        if not line:
            return
        if len(line) > MAX_LINE and not line.endswith(b"\n"):
            while True:
                more = f.readline(MAX_LINE + 1)
                if not more or more.endswith(b"\n"):
                    break
            yield None
            continue
        yield line


def public_path(cfg):
    """(the path players' requests start with, the host) from public_url: which lines are this project's."""
    url = str(cfg.get("public_url") or "").strip()
    if not url and cfg.get("deploy_mode") == "local":
        url = "http://localhost:%d/" % int(cfg.get("serve_port") or 8000)
    if not url:
        raise StatsUnavailable("public_url is not set: the address players update from tells which requests in "
                               "the log are this project's")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise StatsUnavailable("public_url is not an http(s) address: %s" % url)
    prefix = urllib.parse.unquote(parts.path or "/")
    return (prefix if prefix.endswith("/") else prefix + "/"), parts.hostname.lower()


def _read_local(path, counter):
    p = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(p):
        raise StatsUnavailable("there is no file %s on this computer (access_log)" % p)
    if os.path.getsize(p) > LOG_LIMIT:
        raise StatsUnavailable("the access log %s is %s, more than %s: rotate it more often" %
                               (p, human(os.path.getsize(p)), human(LOG_LIMIT)))
    try:
        with open(p, "rb") as f:
            counter.read(f)
    except OSError as e:
        raise StatsUnavailable("the access log %s cannot be read: %s" % (p, e.strerror or e))


def _read_remote(cfg, path, counter):
    """Read the log on the server over the project's SFTP connection, after the checks a deploy makes:
    a trusted host and a usable key. Nothing is asked here: what is missing is said instead."""
    try:
        _read_over_sftp(cfg, path, counter)
    except ssh.SshError as e:
        raise StatsUnavailable("the access log could not be read: %s" % e)


def _read_over_sftp(cfg, path, counter):
    if not str(cfg.get("ssh_host") or "").strip():
        raise StatsUnavailable("the server is not set (ssh_host): the access log is read from it")
    try:
        t = ssh.Target(cfg["ssh_host"], cfg["ssh_user"], cfg["ssh_port"], cfg["ssh_key"])
        remote = ssh.check_remote_path(path)
        get = ssh.q(remote)
    except ssh.SshError as e:
        raise StatsUnavailable("the server or access_log cannot be used: %s" % e)
    if not ssh.is_trusted(t):
        raise StatsUnavailable("the server %s is not trusted yet: confirm its host key first (Release, Check host key; "
                               "or deltapatcher ssh trust)" % t.known_name())
    if t.key and os.path.isfile(t.key):
        problems = ssh.permission_problems(t.key)
        if problems:
            raise StatsUnavailable("the private key %s can be read by other accounts (%s): deltapatcher ssh "
                                   "fix-permissions" % (t.key, "; ".join(problems)))
        try:
            locked = ssh.key_has_passphrase(t.key) and not ssh.agent_has(t.key)
        except ssh.SshError:
            locked = False
        if locked:
            raise StatsUnavailable("the SSH key %s is locked: unlock it first (Release, Unlock key; or deltapatcher "
                                   "ssh unlock)" % t.key)
    r = ssh.sftp(t, ["ls -ln %s" % get], timeout=120)
    if r.returncode != 0:
        low = ((r.stderr or "") + (r.stdout or "")).lower()
        if "not found" in low or "no such file" in low:
            raise StatsUnavailable("there is no file %s on the server (access_log)" % remote)
        raise StatsUnavailable("the access log could not be read: %s" % ssh.explain(r))
    size = ssh.parse_ls(r.stdout, blobs_only=False).get(posixpath.basename(remote))
    if size is None:
        raise StatsUnavailable("%s on the server is not a file (access_log)" % remote)
    if size > LOG_LIMIT:
        raise StatsUnavailable("the access log %s is %s, more than %s: rotate it more often" %
                               (remote, human(size), human(LOG_LIMIT)))
    d = tempfile.mkdtemp(prefix="dp-log-")
    try:
        r = ssh.sftp(t, ["get %s %s" % (get, ssh.q("access.log"))], cwd=d, timeout=600 + size // (50 * 1024))
        local = os.path.join(d, "access.log")
        if r.returncode != 0 or not os.path.isfile(local):
            low = ((r.stderr or "") + (r.stdout or "")).lower()
            if "permission denied" in low and "publickey" not in low:
                raise StatsUnavailable("the server does not let %s read %s: give that user read access to the log "
                                       "(on most servers: add it to the adm group)" % (t.user or "the deploy user", remote))
            raise StatsUnavailable("the access log could not be read: %s" % ssh.explain(r))
        with open(local, "rb") as f:
            counter.read(f)
    finally:
        shutil.rmtree(d, ignore_errors=True)       # the copy of the log goes at once


def collect_downloads(project, cfg, days):
    """Update checks, downloads, bytes and players per day, and the releases players are on, from the access
    log (access_log): on the server for an SFTP release, on this computer for a folder or local one."""
    path = str(cfg.get("access_log") or "").strip()
    if not path:
        raise StatsUnavailable("access_log is not set: name the web server's access log to see update checks, "
                               "downloads and players")
    prefix, host = public_path(cfg)
    counter = Counter(prefix, host, days, now())
    remote = (cfg.get("deploy_mode") or "sftp") == "sftp"
    if remote:
        _read_remote(cfg, path, counter)
    else:
        _read_local(path, counter)
    section = counter.section()
    section.update({"log": path, "read_from": "server" if remote else "this computer", "path": prefix})
    return section


register("releases", collect_releases)
register("downloads", collect_downloads)


# ---- in a terminal ----------------------------------------------------------------------------------

def _when(text):
    return str(text or "").replace("T", " ").replace("Z", " UTC")


def _n(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def text(doc):
    """The document in a few lines for a terminal: [(line, style)]."""
    out = [("Statistics of %s, the last %s" % (doc["project"]["project"], _n(doc["days"], "day")), "head")]
    rel = doc["sections"].get("releases")
    if rel:
        c = rel["current"]
        out.append(("Release %s%s, published %s: %s, %s (%d distinct)" % (
            c["version"] or "(no version)", " (%s)" % c["name"] if c["name"] else "", _when(c["published"]),
            _n(c["files"], "file"), human(c["bytes"]), c["blobs"]), None))
        ch = rel["change"]
        if ch:
            out.append(("  from %s: %d new, %d changed, %d removed; a player on it downloads %s" % (
                ch["from"]["version"] or "the release before", ch["new"], ch["changed"], ch["removed"],
                human(ch["download_bytes"])), None))
        out.append(("  the store: %s in %s; %s in its history" % (
            human(rel["store_bytes"]), _n(rel["store_files"], "file"), _n(rel["history_total"], "release")), None))
    dl = doc["sections"].get("downloads")
    if dl:
        t = dl["total"]
        out.append(("Downloads, from %s (%s): %s, %s (%s), %s" % (
            dl["log"], dl["read_from"], _n(t["checks"], "update check"), _n(t["downloads"], "download"),
            human(t["bytes"]), _n(t["players"], "player")), None))
        if dl["releases"]:
            out.append(("  players are on: " + ", ".join("%s (%d)" % (r["release"] or "unknown", r["players"])
                                                       for r in dl["releases"]) +
                        (", others (%d)" % dl["releases_more"] if dl["releases_more"] else ""), None))
        busiest = max(dl["days"], key=lambda d: (d["checks"], d["date"]))
        if busiest["checks"]:
            out.append(("  the busiest day: %s, %s from %s" % (
                busiest["date"], _n(busiest["checks"], "update check"), _n(busiest["players"], "player")), None))
        if dl["unreadable"]:
            out.append(("  %d of %d lines of the log could not be read" % (dl["unreadable"], dl["lines"]), "warn"))
    for name, why in doc["problems"].items():
        out.append(("No %s: %s" % (name, why), "warn"))
    return out
