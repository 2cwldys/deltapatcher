"""Repacking (publish.py, when a project's setting repack_archives is on): a big .tar.gz, .gz or solid .7z that gets
no parts index as it is is packed again so it patches inside, and the repacked archive is what the release holds. The
files inside stay byte for byte the same; only their packing changes, and every tool still opens the archive.
  .gz      a gzip is written as ONE gzip member, so every gzip reader reads all of it: the original's MTIME, OS, FNAME
           and FCOMMENT (from its first member), XFL 2, and FEXTRA with one subfield "DP": the segment table (a
           version byte, 1; the count of segments, 4 bytes; the deflate length of each, 4 bytes; little endian). Then
           one deflate stream (zlib at level 9, a 15-bit window, memory level 8, the default strategy) with a full
           flush after every segment, which resets the dictionary and ends on a byte, so a segment's bytes depend on
           its content alone; the last segment finishes the stream. Then the CRC32 and ISIZE of all of it. The
           segments of a gzip of a tar the tar walk of layouts.py reads: each tar member's data, and each run of the
           bytes between them (padding, headers, the end blocks); of a gzip of anything else: its content cut where a
           rolling hash of the 64 bytes before the cut has its low 20 bits zero, CDC_MIN to CDC_MAX bytes a chunk.
           Each segment is at most MEMBER_MAX bytes of content; past SEGMENTS_GZ segments, neighbours are merged, the
           two smallest first. layouts.py reads the table (the kind "gz"): each segment of PIECE_MIN or more a piece.
           The repacked gzip is read back before it is used: one single-member reader must read it to its end, to the
           original's length and SHA-256, and each segment must inflate on its own.
  .7z      solid (one folder of several files: layouts.py's WHY_SOLID), when every coder of the folder is one Python's
           own modules decode (Copy, LZMA, LZMA2, the BCJ filters of liblzma, Delta, Deflate, BZip2): written again
           with one folder for each file, LZMA2 at preset 6 with a dictionary of LZMA2_DICT_MAX (or the file's size
           when smaller), the original header's file list (names, times, attributes, empty files and folders,
           anti-items, in their order) and archive properties copied as they are, the header plain or LZMA-encoded
           as the original's was. It is checked before it is used: layouts.py reads it as a 7z of one folder for each
           file, and every file decodes to the original's size and CRC.
Every repack is deterministic: the same archive gives the same bytes on every publish and every computer with the same
zlib and liblzma, and an unchanged file inside gives the same piece in the next version. Bounded as the readers are: at
most SEGMENTS_GZ segments or SEGMENTS_MAX files, REPACK_MAX bytes decoded, the cancel event looked at for every chunk
read or written, and RESERVE bytes kept free on the drive the repacked archive is written to (looked at every 64 MiB
written). bz2 and lzma are used only when this Python has them.
"""

import hashlib
import heapq
import itertools
import json
import operator
import os
import shutil
import struct
import zlib

from . import layouts, parts
from .layouts import lzma
from .manifest import HEX64

try:
    import bz2
except ImportError:                         # a Python built without it: a BZip2 folder is one it cannot decode
    bz2 = None

REPACK_MAX = 16 << 30       # an archive is repacked only when it decodes to at most this many bytes
RESERVE = 256 << 20         # and the drive the repacked archive goes to keeps this many free while it is written
MEMBER_MAX = 1 << 30        # a segment of a repacked gzip holds at most this many bytes of its content
SEGMENTS_GZ = 16000         # and a repacked gzip at most this many segments (its table fits a FEXTRA subfield)
CDC_WINDOW = 64             # a .gz is cut where the rolling hash of the 64 bytes before the cut
CDC_MASK = (1 << 20) - 1    # has these bits zero
CDC_MIN = 256 << 10         # a chunk at least this long
CDC_MAX = 4 << 20           # and at most this long
CDC_STEP = 64 << 10         # the hash is worked out over this many places at a time
LZMA2_PRESET = 6
LZMA2_DICT_MAX = 16 << 20
CHUNK = 1 << 20
CACHE_NAME = ".deltapatcher-repackcache.json"
# what decides a repack: a store's repack cache of other rules is not used
RULES = "repack 2:gzip one member 9 15 8 %d %d:cdc %d %d %d %d:7z lzma2 %d %d" % (
    MEMBER_MAX, SEGMENTS_GZ, CDC_WINDOW, CDC_MASK.bit_length(), CDC_MIN, CDC_MAX, LZMA2_PRESET, LZMA2_DICT_MAX)

# why an archive is not repacked: a code; words() says it. The store's repack cache keeps it, unless it is this
# computer's and not the archive's (transient(): too little space, too little memory, a module this Python lacks)
WHY_GZIP = "gzip"           # a gzip zlib cannot read, or with bytes after its last member
WHY_7Z = "7z"               # a 7z this reader cannot take apart
WHY_CODER = "coder"         # "coder:<name>": a folder coder Python's modules do not decode
WHY_MODULE = "module"       # "module:<name>": a module this Python lacks (bz2, lzma)
WHY_BIG = "big"             # it decodes to more than REPACK_MAX bytes
WHY_PIECES = "pieces"       # more files or segments than an index can hold
WHY_SPACE = "space"         # the drive would keep less than RESERVE free
WHY_MEMORY = "memory"       # this computer ran out of memory repacking it
WHY_DAMAGED = "damaged"     # the original's data does not match its own CRCs, or does not decode
WHY_CHECK = "check"         # the repacked archive did not decode to the original's files
WHY_NO_GAIN = "no-gain"     # repacked, it would still get no parts index
_WORDS = {
    WHY_GZIP: "not repacked: it is not a gzip Python's zlib reads to its end",
    WHY_7Z: "not repacked: it is not a 7z this reader takes apart",
    WHY_BIG: "not repacked: it holds more than %d GiB" % (REPACK_MAX >> 30),
    WHY_PIECES: "not repacked: it holds more files than an index can hold",
    WHY_SPACE: "not repacked: the drive of the store has too little free space",
    WHY_MEMORY: "not repacked: this computer ran out of memory",
    WHY_DAMAGED: "not repacked: its data does not match its own CRCs: the archive is damaged, so it goes out as it is",
    WHY_CHECK: "not repacked: its repacked copy did not decode to the same files",
    WHY_NO_GAIN: "not repacked: repacked, it would still get no parts index",
}

# 7z coders: method id -> (name, what decodes it); the BCJ filters by their old and new ids
_FILTERS = {b"\x03\x03\x01\x03": "X86", b"\x04": "X86", b"\x03\x03\x02\x05": "POWERPC", b"\x05": "POWERPC",
            b"\x03\x03\x04\x01": "IA64", b"\x06": "IA64", b"\x03\x03\x05\x01": "ARM", b"\x07": "ARM",
            b"\x03\x03\x07\x01": "ARMTHUMB", b"\x08": "ARMTHUMB", b"\x03\x03\x08\x05": "SPARC", b"\x09": "SPARC",
            b"\x0a": "ARM64"}
_NAMES = {b"\x00": "Copy", b"\x03\x01\x01": "LZMA", b"\x21": "LZMA2", b"\x04\x01\x08": "Deflate",
          b"\x04\x02\x02": "BZip2", b"\x03": "Delta", b"\x03\x03\x01\x1b": "BCJ2", b"\x03\x04\x01": "PPMd",
          b"\x06\xf1\x07\x01": "AES",
          b"\x04\x01\x09": "Deflate64", b"\x04\xf7\x11\x01": "Zstandard", b"\x04\xf7\x11\x02": "Brotli",
          b"\x04\xf7\x11\x04": "LZ4", b"\x04\xf7\x11\x05": "LZ5", b"\x04\xf7\x11\x06": "Lizard", b"\x0b": "RISC-V",
          b"\x03\x03\x03\x01": "Alpha", b"\x03\x03\x06\x05": "M68"}
_NAMES.update({k: "BCJ " + v.title().replace("Powerpc", "PPC").replace("Armthumb", "ARMT").replace("Arm", "ARM")
               .replace("Sparc", "SPARC").replace("Ia64", "IA64").replace("X86", "x86") for k, v in _FILTERS.items()})
# the gear of the rolling hash: 256 numbers of 32 bits, the same everywhere
_GEAR = [int.from_bytes(hashlib.sha256(b"DeltaPatcher chunk %d" % i).digest()[:4], "little") for i in range(256)]


class NotRepacked(Exception):
    """An archive left as it is: why (a code, words())."""

    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = code


def words(code):
    """The words of a code of NotRepacked, for the publish warning."""
    if code.startswith(WHY_CODER + ":"):
        return ("packed as one solid stream with %s, which repacking does not decode: with 7-Zip, pack it with solid "
                "mode off (-ms=off) so only the files that changed travel" % code.split(":", 1)[1])
    if code.startswith(WHY_MODULE + ":"):
        return "not repacked: this Python has no %s module to decode it" % code.split(":", 1)[1]
    return _WORDS.get(code, "")


def is_reason(code):
    return isinstance(code, str) and (code in _WORDS or (code.startswith((WHY_CODER + ":", WHY_MODULE + ":"))
                                                         and 6 < len(code) < 80))


def transient(code):
    """A reason of this computer and not of the archive, which is never kept: the next publish asks again."""
    return code in (WHY_SPACE, WHY_MEMORY) or code.startswith(WHY_MODULE + ":")


class Cache(object):
    """Which archives a store repacked: {the sha256 of the archive as published: the sha256 of its repacked blob, or
    why it was not repacked (a code of NotRepacked)}, saved in the store as CACHE_NAME and taken while RULES are the
    same, so an unchanged archive is not repacked again."""

    def __init__(self, path):
        self.path = path
        self.sources = {}
        self.dirty = False
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            if isinstance(doc, dict) and doc.get("rules") == RULES and isinstance(doc.get("sources"), dict):
                self.sources = {k: v for k, v in doc["sources"].items()
                                if HEX64.match(str(k)) and isinstance(v, str) and (HEX64.match(v) or is_reason(v))}
        except (OSError, ValueError, RecursionError):
            pass

    def get(self, sha):
        return self.sources.get(sha)

    def put(self, sha, value):
        if self.sources.get(sha) != value:
            self.sources[sha] = value
            self.dirty = True

    def keep_only(self, shas):
        gone = [k for k in self.sources if k not in shas]
        for k in gone:
            del self.sources[k]
        self.dirty = self.dirty or bool(gone)

    def save(self):
        if not self.dirty:
            return
        tmp = "%s.%d.tmp" % (self.path, os.getpid())
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"format": 1, "rules": RULES, "sources": self.sources}, f, sort_keys=True, separators=(",", ":"))
        os.replace(tmp, self.path)
        self.dirty = False


def _stop(cancel):
    if cancel is not None and cancel.is_set():
        raise layouts.Stopped()


def _room(path, need):
    """NotRepacked(WHY_SPACE) when the drive of `path` has less than need + RESERVE bytes free."""
    if shutil.disk_usage(os.path.dirname(os.path.abspath(path))).free < need + RESERVE:
        raise NotRepacked(WHY_SPACE)


# ---- what is repacked ------------------------------------------------------------------------------------------------

def candidate(path, size):
    """'gz' or '7z' when the file at `path` (`size` bytes) is one repacking is for: PARTS_MIN bytes or more, and a gzip
    no index reads (not one repacked already) or a 7z of one folder of several files; '' otherwise."""
    if size < parts.PARTS_MIN:
        return ""
    with open(path, "rb") as f:
        head = f.read(8)
        if head.startswith(layouts.GZIP_MAGIC):
            return "gz" if layouts.gz_layout(f, size, parts.SEGMENTS_MAX)[0] is None else ""
        if head.startswith(layouts.SEVENZIP_MAGIC):
            return "7z" if parts.find_layout(f, size)[2] == layouts.WHY_SOLID else ""
    return ""


def repack(path, out, cancel=None):
    """Repack the archive at `path` into the new file `out`: 'tar.gz', 'gz' or '7z'. NotRepacked when it is left as
    it is, layouts.Stopped when `cancel` is set; `out` is there only when it is repacked."""
    size = os.path.getsize(path)
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        _room(out, size)
        try:
            if head.startswith(layouts.GZIP_MAGIC):
                return _gzip(path, out, cancel)
            if head.startswith(layouts.SEVENZIP_MAGIC):
                return _seven(path, size, out, cancel)
        except MemoryError:
            raise NotRepacked(WHY_MEMORY)
        raise NotRepacked(WHY_GZIP)
    except BaseException:
        try:
            os.remove(out)
        except OSError:
            pass
        raise


# ---- gzip ------------------------------------------------------------------------------------------------------------

def _gunzip(path, cancel):
    """The bytes the gzip at `path` holds, a CHUNK or so at a time, member after member: NotRepacked(WHY_GZIP) for a
    gzip zlib cannot read or with anything but members after its last one, WHY_BIG past REPACK_MAX bytes."""
    total = 0
    with open(path, "rb") as f:
        buf = f.read(CHUNK)
        while buf:
            if not buf.startswith(layouts.GZIP_MAGIC):
                raise NotRepacked(WHY_GZIP)
            dec = zlib.decompressobj(31)
            while not dec.eof:
                _stop(cancel)
                if not buf:
                    buf = f.read(CHUNK)
                    if not buf:
                        raise NotRepacked(WHY_GZIP)     # cut short
                try:
                    data = dec.decompress(buf, CHUNK)
                except zlib.error:
                    raise NotRepacked(WHY_GZIP)
                buf = dec.unconsumed_tail
                total += len(data)
                if total > REPACK_MAX:
                    raise NotRepacked(WHY_BIG)
                if data:
                    yield data
            buf = dec.unused_data
            if len(buf) < 2:
                buf += f.read(CHUNK)


def _first_header(path):
    """(MTIME, OS, FNAME or None, FCOMMENT or None) of the header of the gzip's first member: kept by its repacking."""
    with open(path, "rb") as f:
        head = f.read(10)
        if len(head) < 10 or head[:3] != b"\x1f\x8b\x08" or head[3] & 0xE0:
            raise NotRepacked(WHY_GZIP)
        flags = head[3]
        if flags & 0x04:
            xlen = f.read(2)
            if len(xlen) < 2:
                raise NotRepacked(WHY_GZIP)
            f.seek(struct.unpack("<H", xlen)[0], 1)
        texts = []
        for bit in (0x08, 0x10):
            if not flags & bit:
                texts.append(None)
                continue
            at = f.tell()
            block = f.read(1 << 16)
            end = block.find(b"\0")
            if end < 0:
                raise NotRepacked(WHY_GZIP)
            texts.append(block[:end])
            f.seek(at + end + 1)
    return head[4:8], head[9], texts[0], texts[1]


class _Forward(object):
    """A file of the bytes `chunks` gives, for a reader that only ever goes forward (the tar walk)."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.buf = b""
        self.base = 0               # where buf begins in the file
        self.pos = 0

    def seek(self, pos):
        if pos < self.base:
            raise ValueError("backward")
        self.pos = pos

    def read(self, n):
        if self.pos > self.base:
            drop = min(self.pos - self.base, len(self.buf))
            self.buf, self.base = self.buf[drop:], self.base + drop
        while self.base + len(self.buf) < self.pos + n:
            data = next(self.chunks, None)
            if data is None:
                break
            if self.base + len(self.buf) < self.pos:          # wholly before pos: skipped
                skip = min(len(data), self.pos - self.base - len(self.buf))
                self.base += len(self.buf) + skip
                self.buf = data[skip:]
            else:
                self.buf += data
        out = self.buf[self.pos - self.base:self.pos - self.base + n]
        self.pos += len(out)
        return out


def _split(a, b):
    """[a, b) cut every MEMBER_MAX bytes."""
    return [(s, min(b, s + MEMBER_MAX)) for s in range(a, b, MEMBER_MAX)]


def tar_cuts(ranges, total):
    """The segments of a tar of `total` bytes whose data areas are `ranges`: each data area, and each run of the
    bytes between them (padding, headers, the end blocks and what follows), MEMBER_MAX bytes at most each."""
    cuts = []
    pos = 0
    for a, b in list(ranges) + [(total, total)]:
        cuts.extend(_split(pos, a))
        cuts.extend(_split(a, b))
        pos = b
    return cuts


def merged(cuts, most):
    """`cuts` with neighbours merged, the two whose bytes are fewest first (the lowest place when two are as few),
    never past MEMBER_MAX, until there are at most `most`; NotRepacked(WHY_PIECES) when they cannot be."""
    n = len(cuts)
    if n <= most:
        return list(cuts)
    starts, ends = [a for a, _b in cuts], [b for _a, b in cuts]
    after, before, alive = list(range(1, n + 1)), list(range(-1, n - 1)), [True] * n
    heap = [(ends[i + 1] - starts[i], i) for i in range(n - 1)]
    heapq.heapify(heap)
    count = n
    while count > most and heap:
        size, i = heapq.heappop(heap)
        j = after[i]
        if not alive[i] or j >= n or ends[j] - starts[i] != size:
            continue                                            # a pair since merged into another
        if size > MEMBER_MAX:
            break
        ends[i] = ends[j]
        alive[j] = False
        after[i] = after[j]
        if after[i] < n:
            before[after[i]] = i
        count -= 1
        if before[i] >= 0:
            heapq.heappush(heap, (ends[i] - starts[before[i]], before[i]))
        if after[i] < n:
            heapq.heappush(heap, (ends[after[i]] - starts[i], i))
    if count > most:
        raise NotRepacked(WHY_PIECES)
    return [(starts[i], ends[i]) for i in range(n) if alive[i]]


def first_cut(buf, limit):
    """The first place p, CDC_MIN <= p <= min(limit, len(buf)), where a chunk that begins at buf[0] is cut: where the
    sum of _GEAR over the 64 bytes before p has its low 20 bits zero; None when there is none."""
    limit = min(limit, len(buf))
    start = CDC_MIN
    while start <= limit:
        stop = min(limit, start + CDC_STEP - 1)               # the windows that end at start .. stop
        acc = list(itertools.accumulate(map(_GEAR.__getitem__, buf[start - CDC_WINDOW:stop])))
        sums = [acc[CDC_WINDOW - 1]] + list(map(operator.sub, acc[CDC_WINDOW:], acc))
        try:
            return start + list(map(CDC_MASK.__and__, sums)).index(0)
        except ValueError:
            start = stop + 1
    return None


def cdc_cuts(chunks):
    """The chunks first_cut() cuts the bytes of `chunks` into: [(start, end)]."""
    cuts = []
    buf = bytearray()
    base = 0
    for data in chunks:
        buf += data
        while len(buf) >= CDC_MAX:
            n = first_cut(buf, CDC_MAX) or CDC_MAX
            cuts.append((base, base + n))
            del buf[:n]
            base += n
    while buf:
        n = first_cut(buf, len(buf)) or len(buf)
        cuts.append((base, base + n))
        del buf[:n]
        base += n
    return cuts


def _write_gz(out, chunks, cuts, header, cancel):
    """Write the gzip of the bytes of `chunks` as repacking writes it: one member, the original's MTIME, OS, FNAME
    and FCOMMENT (`header`: _first_header()), XFL 2 (what zlib writes at level 9), FEXTRA with the subfield "DP" of
    its segment table (a version byte, 1; the count, 4 bytes; each segment's deflate length, 4 bytes; little endian),
    written in place once the stream is done; then one deflate stream with a full flush after every segment of
    `cuts` (a dictionary reset and a byte boundary: a segment's bytes depend on its content alone), the last one
    finishing it; then CRC32 and ISIZE. [(where each segment's deflate data begins, its length)]."""
    mtime, os_byte, name, comment = header
    table = 5 + 4 * len(cuts)
    flags = 0x04 | (0x08 if name is not None else 0) | (0x10 if comment is not None else 0)
    head = b"\x1f\x8b\x08" + bytes([flags]) + mtime + bytes([2, os_byte]) + struct.pack("<H", 4 + table) + b"DP"
    with open(out, "wb") as f:
        f.write(head + struct.pack("<H", table) + bytes(table))
        for text in (name, comment):
            if text is not None:
                f.write(text + b"\0")
        z = zlib.compressobj(9, zlib.DEFLATED, -15, 8, zlib.Z_DEFAULT_STRATEGY)
        done = []
        crc = pos = 0
        checked = f.tell()
        index = 0
        at = f.tell()
        for data in chunks:
            view = memoryview(data)
            while len(view):
                if index >= len(cuts):
                    raise NotRepacked(WHY_GZIP)                # more bytes than were cut
                take = min(len(view), cuts[index][1] - pos)
                piece = view[:take]
                view = view[take:]
                crc = zlib.crc32(piece, crc)
                pos += take
                f.write(z.compress(piece))
                if pos == cuts[index][1]:
                    f.write(z.flush(zlib.Z_FINISH if index == len(cuts) - 1 else zlib.Z_FULL_FLUSH))
                    done.append((at, f.tell() - at))
                    at = f.tell()
                    index += 1
                if f.tell() - checked >= 64 << 20:              # the drive, every 64 MiB written
                    checked = f.tell()
                    _stop(cancel)
                    _room(out, 0)
        if index != len(cuts):
            raise NotRepacked(WHY_GZIP)                        # fewer bytes than were cut
        f.write(struct.pack("<II", crc, pos & 0xFFFFFFFF))
        f.seek(len(head) + 2)
        f.write(b"\x01" + struct.pack("<I", len(cuts)) + struct.pack("<%dI" % len(cuts), *(n for _a, n in done)))
    return done


def _check_gz(out, cuts, done, total, digest, cancel):
    """NotRepacked(WHY_CHECK) unless the repacked gzip at `out` is one member a single-member reader reads to its
    end, to the original's length and SHA-256, layouts.py reads its table, and each segment inflates on its own to its
    length."""
    with open(out, "rb") as f:
        if parts.find_layout(f, os.path.getsize(out))[1] != [(a, a + n) for a, n in done]:
            raise NotRepacked(WHY_CHECK)
        f.seek(0)
        dec = zlib.decompressobj(31)
        h = hashlib.sha256()
        got = 0
        try:
            for data in iter(lambda: f.read(CHUNK), b""):
                _stop(cancel)
                while data:
                    if dec.eof:
                        raise NotRepacked(WHY_CHECK)            # anything after the one member
                    out_bytes = dec.decompress(data, CHUNK)
                    h.update(out_bytes)
                    got += len(out_bytes)
                    data = dec.unconsumed_tail
            if not dec.eof or dec.unused_data or got != total or h.digest() != digest:
                raise NotRepacked(WHY_CHECK)
            for (a, n), (start, end) in zip(done, cuts):
                f.seek(a)
                raw = zlib.decompressobj(-15)
                left, made = n, 0
                while left:
                    _stop(cancel)
                    data = f.read(min(CHUNK, left))
                    left -= len(data)
                    while data:
                        made += len(raw.decompress(data, CHUNK))
                        data = raw.unconsumed_tail
                if made != end - start:
                    raise NotRepacked(WHY_CHECK)
        except zlib.error:
            raise NotRepacked(WHY_CHECK)


def _gzip(path, out, cancel):
    h = hashlib.sha256()
    total = 0
    for data in _gunzip(path, cancel):
        h.update(data)
        total += len(data)
    if not total:
        raise NotRepacked(WHY_GZIP)
    try:
        ranges, why = layouts.tar_layout(_Forward(_gunzip(path, cancel)), total, parts.SEGMENTS_MAX, cancel)
    except ValueError:
        ranges, why = None, layouts.WHY_NOT_TAR
    tar = bool(ranges) and not why
    cuts = merged(tar_cuts(ranges, total) if tar else cdc_cuts(_gunzip(path, cancel)), SEGMENTS_GZ)
    done = _write_gz(out, _gunzip(path, cancel), cuts, _first_header(path), cancel)
    _check_gz(out, cuts, done, total, h.digest(), cancel)
    return "tar.gz" if tar else "gz"


# ---- 7z --------------------------------------------------------------------------------------------------------------

class _Coder(object):
    def __init__(self, method, props, ins, outs):
        self.method, self.props, self.ins, self.outs = method, props, ins, outs
        self.name = _NAMES.get(method, "method " + method.hex())


def _folder(b):
    """One folder of a header, whole: ([coders], [(in index, out index)] bind pairs, [packed in indexes])."""
    coders = []
    for _ in range(b.count(64, WHY_7Z)):
        flags = b.byte()
        if flags & 0xC0:
            raise NotRepacked(WHY_7Z)
        method = bytes(b.take(flags & 0x0F))
        ins = outs = 1
        if flags & 0x10:
            ins, outs = b.number(), b.number()
        props = bytes(b.take(b.number())) if flags & 0x20 else b""
        coders.append(_Coder(method, props, ins, outs))
    ins, outs = sum(c.ins for c in coders), sum(c.outs for c in coders)
    if not coders or ins > layouts.FOLDER_STREAMS_MAX or outs > layouts.FOLDER_STREAMS_MAX or ins < outs:
        raise NotRepacked(WHY_7Z)
    binds = [(b.number(), b.number()) for _ in range(outs - 1)]
    packed = [b.number() for _ in range(ins - outs + 1)] if ins - outs + 1 > 1 else \
        [i for i in range(ins) if i not in {p for p, _o in binds}]
    return coders, binds, packed


def _chain(coders, binds, packed):
    """The coders of a folder from the one that gives its output to the one that reads its packed stream, when they
    are a chain of coders of one stream in and one out over one packed stream that Python's modules decode:
    NotRepacked(WHY_CODER) naming the first coder that is not."""
    for c in coders:
        if c.method not in _FILTERS and c.method not in (b"\x00", b"\x03\x01\x01", b"\x21", b"\x04\x01\x08",
                                                         b"\x04\x02\x02", b"\x03") or \
                (c.method in _FILTERS and lzma is not None and not hasattr(lzma, "FILTER_" + _FILTERS[c.method])):
            raise NotRepacked(WHY_CODER + ":" + c.name)
    for c in coders:                            # one this Python could decode with a module it was built without
        if c.method == b"\x04\x02\x02" and bz2 is None:
            raise NotRepacked(WHY_MODULE + ":bz2")
        if c.method not in (b"\x00", b"\x04\x01\x08", b"\x04\x02\x02") and lzma is None:
            raise NotRepacked(WHY_MODULE + ":lzma")
    if any(c.ins != 1 or c.outs != 1 for c in coders) or len(packed) != 1:
        raise NotRepacked(WHY_CODER + ":" + coders[0].name)
    main = [o for o in range(len(coders)) if o not in {o for _i, o in binds}]
    if len(main) != 1:
        raise NotRepacked(WHY_7Z)
    order = [main[0]]
    while len(order) <= len(coders):
        feeding = [o for i, o in binds if i == order[-1]]
        if not feeding:
            break
        order.append(feeding[0])
    if sorted(order) != list(range(len(coders))) or order[-1] != packed[0]:
        raise NotRepacked(WHY_7Z)
    chain = [coders[i] for i in order]
    if any(c.method in (b"\x00", b"\x03\x01\x01", b"\x21", b"\x04\x01\x08", b"\x04\x02\x02") for c in chain[:-1]) or \
            chain[-1].method in _FILTERS or chain[-1].method == b"\x03":
        raise NotRepacked(WHY_CODER + ":" + chain[-1].name)    # filters before one coder that unpacks
    return chain


def _spec(c, size):
    """lzma's filter for the coder c (its dictionary no bigger than `size`, what it decodes)."""
    if c.method == b"\x21":
        return layouts._filters(c.method, c.props, size)[0]
    if c.method == b"\x03\x01\x01":
        return layouts._filters(c.method, c.props, size)[0]
    if c.method == b"\x03":
        if len(c.props) != 1:
            raise NotRepacked(WHY_7Z)
        return {"id": lzma.FILTER_DELTA, "dist": c.props[0] + 1}
    spec = {"id": getattr(lzma, "FILTER_" + _FILTERS[c.method])}
    if len(c.props) == 4:
        spec["start_offset"] = struct.unpack("<I", c.props)[0]
    elif c.props:
        raise NotRepacked(WHY_7Z)
    return spec


def _drain(dec, source, size, cancel):
    """What the lzma decompressor `dec` gives of the bytes of `source`, exactly `size` bytes, a CHUNK at a time:
    NotRepacked(WHY_DAMAGED) for data that ends before it."""
    got = idle = 0
    while got < size:
        _stop(cancel)
        if dec.eof:
            raise NotRepacked(WHY_DAMAGED)
        data = b""
        if dec.needs_input:
            data = next(source, b"")
            if not data:
                raise NotRepacked(WHY_DAMAGED)
        out = dec.decompress(data, max_length=min(CHUNK, size - got))
        idle = 0 if out or data else idle + 1
        if idle > 1:
            raise NotRepacked(WHY_DAMAGED)
        got += len(out)
        if out:
            yield out


def _unpacked(c, source, cancel):
    """The bytes the coder c (Copy, Deflate or BZip2) unpacks from the bytes of `source`, a CHUNK at most at a time."""
    if c.method == b"\x00":
        yield from source
        return
    deflate = c.method == b"\x04\x01\x08"
    dec = zlib.decompressobj(-15) if deflate else bz2.BZ2Decompressor()
    for data in source:
        while True:
            _stop(cancel)
            if deflate:
                out = dec.decompress(data, CHUNK)
                data = dec.unconsumed_tail
            elif dec.eof:
                break
            else:
                try:
                    out = dec.decompress(data, CHUNK)
                except (OSError, EOFError):             # bz2's words for data that is not BZip2
                    raise NotRepacked(WHY_DAMAGED)
                data = b""
            if out:
                yield out
            if deflate and not data or not deflate and (dec.eof or dec.needs_input):
                break


def _framed(source):
    """The bytes of `source` as LZMA2 chunks left uncompressed, so lzma's filters can be undone over them."""
    first = True
    for data in source:
        for i in range(0, len(data), 1 << 16):
            piece = data[i:i + (1 << 16)]
            yield bytes([1 if first else 2]) + struct.pack(">H", len(piece) - 1) + bytes(piece)
            first = False
    yield b"\0"


def _folder_bytes(f, at, packed, chain, sizes, cancel):
    """The output of a folder whose chain of coders is `chain` (from the one that gives the output), its packed stream
    of `packed` bytes at `at`, `sizes` what each coder of the chain gives: a CHUNK at a time."""
    def source():
        f.seek(at)
        left = packed
        while left:
            data = f.read(min(CHUNK, left))
            if not data:
                raise NotRepacked(WHY_7Z)
            left -= len(data)
            yield data

    if chain[-1].method == b"\x21" or chain[-1].method == b"\x03\x01\x01" and len(chain) == 1:
        dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[_spec(c, s) for c, s in zip(chain, sizes)])
        return _drain(dec, source(), sizes[0], cancel)
    if chain[-1].method == b"\x03\x01\x01":     # LZMA under filters: LZMA alone (its stream has no end that would
        raw = _drain(lzma.LZMADecompressor(     # make the filters give their last bytes), the filters undone after
            format=lzma.FORMAT_RAW, filters=[_spec(chain[-1], sizes[-1])]), source(), sizes[-1], cancel)
    else:
        raw = _unpacked(chain[-1], source(), cancel)
    if len(chain) == 1:
        return raw
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[_spec(c, s) for c, s in zip(chain[:-1], sizes)]
                                + [{"id": lzma.FILTER_LZMA2, "dict_size": 1 << 20}])
    return _drain(dec, _framed(raw), sizes[0], cancel)


def _header(f, size, cancel):
    """(the 7z's header, plain and whole; whether it was encoded; the size of its packed area before it)."""
    sig = layouts._read_at(f, 0, 32)
    start_crc, offset, n, crc = struct.unpack("<IQQI", sig[8:32])
    if sig[6] != 0 or zlib.crc32(sig[12:32]) != start_crc or not 0 < n <= layouts.HEADER_MAX or 32 + offset + n > size:
        raise NotRepacked(WHY_7Z)
    head, check = layouts._header_start(f, 32 + offset, n, cancel, keep=layouts.HEADER_MAX)
    if check != crc:
        raise NotRepacked(WHY_7Z)
    b = layouts._Bytes(head, len(head) + layouts.READS_BASE, cancel)
    if b.number() != 0x17:
        return head, False, offset
    if b.number() != 0x06:
        raise NotRepacked(WHY_7Z)
    pos, sizes = layouts._pack_info(b, 1, WHY_7Z, offset)
    if b.number() != 0x07:
        raise NotRepacked(WHY_7Z)
    folders = layouts._unpack_info(b, 1, WHY_7Z, True, len(sizes))
    if len(sizes) != 1 or len(folders) != 1 or 32 + pos + sizes[0] > 32 + offset:
        raise NotRepacked(WHY_7Z)
    return layouts._decoded(f, 32 + pos, sizes[0], folders[0], cancel, keep=layouts.HEADER_MAX), True, offset


class _Solid(object):
    """What repacking reads of a 7z of one folder: its packed stream (at, size), the folder's chain of coders and what
    each gives, its files' sizes and CRCs (None where the header has none), and the header's raw archive
    properties and file list."""


def _parse(head, offset, cancel):
    """The _Solid of a 7z's plain header `head` (its packed area `offset` bytes before it)."""
    s = _Solid()
    b = layouts._Bytes(head, len(head) + layouts.READS_BASE, cancel)
    if b.number() != 0x01:
        raise NotRepacked(WHY_7Z)
    t = b.number()
    s.properties = b""
    if t == 0x02:
        start = b.at - 1
        for _t in layouts._attributes(b):
            layouts._skip(b)
        s.properties = bytes(head[start:b.at])
        t = b.number()
    if t != 0x04 or b.number() != 0x06:
        raise NotRepacked(WHY_7Z)
    pos, sizes = layouts._pack_info(b, layouts.FOLDER_STREAMS_MAX, WHY_7Z, offset)
    if b.number() != 0x07 or b.number() != 0x0B or b.count(1, WHY_7Z, len(sizes)) != 1 or b.byte() != 0:
        raise NotRepacked(WHY_7Z)
    coders, binds, packed = _folder(b)
    _chain(coders, binds, packed)                   # a coder it does not decode is named before anything else
    if len(sizes) != 1:
        raise NotRepacked(WHY_7Z)
    s.at, s.packed = 32 + pos, sizes[0]
    if b.number() != 0x0C:
        raise NotRepacked(WHY_7Z)
    outs = [b.number() for _ in range(sum(c.outs for c in coders))]
    folder_crc = None
    for t in layouts._attributes(b):
        if t == 0x0A:
            folder_crc = layouts._digests(b, 1)[0]
        else:
            layouts._skip(b)
    s.chain = _chain(coders, binds, packed)
    order = [coders.index(c) for c in s.chain]
    s.sizes = [outs[i] for i in order]          # what each coder of the chain gives (one out stream each)
    total = s.sizes[0]
    if total > REPACK_MAX:
        raise NotRepacked(WHY_BIG)
    t = b.number()
    files, lengths, crcs = 1, None, None
    if t == 0x08:
        for k, t in enumerate(layouts._attributes(b)):
            if t == 0x0D:
                if k:                               # how many files: once, before their sizes and CRCs
                    raise NotRepacked(WHY_7Z)
                files = b.number()
                if files > parts.SEGMENTS_MAX or files > total + 1:
                    raise NotRepacked(WHY_PIECES)
            elif t == 0x09:
                lengths = [b.number() for _ in range(files - 1)]
            elif t == 0x0A:
                crcs = layouts._digests(b, files) if files != 1 or folder_crc is None else crcs
            else:
                layouts._skip(b)
        t = b.number()
    if files < 2:
        raise NotRepacked(WHY_7Z)
    if lengths is None or len(lengths) != files - 1 or sum(lengths) > total or \
            crcs is not None and len(crcs) != files:
        raise NotRepacked(WHY_7Z)
    s.lengths = lengths + [total - sum(lengths)]
    s.crcs = crcs if crcs is not None else [None] * files
    if t != 0x00:
        raise NotRepacked(WHY_7Z)
    s.files = b""
    t = b.number()
    if t == 0x05:
        start = b.at - 1
        b.number()                                  # how many files
        for _ in range(256):
            kind = b.number()
            if kind == 0x00:
                break
            layouts._skip(b)
        else:
            raise NotRepacked(WHY_7Z)
        s.files = bytes(head[start:b.at])
        t = b.number()
    if t != 0x00:
        raise NotRepacked(WHY_7Z)
    return s


def coder_problem(path, size):
    """'' when repacking decodes the coders of the solid 7z at `path`; else the NotRepacked code saying why not."""
    try:
        with open(path, "rb") as f:
            head, _encoded, offset = _header(f, size, None)
        _parse(head, offset, None)
    except NotRepacked as e:
        return e.code
    except (layouts._Bad, layouts._Reason, ValueError, struct.error, EOFError, MemoryError) + layouts._LZMA_ERRORS:
        return WHY_7Z
    return ""


def _number(n):
    for k in range(8):
        if n < 1 << (7 * k + 7):
            return bytes([((0xFF00 >> k) & 0xFF) | (n >> (8 * k))]) + (n & ((1 << (8 * k)) - 1)).to_bytes(k, "little")
    return b"\xff" + n.to_bytes(8, "little")


def _dict_prop(size):
    """The LZMA2 dictionary property of the dictionary repacking packs a file of `size` bytes with, and that size."""
    want = max(4096, min(LZMA2_DICT_MAX, size))
    for p in range(40):
        if (2 | (p & 1)) << (p // 2 + 11) >= want:
            return p, want
    return 40, want


def _seven(path, size, out, cancel):
    """Repack the solid 7z at `path`: anything wrong with its header is WHY_7Z, with its data (an error decoding it,
    a CRC of its own that does not match) WHY_DAMAGED; I/O errors are left as they are (asked again next time)."""
    if lzma is None:
        raise NotRepacked(WHY_MODULE + ":lzma")    # what it is written with
    with open(path, "rb") as f:
        try:
            head, encoded, offset = _header(f, size, cancel)
            s = _parse(head, offset, cancel)
        except (layouts._Bad, layouts._Reason, ValueError, struct.error, EOFError, IndexError,
                OverflowError) + layouts._LZMA_ERRORS:
            raise NotRepacked(WHY_7Z)
        _room(out, size + sum(s.lengths) // 1000)
        try:
            written = _write_seven(f, s, encoded, out, cancel)
        except (layouts._Bad, layouts._Reason):       # a coder's properties lzma takes no filter for (_spec)
            raise NotRepacked(WHY_7Z)
        except (zlib.error, EOFError, IndexError, OverflowError, ValueError, struct.error) + layouts._LZMA_ERRORS:
            raise NotRepacked(WHY_DAMAGED)
    _check_seven(out, written, cancel)
    return "7z"


def _write_seven(f, s, encoded, out, cancel):
    """Write the 7z of s's files, a folder for each: [(where its packed stream is, its size, the file's size, its CRC,
    its LZMA2 dictionary property)]."""
    streams = []
    with open(out, "wb") as o:
        o.write(bytes(32))
        checked = 0
        source = _folder_bytes(f, s.at, s.packed, s.chain, s.sizes, cancel)
        data = memoryview(b"")
        for i, length in enumerate(s.lengths):
            prop, dict_size = _dict_prop(length)
            z = lzma.LZMACompressor(format=lzma.FORMAT_RAW, filters=[
                {"id": lzma.FILTER_LZMA2, "preset": LZMA2_PRESET, "dict_size": dict_size}])
            at, left, crc = o.tell(), length, 0
            while left:
                if not len(data):
                    data = memoryview(next(source, b""))
                    if not len(data):
                        raise NotRepacked(WHY_DAMAGED)
                piece = data[:left]
                data = data[len(piece):]
                left -= len(piece)
                crc = zlib.crc32(piece, crc)
                o.write(z.compress(piece))
                if o.tell() - checked >= 64 << 20:          # the drive, every 64 MiB written
                    checked = o.tell()
                    _room(out, 0)
            o.write(z.flush())
            if s.crcs[i] is not None and s.crcs[i] != crc:
                raise NotRepacked(WHY_DAMAGED)              # the original's own CRC: it is damaged
            streams.append((at, o.tell() - at, length, crc, prop))
            _room(out, 0)
        if len(data) or next(source, b""):
            raise NotRepacked(WHY_7Z)
        n = _number
        h = b"\x01" + s.properties + b"\x04\x06" + n(0) + n(len(streams)) + b"\x09"
        h += b"".join(n(p) for _a, p, _l, _c, _d in streams) + b"\x00\x07\x0b" + n(len(streams)) + b"\x00"
        h += b"".join(b"\x01\x21\x21\x01" + bytes([d]) for _a, _p, _l, _c, d in streams)
        h += b"\x0c" + b"".join(n(length) for _a, _p, length, _c, _d in streams) + b"\x00"
        # the CRC of each file, as 7-Zip writes them: in kSubStreamsInfo
        h += b"\x08\x0a\x01" + b"".join(struct.pack("<I", c) for _a, _p, _l, c, _d in streams) + b"\x00\x00"
        h += s.files + b"\x00"
        if encoded:                         # as 7-Zip encodes a header: LZMA, its packed stream after the files'
            dict_size = max(4096, min(1 << 20, len(h)))
            packed = lzma.compress(h, format=lzma.FORMAT_RAW, filters=[
                {"id": lzma.FILTER_LZMA1, "dict_size": dict_size, "lc": 3, "lp": 0, "pb": 2}])
            at = o.tell() - 32
            o.write(packed)
            h = (b"\x17\x06" + n(at) + n(1) + b"\x09" + n(len(packed)) + b"\x00"
                 + b"\x07\x0b\x01\x00\x01\x23\x03\x01\x01" + n(5) + bytes([(2 * 5 + 0) * 9 + 3])
                 + struct.pack("<I", dict_size) + b"\x0c" + n(len(h)) + b"\x0a\x01"
                 + struct.pack("<I", zlib.crc32(h)) + b"\x00\x00")
        end = o.tell()
        o.write(h)
        start = struct.pack("<QQI", end - 32, len(h), zlib.crc32(h))
        o.seek(0)
        o.write(layouts.SEVENZIP_MAGIC + b"\x00\x04" + struct.pack("<I", zlib.crc32(start)) + start)
    return streams


def _check_seven(out, streams, cancel):
    """NotRepacked(WHY_CHECK) unless layouts.py reads the repacked 7z at `out` as one of a folder for each file, and
    each of its packed streams decodes to the file's size and CRC."""
    size = os.path.getsize(out)
    with open(out, "rb") as f:
        kind, ranges, why = parts.find_layout(f, size, cancel)
        if kind != "7z" or why or ranges != [(a, a + p) for a, p, _l, _c, _d in streams if p]:
            raise NotRepacked(WHY_CHECK)
        for at, packed, length, crc, prop in streams:
            def source(at=at, left=packed):
                f.seek(at)
                while left:
                    data = f.read(min(CHUNK, left))
                    if not data:
                        return
                    left -= len(data)
                    yield data
            dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[
                {"id": lzma.FILTER_LZMA2, "dict_size": _dict_prop(length)[1]}])
            check = 0
            try:
                for data in _drain(dec, source(), length, cancel):
                    check = zlib.crc32(data, check)
            except (NotRepacked,) + layouts._LZMA_ERRORS:
                raise NotRepacked(WHY_CHECK)
            if check != crc:
                raise NotRepacked(WHY_CHECK)
