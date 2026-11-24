"""Publish: turn a folder into a store (manifest.json + blobs/<sha256>).

The store is what a web server serves and what `deploy` sends to a host. Publishing again only
copies files whose content is new; the previous manifest is kept as manifest.previous.json so
clients that are halfway through an update can still fetch the blobs they started with.
"""

import os
import shutil

from . import hashing, signing
from .manifest import (BLOB_DIR, MANIFEST_NAME, PREVIOUS_NAME, Entry, Manifest, ManifestError,
                       check_path, rule_matches)

IGNORE_FILE = ".deltaignore"
DEFAULT_IGNORE = [".git/", ".svn/", ".hg/", ".deltapatcher/", "__pycache__/", "Thumbs.db",
                  "desktop.ini", ".DS_Store", "*.tmp", IGNORE_FILE]
HASH_CACHE = ".deltapatcher-hashcache.json"
HTACCESS = ".htaccess"

# Apache (2.2 and 2.4) for a store: serve the manifests, the blobs and the signatures and nothing else,
# never list a folder, give blobs (which have no extension) a type, and set the cache headers.
HTACCESS_TEXT = r"""# Written by DeltaPatcher: Apache serves this store and nothing else from it.
# The manifests are never cached; blobs and signatures never change, so they are cached for a year.

Options -Indexes

# blobs have no file extension: give them a type, or some servers refuse them
<FilesMatch "^[0-9a-f]{64}$">
    ForceType application/octet-stream
</FilesMatch>
<FilesMatch "^[0-9a-f]{64}\.sig$">
    ForceType application/json
</FilesMatch>

<IfModule mod_headers.c>
    <FilesMatch "^manifest(\.previous)?\.json$">
        Header set Cache-Control "no-cache"
    </FilesMatch>
    <FilesMatch "^[0-9a-f]{64}(\.sig)?$">
        Header set Cache-Control "public, max-age=31536000, immutable"
    </FilesMatch>
</IfModule>

# everything else is refused: this file, half-uploaded .part files, anything put here by hand
<FilesMatch "^(?!manifest(\.previous)?\.json$|[0-9a-f]{64}(\.sig)?$)">
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


def publish(src, store, name="", version="", protect=None, prune=True, progress=None, log=None,
            signer=None, htaccess=True, leave_out=None):
    """Publish the folder `src` into `store`. Returns a PublishResult.

    progress(done, total, label) and log(text) are optional callbacks. `signer` is a signing key
    (signing.load_key) to sign the manifest with; `htaccess` writes the store's .htaccess for Apache.
    `protect` and `leave_out` are rules (manifest.rule_matches): files players keep once they have
    them, and files the release leaves out (with DEFAULT_IGNORE and the folder's .deltaignore).
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
        with open(mpath, "rb") as f:
            data = f.read()
        log("nothing changed since the last publish: the manifest is kept as it was")
        if signer is not None:
            _write_sig(store, data, signer, log)
    else:
        data = man.to_json()
        if signer is not None:
            _write_sig(store, data, signer, log)     # the signature is there before the manifest it signs
        # new manifest written aside first, so a store being served is never without one
        with open(mpath + ".tmp", "wb") as f:
            f.write(data)
        if os.path.isfile(mpath):
            prev = os.path.join(store, PREVIOUS_NAME)
            shutil.copyfile(mpath, prev + ".tmp")
            os.replace(prev + ".tmp", prev)
        os.replace(mpath + ".tmp", mpath)
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

    res.files = len(entries)
    res.bytes = man.bytes
    res.blobs = len(man.blobs())
    res.manifest = man
    log("published %s" % res.summary())
    return res
