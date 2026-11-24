"""release.item: the signed pointer to a release's torrent, as the DHT keeps it (a BEP 44 mutable item).

    k     the publisher key (its 32 bytes): the key DeltaPatcher signs releases with
    salt  the project's torrent id (its 32 hexadecimal characters)
    seq   the release's publish time in whole seconds, or the item before's number + 1 when that is not larger
    v     {"ih": the torrent's v2 infohash, 32 bytes, "m": the SHA-256 of the release's manifest, hex}
    sig   the Ed25519 signature of "4:salt<n>:<salt>3:seqi<seq>e1:v<v bencoded>" (BEP 44)

release.item holds those five, bencoded. DHT nodes keep only the item with the highest number, and a player
remembers the highest one it accepted and refuses anything lower, from anyone: the publisher's restore of an
older release is a new item with a higher number. The item is made and signed here, never by libtorrent, whose
own put numbers items after whatever the DHT still holds.
"""

import calendar
import hashlib
import time

from .. import signing
from . import TorrentError, bencode

MAX_VALUE = 1000             # BEP 44: a value bencodes to at most 1000 bytes


def salt_of(torrent_id):
    return torrent_id.encode("ascii")


def target(public, torrent_id):
    """Where the item lives in the DHT: SHA-1 of the key and the salt (BEP 44)."""
    return hashlib.sha1(public + salt_of(torrent_id)).digest()


def value(infohash, manifest_sha):
    return {b"ih": bytes(infohash), b"m": manifest_sha.encode("ascii")}


def signed_part(salt, seq, v_bencoded):
    """The bytes an item's signature covers (BEP 44)."""
    out = b""
    if salt:
        out += b"4:salt%d:%s" % (len(salt), salt)
    return out + b"3:seqi%de1:v%s" % (seq, v_bencoded)


def seq_for(published, previous=0):
    """The number of a new item: the release's publish time in whole seconds, or previous + 1 when that is not
    larger."""
    try:
        t = calendar.timegm(time.strptime(published, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        t = int(time.time())
    return max(int(t), int(previous or 0) + 1)


def make(private_key, torrent_id, infohash, manifest_sha, seq):
    """The bytes of release.item for this release, signed with the publisher's signing key."""
    from cryptography.hazmat.primitives import serialization
    pub = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    v = value(infohash, manifest_sha)
    salt = salt_of(torrent_id)
    sig = private_key.sign(signed_part(salt, seq, bencode.encode(v)))
    return bencode.encode({"k": pub, "salt": salt, "seq": seq, "sig": sig, "v": v})


class Item(object):
    """A release item, checked: its signature, its key, its salt and its value."""

    def __init__(self, k, salt, seq, sig, v):
        self.k, self.salt, self.seq, self.sig, self.v = k, salt, seq, sig, v
        self.ih = v[b"ih"]
        self.m = v[b"m"].decode("ascii")

    def to_bytes(self):
        return bencode.encode({"k": self.k, "salt": self.salt, "seq": self.seq, "sig": self.sig, "v": self.v})

    def describe(self):
        return {"seq": self.seq, "infohash": self.ih.hex(), "manifest_sha256": self.m}


def check(k, salt, seq, sig, v, key, torrent_id):
    """An Item from its parts (from release.item, or from the DHT), each checked: the key is `key` (the publisher
    key trusted, "ed25519:..."), the salt is the torrent id, the value is a pointer to a release and the signature
    holds. TorrentError (code bad-item) otherwise."""
    def bad(why):
        raise TorrentError("a release item is refused: %s" % why, "bad-item")
    if not isinstance(k, bytes) or len(k) != 32:
        bad("no publisher key in it")
    try:
        trusted = signing.parse_key(key)
    except signing.SignError:
        bad("no publisher key to check it with")
    if k != trusted:
        bad("it is signed by %s, not by the publisher key %s" % (signing.fingerprint(k), signing.fingerprint(trusted)))
    if salt != salt_of(torrent_id):
        bad("it is for another torrent")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        bad("no number in it")
    if not isinstance(sig, bytes) or len(sig) != 64:
        bad("no signature in it")
    if not isinstance(v, dict) or not isinstance(v.get(b"ih"), bytes) or len(v[b"ih"]) != 32 or \
            not isinstance(v.get(b"m"), bytes):
        bad("it does not point to a release")
    try:
        m = v[b"m"].decode("ascii")
    except UnicodeDecodeError:
        bad("it does not point to a release")
    if len(m) != 64 or any(c not in "0123456789abcdef" for c in m):
        bad("it does not point to a release")
    v_bytes = bencode.encode(v)
    if len(v_bytes) > MAX_VALUE:
        bad("its value is too long")
    if not signing.verify(k, signed_part(salt, seq, v_bytes), sig):
        bad("its signature does not hold")
    return Item(k, salt, seq, sig, v)


def read(data, key, torrent_id):
    """release.item's bytes as an Item, checked (check())."""
    try:
        doc = bencode.decode(data)
    except (bencode.BencodeError, ValueError):
        raise TorrentError("a release item is refused: it is not readable", "bad-item")
    if not isinstance(doc, dict) or set(doc) != {b"k", b"salt", b"seq", b"sig", b"v"}:
        raise TorrentError("a release item is refused: it is not readable", "bad-item")
    return check(doc[b"k"], doc[b"salt"], doc[b"seq"], doc[b"sig"], doc[b"v"], key, torrent_id)


def newest(items, kept_seq=0):
    """The item to follow of several: the one with the highest number, if it is not below the highest one
    accepted before (kept_seq); None when there is none."""
    best = None
    for it in items:
        if it is None or it.seq < kept_seq:
            continue
        if best is None or it.seq > best.seq:
            best = it
    return best
