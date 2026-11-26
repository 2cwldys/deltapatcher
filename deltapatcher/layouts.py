"""The layouts of the archives parts.py indexes besides zip: tar, 7z and RAR, read from their own structure, never
from a name. Nothing here decompresses an archive's files or writes one: a reader only finds where each archive keeps
the packed data of the files in it (its data areas), so an index can describe the archive as those pieces and the
bytes between them (parts.layout()).

Each reader takes an open binary file, its size, the most data areas it may give and a cancel event, and gives
([(start, end)] the data areas, sorted, why) or (None, why): why is '' for an archive that gets an index, else a
reason code. A 7z of one folder (one solid block) has data areas and a reason (WHY_SOLID, or WHY_ONE_FILE when it holds
one file): a piece of it may still be found elsewhere, but it gets no index of its own. The data areas:
  tar  each member's data. This module walks the tar itself, one 512-byte header at a time: its checksum (unsigned,
       or the signed form tarfile also takes), its size (octal or GNU base-256), its type. Regular files ('0', NUL,
       '7') have data; links, devices, directories and fifos ('1' to '6') none. Extension blocks are read only as far
       as needed, each at most EXT_MAX bytes and a pax one at most PAX_RECORDS_MAX records (else WHY_LONG_HEADER),
       the pax ones of a tar at most PAX_RECORDS_TOTAL records in all (else WHY_PAX_RECORDS): a pax 'x' header's
       "size" record sets the next member's size (any GNU.sparse key: WHY_SPARSE), a pax global 'g' header is
       allowed and never applied (a "size" in it: WHY_NOT_TAR), a GNU long name or link ('L', 'K') is skipped, never
       decoded. GNU sparse 'S' is WHY_SPARSE; a volume 'V', a continuation 'M' or any other type, WHY_NOT_TAR. The
       first zero block ends it; what follows is literal. A file whose first block is zeros or no valid header, or
       that is shorter than a block, is no tar at all; so is one whose first header is of a type this reader does
       not know, unless that header has the ustar or GNU magic: then it is WHY_NOT_TAR (a GNU incremental tar's
       'D', for one).
  7z   every packed stream of the archive's files, at 32 + PackPos and the sizes before it. The signature header, any
       gap, the header (plain, or LZMA or LZMA2 encoded: decoded here with lzma in chunks, its CRC checked, at most
       HEADER_MAX bytes and 1000 times its packed size) and an encoded header's own packed stream are the rest. A
       header encoded with AES is WHY_ENCRYPTED; one encoded by a Python without lzma, WHY_NO_LZMA; any other coder,
       or anything unexpected, WHY_NOT_7Z. The walk through a header is tied to its file: at most READS_BASE
       numbers and one more for every READS_BYTES bytes of the file, ATTRIBUTES_MAX attributes in a property list,
       no more packed streams than bytes before the header, no more folders than packed streams, and at most
       FOLDER_STREAMS_MAX streams into a folder and as many out of it (else WHY_NOT_7Z).
  RAR  the data area of each file and service block, RAR5 (rarlab.com's "RAR 5.0 archive format": CRC32 and vints)
       and RAR4 (its "RAR 4.x archive format": HEAD_SIZE, ADD_SIZE, PACK_SIZE and HIGH_PACK_SIZE). Every header and
       the end of archive are the rest. Encrypted headers (a RAR5 archive encryption header, RAR4's MHD_PASSWORD)
       are WHY_ENCRYPTED; anything unexpected, WHY_NOT_RAR. Each volume of a set is a file of its own, its data areas
       its own (a file continued from the volume before is a piece of each).

Every count, size and offset is checked against the file's size (or the header's) before anything is allocated or
read; every step reads one header or skips a known length; a reader stops past `most` headers or data areas
(WHY_PIECES) and looks at its cancel event every CANCEL_EVERY of them, or of the numbers it reads (Stopped);
anything unexpected (a CRC that does not match, an overlap, a truncated header) is the kind's "not this kind"
reason: never an exception that stops a publish or an update. Python's standard library only, and lzma only when it
is there.
"""

import bisect
import struct
import zlib

try:
    import lzma
except ImportError:                         # a Python built without it: an encoded 7z header gets WHY_NO_LZMA
    lzma = None

SEVENZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"
GZIP_MAGIC = b"\x1f\x8b"
RAR5_MAGIC = b"Rar!\x1a\x07\x01\x00"
RAR4_MAGIC = b"Rar!\x1a\x07\x00"
HEADER_KEEP = 32 << 20      # of a 7z header, the first bytes kept to read its packed streams (all of it is CRC-checked)
HEADER_MAX = 256 << 20      # a 7z header (decoded) is at most this long
HEADER_RATIO = 1000         # and decodes to at most this many times its packed size
RAR5_HEADER_MAX = 2 << 20   # a RAR5 block header is at most this long: the format's own limit
EXT_MAX = 64 << 10          # a tar extension block (a pax header, a GNU long name) is at most this long
PAX_RECORDS_MAX = 256       # and a pax header holds at most this many records
PAX_RECORDS_TOTAL = 4000000  # and a tar at most this many pax records in all
ATTRIBUTES_MAX = 16         # the most attributes a 7z property list has (kPackInfo's, kUnPackInfo's, ...)
FOLDER_STREAMS_MAX = 64     # a 7z folder takes at most this many streams in and gives this many out (as 7-Zip)
READS_BASE = 1 << 16        # a 7z header is read with at most this many numbers (and bytes read on their own),
READS_BYTES = 8             # and one more for every this many bytes of its file
CANCEL_EVERY = 4096         # a reader looks at its cancel event every this many headers, blocks or numbers
CHUNK = 1 << 20

# why an archive of these kinds gets no index: reason codes (parts.reason() says them in words)
WHY_PIECES = "pieces"       # more data areas (or headers) than an index can hold
WHY_SOLID = "solid"         # a 7z of one folder: one solid block
WHY_ONE_FILE = "one-file"   # a 7z of one file
WHY_ENCRYPTED = "encrypted"
WHY_SPARSE = "sparse"
WHY_LONG_HEADER = "long-header"   # a tar extension block longer than EXT_MAX, a pax header of more records
WHY_PAX_RECORDS = "pax-records"   # a tar of more than PAX_RECORDS_TOTAL pax records
WHY_NO_LZMA = "no-lzma"     # a 7z header encoded, and no lzma in this Python to decode it
WHY_NOT_TAR = "not-tar"
WHY_NOT_7Z = "not-7z"
WHY_NOT_RAR = "not-rar"


class Stopped(Exception):
    """A reader was stopped: its cancel event is set."""


class _Bad(Exception):
    """Not the archive its first bytes say: something in it is not as its format has it."""


class _Reason(Exception):
    """An archive this reader knows that gets no index: why (a reason code)."""

    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = code


def _read_at(f, at, n):
    """The n bytes at `at`; _Bad when the file has fewer."""
    f.seek(at)
    data = f.read(n)
    if len(data) != n:
        raise _Bad()
    return data


def _stop(cancel, n):
    """Stopped when `cancel` is set, looked at every CANCEL_EVERY steps (n counts them)."""
    if cancel is not None and not n % CANCEL_EVERY and cancel.is_set():
        raise Stopped()


# ---- tar ------------------------------------------------------------------------------------------------------------

_TAR_DATA = (b"0", b"\0", b"7")             # regular files (and contiguous ones): their data follows the header
_TAR_NONE = (b"1", b"2", b"3", b"4", b"5", b"6")   # links, devices, directories, fifos: a header, no data
_TAR_EXTENSIONS = (b"x", b"g", b"L", b"K")
_TAR_REFUSED = (b"S", b"V", b"M")           # GNU sparse, volume label, continued from the volume before
_HIGH = bytes(range(128, 256))


def _tar_number(field):
    """A tar header's number: octal digits (up to a NUL, spaces around them), or GNU base-256 (its first byte 0x80);
    None for anything else (base-256 negative numbers too)."""
    if field[:1] == b"\x80":
        n = 0
        for c in field[1:]:
            n = n << 8 | c
        return n
    text = field.split(b"\0", 1)[0].strip()
    if not text:
        return 0
    if text.strip(b"01234567"):
        return None
    return int(text, 8)


def _tar_header(block):
    """(type flag, size) of a tar header block whose checksum is right (the unsigned sum of its bytes, the checksum
    field counted as spaces, or the signed sum some old writers made); None for any other block."""
    stored = _tar_number(block[148:156])
    if stored is None:
        return None
    unsigned = sum(block) - sum(block[148:156]) + 8 * 32
    high = 512 - len(block.translate(None, _HIGH)) - (8 - len(block[148:156].translate(None, _HIGH)))
    if stored != unsigned and stored != unsigned - 256 * high:
        return None
    size = _tar_number(block[124:136])
    return None if size is None else (block[156:157], size)


def _pax_size(data, is_global, total):
    """(the size a pax extended header's records ("<length> <key>=<value>\\n") give the member after it, or None,
    and `total`, the pax records of the tar before them, with theirs). WHY_SPARSE for a GNU.sparse key;
    WHY_LONG_HEADER for more than PAX_RECORDS_MAX records, WHY_PAX_RECORDS for more than PAX_RECORDS_TOTAL in the
    tar (each counted before it is read: none past them is); WHY_NOT_TAR for records not as POSIX writes them, or
    for a size in a global header (never applied here)."""
    size = None
    at = records = 0
    while at < len(data) and data[at]:
        records += 1
        if records > PAX_RECORDS_MAX:
            raise _Reason(WHY_LONG_HEADER)
        total += 1
        if total > PAX_RECORDS_TOTAL:
            raise _Reason(WHY_PAX_RECORDS)
        space = data.find(b" ", at, at + 21)
        if space <= at or not data[at:space].isdigit():
            raise _Reason(WHY_NOT_TAR)
        end = at + int(data[at:space])
        if end <= space + 1 or end > len(data) or data[end - 1:end] != b"\n":
            raise _Reason(WHY_NOT_TAR)
        equals = data.find(b"=", space + 1, end)
        if equals < 0:
            raise _Reason(WHY_NOT_TAR)
        key = data[space + 1:equals]
        if key.startswith(b"GNU.sparse"):
            raise _Reason(WHY_SPARSE)
        if key == b"size":
            value = data[equals + 1:end - 1]
            if is_global or not value.isdigit() or len(value) > 20:
                raise _Reason(WHY_NOT_TAR)
            size = int(value)
        at = end
    return size, total


def tar_layout(f, size, most, cancel=None):
    """The data areas of the tar in f; (None, '') when f is no tar at all: shorter than a block, its first block zeros
    or no valid header, or a header of a type this reader does not know without the ustar or GNU magic (with it:
    WHY_NOT_TAR)."""
    try:
        return _tar(f, size, most, cancel), ""
    except _Reason as r:
        return None, r.code
    except _Bad:
        return None, WHY_NOT_TAR


def _tar(f, size, most, cancel):
    if size < 512:
        raise _Reason("")               # no first block: no tar at all
    ranges = []
    pos = 0
    headers = 0
    next_size = None                        # what a pax 'x' header says the next member's size is
    records = 0                             # the pax records read so far
    while pos + 512 <= size:
        headers += 1
        _stop(cancel, headers)
        block = _read_at(f, pos, 512)
        if not block.strip(b"\0"):          # the first zero block: the end; what follows is literal
            if not pos:
                raise _Reason("")
            break
        if headers > most:
            raise _Reason(WHY_PIECES)
        head = _tar_header(block)
        known = head is not None and head[0] in _TAR_DATA + _TAR_NONE + _TAR_EXTENSIONS + _TAR_REFUSED
        if not known:
            if pos:
                raise _Bad()
            if head is not None and block[257:263] in (b"ustar\0", b"ustar "):
                raise _Reason(WHY_NOT_TAR)  # a tar header (ustar or GNU) of a type this reader does not know
            raise _Reason("")               # no tar at all
        kind, length = head
        body = pos + 512
        if kind in _TAR_EXTENSIONS:
            if length > EXT_MAX:
                raise _Reason(WHY_LONG_HEADER)
            if length > size - body:
                raise _Bad()
            if kind in (b"x", b"g"):
                found, records = _pax_size(_read_at(f, body, length), kind == b"g", records)
                if kind == b"x":
                    next_size = found
            pos = body + -(-length // 512) * 512
            continue
        if next_size is not None:
            length, next_size = next_size, None
        if kind in _TAR_DATA:
            if length > size - body:
                raise _Bad()
            if length:
                ranges.append((body, body + length))
                if len(ranges) > most:
                    raise _Reason(WHY_PIECES)
            pos = body + -(-length // 512) * 512
        elif kind in _TAR_NONE:
            pos = body
        elif kind == b"S":
            raise _Reason(WHY_SPARSE)
        else:
            raise _Bad()                    # a volume label, a continuation from the volume before
    return ranges


# ---- gz: a gzip DeltaPatcher repacked (repack.py) --------------------------------------------------------------------

GZ_TAIL = 8                 # after the deflate stream: CRC32 and ISIZE
SEGMENTS_GZ = 16000         # a repacked gzip's table has at most this many segments
_GZ_FLAGS = 0x1F            # FTEXT, FHCRC, FEXTRA, FNAME, FCOMMENT: the flags a gzip header may have


def gz_layout(f, size, most, cancel=None):
    """The data areas of a gzip repack.py wrote: one member whose header has FEXTRA with exactly one subfield, "DP":
    a version byte (1), the count of segments (4 bytes) and the deflate length of each (4 bytes; little endian),
    each at least 1, at most SEGMENTS_GZ of them, together the whole deflate stream (the file less its header and the
    8 bytes of CRC32 and ISIZE). Each segment is a data area; the header and the trailer are the rest. Nothing is
    inflated. (None, '') for any other gzip (a plain one); WHY_PIECES past `most` segments."""
    try:
        return _gz(f, size, most, cancel), ""
    except _Reason as r:
        return None, r.code
    except _Bad:
        return None, ""


def _gz(f, size, most, cancel):
    head = _read_at(f, 0, 12)
    flags = head[3]
    if head[:3] != b"\x1f\x8b\x08" or not flags & 0x04 or flags & ~_GZ_FLAGS:
        raise _Bad()
    xlen = struct.unpack("<H", head[10:12])[0]
    extra = _read_at(f, 12, xlen)
    if xlen < 9 or extra[:2] != b"DP" or struct.unpack("<H", extra[2:4])[0] != xlen - 4 or extra[4] != 1:
        raise _Bad()                            # one subfield, "DP", filling the extra field; version 1
    count = struct.unpack("<I", extra[5:9])[0]
    if not 0 < count <= SEGMENTS_GZ or xlen != 9 + 4 * count:
        raise _Bad()
    if count > most:
        raise _Reason(WHY_PIECES)
    lengths = struct.unpack("<%dI" % count, extra[9:])
    pos = 12 + xlen
    for bit in (0x08, 0x10):                    # FNAME, FCOMMENT: up to a NUL, 64 KiB at most each
        if flags & bit:
            text = _read_at(f, pos, min(1 << 16, size - pos))
            end = text.find(b"\0")
            if end < 0:
                raise _Bad()
            pos += end + 1
    if flags & 0x02:
        pos += 2                                # FHCRC
    if min(lengths) < 1 or sum(lengths) != size - pos - GZ_TAIL:
        raise _Bad()
    ranges = []
    for i, n in enumerate(lengths):
        _stop(cancel, i + 1)
        ranges.append((pos, pos + n))
        pos += n
    return ranges


# ---- 7z -------------------------------------------------------------------------------------------------------------

_END, _HEADER, _ARCHIVE_PROPERTIES, _ADDITIONAL_STREAMS, _MAIN_STREAMS = 0x00, 0x01, 0x02, 0x03, 0x04
_PACK_INFO, _UNPACK_INFO, _SUBSTREAMS, _SIZE, _CRC, _FOLDER, _UNPACK_SIZE = 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C
_NUM_UNPACK_STREAM, _ENCODED_HEADER = 0x0D, 0x17
_LZMA, _LZMA2, _AES = b"\x03\x01\x01", b"\x21", b"\x06\xf1\x07\x01"
_LZMA_ERRORS = (lzma.LZMAError,) if lzma is not None else ()


class _Bytes(object):
    """Bytes read in order, every read checked against their end. Each number, and each byte read on its own, counts
    against `reads`, the most a header of this file may take (more: _Bad); the cancel event is looked at every
    CANCEL_EVERY of them. `done` carries the count over from a header read before (an encoded header's)."""

    def __init__(self, data, reads, cancel, done=0):
        self.data = data
        self.at = 0
        self.reads = reads
        self.done = done
        self.cancel = cancel

    def _counted(self):
        self.done += 1
        if self.done > self.reads:
            raise _Bad()                    # more than a header of a file this size can need
        _stop(self.cancel, self.done)

    def _next(self):
        if self.at >= len(self.data):
            raise _Bad()
        self.at += 1
        return self.data[self.at - 1]

    def left(self):
        return len(self.data) - self.at

    def byte(self):
        self._counted()
        return self._next()

    def take(self, n):
        if n < 0 or n > len(self.data) - self.at:
            raise _Bad()
        self.at += n
        return self.data[self.at - n:self.at]

    def number(self):
        """A 7z number: the high bits of its first byte say how many bytes follow (7zFormat.txt, REAL_UINT64)."""
        self._counted()
        first = self._next()
        mask = 0x80
        value = 0
        for i in range(8):
            if not first & mask:
                return value | ((first & (mask - 1)) << (8 * i))
            value |= self._next() << (8 * i)
            mask >>= 1
        return value

    def count(self, most, why, limit=None):
        """A number of things each written in a byte or more: _Bad past the bytes left or past `limit`, _Reason(why)
        past `most`; all before any of them is read."""
        n = self.number()
        if n > self.left() or (limit is not None and n > limit):
            raise _Bad()
        if n > most:
            raise _Reason(why)
        return n


def _attributes(b):
    """The type of each attribute of a property list, read up to its kEnd as the caller reads each attribute in turn:
    at most ATTRIBUTES_MAX of them (more: _Bad)."""
    for _ in range(ATTRIBUTES_MAX):
        t = b.number()
        if t == _END:
            return
        yield t
    if b.number() != _END:
        raise _Bad()


def _skip(b):
    """An attribute this reader does not need: its size, then that many bytes."""
    b.take(b.number())


def _digests(b, n, keep=True):
    """The CRCs of n streams: [crc or None]; with keep False, read past and not kept."""
    if b.byte():
        defined = n
        bits = None
    else:
        bits = b.take((n + 7) // 8)
        full, rest = divmod(n, 8)
        defined = bin(int.from_bytes(bits[:full], "big")).count("1")
        if rest:
            defined += bin(bits[full] >> (8 - rest)).count("1")
    if not keep:
        b.take(4 * defined)
        return None
    crcs = iter(struct.unpack("<%dI" % defined, b.take(4 * defined)))
    return [next(crcs) if bits is None or bits[i >> 3] & (0x80 >> (i & 7)) else None for i in range(n)]


def _pack_info(b, most, why, area):
    """(PackPos, [the size of each packed stream]) of a kPackInfo, read through its kEnd: PackPos inside the `area`
    bytes before the header, and no more streams than the bytes after it there (else _Bad); at most `most` streams
    (else _Reason(why))."""
    pos = b.number()
    if pos > area:
        raise _Bad()
    n = b.count(most, why, area - pos)
    sizes = None
    for t in _attributes(b):
        if t == _SIZE:
            sizes = [b.number() for _ in range(n)]
        elif t == _CRC:
            _digests(b, n, keep=False)
        else:
            _skip(b)
    if sizes is None:
        raise _Bad()
    return pos, sizes


def _folder(b):
    """One folder of a kUnPackInfo: (its coders [(method, properties)], how many streams come out of it). At most 64
    coders, FOLDER_STREAMS_MAX streams in and FOLDER_STREAMS_MAX out (else _Bad): so at most FOLDER_STREAMS_MAX - 1
    bind pairs."""
    coders = []
    ins = outs = 0
    for _ in range(b.count(64, WHY_NOT_7Z)):
        flags = b.byte()
        if flags & 0xC0:                    # alternative methods, reserved
            raise _Bad()
        method = bytes(b.take(flags & 0x0F))
        ci = co = 1
        if flags & 0x10:
            ci, co = b.number(), b.number()
        ins += ci
        outs += co
        if not (0 < ci and 0 < co) or ins > FOLDER_STREAMS_MAX or outs > FOLDER_STREAMS_MAX:
            raise _Bad()
        props = bytes(b.take(b.number())) if flags & 0x20 else b""
        coders.append((method, props))
    if not coders or ins < outs:
        raise _Bad()
    for _ in range(2 * (outs - 1)):         # bind pairs: an in index and an out index each
        b.number()
    packed = ins - (outs - 1)
    if packed > 1:
        for _ in range(packed):
            b.number()
    return coders, outs


def _unpack_info(b, most, why, keep, limit):
    """A kUnPackInfo, read through its kEnd: its folders [(coders, [unpack sizes], crc)] with `keep`, else how many it
    has. No more folders than `limit`, the packed streams there are (else _Bad), at most `most` (else _Reason(why)):
    counted before any is read."""
    if b.number() != _FOLDER:
        raise _Bad()
    n = b.count(most, why, limit)
    if b.byte() != 0:                       # External: not in this header
        raise _Bad()
    folders = []
    outs = 0
    for _ in range(n):
        coders, out = _folder(b)
        outs += out
        if keep:
            folders.append((coders, out))
    if b.number() != _UNPACK_SIZE:
        raise _Bad()
    sizes = [[b.number() for _ in range(out)] for _coders, out in folders]
    for _ in range(0 if keep else outs):
        b.number()
    crcs = [None] * n if keep else None
    for t in _attributes(b):
        if t == _CRC:
            crcs = _digests(b, n, keep)
        else:
            _skip(b)
    return [(coders, s, c) for (coders, _out), s, c in zip(folders, sizes, crcs)] if keep else n


def _filters(method, props, size):
    """lzma's raw filters for a 7z header coder: LZMA or LZMA2 (the dictionary no bigger than what it decodes)."""
    if method == _LZMA and len(props) == 5 and props[0] < 9 * 5 * 5:
        lc, lp, pb = props[0] % 9, props[0] // 9 % 5, props[0] // 45
        dict_size = struct.unpack("<I", props[1:5])[0]
        return [{"id": lzma.FILTER_LZMA1, "lc": lc, "lp": lp, "pb": pb,
                 "dict_size": max(4096, min(dict_size, size))}]
    if method == _LZMA2 and len(props) == 1 and props[0] <= 40:
        p = props[0]
        dict_size = 0xFFFFFFFF if p == 40 else (2 | (p & 1)) << (p // 2 + 11)
        return [{"id": lzma.FILTER_LZMA2, "dict_size": max(4096, min(dict_size, size))}]
    raise _Reason(WHY_NOT_7Z)


def _decoded(f, at, packed, folder, cancel, keep=HEADER_KEEP):
    """The start (`keep` bytes at most) of the header an encoded header's folder decodes to, its CRC checked: its
    packed stream read and decoded a CHUNK at a time, refused before anything is decoded when it says it decodes to
    more than HEADER_MAX or more than HEADER_RATIO times its packed size."""
    coders, sizes, crc = folder
    if any(method == _AES for method, _props in coders):
        raise _Reason(WHY_ENCRYPTED)
    if len(coders) != 1 or len(sizes) != 1:
        raise _Reason(WHY_NOT_7Z)
    if lzma is None:
        raise _Reason(WHY_NO_LZMA)
    size = sizes[0]
    if not 0 < size <= HEADER_MAX or size > HEADER_RATIO * packed:
        raise _Bad()
    dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=_filters(coders[0][0], coders[0][1], size))
    f.seek(at)
    left = packed
    head = bytearray()
    got = check = steps = idle = 0
    while got < size:
        steps += 1
        _stop(cancel, steps * CANCEL_EVERY)
        if dec.eof:
            raise _Bad()                    # its stream ends before its stated size
        data = b""
        if dec.needs_input:
            data = f.read(min(CHUNK, left))
            if not data:
                raise _Bad()                # its packed stream ends before its stated size
            left -= len(data)
        chunk = dec.decompress(data, max_length=min(CHUNK, size - got))
        idle = 0 if chunk or data else idle + 1
        if idle > 1:
            raise _Bad()                    # nothing in and nothing out, twice: it goes nowhere
        got += len(chunk)
        check = zlib.crc32(chunk, check)
        if len(head) < keep:
            head += chunk[:keep - len(head)]
    if crc is not None and check != crc:
        raise _Bad()
    return bytes(head)


def _header_start(f, at, n, cancel, keep=HEADER_KEEP):
    """The start (`keep` bytes at most) of the n header bytes at `at`, and the CRC32 of all of them."""
    head = bytearray()
    check = steps = 0
    f.seek(at)
    left = n
    while left:
        steps += 1
        _stop(cancel, steps * CANCEL_EVERY)
        chunk = f.read(min(CHUNK, left))
        if not chunk:
            raise _Bad()
        check = zlib.crc32(chunk, check)
        if len(head) < keep:
            head += chunk[:keep - len(head)]
        left -= len(chunk)
    return bytes(head), check


def sevenzip_layout(f, size, most, cancel=None):
    """The data areas of the 7z in f: its packed streams. With them, WHY_SOLID for a 7z of one folder (one solid
    block), WHY_ONE_FILE for one of one file."""
    try:
        return _sevenzip(f, size, most, cancel)
    except _Reason as r:
        return None, r.code
    except (_Bad, EOFError, ValueError, OverflowError, IndexError, struct.error, MemoryError) + _LZMA_ERRORS:
        return None, WHY_NOT_7Z


def _sevenzip(f, size, most, cancel):
    sig = _read_at(f, 0, 32)
    if not sig.startswith(SEVENZIP_MAGIC) or sig[6] != 0:
        raise _Bad()
    start_crc, offset, n, crc = struct.unpack("<IQQI", sig[8:32])
    if zlib.crc32(sig[12:32]) != start_crc:
        raise _Bad()
    hstart = 32 + offset
    if not 0 < n <= HEADER_MAX or hstart + n > size:
        raise _Bad()
    head, check = _header_start(f, hstart, n, cancel)
    if check != crc:
        raise _Bad()
    b = _Bytes(head, size // READS_BYTES + READS_BASE, cancel)
    t = b.number()
    own = None                              # an encoded header's packed stream: not a data area
    if t == _ENCODED_HEADER:
        if b.number() != _PACK_INFO:
            raise _Bad()
        pos, sizes = _pack_info(b, 1, WHY_NOT_7Z, offset)
        if b.number() != _UNPACK_INFO:
            raise _Bad()
        folders = _unpack_info(b, 1, WHY_NOT_7Z, True, len(sizes))
        if len(sizes) != 1 or len(folders) != 1:
            raise _Reason(WHY_NOT_7Z)
        at = 32 + pos
        if at + sizes[0] > hstart:
            raise _Bad()
        own = (at, at + sizes[0])
        b = _Bytes(_decoded(f, at, sizes[0], folders[0], cancel), b.reads, cancel, b.done)
        t = b.number()
    if t != _HEADER:
        raise _Bad()
    t = b.number()
    if t == _ARCHIVE_PROPERTIES:
        for _t in _attributes(b):
            _skip(b)
        t = b.number()
    if t == _ADDITIONAL_STREAMS:
        raise _Reason(WHY_NOT_7Z)
    ranges = []
    why = ""
    if t == _MAIN_STREAMS:
        t = b.number()
        streams = 0
        if t == _PACK_INFO:
            pos, sizes = _pack_info(b, most, WHY_PIECES, offset)
            streams = len(sizes)
            at = 32 + pos
            for i, s in enumerate(sizes):
                _stop(cancel, i + 1)
                if s:
                    ranges.append((at, at + s))
                at += s
                if at > hstart:             # every packed stream ends before the header
                    raise _Bad()
            t = b.number()
        if t == _UNPACK_INFO and _unpack_info(b, most, WHY_PIECES, False, streams) == 1:
            # one folder: one solid block, whatever its streams (a BCJ2 folder has four); one file when it holds one
            files = 1
            if b.number() == _SUBSTREAMS and b.number() == _NUM_UNPACK_STREAM:
                files = b.number()
            why = WHY_ONE_FILE if files == 1 else WHY_SOLID
    if own is not None:                     # no packed stream reaches into the encoded header's own (sorted, apart)
        i = bisect.bisect_left(ranges, (own[1],))
        if i and ranges[i - 1][1] > own[0]:
            raise _Bad()
    return ranges, why


# ---- RAR ------------------------------------------------------------------------------------------------------------

def _vint(data, at):
    """(the RAR5 vint at data[at:], where it ends): 7 bits a byte, low bits first, at most 10 bytes."""
    value = 0
    for i in range(10):
        if at + i >= len(data):
            raise _Bad()
        c = data[at + i]
        value |= (c & 0x7F) << (7 * i)
        if not c & 0x80:
            return value, at + i + 1
    raise _Bad()


def rar_layout(f, size, most, cancel=None):
    """The data areas of the RAR (5 or 4) in f: those of its file and service blocks."""
    try:
        head = _read_at(f, 0, 8 if size >= 8 else 7)
        if head.startswith(RAR5_MAGIC):
            return _rar5(f, size, most, cancel)[0], ""
        if head.startswith(RAR4_MAGIC):
            return _rar4(f, size, most, cancel)[0], ""
        raise _Bad()
    except _Reason as r:
        return None, r.code
    except (_Bad, ValueError, OverflowError, IndexError, struct.error):
        return None, WHY_NOT_RAR


def rar_solid(f, size):
    """The RAR in f is solid (its main header says so; False for anything else)."""
    try:
        head = _read_at(f, 0, 8 if size >= 8 else 7)
        if head.startswith(RAR5_MAGIC):
            return _rar5(f, size, 0, None, main_only=True)[1]
        if head.startswith(RAR4_MAGIC):
            return _rar4(f, size, 0, None, main_only=True)[1]
    except (_Bad, _Reason, ValueError, OverflowError, IndexError, struct.error):
        pass
    return False


def _rar5(f, size, most, cancel, main_only=False):
    """([data areas], solid) of a RAR5 archive: blocks of CRC32, header size (a vint), header type, flags, an extra
    area size and a data area size when the flags say, then the type's fields; the data area follows the header."""
    pos = 8
    ranges = []
    solid = False
    first = True
    blocks = 0
    while True:
        blocks += 1
        _stop(cancel, blocks)
        if blocks > 2 * most + 16 and not main_only:
            raise _Reason(WHY_PIECES)
        lead = _read_at(f, pos, min(7, size - pos)) if pos < size else b""
        if len(lead) < 5:
            raise _Bad()                    # no end of archive block
        crc = struct.unpack("<I", lead[:4])[0]
        hsize, at = _vint(lead, 4)
        if not 0 < hsize <= RAR5_HEADER_MAX:
            raise _Bad()
        body = pos + at
        if body + hsize > size:
            raise _Bad()
        raw = _read_at(f, pos + 4, at - 4 + hsize)
        if zlib.crc32(raw) != crc:
            raise _Bad()
        hdr = raw[at - 4:]
        htype, i = _vint(hdr, 0)
        flags, i = _vint(hdr, i)
        if flags & 0x0001:
            _extra, i = _vint(hdr, i)
        data = 0
        if flags & 0x0002:
            data, i = _vint(hdr, i)
        if first:
            if htype == 4:
                raise _Reason(WHY_ENCRYPTED)  # the archive encryption header: every header after it is encrypted
            if htype != 1:
                raise _Bad()
            archive_flags, i = _vint(hdr, i)
            solid = bool(archive_flags & 0x0004)
            first = False
            if main_only:
                return ranges, solid
        elif htype == 4:
            raise _Bad()
        end = body + hsize
        if data > size - end:
            raise _Bad()
        if data:
            ranges.append((end, end + data))
            if len(ranges) > most:
                raise _Reason(WHY_PIECES)
        pos = end + data
        if htype == 5:                      # the end of the archive
            return ranges, solid


def _rar4(f, size, most, cancel, main_only=False):
    """([data areas], solid) of a RAR4 archive: after the 7 bytes of its marker block, blocks of HEAD_CRC (the low 16
    bits of the CRC32 of the rest of the header), HEAD_TYPE, HEAD_FLAGS, HEAD_SIZE; a file or service block gives its
    data size as PACK_SIZE (and HIGH_PACK_SIZE with LHD_LARGE), any other block as ADD_SIZE when its flags have
    0x8000. The data area follows the header."""
    pos = 7
    ranges = []
    solid = False
    first = True
    blocks = 0
    while pos < size:
        blocks += 1
        _stop(cancel, blocks)
        if blocks > 2 * most + 16 and not main_only:
            raise _Reason(WHY_PIECES)
        lead = _read_at(f, pos, min(7, size - pos))
        if len(lead) < 7:
            raise _Bad()
        crc, htype, flags, hsize = struct.unpack("<HBHH", lead)
        if hsize < 7 or pos + hsize > size:
            raise _Bad()
        hdr = _read_at(f, pos, hsize)
        if zlib.crc32(hdr[2:]) & 0xFFFF != crc and not (htype == 0x73 and hsize > 13
                                                        and zlib.crc32(hdr[2:13]) & 0xFFFF == crc):
            raise _Bad()                    # (an old main header's CRC leaves out the comment inside it)
        if first:
            if htype != 0x73:
                raise _Bad()
            if flags & 0x0080:
                raise _Reason(WHY_ENCRYPTED)  # MHD_PASSWORD: every header after it is encrypted
            solid = bool(flags & 0x0008)
            first = False
            if main_only:
                return ranges, solid
        data = 0
        if htype in (0x74, 0x7A):
            if hsize < 32:
                raise _Bad()
            data = struct.unpack("<I", hdr[7:11])[0]
            if flags & 0x0100:
                if hsize < 36:
                    raise _Bad()
                data |= struct.unpack("<I", hdr[32:36])[0] << 32
        elif flags & 0x8000:
            if hsize < 11:
                raise _Bad()
            data = struct.unpack("<I", hdr[7:11])[0]
        end = pos + hsize
        if data > size - end:
            raise _Bad()
        if data:
            ranges.append((end, end + data))
            if len(ranges) > most:
                raise _Reason(WHY_PIECES)
        pos = end + data
        if htype == 0x7B:                   # the end of the archive
            break
    if first:
        raise _Bad()
    return ranges, solid
