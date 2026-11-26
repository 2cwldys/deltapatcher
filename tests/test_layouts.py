"""Archive deltas for tar, 7z and RAR (deltapatcher/layouts.py, parts.py): each kind's reader finds where the archive
keeps the packed data of its files, so a changed archive is rebuilt from the ones a player has, as a zip is
(test_archives.py). Python writes no 7z or RAR: this file carries a small writer of each (7z with the Copy coder, one
folder for each file or one for all, or one folder of four packed streams as 7-Zip's BCJ2 filter makes, its header
plain or LZMA or LZMA2 encoded; RAR5 and RAR4 with the store method, their CRCs and sizes as rarlab.com documents
them), checked once against 7-Zip and RAR themselves, and tests/data/ holds a few tiny archives made by 7-Zip 22.00
and RAR 7.12 (how: tests/data/README.txt). tar archives are made with tarfile (ustar, GNU and pax) and by hand, a
header at a time. Every reader is also given damaged copies of each archive (cut short at every byte, a CRC flipped,
a size or a count past the end) and crafted ones (counts, extension blocks and headers that would cost minutes or
memory): each gets its reason at once, never an exception."""

import functools
import hashlib
import io
import json
import lzma
import os
import random
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import zipfile
import zlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import read, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import client, layouts, parts, publish, rebuild  # noqa: E402
from test_archives import TEXTURE, Base  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LFS_POINTER = b"version https://git-lfs"   # how a Git LFS pointer file begins


# ---- writers --------------------------------------------------------------------------------------------------------

def sz_number(n):
    """A 7z number (7zFormat.txt): as many leading 1 bits in the first byte as bytes follow, low bytes first."""
    for k in range(8):
        if n < 1 << (7 * k + 7):
            return bytes([((0xFF00 >> k) & 0xFF) | (n >> (8 * k))]) + (n & ((1 << (8 * k)) - 1)).to_bytes(k, "little")
    return b"\xff" + n.to_bytes(8, "little")


def sevenzip_bytes(files, encoded="", solid=False, aes=False, bcj2=False):
    """A 7z of files [(name, data)] with the Copy coder: one folder (and packed stream) for each file, or with `solid`
    one for all; with `bcj2`, one folder of four packed streams as 7-Zip's BCJ2 filter makes (a BCJ2 coder of four
    streams in, three of them out of LZMA coders; nothing here is really packed); its header plain, or encoded
    ("lzma", "lzma2"); with `aes`, an encoded header whose folder names AES before LZMA (as 7-Zip's -mhe=on does;
    nothing here is really encrypted)."""
    n = sz_number
    datas = [d for _name, d in files]
    whole = b"".join(datas)
    if bcj2:
        packs = [whole[:-30], whole[-30:-20], whole[-20:-10], whole[-10:]]
        lz = b"\x23\x03\x01\x01" + n(5) + bytes([(2 * 5 + 0) * 9 + 3]) + struct.pack("<I", 1 << 16)
        pairs = n(0) + n(1) + n(1) + n(2) + n(2) + n(3)                  # BCJ2's in 0, 1, 2 from the LZMA coders
        folders = [n(4) + b"\x14\x03\x03\x01\x1b" + n(4) + n(1) + lz * 3 + pairs + n(3) + n(4) + n(5) + n(6)]
        outs = [[whole] + packs[:3]]                                     # what each coder of the folder gives
    else:
        packs = [whole] if solid else datas
        folders = [n(1) + b"\x01\x00" for _p in packs]
        outs = [[p] for p in packs]
    body = b"".join(packs)
    h = bytearray(b"\x01\x04\x06") + n(0) + n(len(packs)) + b"\x09" + b"".join(n(len(p)) for p in packs) + b"\x00"
    h += b"\x07\x0b" + n(len(folders)) + b"\x00" + b"".join(folders)
    h += b"\x0c" + b"".join(n(len(o)) for out in outs for o in out)
    h += b"\x0a\x01" + b"".join(struct.pack("<I", zlib.crc32(out[0])) for out in outs) + b"\x00"
    if solid or bcj2:
        h += b"\x08\x0d" + n(len(files)) + b"\x09" + b"".join(n(len(d)) for d in datas[:-1])
        h += b"\x0a\x01" + b"".join(struct.pack("<I", zlib.crc32(d)) for d in datas) + b"\x00"
    h += b"\x00"
    names = b"\x00" + b"".join(name.encode("utf-16-le") + b"\x00\x00" for name, _d in files)
    h += b"\x05" + n(len(files)) + b"\x11" + n(len(names)) + names + b"\x00\x00"
    header = bytes(h)
    if encoded or aes:
        if encoded == "lzma2":
            packed = lzma.compress(header, format=lzma.FORMAT_RAW,
                                   filters=[{"id": lzma.FILTER_LZMA2, "dict_size": 1 << 16}])
            coder = b"\x21\x21" + n(1) + bytes([8])                      # LZMA2, a 64 KiB dictionary
        else:
            packed = lzma.compress(header, format=lzma.FORMAT_RAW, filters=[
                {"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16, "lc": 3, "lp": 0, "pb": 2}])
            coder = b"\x23\x03\x01\x01" + n(5) + bytes([(2 * 5 + 0) * 9 + 3]) + struct.pack("<I", 1 << 16)
        coders, sizes = n(1) + coder, n(len(header))
        if aes:                                                          # AES, then LZMA, and the pair binding them
            coders = n(2) + b"\x24\x06\xf1\x07\x01" + n(2) + b"\x00\x00" + coder + n(1) + n(0)
            sizes = n(len(header)) + n(len(header))
        enc = b"\x17\x06" + n(len(body)) + n(1) + b"\x09" + n(len(packed)) + b"\x00"
        enc += b"\x07\x0b" + n(1) + b"\x00" + coders + b"\x0c" + sizes
        enc += b"\x0a\x01" + struct.pack("<I", zlib.crc32(header)) + b"\x00\x00"
        body += packed
        header = enc
    start = struct.pack("<QQI", len(body), len(header), zlib.crc32(header))
    return b"7z\xbc\xaf\x27\x1c\x00\x04" + struct.pack("<I", zlib.crc32(start)) + start + body + header


def vint(n):
    """A RAR5 vint: 7 bits a byte, low bits first, the high bit set while more follow."""
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def rar5_block(htype, fields, data=b""):
    """A RAR5 block: its CRC32 (of the header size and the header), the header size, type, flags, data size, the
    type's fields, then the data area."""
    body = vint(htype) + vint(0x0002 if data else 0) + (vint(len(data)) if data else b"") + fields
    size = vint(len(body))
    return struct.pack("<I", zlib.crc32(size + body)) + size + body + data


def rar5_bytes(files, solid=False, encrypted=False):
    """A RAR5 archive of files [(name, data)], stored (method 0): its main header (solid when asked), a file header
    with each data area, the end of archive. With `encrypted`, an archive encryption header and bytes that stand for
    the encrypted headers and data after it."""
    if encrypted:
        rest = bytes(64 + sum(len(d) for _name, d in files))
        return layouts.RAR5_MAGIC + rar5_block(4, vint(0) + vint(0) + bytes([15]) + bytes(16)) + rest
    out = layouts.RAR5_MAGIC + rar5_block(1, vint(0x0004 if solid else 0))
    for name, data in files:
        nb = name.encode("utf-8")
        out += rar5_block(2, vint(0x0004) + vint(len(data)) + vint(0x20) + struct.pack("<I", zlib.crc32(data))
                          + vint(0) + vint(0) + vint(len(nb)) + nb, data)
    return out + rar5_block(5, vint(0))


def rar4_block(htype, flags, rest, data=b""):
    """A RAR4 block: HEAD_CRC (the low 16 bits of the CRC32 of the rest of the header), HEAD_TYPE, HEAD_FLAGS,
    HEAD_SIZE, the rest of the header, then the data."""
    body = struct.pack("<BHH", htype, flags, 7 + len(rest)) + rest
    return struct.pack("<H", zlib.crc32(body) & 0xFFFF) + body + data


def rar4_bytes(files, solid=False, encrypted=False, large=False):
    """A RAR4 archive of files [(name, data)], stored (METHOD 0x30): marker, main header (MHD_SOLID, MHD_PASSWORD
    when asked), a file header with PACK_SIZE (and HIGH_PACK_SIZE with `large`: LHD_LARGE) before each data, the end
    of archive."""
    out = layouts.RAR4_MAGIC + rar4_block(0x73, (0x0008 if solid else 0) | (0x0080 if encrypted else 0), bytes(6))
    if encrypted:
        return out + bytes(64 + sum(len(d) for _name, d in files))
    for i, (name, data) in enumerate(files):
        nb = name.encode("ascii")
        rest = struct.pack("<IIBIIBBHI", len(data), len(data), 2, zlib.crc32(data), 0x5B610000, 20, 0x30,
                           len(nb), 0x20)
        if large:
            rest += struct.pack("<II", 0, 0)
        out += rar4_block(0x74, 0x8000 | (0x0010 if solid and i else 0) | (0x0100 if large else 0), rest + nb, data)
    return out + rar4_block(0x7B, 0x4000, b"")


def tar_bytes(files, fmt=tarfile.USTAR_FORMAT, pax=None):
    """A tar of files [(name, data)] in the tarfile format `fmt`; `pax`: pax headers for every member."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as tf:
        for name, data in files:
            ti = tarfile.TarInfo(name)
            ti.size, ti.mtime, ti.mode = len(data), 1793000000, 0o644
            if pax:
                ti.pax_headers = dict(pax)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def tar_header(name, size=0, kind=b"0", size_field=None, signed=False, magic=b"ustar\x0000"):
    """A ustar header block made by hand (or with another `magic`: GNU's "ustar  \0"): `size` in octal, or
    `size_field` as it is (GNU base-256); its checksum the unsigned sum of its bytes, or with `signed` the signed sum
    some old writers made."""
    h = bytearray(512)
    h[0:len(name)] = name
    h[100:108], h[108:116], h[116:124] = b"0000644\0", b"0000000\0", b"0000000\0"
    h[124:136] = b"%011o\0" % size if size_field is None else size_field
    h[136:148] = b"15270321400\0"
    h[148:156] = b" " * 8
    h[156:157] = kind
    h[257:265] = magic
    h[148:156] = b"%06o\0 " % (sum(h) - (256 * sum(c >= 128 for c in h) if signed else 0))
    return bytes(h)


def pax_record(key, value):
    """A pax record, "<length> <key>=<value>\\n", its length counting its own digits."""
    body = b" " + key + b"=" + value + b"\n"
    n = len(body) + 1
    while len(b"%d" % n) + len(body) != n:
        n += 1
    return b"%d" % n + body


def pad(data):
    return data + bytes(-len(data) % 512)


def tar_by_hand(*members):
    """A tar of hand-made members (header block, data): each data padded to 512 bytes, then the two end blocks."""
    return b"".join(head + pad(data) for head, data in members) + bytes(1024)


def seven_bytes(header, packed=b""):
    """A 7z of `packed` (its packed streams) and `header`, every CRC right."""
    start = struct.pack("<QQI", len(packed), len(header), zlib.crc32(header))
    return layouts.SEVENZIP_MAGIC + b"\x00\x04" + struct.pack("<I", zlib.crc32(start)) + start + packed + header


def encoded_7z(plain):
    """A 7z whose header is `plain` packed with LZMA2, its packed stream as short as HEADER_RATIO lets it be."""
    n = sz_number
    packed = lzma.compress(plain, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "preset": 1}])
    packed += bytes(max(0, -(-len(plain) // layouts.HEADER_RATIO) - len(packed)))
    enc = b"\x17\x06" + n(0) + n(1) + b"\x09" + n(len(packed)) + b"\x00"
    enc += b"\x07\x0b" + n(1) + b"\x00" + n(1) + b"\x21\x21" + n(1) + bytes([40]) + b"\x0c" + n(len(plain))
    enc += b"\x0a\x01" + struct.pack("<I", zlib.crc32(plain)) + b"\x00\x00"
    return seven_bytes(enc, packed)


def attribute_pairs():
    """A 7z header of kArchiveProperties attributes, each a type and a size of 0, up to HEADER_KEEP bytes."""
    return b"\x01\x02" + b"\x01\x00" * ((layouts.HEADER_KEEP - 4) // 2) + b"\x00\x00"


@functools.lru_cache(maxsize=None)
def crafted_7z():
    """attribute_pairs() packed with LZMA2: a 7z of 33 KB whose header decodes to 32 MiB."""
    return encoded_7z(attribute_pairs())


MAKERS = (("tar (ustar)", "tar", lambda files: tar_bytes(files, tarfile.USTAR_FORMAT)),
          ("tar (GNU)", "tar", lambda files: tar_bytes(files, tarfile.GNU_FORMAT)),
          ("tar (pax)", "tar", lambda files: tar_bytes(files, tarfile.PAX_FORMAT)),
          ("7z (plain header)", "7z", sevenzip_bytes),
          ("7z (LZMA header)", "7z", lambda files: sevenzip_bytes(files, encoded="lzma")),
          ("7z (LZMA2 header)", "7z", lambda files: sevenzip_bytes(files, encoded="lzma2")),
          ("RAR5", "rar", rar5_bytes),
          ("RAR4", "rar", rar4_bytes),
          ("RAR4 (LHD_LARGE)", "rar", lambda files: rar4_bytes(files, large=True)))


def small_files(long_name=True):
    """Two files of 3 KB or so, one with a long name (ustar takes only up to 100 characters a component), and a note:
    an archive of a few KB."""
    return [("a.bin", os.urandom(3000)), (("x" * 120 if long_name else "dir") + "/b.bin", os.urandom(3100)),
            ("c.txt", b"hello\n" * 30)]


def big_files(n=13):
    """The files of a big archive: n textures of TEXTURE random bytes (nothing packs them smaller) and two notes."""
    return ([("textures/t%02d.tga" % i, os.urandom(TEXTURE)) for i in range(n)]
            + [("notes/%d.txt" % i, b"note %d\n" % i * 20) for i in range(2)])


def medium_files():
    """Two files of 20 KB or so and a note: an archive whose headers (and a tar's padding) are a small part of it."""
    return [("a.bin", os.urandom(20000)), ("dir/b.bin", os.urandom(21000)), ("c.txt", b"hello\n" * 30)]


def layout_of(data):
    return parts.find_layout(io.BytesIO(data), len(data))


def fixture(name):
    data = read(os.path.join(DATA, name))
    if data.startswith(LFS_POINTER):
        raise AssertionError("tests/data holds Git LFS pointers: check .gitattributes")
    return data


# ---- the readers ----------------------------------------------------------------------------------------------------

class Readers(unittest.TestCase):
    def test_every_writer_and_form_gives_the_data_of_each_file(self):
        for label, kind, make in MAKERS:
            files = small_files(long_name=label != "tar (ustar)")
            data = make(files)
            got = layout_of(data)
            self.assertEqual((got[0], got[2]), (kind, ""), label)
            self.assertEqual([data[a:b] for a, b in got[1]], [d for _n, d in files], label)   # each file's bytes
        # tarfile's own offsets, a GNU long name header among the blocks
        data = tar_bytes(small_files(), tarfile.GNU_FORMAT)
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            members = tf.getmembers()
        self.assertEqual([(ti.offset_data, ti.offset_data + ti.size) for ti in members], layout_of(data)[1])
        at = members[1].offset                                             # the second member's first header: its
        self.assertEqual(data[at + 156:at + 157], tarfile.GNUTYPE_LONGNAME)  # long name, its own header after it

    def test_what_gets_no_layout_and_why(self):
        files = small_files()
        sparse = tar_bytes(files[:1], tarfile.PAX_FORMAT, {"GNU.sparse.numblocks": "1", "GNU.sparse.offset": "0",
                                                           "GNU.sparse.numbytes": "3000", "GNU.sparse.size": "3000"})
        for data, want in ((sparse, ("tar", None, layouts.WHY_SPARSE)),
                           (sevenzip_bytes(files, aes=True), ("7z", None, layouts.WHY_ENCRYPTED)),
                           (rar5_bytes(files, encrypted=True), ("rar", None, layouts.WHY_ENCRYPTED)),
                           (rar4_bytes(files, encrypted=True), ("rar", None, layouts.WHY_ENCRYPTED)),
                           (layouts.SEVENZIP_MAGIC + os.urandom(5000), ("7z", None, layouts.WHY_NOT_7Z)),
                           (layouts.RAR5_MAGIC + os.urandom(5000), ("rar", None, layouts.WHY_NOT_RAR)),
                           (layouts.RAR4_MAGIC + os.urandom(5000), ("rar", None, layouts.WHY_NOT_RAR)),
                           (b"\x1f\x8b\x08" + os.urandom(5000), ("", None, parts.WHY_NONE)),
                           (b"MZ" + os.urandom(5000), ("", None, parts.WHY_NONE)),
                           (b"PK\x03\x04" + os.urandom(5000), ("zip", None, parts.WHY_UNREADABLE))):
            self.assertEqual(layout_of(data), want, data[:8])
        # a 7z of one folder (one solid block) has a layout, but gets no index: nothing of it could be reused
        solid = sevenzip_bytes(files, solid=True)
        self.assertEqual(len(layout_of(solid)[1]), 1)
        folder = tempfile.mkdtemp(prefix="dp-layouts-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        write(os.path.join(folder, "solid.7z"), solid)
        with mock.patch.object(parts, "PARTS_MIN", 1024):
            self.assertEqual(parts.make_index(os.path.join(folder, "solid.7z"), hashlib.sha256(solid).hexdigest(),
                                              len(solid)), (None, layouts.WHY_SOLID))
        self.assertEqual(parts.reason(layouts.WHY_SOLID), "packed as one solid stream: with 7-Zip, pack it with solid "
                                                          "mode off (-ms=off) so only the files that changed travel")
        self.assertEqual([parts.reason(w) for w in (layouts.WHY_ENCRYPTED, layouts.WHY_SPARSE, layouts.WHY_NOT_7Z,
                                                    layouts.WHY_NOT_RAR, layouts.WHY_NOT_TAR, parts.WHY_NONE)],
                         ["its headers are encrypted", "it holds a sparse file", "not a 7z this reader knows",
                          "not a RAR this reader knows", "not a tar this reader knows", ""])

    def test_a_solid_rar_is_known_by_its_main_header(self):
        files = small_files()
        for data, solid in ((rar5_bytes(files), False), (rar5_bytes(files, solid=True), True),
                            (rar4_bytes(files), False), (rar4_bytes(files, solid=True), True),
                            (fixture("rar5-solid.rar"), True), (fixture("rar5-files-v1.rar"), False),
                            (sevenzip_bytes(files), False), (b"", False)):
            self.assertEqual(layouts.rar_solid(io.BytesIO(data), len(data)), solid)

    def test_real_archives_from_7zip_and_rar(self):
        for name in ("7z-files-v1.7z", "7z-files-v2.7z", "7z-plain-header.7z"):
            kind, ranges, why = layout_of(fixture(name))                 # LZMA2 file by file: random bytes, a bit more
            self.assertEqual((kind, why, [b - a for a, b in ranges], ranges[0][0]), ("7z", "", [1504, 1604, 1704, 36],
                                                                                     32), name)
        for one, two in (("7z-files-v1.7z", "7z-files-v2.7z"), ("rar5-files-v1.rar", "rar5-files-v2.rar")):
            v1, v2 = fixture(one), fixture(two)
            r1, r2 = layout_of(v1)[1], layout_of(v2)[1]
            self.assertEqual(r1, r2, one)
            self.assertEqual([v1[a:b] == v2[a:b] for a, b in r1], [True, False, True, True], one)   # b.bin changed
        self.assertEqual(len(layout_of(fixture("7z-solid.7z"))[1]), 1)
        self.assertEqual(layout_of(fixture("7z-encrypted-header.7z")), ("7z", None, layouts.WHY_ENCRYPTED))
        kind, ranges, why = layout_of(fixture("rar5-files-v1.rar"))
        self.assertEqual((kind, why, [b - a for a, b in ranges]), ("rar", "", [1500, 1600, 1700, 33]))
        self.assertEqual(layout_of(fixture("rar5-encrypted-headers.rar")), ("rar", None, layouts.WHY_ENCRYPTED))
        kind, ranges, why = layout_of(fixture("rar5-solid.rar"))
        self.assertEqual((kind, why, len(ranges)), ("rar", "", 4))
        # a set of volumes: each its own; b.bin's data area in both, 1389 bytes of it and then the rest
        one, two = layout_of(fixture("rar5-volumes.part1.rar")), layout_of(fixture("rar5-volumes.part2.rar"))
        self.assertEqual(([b - a for a, b in one[1]], [b - a for a, b in two[1]]), ([1500, 1389], [211, 1700, 300]))

    def samples(self):
        """Archives to damage: (label, its bytes, whether it needs its last block: a 7z its header, a RAR5 its end)."""
        out = [(label, make(small_files(long_name=label != "tar (ustar)")), kind != "tar" and not label.startswith(
            "RAR4")) for label, kind, make in MAKERS]
        out += [(name, fixture(name), True) for name in ("7z-files-v1.7z", "7z-plain-header.7z", "7z-solid.7z",
                                                          "rar5-files-v1.rar", "rar5-solid.rar",
                                                          "rar5-volumes.part1.rar")]
        return out

    def test_cut_short_anywhere_never_raises_and_never_reaches_past_the_cut(self):
        started = time.time()
        for label, data, needs_end in self.samples():
            whole = layout_of(data)[1]
            for cut in range(len(data)):
                got = layout_of(data[:cut])
                if got[1] is not None:
                    self.assertTrue(all(b <= cut for _a, b in got[1]), (label, cut))
                    if needs_end:                                    # only what is after its last block is gone
                        self.assertEqual(got[1], whole, (label, cut))
        self.assertLess(time.time() - started, 180)

    def test_bytes_changed_at_random_never_raise(self):
        rnd = random.Random(20261125)
        started = time.time()
        for label, data, _needs_end in self.samples():
            for _ in range(300):
                damaged = bytearray(data)
                for _ in range(rnd.randint(1, 4)):
                    damaged[rnd.randrange(len(damaged))] = rnd.randrange(256)
                got = layout_of(bytes(damaged))
                if got[1] is not None:
                    self.assertTrue(all(0 <= a < b <= len(damaged) for a, b in got[1]), label)
        self.assertLess(time.time() - started, 60)

    def test_a_flipped_crc_a_size_past_the_end_or_a_huge_count_is_not_this_kind(self):
        files = small_files()
        plain = sevenzip_bytes(files)
        _crc, off, _n, _hcrc = struct.unpack("<IQQI", plain[8:32])
        header = plain[32 + off:]

        def seven(h=header, offset=off):
            """The 7z with another header, every CRC right."""
            start = struct.pack("<QQI", offset, len(h), zlib.crc32(h))
            return plain[:8] + struct.pack("<I", zlib.crc32(start)) + start + plain[32:32 + off] + h

        rar5 = rar5_bytes(files)
        rar4 = rar4_bytes(files)
        body = vint(2) + vint(0x0002) + vint(1 << 62) + vint(0) + vint(1) + vint(0) + vint(0) + vint(0) + vint(1) + b"a"
        huge5 = struct.pack("<I", zlib.crc32(vint(len(body)) + body)) + vint(len(body)) + body   # its CRC right
        cases = [("a 7z start header CRC", plain[:9] + bytes([plain[9] ^ 1]) + plain[10:], "7z"),
                 ("a 7z header CRC", plain[:29] + bytes([plain[29] ^ 1]) + plain[30:], "7z"),
                 ("a 7z header past the end", seven(offset=1 << 40), "7z"),
                 ("7z packed streams past the header", seven(header[:3] + sz_number(1 << 40) + header[4:]), "7z"),
                 ("a 7z count of 2^60", seven(header[:3] + sz_number(0) + sz_number(1 << 60) + header[5:]), "7z"),
                 ("a RAR5 header CRC", rar5[:9] + bytes([rar5[9] ^ 1]) + rar5[10:], "rar"),
                 ("a RAR5 data size past the end", layouts.RAR5_MAGIC + rar5_block(1, vint(0)) + rar5_block(
                     2, vint(0) + vint(10) + vint(0) + vint(0) + vint(0) + vint(1) + b"a", b"x" * 10)[:-10]
                  + rar5_block(5, vint(0)), "rar"),
                 ("a RAR5 data size of 2^62", layouts.RAR5_MAGIC + rar5_block(1, vint(0)) + huge5 + bytes(8), "rar"),
                 ("a RAR4 HEAD_CRC", rar4[:7] + bytes([rar4[7] ^ 1]) + rar4[8:], "rar"),
                 ("a RAR4 PACK_SIZE past the end", layouts.RAR4_MAGIC + rar4_block(0x73, 0, bytes(6)) + rar4_block(
                     0x74, 0x8000, struct.pack("<IIBIIBBHI", 1 << 31, 1, 2, 0, 0, 20, 0x30, 1, 0x20) + b"a", b"x"),
                  "rar")]
        for label, data, kind in cases:
            started = time.time()
            got = layout_of(data)
            self.assertEqual((got[0], got[1]), (kind, None), label)
            self.assertLess(time.time() - started, 2, label)
        # a tar header's checksum flipped: no tar at all; a size past the end (its checksum right): not one it takes
        t = bytearray(tar_bytes(files))
        t[150] ^= 1
        self.assertEqual(layout_of(bytes(t)), ("", None, parts.WHY_NONE))
        t = bytearray(tar_bytes(files[:1]))
        t[124:136] = b"77777777777\0"
        t[148:156] = b" " * 8
        t[148:156] = b"%06o\0 " % sum(t[:512])
        self.assertEqual(layout_of(bytes(t)), ("tar", None, layouts.WHY_NOT_TAR))

    def test_the_fixtures_are_archives_not_git_lfs_pointers(self):
        # CI checks the repository out without Git LFS: .gitattributes keeps tests/data out of it, byte for byte
        for name in sorted(os.listdir(DATA)):
            with open(os.path.join(DATA, name), "rb") as f:
                if f.read(len(LFS_POINTER)) == LFS_POINTER:
                    self.fail("tests/data holds Git LFS pointers: check .gitattributes")
        lines = read(os.path.join(os.path.dirname(DATA), os.pardir, ".gitattributes")).splitlines()
        self.assertIn(b"tests/data/** -text -filter", [line.strip() for line in lines])

    def test_the_tar_walk_reads_each_header_and_skips_known_lengths(self):
        a, b = os.urandom(3000), os.urandom(1500)

        def offsets(data):
            """tarfile's own data areas of a tar."""
            with tarfile.open(fileobj=io.BytesIO(data)) as tf:
                return [(ti.offset_data, ti.offset_data + ti.size) for ti in tf.getmembers() if ti.isreg() and ti.size]

        def written(fmt, members, **kw):
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w", format=fmt, **kw) as tf:
                for name, data, kind, linkname in members:
                    ti = tarfile.TarInfo(name)
                    ti.size, ti.type, ti.linkname = len(data), kind, linkname
                    tf.addfile(ti, io.BytesIO(data) if data else None)
            return buf.getvalue()

        # git archive's tar: a pax global header first (the commit), never applied; GNU long names and long link
        # names ('L', 'K'), skipped and never decoded
        git = written(tarfile.PAX_FORMAT, [("a.bin", a, tarfile.REGTYPE, ""), ("dir/b.bin", b, tarfile.REGTYPE, "")],
                      pax_headers={"comment": "f" * 40})
        gnu = written(tarfile.GNU_FORMAT, [("x" * 300 + "/a.bin", a, tarfile.REGTYPE, ""),
                                           ("l", b"", tarfile.SYMTYPE, "y" * 300), ("b.bin", b, tarfile.REGTYPE, "")])
        self.assertEqual(git[156:157], tarfile.XGLTYPE)
        self.assertIn(tarfile.GNUTYPE_LONGLINK, gnu[156::512])
        # a pax 'x' header's size record is the next member's size, whatever its own header says; a directory and a
        # link take no data, whatever their size field says (as tarfile has it); a GNU base-256 size; a checksum
        # some old writers made of signed bytes
        x = pax_record(b"path", b"a.bin") + pax_record(b"size", b"%d" % len(a))
        crafted = tar_by_hand((tar_header(b"././@PaxHeader", len(x), b"x"), x), (tar_header(b"a.bin", 7), a),
                              (tar_header(b"d/", 4096, b"5"), b""), (tar_header(b"s", 4096, b"2"), b""),
                              (tar_header(b"\xe9t\xe9.bin", size_field=b"\x80" + len(b).to_bytes(11, "big"),
                                          signed=True), b))
        for data in (git, gnu, crafted):
            self.assertEqual(layout_of(data), ("tar", offsets(data), ""))
            self.assertEqual([data[s:e] for s, e in layout_of(data)[1]], [a, b])
        # no index: a size in a global header (never applied), a GNU sparse member, a volume label, a member continued
        # from the volume before, a type no tar has, a header whose checksum does not match
        g = pax_record(b"size", b"10")
        bad = bytearray(tar_header(b"b.bin", len(b)))
        bad[0] ^= 1
        for members, why in ((((tar_header(b"pax_global_header", len(g), b"g"), g), (tar_header(b"a.bin", len(a)), a)),
                              layouts.WHY_NOT_TAR),
                             (((tar_header(b"a.bin", len(a)), a), (tar_header(b"s.bin", len(b), b"S"), b)),
                              layouts.WHY_SPARSE),
                             (((tar_header(b"label", 0, b"V"), b""), (tar_header(b"a.bin", len(a)), a)),
                              layouts.WHY_NOT_TAR),
                             (((tar_header(b"a.bin", len(a)), a), (tar_header(b"b.bin", len(b), b"M"), b)),
                              layouts.WHY_NOT_TAR),
                             (((tar_header(b"a.bin", len(a)), a), (tar_header(b"b.bin", len(b), b"D"), b)),
                              layouts.WHY_NOT_TAR),
                             (((tar_header(b"a.bin", len(a)), a), (bytes(bad), b)), layouts.WHY_NOT_TAR)):
            self.assertEqual(layout_of(tar_by_hand(*members)), ("tar", None, why), members[-1][0][:8])
        # at most `most` headers; an extension block at most EXT_MAX bytes
        dirs = tar_by_hand(*[(tar_header(b"d%d/" % i, 0, b"5"), b"") for i in range(4)])
        self.assertEqual(layouts.tar_layout(io.BytesIO(dirs), len(dirs), 4), ([], ""))
        self.assertEqual(layouts.tar_layout(io.BytesIO(dirs), len(dirs), 3), (None, layouts.WHY_PIECES))
        for kind in (b"L", b"K", b"x", b"g"):
            for n, why in ((layouts.EXT_MAX, ""), (layouts.EXT_MAX + 1, layouts.WHY_LONG_HEADER)):
                ext = pax_record(b"comment", b"c" * (n - 15)) if kind in b"xg" else b"n" * n
                self.assertEqual(len(ext), n)
                data = tar_by_hand((tar_header(b"././@LongLink", n, kind), ext), (tar_header(b"a.bin", len(a)), a))
                self.assertEqual(layouts.tar_layout(io.BytesIO(data), len(data), 9)[1], why, (kind, n))

    def test_crafted_tars_cost_one_walk_not_minutes(self):
        # tars that cost Python's tarfile minutes or memory: a global pax header of many keys before many members
        # (it applies every key to every member), pax headers of digits alone (its regular expression goes back over
        # them), a GNU long name as long as the file (it reads it whole)
        members = b"".join(tar_header(b"m%07d" % i) for i in range(4000))
        cases = []
        for keys, want in ((layouts.PAX_RECORDS_MAX, ([], "")), (4000, (None, layouts.WHY_LONG_HEADER)),
                           (40000, (None, layouts.WHY_LONG_HEADER))):                   # 3 KB, 52 KB, 560 KB
            records = b"".join(pax_record(b"k%07d" % i, b"") for i in range(keys))
            cases.append(("%d global keys, 4000 members" % keys,
                          tar_header(b"pax_global_header", len(records), b"g") + pad(records) + members + bytes(1024),
                          want))
        for n in (20000, 40000, 80000):
            cases.append(("a pax header of %d digits" % n,
                          tar_header(b"././@PaxHeader", n, b"x") + pad(b"1" * n) + tar_header(b"a") + bytes(1024),
                          (None, layouts.WHY_NOT_TAR if n <= layouts.EXT_MAX else layouts.WHY_LONG_HEADER)))
        for label, data, want in cases:
            started = time.time()
            self.assertEqual(layouts.tar_layout(io.BytesIO(data), len(data), parts.SEGMENTS_MAX), want, label)
            self.assertLess(time.time() - started, 1, label)
        # a GNU long name of 200 MiB: refused from its header alone, none of it read (the file here is that header)
        f = io.BytesIO(tar_header(b"././@LongLink", 200 << 20, b"L"))
        self.assertEqual(layouts.tar_layout(f, 512 + (200 << 20) + 1536, parts.SEGMENTS_MAX),
                         (None, layouts.WHY_LONG_HEADER))
        self.assertEqual(f.tell(), 512)

    def test_a_pax_header_of_more_than_pax_records_max_records_is_refused_at_once(self):
        a = os.urandom(3000)
        # 64 KiB of the smallest records (5 bytes each), as a global header 256 times over: refused at the first
        # header, once PAX_RECORDS_MAX of its records are read
        flood = b"5 a=\n" * (layouts.EXT_MAX // 5)
        data = (tar_header(b"pax_global_header", len(flood), b"g") + pad(flood)) * 256 + bytes(1024)
        f = io.BytesIO(data)
        started = time.time()
        self.assertEqual(layouts.tar_layout(f, len(data), parts.SEGMENTS_MAX), (None, layouts.WHY_LONG_HEADER))
        self.assertLess(time.time() - started, 0.5)
        self.assertEqual(f.tell(), 512 + len(flood))
        # PAX_RECORDS_MAX records are read, one more is refused: in a per-file header and in a global one
        for kind in (b"x", b"g"):
            for n, why in ((layouts.PAX_RECORDS_MAX, ""), (layouts.PAX_RECORDS_MAX + 1, layouts.WHY_LONG_HEADER)):
                records = b"".join(pax_record(b"k%03d" % i, b"v") for i in range(n))
                data = tar_by_hand((tar_header(b"././@PaxHeader", len(records), kind), records),
                                   (tar_header(b"a.bin", len(a)), a))
                got_kind, ranges, got = layout_of(data)
                self.assertEqual((got_kind, got), ("tar", why), (kind, n))
                self.assertEqual(None if ranges is None else [data[s:e] for s, e in ranges], None if why else [a])
        # git archive's global header (one record: its commit) still indexes
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT, pax_headers={"comment": "f" * 40}) as tf:
            ti = tarfile.TarInfo("a.bin")
            ti.size = len(a)
            tf.addfile(ti, io.BytesIO(a))
        git = buf.getvalue()
        _kind, ranges, why = layout_of(git)
        self.assertEqual((git[156:157], why, [git[s:e] for s, e in ranges]), (tarfile.XGLTYPE, "", [a]))
        self.assertEqual(parts.reason(layouts.WHY_LONG_HEADER),
                         "a header of it is longer than 64 KiB or holds more than 256 pax records")

    def test_a_tar_holds_at_most_pax_records_total_pax_records(self):
        # global headers of PAX_RECORDS_MAX records of 5 bytes (2 KiB each), 8,192 of them: 2,097,152 records, read
        small = b"5 a=\n" * layouts.PAX_RECORDS_MAX
        block = tar_header(b"pax_global_header", len(small), b"g") + pad(small)
        data = block * 8192 + bytes(1024)
        self.assertEqual(layouts.tar_layout(io.BytesIO(data), len(data), parts.SEGMENTS_MAX), ([], ""))
        # PAX_RECORDS_TOTAL of them (15,625 such headers), then one more: refused at that one, nothing after it read
        full = layouts.PAX_RECORDS_TOTAL // layouts.PAX_RECORDS_MAX
        one = b"5 a=\n"
        data = block * full + tar_header(b"pax_global_header", len(one), b"g") + pad(one) + block * 64 + bytes(1024)
        f = io.BytesIO(data)
        started = time.time()
        self.assertEqual(layouts.tar_layout(f, len(data), parts.SEGMENTS_MAX), (None, layouts.WHY_PAX_RECORDS))
        self.assertLess(time.time() - started, 60)
        self.assertEqual(f.tell(), len(block) * full + 512 + len(one))
        # a real pax tar (tarfile: a pax header of a few records for each file) still indexes
        files = [("d/f%04d.bin" % i, os.urandom(1500)) for i in range(500)]
        real = tar_bytes(files, tarfile.PAX_FORMAT, {"mtime": "1793000000.123456789", "atime": "1793000000.5",
                                                     "ctime": "1793000000.5"})
        kind, ranges, why = layout_of(real)
        self.assertEqual((kind, why, [real[a:b] for a, b in ranges]), ("tar", "", [d for _n, d in files]))
        self.assertEqual(parts.reason(layouts.WHY_PAX_RECORDS), "it holds more than 4,000,000 pax records")

    def test_every_reader_stops_when_asked(self):
        # publish's stop and the update's: every reader looks at its cancel event every CANCEL_EVERY headers (8 here)
        stop = threading.Event()
        stop.set()
        files = [("f%02d.bin" % i, os.urandom(1100)) for i in range(20)]
        folder = tempfile.mkdtemp(prefix="dp-layouts-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        path = os.path.join(folder, "a.arc")
        with mock.patch.object(layouts, "CANCEL_EVERY", 8), mock.patch.object(parts, "PARTS_MIN", 1024):
            for label, _kind, make in MAKERS:
                data = make(files)
                write(path, data)
                self.assertEqual(len(parts.find_layout(io.BytesIO(data), len(data), threading.Event())[1]), 20, label)
                with self.assertRaises(layouts.Stopped, msg=label):
                    parts.find_layout(io.BytesIO(data), len(data), stop)
                with self.assertRaises(parts.Stopped, msg=label):
                    parts.make_index(path, hashlib.sha256(data).hexdigest(), len(data), stop)
                with self.assertRaises(rebuild.SourceError, msg=label):
                    rebuild.Members(os.path.join(folder, "members.json")).members("a.arc", path, stop)

    def test_a_7z_header_is_counted_and_bounded_before_it_is_built_or_decoded(self):
        n = sz_number

        def seven(header, packed=b""):
            """A 7z of `packed` (its packed streams) and `header`, every CRC right."""
            start = struct.pack("<QQI", len(packed), len(header), zlib.crc32(header))
            return layouts.SEVENZIP_MAGIC + b"\x00\x04" + struct.pack("<I", zlib.crc32(start)) + start + packed + header

        def encoded(size, packed, prop=8):
            """A 7z whose header is `packed` with LZMA2 (its dictionary property `prop`) and says it decodes to
            `size` bytes."""
            enc = b"\x17\x06" + n(0) + n(1) + b"\x09" + n(len(packed)) + b"\x00"
            enc += b"\x07\x0b" + n(1) + b"\x00" + n(1) + b"\x21\x21" + n(1) + bytes([prop]) + b"\x0c" + n(size)
            enc += b"\x0a\x01" + struct.pack("<I", zlib.crc32(smallest)) + b"\x00\x00"
            return seven(enc, packed)

        never_built = mock.patch.object(layouts, "_folder", side_effect=AssertionError("a folder was built"))
        # an encoded header of 200,000 folders, where one may be: refused on its count
        many = 200000
        probe = seven(b"\x17\x06" + n(0) + n(1) + b"\x09" + n(4096) + b"\x00\x07\x0b" + n(many) + b"\x00"
                      + b"\x01\x00" * many + b"\x0c" + b"\x01" * many + b"\x00", bytes(4096))
        # a main header of more folders than packed streams (none here), or of more packed streams than an index
        # holds (`most`: 2 here)
        folders = seven(b"\x01\x04\x07\x0b" + n(3) + b"\x00" + b"\x01\x00" * 3 + b"\x0c" + b"\x01" * 3 + b"\x00" * 3)
        streams = sevenzip_bytes(small_files())
        with never_built:
            started = time.time()
            self.assertEqual(layout_of(probe), ("7z", None, layouts.WHY_NOT_7Z))
            self.assertLess(time.time() - started, 1)
            self.assertEqual(layouts.sevenzip_layout(io.BytesIO(folders), len(folders), 2),
                             (None, layouts.WHY_NOT_7Z))
        # and an encoded header of 100,000 packed streams, where one may be: refused on its count, no size read
        packs = seven(b"\x17\x06" + n(0) + n(100000) + b"\x09" + b"\x01" * 100000 + b"\x00\x07\x0b\x01\x00\x01\x00"
                      + b"\x0c\x01\x00", bytes(100000))
        with mock.patch.object(layouts, "_stop", wraps=layouts._stop) as looked:
            self.assertEqual(layout_of(packs), ("7z", None, layouts.WHY_NOT_7Z))
        self.assertLess(looked.call_count, 10)
        self.assertEqual(layouts.sevenzip_layout(io.BytesIO(streams), len(streams), 2), (None, layouts.WHY_PIECES))
        with mock.patch.object(parts, "SEGMENTS_MAX", 2):
            self.assertEqual(layout_of(streams), ("7z", None, layouts.WHY_PIECES))
        self.assertEqual(layout_of(streams)[2], "")
        # an encoded header is decoded a CHUNK at a time up to its stated size, and refused before any of it is
        # decoded when it says it decodes to more than HEADER_MAX, or HEADER_RATIO times its packed size; its
        # dictionary is never larger than that size (property 40: 4 GiB)
        smallest = b"\x01\x00"                                           # kHeader, kEnd
        packed = lzma.compress(smallest, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "preset": 1}])
        self.assertEqual(layout_of(encoded(len(smallest), packed)), ("7z", [], ""))
        self.assertEqual(layout_of(encoded(len(smallest), packed, prop=40)), ("7z", [], ""))
        self.assertEqual(layout_of(encoded(len(smallest) - 1, packed)), ("7z", None, layouts.WHY_NOT_7Z))
        for data in (sevenzip_bytes(small_files(), encoded="lzma"), sevenzip_bytes(small_files(), encoded="lzma2")):
            whole = layout_of(data)
            with mock.patch.object(layouts, "CHUNK", 16):
                self.assertEqual(layout_of(data), whole)
        zeros = bytes(16 << 20)                                          # packed to a few KiB
        bomb = lzma.compress(zeros, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "preset": 1}])
        with mock.patch.object(lzma, "LZMADecompressor", side_effect=AssertionError("it was decoded")):
            for data in (encoded(layouts.HEADER_RATIO * len(packed) + 1, packed),
                         encoded(layouts.HEADER_MAX + 1, os.urandom(300 << 10)), encoded(len(zeros), bomb)):
                self.assertEqual(layout_of(data), ("7z", None, layouts.WHY_NOT_7Z))

    def test_a_7z_header_walk_is_tied_to_its_file(self):
        n = sz_number

        def read(data, most=parts.SEGMENTS_MAX):
            return layouts.sevenzip_layout(io.BytesIO(data), len(data), most)

        # at most ATTRIBUTES_MAX attributes in a property list: kArchiveProperties of 16 is read, of 17 refused
        for k, want in ((layouts.ATTRIBUTES_MAX, ([], "")), (layouts.ATTRIBUTES_MAX + 1, (None, layouts.WHY_NOT_7Z))):
            self.assertEqual(read(seven_bytes(b"\x01\x02" + b"\x01\x00" * k + b"\x00\x00")), want, k)
        # a folder takes at most FOLDER_STREAMS_MAX streams in and as many out: so at most 63 bind pairs
        for pairs, ok in ((((64, 64),), True), (((32, 1), (32, 1)), True), (((65, 1),), False),
                          (((33, 1), (32, 1)), False), (((2, 65),), False)):
            ins, outs = sum(p[0] for p in pairs), sum(p[1] for p in pairs)
            folder = n(len(pairs)) + b"".join(b"\x10" + n(ci) + n(co) for ci, co in pairs) + n(0) * (2 * (outs - 1))
            folder += n(0) * (ins - outs + 1) if ins - outs + 1 > 1 else b""
            b = layouts._Bytes(folder, 1 << 20, None)
            if ok:
                self.assertEqual(layouts._folder(b)[1], outs, pairs)
            else:
                self.assertRaises(layouts._Bad, layouts._folder, b)

        # no more packed streams than bytes before the header; no more folders than packed streams
        def streams(k, folders, area):
            h = b"\x01\x04\x06" + n(0) + n(k) + b"\x09" + b"\x01" * k + b"\x00"
            if folders:
                h += b"\x07\x0b" + n(folders) + b"\x00" + b"\x01\x01\x00" * folders + b"\x0c" + b"\x01" * folders
                h += b"\x00"
            return seven_bytes(h + b"\x00\x00", bytes(area))

        self.assertEqual(len(read(streams(4, 0, 4))[0]), 4)
        self.assertEqual(read(streams(5, 0, 4)), (None, layouts.WHY_NOT_7Z))
        for k, want in ((4, ([], "")), (5, (None, layouts.WHY_NOT_7Z))):         # empty ones too
            self.assertEqual(read(seven_bytes(b"\x01\x04\x06" + n(0) + n(k) + b"\x09" + bytes(k) + b"\x00\x00\x00",
                                              bytes(4))), want, k)
        self.assertEqual(len(read(streams(2, 2, 2))[0]), 2)
        self.assertEqual(read(streams(2, 3, 3)), (None, layouts.WHY_NOT_7Z))
        # at most READS_BASE numbers and one more for every READS_BYTES bytes of the file: 10,000 one-byte streams in
        # as many folders are read (about 40,000 numbers), 100,000 are not (400,000 numbers for a file of 600 KB)
        self.assertEqual(read(streams(10000, 10000, 10000))[1], "")
        self.assertEqual(read(streams(100000, 100000, 100000)), (None, layouts.WHY_NOT_7Z))
        # the stop is looked at as the header is read: set once it is in hand, it is seen within 8 numbers (a header
        # that, read on, ends in an attribute larger than itself)
        stop = threading.Event()
        real = layouts._header_start

        def read_then_stop(*args):
            got = real(*args)
            stop.set()
            return got

        cut = seven_bytes(b"\x01\x04\x06" + n(0) + n(50000) + b"\x09" + b"\x01" * 50000 + b"\x19" + n(1 << 40),
                          bytes(50000))
        self.assertEqual(read(cut), (None, layouts.WHY_NOT_7Z))
        with mock.patch.object(layouts, "_header_start", side_effect=read_then_stop), \
                mock.patch.object(layouts, "CANCEL_EVERY", 8):
            self.assertRaises(layouts.Stopped, layouts.sevenzip_layout, io.BytesIO(cut), len(cut),
                              parts.SEGMENTS_MAX, stop)
        # headers made to cost their reader: attribute pairs up to HEADER_KEEP (plain, a 32 MiB file, and packed into
        # 33 KB); 1,000,000 empty packed streams and as many folders; folders of 64 coders of 64 streams in and out
        k = parts.SEGMENTS_MAX
        empties = (b"\x01\x04\x06" + n(0) + n(k) + b"\x09" + b"\x00" * k + b"\x00\x07\x0b" + n(k) + b"\x00"
                   + b"\x01\x00" * k + b"\x0c" + b"\x00" * k + b"\x00\x00")
        folder = n(64) + (b"\x10" + n(64) + n(64)) * 64 + b"\x00" * (2 * (64 * 64 - 1))
        k = (layouts.HEADER_KEEP - 64) // (len(folder) + 64 * 64)
        binds = b"\x01\x04\x07\x0b" + n(k) + b"\x00" + folder * k + b"\x0c" + b"\x00" * (64 * 64 * k) + b"\x00\x00"
        for label, data in (("attribute pairs", seven_bytes(attribute_pairs())), ("packed", crafted_7z()),
                            ("empty streams and folders", encoded_7z(empties)), ("bind pairs", encoded_7z(binds))):
            started = time.time()
            self.assertEqual(layout_of(data), ("7z", None, layouts.WHY_NOT_7Z), label)
            self.assertLess(time.time() - started, 1, label)
        # and a stop asked for before it is read is seen at once
        stop = threading.Event()
        stop.set()
        started = time.time()
        self.assertRaises(layouts.Stopped, parts.find_layout, io.BytesIO(crafted_7z()), len(crafted_7z()), stop)
        self.assertLess(time.time() - started, 0.5)

    def test_a_first_tar_header_of_a_type_this_reader_does_not_know_is_a_tar_it_does_not_know(self):
        a = os.urandom(3000)
        # GNU incremental 'D' and 'N', Solaris 'X', 'A' and 'E', star's 'I': with GNU's magic or POSIX's
        for magic in (b"ustar  \0", b"ustar\x0000"):
            for kind in (b"D", b"N", b"X", b"A", b"E", b"I"):
                data = tar_by_hand((tar_header(b"dir/", 0, kind, magic=magic), b""), (tar_header(b"a.bin", len(a)), a))
                self.assertEqual(layout_of(data), ("tar", None, layouts.WHY_NOT_TAR), (magic, kind))
        # with no magic, such a header is no tar's: no archive at all
        data = tar_by_hand((tar_header(b"dir/", 0, b"D", magic=bytes(8)), b""), (tar_header(b"a.bin", len(a)), a))
        self.assertEqual(layout_of(data), ("", None, parts.WHY_NONE))
        folder = tempfile.mkdtemp(prefix="dp-layouts-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        data = tar_by_hand((tar_header(b"dir/", 0, b"D", magic=b"ustar  \0"), b""), (tar_header(b"a.bin", len(a)), a))
        write(os.path.join(folder, "inc.tar"), data)
        with mock.patch.object(parts, "PARTS_MIN", 1024):
            self.assertEqual(parts.make_index(os.path.join(folder, "inc.tar"), hashlib.sha256(data).hexdigest(),
                                              len(data)), (None, layouts.WHY_NOT_TAR))
        self.assertEqual(parts.reason(layouts.WHY_NOT_TAR), "not a tar this reader knows")

    def test_a_members_list_from_other_readers_is_read_again(self):
        folder = tempfile.mkdtemp(prefix="dp-layouts-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        path = os.path.join(folder, "members.json")
        entry = {"size": 1, "mtime_ns": 1, "members": []}
        for doc in ({"format": rebuild.MEMBERS_FORMAT, "files": {"a": entry}},
                    {"format": rebuild.MEMBERS_FORMAT, "readers": parts.READERS - 1, "files": {"a": entry}}):
            write(path, json.dumps(doc).encode("utf-8"))
            self.assertEqual(rebuild.Members(path).files, {}, doc)
        write(path, json.dumps({"format": rebuild.MEMBERS_FORMAT, "readers": parts.READERS,
                                "files": {"a": entry}}).encode("utf-8"))
        members = rebuild.Members(path)
        self.assertEqual(members.files, {"a": entry})
        members.dirty = True
        members.save({"a"})
        self.assertEqual(json.loads(read(path))["readers"], parts.READERS)

    def test_a_members_list_never_keeps_a_file_this_python_could_not_read(self):
        folder = tempfile.mkdtemp(prefix="dp-layouts-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        path = os.path.join(folder, "maps.7z")
        write(path, sevenzip_bytes(medium_files(), encoded="lzma"))
        members = rebuild.Members(os.path.join(folder, "members.json"))
        with mock.patch.object(layouts, "lzma", None):
            self.assertEqual(members.members("maps.7z", path)["members"], [])
        self.assertNotIn("maps.7z", members.files)                       # asked again next time
        self.assertEqual(len(members.members("maps.7z", path)["members"]), 2)
        self.assertIn("maps.7z", members.files)

    def test_without_lzma_an_encoded_7z_header_gets_its_reason(self):
        files = small_files()
        packed, plain = sevenzip_bytes(files, encoded="lzma"), sevenzip_bytes(files)
        with mock.patch.object(layouts, "lzma", None):
            self.assertEqual(layout_of(packed), ("7z", None, layouts.WHY_NO_LZMA))
            self.assertEqual((layout_of(plain)[::2], len(layout_of(plain)[1])), (("7z", ""), 3))
        self.assertEqual(parts.reason(layouts.WHY_NO_LZMA), "this Python cannot read the 7z's packed header (no lzma)")
        # a Python built without lzma: the engine imports and runs, and says why of an encoded header
        folder = tempfile.mkdtemp(prefix="dp-layouts-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        write(os.path.join(folder, "packed.7z"), packed)
        write(os.path.join(folder, "plain.7z"), plain)
        code = ("import io, sys\n"
                "sys.modules['lzma'] = None\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "from deltapatcher import cli, client, layouts, parts, publish, rebuild\n"
                "for path in sys.argv[2:]:\n"
                "    with open(path, 'rb') as f:\n"
                "        data = f.read()\n"
                "    kind, ranges, why = parts.find_layout(io.BytesIO(data), len(data))\n"
                "    print(kind, None if ranges is None else len(ranges), why or '-')\n")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run([sys.executable, "-c", code, root, os.path.join(folder, "packed.7z"),
                            os.path.join(folder, "plain.7z")], cwd=folder, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=120)
        self.assertEqual((r.returncode, r.stdout.decode("ascii").split()),
                         (0, ["7z", "None", "no-lzma", "7z", "3", "-"]), r.stderr.decode("utf-8", "replace"))

    def test_a_file_that_begins_with_a_zero_block_is_no_tar(self):
        # an ISO 9660 image (its first 32 KiB zero), a file of zeros, one too short for a header: nothing to say
        iso = bytes(32768) + b"\x01CD001\x01" + os.urandom(20000)
        for data in (iso, bytes(20000), bytes(511), b""):
            self.assertEqual(layout_of(data), ("", None, parts.WHY_NONE), len(data))
        # a zip with zeros before its first member (zipfile reads it): the zip's own layout
        files = small_files()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            for name, data in files:
                zf.writestr(name, data)
        stub = bytes(1024) + buf.getvalue()
        kind, ranges, why = layout_of(stub)
        self.assertEqual((kind, why, [stub[a:b] for a, b in ranges]), ("zip", "", [d for _n, d in files]))
        self.assertEqual(ranges, [(a, b) for _off, a, b in parts.zip_layout(io.BytesIO(stub), len(stub))[0]])

    def test_a_7z_of_one_folder_is_solid_whatever_its_streams_and_one_of_one_file_says_so(self):
        files = small_files()
        bcj2, one = sevenzip_bytes(files, bcj2=True), sevenzip_bytes(files[:1])
        for data, streams, why in ((bcj2, 4, layouts.WHY_SOLID),
                                   (sevenzip_bytes(files, encoded="lzma", bcj2=True), 4, layouts.WHY_SOLID),
                                   (sevenzip_bytes(files, solid=True), 1, layouts.WHY_SOLID),
                                   (one, 1, layouts.WHY_ONE_FILE),
                                   (sevenzip_bytes(files[:1], solid=True), 1, layouts.WHY_ONE_FILE),
                                   (sevenzip_bytes(files[:1], encoded="lzma2"), 1, layouts.WHY_ONE_FILE),
                                   (sevenzip_bytes(files), 3, "")):
            kind, ranges, got = layout_of(data)
            self.assertEqual((kind, len(ranges), got), ("7z", streams, why))
        self.assertEqual(b"".join(bcj2[a:b] for a, b in layout_of(bcj2)[1]), b"".join(d for _n, d in files))
        folder = tempfile.mkdtemp(prefix="dp-layouts-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        with mock.patch.object(parts, "PARTS_MIN", 1024):
            for name, data, why in (("bcj2.7z", bcj2, layouts.WHY_SOLID), ("one.7z", one, layouts.WHY_ONE_FILE)):
                write(os.path.join(folder, name), data)
                self.assertEqual(parts.make_index(os.path.join(folder, name), hashlib.sha256(data).hexdigest(),
                                                  len(data)), (None, why))
        self.assertIn("(-ms=off)", parts.reason(layouts.WHY_SOLID))
        self.assertEqual(parts.reason(layouts.WHY_ONE_FILE), "it holds one file")


# ---- updates and publishing ----------------------------------------------------------------------------------------

class Updates(Base):
    def test_every_kind_rebuilds_with_only_the_changed_file_fetched(self):
        names = {label: "data/%d.arc" % i for i, (label, _kind, _make) in enumerate(MAKERS)}
        files = {label: big_files() for label, _kind, _make in MAKERS}
        for label, _kind, make in MAKERS:
            self.put(names[label], make(files[label]))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        new, fresh = {}, {}
        for label, _kind, make in MAKERS:
            fresh[label] = os.urandom(TEXTURE)
            new[label] = make([(n, fresh[label] if n == "textures/t05.tga" else d) for n, d in files[label]])
            self.put(names[label], new[label])
        self.release("2")
        man = self.manifest()
        for label, kind, _make in MAKERS:                                 # the index says the archive's kind
            index = read(os.path.join(self.store, "parts", man.files[names[label]].parts))
            self.assertEqual(json.loads(zlib.decompress(index))["kind"], kind, label)
        del self.served.answers[:]
        plan = client.update(url, self.game)
        self.assertEqual(len(plan.rebuilds), len(MAKERS))
        asked = [(a[0].rsplit("/", 1)[-1], a[1], a[2], a[3]) for a in self.served.of("blobs")]
        self.assertEqual(len(asked), len(MAKERS))                         # one range each
        for label, _kind, _make in MAKERS:
            self.assertEqual(self.installed(names[label]), new[label], label)   # byte for byte
            start = new[label].find(fresh[label])
            self.assertIn((man.files[names[label]].sha256, "bytes=%d-%d" % (start, start + TEXTURE - 1), 206, TEXTURE),
                          asked, label)
        whole = sum(len(d) for d in new.values())
        self.assertEqual(plan.fetch_bytes(), len(MAKERS) * TEXTURE)          # 9 x 700 KiB of about 9 x 9.3 MB
        self.assertLess(plan.fetch_bytes() * 13, whole)

    def test_a_piece_is_found_in_an_archive_of_another_kind(self):
        # release 1 has a tar; release 2 also has a 7z holding the same files but one: the tar gives them
        files = big_files()
        self.put("data/maps.tar", tar_bytes(files))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        fresh = os.urandom(TEXTURE)
        new = sevenzip_bytes([(n, fresh if n == "textures/t02.tga" else d) for n, d in files], encoded="lzma")
        self.put("data/maps.7z", new)
        self.release("2")
        del self.served.answers[:]
        plan = client.update(url, self.game)
        self.assertEqual(self.installed("data/maps.7z"), new)
        self.assertEqual((len(plan.rebuilds), plan.fetch_bytes()), (1, TEXTURE))
        self.assertEqual([(a[2], a[3]) for a in self.served.of("blobs")], [(206, TEXTURE)])

    def test_real_7zip_and_rar_archives_rebuild(self):
        # tiny archives (5 KB): with PARTS_MIN this low, they get an index; b.bin changed between v1 and v2
        with mock.patch.object(parts, "PARTS_MIN", 1024):
            for i, (one, two) in enumerate((("7z-files-v1.7z", "7z-files-v2.7z"),
                                            ("rar5-files-v1.rar", "rar5-files-v2.rar"))):
                self.put("data/%d.arc" % i, fixture(one))
            self.release("1")
            url = self.serve()
            client.update(url, self.game)
            for i, (one, two) in enumerate((("7z-files-v1.7z", "7z-files-v2.7z"),
                                            ("rar5-files-v1.rar", "rar5-files-v2.rar"))):
                self.put("data/%d.arc" % i, fixture(two))
            self.release("2")
            del self.served.answers[:]
            plan = client.update(url, self.game)
        self.assertEqual((self.installed("data/0.arc"), self.installed("data/1.arc")),
                         (fixture("7z-files-v2.7z"), fixture("rar5-files-v2.rar")))
        self.assertEqual(len(plan.rebuilds), 2)
        self.assertEqual(sorted(a[3] for a in self.served.of("blobs")), [1600, 1604])   # b.bin's packed data, each

    def test_two_crafted_7z_files_installed_cost_an_update_no_time(self):
        # each a 33 KB 7z whose header decodes to 32 MiB of attribute pairs: read among the files the changed
        # archive's pieces are looked for in
        files = big_files()
        self.put("data/maps.tar", tar_bytes(files))
        for i in range(2):
            self.put("maps/extra%d.dat" % i, crafted_7z())
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        fresh = os.urandom(TEXTURE)
        new = tar_bytes([(name, fresh if name == "textures/t04.tga" else d) for name, d in files])
        self.put("data/maps.tar", new)
        self.release("2")
        started = time.time()
        plan = client.update(url, self.game)
        self.assertLess(time.time() - started, 15)
        self.assertEqual((len(plan.rebuilds), plan.fetch_bytes()), (1, TEXTURE))
        self.assertEqual(self.installed("data/maps.tar"), new)

    def test_a_members_list_from_before_every_kind_is_read_again(self):
        # members.json of format 1 knew only zips: a tar it listed as no archive is read again
        files = big_files()
        self.put("data/maps.tar", tar_bytes(files))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        st = os.stat(os.path.join(self.game, "data", "maps.tar"))
        write(os.path.join(self.game, ".deltapatcher", rebuild.MEMBERS_NAME), json.dumps(
            {"format": 1, "files": {"data/maps.tar": {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
                                                       "members": []}}}).encode("utf-8"))
        fresh = os.urandom(TEXTURE)
        new = tar_bytes([(n, fresh if n == "textures/t09.tga" else d) for n, d in files])
        self.put("data/maps.tar", new)
        self.release("2")
        plan = client.update(url, self.game)
        self.assertEqual((len(plan.rebuilds), plan.fetch_bytes()), (1, TEXTURE))
        self.assertEqual(self.installed("data/maps.tar"), new)


class Publishing(Base):
    def test_which_archives_get_an_index_and_what_is_said_of_the_others(self):
        files = medium_files()
        sparse = tar_bytes(files[:1], tarfile.PAX_FORMAT, {"GNU.sparse.numblocks": "1", "GNU.sparse.offset": "0",
                                                           "GNU.sparse.numbytes": "20000", "GNU.sparse.size": "20000"})
        archives = {"a.tar": tar_bytes(files), "b.7z": sevenzip_bytes(files, encoded="lzma"),
                    "c.rar": rar5_bytes(files),
                    "d.rar": rar4_bytes(files), "e.7z": sevenzip_bytes(files, solid=True),
                    "f.7z": sevenzip_bytes(files, aes=True), "g.rar": rar5_bytes(files, encrypted=True),
                    "h.tar": sparse, "i.7z": layouts.SEVENZIP_MAGIC + os.urandom(9000),
                    "j.tar.gz": b"\x1f\x8b\x08" + os.urandom(9000), "k.bin": os.urandom(9000)}
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            for name, data in files:
                zf.writestr(name, data)
        archives.update({"l.iso": bytes(32768) + b"\x01CD001\x01" + os.urandom(9000),
                         "m.zip": bytes(1024) + buf.getvalue(), "n.7z": sevenzip_bytes(files[:1]),
                         "o.7z": sevenzip_bytes(files, bcj2=True)})
        for name, data in archives.items():
            self.put("data/" + name, data)
        said = []
        with mock.patch.object(parts, "PARTS_MIN", 1024):
            res = self.release("1", log=said.append)
        man = self.manifest()
        self.assertEqual(sorted(p for p, e in man.files.items() if e.parts),
                         ["data/a.tar", "data/b.7z", "data/c.rar", "data/d.rar", "data/m.zip"])
        size = {n: publish.human(len(d)) for n, d in archives.items()}
        self.assertEqual(res.warnings, [
            "Warning: data/e.7z (%s) is sent whole whenever it changes: packed as one solid stream: with 7-Zip, pack "
            "it with solid mode off (-ms=off) so only the files that changed travel." % size["e.7z"],
            "Warning: data/f.7z (%s) is sent whole whenever it changes: its headers are encrypted." % size["f.7z"],
            "Warning: data/g.rar (%s) is sent whole whenever it changes: its headers are encrypted." % size["g.rar"],
            "Warning: data/h.tar (%s) is sent whole whenever it changes: it holds a sparse file." % size["h.tar"],
            "Warning: data/i.7z (%s) is sent whole whenever it changes: not a 7z this reader knows." % size["i.7z"],
            "Warning: data/j.tar.gz (%s) is sent whole whenever it changes: pack it as .zip or .pk3 so only the files "
            "that changed inside it travel." % size["j.tar.gz"],
            "Warning: data/n.7z (%s) is sent whole whenever it changes: it holds one file." % size["n.7z"],
            "Warning: data/o.7z (%s) is sent whole whenever it changes: packed as one solid stream: with 7-Zip, pack "
            "it with solid mode off (-ms=off) so only the files that changed travel." % size["o.7z"]])
        self.assertEqual(res.solid, [])

    def test_no_lzma_is_never_kept_in_a_stores_cache(self):
        # this Python's, not the archive's: the next publish, with lzma, makes the index
        data = sevenzip_bytes(medium_files(), encoded="lzma")
        self.put("data/maps.7z", data)
        with mock.patch.object(parts, "PARTS_MIN", 1024):
            with mock.patch.object(layouts, "lzma", None):
                res = self.release("1")
            self.assertEqual(res.warnings, ["Warning: data/maps.7z (%s) is sent whole whenever it changes: this Python "
                                            "cannot read the 7z's packed header (no lzma)." % publish.human(len(data))])
            self.assertFalse(self.manifest().files["data/maps.7z"].parts)
            res = self.release("2")
        self.assertEqual(res.warnings, [])
        self.assertTrue(self.manifest().files["data/maps.7z"].parts)

    def test_a_solid_rar_gets_an_index_and_one_plain_line(self):
        files = big_files()
        self.put("data/maps.rar", rar5_bytes(files, solid=True))
        said = []
        res = self.release("1", log=said.append)
        self.assertTrue(self.manifest().files["data/maps.rar"].parts)
        self.assertEqual((res.warnings, res.solid), ([], ["data/maps.rar"]))
        self.assertIn("data/maps.rar is a solid RAR: players reuse only its files before the first one that changed "
                      "(packed with solid mode off, rar -s-, every file that did not change is reused)", said)
        self.assertTrue(res.summary().endswith("; 1 with a parts index (1 of them a solid RAR, reused only up to its "
                                               "first changed file)"))

    def test_a_store_cache_of_other_rules_is_redone(self):
        # a store's parts cache made before tar, 7z and RAR had readers, or by readers of another version: every big
        # blob is read again once
        files = big_files()
        self.put("data/maps.rar", rar5_bytes(files))
        self.release("1")
        cache = os.path.join(self.store, parts.CACHE_NAME)
        doc = json.loads(read(cache))
        sha = self.manifest().files["data/maps.rar"].sha256
        self.assertEqual(doc["rules"], parts.RULES)
        self.assertTrue(parts.RULES.startswith("zip,tar,7z,rar:"))
        self.assertTrue(parts.RULES.endswith(":readers %d" % parts.READERS))
        for i, rules in enumerate(("zip:1:8388608:1024:4:50331648", "zip,tar,7z,rar:1:8388608:1024:4:50331648",
                                   "zip,tar,7z,rar:1:8388608:1024:4:50331648:readers 2")):
            write(cache, json.dumps({"format": 1, "rules": rules, "blobs": {sha: layouts.WHY_NOT_RAR}}).encode("utf-8"))
            with mock.patch.object(parts, "make_index", wraps=parts.make_index) as made:
                res = self.release(str(2 + i))
            self.assertEqual((made.call_count, res.warnings), (1, []), rules)
            self.assertTrue(self.manifest().files["data/maps.rar"].parts)


if __name__ == "__main__":
    unittest.main()
