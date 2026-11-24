"""Publish: turn a folder into a store (manifest.json + blobs/<sha256>).

The store is what a web server serves and what `deploy` sends to a host. Publishing again only
copies files whose content is new; the previous manifest is kept as manifest.previous.json so
clients that are halfway through an update can still fetch the blobs they started with.
"""

import fnmatch
import os
import shutil

from . import hashing
from .manifest import (BLOB_DIR, MANIFEST_NAME, PREVIOUS_NAME, Entry, Manifest, ManifestError,
                       check_path)

IGNORE_FILE = ".deltaignore"
DEFAULT_IGNORE = [".git/", ".svn/", ".hg/", ".deltapatcher/", "__pycache__/", "Thumbs.db",
                  "desktop.ini", ".DS_Store", "*.tmp", IGNORE_FILE]
HASH_CACHE = ".deltapatcher-hashcache.json"


class PublishError(Exception):
    pass


class PublishResult(object):
    def __init__(self):
        self.files = 0
        self.bytes = 0
        self.blobs = 0
        self.new_blobs = 0
        self.new_bytes = 0
        self.pruned = 0
        self.skipped = []
        self.manifest = None

    def summary(self):
        return ("%d files (%s), %d distinct; %d new blobs (%s) copied, %d pruned"
                % (self.files, human(self.bytes), self.blobs, self.new_blobs, human(self.new_bytes), self.pruned))


def human(n):
    n = float(n)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "bytes" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def read_ignore(src):
    """Patterns from DEFAULT_IGNORE and the folder's .deltaignore (one glob a line, # comments,
    a trailing / for a folder)."""
    pats = list(DEFAULT_IGNORE)
    try:
        with open(os.path.join(src, IGNORE_FILE), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    pats.append(line.replace("\\", "/"))
    except OSError:
        pass
    return pats


def ignored(rel, is_dir, pats):
    name = rel.rsplit("/", 1)[-1]
    for p in pats:
        if p.endswith("/"):
            if not is_dir:
                continue
            p = p[:-1]
        target = rel if "/" in p else name
        if fnmatch.fnmatchcase(target, p) or fnmatch.fnmatchcase(target.lower(), p.lower()):
            return True
    return False


_LINK_TAGS = (0xA0000003, 0xA000000C)   # IO_REPARSE_TAG_MOUNT_POINT (a junction), IO_REPARSE_TAG_SYMLINK


def is_link(path):
    """A symbolic link, or on Windows a junction. Other reparse points (cloud placeholder files,
    deduplicated files) are ordinary files and are published."""
    if os.path.islink(path):
        return True
    if os.name == "nt":
        try:
            return getattr(os.lstat(path), "st_reparse_tag", 0) in _LINK_TAGS
        except OSError:
            return False
    return False


def walk(src, pats=None, skipped=None):
    """Sorted relative paths (forward slashes) of the regular files under src. Links and junctions
    are not followed: one could point outside the folder, or back into it forever."""
    pats = read_ignore(src) if pats is None else pats
    out = []
    root_len = len(os.path.abspath(src).rstrip("\\/")) + 1
    for dirpath, dirnames, filenames in os.walk(src, followlinks=False):
        reldir = os.path.abspath(dirpath)[root_len:].replace("\\", "/")
        keep = []
        for d in sorted(dirnames):
            rel = (reldir + "/" + d) if reldir else d
            if is_link(os.path.join(dirpath, d)):
                if skipped is not None:
                    skipped.append((rel, "a link or junction"))
                continue
            if not ignored(rel, True, pats):
                keep.append(d)
        dirnames[:] = keep
        for fn in sorted(filenames):
            rel = (reldir + "/" + fn) if reldir else fn
            full = os.path.join(dirpath, fn)
            if ignored(rel, False, pats):
                continue
            if is_link(full) or not os.path.isfile(full):
                if skipped is not None:
                    skipped.append((rel, "not a regular file"))
                continue
            out.append(rel)
    return sorted(out)


def load_manifest_file(path):
    """The manifest at path, or None when there is none or it cannot be read."""
    try:
        with open(path, "rb") as f:
            return Manifest.from_json(f.read())
    except (OSError, ManifestError):
        return None


def publish(src, store, name="", version="", protect=None, prune=True, progress=None, log=None):
    """Publish the folder `src` into `store`. Returns a PublishResult.

    progress(done, total, label) and log(text) are optional callbacks.
    """
    log = log or (lambda s: None)
    progress = progress or (lambda d, t, l: None)
    src = os.path.abspath(src)
    store = os.path.abspath(store)
    if not os.path.isdir(src):
        raise PublishError("no folder %s" % src)
    if os.path.normcase(store) == os.path.normcase(src) or os.path.normcase(store).startswith(os.path.normcase(src) + os.sep):
        raise PublishError("the store must not be inside the folder it publishes")
    blob_dir = os.path.join(store, BLOB_DIR)
    os.makedirs(blob_dir, exist_ok=True)

    res = PublishResult()
    rels = walk(src, skipped=res.skipped)
    for rel, why in res.skipped:
        log("skipped %s (%s)" % (rel, why))
    for rel in rels:
        try:
            check_path(rel)
        except ManifestError as e:
            raise PublishError("%s cannot be published: %s" % (rel, e))

    cache = hashing.HashCache(os.path.join(store, HASH_CACHE))
    entries = []
    total = len(rels)
    for i, rel in enumerate(rels):
        full = os.path.join(src, *rel.split("/"))
        progress(i, total, rel)
        sha = cache.hash(src + "|" + rel, full)
        blob = os.path.join(blob_dir, sha)
        if not os.path.isfile(blob):
            tmp = blob + ".part"
            got, size = hashing.copy_hashing(full, tmp)
            if got != sha:
                # the file changed between the hash and the copy: the copy is what we publish
                sha, blob = got, os.path.join(blob_dir, got)
                cache.put(src + "|" + rel, os.stat(full), got)
            if os.path.isfile(blob):
                os.remove(tmp)
            else:
                os.replace(tmp, blob)
                res.new_blobs += 1
                res.new_bytes += size
        size = os.path.getsize(blob)
        entries.append(Entry(rel, sha, size))
    progress(total, total, "")
    cache.save()

    man = Manifest(name, version, files=entries, protect=protect)
    mpath = os.path.join(store, MANIFEST_NAME)
    old = load_manifest_file(mpath)
    if old is not None and (old.files, old.protect, old.name, old.version) == (man.files, man.protect, man.name, man.version):
        # the same release again: the manifest (and its date) stay, so the previous release is not rotated out
        man = old
        log("nothing changed since the last publish: the manifest is kept as it was")
    else:
        # new manifest written aside first, so a store being served is never without one
        with open(mpath + ".tmp", "wb") as f:
            f.write(man.to_json())
        if os.path.isfile(mpath):
            prev = os.path.join(store, PREVIOUS_NAME)
            shutil.copyfile(mpath, prev + ".tmp")
            os.replace(prev + ".tmp", prev)
        os.replace(mpath + ".tmp", mpath)

    if prune:
        keep = set(man.blobs())
        prev = load_manifest_file(os.path.join(store, PREVIOUS_NAME))
        if prev:
            keep.update(prev.blobs())
        for fn in os.listdir(blob_dir):
            if fn.endswith(".part") or (len(fn) == 64 and fn not in keep):
                try:
                    os.remove(os.path.join(blob_dir, fn))
                    res.pruned += 0 if fn.endswith(".part") else 1
                except OSError:
                    pass

    res.files = len(entries)
    res.bytes = man.bytes
    res.blobs = len(man.blobs())
    res.manifest = man
    log("published %s" % res.summary())
    return res
