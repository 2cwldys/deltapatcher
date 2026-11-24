"""The publisher's side: a release shared by torrent gets release.torrent and release.item in its store, written
before its manifest is put in place, and sent with it (deploy sends both before the manifest).

release.torrent is the store's own files as a BitTorrent v2 torrent (make.py), named as the project's torrent is,
with the address's parent folder as its web seed when the address ends in that name. release.item is its signed
pointer (item.py), numbered by the release's publish time. The same release published again keeps both as they
are, so its number does not move; a change to the signature, the name or the web seed makes a new torrent and a
new item numbered after the one before.
"""

import hashlib
import os
import time

from ..manifest import BLOB_DIR, Manifest, ManifestError
from . import ITEM_FILE, TORRENT_FILE, TorrentError, item, make


def _read(path, limit):
    try:
        with open(path, "rb") as f:
            return f.read(limit)
    except OSError:
        return None


def _replace(path, data):
    """Write a file whole beside, then put it in place; tried again for a moment when another program has it
    open (a seed run reading it, on Windows)."""
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "wb") as f:
        f.write(data)
    for attempt in range(50):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 49:
                os.remove(tmp)
                raise
            time.sleep(0.05)


def write(store, raw, sig, tinfo, web_seed, signer, log=None, cancel=None, progress=None):
    """release.torrent and release.item of the release whose manifest is `raw` (its signature `sig`), in `store`.
    signer: the publisher's signing key, which signs the item. Returns {"infohash", "seq", "made"}."""
    from cryptography.hazmat.primitives import serialization
    from .. import signing
    log = log or (lambda s: None)
    try:
        man = Manifest.from_json(raw)
    except ManifestError as e:
        raise TorrentError("the release's manifest cannot be read: %s" % e, "bad-manifest")
    sha = hashlib.sha256(raw).hexdigest()
    cache = make.Cache(os.path.join(store, make.CACHE_NAME))
    try:
        data, ih = make.make(tinfo["name"], raw, (sha + ".sig", sig) if sig else None, man.blobs(),
                             os.path.join(store, BLOB_DIR), web_seed, cache, cancel, progress)
    except make.MakeError as e:
        raise TorrentError("the release's torrent cannot be made: %s" % e, "torrent-failed")
    pub = signer.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    old_item = None
    old_bytes = _read(os.path.join(store, ITEM_FILE), 4096)
    if old_bytes:
        try:
            old_item = item.read(old_bytes, signing.key_text(pub), tinfo["id"])
        except TorrentError:
            old_item = None
    old_torrent = _read(os.path.join(store, TORRENT_FILE), 64 << 20)
    if old_item is not None and old_item.m == sha and old_item.ih == ih and old_torrent == data:
        cache.save()
        return {"infohash": ih.hex(), "seq": old_item.seq, "made": False}
    seq = item.seq_for(man.published, old_item.seq if old_item is not None else 0)
    item_bytes = item.make(signer, tinfo["id"], ih, sha, seq)
    _replace(os.path.join(store, TORRENT_FILE), data)
    _replace(os.path.join(store, ITEM_FILE), item_bytes)
    cache.save()
    log("made the release's torrent %s (%d files%s) and its release item, number %d"
        % (tinfo["name"], len(man.blobs()) + 1 + (1 if sig else 0), (", web seed " + web_seed) if web_seed else "",
           seq))
    return {"infohash": ih.hex(), "seq": seq, "made": True}
