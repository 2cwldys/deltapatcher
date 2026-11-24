"""A release as a BitTorrent v2 torrent (BEP 52), made here without libtorrent.

The torrent's files are the store's own: manifest.json, sigs/<sha256 of the manifest>.sig and blobs/<sha256> for
every blob the manifest names, under the torrent's name. Every file is hashed as v2 asks: SHA-256 of each 16 KiB
block, a merkle tree over them, its root in the info dictionary and, for a file longer than a piece, the layer of
the tree where one hash covers one piece ("piece layers", beside the info dictionary). The infohash is the
SHA-256 of the info dictionary. No tracker is ever written into it; a web seed only when one is given.

A blob never changes, so what a blob hashes to is kept in a cache file (the store's .deltapatcher-torrentcache.json):
publishing again hashes only the blobs that are new.
"""

import base64
import hashlib
import json
import os
import threading
import time

from .. import __version__
from . import bencode

BLOCK = 16384
ZERO = b"\0" * 32
CACHE_NAME = ".deltapatcher-torrentcache.json"
# the total size each piece length is chosen up to (libtorrent's own steps): 16 KiB pieces up to 2.5 MB, ...
_STEPS = (2684355, 10737418, 42949673, 171798692, 687194767, 2748779069, 10995116278, 43980465111, 175921860444,
          703687441777)


class MakeError(Exception):
    pass


def piece_length_for(total):
    """The piece length of a torrent of `total` bytes: 16 KiB up to 8 MiB, longer as the torrent grows."""
    i = 0
    for step in _STEPS:
        if step >= total:
            break
        i += 1
    return BLOCK << i


def _pow2(n):
    p = 1
    while p < n:
        p <<= 1
    return p


def _up(layer, pad):
    """The layer above: each pair hashed together, `pad` standing in for a missing right half."""
    if len(layer) % 2:
        layer = layer + [pad]
    return [hashlib.sha256(layer[i] + layer[i + 1]).digest() for i in range(0, len(layer), 2)]


def _root(layer, width, pad):
    """The root of a tree whose bottom layer is `layer`, padded with `pad` to `width` (a power of two) nodes."""
    layer = list(layer) + [pad] * (width - len(layer))
    while len(layer) > 1:
        layer = _up(layer, pad)
    return layer[0]


def _pad_hash(levels):
    """The root of a subtree of 2**levels zero leaves."""
    h = ZERO
    for _ in range(levels):
        h = hashlib.sha256(h + h).digest()
    return h


def leaves_of(read):
    """The SHA-256 of each 16 KiB block of what `read` (a function returning the next bytes, b'' at the end)
    gives; and the total size."""
    leaves, size, rest = [], 0, b""
    while True:
        chunk = read(1 << 20)
        if not chunk:
            break
        size += len(chunk)
        data = rest + chunk
        whole = len(data) - len(data) % BLOCK
        for i in range(0, whole, BLOCK):
            leaves.append(hashlib.sha256(data[i:i + BLOCK]).digest())
        rest = data[whole:]
    if rest:
        leaves.append(hashlib.sha256(rest).digest())
    return leaves, size


def hashes_of_leaves(leaves, size, piece_length):
    """(pieces root, piece layer) of a file from its block hashes: the layer is b'' for a file of one piece or
    less, and the root is None for an empty file."""
    if size == 0:
        return None, b""
    per_piece = piece_length // BLOCK
    if len(leaves) <= per_piece:
        return _root(leaves, _pow2(len(leaves)), ZERO), b""
    pieces = []
    for i in range(0, len(leaves), per_piece):
        pieces.append(_root(leaves[i:i + per_piece], per_piece, ZERO))
    levels = per_piece.bit_length() - 1
    root = _root(pieces, _pow2(len(pieces)), _pad_hash(levels))
    return root, b"".join(pieces)


def hashes_of_bytes(data, piece_length):
    pos = [0]

    def read(n):
        chunk = data[pos[0]:pos[0] + n]
        pos[0] += len(chunk)
        return chunk
    leaves, size = leaves_of(read)
    return hashes_of_leaves(leaves, size, piece_length)


def hashes_of_file(path, piece_length):
    with open(path, "rb") as f:
        leaves, size = leaves_of(f.read)
    return hashes_of_leaves(leaves, size, piece_length) + (size,)


def merkle_root(data):
    """The v2 pieces root of a small file's bytes (any piece length gives the same root)."""
    return hashes_of_bytes(data, BLOCK)[0]


class Cache(object):
    """{blob sha: [piece length, root hex, piece layer base64]} of the blobs hashed before."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        self.dirty = False
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self.data = {k: v for k, v in raw.items() if isinstance(v, list) and len(v) == 3}
        except (OSError, ValueError):
            pass

    def get(self, sha, piece_length):
        v = self.data.get(sha)
        if not v or v[0] != piece_length:
            return None
        try:
            return (bytes.fromhex(v[1]) if v[1] else None), base64.b64decode(v[2])
        except (ValueError, TypeError):
            return None

    def put(self, sha, piece_length, root, layer):
        self.data[sha] = [piece_length, root.hex() if root else "", base64.b64encode(layer).decode("ascii")]
        self.dirty = True

    def keep_only(self, shas):
        gone = [k for k in self.data if k not in shas]
        for k in gone:
            del self.data[k]
            self.dirty = True

    def save(self):
        """Write it when it changed, through a temporary file of this process and thread (an update and a seed run's
        look of one folder can both write it)."""
        if not self.dirty:
            return
        tmp = "%s.%d.%d.tmp" % (self.path, os.getpid(), threading.get_ident())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, separators=(",", ":"))
        for attempt in range(40):
            try:
                os.replace(tmp, self.path)
                break
            except PermissionError:                  # Windows: another process reads it for a moment
                if attempt == 39:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                    raise
                time.sleep(0.05)
        self.dirty = False


def _place(tree, rel, node):
    parts = rel.split("/")
    for p in parts[:-1]:
        tree = tree.setdefault(p, {})
        if "" in tree:
            raise MakeError("%s is a file and a folder in one torrent" % p)
    if parts[-1] in tree:
        raise MakeError("%s is in the torrent twice" % rel)
    tree[parts[-1]] = {"": node}


def make(name, manifest, sig, blobs, blob_dir, web_seed="", cache=None, cancel=None, progress=None):
    """The torrent of one release: (its bytes, its v2 infohash, 32 bytes). manifest: the manifest's bytes; sig:
    (its file name, its bytes) or None; blobs: {sha: size} of every blob the manifest names, read from blob_dir.
    web_seed: an http(s) address that serves <it>/<name>/blobs/<sha>, '' for none."""
    progress = progress or (lambda done, total: None)
    files = [("manifest.json", len(manifest))]
    if sig is not None:
        files.append(("sigs/" + sig[0], len(sig[1])))
    files += [("blobs/" + sha, size) for sha, size in sorted(blobs.items())]
    total = sum(size for _r, size in files)
    piece_length = piece_length_for(total)
    tree, layers = {}, {}
    done = 0
    for rel, size in files:
        if cancel is not None and cancel.is_set():
            raise MakeError("stopped")
        if rel == "manifest.json":
            root, layer = hashes_of_bytes(manifest, piece_length)
        elif rel.startswith("sigs/"):
            root, layer = hashes_of_bytes(sig[1], piece_length)
        else:
            sha = rel[len("blobs/"):]
            got = cache.get(sha, piece_length) if cache is not None else None
            if got is None:
                path = os.path.join(blob_dir, sha)
                try:
                    root, layer, real = hashes_of_file(path, piece_length)
                except OSError as e:
                    raise MakeError("cannot read the blob %s: %s" % (sha[:12], e))
                if real != size:
                    raise MakeError("the blob %s is %d bytes, not %d" % (sha[:12], real, size))
                if cache is not None:
                    cache.put(sha, piece_length, root, layer)
            else:
                root, layer = got
        node = {"length": size}
        if root is not None:
            node["pieces root"] = root
        _place(tree, rel, node)
        if layer:
            layers[root] = layer
        done += size
        progress(done, total)
    if cache is not None:
        cache.keep_only(set(blobs))
    info = {"file tree": tree, "meta version": 2, "name": name, "piece length": piece_length}
    torrent = {"info": info, "created by": "DeltaPatcher %s" % __version__}
    if layers:
        torrent["piece layers"] = layers
    if web_seed:
        torrent["url-list"] = [web_seed]
    return bencode.encode(torrent), hashlib.sha256(bencode.encode(info)).digest()


def files_of(torrent):
    """{path inside the torrent: (length, pieces root or None)} of a torrent made by make() (decoded), and its name;
    MakeError when it is not such a torrent."""
    try:
        info = torrent[b"info"]
        name = info[b"name"].decode("utf-8")
        tree = info[b"file tree"]
        if info.get(b"meta version") != 2 or not isinstance(tree, dict):
            raise MakeError("not a v2 torrent")
    except (KeyError, TypeError, AttributeError, UnicodeDecodeError):
        raise MakeError("not a torrent DeltaPatcher made")
    out = {}

    def walk(node, path):
        for k, v in node.items():
            if not isinstance(v, dict):
                raise MakeError("not a torrent DeltaPatcher made")
            if k == b"":
                length = v.get(b"length")
                if not isinstance(length, int) or isinstance(length, bool) or length < 0:
                    raise MakeError("a file of no length in the torrent")
                out["/".join(path)] = (length, v.get(b"pieces root"))
            else:
                try:
                    walk(v, path + [k.decode("utf-8")])
                except UnicodeDecodeError:
                    raise MakeError("not a torrent DeltaPatcher made")
    walk(tree, [])
    return out, name


def infohash(torrent_bytes):
    """The v2 infohash of a torrent's bytes (32 bytes): the SHA-256 of its info dictionary as written."""
    data = bytes(torrent_bytes)
    if data[:1] != b"d":
        raise MakeError("not a torrent")
    i = 1
    try:
        while i < len(data) and data[i:i + 1] != b"e":
            key, i = bencode.decode_at(data, i)
            start = i
            _value, i = bencode.decode_at(data, i)
            if key == b"info":
                return hashlib.sha256(data[start:i]).digest()
    except (bencode.BencodeError, ValueError):
        raise MakeError("not a torrent")
    raise MakeError("a torrent without its info")
