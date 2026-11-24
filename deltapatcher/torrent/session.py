"""The one module that loads libtorrent, and only when it is first used: sessions with DeltaPatcher's settings,
torrents read and added, the BitTorrent side of an update, and what seeding needs.

libtorrent's own defaults are unsafe for DeltaPatcher (it would listen on 0.0.0.0:6881, start its DHT from
dht.libtorrent.org, and ask the router for a port with UPnP and NAT-PMP), so every session is made from one
settings pack given when it is made (pack()), and nothing of it ever runs with a default:
  - listen_interfaces always named: 127.0.0.1 for an update that only downloads (others cannot reach it, so no
    firewall asks; it still connects out, over TCP: uTP rides on the listening socket and is off there), every
    interface for a seed run or a project with no address;
  - UPnP and NAT-PMP as the person set general.torrent_port_mapping (off unless they turn it on), never for
    127.0.0.1; local discovery (LSD) only while seeding, and never in a test on one computer (libtorrent's LSD
    listens on every interface, port 6771, whatever the session listens on); the DHT only for a project with no
    address, with no
    bootstrap node of its own (dht_bootstrap_nodes is empty: nodes come only from the project's seeds and the
    peer cache), and read-only unless the run seeds;
  - no tracker ever: torrents are made without one, and a torrent read from anywhere has its trackers, web
    seeds and DHT nodes taken out (read_torrent), and is added with trackers and web seeds overridden (the
    address is the update's floor through its own HTTP: AddressTorrent says why it is no web seed);
  - the peer id and user agent say DeltaPatcher and its version; protocol encryption on, not forced;
  - the person's upload limit (general.seed_upload_kbps); torrents never auto-managed (those start paused).
"""

import collections
import concurrent.futures
import hashlib
import ipaddress
import os
import re
import secrets
import shutil
import socket
import threading
import time

from .. import USER_AGENT, __version__
from ..source import SourceError
from . import (ITEM_FILE, NO_SUPPORT, RELEASE_DIR, TORRENT_FILE, Kept, TorrentError, bencode, folder, item, krpc,
               make, pause, pid_alive, test_ip)

STALL = 30.0                 # seconds without progress before the swarm is given up (the address takes over)
GRACE = 5.0                  # seconds to find and reach peers before a blob none of them has comes from the address
FIND = 60.0                  # seconds to look for a release item in the DHT
SNAPSHOT = 30.0              # seconds between snapshots of the peers met, for the peer cache
RACE_AFTER = 10.0            # seconds before a slow swarm has the address send blobs too (the first to finish wins)
RATE_FLOOR = 256 * 1024      # bytes a second: a swarm slower than this has the address send blobs at the same time
WINDOW = 5.0                 # seconds over which the swarm's speed is measured
WORK = "torrent"             # <state>/torrent/<pid>.<random>/: a work folder of one finding of a release
PE_ENABLED = 1               # libtorrent's enc_policy: encryption offered and accepted, not forced
ENC_BOTH = 3                 # libtorrent's enc_level: plain text and RC4 both accepted
# the alerts a wait() looks for; what they say is read as they come (Seen)
WAITED = ("torrent_removed_alert", "file_renamed_alert", "file_rename_failed_alert", "cache_flushed_alert",
          "torrent_paused_alert", "save_resume_data_alert", "save_resume_data_failed_alert", "metadata_received_alert")

_LT = []


def lt():
    """libtorrent, loaded on first use; TorrentError(NO_SUPPORT) when it is not there."""
    if not _LT:
        try:
            import libtorrent
        except ImportError:
            raise TorrentError(NO_SUPPORT, "no-torrent-support")
        _LT.append(libtorrent)
    return _LT[0]


def fingerprint():
    """The start of DeltaPatcher's peer id: -DP<version>-, as BitTorrent clients write theirs (0.3.0: -DP0300-)."""
    digits = [int(x) for x in re.findall(r"\d+", __version__)][:4]
    digits += [0] * (4 - len(digits))
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "-DP%s-" % "".join(chars[min(d, 35)] for d in digits)


def listen_on(kind, port=0, ip=None):
    """listen_interfaces: "loopback" (an update that only downloads: 127.0.0.1), or "all" (a seed run, or a
    project with no address: every interface, IPv4 and IPv6, on `port`). `ip` (or DELTAPATCHER_TORRENT_TEST_IP):
    that one address instead, for tests on one computer."""
    ip = ip or test_ip()
    if ip:
        return "%s:%d" % (ip, port)
    if kind == "loopback":
        return "127.0.0.1:0"
    return "0.0.0.0:%d,[::]:%d" % (port, port)


def pack(listen, dht=False, dht_read_only=True, lsd=False, port_mapping=False, upload_kbps=0, ip=None):
    """The settings every session is made with (see the top of this file)."""
    lt_ = lt()
    cat = lt_.alert.category_t
    loopback_only = listen.startswith("127.0.0.1:")
    ip = ip or test_ip()
    s = {
        "listen_interfaces": listen,
        "outgoing_interfaces": ip or "",
        "enable_outgoing_utp": not loopback_only,
        "enable_incoming_utp": not loopback_only,
        "enable_dht": bool(dht),
        "dht_bootstrap_nodes": "",
        "dht_read_only": bool(dht and dht_read_only),
        "enable_lsd": bool(lsd) and not loopback_only and not ip,
        "enable_upnp": bool(port_mapping) and not loopback_only and not ip,
        "enable_natpmp": bool(port_mapping) and not loopback_only and not ip,
        "user_agent": USER_AGENT,
        "handshake_client_version": USER_AGENT,
        "peer_fingerprint": fingerprint(),
        "out_enc_policy": PE_ENABLED,
        "in_enc_policy": PE_ENABLED,
        "allowed_enc_level": ENC_BOTH,
        "upload_rate_limit": max(0, int(upload_kbps or 0)) * 1024,
        "anonymous_mode": False,
        "alert_mask": int(cat.error_notification | cat.status_notification | cat.storage_notification
                          | cat.dht_notification | cat.peer_notification | cat.port_mapping_notification),
        "alert_queue_size": 10000,
    }
    if ip:
        # every test node shares 127.0.0.0/8: one /24 per node would be the rule on the internet
        s.update({"dht_restrict_routing_ips": False, "dht_restrict_search_ips": False,
                  "allow_multiple_connections_per_ip": True})
    return s


_OPEN_FILES = []


def _open_files():
    """On POSIX, the soft limit of open files raised once a process, before its first session: to the hard limit, at
    most 4096 (4096 when the hard one has no limit). macOS starts every process at 256 (a terminal's, an app's and
    the engine it starts), and a seed run's peers and files take more. Never lowered; a failure changes nothing."""
    if _OPEN_FILES:
        return
    _OPEN_FILES.append(True)
    try:
        import resource
    except ImportError:                     # Windows: no such limit
        return
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = 4096 if hard == resource.RLIM_INFINITY else min(hard, 4096)
        if soft != resource.RLIM_INFINITY and soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    except (ValueError, OSError):
        pass


def make_session(settings):
    """A session made with these settings in its constructor, listening before it is returned (5 s at most)."""
    _open_files()
    ses = lt().session(settings)
    end = time.time() + 5
    while ses.listen_port() == 0 and time.time() < end:
        time.sleep(0.02)
    return ses


def _bytes(x):
    if isinstance(x, bytes):
        return x
    if hasattr(x, "to_bytes"):
        return x.to_bytes()
    return bytes(x)


def _plain(obj):
    """A value libtorrent decoded, with every text as bytes (the binding gives str or bytes)."""
    if isinstance(obj, str):
        return obj.encode("utf-8")
    if isinstance(obj, dict):
        return {_plain(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_plain(v) for v in obj]
    return obj


# ---- torrents read ------------------------------------------------------------------------------------

def files_of(ti):
    """The files of a torrent_info as folder.TFile, with paths inside the torrent (its name taken off)."""
    fs = ti.files()
    plen = ti.piece_length()
    out = []
    for i in range(fs.num_files()):
        pad = bool(fs.file_flags(i) & fs.flag_pad_file)
        path = fs.file_path(i).replace("\\", "/")
        rel = path.split("/", 1)[1] if "/" in path else path
        size = fs.file_size(i)
        first = fs.piece_index_at_file(i) if size else 0
        try:
            root = _bytes(fs.root(i))
            root = None if root == b"\0" * 32 else root
        except (RuntimeError, ValueError, AttributeError):
            root = None
        out.append(folder.TFile(i, rel, size, first, (size + plen - 1) // plen if size else 0, pad, root))
    return out


def clean_torrent(data):
    """A release's torrent as it is read and kept: only its info and piece layers (a tracker, a web seed, a DHT node
    or any other key of it is dropped), written the one way bencoding allows, its info the one its bytes name.
    TorrentError when it is not readable."""
    try:
        doc = bencode.decode(data)
        info = doc[b"info"]
        if not isinstance(info, dict):
            raise KeyError("info")
        clean = {b"info": info}
        if isinstance(doc.get(b"piece layers"), dict):
            clean[b"piece layers"] = doc[b"piece layers"]
        if make.infohash(data) != hashlib.sha256(bencode.encode(info)).digest():
            raise KeyError("info")
        return bencode.encode(clean)
    except (bencode.BencodeError, ValueError, KeyError, TypeError, make.MakeError):
        raise TorrentError("the release's torrent is not readable", "bad-torrent")


def read_torrent(data, name=None):
    """(torrent_info, files) of a release's torrent read from anywhere: only its info and piece layers are kept (no
    tracker, web seed or DHT node of it is ever used: clean_torrent), it must be a v2 torrent of one release
    (folder.layout_problem) written the one way bencoding allows, and named `name` when given. TorrentError
    otherwise."""
    raw = clean_torrent(data)
    try:
        ti = lt().torrent_info(raw)
    except RuntimeError as e:
        raise TorrentError("the release's torrent is not readable (%s)" % e, "bad-torrent")
    return checked(ti, name)


def checked(ti, name=None):
    ih = ti.info_hashes()
    if not ih.has_v2() or ih.has_v1():
        raise TorrentError("the release's torrent is not a BitTorrent v2 torrent", "bad-torrent")
    if name is not None and ti.name() != name:
        raise TorrentError("the release's torrent is named %s, not %s" % (ti.name(), name), "bad-torrent")
    files = files_of(ti)
    problem = folder.layout_problem(files)
    if problem:
        raise TorrentError("the release's torrent is not a release: %s" % problem, "bad-torrent")
    return ti, files


def infohash_of(ti):
    return _bytes(ti.info_hashes().v2)


def _infohash_or_none(data):
    """The v2 infohash of a torrent's bytes; None when they are not a torrent (a damaged copy is as good as none)."""
    try:
        return make.infohash(data) if data else None
    except make.MakeError:
        return None


LOOKUP = 10.0                # seconds a name's lookup may take when no deadline is given


def _lookup(host, port, seconds):
    """getaddrinfo of host:port; a name (not an address) is looked up in a thread of its own and given up after
    `seconds`, as a system resolver can hang well past any deadline. [] when it cannot be had."""
    try:
        ipaddress.ip_address(host)
        numeric = True
    except ValueError:
        numeric = False
    if numeric:
        try:
            return socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        except (OSError, ValueError, UnicodeError):
            return []
    got = []

    def look():
        try:
            got.append(socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM))
        except (OSError, ValueError, UnicodeError):
            pass
    t = threading.Thread(target=look, name="deltapatcher-lookup", daemon=True)
    t.start()
    t.join(max(0.0, seconds))
    return got[0] if got else []


def resolve(seeds, deadline=None, cancel=None):
    """(ip, port) of each host:port that can be resolved now (a name that cannot is left out). It ends at
    `deadline` (a time.time()) or a stop (`cancel`), with what it has by then; a lookup that hangs is given up at
    the deadline (LOOKUP seconds without one)."""
    out = []
    for s in seeds:
        if (deadline is not None and time.time() >= deadline) or (cancel is not None and cancel.is_set()):
            break
        host, _sep, port = s.rpartition(":")
        host = host.strip("[]")
        try:
            port = int(port)
        except ValueError:
            continue
        infos = _lookup(host, port, (deadline - time.time()) if deadline is not None else LOOKUP)
        for fam, _t, _p, _c, addr in infos:
            if fam in (socket.AF_INET, socket.AF_INET6):
                ep = (addr[0], addr[1])
                if ep not in out:
                    out.append(ep)
                break
    return out


def endpoint_text(ep):
    host, port = ep[0], ep[1]
    return ("[%s]:%d" % (host, port)) if ":" in host else "%s:%d" % (host, port)


def add_dht_nodes(swarm, seeds):
    """Start the DHT from these host:port only: the project's seeds and the peers met before."""
    for s in seeds:
        host, _sep, port = s.rpartition(":")
        try:
            swarm.ses.add_dht_node((host.strip("[]"), int(port)))
        except (RuntimeError, ValueError, TypeError):
            pass


# ---- a session and what is seen of it -----------------------------------------------------------------

class Seen(object):
    """What an alert said, read when it came: libtorrent's alert objects are gone at the next pop_alerts()."""

    __slots__ = ("kind", "handle", "index", "info_hashes", "peers")

    def __init__(self, a):
        self.kind = type(a).__name__
        self.handle = self.index = self.info_hashes = self.peers = None
        try:
            self.handle = a.handle
        except (AttributeError, RuntimeError):
            pass
        if self.kind in ("file_renamed_alert", "file_rename_failed_alert"):
            self.index = a.index
        elif self.kind == "torrent_removed_alert":
            self.info_hashes = a.info_hashes
        elif self.kind == "save_resume_data_alert":
            try:
                self.peers = [endpoint_text(ep) for ep in a.params.peers]
            except (AttributeError, RuntimeError, TypeError):
                self.peers = []

    def of(self, kind, h=None):
        return self.kind == kind and (h is None or (self.handle is not None and self.handle == h))


class Swarm(object):
    """A libtorrent session made with pack(), and the alerts it posts: damaged data and bans are said once."""

    def __init__(self, kind, port=0, dht=False, dht_read_only=True, lsd=False, port_mapping=False, upload_kbps=0,
                 ip=None, log=None):
        self.log = log or (lambda s: None)
        self.ip = ip or test_ip()
        self.settings = pack(listen_on(kind, port, self.ip), dht=dht, dht_read_only=dht_read_only, lsd=lsd,
                             port_mapping=port_mapping, upload_kbps=upload_kbps, ip=self.ip)
        self.ses = make_session(self.settings)
        if self.ip:
            # tests on one computer: every address in the global peer class, so upload limits apply between them
            # as they do between computers (libtorrent exempts local addresses)
            L = lt()
            f = L.ip_filter()
            f.add_rule("0.0.0.0", "255.255.255.255", 1 << L.session.global_peer_class_id)
            self.ses.set_peer_class_filter(f)
        self.seen = []                   # alerts kept until a wait() looks at them
        self.said = set()
        self.listen_errors = []

    @property
    def port(self):
        return self.ses.listen_port()

    def listen_anywhere(self):
        """When the port asked for cannot be had (another program has it): any free port instead."""
        self.settings["listen_interfaces"] = listen_on("all", 0, self.ip)
        self.ses.apply_settings({"listen_interfaces": self.settings["listen_interfaces"]})
        end = time.time() + 5
        while self.ses.listen_port() == 0 and time.time() < end:
            time.sleep(0.02)
        return self.ses.listen_port()

    def pump(self, ms=100):
        """What the alerts that came said (Seen), after waiting up to `ms` milliseconds for one. Only the kinds a
        wait() looks for are kept."""
        self.ses.wait_for_alert(ms)
        got = []
        for a in self.ses.pop_alerts():
            kind = type(a).__name__
            if kind == "hash_failed_alert" and "damaged" not in self.said:
                self.said.add("damaged")
                self.log("a peer sent data that fails its hash: it is not used, and the peer is banned for this run")
            elif kind == "peer_ban_alert" and "banned" not in self.said:
                self.said.add("banned")
                self.log("a peer was banned for this run: it sent data that fails its hash")
            elif kind == "listen_failed_alert":
                self.listen_errors.append(a.message())
            elif kind in WAITED:
                got.append(Seen(a))
        self.seen.extend(got)
        if len(self.seen) > 5000:
            del self.seen[:-1000]
        return got

    def wait(self, pred, seconds, cancel=None):
        """Pump alerts until pred(seen) holds for what one of them said (Seen); that, or None after `seconds` (or a
        stop)."""
        end = time.time() + seconds
        while True:
            for i, a in enumerate(self.seen):
                if pred(a):
                    del self.seen[i]
                    return a
            if time.time() >= end or (cancel is not None and cancel.is_set()):
                return None
            self.pump(100)

    def forget(self):
        self.seen = []

    def add(self, ti=None, infohash=None, save_path=".", mapping=None, peers=(), web_seeds=(), seed=False,
            dht=False, dont_download=False):
        """Add a torrent (its torrent_info, or only its v2 infohash: the info comes from peers) the DeltaPatcher way:
        not auto-managed, started, no tracker, only the web seeds given; a seed in upload mode with the pieces the
        mapping says it has, never checked by libtorrent and never written to."""
        L = lt()
        atp = L.add_torrent_params()
        if ti is not None:
            atp.ti = ti
        else:
            atp.info_hashes = L.info_hash_t(L.sha256_hash(bytes(infohash)))
        atp.save_path = os.path.abspath(save_path)
        if mapping is not None:
            atp.renamed_files = dict(mapping.renamed)
            atp.file_priorities = list(mapping.priorities)
        if seed and mapping is not None:
            atp.have_pieces = list(mapping.have)
        atp.peers = list(peers)
        atp.trackers = []
        atp.url_seeds = list(web_seeds)
        flags = atp.flags & ~L.torrent_flags.paused & ~L.torrent_flags.auto_managed
        flags |= L.torrent_flags.override_trackers | L.torrent_flags.override_web_seeds
        if seed:
            flags |= L.torrent_flags.upload_mode | L.torrent_flags.no_verify_files
        if not dht:
            flags |= L.torrent_flags.disable_dht
        if dont_download:
            flags |= L.torrent_flags.default_dont_download
        atp.flags = flags
        return self.ses.add_torrent(atp)

    def remove(self, h, seconds=5.0):
        try:
            ih = h.info_hashes()
            self.ses.remove_torrent(h)
        except RuntimeError:
            return
        self.wait(lambda a: a.kind == "torrent_removed_alert" and a.info_hashes == ih, seconds)

    def close_files(self, h):
        """A torrent out of the session with its files closed first (libtorrent says a torrent is paused once its
        files are closed; a removal alone does not wait for that), so they can be renamed or replaced at once."""
        self.pause(h, 3.0)
        self.remove(h)

    def rename(self, h, renamed, seconds=10.0):
        """Rename files of a torrent ({index: path}) and wait until libtorrent has, before any of them is written."""
        left = set(renamed)
        for i, rel in renamed.items():
            h.rename_file(i, rel)
        end = time.time() + seconds
        while left and time.time() < end:
            a = self.wait(lambda a: (a.of("file_renamed_alert", h) or a.of("file_rename_failed_alert", h)),
                          max(0.1, end - time.time()))
            if a is None:
                break
            left.discard(a.index)
        return not left

    def flush(self, h, seconds=10.0):
        """Everything libtorrent wrote of this torrent is on disk (its cache flushed), before anything reads it."""
        h.flush_cache()
        self.wait(lambda a: a.of("cache_flushed_alert", h), seconds)

    def pause(self, h, seconds=5.0):
        """The torrent paused and its files closed (libtorrent posts torrent_paused_alert once they are)."""
        self.forget()
        h.pause()
        return self.wait(lambda a: a.of("torrent_paused_alert", h), seconds) is not None

    def snapshot(self, h, seconds=3.0):
        """The connectable peers of a torrent right now (host:port), from libtorrent's resume data while connected:
        it drops peers once they disconnect."""
        try:
            h.save_resume_data(0)
        except RuntimeError:
            return []
        a = self.wait(lambda a: a.of("save_resume_data_alert", h) or a.of("save_resume_data_failed_alert", h), seconds)
        return (a.peers or []) if a is not None else []

    def own(self):
        """This session's own addresses as peers would name them (left out of the peer cache)."""
        port = self.port
        names = {"127.0.0.1:%d" % port}
        if self.ip:
            names.add("%s:%d" % (self.ip, port))
        try:
            for fam, _t, _p, _c, addr in socket.getaddrinfo(socket.gethostname(), None):
                names.add(endpoint_text((addr[0], port)))
        except OSError:
            pass
        return names

    def dht_nodes(self):
        """The DHT nodes this session knows now: (ip, port) pairs, IPv4 only."""
        try:
            state = self.ses.save_state()
            nodes = state.get(b"dht state", {}).get(b"nodes", b"")
        except (RuntimeError, AttributeError, TypeError):
            return []
        out = []
        if isinstance(nodes, list):
            nodes = b"".join(n for n in nodes if isinstance(n, bytes))
        for i in range(0, len(nodes) - len(nodes) % 6, 6):
            ep = (socket.inet_ntoa(nodes[i:i + 4]), int.from_bytes(nodes[i + 4:i + 6], "big"))
            if ep[1]:
                out.append(ep)
        return out

    def get_items(self, key_bytes, torrent_id, key_text, seeds, seconds=FIND, cancel=None):
        """Release items in the DHT for this publisher key and torrent id, each checked (item.check), read with the
        small DHT client (krpc.py) from the seeds and the nodes this session knows; asked again every few seconds
        until one is found or `seconds` pass."""
        found, refused = [], []
        end = time.time() + seconds
        target = item.target(key_bytes, torrent_id)
        salt = item.salt_of(torrent_id)
        while time.time() < end and not (cancel is not None and cancel.is_set()):
            from_seeds = resolve(seeds, end, cancel)              # once a round: a name may point elsewhere later
            start = from_seeds + [ep for ep in self.dht_nodes() if ep not in from_seeds]
            for d in krpc.get_items(target, start, self.ip or "0.0.0.0", min(10.0, max(1.0, end - time.time())),
                                    stop=cancel):
                try:
                    found.append(item.check(d[b"k"], salt, d[b"seq"], d[b"sig"], d[b"v"], key_text, torrent_id))
                except TorrentError as e:
                    refused.append(str(e))
            if found:
                break
            self.pump(1000)
        for why in refused[:1]:
            self.log("Warning: %s" % why)
        return found

    def close(self):
        """Every torrent out and the session ended; libtorrent waits for its network thread (about a second)."""
        try:
            self.ses.pause()
            for h in self.ses.get_torrents():
                self.ses.remove_torrent(h)
        except RuntimeError:
            pass
        ses, self.ses = self.ses, None
        del ses


# ---- the BitTorrent side of an update ---------------------------------------------------------------------

MEASURED_FROM = 256 * 1024   # bytes: a download from the address shorter than this says little of its speed


def _speed(measured):
    """The bytes a second one download from the address gets, from its downloads so far that were long enough to
    tell ((bytes, seconds) each); None before there is one."""
    done = [(b, s) for b, s in list(measured) if b >= MEASURED_FROM and s > 0]
    if not done:
        return None
    return sum(b for b, _s in done) / sum(s for _b, s in done)


class _Either(object):
    """The stop of one blob's download from the address: the update's stop, or the swarm having brought it first."""

    def __init__(self, *events):
        self.events = [e for e in events if e is not None]

    def is_set(self):
        return any(e.is_set() for e in self.events)


def get_blobs(swarm, ti, files, wanted, cdir, peers, log, progress, cancel, kept, own_dht=False, floor=None,
              workers=4):
    """Download the blobs `wanted` ({sha: size}) of a torrent into the folder `cdir`, as <sha>.bt, until each one is
    there (from the swarm, or from the address), the swarm makes no progress for STALL seconds, or a stop.
    Everything is flushed to disk and the torrent taken out before it returns, and each blob the swarm brought whole
    becomes cdir/<sha> (the update checks each one's SHA-256 before it is used). Returns ({sha: size} of those from
    the swarm, [sha] from the address because no peer had them, [sha] from the address because it was first).

    floor(sha, size, stop): fetches a blob into cdir/<sha> from the address (the update's own HTTP), until
    stop.is_set(). The address is the swarm's floor, in what it has and in speed, `workers` blobs at a time:
      - a blob no connected peer has GRACE seconds after the start (time to find and reach peers) comes from the
        address, and the swarm leaves it;
      - the address's speed is measured from its own downloads in this run (the bytes a second one download of it
        gets). From RACE_AFTER seconds on, while the swarm brings less than half of that (over the last WINDOW
        seconds; RATE_FLOOR until the address is measured), the blobs the swarm has done least of come from the
        address too, as many at once as it has free downloads; so does a blob the swarm has not finished RACE_AFTER
        seconds, plus the time half the address's speed takes for its size, after the start. When nothing has
        measured the address by RACE_AFTER, the blob the swarm has done least of, of those of MEASURED_FROM bytes or
        more, comes from it, to measure it;
      - the first to finish a blob wins: the address's download stops when the swarm is first, and the swarm
        leaves the blob when the address is.
    What holds: once the swarm is the slower, the address works at its own speed on what the swarm has done least
    of, so an update takes at most about RACE_AFTER seconds more than its address alone would; a swarm faster than
    half the address is left to it. A blob both finish at the same moment was brought twice (one copy is dropped).
    Whatever is still missing when the swarm stalls is left to the update's own download."""
    os.makedirs(cdir, exist_ok=True)
    m = folder.for_download(files, wanted, ti.num_pieces())
    if not m.offered:
        return {}, [], []
    h = swarm.add(ti, save_path=cdir, mapping=m, peers=peers, dht=own_dht)
    mine = [f for f in files if f.path in m.offered]
    sha_of = {f.index: folder.blob_of(f.path) for f in mine}
    total = sum(f.size for f in mine)
    taken = {}                       # file index: (its download from the address, that download's stop, why)
    done_floor = set()               # shas the address brought
    left = set()                     # file indexes the swarm was told to leave (priority 0)
    rates = collections.deque()      # (time, bytes the swarm had brought then), for its speed
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) if floor is not None else None
    started = time.time()
    swarm_before, last_move, last_snap, last_look = -1, time.time(), time.time(), 0.0
    said_slow = False
    final = None
    measured = []                    # (bytes, seconds) of the address's downloads: its speed
    log("downloading %d blobs by torrent (%s)" % (len(mine), _human(total)))

    def by_floor(f, sha, stop):
        if stop.is_set():
            return
        t0 = time.time()
        try:
            floor(sha, f.size, stop)
            done_floor.add(sha)
            measured.append((f.size, time.time() - t0))
        except Exception:                   # stopped, or failed: the update's own download tries it again if needed
            try:
                partly = os.path.getsize(os.path.join(cdir, sha + ".part"))      # a stopped one measures it too
            except OSError:
                partly = 0
            if partly:
                measured.append((partly, time.time() - t0))

    def take(f, why):
        mine_stop = threading.Event()
        taken[f.index] = (pool.submit(by_floor, f, sha_of[f.index], _Either(cancel, mine_stop)), mine_stop, why)

    try:
        while not (cancel is not None and cancel.is_set()):
            swarm.pump(250)
            st = h.status()
            if st.errc.value():
                log("the torrent stopped: %s" % st.errc.message())
                break
            now = time.time()
            prog = h.file_progress(flags=1)
            for f in mine:                                      # a race: the first to finish the blob wins
                t = taken.get(f.index)
                if t is None:
                    continue
                if prog[f.index] >= f.size and not t[0].done():
                    t[1].set()
                elif sha_of[f.index] in done_floor and f.index not in left:
                    h.file_priority(f.index, 0)
                    left.add(f.index)
            outstanding = [f for f in mine if prog[f.index] < f.size and sha_of[f.index] not in done_floor]
            swarm_bytes = sum(min(prog[f.index], f.size) for f in mine)
            rates.append((now, swarm_bytes))
            while now - rates[0][0] > WINDOW:
                rates.popleft()
            if pool is not None and now - started >= GRACE and now - last_look >= 1.0:
                last_look = now
                avail = h.piece_availability()
                for f in outstanding:
                    if f.index not in taken and f.pieces and \
                            not any(avail[p] for p in range(f.first, min(f.first + f.pieces, len(avail)))):
                        take(f, "none")
                        h.file_priority(f.index, 0)
                        left.add(f.index)
                if now - started >= RACE_AFTER:
                    span = now - rates[0][0]
                    rate = (swarm_bytes - rates[0][1]) / span if span >= 1.0 else None
                    address = _speed(measured)
                    floor_rate = address / 2.0 if address else float(RATE_FLOOR)
                    running = sum(1 for t in taken.values() if not t[0].done())
                    least = sorted((f for f in outstanding if f.index not in taken),
                                   key=lambda f: prog[f.index] / float(f.size or 1))
                    due = [f for f in least if now - started >= RACE_AFTER + f.size / floor_rate]
                    slow = rate is not None and rate < floor_rate
                    if slow:
                        due += [f for f in least if f not in due]
                    elif address is None and not running and not due:
                        # nothing measured the address yet: the blob the swarm has done least of, of those long
                        # enough to tell, does
                        due = [f for f in least if f.size >= MEASURED_FROM][:1]
                    chosen = due[:max(0, workers - running)]
                    for f in chosen:
                        take(f, "race")
                    if chosen and slow and not said_slow:
                        said_slow = True
                        log("the swarm is slow (%s/s%s): the address sends blobs too, and the first to bring one wins"
                            % (_human(int(rate or 0)), (", the address %s/s" % _human(int(address))) if address else ""))
            progress(sum(f.size if sha_of[f.index] in done_floor else min(prog[f.index], f.size) for f in mine),
                     total, "downloading")
            if not outstanding:
                break
            if swarm_bytes != swarm_before:
                swarm_before, last_move = swarm_bytes, now
            if not [f for f in outstanding if f.index not in left]:
                if all(t[0].done() for t in taken.values()):
                    break                           # the address failed for what is left: the update tries it again
                last_move = now                     # only the address is busy: the swarm has nothing left to stall on
            elif now - last_move > STALL:
                log("the swarm made no progress for %d seconds" % int(STALL))
                break
            if now - last_snap > SNAPSHOT:
                kept.met(swarm.snapshot(h), swarm.own())
                last_snap = time.time()
        kept.met(swarm.snapshot(h), swarm.own())
        _count_sources(h, log)
        swarm.flush(h)
        final = h.file_progress(flags=1)
    finally:
        try:
            sizes = {f.index: f.size for f in mine}
            for i, t in taken.items():
                if final is not None and final[i] >= sizes[i]:
                    t[1].set()                      # the swarm brought it: the address need not
            swarm.close_files(h)
        finally:
            if pool is not None:
                pool.shutdown(wait=True)            # whatever happened above: no download from the address is left
    got, by_none, by_race = {}, [], []
    for f in mine:
        sha = sha_of[f.index]
        part = os.path.join(cdir, sha + ".bt")
        try:
            if sha in done_floor:
                (by_none if taken[f.index][2] == "none" else by_race).append(sha)
                if os.path.lexists(part):
                    os.remove(part)
            elif final is not None and final[f.index] >= f.size and os.path.isfile(part) and \
                    os.path.getsize(part) == wanted[sha]:
                os.replace(part, os.path.join(cdir, sha))
                got[sha] = wanted[sha]
                if os.path.lexists(os.path.join(cdir, sha + ".part")):
                    os.remove(os.path.join(cdir, sha + ".part"))     # what a stopped download from the address left
            elif os.path.lexists(part):
                os.remove(part)
        except OSError:
            pass
    return got, sorted(by_none), sorted(by_race)


def _count_sources(h, log):
    try:
        peers = h.get_peer_info()
    except RuntimeError:
        return
    if peers:
        log("connected to %d peer%s" % (len(peers), "" if len(peers) == 1 else "s"))


def _human(n):
    from ..publish import human
    return human(n)


class AddressTorrent(object):
    """An update's BitTorrent side for a project with an address: the release's torrent from the address
    (release.torrent), and the blobs the folder lacks from peers, while the address stays the floor: a blob no
    peer has comes from it at the same time, and whatever the swarm has not brought when it makes no progress for
    STALL seconds comes from it the usual way, so an update never fails because of torrents. After the update the
    folder keeps the release (keep()), for seeding.

    The address is the floor through the update's own HTTP, not handed to libtorrent as a web seed: libtorrent
    2.0's web seed takes the start of the next answer on a kept-open connection (every web server's way) for the
    padding a v2 torrent puts after each file, so every file after the first fails its hash."""

    def __init__(self, tinfo, key, state_dir, general, log=None, workers=4):
        self.tinfo = tinfo
        self.key = key
        self.state_dir = state_dir
        self.general = general
        self.log = log or (lambda s: None)
        self.torrent = None
        self.item_bytes = None
        self.workers = max(1, int(workers or 4))

    def _torrent(self, src):
        if self.torrent is None:
            data = src.get_file(TORRENT_FILE, 64 << 20)
            self.torrent = data or b""
        return self.torrent

    def _item(self, src):
        if self.item_bytes is None:
            self.item_bytes = src.get_file(ITEM_FILE, 4096) or b""
        return self.item_bytes

    def _problem(self, data, item_bytes, raw, key):
        """Why the release's torrent from the address is not this release's ('' when it is): its release item must
        be signed by the publisher key, name this manifest, and name this torrent. Checked before libtorrent reads
        the torrent at all."""
        if not item_bytes:
            return "the release's torrent comes without its release item"
        if not key:
            return "the release's torrent cannot be checked: no publisher key signs this release"
        if raw is None:
            return "the release's torrent cannot be checked against the release"
        it = item.read(item_bytes, key, self.tinfo["id"])                 # TorrentError: a refused item
        if it.m != hashlib.sha256(raw).hexdigest():
            return "the release's torrent and item are another release's (one going up now?)"
        if _infohash_or_none(data) != it.ih:
            return "the release's torrent is not the one its release item names"
        return ""

    def fetch(self, src, need, cdir, progress, cancel, raw=None, signer=None):
        try:
            data = self._torrent(src)
            item_bytes = self._item(src) if data else b""
        except SourceError as e:
            self.log("the release's torrent could not be read (%s): everything comes from the address" % e)
            return
        if not data:
            self.log("the release has no torrent: everything comes from the address")
            return
        try:
            problem = self._problem(data, item_bytes, raw, signer or self.key)
            if problem:
                self.log("%s: everything comes from the address" % problem)
                return
            ti, files = read_torrent(data, self.tinfo["name"])
        except TorrentError as e:
            self.log("Warning: %s: everything comes from the address" % e)
            return
        kept = Kept(self.state_dir, self.tinfo["id"])
        peers = resolve(self.tinfo["seeds"] + kept.peers, time.time() + 10, cancel)

        def floor(sha, size, stop):
            src.fetch_blob(sha, os.path.join(cdir, sha), size, None, stop)
        swarm = Swarm("loopback", port_mapping=False, upload_kbps=self.general.get("seed_upload_kbps", 0), log=self.log)
        try:
            got, by_none, by_race = get_blobs(swarm, ti, files, need, cdir, peers, self.log, progress, cancel, kept,
                                              floor=floor, workers=self.workers)
        finally:
            swarm.close()
            kept.save()
        rest = len(need) - len(got) - len(by_none) - len(by_race)
        self.log("by torrent: %d of %d blobs%s%s%s" % (
            len(got), len(need), ("; from the address at the same time: %d (no peer had them)" % len(by_none))
            if by_none else "", ("; from the address, first to finish them: %d (the swarm was slow)" % len(by_race))
            if by_race else "", ("; the other %d come from the address" % rest) if rest else ""))

    def needs_keep(self, raw):
        """The folder does not keep this release, with its torrent and item, yet."""
        kraw, _sig, tor, it = read_kept(self.state_dir, self.key, self.tinfo["id"])
        return kraw != raw or not tor or it is None or it.m != hashlib.sha256(raw).hexdigest()

    def keep(self, src, raw, sig, signer):
        """The release the folder has now, kept in .deltapatcher/release/ for seeding: the manifest and signature,
        and the release's torrent and item when they belong to this manifest (the item signed by the publisher
        key, naming this manifest and this torrent)."""
        files = {}
        try:
            data = self._torrent(src)
            item_bytes = self._item(src)
        except SourceError:
            data, item_bytes = b"", None
        if data and item_bytes and (signer or self.key):
            try:
                it = item.read(item_bytes, signer or self.key, self.tinfo["id"])
                if it.m == hashlib.sha256(raw).hexdigest() and make.infohash(data) == it.ih:
                    # the torrent as it is read (clean_torrent), never the bytes the address gave
                    files = {TORRENT_FILE: clean_torrent(data), ITEM_FILE: it.to_bytes()}
            except (TorrentError, make.MakeError):
                files = {}
        keep_release(self.state_dir, raw, sig, files)


def keep_release(state_dir, raw, sig, files):
    """Write the release a folder has into <state>/release/: manifest.json, sigs/<sha>.sig, and `files` ({name:
    bytes}: release.torrent, release.item); a torrent or item of an older release that is not replaced goes. A
    newer release a seed run found and seeds from <state>/seeding/next/ goes too: the folder has that one now, or
    a newer one (it runs while the seed runs of the folder are paused)."""
    shutil.rmtree(os.path.join(state_dir, pause.DIR, pause.NEXT), ignore_errors=True)
    d = os.path.join(state_dir, RELEASE_DIR)
    os.makedirs(os.path.join(d, "sigs"), exist_ok=True)
    sha = hashlib.sha256(raw).hexdigest()
    writes = {"manifest.json": raw}
    if sig:
        writes["sigs/%s.sig" % sha] = sig
    writes.update(files)
    for rel, data in writes.items():
        p = os.path.join(d, *rel.split("/"))
        with open(p + ".tmp", "wb") as f:
            f.write(data)
        os.replace(p + ".tmp", p)
    for name in (TORRENT_FILE, ITEM_FILE):
        if name not in files:
            try:
                os.remove(os.path.join(d, name))
            except OSError:
                pass
    for n in os.listdir(os.path.join(d, "sigs")):
        if n != "%s.sig" % sha:
            try:
                os.remove(os.path.join(d, "sigs", n))
            except OSError:
                pass


def read_kept(state_dir, key, torrent_id):
    """The release a folder keeps: (manifest bytes, sig bytes or None, torrent bytes or None, Item or None);
    (None, None, None, None) when it keeps none."""
    d = os.path.join(state_dir, RELEASE_DIR)
    try:
        with open(os.path.join(d, "manifest.json"), "rb") as f:
            raw = f.read()
    except OSError:
        return None, None, None, None
    sha = hashlib.sha256(raw).hexdigest()

    def read(rel, limit):
        try:
            with open(os.path.join(d, *rel.split("/")), "rb") as f:
                return f.read(limit)
        except OSError:
            return None
    sig = read("sigs/%s.sig" % sha, 1 << 16)
    tor = read(TORRENT_FILE, 64 << 20)
    it = None
    data = read(ITEM_FILE, 4096)
    if data and key and torrent_id:
        try:
            it = item.read(data, key, torrent_id)
        except TorrentError:
            it = None
    return raw, sig, tor, it


class TorrentSource(object):
    """The release of a project with no address, from the swarm: the newest release item in the DHT (signed by the
    publisher key, numbered no lower than the highest accepted here), the torrent it names (from peers by its
    infohash, or the copy kept here), its manifest and signature first, then the blobs the update needs. It stands
    in for source.Source in client.update(); fetch_blob() fails for a blob no peer gave.

    What it downloads to find the release goes into a work folder of its own, <state>/torrent/<pid>.<random>/, and
    only that folder goes when it closes: an update, a check and a seed run's look of one folder never touch each
    other's files. So it finds the release before the update locks the folder (prepare()). The torrent's name is
    never checked here: the release item, signed by the publisher key, names the torrent by its infohash, so a
    publisher who renames the torrent strands no player. It never asks the router for a port (only seed runs may)."""

    http = False

    def __init__(self, tinfo, key, install, general, log=None, cancel=None, swarm=None, ip=None):
        from .. import signing
        self.tinfo = tinfo
        self.key = key
        self.key_bytes = signing.parse_key(key)
        self.state_dir = os.path.join(os.path.abspath(install), ".deltapatcher")
        self.general = general
        self.log = log or (lambda s: None)
        self.cancel = cancel
        self.kept = Kept(self.state_dir, tinfo["id"])
        self.swarm = swarm
        self.own_swarm = swarm is None
        self.ip = ip
        self.item = None
        self.ti = None
        self.files = None
        self.handle = None
        self.manifest = None
        self.sig = None
        self.info = None
        self.work = None                    # its own work folder, made when first needed
        self.resolved = None                # the seeds and peers met, resolved once

    def __str__(self):
        return "the torrent %s" % self.tinfo["name"]

    def for_release(self, version):
        pass

    def prepare(self):
        """Find the release (the DHT, then peers): client.update calls it before it locks the folder."""
        self.get_manifest()

    def confirm(self):
        """Inside the folder's lock (client.update): the release found before it is not numbered below the highest
        one this folder accepted, which another update of the folder may have raised meanwhile. SourceError
        otherwise: the next update finds the newer one."""
        if self.item is None:
            return
        _raw, _sig, _tor, kept_item = read_kept(self.state_dir, self.key, self.tinfo["id"])
        floor = max(Kept(self.state_dir, self.tinfo["id"]).seq, kept_item.seq if kept_item is not None else 0)
        if self.item.seq < floor:
            raise SourceError("the release found (number %d) is older than the one this folder accepted meanwhile "
                              "(number %d): update again" % (self.item.seq, floor))

    def _work(self):
        """This finding's work folder (made now); work folders of processes that ended go."""
        if self.work is None:
            parent = os.path.join(self.state_dir, WORK)
            try:
                for n in os.listdir(parent):
                    pid = n.split(".", 1)[0]
                    if pid.isascii() and pid.isdigit() and int(pid) != os.getpid() and not pid_alive(int(pid)):
                        shutil.rmtree(os.path.join(parent, n), ignore_errors=True)
            except OSError:
                pass
            self.work = os.path.join(parent, "%d.%s" % (os.getpid(), secrets.token_hex(4)))
            os.makedirs(self.work, exist_ok=True)
        return self.work

    def open(self):
        if self.swarm is None or not getattr(self, "_nodes_added", False):
            if self.swarm is None:
                self.swarm = Swarm("all", dht=True, dht_read_only=True, port_mapping=False,
                                   upload_kbps=self.general.get("seed_upload_kbps", 0), ip=self.ip, log=self.log)
            add_dht_nodes(self.swarm, self.tinfo["seeds"] + self.kept.peers)
            self._nodes_added = True
        return self.swarm

    def close(self):
        if self.swarm is not None:
            if self.handle is not None:
                self.kept.met(self.swarm.snapshot(self.handle), self.swarm.own())
                self.swarm.close_files(self.handle)
                self.handle = None
            if self.own_swarm:
                self.swarm.close()
            self.swarm = None
        if self.work is not None:
            shutil.rmtree(self.work, ignore_errors=True)
            try:
                os.rmdir(os.path.dirname(self.work))        # only when no other run works there
            except OSError:
                pass
            self.work = None
        self.kept.save()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- what client.update asks a source for

    def get_manifest(self):
        if self.manifest is None:
            self._find()
        return self.manifest

    def get_signature(self, manifest_sha):
        if self.manifest is not None and hashlib.sha256(self.manifest).hexdigest() == manifest_sha:
            return self.sig
        return None

    def fetch_blob(self, sha, dest, size, progress=None, cancel=None):
        if size == 0:
            with open(dest, "wb"):
                pass
            return
        raise SourceError("the blob %s could not be had from any peer of the torrent" % sha[:12])

    # ---- finding the release

    def _find(self):
        raw, sig, tor, kept_item = read_kept(self.state_dir, self.key, self.tinfo["id"])
        floor = max(self.kept.seq, kept_item.seq if kept_item is not None else 0)
        self.open()
        self.log("looking for the newest release in the DHT (seeds: %s)" % (", ".join(self.tinfo["seeds"]) or "none"))
        found = self.swarm.get_items(self.key_bytes, self.tinfo["id"], self.key, self.tinfo["seeds"] + self.kept.peers,
                                     FIND, self.cancel)
        lower = [it for it in found if it.seq < floor]
        if lower:
            self.log("Warning: a release item numbered %d was refused: this folder accepted %d before"
                     % (lower[0].seq, floor))
        chosen = item.newest(found + [kept_item], floor)
        if chosen is None:
            if self.cancel is not None and self.cancel.is_set():
                raise SourceError("stopped")
            raise SourceError("the release of this project could not be found: no seed or DHT node had it "
                              "(its seeds: %s)" % (", ".join(self.tinfo["seeds"]) or "none"))
        if not found:
            self.log("Warning: no seed or DHT node answered: the release this folder has is the newest known")
        self.item = chosen
        if raw is not None and hashlib.sha256(raw).hexdigest() == chosen.m and tor and _infohash_or_none(tor) == chosen.ih:
            self.manifest, self.sig = raw, sig
            self.ti, self.files = read_torrent(tor)
            return
        self._metadata(tor)
        self._manifest()

    def _peers(self):
        """The seeds and the peers met before, as addresses: resolved once (at most 10 seconds, or until a stop)."""
        if self.resolved is None:
            self.resolved = resolve(self.tinfo["seeds"] + self.kept.peers, time.time() + 10, self.cancel)
        return self.resolved

    def _metadata(self, kept_torrent):
        """The torrent the chosen item names: the copy kept here when it is that one (a damaged copy is as good as
        none), else its info from peers (libtorrent checks it against the infohash). The torrent that fetched the
        info is taken out again: while it wanted nothing, peers that only upload closed their connections to it."""
        if kept_torrent and _infohash_or_none(kept_torrent) == self.item.ih:
            self.ti, self.files = read_torrent(kept_torrent)
            return
        self.log("getting the release's torrent from peers")
        h = self.swarm.add(infohash=self.item.ih, save_path=self._work(), peers=self._peers(), dht=True,
                           dont_download=True)
        try:
            a = self.swarm.wait(lambda a: a.of("metadata_received_alert", h), STALL, self.cancel)
            if a is None:
                raise SourceError("stopped" if self.cancel is not None and self.cancel.is_set() else
                                  "the release's torrent could not be had from any peer within %d seconds" % int(STALL))
            ti = h.torrent_file()
            if infohash_of(ti) != self.item.ih:
                raise SourceError("the torrent peers gave is not the one the release item names")
            self.ti, self.files = checked(ti)
        finally:
            self.swarm.remove(h)

    def _hold_all(self, wanted=()):
        """Every file of the torrent where this finding's work folder holds it: the manifest and signature under
        their own names, each blob as blobs/<sha>.bt; only the files `wanted` (torrent paths) download, first."""
        m = folder.Mapping(len(self.files), self.ti.num_pieces())
        for f in self.files:
            if f.pad:
                continue
            sha = folder.blob_of(f.path)
            m.renamed[f.index] = ("blobs/%s.bt" % sha) if sha else f.path
            if f.path in wanted:
                m.priorities[f.index] = 7
        return m

    def _manifest(self):
        """The release's manifest.json and signature, from peers, checked against the item's manifest hash."""
        want = [f for f in self.files if f.path == "manifest.json" or f.path.startswith("sigs/")]
        work = self._work()
        self.handle = self.swarm.add(self.ti, save_path=work, mapping=self._hold_all([f.path for f in want]),
                                     peers=self._peers(), dht=True)
        self.log("getting the release's manifest from peers")
        if not self._until_done(want, "the release's manifest"):
            raise SourceError("stopped" if self.cancel is not None and self.cancel.is_set() else
                              "the release's manifest could not be had from any peer within %d seconds" % int(STALL))
        self.swarm.flush(self.handle)
        try:
            with open(os.path.join(work, "manifest.json"), "rb") as f:
                raw = f.read(64 << 20)
            sig = None
            for f in want:
                if f.path.startswith("sigs/"):
                    with open(os.path.join(work, *f.path.split("/")), "rb") as fh:
                        sig = fh.read(1 << 16)
        except OSError as e:
            raise SourceError("the release's manifest from peers cannot be read: %s" % e)
        if hashlib.sha256(raw).hexdigest() != self.item.m:
            raise SourceError("the manifest peers gave is not the one the release item names: refused")
        self.manifest, self.sig = raw, sig

    def _until_done(self, want, what):
        total = sum(f.size for f in want)
        last, last_move, last_snap = -1, time.time(), time.time()
        while not (self.cancel is not None and self.cancel.is_set()):
            self.swarm.pump(250)
            prog = self.handle.file_progress(flags=1)
            done = sum(prog[f.index] for f in want)
            if done >= total:
                return True
            if done != last:
                last, last_move = done, time.time()
            if time.time() - last_move > STALL:
                return False
            if time.time() - last_snap > SNAPSHOT:
                self.kept.met(self.swarm.snapshot(self.handle), self.swarm.own())
                last_snap = time.time()
        return False

    # ---- the update's BitTorrent side

    def fetch(self, src, need, cdir, progress, cancel, raw=None, signer=None):
        """The blobs `need` from the swarm into `cdir` (the release was found and checked by its item already:
        `raw` and `signer` are for a project with an address, AddressTorrent)."""
        if self.ti is None:
            self._metadata(None)
        if self.handle is not None:
            self.swarm.close_files(self.handle)
            self.handle = None
        got, _none, _race = get_blobs(self.swarm, self.ti, self.files, need, cdir, self._peers(), self.log, progress,
                                      cancel, self.kept, own_dht=True)
        missing = len(need) - len(got)
        self.log("by torrent: %d of %d blobs%s" % (len(got), len(need), ("; %d no peer had" % missing) if missing else ""))

    def needs_keep(self, raw):
        kraw, _sig, tor, it = read_kept(self.state_dir, self.key, self.tinfo["id"])
        return kraw != raw or not tor or it is None or it.seq < self.item.seq or _infohash_or_none(tor) != self.item.ih

    def keep(self, src, raw, sig, signer):
        files = {ITEM_FILE: self.item.to_bytes()}
        tor = self._torrent_bytes()
        if tor:
            files[TORRENT_FILE] = tor
        keep_release(self.state_dir, raw, sig, files)
        self.kept.accept(self.item.seq)
        self.kept.save()

    def _torrent_bytes(self):
        """The torrent to keep: its info as peers gave it (its infohash checked), with the piece layers of every
        blob this folder now has, hashed here; a blob it lacks keeps none, and a seed does not offer it."""
        if self.ti is None:
            return b""
        try:
            info_raw = bytes(self.ti.info_section())
            if hashlib.sha256(info_raw).digest() != self.item.ih:
                return b""
            info = bencode.decode(info_raw)
        except (RuntimeError, bencode.BencodeError, ValueError):
            return b""
        install = os.path.dirname(self.state_dir)
        cache = make.Cache(os.path.join(self.state_dir, "torrentcache.json"))
        man = _manifest_of(self.manifest)
        layers = {}
        plen = info[b"piece length"]
        for f in self.files:
            sha = folder.blob_of(f.path)
            if sha is None or f.root is None or f.size <= plen or man is None:
                continue
            got = cache.get(sha, plen)
            if got is None:
                paths = [p for p, e in man.files.items() if e.sha256 == sha]
                for p in paths:
                    try:
                        root, layer, size = make.hashes_of_file(os.path.join(install, *p.split("/")), plen)
                    except OSError:
                        continue
                    if root == f.root and size == f.size:
                        cache.put(sha, plen, root, layer)
                        got = (root, layer)
                        break
            if got is not None and got[0] == f.root:
                layers[f.root] = got[1]
        cache.keep_only({folder.blob_of(f.path) for f in self.files if folder.blob_of(f.path)})
        try:
            cache.save()
        except OSError:
            pass
        torrent = {b"info": info}
        if layers:
            torrent[b"piece layers"] = layers
        return bencode.encode(torrent)


def _manifest_of(raw):
    from ..manifest import Manifest, ManifestError
    try:
        return Manifest.from_json(raw)
    except ManifestError:
        return None


# ---- a check of this DeltaPatcher's BitTorrent support -------------------------------------------------

def self_test(work, log=None):
    """A session on 127.0.0.1 with DeltaPatcher's settings, and a small v2 torrent made here and checked by
    libtorrent. Returns {"passed", "steps": [{"step", "ok", "detail"}]}."""
    log = log or (lambda s: None)
    steps = []

    def step(name, ok, detail=""):
        steps.append({"step": name, "ok": bool(ok), "detail": detail})
        log("%s %s%s" % ("ok    " if ok else "FAILED", name, (": " + detail) if detail else ""))
        return ok

    try:
        L = lt()
    except TorrentError as e:
        step("libtorrent loads", False, str(e))
        return {"passed": False, "steps": steps}
    step("libtorrent loads", True, "libtorrent %s" % L.__version__)
    store = os.path.join(work, "store")
    os.makedirs(os.path.join(store, "blobs"), exist_ok=True)
    blob = os.urandom(70000)
    sha = hashlib.sha256(blob).hexdigest()
    with open(os.path.join(store, "blobs", sha), "wb") as f:
        f.write(blob)
    manifest = b'{"format": 1, "files": []}\n'
    with open(os.path.join(store, "manifest.json"), "wb") as f:
        f.write(manifest)
    data, ih = make.make("selftest", manifest, None, {sha: len(blob)}, os.path.join(store, "blobs"))
    swarm = None
    try:
        swarm = Swarm("loopback")
        got = swarm.ses.get_settings()
        safe = (got.get("listen_interfaces") == listen_on("loopback") and got.get("enable_dht") is False and
                got.get("dht_bootstrap_nodes") == "" and got.get("enable_upnp") is False and
                got.get("enable_natpmp") is False and got.get("enable_lsd") is False)
        step("a session on 127.0.0.1 with the safe settings", safe and swarm.port > 0, "port %d" % swarm.port)
        ti, files = read_torrent(data, "selftest")
        step("a v2 torrent made and read", infohash_of(ti) == ih, ih.hex()[:16])
        m = folder.for_store(files, ti.num_pieces(), store, folder.hash_cache(store))
        h = swarm.add(ti, save_path=store, mapping=m)
        h.force_recheck()
        end = time.time() + 30
        while time.time() < end and not h.status().is_seeding:
            swarm.pump(100)
        step("libtorrent checks every piece of it", h.status().is_seeding)
    except (TorrentError, RuntimeError, OSError, make.MakeError) as e:
        step("the torrent", False, str(e))
    finally:
        if swarm is not None:
            swarm.close()
    return {"passed": all(s["ok"] for s in steps), "steps": steps}
