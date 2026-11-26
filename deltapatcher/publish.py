"""Publish: turn a folder into a store (manifest.json + blobs/<sha256>).

The store is what a web server serves and what `deploy` sends to a host. Publishing again only
copies files whose content is new; the previous manifest is kept as manifest.previous.json so
clients that are halfway through an update can still fetch the blobs they started with. The same
files with the same name, version, rules and project details keep the manifest as it was. Each new
release also adds a line to the store's history.jsonl, for statistics (stats.py); that file stays on
this computer.

A release file of parts.PARTS_MIN bytes or more that is a zip, tar, 7z, RAR or gz (a gzip repack.py wrote; found by
its structure, never by its name) gets a parts index, parts/<sha256 of the index>, named by its manifest entry's
"parts" (parts.py): an update then rebuilds it from the files inside it the player already has. Each blob is
indexed once (the store's .deltapatcher-partscache.json remembers the answer), and an index already in the store
is not written again. A big
archive DeltaPatcher cannot patch inside is published as any file is, with one warning line: an archive of those kinds
that gets no index with why (ZIP_WARNING), a gzip with what would let it be (WHOLE_WARNING). A solid RAR that gets an
index is said once too, not as a warning: players reuse only its files before the first one that changed (SOLID_LINE).
A blob that cannot be read while it is indexed is one plain line (UNREAD_LINE), no warning, and nothing is remembered
of it: the next publish reads it again. Only indexes (and what writing one left) ever leave the store's parts/: a file
put there by hand (IIS's web.config) stays.
"""

import json
import os
import shutil

from . import hashing, layouts, parts, repack, signing
from .manifest import (BLOB_DIR, HEX64, MANIFEST_NAME, PARTS_DIR, PREVIOUS_NAME, Entry, Manifest, ManifestError,
                       check_path, rule_matches)
from .torrent import pause

IGNORE_FILE = ".deltaignore"
DEFAULT_IGNORE = [".git/", ".svn/", ".hg/", ".deltapatcher/", "__pycache__/", "Thumbs.db",
                  "desktop.ini", ".DS_Store", "*.tmp", IGNORE_FILE]
HASH_CACHE = ".deltapatcher-hashcache.json"
HTACCESS = ".htaccess"
HISTORY_NAME = "history.jsonl"

# Apache (2.2 and 2.4) for a store: serve the manifests, the blobs, the signatures and, for a release shared by
# torrent, its torrent and item, and nothing else,
# never list a folder, give blobs (which have no extension) a type, and set the cache headers. A parts index,
# parts/<sha256>, has a blob's kind of name: it is served, typed and cached as a blob is.
HTACCESS_TEXT = r"""# Written by DeltaPatcher: Apache serves this store and nothing else from it.
# The manifests (and release.torrent and release.item) are never cached; blobs and signatures never change, so
# they are cached for a year.

Options -Indexes

# blobs have no file extension: give them a type, or some servers refuse them
<FilesMatch "^[0-9a-f]{64}$">
    ForceType application/octet-stream
</FilesMatch>
<FilesMatch "^[0-9a-f]{64}\.sig$">
    ForceType application/json
</FilesMatch>

<FilesMatch "^release\.(torrent|item)$">
    ForceType application/octet-stream
</FilesMatch>

<IfModule mod_headers.c>
    <FilesMatch "^manifest(\.previous)?\.json$">
        Header set Cache-Control "no-cache"
    </FilesMatch>
    <FilesMatch "^release\.(torrent|item)$">
        Header set Cache-Control "no-cache"
    </FilesMatch>
    <FilesMatch "^[0-9a-f]{64}(\.sig)?$">
        Header set Cache-Control "public, max-age=31536000, immutable"
    </FilesMatch>
</IfModule>

# everything else is refused: this file, half-uploaded .part files, anything put here by hand
<FilesMatch "^(?!manifest(\.previous)?\.json$|[0-9a-f]{64}(\.sig)?$|release\.(torrent|item)$)">
    <IfModule mod_authz_core.c>
        Require all denied
    </IfModule>
    <IfModule !mod_authz_core.c>
        Order allow,deny
        Deny from all
    </IfModule>
</FilesMatch>

# Optional: answer only DeltaPatcher (it sends "User-Agent: DeltaPatcher/<version>"). This keeps
# casual browsing out; it is not security, since anyone can send that header. Remove the # to use it.
#<IfModule mod_rewrite.c>
#    RewriteEngine On
#    RewriteCond %{HTTP_USER_AGENT} !^DeltaPatcher/
#    RewriteRule ^ - [R=404,L]
#</IfModule>
"""


class PublishError(Exception):
    pass


STOPPED = "stopped before the new manifest was put in place: the store serves what it did before"
WHOLE_WARNING = ("Warning: %s (%s) is sent whole whenever it changes: turn on Repack archives so they patch inside "
                 "(repack_archives) and each release packs it again one file at a time, so only what changed inside "
                 "it travels.")
# a solid 7z repacking would take, while it is off: in place of the reason's words (parts.reason(WHY_SOLID))
SOLID_REPACK = ("packed as one solid stream: turn on Repack archives so they patch inside (repack_archives) and each "
                "release packs it again one file at a time, or pack it with 7-Zip's solid mode off (-ms=off), so only "
                "the files that changed travel")
REPACKED_LINE = "repacked %s to patch inside: %s, then %s (%s)"
REPACK_UNREAD = "could not read %s to repack it (%s): players get it whole this time"
# a big zip, tar, 7z or RAR that gets no parts index: why, in words made from make_index's reason code (parts.reason())
ZIP_WARNING = "Warning: %s (%s) is sent whole whenever it changes: %s."
SOLID_LINE = ("%s is a solid RAR: players reuse only its files before the first one that changed (packed with solid "
              "mode off, rar -s-, every file that did not change is reused)")
UNREAD_LINE = "could not read %s to index it (%s): players get it whole this time"


class PublishResult(object):
    def __init__(self):
        self.files = 0
        self.bytes = 0
        self.blobs = 0
        self.new_blobs = 0
        self.new_bytes = 0
        self.pruned = 0
        self.skipped = []
        self.left_out = []       # what the leave-out rules kept out of the release (a folder with a trailing /)
        self.manifest = None
        self.parts = 0           # files of the release with a parts index
        self.solid = []          # of those, the solid RARs: only their files before the first that changed are reused
        self.warnings = []       # one line for each big archive DeltaPatcher cannot patch inside
        self.repacked = []       # [{path, from_bytes, to_bytes}]: the archives repacked to patch inside

    def summary(self):
        return ("%d files (%s), %d distinct; %d new blobs (%s) copied, %d pruned"
                % (self.files, human(self.bytes), self.blobs, self.new_blobs, human(self.new_bytes), self.pruned)
                + ("; %d with a parts index" % self.parts if self.parts else "")
                + (" (%d of them a solid RAR, reused only up to its first changed file)" % len(self.solid)
                   if self.solid else "")
                + ("; %d archive%s repacked to patch inside, %s" % (len(self.repacked),
                                                                   "" if len(self.repacked) == 1 else "s",
                                                                   self.growth()) if self.repacked else ""))

    def growth(self):
        """How much bigger (+) or smaller (-) the repacked archives are than the archives published, in all."""
        return growth({"from_bytes": sum(r["from_bytes"] for r in self.repacked),
                       "to_bytes": sum(r["to_bytes"] for r in self.repacked)})


def growth(r):
    """How much bigger (+) or smaller (-) a repacked archive is ({from_bytes, to_bytes}): "+3.1 MB", "-12.0 KB"."""
    n = r["to_bytes"] - r["from_bytes"]
    return ("-" if n < 0 else "+") + human(abs(n))


def human(n):
    n = float(n)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "bytes" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def read_ignore(src):
    """Rules from DEFAULT_IGNORE and the folder's .deltaignore (one a line, # comments; the rules are
    manifest.rule_matches': /a/b.txt that file, /cache/ that folder, *.log a pattern)."""
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
    return any(rule_matches(rel, is_dir, p) for p in pats)


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


def walk(src, pats=None, skipped=None, left_out=None):
    """Sorted relative paths (forward slashes) of the regular files under src. Links and junctions
    are not followed: one could point outside the folder, or back into it forever. `left_out`, when
    given, gets what the rules left out (a folder once, with a trailing /)."""
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
            elif left_out is not None:
                left_out.append(rel + "/")
        dirnames[:] = keep
        for fn in sorted(filenames):
            rel = (reldir + "/" + fn) if reldir else fn
            full = os.path.join(dirpath, fn)
            if ignored(rel, False, pats):
                if left_out is not None:
                    left_out.append(rel)
                continue
            if is_link(full) or not os.path.isfile(full):
                if skipped is not None:
                    skipped.append((rel, "not a regular file"))
                continue
            out.append(rel)
    return sorted(out)


def private_files(src, rels):
    """The files of the release (rels, relative paths under src) that are private project files
    (presets.is_private_file()): never sent to players."""
    from .presets import EXTENSIONS, is_private_file
    return [r for r in rels if r.lower().endswith(EXTENSIONS) and is_private_file(os.path.join(src, *r.split("/")))]


def load_manifest_file(path):
    """The manifest at path, or None when there is none or it cannot be read."""
    try:
        with open(path, "rb") as f:
            return Manifest.from_json(f.read())
    except (OSError, ManifestError):
        return None


def write_htaccess(store):
    """The store's .htaccess for Apache; True when it was written or changed."""
    p = os.path.join(store, HTACCESS)
    data = HTACCESS_TEXT.replace("\r\n", "\n").encode("ascii")
    try:
        with open(p, "rb") as f:
            if f.read() == data:
                return False
    except OSError:
        pass
    with open(p + ".tmp", "wb") as f:
        f.write(data)
    os.replace(p + ".tmp", p)
    return True


def add_history(store, man, new_blobs, before):
    """One line in <store>/history.jsonl for a new release, for statistics: when, its name and version, its
    files and bytes, the blobs it added to the store, and the size of its new and changed files (download_bytes:
    what a player on the release before downloads, less when it rebuilds a big archive; None for the first one). It
    stays on this computer: deploy never sends it and the built-in server never serves it."""
    had = before.blobs() if before is not None else None
    line = {"time": man.published, "name": man.name, "version": man.version, "files": len(man.files),
            "bytes": man.bytes, "new_blobs": new_blobs,
            "download_bytes": None if had is None else sum(s for sha, s in man.blobs().items() if sha not in had)}
    with open(os.path.join(store, HISTORY_NAME), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(line, sort_keys=True) + "\n")


def _write_sig(store, data, signer, log):
    """sigs/<sha of data>.sig, written before the manifest it signs is in place."""
    d = os.path.join(store, signing.SIG_DIR)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, signing.sig_name(hashing.sha256_bytes(data)))
    sig = signing.make_sig(signer, data)
    with open(p + ".tmp", "wb") as f:
        f.write(sig)
    os.replace(p + ".tmp", p)
    log("signed with publisher key %s" % signing.fingerprint(signing.parse_key(signing.public_key_text(signer))))


def _sig_bytes(store, data):
    try:
        with open(os.path.join(store, signing.SIG_DIR, signing.sig_name(hashing.sha256_bytes(data))), "rb") as f:
            return f.read()
    except OSError:
        return None


def _index_there(parts_dir, name):
    """parts/<name> is in the store, and is what its name says."""
    try:
        return hashing.sha256_file(os.path.join(parts_dir, name))[0] == name
    except OSError:
        return False


def _parts_of(store, blob, sha, size, pcache, cancel):
    """(the SHA-256 of the parts index of the blob `sha` (the file `blob`, `size` bytes), '') when it has one, made now
    and put in the store's parts/ when the store has none of it yet; (None, why it has none: '' under PARTS_MIN, else
    make_index's reason code) otherwise, remembered in the store's cache. OSError when the blob cannot be read whole:
    nothing is remembered, and the next publish asks again. PublishError(STOPPED) on a stop."""
    if size < parts.PARTS_MIN:
        return None, ""
    known = pcache.get(sha)
    if known and not HEX64.match(known):
        return None, known                  # why it has none, found before
    d = os.path.join(store, PARTS_DIR)
    if known and _index_there(d, known):
        return known, ""
    try:
        data, why = parts.make_index(blob, sha, size, cancel)
    except parts.Stopped:
        raise PublishError(STOPPED)
    if data is None:
        if why != parts.WHY_NO_LZMA:        # this Python's, not the blob's: the next publish asks again
            pcache.put(sha, why)
        return None, why
    name = hashing.sha256_bytes(data)
    if not _index_there(d, name):           # an index already in the store is not written again
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with open(p + ".tmp", "wb") as f:
            f.write(data)
        os.replace(p + ".tmp", p)
    pcache.put(sha, name)
    return name, ""


def _repacked(store, full, sha, rcache, pcache, cancel):
    """(the sha256 of the repacked blob of the archive at `full` (its SHA-256 `sha`), how many bytes of it are new in
    the store, '', whether it came from the store's repack cache) when it is repacked: from that cache, or now, into
    the store's blobs with its parts index; (None, 0, why it is not, False) otherwise: '' for a file repacking is not
    for, else a code of repack.NotRepacked, kept in the cache unless it is this computer's (repack.transient()).
    OSError when it cannot be read whole as `sha`; PublishError(STOPPED) on a stop."""
    known = rcache.get(sha)
    blob_dir = os.path.join(store, BLOB_DIR)
    if known and HEX64.match(known) and os.path.isfile(os.path.join(blob_dir, known)):
        return known, 0, "", True
    if known and not HEX64.match(known):
        return None, 0, known, False
    if not repack.candidate(full, os.path.getsize(full)):
        return None, 0, "", False
    tmp = os.path.join(blob_dir, sha + ".repack.part")
    try:
        try:
            repack.repack(full, tmp, cancel)
        except repack.NotRepacked as e:
            if not repack.transient(e.code):
                rcache.put(sha, e.code)
            return None, 0, e.code, False
        except layouts.Stopped:
            raise PublishError(STOPPED)
        rsha, rsize = hashing.sha256_file(tmp)
        if hashing.sha256_file(full)[0] != sha:
            raise OSError("it changed while it was repacked")
        index, _why = _parts_of(store, tmp, rsha, rsize, pcache, cancel)
        if index is None:
            rcache.put(sha, repack.WHY_NO_GAIN)
            return None, 0, repack.WHY_NO_GAIN, False
        blob = os.path.join(blob_dir, rsha)
        new = 0
        if not os.path.isfile(blob):
            os.replace(tmp, blob)
            new = rsize
        rcache.put(sha, rsha)
        return rsha, new, "", False
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def publish(src, store, name="", version="", protect=None, prune=True, progress=None, log=None,
            signer=None, htaccess=True, leave_out=None, cancel=None, project=None, extra=None,
            repack_archives=False):
    """Publish the folder `src` into `store`. Returns a PublishResult.

    progress(done, total, label) and log(text) are optional callbacks. `cancel`: an optional threading.Event;
    set, the publish stops before the new manifest is put in place, so the store serves what it did before.
    `signer` is a signing key
    (signing.load_key) to sign the manifest with; `htaccess` writes the store's .htaccess for Apache.
    `protect` and `leave_out` are rules (manifest.rule_matches): files players keep once they have
    them, and files the release leaves out (with DEFAULT_IGNORE and the folder's .deltaignore). A private project
    file in the folder (private_files()) is always left out, with a warning.
    `project`: what the release says about its project (publisher.release_object()); a change to it alone
    makes a new manifest, as a change to a file does.
    `extra`: called as extra(store, manifest bytes, signature bytes or None) once the release's blobs and signature
    are in the store and before its manifest is put in place (the release's torrent and item: torrent/release.py).
    A seed run seeding the store pauses while the manifest changes (torrent/pause.py).
    `repack_archives`: a big .tar.gz, .gz or solid .7z that gets no parts index as it is is repacked so it patches
    inside (repack.py), and the repacked archive is what the release holds; the folder published is never written.
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
    rels = walk(src, read_ignore(src) + [r for r in (leave_out or []) if r and r.strip()],
                skipped=res.skipped, left_out=res.left_out)
    if res.left_out:
        shown = ", ".join(res.left_out[:8]) + (" and %d more" % (len(res.left_out) - 8) if len(res.left_out) > 8 else "")
        log("left out: %s" % shown)
    private = private_files(src, rels)
    if private:
        rels = [r for r in rels if r not in private]
        res.left_out += private
        log("Warning: left out of the release, a private project file (it holds your server's address and login "
            "name): %s" % ", ".join(private))
    for rel, why in res.skipped:
        log("skipped %s (%s)" % (rel, why))
    for rel in rels:
        try:
            check_path(rel)
        except ManifestError as e:
            raise PublishError("%s cannot be published: %s" % (rel, e))

    cache = hashing.HashCache(os.path.join(store, HASH_CACHE))
    pcache = parts.StoreCache(os.path.join(store, parts.CACHE_NAME))
    rcache = repack.Cache(os.path.join(store, repack.CACHE_NAME)) if repack_archives else None
    sources = set()             # the archives as published, repacked or asked about
    entries = []
    total = len(rels)
    for i, rel in enumerate(rels):
        full = os.path.join(src, *rel.split("/"))
        progress(i, total, rel)
        sha = cache.hash(src + "|" + rel, full)
        rwhy = ""
        if rcache is not None:
            sources.add(sha)
            try:
                rsha, new, rwhy, cached = _repacked(store, full, sha, rcache, pcache, cancel)
            except OSError as e:
                rsha = None
                log(REPACK_UNREAD % (rel, e.strerror or e))
            if rsha:
                res.new_blobs += 1 if new else 0
                res.new_bytes += new
                res.repacked.append({"path": rel, "from_bytes": os.path.getsize(full),
                                     "to_bytes": os.path.getsize(os.path.join(blob_dir, rsha))})
                r = res.repacked[-1]
                log(REPACKED_LINE % (rel, human(r["from_bytes"]), human(r["to_bytes"]), growth(r))
                    + (" (as before)" if cached else ""))
                sha = rsha
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
        # a big archive gets a parts index, read from the blob (its bytes are sha's); one that cannot be patched
        # inside is said once: a zip, tar, 7z or RAR with why it got none, a gzip with what would let it be
        try:
            index, why = _parts_of(store, blob, sha, size, pcache, cancel)
        except OSError as e:
            index, why = None, ""
            log(UNREAD_LINE % (rel, e.strerror or e))
        entries.append(Entry(rel, sha, size, index))
        if rwhy:
            res.warnings.append(ZIP_WARNING % (rel, human(size), repack.words(rwhy)))
        elif index is None and why == layouts.WHY_SOLID and rcache is None:
            problem = repack.coder_problem(blob, size)
            res.warnings.append(ZIP_WARNING % (rel, human(size), repack.words(problem) if problem.startswith(
                repack.WHY_CODER) else SOLID_REPACK))
        elif index is None and why and why != parts.WHY_NONE:
            res.warnings.append(ZIP_WARNING % (rel, human(size), parts.reason(why)))
        elif index is None and why == parts.WHY_NONE and parts.archive_kind(blob) == "gzip":
            res.warnings.append(WHOLE_WARNING % (rel, human(size)))
        elif index is not None and rel not in res.solid and parts.solid_rar(blob):
            res.solid.append(rel)
    progress(total, total, "")
    cache.save()
    if rcache is not None:
        rcache.keep_only(sources)
        rcache.save()
    for line in res.warnings:
        log(line)
    for rel in res.solid:
        log(SOLID_LINE % rel)

    man = Manifest(name, version, files=entries, protect=protect, project=project)
    mpath = os.path.join(store, MANIFEST_NAME)
    old = load_manifest_file(mpath)
    if old is not None and (old.files, old.protect, old.name, old.version, old.project) == \
            (man.files, man.protect, man.name, man.version, man.project):
        # the same release again: the manifest (and its date) stay, so the previous release is not rotated out
        man = old
        with open(mpath, "rb") as f:
            data = f.read()
        log("nothing changed since the last publish: the manifest is kept as it was")
        if signer is not None:
            _write_sig(store, data, signer, log)
        if extra is not None:
            extra(store, data, _sig_bytes(store, data))
    else:
        if cancel is not None and cancel.is_set():
            raise PublishError(STOPPED)
        data = man.to_json()
        if signer is not None:
            _write_sig(store, data, signer, log)     # the signature is there before the manifest it signs
        if extra is not None:
            extra(store, data, _sig_bytes(store, data))
        # new manifest written aside first, so a store being served is never without one
        with open(mpath + ".tmp", "wb") as f:
            f.write(data)
        with pause.paused(store, log):
            if os.path.isfile(mpath):
                prev = os.path.join(store, PREVIOUS_NAME)
                shutil.copyfile(mpath, prev + ".tmp")
                os.replace(prev + ".tmp", prev)
            os.replace(mpath + ".tmp", mpath)
        try:
            add_history(store, man, res.new_blobs, old)
        except OSError as e:
            log("the release history was not written (%s): statistics leave this release out" % e)
    if htaccess and write_htaccess(store):
        log("wrote %s (for Apache: serves only the release, no listings)" % HTACCESS)

    if prune:
        keep = set(man.blobs())
        sigs_keep = {signing.sig_name(hashing.sha256_bytes(data))}
        prev_path = os.path.join(store, PREVIOUS_NAME)
        prev = load_manifest_file(prev_path)
        if prev:
            keep.update(prev.blobs())
            with open(prev_path, "rb") as f:
                sigs_keep.add(signing.sig_name(hashing.sha256_bytes(f.read())))
        for fn in os.listdir(blob_dir):
            if fn.endswith(".part") or (len(fn) == 64 and fn not in keep):
                try:
                    os.remove(os.path.join(blob_dir, fn))
                    res.pruned += 0 if fn.endswith(".part") else 1
                except OSError:
                    pass
        sig_dir = os.path.join(store, signing.SIG_DIR)
        if os.path.isdir(sig_dir):
            for fn in os.listdir(sig_dir):
                if fn not in sigs_keep:
                    try:
                        os.remove(os.path.join(sig_dir, fn))
                    except OSError:
                        pass
        # the parts indexes the two releases name stay, and those of the blobs kept (a release kept as it was
        # names none when an older DeltaPatcher wrote it: its indexes wait for the next one)
        pcache.keep_only(keep)
        parts_keep = set(man.parts())
        parts_keep.update(prev.parts() if prev else {})
        parts_keep.update(v for v in pcache.blobs.values() if HEX64.match(v))
        parts_dir = os.path.join(store, PARTS_DIR)
        if os.path.isdir(parts_dir):
            # only indexes and what writing one left (<sha256>.tmp): anything else there (a web.config) is not ours
            for fn in os.listdir(parts_dir):
                if (HEX64.match(fn) and fn not in parts_keep) or (fn.endswith(".tmp") and HEX64.match(fn[:-4])):
                    try:
                        os.remove(os.path.join(parts_dir, fn))
                    except OSError:
                        pass
    try:
        pcache.save()
    except OSError:
        pass                                # it only saves reading the archives again

    res.files = len(entries)
    res.bytes = man.bytes
    res.blobs = len(man.blobs())
    res.parts = sum(1 for e in man.files.values() if e.parts)
    res.manifest = man
    log("published %s" % res.summary())
    return res
