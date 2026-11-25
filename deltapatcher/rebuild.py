"""An update's rebuild of a changed archive from what the folder already has (the index format is in parts.py).

For each blob a plan needs whose manifest entry names a parts index, when the release is read from an http(s)
address (prepare()):
  1. the index, parts/<sha> beside the manifest, fetched only when the folder has a zip-structured file at all: read
     at most once per release, kept in <state>/parts/<sha> so a check and the update after it fetch it once (only
     while the drive keeps the update's free space after it), removed when no plan needs it, and checked before use
     (parts.read_index). One that is refused is remembered (<state>/parts/<sha>.refused) and never fetched again. In
     path order, one plan keeps at most PLAN_INDEX_MAX bytes and PLAN_SEGMENTS_MAX segments of indexes, and fetches
     at most PLAN_FETCH_MAX bytes of them: from the first archive past any of it on, the archives come whole and no
     more index is fetched. A check fetches none when the drive could not keep it (it then counts those archives
     whole; the update reads them as it runs);
  2. what is here: the members of the old archive at the same path first, then of every other zip-structured file
     the folder's state.json says DeltaPatcher installed, each looked at only while a piece is still missing, by the
     SHA-256 of each member's compressed data; never a path the player keeps as their own (protected by the release,
     or kept as edited here). They are kept in <state>/members.json by path, size and modification time (a file that
     changed is read again, one that is not a zip is passed over), and a member is hashed only when an index has a
     missing piece of its size;
  3. the plan: each piece the folder lacks is a range of the blob to fetch, neighbours closer than GAP fetched as one,
     at most MAX_RANGES requests a file (GAP grows until it is); when what must be fetched comes to more than
     WHOLE_SHARE of the file, the file is downloaded whole instead.
run() then writes the index's literals and the pieces the folder has, in order, into cache/<sha>.rebuild (each piece
hashed as it is copied: one that no longer matches, or that cannot be read for any reason, is fetched too; at most
MAX_OPEN of the folder's files are open at once), fetches the ranges with the update's workers
(Source.fetch_range: only a 206 of exactly the bytes asked for is taken), hashes the whole file, and renames it to
cache/<sha> only when it is the entry's SHA-256 and size. Anything that does not work out (a server that ignores
Range, a wrong answer, a failure after the usual tries, a file that is not the right one) leaves that blob to the
whole download, as before: correctness never depends on the rebuild, only the amount downloaded does. A stop
removes the half-built file; the next update starts that rebuild again (its fetched ranges are small).
"""

import collections
import concurrent.futures
import hashlib
import itertools
import json
import os
import shutil
import threading

from . import parts
from .hashing import sha256_file
from .manifest import HEX64
from .publish import human
from .source import SourceError

PARTS_DIR = "parts"                 # <state>/parts/<sha>: the indexes the current plan needs
MEMBERS_NAME = "members.json"       # <state>/members.json: the members of the folder's zip-structured files
REBUILD_SUFFIX = ".rebuild"         # cache/<sha>.rebuild: an archive being rebuilt (never resumed: started again)
GAP = 256 << 10                     # pieces to fetch closer than this are fetched as one range
MAX_RANGES = 256                    # at most this many range requests a file
WHOLE_SHARE = 0.7                   # more than this share of a file to fetch: it is downloaded whole
PLAN_INDEX_MAX = 256 << 20          # one plan keeps at most this much of indexes (their JSON), all together
PLAN_SEGMENTS_MAX = parts.SEGMENTS_MAX   # and at most this many of their segments (one index's most)
PLAN_FETCH_MAX = 256 << 20          # and fetches at most this many bytes of indexes, as they come (refused ones too)
REFUSED_SUFFIX = ".refused"         # <state>/parts/<sha>.refused: an index that was refused, never fetched again
MAX_OPEN = 32                       # a rebuild has at most this many of the folder's files open at once
CHUNK = 1 << 20


class Rebuild(object):
    """One archive an update rebuilds: its manifest entry, its index's segments, where each piece the folder has is
    ({piece sha: (path, offset)}), the ranges of the blob to fetch ([(start, end)]) and how many bytes they are.
    `fetched`: the bytes fetched so far."""

    def __init__(self, entry, segments, have, ranges):
        self.entry = entry
        self.segments = segments
        self.have = have
        self.ranges = ranges
        self.fetch_bytes = sum(b - a for a, b in ranges)
        self.fetched = 0


class _Either(object):
    """Set when any of its events is."""

    def __init__(self, *events):
        self.events = [e for e in events if e is not None]

    def is_set(self):
        return any(e.is_set() for e in self.events)


class _Fallback(Exception):
    """The rebuild of one file stops: it is downloaded whole."""


def coalesce(pieces, gap=GAP, most=MAX_RANGES):
    """The ranges to fetch for the pieces [(start, end)]: neighbours closer than `gap` are fetched as one (the bytes
    between come with them), and `gap` grows until there are at most `most` ranges."""
    pieces = sorted(pieces)
    while True:
        ranges = []
        for a, b in pieces:
            if ranges and a - ranges[-1][1] < gap:
                ranges[-1][1] = max(ranges[-1][1], b)
            else:
                ranges.append([a, b])
        if len(ranges) <= most:
            return [(a, b) for a, b in ranges]
        gap *= 2


# ---- what is here: the members of the folder's zip-structured files ------------------------------------------------

class Members(object):
    """The members of the zip-structured files of a folder, kept in <state>/members.json:
    {"format": 1, "files": {path: {"size", "mtime_ns", "members": [[offset, size, sha256 or ""], ...]}}}, every
    member of PIECE_MIN bytes or more by where its compressed data is; [] for a file that is not a zip. A member's
    SHA-256 is worked out when it is first asked for, and kept."""

    def __init__(self, path):
        self.path = path
        self.files = {}
        self.dirty = False
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            raw = doc.get("files") if isinstance(doc, dict) and doc.get("format") == 1 else None
            for rel, e in (raw or {}).items():
                if self._good(e):
                    self.files[rel] = e
        except (OSError, ValueError, AttributeError, RecursionError):
            pass

    @staticmethod
    def _good(e):
        if not isinstance(e, dict) or not isinstance(e.get("members"), list):
            return False
        if not all(isinstance(e.get(k), int) and not isinstance(e.get(k), bool) for k in ("size", "mtime_ns")):
            return False
        for m in e["members"]:
            if not (isinstance(m, list) and len(m) == 3
                    and all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in m[:2])
                    and isinstance(m[2], str) and (m[2] == "" or HEX64.match(m[2]))):
                return False
        return True

    def members(self, rel, full):
        """The members of the file `rel` (at `full`): read again when its size or modification time moved."""
        st = os.stat(full)
        e = self.files.get(rel)
        if e is not None and (e["size"], e["mtime_ns"]) == (st.st_size, st.st_mtime_ns):
            return e
        with open(full, "rb") as f:
            found = parts.zip_members(f, st.st_size)
        got = [] if found is None else [[a, b - a, ""] for _off, a, b in found[0] if b - a >= parts.PIECE_MIN]
        if len(got) > parts.SEGMENTS_MAX:
            got = []                        # more pieces than any index may have: passed over, as one that is no zip
        e = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "members": got}
        st2 = os.stat(full)
        if (st2.st_size, st2.st_mtime_ns) == (st.st_size, st.st_mtime_ns):
            self.files[rel] = e
            self.dirty = True
        return e

    def hash_of(self, rel, full, e, m, cancel=None):
        """The SHA-256 of the member m of the file `rel`, kept; None when the file changed meanwhile."""
        if m[2]:
            return m[2]
        h = hashlib.sha256()
        with open(full, "rb") as f:
            f.seek(m[0])
            left = m[1]
            while left:
                if cancel is not None and cancel.is_set():
                    raise SourceError("stopped")
                data = f.read(min(CHUNK, left))
                if not data:
                    return None
                h.update(data)
                left -= len(data)
        st = os.stat(full)
        if (st.st_size, st.st_mtime_ns) != (e["size"], e["mtime_ns"]):
            self.files.pop(rel, None)
            self.dirty = True
            return None
        m[2] = h.hexdigest()
        self.dirty = True
        return m[2]

    def save(self, keep):
        """Write the file, keeping only the paths `keep`. Never fails an update: it only saves time later."""
        gone = [rel for rel in self.files if rel not in keep]
        for rel in gone:
            del self.files[rel]
        if not (self.dirty or gone):
            return
        try:
            tmp = "%s.%d.tmp" % (self.path, os.getpid())
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"format": 1, "files": self.files}, f, separators=(",", ":"))
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError:
            pass


def find(wanted, candidates, members, cancel=None):
    """Where the folder has the pieces `wanted` ({piece sha: size}): {piece sha: (path, offset)}, looking through
    `candidates` (an iterable of (path, full path)) in order, only while a piece is missing, and hashing only the
    members of a size some missing piece has."""
    found = {}
    missing = dict(wanted)
    sizes = collections.Counter(missing.values())        # how many pieces still missing have each size
    for rel, full in candidates:
        if not missing:
            break
        if cancel is not None and cancel.is_set():
            raise SourceError("stopped")
        try:
            e = members.members(rel, full)
            for m in e["members"]:
                if not sizes[m[1]]:
                    continue
                sha = members.hash_of(rel, full, e, m, cancel)
                if sha is None:
                    break                   # the file changed: passed over this time
                if missing.get(sha) == m[1]:
                    found[sha] = (rel, m[0])
                    del missing[sha]
                    sizes[m[1]] -= 1
                    if not missing:
                        break
        except OSError:
            continue
    return found


# ---- the plan ------------------------------------------------------------------------------------------------------

def _free(path):
    """The free space of the drive `path` is on (a test stands in for it)."""
    return shutil.disk_usage(path).free


def _write_whole(path, data):
    """`data` at `path`, written whole or not at all (a .tmp beside it, then renamed)."""
    with open(path + ".tmp", "wb") as f:
        f.write(data)
    os.replace(path + ".tmp", path)


def _index(src, entry, pdir, log, space=0, check=False, budget=PLAN_FETCH_MAX):
    """(the index of `entry` as parts.read_index(sized=True) gives it, or None when there is none to use; the bytes
    fetched for it; '', or why prepare() stops here: "room", a check that could not keep it fetches none (the drive
    lacks `space` and the most an index may be, INDEX_MAX), or "budget", it is longer than the `budget` bytes the plan
    may still fetch, of which it takes no more). The one kept in pdir is read first, and one refused before
    (pdir/<sha>.refused) is not fetched again. A fetched one is kept in pdir only while `space` bytes (what the update
    needs free: client._space_needed()) stay free on the drive after it; otherwise it is used from memory, and the
    update after a check fetches it again. A refused one is remembered, written whole or not at all."""
    kept = os.path.join(pdir, entry.parts)
    try:
        with open(kept, "rb") as f:
            data = f.read(parts.INDEX_MAX + 1)
        try:
            return parts.read_index(data, entry, sized=True), 0, ""
        except parts.PartsError:
            os.remove(kept)
    except OSError:
        pass
    if os.path.isfile(kept + REFUSED_SUFFIX):
        log("the parts index of %s was refused before: it comes whole" % entry.path)
        return None, 0, ""
    if check and _free(os.path.dirname(pdir)) - parts.INDEX_MAX < space:
        return None, 0, "room"
    limit = min(parts.INDEX_MAX, budget - 1)
    try:
        data = src.get_parts(entry.parts, limit)
    except SourceError as e:
        log("the parts index of %s could not be read (%s): it comes whole" % (entry.path, e))
        return None, 0, ""
    if data is None:
        log("the release has no parts index of %s: it comes whole" % entry.path)
        return None, 0, ""
    if len(data) > limit and limit < parts.INDEX_MAX:
        return None, len(data), "budget"    # no more of it was read: the plan may fetch no more
    try:
        got = parts.read_index(data, entry, sized=True)
    except parts.PartsError as e:
        log("Warning: the parts index of %s is refused (%s): it comes whole" % (entry.path, e))
        try:
            os.makedirs(pdir, exist_ok=True)
            _write_whole(kept + REFUSED_SUFFIX, (str(e)[:200] + "\n").encode("ascii", "replace"))
        except OSError:
            pass                            # only kept to save fetching it again
        return None, len(data), ""
    try:
        if _free(os.path.dirname(pdir)) - len(data) >= space:
            os.makedirs(pdir, exist_ok=True)
            _write_whole(kept, data)
    except OSError:
        pass                                # only kept to save fetching it again
    return got, len(data), ""


def tidy(state_dir, keep=()):
    """<state>/parts/ without the indexes no plan needs (all of them but `keep`) and their records of a refused one,
    and the folder itself when it is empty."""
    pdir = os.path.join(state_dir, PARTS_DIR)
    try:
        names = os.listdir(pdir)
    except OSError:
        return
    for n in names:
        if (n[:-len(REFUSED_SUFFIX)] if n.endswith(REFUSED_SUFFIX) else n) not in keep:
            try:
                os.remove(os.path.join(pdir, n))
            except OSError:
                pass
    try:
        os.rmdir(pdir)
    except OSError:
        pass


def _some(paths):
    """Archives named in a log line: the first, and how many more."""
    more = len(paths) - 1
    return paths[0] if not more else "%s and %d more archive%s" % (paths[0], more, "" if more == 1 else "s")


def prepare(src, plan, state, state_dir, locate, log, cancel=None, space=0, check=False):
    """{blob sha: Rebuild} for the blobs `plan` needs that the folder can rebuild from what it has; {} when the source
    is not an http(s) address or none can. `state`: the folder's State (the files DeltaPatcher installed);
    locate(path): the file of a release path in the folder (client.target_path), or an exception when it may not be
    read; `space`: the free space the update needs (_index()); `check`: a check, which fetches no index it could not
    keep. The indexes and records no plan needs leave <state>/parts/, and <state>/members.json is kept up to date.
    In path order, the plan keeps at most PLAN_INDEX_MAX bytes and PLAN_SEGMENTS_MAX segments of indexes and fetches at
    most PLAN_FETCH_MAX bytes of them: the archive past any of it and every one after it come whole, and no index is
    fetched for them. The paths the player keeps as their own (the release's protected paths, and those kept as edited
    here) are never opened."""
    entries = {}
    if getattr(src, "http", False):
        for e in sorted(plan.add + plan.replace, key=lambda e: e.path):
            if e.parts and e.sha256 not in entries:
                entries[e.sha256] = e
    pdir = os.path.join(state_dir, PARTS_DIR)
    tidy(state_dir, {e.parts for e in entries.values()})
    if not entries:
        return {}
    # what is here: the old archive at each path first, then every other file DeltaPatcher installed, looked at only
    # while a piece is missing, never one the player keeps as their own; only a zip can hold a piece, and with none
    # here no index is fetched
    mine = set(plan.kept_changed) | set(plan.protected)
    man = plan.manifest

    def theirs(rel):
        return rel in mine or (man is not None and man.is_protected(rel))

    order = [e.path for e in sorted(entries.values(), key=lambda e: e.path) if not theirs(e.path)]
    first_paths = set(order)
    order += sorted(p for p in state.files if p not in first_paths and not theirs(p))
    members = Members(os.path.join(state_dir, MEMBERS_NAME))

    def here(rel):
        """The file of `rel` when it is one that can hold a piece; None otherwise."""
        try:
            full = locate(rel)
            if os.path.isfile(full) and not os.path.islink(full) and os.path.getsize(full) >= parts.PIECE_MIN:
                return full
        except Exception:                   # cannot be read, or a path that may not be (client.target_path refuses it)
            pass
        return None

    try:
        rest = iter(order)
        first = []
        for rel in rest:
            if cancel is not None and cancel.is_set():
                raise SourceError("stopped")
            full = here(rel)
            try:
                if full and members.members(rel, full)["members"]:
                    first.append((rel, full))
                    break
            except OSError:
                continue
        if not first:
            return {}
        candidates = itertools.chain(first, ((rel, full) for rel, full in ((r, here(r)) for r in rest) if full))
        indexes = {}
        kept_json = kept_segments = fetched = 0
        capped = False                      # the plan has all the indexes it may: the rest come whole, unread
        unread = []                         # the archives after that
        no_room = []                        # a check's archives whose index it could not keep: counted whole
        for sha, e in sorted(entries.items(), key=lambda kv: kv[1].path):
            if cancel is not None and cancel.is_set():
                raise SourceError("stopped")
            if capped or kept_json >= PLAN_INDEX_MAX or kept_segments >= PLAN_SEGMENTS_MAX or fetched >= PLAN_FETCH_MAX:
                capped = True
                unread.append(e.path)
                continue
            got, n, why = _index(src, e, pdir, log, space, check, PLAN_FETCH_MAX - fetched)
            fetched += n
            if why == "room":
                no_room.append(e.path)
                continue
            if why == "budget":
                log("%s: its parts index would take this update past %d MiB of indexes fetched: it comes whole"
                    % (e.path, PLAN_FETCH_MAX >> 20))
                capped = True
                continue
            if got is None:
                continue
            segs, json_bytes = got
            got = None
            if kept_json + json_bytes > PLAN_INDEX_MAX or kept_segments + len(segs) > PLAN_SEGMENTS_MAX:
                # it stays kept (when it was), so the next check reads it here instead of fetching it again
                log("%s: its parts index would take this update past %d MiB or %s segments of indexes: it comes whole"
                    % (e.path, PLAN_INDEX_MAX >> 20, format(PLAN_SEGMENTS_MAX, ",")))
                segs = None
                capped = True
                continue
            kept_json += json_bytes
            kept_segments += len(segs)
            indexes[sha] = segs
        if unread:
            log("%s come%s whole: this update has all the parts indexes it may read"
                % (_some(unread), "s" if len(unread) == 1 else ""))
        if no_room:
            log("this check counts %s whole: the drive has too little free space to keep %s (an update reads %s as it "
                "runs)" % (_some(no_room), "its parts index" if len(no_room) == 1 else "their parts indexes",
                           "it" if len(no_room) == 1 else "them"))
        if not indexes:
            return {}
        wanted = {}
        for segs in indexes.values():
            for kind, _off, length, payload in segs:
                if kind == "p":
                    wanted[payload] = length
        log("looking for the pieces of %d changed archive%s in what is here"
            % (len(indexes), "" if len(indexes) == 1 else "s"))
        found = find(wanted, candidates, members, cancel)
    finally:
        members.save(set(order))
    out = {}
    for sha in sorted(indexes, key=lambda s: entries[s].path):
        e, segs = entries[sha], indexes[sha]
        have = {}
        lacking = []
        for kind, off, length, payload in segs:
            if kind != "p":
                continue
            if payload in found:
                have[payload] = found[payload]
            else:
                lacking.append((off, off + length))
        rb = Rebuild(e, segs, have, coalesce(lacking))
        if rb.fetch_bytes > WHOLE_SHARE * e.size:
            log("%s: %s of its %s would be fetched: it comes whole" % (e.path, human(rb.fetch_bytes), human(e.size)))
            continue
        log("%s: rebuilt from what is here, %s to fetch of %s" % (e.path, human(rb.fetch_bytes), human(e.size)))
        out[sha] = rb
    return out


# ---- the rebuild ---------------------------------------------------------------------------------------------------

def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def clear(cdir):
    """Half-built archives a stopped update left in the cache (<sha>.rebuild): a rebuild is never resumed."""
    try:
        names = os.listdir(cdir)
    except OSError:
        return
    for n in names:
        if n.endswith(REBUILD_SUFFIX) and HEX64.match(n[:-len(REBUILD_SUFFIX)]):
            _remove(os.path.join(cdir, n))


class _Sources(object):
    """The folder's files a rebuild reads its pieces from, at most MAX_OPEN of them open at once: when one more is
    needed, the one used longest ago is closed (a folder can hold many archives, and a process few open files: 256 by
    default on macOS, shared by every project update --all runs)."""

    def __init__(self, locate):
        self.locate = locate
        self.files = collections.OrderedDict()          # path: its open file, the one used longest ago first

    def get(self, rel):
        f = self.files.pop(rel, None)
        if f is None:
            while len(self.files) >= MAX_OPEN:
                self.files.popitem(last=False)[1].close()
            f = open(self.locate(rel), "rb")
        self.files[rel] = f
        return f

    def drop(self, rel):
        f = self.files.pop(rel, None)
        if f is not None:
            f.close()

    def close(self):
        while self.files:
            self.files.popitem()[1].close()


def _write_known(rb, tmp, locate, cancel):
    """Write the literals and the pieces the folder has at their offsets of `tmp`, in order; each piece is hashed as it
    is copied. Returns the [(start, end)] still missing: the pieces the folder lacks, those that no longer match, and
    those that could not be read for any reason (the file is gone, or its path is one that may not be read now: a link
    on the way, which locate() refuses)."""
    missing = []
    sources = _Sources(locate)
    try:
        with open(tmp, "wb") as out:
            for kind, off, length, payload in rb.segments:
                if cancel is not None and cancel.is_set():
                    raise SourceError("stopped")
                if kind == "l":
                    out.seek(off)
                    out.write(payload)
                    continue
                where = rb.have.get(payload)
                if where is None:
                    missing.append((off, off + length))
                    continue
                rel, at = where
                try:
                    f = sources.get(rel)
                    f.seek(at)
                    out.seek(off)
                    h = hashlib.sha256()
                    left = length
                    while left:
                        if cancel is not None and cancel.is_set():
                            raise SourceError("stopped")
                        data = f.read(min(CHUNK, left))
                        if not data:
                            break
                        h.update(data)
                        out.write(data)
                        left -= len(data)
                    good = not left and h.hexdigest() == payload
                except SourceError:
                    raise                   # stopped
                except Exception:
                    good = False
                    sources.drop(rel)
                if not good:
                    missing.append((off, off + length))
            out.truncate(rb.entry.size)
    finally:
        sources.close()
    return missing


def _fetch(rb, ranges, src, tmp, workers, tick, cancel):
    """Fetch the ranges into `tmp`, `workers` at a time. _Fallback when one cannot be had as asked; SourceError
    ("stopped") on a stop."""
    failed = threading.Event()
    stop = _Either(cancel, failed)
    errors = []
    lock = threading.Lock()

    def counted(n):
        with lock:
            rb.fetched += n
        tick(n)

    def one(a, b):
        if stop.is_set():
            return
        try:
            src.fetch_range(rb.entry.sha256, a, b, rb.entry.size, tmp, counted, stop)
        except Exception as e:
            errors.append(e)
            failed.set()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for f in [pool.submit(one, a, b) for a, b in ranges]:
            f.result()
    if cancel is not None and cancel.is_set():
        raise SourceError("stopped")
    if errors:
        first = [e for e in errors if not (isinstance(e, SourceError) and str(e) == "stopped")] or errors
        raise _Fallback(str(first[0]))


def run(rb, src, cdir, locate, workers, tick, cancel, log):
    """Rebuild one archive into cdir/<sha>: True when it is there and is the entry's SHA-256 and size; False when it did
    not work out (the caller downloads it whole). SourceError("stopped") on a stop, with nothing of it left behind.
    tick(n) hears of the bytes fetched as they come."""
    sha, size, path = rb.entry.sha256, rb.entry.size, rb.entry.path
    dest = os.path.join(cdir, sha)
    tmp = dest + REBUILD_SUFFIX
    _remove(tmp)
    try:
        if os.path.isfile(dest) and sha256_file(dest) == (sha, size):
            rb.ranges, rb.fetch_bytes = [], 0
            return True                     # already here: an update that stopped after it left it
        missing = _write_known(rb, tmp, locate, cancel)
        ranges = coalesce(missing)
        need = sum(b - a for a, b in ranges)
        if need > rb.fetch_bytes:
            log("%s: some pieces here no longer match what was read: %s to fetch" % (path, human(need)))
        if need > WHOLE_SHARE * size:
            raise _Fallback("%s of %s to fetch" % (human(need), human(size)))
        rb.ranges, rb.fetch_bytes = ranges, need
        if ranges:
            _fetch(rb, ranges, src, tmp, workers, tick, cancel)
        got = sha256_file(tmp)
        if got != (sha, size):
            raise _Fallback("the rebuilt file is not the release's (its SHA-256 differs)")
        os.replace(tmp, dest)
        _remove(dest + ".part")             # a whole download of it that was cut: not needed now
        log("rebuilt %s from what is here: %s fetched of %s" % (path, human(rb.fetched), human(size)))
        return True
    except SourceError:
        _remove(tmp)
        if cancel is not None and cancel.is_set():
            raise SourceError("stopped")
        log("%s could not be rebuilt: it comes whole" % path)
        return False
    except _Fallback as e:
        _remove(tmp)
        log("%s could not be rebuilt (%s): it comes whole" % (path, e))
        return False
    except OSError as e:
        _remove(tmp)
        log("%s could not be rebuilt (%s): it comes whole" % (path, e.strerror or e))
        return False
    except Exception as e:                  # anything else: the rebuild only saves downloading
        _remove(tmp)
        log("%s could not be rebuilt (%s: %s): it comes whole" % (path, type(e).__name__, e))
        return False
