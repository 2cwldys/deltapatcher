"""Parts indexes: a big archive release file described as the pieces it is made of, so that an update can rebuild it
byte for byte from the pieces a player already has, the pieces that changed (fetched from its blob with HTTP Range
requests) and the small structural bytes the index carries (rebuild.py). Nothing is ever recompressed: every piece
is the archive's own bytes, and a rebuilt file is used only when it is the manifest's SHA-256.

A parts index is stored as parts/<sha256 of the index's bytes> beside the blobs, and the manifest entry of its file
names it ("parts"). It is zlib-compressed JSON, sorted keys and no spaces, so the same file always gives the same
index bytes:

    {"format": 1, "kind": "zip", "file": "<sha256 of the file>", "size": <file size>,
     "segments": [["l", "<base64 bytes>"], ["p", "<sha256>", <size>], ...]}

"kind" is the archive's (KINDS): zip, tar, 7z or rar, found by its structure, never by its name (find_layout()). The
segments, in order, cover every byte of the file exactly once: each starts where the one before ended.
  "p"  a piece: the packed data of a file in the archive, a data area: a zip member's compressed data
       ([data_start, data_start + compress_size), data_start read from that member's own local header: 30 bytes,
       then its name and its local extra field, which can differ from the central one), a tar member's data, a 7z
       packed stream, the data area of a RAR file or service block (layouts.py). A piece smaller than PIECE_MIN is a
       literal instead.
  "l"  a literal: everything else (headers, data descriptors, padding, gaps, a stub before the first member, a
       directory or header at the end, the end records), joined where neighbours are literals.

Only a file of PARTS_MIN bytes or more gets an index, and only when its kind's reader finds its data areas (for a zip:
zipfile reads its central directory, every member's local header is where the central directory says, no two members
overlap; zip64 works through zipfile's own reading), and the index stays small: its literals at most a quarter of the
file (LITERAL_SHARE) and LITERAL_MAX bytes, at most SEGMENTS_MAX segments, at most INDEX_MAX bytes. A 7z of one
folder (one solid block, or one file) gets none: nothing of it could be reused alone. make_index() says why an
archive gets none, as a reason code (reason() says it in words).

A reader treats every index as untrusted input, though its hash is in the (signed) manifest: at most INDEX_MAX bytes
compressed and JSON_MAX decompressed (the most JSON an index can have), at most SEGMENTS_MAX segments that tile
exactly "size", "file" the entry's own SHA-256, and its literals within the writer's limits. Before the JSON is parsed
its text must have the shape an index has (one object, at most SEGMENTS_MAX + 1 lists and 3 * SEGMENTS_MAX + 4
commas, no escapes, no string longer than the base64 of the most literal bytes the file may have, no number longer
than 20 characters), and it is parsed with no float, so a small hostile index cannot make the parser build millions
of objects or one huge one. The bytes and the text are each let go once the next form of them is made. An index that
fails any of it is not used, and its file is downloaded whole.
"""

import base64
import binascii
import hashlib
import json
import os
import re
import struct
import zipfile
import zlib

from . import layouts
from .layouts import (WHY_ENCRYPTED, WHY_LONG_HEADER, WHY_NO_LZMA, WHY_NOT_7Z, WHY_NOT_RAR, WHY_NOT_TAR,
                      WHY_ONE_FILE, WHY_PAX_RECORDS, WHY_PIECES, WHY_SOLID, WHY_SPARSE)
from .manifest import HEX64

FORMAT = 1
KIND = "zip"
KINDS = (KIND, "tar", "7z", "rar")   # the archives an index describes: its "kind"
PARTS_MIN = 8 << 20          # a release file this big or bigger gets a parts index, when it is an archive of KINDS
PIECE_MIN = 1 << 10          # a member's compressed data smaller than this travels in the index, as a literal
INDEX_MAX = 64 << 20         # an index is at most this many bytes, compressed
SEGMENTS_MAX = 1000000       # and has at most this many segments
LITERAL_SHARE = 4            # its literals are at most a quarter of the file
LITERAL_MAX = 48 << 20       # and at most this many bytes
NUMBER_DIGITS = 20           # a number in an index is a size: never longer than this
B64_MAX = 4 * -(-LITERAL_MAX // 3)   # the base64 of LITERAL_MAX bytes: the longest literal an index has, as text
# the most JSON an index can have, for writer and reader alike: SEGMENTS_MAX segments as long as a piece's can be (and
# the comma after it), the base64 of LITERAL_MAX bytes of literals, and the rest of the object (about 153.6 MiB)
JSON_MAX = (SEGMENTS_MAX * len('["p","%s",%s],' % ("0" * 64, "9" * NUMBER_DIGITS)) + B64_MAX
            + len('{"file":"%s","format":%d,"kind":"%s","segments":[],"size":%s}'
                  % ("0" * 64, FORMAT, max(KINDS, key=len), "9" * NUMBER_DIGITS)))
CHUNK = 1 << 20
CACHE_NAME = ".deltapatcher-partscache.json"     # in a store: which blobs have an index, so publishing reads each once
# what decides an index: a store's cache from other rules is not used
READERS = 3                  # the readers' version: a store's cache or a members list from others is read again
RULES = "%s:%d:%d:%d:%d:%d:readers %d" % (",".join(KINDS), FORMAT, PARTS_MIN, PIECE_MIN, LITERAL_SHARE, LITERAL_MAX,
                                          READERS)

_LOCAL = b"PK\x03\x04"
_KINDS = ((b"Rar!\x1a\x07", "RAR"), (b"7z\xbc\xaf\x27\x1c", "7z"), (b"\x1f\x8b", "gzip"))
# a number of more than NUMBER_DIGITS characters (digits, sign, point, exponent) where JSON has values
_LONG_NUMBER = re.compile(rb"[:,\[][ \t\n\r]*[-+.0-9eE]{%d}" % (NUMBER_DIGITS + 1))

# why a file of PARTS_MIN bytes or more gets no index, as make_index() gives it: a short code, which a store's cache
# keeps (with its numbers), and words for the publish warning, made from the code when it is said (reason()): new
# words never need an archive read again. The readers of layouts.py give theirs (WHY_PIECES, WHY_SOLID, ...)
WHY_NONE = "none"           # not an archive of KINDS: nothing to say
WHY_UNREADABLE = "unreadable"
WHY_LAYOUT = "layout"
WHY_TOO_BIG = "big"
WHY_LITERAL = "literal"      # "literal:<small>:<outside>:<size>": too much would travel inside the index (_too_much())
_WORDS = {
    WHY_NONE: "",
    WHY_UNREADABLE: "Python's zip reader cannot open it",
    WHY_LAYOUT: "its files overlap or are not where its directory says",
    WHY_PIECES: ("it has more files than an index can hold: at most %s parts, about two for each file (so about %s "
                 "files)" % (format(SEGMENTS_MAX, ","), format(SEGMENTS_MAX // 2, ","))),
    WHY_TOO_BIG: "its index would be larger than %d MiB" % (INDEX_MAX >> 20),
    WHY_SOLID: ("packed as one solid stream: with 7-Zip, pack it with solid mode off (-ms=off) so only the files "
                "that changed travel"),
    WHY_ONE_FILE: "it holds one file",
    WHY_ENCRYPTED: "its headers are encrypted",
    WHY_LONG_HEADER: ("a header of it is longer than %d KiB or holds more than %d pax records"
                      % (layouts.EXT_MAX >> 10, layouts.PAX_RECORDS_MAX)),
    WHY_PAX_RECORDS: "it holds more than %s pax records" % format(layouts.PAX_RECORDS_TOTAL, ","),
    WHY_NO_LZMA: "this Python cannot read the 7z's packed header (no lzma)",
    WHY_SPARSE: "it holds a sparse file",
    WHY_NOT_TAR: "not a tar this reader knows",
    WHY_NOT_7Z: "not a 7z this reader knows",
    WHY_NOT_RAR: "not a RAR this reader knows",
}
_REASON = re.compile(r"\A(?:%s|%s:\d{1,20}:\d{1,20}:[1-9]\d{0,19})\Z"
                     % ("|".join(map(re.escape, _WORDS)), WHY_LITERAL))


class PartsError(ValueError):
    """A parts index that must not be used."""


class Stopped(Exception):
    """Making an index was stopped (publish's cancel)."""


def is_reason(code):
    """`code` is a reason make_index() gives, as a store's cache keeps it."""
    return isinstance(code, str) and bool(_REASON.match(code))


def reason(code):
    """The words of the reason `code` (make_index()'s), for the publish warning; '' for anything else."""
    if code in _WORDS:
        return _WORDS[code]
    if not is_reason(code):
        return ""
    _kind, small, outside, size = code.split(":")
    return _too_much(int(small), int(outside), int(size))


def _mib(n, up=False):
    """n bytes in MiB, to a tenth: rounded up with `up`, else to the nearest."""
    tenths = -(-n * 10 // (1 << 20)) if up else (n * 10 + (1 << 19)) // (1 << 20)
    return "%d.%d MiB" % divmod(tenths, 10)


def _too_much(small, outside, size):
    """Why an index would not stay small, in words: what would travel inside it, and the limit that broke. `small`:
    the bytes of the members under PIECE_MIN; `outside`: the bytes that are no member's (headers, data descriptors,
    gaps, its directory, a program before its first member); both travel inside an index. The limit is a quarter of
    the file (LITERAL_SHARE), or LITERAL_MAX when that is less: said as a share of the file, or as a size. The total
    is rounded up, so it never reads as the limit or below it."""
    total = small + outside
    if size // LITERAL_SHARE <= LITERAL_MAX:
        return ("%d%% of it would travel inside its index, more than a quarter: its files under 1 KiB (%d%%) and the "
                "bytes outside its files (%d%%)" % (-(-total * 100 // size), (small * 100 + size // 2) // size,
                                                     (outside * 100 + size // 2) // size))
    return ("%s of it would travel inside its index, more than %d MiB: its files under 1 KiB (%s) and the bytes "
            "outside its files (%s)" % (_mib(total, up=True), LITERAL_MAX >> 20, _mib(small), _mib(outside)))


def zip_layout(f, size):
    """The members of the zip in the open binary file f of `size` bytes: ([(header_offset, data_start, data_end)] sorted
    by offset, the start of its central directory, ''), or (None, None, why it is not one, a reason code):
    WHY_UNREADABLE when zipfile cannot read its central directory, WHY_LAYOUT when a member's local header is not
    where the central directory says, a member runs into the central directory, or two members overlap."""
    try:
        zf = zipfile.ZipFile(f)
    except Exception:                       # BadZipFile, a name that does not decode, a version it does not read...
        return None, None, WHY_UNREADABLE
    try:
        infos = zf.infolist()
        start_dir = zf.start_dir
    finally:
        zf.close()
    if not isinstance(start_dir, int) or not 0 <= start_dir <= size:
        return None, None, WHY_UNREADABLE
    out = []
    end = 0
    for info in sorted(infos, key=lambda i: i.header_offset):
        off = info.header_offset
        if off < end or off + 30 > start_dir:
            return None, None, WHY_LAYOUT   # overlaps the member before, or no room for its header
        f.seek(off)
        head = f.read(30)
        if len(head) != 30 or head[:4] != _LOCAL:
            return None, None, WHY_LAYOUT
        name_len, extra_len = struct.unpack("<HH", head[26:30])
        start = off + 30 + name_len + extra_len
        end = start + info.compress_size
        if end > start_dir:
            return None, None, WHY_LAYOUT
        out.append((off, start, end))
    return out, start_dir, ""


def zip_members(f, size):
    """The members of the zip in the open binary file f of `size` bytes, as ([(header_offset, data_start, data_end)]
    sorted by offset, the start of its central directory); None when it is not one (zip_layout() says why)."""
    members, start_dir, _why = zip_layout(f, size)
    return None if members is None else (members, start_dir)


def find_layout(f, size, cancel=None):
    """(kind, [(start, end)] its data areas, sorted, why) of the archive in the open binary file f of `size` bytes, by
    its structure: a 7z or a RAR by their first bytes, else a tar when its first block is a tar header, else a zip
    when zipfile reads it (layouts.py; zip_layout()). why is '' for an archive that gets an index, else a reason code:
    with data areas for a 7z of one folder (WHY_SOLID, WHY_ONE_FILE), without them (None) for one its reader cannot
    take apart. ('', None, WHY_NONE) for any other file. Data areas that overlap, or that run past the file, are the
    kind's "not this kind" reason. layouts.Stopped when `cancel` (a threading.Event) is set."""
    f.seek(0)
    head = f.read(8)
    if head.startswith(layouts.SEVENZIP_MAGIC):
        kind, (ranges, why), bad = "7z", layouts.sevenzip_layout(f, size, SEGMENTS_MAX, cancel), WHY_NOT_7Z
    elif head.startswith(layouts.RAR5_MAGIC) or head.startswith(layouts.RAR4_MAGIC):
        kind, (ranges, why), bad = "rar", layouts.rar_layout(f, size, SEGMENTS_MAX, cancel), WHY_NOT_RAR
    else:
        kind, (ranges, why), bad = "tar", layouts.tar_layout(f, size, SEGMENTS_MAX, cancel), WHY_NOT_TAR
        if ranges is None and not why:
            members, _start_dir, why = zip_layout(f, size)
            if members is None and why == WHY_UNREADABLE and not head.startswith(_LOCAL):
                return "", None, WHY_NONE
            kind, bad = "zip", WHY_LAYOUT
            ranges = None if members is None else [(a, b) for _off, a, b in members]
    if ranges is None:
        return kind, None, why
    ranges = sorted(r for r in ranges if r[1] > r[0])
    end = 0
    for a, b in ranges:
        if a < end or b > size:
            return kind, None, bad
        end = b
    return kind, ranges, why


def data_ranges(f, size, cancel=None):
    """The data areas [(start, end)] of the archive of any kind in the open binary file f of `size` bytes (a 7z of one
    folder too); None when it is none find_layout() reads (rebuild.py looks for pieces in them)."""
    return find_layout(f, size, cancel)[1]


def layout(ranges, size):
    """The segments of an archive of `size` bytes whose data areas are `ranges` (find_layout()): [(kind, start, end)],
    "p" for a piece and "l" for a literal, neighbouring literals joined."""
    segs = []

    def literal(a, b):
        if b <= a:
            return
        if segs and segs[-1][0] == "l" and segs[-1][2] == a:
            segs[-1] = ("l", segs[-1][1], b)
        else:
            segs.append(("l", a, b))

    pos = 0
    for start, end in ranges:
        literal(pos, start)                 # a stub, a gap, or headers
        if end - start >= PIECE_MIN:
            segs.append(("p", start, end))
        else:
            literal(start, end)
        pos = end
    literal(pos, size)                      # what is after the last data area: a directory, a header, end records
    return segs


def make_index(path, sha, size, cancel=None):
    """(the bytes of the parts index of the file at `path` (its SHA-256 `sha`, `size` bytes), ''), or (None, why it
    gets none): '' under PARTS_MIN, else a reason code (reason() says it in words), the same for the same file
    whenever it is asked (WHY_NONE for a file that is no archive of KINDS; find_layout()'s reasons, WHY_SOLID and
    WHY_ONE_FILE for a 7z of one folder among them; an index that would not be small: WHY_LITERAL with its numbers,
    WHY_PIECES, WHY_TOO_BIG). An archive with no piece at all is all literal, so WHY_LITERAL says it. Pieces are
    hashed as the file is read once, in order. OSError when it cannot be read whole as `sha` (it changed while it
    was read: asked again next time); Stopped when `cancel` (a threading.Event) is set."""
    if size < PARTS_MIN:
        return None, ""
    whole = hashlib.sha256()
    out = []
    with open(path, "rb") as f:
        try:
            kind, ranges, why = find_layout(f, size, cancel)
        except layouts.Stopped:
            raise Stopped()
        if ranges is None or why:
            return None, why
        segs = layout(ranges, size)
        literal = sum(b - a for k, a, b in segs if k == "l")
        if literal > literal_limit(size):
            small = sum(end - start for start, end in ranges if end - start < PIECE_MIN)
            return None, "%s:%d:%d:%d" % (WHY_LITERAL, small, literal - small, size)
        if len(segs) > SEGMENTS_MAX:
            return None, WHY_PIECES
        for k, a, b in segs:
            if cancel is not None and cancel.is_set():
                raise Stopped()
            f.seek(a)
            if k == "l":
                data = f.read(b - a)
                if len(data) != b - a:
                    raise OSError("it changed while it was read")
                whole.update(data)
                out.append(["l", base64.b64encode(data).decode("ascii")])
                continue
            h = hashlib.sha256()
            left = b - a
            while left:
                if cancel is not None and cancel.is_set():
                    raise Stopped()
                data = f.read(min(CHUNK, left))
                if not data:
                    raise OSError("it changed while it was read")
                h.update(data)
                whole.update(data)
                left -= len(data)
            out.append(["p", h.hexdigest(), b - a])
        if f.read(1):
            raise OSError("it changed while it was read")   # longer than `size`
    if whole.hexdigest() != sha:
        raise OSError("it changed while it was read")
    text = json.dumps({"format": FORMAT, "kind": kind, "file": sha, "size": size, "segments": out},
                      sort_keys=True, separators=(",", ":")).encode("ascii")
    if len(text) > JSON_MAX:
        return None, WHY_TOO_BIG
    data = zlib.compress(text, 9)
    if len(data) > INDEX_MAX:
        return None, WHY_TOO_BIG
    return data, ""


def _number(text):
    """An integer of an index's JSON: a size, never more than NUMBER_DIGITS digits."""
    if len(text.lstrip("-")) > NUMBER_DIGITS:
        raise PartsError("a number longer than %d digits" % NUMBER_DIGITS)
    return int(text)


def _no_number(text):
    """A float, NaN or Infinity, which no index has."""
    raise PartsError("a number no index has (%s)" % text[:NUMBER_DIGITS])


def literal_limit(size):
    """The most literal bytes the index of a file of `size` bytes may hold: a quarter of it, and LITERAL_MAX."""
    return min(LITERAL_MAX, size // LITERAL_SHARE)


def _shaped(text, size):
    """PartsError unless the JSON text of the index of a file of `size` bytes has the shape of an index, counted before
    it is parsed: one object, from its first character to its last (the writer puts nothing before or after it), at
    most SEGMENTS_MAX + 1 lists and as many ends of lists, at most 3 * SEGMENTS_MAX + 4 commas (two in a segment, one
    between segments, four between the keys), no backslash (none is in base64, hex or the keys: an index never needs
    an escape), nothing between two quotes longer than the base64 of the most literal bytes the file may have (a
    string, or what is between two: at least 64 characters, a SHA-256), and no number of more than NUMBER_DIGITS
    characters. So a small index cannot make the parser build millions of objects, or one as long as the text."""
    if text[:1] != b"{" or text[-1:] != b"}" or text.count(b"{") != 1 or text.count(b"}") != 1:
        raise PartsError("not the shape of an index (one object)")
    if text.count(b"\\"):
        raise PartsError("not the shape of an index (an escape)")
    lists = text.count(b"[")
    if lists > SEGMENTS_MAX + 1 or text.count(b"]") != lists:
        raise PartsError("not the shape of an index (more lists than %d segments have)" % SEGMENTS_MAX)
    if text.count(b",") > 3 * SEGMENTS_MAX + 4:
        raise PartsError("not the shape of an index (more commas than %d segments have)" % SEGMENTS_MAX)
    longest = max(64, 4 * -(-literal_limit(size) // 3))
    if re.search(b'"[^"]{%d}' % (longest + 1), text):
        raise PartsError("not the shape of an index (a string longer than %d characters)" % longest)
    if _LONG_NUMBER.search(text):
        raise PartsError("not the shape of an index (a number longer than %d characters)" % NUMBER_DIGITS)


def read_index(data, entry, sized=False):
    """The segments of the parts index `data` (its bytes as fetched) of the manifest entry `entry`, checked:
    [(kind, offset, length, payload)], payload the literal's bytes for "l" and the piece's SHA-256 for "p"; with
    `sized`, (those segments, the bytes of its JSON). PartsError when it must not be used: not the index the entry
    names, over a limit, not shaped as an index (_shaped(), checked before it is parsed), not this file's, segments
    that do not tile it exactly, or literals over literal_limit(). The JSON's bytes go once its text is made, the
    text once it is parsed, and each parsed segment once it is read."""
    if not isinstance(data, bytes) or len(data) > INDEX_MAX:
        raise PartsError("larger than %d bytes" % INDEX_MAX)
    if entry.parts is None or hashlib.sha256(data).hexdigest() != entry.parts:
        raise PartsError("not the index the manifest names (its SHA-256 differs)")
    d = zlib.decompressobj()
    try:
        text = d.decompress(data, JSON_MAX + 1)
    except zlib.error as e:
        raise PartsError("not zlib data (%s)" % e)
    if len(text) > JSON_MAX or d.unconsumed_tail:
        raise PartsError("more than %d bytes of JSON" % JSON_MAX)
    if not d.eof or d.unused_data:
        raise PartsError("not one whole zlib stream")
    _shaped(text, entry.size)
    json_bytes = len(text)
    try:
        text = text.decode("utf-8")
        doc = json.loads(text, parse_int=_number, parse_float=_no_number, parse_constant=_no_number)
    except PartsError:
        raise
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        raise PartsError("not JSON (%s)" % e)
    finally:
        text = None
    if not isinstance(doc, dict) or doc.get("format") != FORMAT or isinstance(doc.get("format"), bool) \
            or doc.get("kind") not in KINDS:
        raise PartsError("not a parts index of format %d" % FORMAT)
    if doc.get("file") != entry.sha256:
        raise PartsError("the index of another file")
    size = doc.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size != entry.size:
        raise PartsError("the index of a file of another size")
    raw = doc.get("segments")
    if not isinstance(raw, list) or len(raw) > SEGMENTS_MAX:
        raise PartsError("no segments, or more than %d" % SEGMENTS_MAX)
    out = []
    pos = 0
    literal, most = 0, literal_limit(size)
    for i, seg in enumerate(raw):
        raw[i] = None                       # each parsed segment goes once it is read
        if not isinstance(seg, list) or not seg:
            raise PartsError("a segment that is not one")
        if seg[0] == "l" and len(seg) == 2 and isinstance(seg[1], str):
            try:
                payload = base64.b64decode(seg[1].encode("ascii"), validate=True)
            except (binascii.Error, UnicodeEncodeError, ValueError):
                raise PartsError("a literal that is not base64")
            if not payload:
                raise PartsError("an empty literal")
            length = len(payload)
            literal += length
            if literal > most:
                raise PartsError("more literal bytes than a quarter of the file or %d MiB" % (LITERAL_MAX >> 20))
        elif seg[0] == "p" and len(seg) == 3 and isinstance(seg[1], str) and HEX64.match(seg[1]) and \
                isinstance(seg[2], int) and not isinstance(seg[2], bool) and seg[2] > 0:
            payload, length = seg[1], seg[2]
        else:
            raise PartsError("a segment that is not a literal or a piece")
        out.append((seg[0], pos, length, payload))
        pos += length
        if pos > size:
            raise PartsError("segments longer than the file")
    if pos != size:
        raise PartsError("segments that do not cover the file")
    return (out, json_bytes) if sized else out


def archive_kind(path):
    """What archive the file at `path` is, among those DeltaPatcher sends whole whenever they change, by its first
    bytes: "RAR", "7z", "gzip", "zip" (one zipfile can read: one that got no parts index), or '' for any other file."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
            for magic, kind in _KINDS:
                if head.startswith(magic):
                    return kind
            if head.startswith(_LOCAL):
                return "zip"
            try:
                zipfile.ZipFile(f).close()
            except Exception:
                return ""
            return "zip"
    except OSError:
        return ""


def solid_rar(path):
    """The file at `path` is a solid RAR (its main header says so): each of its files is packed after the one before,
    so a player reuses only its files before the first one that changed."""
    try:
        with open(path, "rb") as f:
            return layouts.rar_solid(f, os.path.getsize(path))
    except OSError:
        return False


class StoreCache(object):
    """Which blobs of a store have a parts index: {blob sha256: the index's sha256, or why it has none (make_index's
    reason code, whose words are made when they are said)}, saved in the store as CACHE_NAME, so publishing again
    reads only new blobs. A blob never changes, so its answer holds while RULES do. Anything else in the file (a
    reason kept in words, as this work first kept it) is not taken: that blob is read again."""

    def __init__(self, path):
        self.path = path
        self.blobs = {}
        self.dirty = False
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if isinstance(doc, dict) and doc.get("rules") == RULES and isinstance(doc.get("blobs"), dict):
                self.blobs = {k: v for k, v in doc["blobs"].items()
                              if HEX64.match(str(k)) and isinstance(v, str) and (HEX64.match(v) or is_reason(v))}
        except (OSError, ValueError, RecursionError):
            pass

    def get(self, sha):
        return self.blobs.get(sha)

    def put(self, sha, index):
        if self.blobs.get(sha) != index:
            self.blobs[sha] = index
            self.dirty = True

    def keep_only(self, shas):
        gone = [k for k in self.blobs if k not in shas]
        for k in gone:
            del self.blobs[k]
        self.dirty = self.dirty or bool(gone)

    def save(self):
        if not self.dirty:
            return
        tmp = "%s.%d.tmp" % (self.path, os.getpid())
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"format": FORMAT, "rules": RULES, "blobs": self.blobs}, f, sort_keys=True,
                      separators=(",", ":"))
        os.replace(tmp, self.path)
        self.dirty = False
