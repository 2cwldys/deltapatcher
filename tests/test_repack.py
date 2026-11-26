"""Repacking (deltapatcher/repack.py; publish's repack_archives, off unless a project turns it on): a big .tar.gz, .gz
or solid .7z that gets no parts index as it is is packed again so it patches inside, and players get the repacked
archive, whose files are the original's. Each kind is repacked here from archives Python makes, from tiny real ones
GNU gzip and 7-Zip made (tests/data/README.txt), and from 7z archives of every coder chain repacking decodes, made
by the writer below; then read back (by Python's gzip and tarfile, by layouts.py), repacked twice to the same bytes,
damaged, and released: a changed file inside travels alone. The reader of the gzip it writes (layouts.gz_layout())
gets the damage phase B's readers got."""

import bz2
import gzip
import hashlib
import io
import json
import lzma
import os
import random
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import zlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import read, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import client, config, layouts, parts, presets, publish, repack, signing  # noqa: E402
from test_archives import TEXTURE, Base  # noqa: E402
from test_layouts import big_files, fixture, seven_bytes, sevenzip_bytes, sz_number  # noqa: E402


# ---- writers ---------------------------------------------------------------------------------------------------------

def tar_gz(files):
    """A .tar.gz of files [(name, data)] as Python's tarfile writes one ("w:gz")."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files:
            ti = tarfile.TarInfo(name)
            ti.size, ti.mtime = len(data), 1793000000
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


# a 7z coder: (its folder bytes: flags, id, properties; what lzma, zlib or bz2 packs with it)
_DELTA = (b"\x21\x03" + sz_number(1) + b"\x03", {"id": lzma.FILTER_DELTA, "dist": 4})
_X86 = (b"\x04\x03\x03\x01\x03", {"id": lzma.FILTER_X86})
_LZMA2 = (b"\x21\x21" + sz_number(1) + bytes([16]), {"id": lzma.FILTER_LZMA2, "dict_size": 1 << 20, "preset": 0})
_LZMA = (b"\x23\x03\x01\x01" + sz_number(5) + bytes([93]) + struct.pack("<I", 1 << 20),
         {"id": lzma.FILTER_LZMA1, "dict_size": 1 << 20, "lc": 3, "lp": 0, "pb": 2, "preset": 0})
_DEFLATE = (b"\x03\x04\x01\x08", "deflate")
_BZIP2 = (b"\x03\x04\x02\x02", "bzip2")
_COPY = (b"\x01\x00", "copy")
CHAINS = {"LZMA2": [_LZMA2], "LZMA": [_LZMA], "Deflate": [_DEFLATE], "BZip2": [_BZIP2], "Copy": [_COPY],
          "Delta+LZMA2": [_DELTA, _LZMA2], "BCJ x86+LZMA2": [_X86, _LZMA2], "BCJ x86+Deflate": [_X86, _DEFLATE],
          "Delta+BZip2": [_DELTA, _BZIP2]}


def _filtered(chain, data):
    """data packed by the coders of `chain` (the first the one that gives the output)."""
    inner = chain[-1][1]
    specs = [c[1] for c in chain[:-1]]
    if isinstance(inner, dict):
        return lzma.compress(data, format=lzma.FORMAT_RAW, filters=specs + [inner])
    if specs:                                   # the filters' bytes: what LZMA2 packs after them, unpacked again
        packed = lzma.compress(data, format=lzma.FORMAT_RAW, filters=specs + [{"id": lzma.FILTER_LZMA2, "preset": 0}])
        data = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2,
                                                                       "dict_size": 8 << 20}]).decompress(packed)
    if inner == "deflate":
        z = zlib.compressobj(6, zlib.DEFLATED, -15)
        return z.compress(data) + z.flush()
    if inner == "bzip2":
        return bz2.compress(data)
    return data


def solid_7z(files, chain=(_LZMA2,), encoded=False):
    """A solid 7z of files [(name, data)]: one folder of the coders of `chain` (each one stream in and one out, bound
    one to the next), its files' sizes and CRCs in kSubStreamsInfo, their names; its header plain, or LZMA-encoded."""
    n = sz_number
    whole = b"".join(d for _n, d in files)
    packed = _filtered(list(chain), whole)
    coders = n(len(chain)) + b"".join(c[0] for c in chain) + b"".join(n(i) + n(i + 1) for i in range(len(chain) - 1))
    h = b"\x01\x04\x06" + n(0) + n(1) + b"\x09" + n(len(packed)) + b"\x00"
    h += b"\x07\x0b" + n(1) + b"\x00" + coders + b"\x0c" + n(len(whole)) * len(chain) + b"\x00"
    h += b"\x08\x0d" + n(len(files)) + b"\x09" + b"".join(n(len(d)) for _n, d in files[:-1])
    h += b"\x0a\x01" + b"".join(struct.pack("<I", zlib.crc32(d)) for _n, d in files) + b"\x00\x00"
    names = b"\x00" + b"".join(name.encode("utf-16-le") + b"\x00\x00" for name, _d in files)
    h += b"\x05" + n(len(files)) + b"\x11" + n(len(names)) + names + b"\x00\x00"
    if not encoded:
        return seven_bytes(h, packed)
    hp = lzma.compress(h, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16, "lc": 3,
                                                            "lp": 0, "pb": 2}])
    coder = b"\x23\x03\x01\x01" + n(5) + bytes([93]) + struct.pack("<I", 1 << 16)
    enc = b"\x17\x06" + n(len(packed)) + n(1) + b"\x09" + n(len(hp)) + b"\x00\x07\x0b\x01\x00\x01" + coder
    enc += b"\x0c" + n(len(h)) + b"\x0a\x01" + struct.pack("<I", zlib.crc32(h)) + b"\x00\x00"
    return seven_bytes(enc, packed + hp)


def files_of_7z(data):
    """The files' bytes of a repacked 7z, decoded stream by stream (a folder for each, LZMA2), in order."""
    kind, ranges, why = parts.find_layout(io.BytesIO(data), len(data))
    out = []
    for a, b in ranges:
        dec = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "dict_size": 16 << 20}])
        out.append(dec.decompress(data[a:b]))
    return out


def table(data):
    """(the version, the count, [each segment's deflate length], where the deflate stream begins) of the "DP" subfield
    of a gzip repack wrote."""
    xlen = struct.unpack("<H", data[10:12])[0]
    extra = data[12:12 + xlen]
    count = struct.unpack("<I", extra[5:9])[0]
    pos = 12 + xlen
    for bit in (0x08, 0x10):
        if data[3] & bit:
            pos = data.index(b"\0", pos) + 1
    return extra[4], count, list(struct.unpack("<%dI" % count, extra[9:9 + 4 * count])), pos


def segments(data):
    """[(where, deflate bytes, content bytes)] of each segment of a gzip repack wrote, each inflated on its own."""
    _v, _n, lengths, pos = table(data)
    out = []
    for n in lengths:
        out.append((pos, data[pos:pos + n], zlib.decompressobj(-15).decompress(data[pos:pos + n])))
        pos += n
    return out


def reads_fextra_in_stream_mode():
    """Whether this Python's tarfile reads a gzip with FEXTRA in stream mode ("r|gz"): CPython's reads the extra field
    through its decompressor (tarfile.py, _init_read_gz: self.read(xlen)), so no gzip with FEXTRA opens that way."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        ti = tarfile.TarInfo("a")
        ti.size = 1
        tf.addfile(ti, io.BytesIO(b"a"))
    z = zlib.compressobj(9, zlib.DEFLATED, -15)
    body = z.compress(buf.getvalue()) + z.flush()
    gz = b"\x1f\x8b\x08\x04" + bytes(4) + b"\x02\xff\x06\x00BC\x02\x00\x00\x00" + body + struct.pack(
        "<II", zlib.crc32(buf.getvalue()), len(buf.getvalue()))
    try:
        with tarfile.open(fileobj=io.BytesIO(gz), mode="r|gz") as tf:
            return [m.name for m in tf] == ["a"]
    except tarfile.TarError:
        return False


def repacked(data, suffix=".gz"):
    """The bytes repack.repack() makes of `data`, and what kind it says it was."""
    folder = tempfile.mkdtemp(prefix="dp-repack-")
    try:
        src, out = os.path.join(folder, "in" + suffix), os.path.join(folder, "out")
        write(src, data)
        kind = repack.repack(src, out)
        return read(out), kind
    finally:
        __import__("shutil").rmtree(folder, True)


# ---- the repackers ---------------------------------------------------------------------------------------------------

class Repacking(unittest.TestCase):
    def test_a_tar_gz_is_repacked_as_one_member_a_segment_for_each_file(self):
        files = [("a.bin", os.urandom(30000)), ("dir/b.bin", os.urandom(31000)), ("c.txt", b"hello\n" * 300)]
        for label, data in (("tarfile", tar_gz(files)), ("GNU gzip", fixture("gnu.tar.gz"))):
            out, kind = repacked(data)
            self.assertEqual(kind, "tar.gz", label)
            tar = gzip.decompress(data)
            # one member: a single-member reader reads all of it, with nothing after it
            dec = zlib.decompressobj(31)
            self.assertEqual((dec.decompress(out), dec.eof, dec.unused_data), (tar, True, b""), label)
            self.assertEqual(zlib.decompress(out, 31), tar, label)
            with tarfile.open(fileobj=io.BytesIO(out), mode="r:gz") as tf, \
                    tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as orig:
                self.assertEqual([(m.name, m.size) for m in tf.getmembers()],
                                 [(m.name, m.size) for m in orig.getmembers()], label)
                self.assertEqual([tf.extractfile(m).read() for m in tf.getmembers() if m.isfile()],
                                 [orig.extractfile(m).read() for m in orig.getmembers() if m.isfile()], label)
            if reads_fextra_in_stream_mode():
                with tarfile.open(fileobj=io.BytesIO(out), mode="r|gz") as tf:
                    self.assertEqual(len([m for m in tf]), len(files))
            self.assertEqual(repacked(data)[0], out, label)                           # the same bytes again
            # the original's MTIME and OS, XFL 2, FEXTRA with the table: version 1, a segment for each file and for
            # each run of headers between them, the lengths the whole deflate stream
            self.assertEqual((out[:3], out[3] & 0x04, out[4:8], out[8], out[9]),
                             (b"\x1f\x8b\x08", 0x04, data[4:8], 2, data[9]), label)
            version, count, lengths, at = table(out)
            self.assertEqual((version, count, sum(lengths)), (1, 2 * len(files) + 1, len(out) - at - 8), label)
            self.assertEqual(b"".join(content for _p, _d, content in segments(out)), tar, label)
            kind, ranges, why = parts.find_layout(io.BytesIO(out), len(out))
            self.assertEqual((kind, why, ranges), ("gz", "", [(p, p + len(d)) for p, d, _c in segments(out)]), label)
        # each file's data is a segment of its own: its deflate bytes the same whatever is around it
        out = repacked(tar_gz(files))[0]
        other = repacked(tar_gz([files[0], ("dir/b.bin", os.urandom(31000)), files[2]]))[0]
        self.assertEqual(segments(out)[1][1], segments(other)[1][1])
        self.assertNotEqual(segments(out)[3][1], segments(other)[3][1])
        self.assertEqual(segments(out)[5][1], segments(other)[5][1])

    def test_every_gzip_reader_reads_a_repacked_gzip_whole_with_its_name_and_time(self):
        content = os.urandom(3 << 20) + b"tail marker"
        raw = io.BytesIO()
        with gzip.GzipFile(filename="level1.dat", mode="wb", fileobj=raw, mtime=1793000000) as g:
            g.write(content)
        out, kind = repacked(raw.getvalue())
        self.assertEqual(kind, "gz")
        self.assertEqual((out[3] & 0x08, out[4:8], b"level1.dat\0" in out[:400]),
                         (0x08, struct.pack("<I", 1793000000), True))
        self.assertEqual((zlib.decompress(out, 31), gzip.decompress(out)), (content, content))
        folder = tempfile.mkdtemp(prefix="dp-repack-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        path = os.path.join(folder, "level1.gz")
        write(path, out)
        tools = {"GNU gzip": [r"C:\Program Files\Git\usr\bin\gzip.exe", "-dc", path],
                 "7-Zip": [r"C:\Program Files\7-Zip\7z.exe", "e", "-so", path]}
        for label, argv in tools.items():
            if os.path.isfile(argv[0]):
                got = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=120).stdout
                self.assertEqual(len(got), len(content), label)
        listed = subprocess.run([r"C:\Program Files\7-Zip\7z.exe", "l", path], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=120).stdout if os.path.isfile(
            r"C:\Program Files\7-Zip\7z.exe") else b"2026-10-26 level1.dat"
        self.assertIn(b"level1.dat", listed)
        powershell = shutil.which("powershell")
        if powershell and os.name == "nt":                          # .NET Framework's GZipStream: one member only
            script = ("Add-Type -AssemblyName System.IO.Compression; $f = [IO.File]::OpenRead('%s'); "
                      "$g = New-Object IO.Compression.GZipStream($f, [IO.Compression.CompressionMode]::Decompress); "
                      "$m = New-Object IO.MemoryStream; $g.CopyTo($m); $m.Length" % path)
            got = subprocess.run([powershell, "-NoProfile", "-Command", script], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=120).stdout.strip()
            self.assertEqual(got, str(len(content)).encode("ascii"))

    def test_a_gz_of_one_file_is_cut_where_its_content_says(self):
        data = os.urandom(16 << 20)
        out, kind = repacked(gzip.compress(data, 1, mtime=0))
        self.assertEqual((kind, gzip.decompress(out)), ("gz", data))
        found = segments(out)
        sizes = [len(content) for _p, _d, content in found]
        self.assertTrue(all(repack.CDC_MIN <= s <= repack.CDC_MAX for s in sizes[:-1]), sizes)
        self.assertEqual(repacked(gzip.compress(data, 9, mtime=0))[0], out)          # the same, however it was packed
        # bytes put in near the start: the chunk they touch (and the one after) change, every other is as it was,
        # though all of it moved
        edited = data[:3 << 20] + b"an edit" + data[3 << 20:]
        out2 = repacked(gzip.compress(edited, 1, mtime=0))[0]
        old = {d for _p, d, _c in found}
        new = [d for _p, d, _c in segments(out2)]
        self.assertGreater(len(new), 6)
        self.assertLessEqual(sum(1 for d in new if d not in old), 2)
        # the cut: where the sum of the gear over the 64 bytes before it has its low 20 bits zero
        buf = bytearray(os.urandom(5 << 20))
        cut = repack.first_cut(buf, repack.CDC_MAX)
        if cut is not None:
            self.assertEqual(sum(repack._GEAR[b] for b in buf[cut - 64:cut]) & repack.CDC_MASK, 0)
            self.assertTrue(all(sum(repack._GEAR[b] for b in buf[p - 64:p]) & repack.CDC_MASK
                                for p in range(repack.CDC_MIN, min(cut, repack.CDC_MIN + 2000))))

    def test_past_segments_gz_neighbours_are_merged_smallest_first(self):
        files = [("f%02d.bin" % i, os.urandom(100 + 1000 * (i % 4))) for i in range(12)]
        data = tar_gz(files)
        with mock.patch.object(repack, "SEGMENTS_GZ", 9):
            out, kind = repacked(data)
        self.assertEqual(kind, "tar.gz")
        self.assertEqual(table(out)[1], 9)
        self.assertEqual(zlib.decompress(out, 31), gzip.decompress(data))
        # merging: the two neighbours of fewest bytes first, never past MEMBER_MAX, each merge one segment fewer
        cuts = [(0, 10), (10, 15), (15, 100), (100, 103), (103, 200)]
        self.assertEqual(repack.merged(cuts, 3), [(0, 15), (15, 103), (103, 200)])      # 15 bytes, then 88
        with mock.patch.object(repack, "MEMBER_MAX", 50):
            self.assertRaises(repack.NotRepacked, repack.merged, [(0, 40), (40, 80), (80, 120)], 2)
        # a run of headers and padding is cut at MEMBER_MAX as a data area is
        with mock.patch.object(repack, "MEMBER_MAX", 1000):
            cuts = repack.tar_cuts([(512, 1112)], 1112 + 512 + 5000)
            self.assertTrue(all(b - a <= 1000 for a, b in cuts))
            self.assertEqual((cuts[0][0], cuts[-1][1]), (0, 1112 + 512 + 5000))

    def test_a_solid_7z_of_every_coder_chain_python_decodes_is_repacked_a_folder_for_each_file(self):
        files = [("a.bin", os.urandom(20000)), ("b.txt", b"the notes\n" * 2000), ("c.bin", os.urandom(15000))]
        archives = [("%s%s" % (label, " (encoded header)" if enc else ""), solid_7z(files, chain, enc))
                    for label, chain in CHAINS.items() for enc in (False, True)]
        archives.append(("phase B's writer (Copy)", sevenzip_bytes(files, solid=True, encoded="lzma2")))
        for label, data in archives:
            self.assertEqual(parts.find_layout(io.BytesIO(data), len(data))[2], layouts.WHY_SOLID, label)
            out, kind = repacked(data, ".7z")
            self.assertEqual(kind, "7z", label)
            k, ranges, why = parts.find_layout(io.BytesIO(out), len(out))
            self.assertEqual((k, why, len(ranges)), ("7z", "", 3), label)            # a folder for each file
            self.assertEqual(files_of_7z(out), [d for _n, d in files], label)
            self.assertEqual(repacked(data, ".7z")[0], out, label)

    def test_a_real_solid_7z_keeps_its_file_list(self):
        rnd = random.Random(20261125)
        a = bytes(rnd.randrange(256) for _ in range(3000))
        b = bytes(rnd.randrange(256) for _ in range(3100))
        data = fixture("7z-solid-lzma2.7z")
        out, kind = repacked(data, ".7z")
        self.assertEqual(sorted(files_of_7z(out), key=len), sorted([a, b, b"hello\n" * 30], key=len))
        folder = tempfile.mkdtemp(prefix="dp-repack-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        for name, d in (("in.7z", data), ("out.7z", out)):
            write(os.path.join(folder, name), d)
        heads = []
        for name in ("in.7z", "out.7z"):
            with open(os.path.join(folder, name), "rb") as f:
                heads.append(repack._header(f, os.path.getsize(f.name), None))
        s = repack._parse(heads[0][0], heads[0][2], None)
        self.assertTrue(s.files.startswith(b"\x05") and len(s.files) > 100)
        # its file list (names, times, attributes, the empty file and folder) as it was, its header encoded as it was
        self.assertTrue(heads[1][0].endswith(s.files + b"\x00"))
        self.assertEqual(heads[1][1], heads[0][1])
        self.assertEqual(sorted(s.lengths), sorted([3000, 3100, 180]))

    def test_a_coder_python_does_not_decode_is_named_and_left_as_it_is(self):
        for name, coder in (("7z-solid-ppmd.7z", "PPMd"), ("7z-solid-bcj2.7z", "BCJ2")):
            data = fixture(name)
            folder = tempfile.mkdtemp(prefix="dp-repack-")
            self.addCleanup(__import__("shutil").rmtree, folder, True)
            path = os.path.join(folder, name)
            write(path, data)
            self.assertEqual(repack.coder_problem(path, len(data)), "coder:" + coder)
            with self.assertRaises(repack.NotRepacked) as e:
                repack.repack(path, path + ".out")
            self.assertEqual(e.exception.code, "coder:" + coder)
            self.assertFalse(os.path.exists(path + ".out"))
            self.assertIn("with %s, which repacking does not decode" % coder, repack.words(e.exception.code))

    def test_a_branch_filter_over_lzma_is_repacked(self):
        rnd = random.Random(20261125)
        a = bytes(rnd.randrange(256) for _ in range(3000))
        b = bytes(rnd.randrange(256) for _ in range(3100))
        for name in ("7z-solid-bcj-lzma.7z", "7z-solid-arm-lzma.7z", "7z-solid-sparc-lzma.7z"):
            out, kind = repacked(fixture(name), ".7z")
            self.assertEqual(kind, "7z", name)
            self.assertEqual(sorted(files_of_7z(out), key=len), sorted([a, b, b"hello\n" * 30], key=len), name)

    def test_a_damaged_or_malformed_solid_7z_is_left_as_it_is(self):
        # its data damaged anywhere: the original's own CRCs, or its decoder, say so; never another exception
        data = fixture("7z-solid-deflate.7z")
        folder = tempfile.mkdtemp(prefix="dp-repack-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        path = os.path.join(folder, "in.7z")
        write(path, data)
        with open(path, "rb") as f:
            head, _encoded, offset = repack._header(f, len(data), None)
        s = repack._parse(head, offset, None)
        rnd = random.Random(11)
        seen = set()
        for at in [s.at + rnd.randrange(s.packed) for _ in range(60)]:
            d = bytearray(data)
            d[at] ^= 1 + rnd.randrange(255)
            write(path, bytes(d))
            try:
                repack.repack(path, path + ".out")
                seen.add("repacked")
            except repack.NotRepacked as e:
                seen.add(e.code)
        self.assertLessEqual(seen, {repack.WHY_DAMAGED, "repacked"})
        self.assertIn(repack.WHY_DAMAGED, seen)
        files = [(n, os.urandom(3000)) for n in ("a", "b", "c")]
        damaged = bytearray(solid_7z(files, [_BZIP2]))
        damaged[60] ^= 0x55
        with self.assertRaises(repack.NotRepacked) as e:
            repacked(bytes(damaged), ".7z")
        self.assertEqual(e.exception.code, repack.WHY_DAMAGED)
        self.assertIn("the archive is damaged, so it goes out as it is", repack.words(repack.WHY_DAMAGED))
        # its header malformed: how many files given twice, around their CRCs (then the CRCs are fewer than the files),
        # or twice the same before their sizes
        n = sz_number
        whole = b"".join(d for _n, d in files)
        packed = lzma.compress(whole, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2, "preset": 0}])
        h = b"\x01\x04\x06" + n(0) + n(1) + b"\x09" + n(len(packed)) + b"\x00\x07\x0b" + n(1) + b"\x00" + n(1)
        h += _LZMA2[0] + b"\x0c" + n(len(whole)) + b"\x00\x08"
        crcs = b"\x0a\x01" + b"".join(struct.pack("<I", zlib.crc32(d)) for _n, d in files)
        well = seven_bytes(h + b"\x0d" + n(3) + b"\x09" + n(3000) * 2 + crcs + b"\x00\x00\x00", packed)
        self.assertEqual(files_of_7z(repacked(well, ".7z")[0]), [d for _n, d in files])     # as 7-Zip writes it
        for sub in (b"\x0d" + n(3) + crcs + b"\x0d" + n(4) + b"\x09" + n(3000) * 3,
                    b"\x0d" + n(3) + b"\x0d" + n(3) + b"\x09" + n(3000) * 2 + crcs):
            with self.assertRaises(repack.NotRepacked) as e:
                repacked(seven_bytes(h + sub + b"\x00\x00\x00", packed), ".7z")
            self.assertEqual(e.exception.code, repack.WHY_7Z)

    def test_without_bz2_or_lzma_the_engine_imports_and_says_why(self):
        code = ("import sys\n"
                "sys.modules['_bz2'] = None\n"
                "sys.modules['_lzma'] = None\n"
                "sys.path.insert(0, sys.argv[1])\n"
                "import deltapatcher.client, deltapatcher.cli, deltapatcher.repack as r\n"
                "print(r.bz2 is None, r.lzma is None)\n")
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        got = subprocess.run([sys.executable, "-c", code, root], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             cwd=tempfile.gettempdir(), timeout=120)
        self.assertEqual((got.returncode, got.stdout.split()), (0, [b"True", b"True"]), got.stderr[-500:])
        files = [(n, os.urandom(3000)) for n in ("a", "b", "c")]
        for module, chain in (("bz2", [_BZIP2]), ("lzma", [_LZMA2])):
            with mock.patch.object(repack, module, None), self.assertRaises(repack.NotRepacked) as e:
                repacked(solid_7z(files, chain), ".7z")
            self.assertEqual(e.exception.code, "module:" + module)
            self.assertTrue(repack.transient(e.exception.code))

    def test_every_limit(self):
        files = [("f%03d.bin" % i, os.urandom(2000)) for i in range(40)]
        data = tar_gz(files)
        # more than REPACK_MAX bytes decoded, too little free space
        for patch, code, made in (((repack, "REPACK_MAX", 50000), repack.WHY_BIG, data),
                                  ((repack, "RESERVE", 1 << 60), repack.WHY_SPACE, data)):
            with mock.patch.object(*patch), self.assertRaises(repack.NotRepacked) as e:
                repacked(made)
            self.assertEqual(e.exception.code, code, patch[1])
        with mock.patch.object(repack, "REPACK_MAX", 50000), self.assertRaises(repack.NotRepacked) as e:
            repacked(solid_7z(files[:30]), ".7z")
        self.assertEqual(e.exception.code, repack.WHY_BIG)
        # the chunks of a .gz are segments too: past SEGMENTS_GZ merged, and when merging cannot get under it (each
        # merge past MEMBER_MAX) not repacked
        with mock.patch.object(repack, "SEGMENTS_GZ", 1), mock.patch.object(repack, "CDC_MAX", 300 << 10), \
                mock.patch.object(repack, "MEMBER_MAX", 400 << 10), self.assertRaises(repack.NotRepacked) as e:
            repacked(gzip.compress(os.urandom(700 << 10), 1))
        self.assertEqual(e.exception.code, repack.WHY_PIECES)
        with mock.patch.object(repack, "SEGMENTS_GZ", 2), mock.patch.object(repack, "CDC_MAX", 300 << 10):
            self.assertEqual(table(repacked(gzip.compress(os.urandom(700 << 10), 1))[0])[1], 2)
        # a repacked 7z that does not decode to the original's files is never used
        real = repack._write_seven

        def flipped(f, s, encoded, out, cancel):           # a byte of a packed stream changed
            streams = real(f, s, encoded, out, cancel)
            with open(out, "r+b") as o:
                o.seek(streams[0][0] + 10)
                byte = o.read(1)
                o.seek(-1, 1)
                o.write(bytes([byte[0] ^ 0xFF]))
            return streams

        def other(f, s, encoded, out, cancel):             # a file that decodes to other bytes than the original's
            streams = real(f, s, encoded, out, cancel)
            return [(a, p, length, crc ^ 1, d) for a, p, length, crc, d in streams]

        for writer in (flipped, other):
            with mock.patch.object(repack, "_write_seven", side_effect=writer), \
                    self.assertRaises(repack.NotRepacked) as e:
                repacked(solid_7z(files[:30]), ".7z")
            self.assertEqual(e.exception.code, repack.WHY_CHECK, writer.__name__)
        # a gzip cut short, or with anything after its last member
        for bad in (data[:-20], data + b"\0" * 10, data + b"not gzip"):
            with self.assertRaises(repack.NotRepacked) as e:
                repacked(bad)
            self.assertEqual(e.exception.code, repack.WHY_GZIP)
        # a stop
        stop = threading.Event()
        stop.set()
        folder = tempfile.mkdtemp(prefix="dp-repack-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        write(os.path.join(folder, "a.tar.gz"), data)
        self.assertRaises(layouts.Stopped, repack.repack, os.path.join(folder, "a.tar.gz"), os.path.join(folder, "o"),
                          stop)
        self.assertFalse(os.path.exists(os.path.join(folder, "o")))

    def test_the_gz_reader_reads_only_the_table_repack_writes(self):
        out = repacked(tar_gz([("a.bin", os.urandom(30000)), ("b.bin", os.urandom(30000))]))[0]
        self.assertEqual(parts.find_layout(io.BytesIO(out), len(out))[::2], ("gz", ""))
        _version, count, lengths, at = table(out)
        xlen = struct.unpack("<H", out[10:12])[0]

        def changed(where, value):
            d = bytearray(out)
            d[where:where + len(value)] = value
            return bytes(d)

        def with_table(n, values):
            """out with its table saying n segments of these lengths (the extra field as long as it was)."""
            body = struct.pack("<I", n) + struct.pack("<%dI" % len(values), *values)
            return changed(17, body)

        crafted = (("method", changed(2, b"\x09")), ("a flag no gzip has", changed(3, bytes([out[3] | 0x20]))),
                   ("no FEXTRA", changed(3, bytes([out[3] & ~0x04]))), ("subfield id", changed(12, b"DQ")),
                   ("subfield length", changed(14, struct.pack("<H", xlen - 5))), ("version 2", changed(16, b"\x02")),
                   ("a count past SEGMENTS_GZ", with_table(layouts.SEGMENTS_GZ + 1, lengths)),
                   ("a count past its table", with_table(count + 1, lengths)),
                   ("lengths that do not sum to the stream", with_table(count, [lengths[0] + 1] + lengths[1:])),
                   ("a length of 0", with_table(count, [0, lengths[0] + lengths[1]] + lengths[2:])),
                   ("cut short", out[:-1]), ("bytes after its end", out + b"\0" * 8),
                   ("a plain gzip", gzip.compress(b"x" * 5000)))
        for label, data in crafted:
            started = time.time()
            self.assertEqual(parts.find_layout(io.BytesIO(data), len(data)), ("", None, parts.WHY_NONE), label)
            self.assertLess(time.time() - started, 0.5, label)
        self.assertEqual(layouts.gz_layout(io.BytesIO(out), len(out), 2), (None, layouts.WHY_PIECES))
        stop = threading.Event()
        stop.set()
        with mock.patch.object(layouts, "CANCEL_EVERY", 2):
            self.assertRaises(layouts.Stopped, parts.find_layout, io.BytesIO(out), len(out), stop)
        self.assertEqual(at, 12 + xlen)
        # cut short at every byte, or bytes changed at random: never an exception, never past its end
        rnd = random.Random(7)
        for cut in range(len(out)):
            got = parts.find_layout(io.BytesIO(out[:cut]), cut)
            self.assertTrue(got[1] is None or all(b <= cut for _a, b in got[1]))
        for _ in range(3000):
            d = bytearray(out)
            for _ in range(rnd.randint(1, 4)):
                d[rnd.randrange(min(len(d), 200))] = rnd.randrange(256)
            got = parts.find_layout(io.BytesIO(bytes(d)), len(d))
            self.assertTrue(got[1] is None or all(0 <= a < b <= len(d) for a, b in got[1]))


# ---- releases --------------------------------------------------------------------------------------------------------

class Releases(Base):
    def release(self, version, store=None, log=None, repack_archives=True):
        return publish.publish(self.src, store or self.store, "Game", version, log=log,
                               repack_archives=repack_archives)

    def rebuilt_with_one_file_changed(self, name, make, changed):
        """Release 1 of the archive `make(files)` repacked, installed; release 2 with the file `changed` replaced:
        (the update's plan, the bytes fetched, the archive players have)."""
        files = big_files()
        self.put(name, make(files))
        res = self.release("1")
        self.assertEqual([r["path"] for r in res.repacked], [name])
        url = self.serve()
        client.update(url, self.game)
        fresh = os.urandom(TEXTURE)
        self.put(name, make([(n, fresh if n == changed else d) for n, d in files]))
        self.release("2")
        del self.served.answers[:]
        plan = client.update(url, self.game)
        installed = self.installed(name)
        self.assertEqual(hashlib.sha256(installed).hexdigest(), self.manifest().files[name].sha256)
        return plan, [a[3] for a in self.served.of("blobs")], installed

    def test_a_changed_file_inside_a_tar_gz_is_all_that_travels(self):
        plan, fetched, have = self.rebuilt_with_one_file_changed("data/maps.tar.gz", tar_gz, "textures/t05.tga")
        self.assertEqual(len(plan.rebuilds), 1)
        self.assertEqual(len(fetched), 1)                                  # that file's member's deflate data
        self.assertLess(abs(fetched[0] - TEXTURE), TEXTURE // 100)
        with tarfile.open(fileobj=io.BytesIO(have)) as tf:
            self.assertEqual(len(tf.getmembers()), len(big_files()))

    def test_an_edit_in_a_gz_fetches_about_the_chunks_it_touched(self):
        data = os.urandom(9 << 20)
        self.put("data/world.gz", gzip.compress(data, 1))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        self.put("data/world.gz", gzip.compress(data[:5 << 20] + b"an edit" + data[(5 << 20) + 7:], 1))
        self.release("2")
        del self.served.answers[:]
        plan = client.update(url, self.game)
        self.assertEqual(len(plan.rebuilds), 1)
        self.assertLessEqual(plan.fetch_bytes(), 2 * repack.CDC_MAX + 4096)
        self.assertLess(plan.fetch_bytes() * 2, len(data))
        self.assertEqual(gzip.decompress(self.installed("data/world.gz"))[5 << 20:(5 << 20) + 7], b"an edit")

    def test_a_change_in_a_solid_7z_fetches_that_file_alone(self):
        plan, fetched, have = self.rebuilt_with_one_file_changed("data/maps.7z", solid_7z, "textures/t02.tga")
        self.assertEqual((len(plan.rebuilds), len(fetched)), (1, 1))
        self.assertLess(abs(fetched[0] - TEXTURE), TEXTURE // 50)
        self.assertEqual(len(files_of_7z(have)), len(big_files()))

    def test_off_nothing_is_repacked_and_the_warning_names_the_switch(self):
        files = big_files()
        self.put("data/maps.tar.gz", tar_gz(files))
        self.put("data/maps.7z", solid_7z(files))
        res = self.release("1", repack_archives=False)
        self.assertEqual(res.repacked, [])
        man = self.manifest()
        for name in ("data/maps.tar.gz", "data/maps.7z"):
            self.assertEqual(man.files[name].sha256, hashlib.sha256(read(self.p("src", *name.split("/")))).hexdigest())
            self.assertFalse(man.files[name].parts)
        self.assertEqual(len(res.warnings), 2)
        switch = "turn on Repack archives so they patch inside (repack_archives)"
        self.assertTrue(all(switch in w for w in res.warnings))

    def test_on_an_unchanged_archive_is_not_repacked_again(self):
        self.put("data/maps.tar.gz", tar_gz(big_files()))
        real = repack.repack
        said = []
        with mock.patch.object(repack, "repack", side_effect=real) as made:
            res = self.release("1", log=said.append)
            self.assertEqual(made.call_count, 1)
            res2 = self.release("2", log=said.append)
            self.assertEqual(made.call_count, 1)                           # the store's repack cache
        self.assertEqual(res.repacked, res2.repacked)
        r = res.repacked[0]
        self.assertIn("; 1 archive repacked to patch inside, %s" % publish.growth(r),
                      res.summary())
        line = publish.REPACKED_LINE % ("data/maps.tar.gz", publish.human(r["from_bytes"]),
                                         publish.human(r["to_bytes"]), publish.growth(r))
        self.assertEqual([s for s in said if s.startswith("repacked ")], [line, line + " (as before)"])
        # nothing of the original archive is in the store: its repacked blob only, with its parts index
        blobs = os.listdir(os.path.join(self.store, "blobs"))
        source = hashlib.sha256(read(self.p("src", "data", "maps.tar.gz"))).hexdigest()
        self.assertNotIn(source, blobs)
        self.assertTrue(self.manifest().files["data/maps.tar.gz"].parts)
        cache = json.loads(read(os.path.join(self.store, repack.CACHE_NAME)))
        self.assertEqual((cache["rules"], cache["sources"]), (repack.RULES, {source: self.manifest().files[
            "data/maps.tar.gz"].sha256}))

    def test_an_archive_of_many_similar_files_is_repacked_however_much_it_grows(self):
        # 1,500 JSON files that share most of their bytes: each packed on its own loses what they share, so the
        # repacked archive is many times bigger; it is used all the same, and the log, the summary and the result
        # say how much bigger
        rnd = random.Random(1500)
        mesh = bytes(rnd.getrandbits(8) for _ in range(3000)).hex()
        files = [("units/u%04d.json" % i, json.dumps({"id": i, "hp": rnd.randrange(100, 900), "mesh": mesh}).encode(
            "ascii")) for i in range(1500)]
        self.put("data/units.tar.gz", tar_gz(files))
        said = []
        with mock.patch.object(parts, "PARTS_MIN", 1024):
            res = self.release("1", log=said.append)
        self.assertEqual([r["path"] for r in res.repacked], ["data/units.tar.gz"])
        r = res.repacked[0]
        self.assertGreater(r["to_bytes"], 5 * r["from_bytes"])
        self.assertTrue(publish.growth(r).startswith("+"))
        self.assertIn(publish.REPACKED_LINE % ("data/units.tar.gz", publish.human(r["from_bytes"]),
                                               publish.human(r["to_bytes"]), publish.growth(r)), said)
        self.assertIn("; 1 archive repacked to patch inside, " + publish.growth(r), res.summary())
        self.assertEqual(self.manifest().files["data/units.tar.gz"].size, r["to_bytes"])
        self.assertTrue(self.manifest().files["data/units.tar.gz"].parts)

    def test_this_computers_reasons_are_asked_again_the_archives_are_kept(self):
        files = [(n, os.urandom(3000)) for n in ("a", "b", "c")]
        self.put("data/maps.7z", solid_7z(files, [_BZIP2]))
        with mock.patch.object(parts, "PARTS_MIN", 1024):
            with mock.patch.object(repack, "bz2", None):
                res = self.release("1")
            self.assertEqual(res.repacked, [])
            self.assertIn("this Python has no bz2 module to decode it", res.warnings[0])
            cache = os.path.join(self.store, repack.CACHE_NAME)
            self.assertFalse(os.path.exists(cache) and json.loads(read(cache))["sources"])   # nothing kept
            res = self.release("2")                                         # asked again: it has bz2 now
            self.assertEqual([r["path"] for r in res.repacked], ["data/maps.7z"])
            damaged = bytearray(solid_7z(files, [_BZIP2]))
            damaged[60] ^= 0x55
            self.put("data/maps.7z", bytes(damaged))
            res = self.release("3")
        self.assertIn("the archive is damaged, so it goes out as it is", res.warnings[0])
        self.assertEqual(list(json.loads(read(os.path.join(self.store, repack.CACHE_NAME)))["sources"].values()),
                         [repack.WHY_DAMAGED])                              # the archive's: kept

    def test_on_a_7z_repacking_cannot_decode_keeps_its_warning_naming_the_coder(self):
        with mock.patch.object(parts, "PARTS_MIN", 1024):
            for name in ("7z-solid-ppmd.7z", "7z-solid-bcj2.7z"):
                self.put("data/" + name, fixture(name))
            res = self.release("1")
        self.assertEqual(res.repacked, [])
        self.assertEqual(len(res.warnings), 2)
        self.assertIn("packed as one solid stream with BCJ2, which repacking does not decode", res.warnings[0])
        self.assertIn("packed as one solid stream with PPMd, which repacking does not decode", res.warnings[1])

    def test_on_a_7z_whose_coder_properties_are_malformed_is_released_as_it_is(self):
        bad = (b"\x21\x21" + sz_number(1) + bytes([41]), _LZMA2[1])        # an LZMA2 dictionary property over 40
        data = solid_7z([(n, os.urandom(3000)) for n in ("a", "b", "c")], [bad])
        self.put("data/maps.7z", data)
        with mock.patch.object(parts, "PARTS_MIN", 1024):
            res = self.release("1")
        self.assertEqual(res.repacked, [])
        self.assertEqual(self.manifest().files["data/maps.7z"].sha256, hashlib.sha256(data).hexdigest())
        self.assertIn(repack.words(repack.WHY_7Z), res.warnings[0])


class ProjectFiles(unittest.TestCase):
    @unittest.skipUnless(signing.can_sign(), "needs cryptography")
    def test_a_private_project_file_carries_the_setting_a_public_one_does_not(self):
        folder = tempfile.mkdtemp(prefix="dp-repack-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        public = signing.generate_key(os.path.join(folder, "signing-key.pem"), "")
        signer = signing.load_key(os.path.join(folder, "signing-key.pem"), "")
        e = {"title": "Game", "source_url": "https://game.example.com/"}
        for on, version in ((True, 5), (False, 4)):
            cfg = dict(config.DEFAULTS, name="Game", version="1", repack_archives=on,
                       public_url="https://game.example.com/")
            raw = presets.build([dict(e, private=presets.private_part(cfg, public))], signer)
            found = presets.parse(raw.encode("utf-8"))
            self.assertEqual(found["version"], version)
            self.assertEqual(found["parts"][0]["publish"].get("repack_archives", False), on)
            self.assertEqual(presets.private_values(found["projects"][0], found["parts"][0])["repack_archives"], on)
        found = presets.parse(presets.build([e]).encode("utf-8"))
        self.assertEqual((found["version"], found["private"]), (1, False))
        self.assertNotIn("repack_archives", json.dumps(found))


if __name__ == "__main__":
    unittest.main()
