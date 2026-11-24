"""Which file on disk each file of a release's torrent is: for a seed (a player's installed folder, or the
publisher's store) and for a download (blobs into the update's cache).

A torrent's files are manifest.json, sigs/<sha256 of the manifest>.sig and blobs/<sha256>. In an installed folder
a blob is wherever the release put a file with that SHA-256, and the manifest and its signature are the copies the
folder keeps in .deltapatcher/release/: libtorrent reads them there through renamed files, and nothing is copied.

A seed offers only what the engine itself has checked: a blob only from a file whose SHA-256 (from the folder's
hash cache, hashed again whenever its size or modification time moved) is the blob's name, never from a file the
release protects (the player's own copy), and the manifest and signature only when their merkle roots are the
torrent's. Everything else gets a path that never exists and priority 0, and libtorrent is told which pieces are
had (have_pieces, without its own check, which misses a file changed in place). The size and time of every file
offered are kept, so a change while seeding is seen.

Mapping a folder can take a while (every file not in the hash cache is hashed), so a mapping may be given a check:
it is asked between files and between 1 MB pieces of a file, and when it says to stop (an update asks the seed run
to pause, or the run stops), the mapping ends at once with Interrupted, its files closed and its hashes kept.
"""

import hashlib
import os
import re

from .. import hashing
from . import RELEASE_DIR, make

HEX64 = re.compile(r"^[0-9a-f]{64}$")
ABSENT = ".deltapatcher/" + RELEASE_DIR + "/absent/%d"
PIECE = 1 << 20              # bytes hashed between two checks


class Interrupted(Exception):
    """A mapping its check stopped."""


class TFile(object):
    """One file of a torrent as libtorrent numbers them: its index, its path inside the torrent (without the
    torrent's name), its size, its first piece and how many pieces it has; pad files are left out of offers."""

    __slots__ = ("index", "path", "size", "first", "pieces", "pad", "root")

    def __init__(self, index, path, size, first, pieces, pad=False, root=None):
        self.index, self.path, self.size, self.first, self.pieces, self.pad, self.root = \
            index, path, size, first, pieces, pad, root

    def __repr__(self):
        return "TFile(%d, %r, %d)" % (self.index, self.path, self.size)


def blob_of(path):
    """The SHA-256 a torrent path names (blobs/<sha>), or None."""
    if path.startswith("blobs/") and HEX64.match(path[6:]):
        return path[6:]
    return None


def layout_problem(files):
    """What is wrong with a torrent as a release ('' when nothing): its files are one manifest.json, at most one
    sigs/<sha>.sig and blobs/<sha>, each once."""
    seen, manifests, sigs = set(), 0, 0
    for f in files:
        if f.pad:
            continue
        if f.path in seen:
            return "%s is in it twice" % f.path
        seen.add(f.path)
        if f.path == "manifest.json":
            manifests += 1
        elif f.path.startswith("sigs/") and HEX64.match(f.path[5:-4]) and f.path.endswith(".sig") and len(f.path) == 73:
            sigs += 1
        elif blob_of(f.path) is None:
            return "it holds %s, which is not part of a release" % f.path
    if manifests != 1 or sigs > 1:
        return "it is not one release"
    return ""


class Mapping(object):
    """What a seed tells libtorrent: renamed files, file priorities, the pieces had; and what it offers."""

    def __init__(self, count, num_pieces):
        self.renamed = {}
        self.priorities = [0] * count
        self.have = [False] * num_pieces
        self.offered = {}            # torrent path: the file on disk (relative to the folder)
        self.held = []               # torrent paths not offered
        self.watch = {}              # file on disk (relative): (size, mtime_ns) when it was checked

    def offer(self, f, rel, st):
        self.renamed[f.index] = rel
        self.priorities[f.index] = 4
        for p in range(f.first, f.first + f.pieces):
            if 0 <= p < len(self.have):
                self.have[p] = True
        self.offered[f.path] = rel
        if st is not None:
            self.watch[rel] = (st.st_size, st.st_mtime_ns)

    def hold(self, f):
        self.renamed[f.index] = ABSENT % f.index
        self.priorities[f.index] = 0
        self.held.append(f.path)


def _stat(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    if not os.path.isfile(path) or os.path.islink(path):
        return None
    return st


def _small_file_matches(path, f):
    """A manifest or signature copy whose size and merkle root are the torrent file's."""
    try:
        with open(path, "rb") as fh:
            data = fh.read(64 << 20)
    except OSError:
        return False
    return len(data) == f.size and (f.root is None or make.merkle_root(data) == f.root)


def _hash(cache, key, path, check):
    """cache.hash(key, path), with check() asked between the 1 MB pieces of a file it reads (Interrupted when it
    says to stop: the file is closed, and nothing of it is kept)."""
    if check is None:
        return cache.hash(key, path)
    st = os.stat(path)
    sha = cache.get(key, st)
    if sha is not None:
        return sha
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            if check():
                raise Interrupted()
            b = f.read(PIECE)
            if not b:
                break
            h.update(b)
    sha = h.hexdigest()
    st2 = os.stat(path)
    if (st2.st_size, st2.st_mtime_ns) == (st.st_size, st.st_mtime_ns):
        cache.put(key, st, sha)
    return sha


def for_folder(files, num_pieces, folder, manifests, release_rel, cache, log=None, check=None):
    """The mapping of a torrent onto an installed folder. manifests: the releases whose paths may hold its blobs
    (the one installed first; a newer one too while seeding it); release_rel: where the folder keeps the release's
    manifest.json and sigs/ (relative to the folder, forward slashes); cache: the folder's hashing.HashCache;
    check: asked between files and pieces (Interrupted when it says to stop)."""
    log = log or (lambda s: None)
    by_sha = {}
    protected = set()
    for man in manifests:
        for p in sorted(man.files):
            if man.is_protected(p):
                protected.add(p.lower())
            else:
                by_sha.setdefault(man.files[p].sha256, []).append(p)
    m = Mapping(len(files), num_pieces)
    try:
        for f in files:
            if f.pad:
                continue
            if check is not None and check():
                raise Interrupted()
            if f.path == "manifest.json" or f.path.startswith("sigs/"):
                rel = release_rel + "/" + f.path
                full = os.path.join(folder, *rel.split("/"))
                st = _stat(full)
                if st is not None and _small_file_matches(full, f):
                    m.offer(f, rel, st)
                else:
                    m.hold(f)
                continue
            sha = blob_of(f.path)
            chosen = None
            for p in by_sha.get(sha, []) if sha else []:
                if p.lower() in protected:
                    continue
                full = os.path.join(folder, *p.split("/"))
                st = _stat(full)
                if st is None or st.st_size != f.size:
                    continue
                try:
                    got = _hash(cache, p, full, check)
                except OSError:
                    continue
                if got == sha:
                    st2 = _stat(full)
                    if st2 is not None and (st2.st_size, st2.st_mtime_ns) == (st.st_size, st.st_mtime_ns):
                        chosen = (p, st2)
                        break
            if chosen is None:
                m.hold(f)
            else:
                m.offer(f, chosen[0], chosen[1])
    finally:
        _save(cache)                    # what was hashed is kept, also when the mapping was stopped
    return m


def _save(cache):
    """The hash cache written; it only saves time later, so a failure to write it never stops a seed run."""
    try:
        cache.save()
    except OSError:
        pass


def for_store(files, num_pieces, store, cache, check=None):
    """The mapping of a torrent onto the publisher's store, where its files are under their own names: each blob
    checked against its SHA-256 (cache: the store's hashing.HashCache), the manifest and signature against the
    torrent's roots. check: asked between files and pieces (Interrupted when it says to stop)."""
    m = Mapping(len(files), num_pieces)
    try:
        for f in files:
            if f.pad:
                continue
            if check is not None and check():
                raise Interrupted()
            full = os.path.join(store, *f.path.split("/"))
            st = _stat(full)
            sha = blob_of(f.path)
            ok = False
            if st is not None and st.st_size == f.size:
                if sha:
                    try:
                        ok = _hash(cache, f.path, full, check) == sha
                    except OSError:
                        ok = False
                else:
                    ok = _small_file_matches(full, f)
            if ok:
                m.offer(f, f.path, _stat(full))
            else:
                m.hold(f)
    finally:
        _save(cache)                    # what was hashed is kept, also when the mapping was stopped
    return m


def changed(folder, watch):
    """The files of a mapping whose size or modification time moved since they were checked, or that are gone."""
    out = []
    for rel, (size, mtime) in watch.items():
        st = _stat(os.path.join(folder, *rel.split("/")))
        if st is None or (st.st_size, st.st_mtime_ns) != (size, mtime):
            out.append(rel)
    return out


def for_download(files, wanted, num_pieces):
    """A download of the blobs `wanted` ({sha: size}) into the update's cache: each to <sha>.bt, priority 4; every
    other file a path that never exists, priority 0."""
    m = Mapping(len(files), num_pieces)
    for f in files:
        if f.pad:
            continue
        sha = blob_of(f.path)
        if sha in wanted and wanted[sha] == f.size:
            m.renamed[f.index] = sha + ".bt"
            m.priorities[f.index] = 4
            m.offered[f.path] = sha + ".bt"
        else:
            m.hold(f)
    return m


def hash_cache(root):
    """The hash cache of a folder or a store: <root>/.deltapatcher/hashes.json (the one updates use)."""
    return hashing.HashCache(os.path.join(root, ".deltapatcher", "hashes.json"))
