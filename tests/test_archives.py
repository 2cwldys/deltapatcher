"""Archive deltas (deltapatcher/parts.py and rebuild.py): a big zip-structured release file gets a parts index when it
is published, and an update rebuilds a changed one byte for byte from the members the folder already has, fetching
only the changed ones from its blob with Range requests. The archives are made here with zipfile: stored and
deflated members, data descriptors, zip64 headers and end records, a stub before the first member, members that
move to another archive or change order, and timestamps that all change while the data does not. Every server
listens on 127.0.0.1 and counts what it answers, so the tests measure what travels. Also every way a rebuild falls
back to the whole download, the check's smaller amount, deploys and backups of the indexes, statistics, the publish
warning for archives DeltaPatcher cannot patch inside, and what an older DeltaPatcher reads of a manifest with
indexes."""

import base64
import contextlib
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import tracemalloc
import unittest
import zipfile
import zlib
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, snapshot, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import (backup, cli, client, config, deploy, parts, projects, publish, rebuild, serve,  # noqa: E402
                          source, ssh, stats)
from deltapatcher import manifest as manifest_module  # noqa: E402
from deltapatcher.manifest import Entry, Manifest, ManifestError  # noqa: E402
from deltapatcher.torrent import folder as torrent_folder  # noqa: E402
from deltapatcher.source import RangeError, Source, SourceError  # noqa: E402
from test_deploy import REMOTE, FakeSftp  # noqa: E402

KB = 1 << 10
TEXTURE = 700 * KB              # a stored texture: its piece is exactly this many bytes
WHEN = (2026, 11, 1, 12, 0, 0)
LATER = (2026, 11, 20, 18, 30, 4)


class Unseekable(object):
    """A stream zipfile cannot seek in: it writes a data descriptor after each member."""

    def __init__(self, f):
        self.f = f

    def write(self, b):
        return self.f.write(b)

    def flush(self):
        pass


def zip_bytes(members, descriptors=False, zip64=False):
    """The bytes of a zip of members [(name, data, deflated, date_time)], as zipfile writes them; zip64: every member
    with zip64 sizes in its local header (and none in the central directory: the two extra fields differ), and the
    zip64 end records zipfile writes past 65535 members (its limit is lowered while it writes)."""
    buf = io.BytesIO()
    with mock.patch.object(zipfile, "ZIP_FILECOUNT_LIMIT", 1 if zip64 else zipfile.ZIP_FILECOUNT_LIMIT):
        with zipfile.ZipFile(Unseekable(buf) if descriptors else buf, "w") as z:
            for name, data, deflated, when in members:
                info = zipfile.ZipInfo(name, when)
                info.compress_type = zipfile.ZIP_DEFLATED if deflated else zipfile.ZIP_STORED
                with z.open(info, "w", force_zip64=zip64) as f:
                    f.write(data)
    return buf.getvalue()


def textures(n=13, when=WHEN):
    """A pk3's members: n stored textures (TEXTURE bytes each), two deflated scripts, and small files that travel in
    the index as literals. 13 textures make it bigger than PARTS_MIN."""
    out = [("textures/t%02d.tga" % i, os.urandom(TEXTURE), False, when) for i in range(n)]
    out += [("scripts/s%d.txt" % i, os.urandom(60 * KB).hex().encode("ascii"), True, when) for i in range(2)]
    out += [("small/%d.cfg" % i, (b"set %d\n" % i) * 10, True, when) for i in range(5)]
    return out


def changed(members, *names):
    """The members with new data for those named."""
    return [(n, os.urandom(len(d)) if n in names else d, z, w) for n, d, z, w in members]


def compressed_size(data, name):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.getinfo(name).compress_size


def data_offset(data, name):
    """Where the compressed data of the member `name` starts, from its local header."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        off = z.getinfo(name).header_offset
    n, m = struct.unpack("<HH", data[off + 26:off + 30])
    return off + 30 + n + m


def run_json(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(sys, "stdin", io.StringIO("")):
        rc = cli.main(["--json", "--no-log"] + argv)
    return rc, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


class Served(object):
    """deltapatcher serve for a store on 127.0.0.1 that keeps what it answered: [(path, Range asked, status, bytes)].
    ignore_range: Range is ignored (a 200 with the whole file, as a server without Range answers); wrong_range: the
    right bytes under another Content-Range; on_range(): called at each Range request, before the answer; cut_parts:
    while this threading.Event is set, a parts index is answered with its whole Content-Length and half its bytes,
    then the connection closes (a connection cut on the way)."""

    def __init__(self, store, ignore_range=False, wrong_range=False, on_range=None, cut_parts=None):
        base = serve.make_handler(store)
        answers = self.answers = []
        lock = threading.Lock()

        class Handler(base):
            def _send(self, head):
                self._asked = self.headers.get("Range")
                full = self._resolve()
                if cut_parts is not None and cut_parts.is_set() and not head and "/parts/" in self.path and full \
                        and os.path.isfile(full):
                    data = read(full)
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data[:len(data) // 2])
                    self.close_connection = True
                    return
                if self._asked and on_range is not None:
                    on_range()
                if self._asked and ignore_range:
                    del self.headers["Range"]
                return base._send(self, head)

            def send_response(self, code, message=None):
                self._code = code
                base.send_response(self, code, message)

            def send_header(self, key, value):
                if key == "Content-Length":
                    with lock:
                        answers.append((self.path, getattr(self, "_asked", None), self._code, int(value)))
                if key == "Content-Range" and wrong_range:
                    first, rest = value.split("-", 1)
                    value = "%s-%s" % (first[:-1] + str(int(first[-1]) ^ 1), rest)
                base.send_header(self, key, value)

        self.srv = serve.Server(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = "http://127.0.0.1:%d/" % self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()

    def of(self, folder):
        """The answers for files in `folder` (blobs, parts)."""
        return [a for a in self.answers if a[0].startswith("/%s/" % folder)]

    def blob_bytes(self):
        return sum(a[3] for a in self.of("blobs") if a[2] in (200, 206))


class Opened(object):
    """rebuild.py's open(), watched: of the files in `folder` (its .deltapatcher folder left out), every one opened
    (`paths`, as os.path.realpath and normcase give them) and the most open at once (`most`)."""

    def __init__(self, folder):
        self.folder = os.path.normcase(os.path.realpath(folder)) + os.sep
        self.state = self.folder + ".deltapatcher" + os.sep
        self.now = set()
        self.most = 0
        self.paths = []

    def __call__(self, path, mode="r", *args, **kw):
        f = open(path, mode, *args, **kw)
        p = os.path.normcase(os.path.realpath(path))
        if not p.startswith(self.folder) or p.startswith(self.state):
            return f
        self.paths.append(p)
        return _Counted(self, f)


class _Counted(object):
    """An open file Opened counts until it is closed."""

    def __init__(self, opened, f):
        self.opened, self.f = opened, f
        opened.now.add(self)
        opened.most = max(opened.most, len(opened.now))

    def __getattr__(self, name):
        return getattr(self.f, name)

    def close(self):
        self.opened.now.discard(self)
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def link_folder(case, link, target):
    """A link to a folder: a symbolic link where the system allows it, else (Windows) a junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name != "nt":
        case.skipTest("no links to folders here")
    done = subprocess.run(["cmd", "/c", "mklink", "/J", link, target], capture_output=True, text=True)
    if done.returncode != 0 or not os.path.isdir(link):
        case.skipTest("no junctions here: %s" % done.stdout)


def unlink_folder(link):
    """Remove a link to a folder (not the folder)."""
    if os.name == "nt":
        os.rmdir(link)                          # a junction, or a link to a folder
    else:
        os.unlink(link)


class Base(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        self.src, self.store, self.game = self.p("src"), self.p("store"), self.p("game")
        self.served = None

    def tearDown(self):
        if self.served is not None:
            self.served.close()
        TempCase.tearDown(self)

    def put(self, rel, data):
        write(self.p("src", *rel.split("/")), data)

    def release(self, version, store=None, log=None):
        return publish.publish(self.src, store or self.store, "Game", version, log=log)

    def serve(self, **kw):
        if self.served is not None:
            self.served.close()
        self.served = Served(self.store, **kw)
        return self.served.url

    def manifest(self, store=None):
        return Manifest.from_json(read(os.path.join(store or self.store, "manifest.json")))

    def installed(self, rel):
        return read(os.path.join(self.game, *rel.split("/")))

    def two_releases(self, **kw):
        """Release 1 of data/pak0.pk3 installed in the game folder from the served store, then release 2 with one
        texture changed published: (release 2's members, its bytes)."""
        one = textures()
        self.put("data/pak0.pk3", zip_bytes(one, **kw))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        two = changed(one, "textures/t03.tga")
        data = zip_bytes(two, **kw)
        self.put("data/pak0.pk3", data)
        self.release("2")
        del self.served.answers[:]
        return two, data


# ---- publishing ----------------------------------------------------------------------------------------------------

class Publishing(Base):
    def test_a_big_zip_gets_a_deterministic_index(self):
        data = zip_bytes(textures())
        self.put("data/pak0.pk3", data)
        res = self.release("1")
        e = self.manifest().files["data/pak0.pk3"]
        self.assertIsNotNone(e.parts)
        index = read(os.path.join(self.store, "parts", e.parts))
        self.assertEqual(hashlib.sha256(index).hexdigest(), e.parts)                    # named by its own SHA-256
        text = zlib.decompress(index)
        doc = json.loads(text)
        self.assertEqual(text, json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii"))
        self.assertEqual((doc["format"], doc["kind"], doc["file"], doc["size"]), (1, "zip", e.sha256, len(data)))
        segs = parts.read_index(index, e)
        self.assertEqual(sum(s[2] for s in segs), len(data))                            # every byte exactly once
        # the file again from its segments: literals as they are, each piece from the file and checked
        self.assertEqual(b"".join(p if k == "l" else data[o:o + n] for k, o, n, p in segs), data)
        pieces = [s for s in segs if s[0] == "p"]
        for _k, o, n, p in pieces:
            self.assertEqual(hashlib.sha256(data[o:o + n]).hexdigest(), p)
            self.assertGreaterEqual(n, parts.PIECE_MIN)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            big = [i for i in z.infolist() if i.compress_size >= parts.PIECE_MIN]
        self.assertEqual(sorted(o for _k, o, _n, _p in pieces), sorted(data_offset(data, i.filename) for i in big))
        kinds = [s[0] for s in segs]
        self.assertNotIn(("l", "l"), list(zip(kinds, kinds[1:])))                        # neighbouring literals joined
        self.assertIn("1 with a parts index", res.summary())
        # the same file in another store: the same index, byte for byte
        self.release("1", store=self.p("store2"))
        self.assertEqual(self.manifest(self.p("store2")).files["data/pak0.pk3"].parts, e.parts)
        self.assertEqual(read(os.path.join(self.p("store2"), "parts", e.parts)), index)
        # publishing again reads no archive: the store remembers which blobs have an index
        with mock.patch.object(parts, "make_index", side_effect=AssertionError("an archive was read again")):
            self.release("2")
        self.assertEqual(self.manifest().files["data/pak0.pk3"].parts, e.parts)

    def test_what_gets_no_index(self):
        self.put("fake.pk3", os.urandom(parts.PARTS_MIN + 1000))                           # named .pk3, not a zip
        small = zip_bytes(textures(n=4))
        self.assertLess(len(small), parts.PARTS_MIN)
        self.put("small.pk3", small)
        self.put("tiny.zip", zip_bytes([("a.txt", b"a", False, WHEN)]))
        res = self.release("1")
        man = self.manifest()
        self.assertEqual([p for p, e in man.files.items() if e.parts], [])
        folder = os.path.join(self.store, "parts")
        self.assertFalse(os.path.isdir(folder) and os.listdir(folder))
        self.assertEqual((res.warnings, res.parts), ([], 0))                              # none of them is warned about
        self.assertNotIn("parts index", res.summary())
        self.assertNotIn("parts", man.to_json().decode("utf-8"))

    def test_every_layout_zipfile_reads(self):
        m = textures()
        layouts = {"descriptors.pk3": zip_bytes(m, descriptors=True), "zip64.pk3": zip_bytes(m, zip64=True),
                   "stub.exe": b"MZ" + os.urandom(5000) + zip_bytes(m),
                   "all.pk3": os.urandom(300) + zip_bytes(changed(m, "scripts/s0.txt"), descriptors=True, zip64=True)}
        with zipfile.ZipFile(io.BytesIO(layouts["descriptors.pk3"])) as z:
            self.assertTrue(all(i.flag_bits & 0x08 for i in z.infolist()))                # a data descriptor each
        data = layouts["zip64.pk3"]
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("textures/t00.tga")
        local_extra = struct.unpack("<H", data[info.header_offset + 28:info.header_offset + 30])[0]
        self.assertNotEqual(local_extra, len(info.extra))                                   # the local extra differs
        self.assertIn(b"PK\x06\x06", data[-200:])                                           # the zip64 end record
        self.assertIn(b"PK\x06\x07", data[-200:])                                           # and its locator
        for name, data in layouts.items():
            self.put(name, data)
        self.release("1")
        man = self.manifest()
        for name, data in layouts.items():
            e = man.files[name]
            self.assertIsNotNone(e.parts, name)
            segs = parts.read_index(read(os.path.join(self.store, "parts", e.parts)), e)
            self.assertEqual(b"".join(p if k == "l" else data[o:o + n] for k, o, n, p in segs), data, name)
            pieces = {p for k, _o, _n, p in segs if k == "p"}
            for member, content, deflated, _w in m:
                if not deflated:                                                            # a stored member's piece
                    self.assertIn(hashlib.sha256(content).hexdigest(), pieces, (name, member))   # is its data

    def test_a_stop_while_indexing_leaves_the_store_as_it_was(self):
        self.put("data/pak0.pk3", zip_bytes(textures()))
        stop = threading.Event()
        stop.set()
        with self.assertRaises(publish.PublishError) as cm:
            publish.publish(self.src, self.store, "Game", "1", cancel=stop)
        self.assertEqual(str(cm.exception), publish.STOPPED)
        self.assertFalse(os.path.exists(os.path.join(self.store, "manifest.json")))
        self.release("1")                                                           # nothing was remembered of it
        self.assertIsNotNone(self.manifest().files["data/pak0.pk3"].parts)

    def test_overlapping_members_get_no_index_and_the_warning(self):
        data = bytearray(zip_bytes(textures()))
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as z:
            start = z.start_dir
            first = z.infolist()[0].header_offset
        # the second entry of the central directory points at the first member's local header
        pos = start
        pos += 46 + sum(struct.unpack("<HHH", bytes(data[pos + 28:pos + 34])))
        data[pos + 42:pos + 46] = struct.pack("<I", first)
        self.put("data/broken.pk3", bytes(data))
        res = self.release("1")
        self.assertIsNone(self.manifest().files["data/broken.pk3"].parts)
        self.assertEqual(res.warnings, ["Warning: data/broken.pk3 (%s) is sent whole whenever it changes: its files "
                                        "overlap or are not where its directory says." % publish.human(len(data))])

    def small_files(self):
        """data/sounds.pk3 in the release folder: 9,966 freshly packed members of 900 bytes, none overlapping, every one
        under 1 KiB. Its bytes."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            i = 0
            while buf.tell() < parts.PARTS_MIN + (1 << 20):
                z.writestr("sounds/s%05d.wav" % i, os.urandom(900))
                i += 1
        self.assertEqual(i, 9966)
        data = buf.getvalue()
        self.put("data/sounds.pk3", data)
        return data

    def test_a_zip_of_small_files_says_why_and_never_to_pack_it_as_a_zip(self):
        data = self.small_files()
        said = []
        res = self.release("1", log=said.append)
        line = ("Warning: data/sounds.pk3 (%s) is sent whole whenever it changes: 100%% of it would travel inside its "
                "index, more than a quarter: its files under 1 KiB (89%%) and the bytes outside its files (11%%)."
                % publish.human(len(data)))
        self.assertEqual((res.warnings, [s for s in said if "is sent whole" in s]), ([line], [line]))
        self.assertNotIn("pack it", line)
        with mock.patch.object(parts, "make_index", side_effect=AssertionError("read again")):
            self.assertEqual(self.release("2").warnings, [line])                    # the reason is remembered

    def test_the_store_keeps_a_reason_as_a_code_and_says_it_in_the_words_of_now(self):
        data = self.small_files()
        self.release("1")
        cache = os.path.join(self.store, parts.CACHE_NAME)
        sha = self.manifest().files["data/sounds.pk3"].sha256
        small = 9966 * 900
        code = "literal:%d:%d:%d" % (small, len(data) - small, len(data))
        self.assertEqual(json.loads(read(cache))["blobs"], {sha: code})               # its numbers, not its words
        # other words for it: said without reading the archive again
        with mock.patch.object(parts, "_too_much", return_value="other words"), \
                mock.patch.object(parts, "make_index", side_effect=AssertionError("read again")):
            self.assertEqual(self.release("2").warnings, ["Warning: data/sounds.pk3 (%s) is sent whole whenever it "
                                                          "changes: other words." % publish.human(len(data))])
        # a reason kept in words, as this work first kept it: read as no reason, and the archive is read again
        doc = json.loads(read(cache))
        doc["blobs"][sha] = "most of it is files under 1 KiB, which travel whole"
        write(cache, json.dumps(doc).encode("utf-8"))
        with mock.patch.object(parts, "make_index", wraps=parts.make_index) as made:
            res = self.release("3")
        self.assertEqual(made.call_count, 1)
        self.assertTrue(res.warnings[0].endswith(": 100% of it would travel inside its index, more than a quarter: its "
                                                 "files under 1 KiB (89%) and the bytes outside its files (11%)."))
        self.assertEqual(json.loads(read(cache))["blobs"], {sha: code})
        self.assertEqual([parts.is_reason(c) for c in (code, "layout", "literal:1:2:0", "layout\n", "most of it")],
                         [True, True, False, False, False])

    def test_why_an_index_would_not_stay_small_in_true_words(self):
        # both what travels inside an index and the limit it broke: a quarter of the file, or 48 MiB when that is less
        mb = 1 << 20
        self.assertEqual(parts._too_much(41 * mb, 9 * mb, 1 << 30),
                         "50.0 MiB of it would travel inside its index, more than 48 MiB: its files under 1 KiB "
                         "(41.0 MiB) and the bytes outside its files (9.0 MiB)")
        self.assertEqual(parts._too_much(20 * mb, 21 * mb, 100 * mb),
                         "41% of it would travel inside its index, more than a quarter: its files under 1 KiB (20%) "
                         "and the bytes outside its files (21%)")
        self.assertTrue(parts._too_much(0, 25 * mb + 1, 100 * mb).startswith("26% of it"))   # never the limit itself
        self.assertTrue(parts._too_much(48 * mb + 1, 0, 1 << 30).startswith("48.1 MiB of it"))   # nor 48 MiB
        self.assertEqual(parts.reason(parts.WHY_PIECES), "it has more files than an index can hold: at most 1,000,000 "
                                                         "parts, about two for each file (so about 500,000 files)")
        self.assertEqual(parts.reason("literal:%d:%d:%d" % (20 * mb, 21 * mb, 100 * mb)),
                         parts._too_much(20 * mb, 21 * mb, 100 * mb))
        self.assertEqual(parts.reason("most of it"), "")
        self.assertFalse(hasattr(parts, "WHY_NOTHING"))

    def test_an_archive_that_cannot_be_read_is_one_plain_line_and_asked_again(self):
        self.put("data/pak0.pk3", zip_bytes(textures()))
        said = []
        with mock.patch.object(parts, "make_index", side_effect=OSError(5, "Input/output error")):
            res = self.release("1", log=said.append)
        self.assertIn("could not read data/pak0.pk3 to index it (Input/output error): players get it whole this time",
                      said)
        self.assertEqual(res.warnings, [])
        self.assertFalse([s for s in said if s.startswith("Warning:")])
        self.assertIsNone(self.manifest().files["data/pak0.pk3"].parts)
        self.put("readme.txt", "two\n")
        self.release("2")                                                           # nothing was remembered of it
        self.assertIsNotNone(self.manifest().files["data/pak0.pk3"].parts)

    def test_publish_removes_only_indexes_from_parts(self):
        self.put("data/pak0.pk3", zip_bytes(textures()))
        self.release("1")
        folder = os.path.join(self.store, "parts")
        write(os.path.join(folder, "web.config"), b"<configuration/>")             # IIS's, put there by hand
        write(os.path.join(folder, "e" * 64), b"an index no release names")
        write(os.path.join(folder, "f" * 64 + ".tmp"), b"what a stopped publish left")
        self.put("readme.txt", "two\n")
        self.release("2")
        self.assertEqual(sorted(os.listdir(folder)), sorted([self.manifest().files["data/pak0.pk3"].parts,
                                                             "web.config"]))


# ---- updating ------------------------------------------------------------------------------------------------------

class Updating(Base):
    def test_only_the_changed_members_travel(self):
        _two, data = self.two_releases()
        plan = client.update(self.served.url, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)                           # byte for byte
        blob = self.served.of("blobs")
        self.assertEqual([(a[2], a[3]) for a in blob], [(206, TEXTURE)])                  # one range: the texture
        self.assertEqual(blob[0][1], "bytes=%d-%d" % (data_offset(data, "textures/t03.tga"),
                                                      data_offset(data, "textures/t03.tga") + TEXTURE - 1))
        self.assertEqual([a[2] for a in self.served.of("parts")], [200])                  # and its index, once
        self.assertEqual((len(plan.rebuilds), plan.fetch_bytes()), (1, TEXTURE))
        self.assertEqual(sum(plan.download().values()), len(data))
        state = os.path.join(self.game, ".deltapatcher")
        self.assertTrue(os.path.isfile(os.path.join(state, "members.json")))
        self.assertFalse(os.path.exists(os.path.join(state, "parts")))                    # no plan needs it now
        self.assertEqual(os.listdir(os.path.join(state, "cache")), [])
        client.rollback(self.game)                                                           # the old one is kept
        self.assertNotEqual(self.installed("data/pak0.pk3"), data)

    def test_timestamps_and_order_that_change_while_the_data_does_not(self):
        one = textures()
        self.put("data/pak0.pk3", zip_bytes(one))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        two = [(n, d, z, LATER) for n, d, z, _w in reversed(changed(one, "textures/t08.tga"))]
        data = zip_bytes(two)
        self.put("data/pak0.pk3", data)
        self.release("2")
        del self.served.answers[:]
        client.update(url, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual([(a[2], a[3]) for a in self.served.of("blobs")], [(206, TEXTURE)])

    def test_a_member_that_moved_to_another_archive_is_taken_from_there(self):
        a = textures()
        b = [("maps/m%d.bsp" % i, os.urandom(500 * KB), False, WHEN) for i in range(3)]
        self.put("data/a.pk3", zip_bytes(a))
        self.put("data/b.pk3", zip_bytes(b))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        # m1.bsp moves from b.pk3 into a.pk3; b.pk3 (small: no index) keeps the other two
        new_a = zip_bytes(a[:5] + [b[1]] + a[5:])
        new_b = zip_bytes([b[0], b[2]])
        self.put("data/a.pk3", new_a)
        self.put("data/b.pk3", new_b)
        self.release("2")
        del self.served.answers[:]
        plan = client.update(url, self.game)
        self.assertEqual((self.installed("data/a.pk3"), self.installed("data/b.pk3")), (new_a, new_b))
        man = self.manifest()
        self.assertEqual(list(plan.rebuilds), [man.files["data/a.pk3"].sha256])
        # nothing of a.pk3's blob travels: every piece was here; b.pk3 comes whole
        self.assertEqual([a_[0].rsplit("/", 1)[-1] for a_ in self.served.of("blobs")], [man.files["data/b.pk3"].sha256])
        self.assertEqual(plan.fetch_bytes(), len(new_b))

    def test_deflated_members_descriptors_zip64_and_a_stub(self):
        stub = os.urandom(777)
        one = textures()
        self.put("bin/game.exe", stub + zip_bytes(one, descriptors=True, zip64=True))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        two = changed(one, "scripts/s1.txt")
        body = zip_bytes(two, descriptors=True, zip64=True)
        data = stub + body
        self.put("bin/game.exe", data)
        self.release("2")
        del self.served.answers[:]
        client.update(url, self.game)
        self.assertEqual(self.installed("bin/game.exe"), data)
        self.assertEqual([(a[2], a[3]) for a in self.served.of("blobs")],
                         [(206, compressed_size(body, "scripts/s1.txt"))])                # the deflated script only

    def test_two_changes_far_apart_are_two_ranges_and_near_ones_one(self):
        one = textures()
        self.put("data/pak0.pk3", zip_bytes(one))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        data = zip_bytes(changed(one, "textures/t01.tga", "textures/t09.tga"))
        self.put("data/pak0.pk3", data)
        self.release("2")
        del self.served.answers[:]
        client.update(url, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual([(a[2], a[3]) for a in self.served.of("blobs")], [(206, TEXTURE), (206, TEXTURE)])
        # two neighbours: one range, with what is between them (a local header)
        two = textures()
        self.put("data/pak0.pk3", zip_bytes(two))
        self.release("3")
        client.update(url, self.game)
        data = zip_bytes(changed(two, "textures/t04.tga", "textures/t05.tga"))
        self.put("data/pak0.pk3", data)
        self.release("4")
        del self.served.answers[:]
        client.update(url, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        gap = data_offset(data, "textures/t05.tga") - data_offset(data, "textures/t04.tga") - TEXTURE
        self.assertEqual([(a[2], a[3]) for a in self.served.of("blobs")], [(206, 2 * TEXTURE + gap)])

    def test_the_swarm_gets_only_what_is_still_missing(self):
        one = textures()
        self.put("data/pak0.pk3", zip_bytes(one))
        self.put("readme.txt", "one\n")
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        data = zip_bytes(changed(one, "textures/t03.tga"))
        self.put("data/pak0.pk3", data)
        self.put("readme.txt", "two\n")
        self.release("2")
        asked = []

        class Swarm(object):
            """The BitTorrent side of an update with an address (torrent/session.py AddressTorrent), stood in for:
            it brings nothing, so the address brings the rest."""

            def fetch(self, src, need, cdir, progress, cancel, raw=None, signer=None):
                asked.append(dict(need))

            def needs_keep(self, raw):
                return False

            def keep(self, src, raw, sig, signer):
                pass

        client.update(url, self.game, torrent=Swarm())
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual(asked, [{self.manifest().files["readme.txt"].sha256: 4}])     # not the rebuilt archive

    def test_a_folder_source_never_rebuilds(self):
        one = textures()
        self.put("data/pak0.pk3", zip_bytes(one))
        self.release("1")
        client.update(self.store, self.game)
        data = zip_bytes(changed(one, "textures/t03.tga"))
        self.put("data/pak0.pk3", data)
        self.release("2")
        plan = client.update(self.store, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual((plan.rebuilds, plan.fetch_bytes()), ({}, len(data)))

    def test_a_cut_download_that_lacks_no_more_than_the_rebuild_would_fetch_is_resumed(self):
        # six of thirteen textures changed: the rebuild fetches them; a whole download of it was cut before
        one = textures()
        self.put("data/pak0.pk3", zip_bytes(one))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        data = zip_bytes(changed(one, *["textures/t%02d.tga" % i for i in range(6)]))
        self.put("data/pak0.pk3", data)
        self.release("2")
        e = self.manifest().files["data/pak0.pk3"]
        planned = client.update(url, self.game, check_only=True)
        fetch = planned.fetch_bytes()
        self.assertEqual((len(planned.rebuilds), fetch < len(data) // 2), (1, True))
        part = os.path.join(self.game, ".deltapatcher", "cache", e.sha256 + ".part")
        # one byte more to fetch than the rebuild: it is rebuilt, and the cut download goes
        write(part, data[:len(data) - fetch - 1])
        plan = client.update(url, self.game)
        self.assertEqual((len(plan.rebuilds), plan.resumes, os.path.exists(part)), (1, {}, False))
        client.rollback(self.game)
        # 90% of it here: only the rest is fetched, counted as what the update downloads
        cut = len(data) * 9 // 10
        write(part, data[:cut])
        del self.served.answers[:]
        progress, said = [], []
        plan = client.update(url, self.game, progress=lambda d, t, label: progress.append((d, t, label)),
                             log=said.append)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual((plan.rebuilds, plan.resumes, plan.fetch_bytes()), ({}, {e.sha256: len(data) - cut},
                                                                             len(data) - cut))
        self.assertEqual([(a[1], a[2], a[3]) for a in self.served.of("blobs")],
                         [("bytes=%d-" % cut, 206, len(data) - cut)])                # the rest of it, once
        self.assertEqual([p[:2] for p in progress if p[2] == "downloading"][-1], (len(data) - cut, len(data) - cut))
        self.assertIn("data/pak0.pk3: a cut download of it lacks only %s: it is resumed, not rebuilt (a rebuild would "
                      "fetch %s)" % (publish.human(len(data) - cut), publish.human(fetch)), said)
        self.assertIn("(1 cut download resumed: %s whole)" % publish.human(len(data)), plan.summary())
        self.assertFalse(os.path.exists(part))

    def test_the_swarm_is_not_asked_for_an_archive_whose_cut_download_resumes(self):
        one = textures()
        self.put("data/pak0.pk3", zip_bytes(one))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        data = zip_bytes(changed(one, *["textures/t%02d.tga" % i for i in range(6)]))
        self.put("data/pak0.pk3", data)
        self.release("2")
        e = self.manifest().files["data/pak0.pk3"]
        cut = len(data) * 9 // 10
        write(os.path.join(self.game, ".deltapatcher", "cache", e.sha256 + ".part"), data[:cut])
        asked = []
        store = self.store

        class Swarm(object):
            """The BitTorrent side of an update with an address, stood in for: its peers bring whole what it is asked
            for."""

            def fetch(self, src, need, cdir, progress, cancel, raw=None, signer=None):
                asked.append(dict(need))
                for sha in need:
                    write(os.path.join(cdir, sha), read(os.path.join(store, "blobs", sha)))

            def needs_keep(self, raw):
                return False

            def keep(self, src, raw, sig, signer):
                pass

        del self.served.answers[:]
        plan = client.update(url, self.game, torrent=Swarm())
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual((plan.resumes, asked), ({e.sha256: len(data) - cut}, []))   # nothing of it from the swarm
        self.assertEqual([(a[1], a[2], a[3]) for a in self.served.of("blobs")],
                         [("bytes=%d-" % cut, 206, len(data) - cut)])                # the rest, from the address

    def test_a_rebuild_from_many_files_keeps_few_open(self):
        maps = [("maps/m%02d.bsp" % i, os.urandom(250 * KB), False, WHEN) for i in range(rebuild.MAX_OPEN + 8)]
        for i, m in enumerate(maps):
            self.put("data/m%02d.pk3" % i, zip_bytes([m]))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        data = zip_bytes(maps)                                                      # every piece of it is here
        self.put("data/all.pk3", data)
        self.release("2")
        del self.served.answers[:]
        opened = Opened(self.game)
        with mock.patch.object(rebuild, "open", opened, create=True):
            plan = client.update(url, self.game)
        self.assertEqual(self.installed("data/all.pk3"), data)
        self.assertEqual((list(plan.rebuilds), plan.fetch_bytes()), ([self.manifest().files["data/all.pk3"].sha256], 0))
        self.assertEqual(self.served.of("blobs"), [])
        self.assertEqual(len(set(opened.paths)), rebuild.MAX_OPEN + 8)              # read from 40 files
        self.assertEqual((opened.most, opened.now), (rebuild.MAX_OPEN, set()))      # at most 32 open at once

    def test_the_files_a_player_keeps_as_their_own_are_never_opened(self):
        one = textures()
        maps = [("maps/m%d.bsp" % i, os.urandom(500 * KB), False, WHEN) for i in range(2)]
        self.put("data/pak0.pk3", zip_bytes(one))
        self.put("data/own.pk3", zip_bytes([maps[0]]))
        self.put("data/old.pk3", zip_bytes([maps[1]]))
        publish.publish(self.src, self.store, "Game", "1", protect=["/data/own.pk3"])
        url = self.serve()
        client.update(url, self.game)
        # the player edits both: own.pk3 the release protects, and old.pk3, which the next release drops (kept as
        # edited); each still holds a map the next pak0.pk3 has
        mine = ("mine.txt", b"mine\n", False, WHEN)
        write(os.path.join(self.game, "data", "own.pk3"), zip_bytes([maps[0], mine]))
        write(os.path.join(self.game, "data", "old.pk3"), zip_bytes([maps[1], mine]))
        data = zip_bytes(one + maps)
        self.put("data/pak0.pk3", data)
        os.remove(self.p("src", "data", "old.pk3"))
        publish.publish(self.src, self.store, "Game", "2", protect=["/data/own.pk3"])
        del self.served.answers[:]
        opened = Opened(self.game)
        with mock.patch.object(rebuild, "open", opened, create=True):
            plan = client.update(url, self.game)
        self.assertEqual((plan.protected, plan.kept_changed), (["data/own.pk3"], ["data/old.pk3"]))
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual(len(plan.rebuilds), 1)
        self.assertEqual(sorted(set(os.path.basename(p) for p in opened.paths)), ["pak0.pk3"])   # never theirs
        start = data_offset(data, "maps/m0.bsp")
        self.assertEqual([(a[1], a[2]) for a in self.served.of("blobs")],                 # both maps are fetched
                         [("bytes=%d-%d" % (start, data_offset(data, "maps/m1.bsp") + 500 * KB - 1), 206)])
        members = json.loads(read(os.path.join(self.game, ".deltapatcher", rebuild.MEMBERS_NAME)))
        self.assertEqual(sorted(members["files"]), ["data/pak0.pk3"])

    def test_a_folder_that_became_a_link_before_the_rebuild_gives_its_pieces_to_fetch(self):
        one = textures()
        maps = [("maps/m%d.bsp" % i, os.urandom(500 * KB), False, WHEN) for i in range(2)]
        self.put("data/pak0.pk3", zip_bytes(one))
        self.put("data2/maps.pk3", zip_bytes(maps))
        link_folder(self, self.p("try"), self.p("src"))                            # links to folders can be made here
        unlink_folder(self.p("try"))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        data = zip_bytes(one + [maps[1]])                                           # a map of maps.pk3 joins pak0.pk3
        self.put("data/pak0.pk3", data)
        self.release("2")
        del self.served.answers[:]
        here, away = os.path.join(self.game, "data2"), self.p("outside", "data2")
        run = rebuild.run

        def run_through_a_link(*args):
            # between the plan and the rebuild, data2 becomes a link to a folder outside the game folder: locate()
            # refuses its files, as it refuses any path through a link
            os.makedirs(os.path.dirname(away))
            os.rename(here, away)
            link_folder(self, here, away)
            try:
                return run(*args)
            finally:
                unlink_folder(here)
                os.rename(away, here)

        with mock.patch.object(rebuild, "run", run_through_a_link):
            plan = client.update(url, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual(len(plan.rebuilds), 1)                                     # rebuilt: not downloaded whole
        start = data_offset(data, "maps/m1.bsp")
        self.assertEqual([(a[1], a[2]) for a in self.served.of("blobs")],          # the map is fetched
                         [("bytes=%d-%d" % (start, start + 500 * KB - 1), 206)])
        self.assertEqual(plan.fetch_bytes(), 500 * KB)


# ---- every way a rebuild falls back: always the right file ---------------------------------------------------------

class Fallbacks(Base):
    def whole(self):
        """The release's pak0.pk3 came whole: one 200 for its blob, no range."""
        sha = self.manifest().files["data/pak0.pk3"].sha256
        self.assertEqual([(a[0], a[1], a[2]) for a in self.served.of("blobs")], [("/blobs/" + sha, None, 200)])

    def test_a_server_that_ignores_range(self):
        _two, data = self.two_releases()
        self.serve(ignore_range=True)
        plan = client.update(self.served.url, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual(plan.rebuilds, {})
        blob = self.served.of("blobs")
        self.assertEqual([(a[1] is not None, a[2]) for a in blob], [(True, 200), (False, 200)])   # refused, then whole
        self.assertEqual(os.listdir(os.path.join(self.game, ".deltapatcher", "cache")), [])

    def test_other_bytes_than_those_asked_for(self):
        _two, data = self.two_releases()
        self.serve(wrong_range=True)
        client.update(self.served.url, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual([a[2] for a in self.served.of("blobs")], [206, 200])

    def test_a_piece_here_that_changed_after_it_was_read(self):
        _two, data = self.two_releases()
        self.assertEqual(client.update(self.served.url, self.game, check_only=True).fetch_bytes(), TEXTURE)
        # an unchanged texture of the installed archive changes in place, its size and time as they were
        path = os.path.join(self.game, "data", "pak0.pk3")
        st = os.stat(path)
        old = bytearray(read(path))
        old[data_offset(bytes(old), "textures/t09.tga") + 1000] ^= 0xFF
        write(path, bytes(old))
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        del self.served.answers[:]
        plan = client.update(self.served.url, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual([(a[2], a[3]) for a in self.served.of("blobs")], [(206, TEXTURE), (206, TEXTURE)])
        self.assertEqual(plan.fetch_bytes(), 2 * TEXTURE)

    def test_a_tampered_index(self):
        _two, data = self.two_releases()
        e = self.manifest().files["data/pak0.pk3"]
        write(os.path.join(self.store, "parts", e.parts), zlib.compress(b'{"format": 1}'))
        plan = client.update(self.served.url, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual(plan.rebuilds, {})
        self.whole()

    def test_an_index_whose_segments_do_not_tile(self):
        _two, data = self.two_releases()
        man = self.manifest()
        e = man.files["data/pak0.pk3"]
        doc = json.loads(zlib.decompress(read(os.path.join(self.store, "parts", e.parts))))
        doc["segments"] = doc["segments"][:-1]                                     # the central directory is gone
        index = zlib.compress(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("ascii"), 9)
        bad = hashlib.sha256(index).hexdigest()
        write(os.path.join(self.store, "parts", bad), index)
        files = [m for p, m in man.files.items() if p != e.path] + [Entry(e.path, e.sha256, e.size, bad)]
        write(os.path.join(self.store, "manifest.json"),
              Manifest(man.name, man.version, man.published, files).to_json())
        plan = client.update(self.served.url, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual(plan.rebuilds, {})
        self.whole()

    def test_more_than_seventy_percent_to_fetch_comes_whole(self):
        one = textures()
        self.put("data/pak0.pk3", zip_bytes(one))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        data = zip_bytes(changed(one, *["textures/t%02d.tga" % i for i in range(10)]))
        self.put("data/pak0.pk3", data)
        self.release("2")
        del self.served.answers[:]
        plan = client.update(url, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual((plan.rebuilds, plan.fetch_bytes()), ({}, len(data)))
        self.whole()

    def test_a_stop_mid_rebuild_then_the_next_update(self):
        _two, data = self.two_releases()
        before = snapshot(self.game)
        stop = threading.Event()
        self.serve(on_range=stop.set)
        with self.assertRaises(SourceError) as cm:
            client.update(self.served.url, self.game, cancel=stop)
        self.assertEqual(str(cm.exception), "stopped")
        self.assertEqual(snapshot(self.game), before)                               # the folder as it was
        cache = os.path.join(self.game, ".deltapatcher", "cache")
        self.assertEqual([n for n in os.listdir(cache) if n.endswith(rebuild.REBUILD_SUFFIX)], [])
        self.serve()
        client.update(self.served.url, self.game)                                   # it starts again
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual([(a[2], a[3]) for a in self.served.of("blobs")], [(206, TEXTURE)])

    def test_a_rebuild_a_crash_left_is_never_taken_for_a_download(self):
        _two, data = self.two_releases()
        e = self.manifest().files["data/pak0.pk3"]
        cache = os.path.join(self.game, ".deltapatcher", "cache")
        write(os.path.join(cache, e.sha256 + rebuild.REBUILD_SUFFIX), os.urandom(len(data)))
        write(os.path.join(self.store, "parts", e.parts), b"gone")
        client.update(self.served.url, self.game)                                   # no index: the whole download
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual(os.listdir(cache), [])

    def test_an_index_cut_on_its_way_is_fetched_again_never_refused(self):
        # the server says the index's Content-Length, sends half of it and closes: that is a cut, not a wrong index
        _two, data = self.two_releases()
        cut = threading.Event()
        cut.set()
        self.serve(cut_parts=cut)
        e = self.manifest().files["data/pak0.pk3"]
        record = os.path.join(self.game, ".deltapatcher", rebuild.PARTS_DIR, e.parts + rebuild.REFUSED_SUFFIX)
        said = []
        with mock.patch.object(source.time, "sleep"):                              # between the tries
            plan = client.update(self.served.url, self.game, check_only=True, log=said.append)
        self.assertEqual((plan.rebuilds, plan.fetch_bytes()), ({}, len(data)))
        self.assertEqual(len(self.served.of("parts")), Source(self.served.url).tries)   # tried again, as any cut
        self.assertTrue([s for s in said if s.startswith("the parts index of data/pak0.pk3 could not be read (")])
        self.assertFalse([s for s in said if "refused" in s])
        self.assertFalse(os.path.exists(record))
        cut.clear()                                                                 # whole answers from now on
        plan = client.update(self.served.url, self.game, check_only=True)
        self.assertEqual((len(plan.rebuilds), plan.fetch_bytes()), (1, TEXTURE))
        del self.served.answers[:]
        client.update(self.served.url, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual([(a[2], a[3]) for a in self.served.of("blobs")], [(206, TEXTURE)])

    def test_a_damaged_cut_download_is_fetched_again_whole_before_the_source_is_blamed(self):
        _two, data = self.two_releases()
        e = self.manifest().files["data/pak0.pk3"]
        fetch = client.update(self.served.url, self.game, check_only=True).fetch_bytes()
        part = os.path.join(self.game, ".deltapatcher", "cache", e.sha256 + ".part")
        have = len(data) - fetch
        write(part, os.urandom(have))                       # as long as a resume needs, and damaged
        said, progress = [], []
        del self.served.answers[:]
        plan = client.update(self.served.url, self.game, log=said.append,
                             progress=lambda d, t, label: progress.append((d, t, label)))
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual(plan.resumes, {e.sha256: fetch})
        self.assertIn("the cut download of data/pak0.pk3 was damaged: it is downloaded again, whole", said)
        self.assertEqual([(a[1], a[2], a[3]) for a in self.served.of("blobs")],
                         [("bytes=%d-" % have, 206, fetch), (None, 200, len(data))])
        self.assertEqual([p[:2] for p in progress if p[2] == "downloading"][-1], (fetch + len(data),) * 2)
        # a blob that is wrong at the source too: the update fails as it always has, after that one more try
        client.rollback(self.game)
        blob = os.path.join(self.store, "blobs", e.sha256)
        bad = bytearray(read(blob))
        bad[-100] ^= 1
        write(blob, bytes(bad))
        write(part, data[:have])
        del self.served.answers[:]
        with self.assertRaises(client.UpdateError) as cm:
            client.update(self.served.url, self.game)
        self.assertRegex(str(cm.exception), r"\Ablob %s from the source does not match its SHA-256 \(got [0-9a-f]{12}, "
                                            r"%d bytes\)\Z" % (e.sha256[:12], len(data)))
        self.assertEqual([a[2] for a in self.served.of("blobs")], [206, 200])

    def test_a_rebuild_a_killed_update_left_never_refuses_the_next_one(self):
        # a nearly full drive: too full while the leftover takes its bytes, room enough without it
        _two, data = self.two_releases()
        e = self.manifest().files["data/pak0.pk3"]
        left = os.path.join(self.game, ".deltapatcher", "cache", e.sha256 + rebuild.REBUILD_SUFFIX)
        write(left, os.urandom(len(data)))                  # what a killed rebuild leaves: the whole size
        need = client._space_needed(len(data))
        real = client.shutil.disk_usage(self.game)

        def usage(path):
            return real._replace(free=need - 1 + (0 if os.path.exists(left) else len(data)))

        with mock.patch.object(client.shutil, "disk_usage", usage):
            client.update(self.served.url, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertFalse(os.path.exists(left))

    def test_a_plan_keeps_its_indexes_within_its_caps(self):
        a, b = textures(), textures()
        self.put("data/a.pk3", zip_bytes(a))
        self.put("data/b.pk3", zip_bytes(b))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        self.put("data/a.pk3", zip_bytes(changed(a, "textures/t03.tga")))
        self.put("data/b.pk3", zip_bytes(changed(b, "textures/t04.tga")))
        self.release("2")
        man = self.manifest()
        ea, eb = man.files["data/a.pk3"], man.files["data/b.pk3"]
        got = [parts.read_index(read(os.path.join(self.store, "parts", e.parts)), e, sized=True) for e in (ea, eb)]
        segments, json_bytes = sum(len(s) for s, _n in got), sum(n for _s, n in got)
        pdir = os.path.join(self.game, ".deltapatcher", rebuild.PARTS_DIR)
        for limit, value in (("PLAN_SEGMENTS_MAX", segments - 1), ("PLAN_INDEX_MAX", json_bytes - 1)):
            said = []
            with mock.patch.object(rebuild, limit, value):
                plan = client.update(url, self.game, check_only=True, log=said.append)
                line = ("data/b.pk3: its parts index would take this update past %d MiB or %s segments of indexes: it "
                        "comes whole" % (rebuild.PLAN_INDEX_MAX >> 20, format(rebuild.PLAN_SEGMENTS_MAX, ",")))
            self.assertEqual(list(plan.rebuilds), [ea.sha256], limit)
            self.assertEqual([s for s in said if "would take this update past" in s], [line])   # one line
            self.assertEqual(sorted(os.listdir(pdir)), sorted([ea.parts, eb.parts]))   # kept: read here next time
        self.assertEqual(len(self.served.of("parts")), 2)                           # each fetched once in all
        self.assertEqual((rebuild.PLAN_INDEX_MAX, rebuild.PLAN_SEGMENTS_MAX, rebuild.PLAN_FETCH_MAX),
                         (256 << 20, parts.SEGMENTS_MAX, 256 << 20))
        plan = client.update(url, self.game, check_only=True)                       # within them: both
        self.assertEqual(sorted(plan.rebuilds), sorted([ea.sha256, eb.sha256]))

    def three_archives(self):
        """Release 1 of data/a.pk3, b.pk3 and c.pk3 installed from the served store, then release 2 with a texture of
        each changed published: its manifest."""
        members = [textures() for _ in range(3)]
        for name, m in zip("abc", members):
            self.put("data/%s.pk3" % name, zip_bytes(m))
        self.release("1")
        url = self.serve()
        client.update(url, self.game)
        for name, m in zip("abc", members):
            self.put("data/%s.pk3" % name, zip_bytes(changed(m, "textures/t03.tga")))
        self.release("2")
        del self.served.answers[:]
        return self.manifest()

    def test_once_the_caps_are_reached_no_more_index_is_fetched_now_or_later(self):
        # the plan may keep the first archive's index and no more: the other two come whole, their indexes unread,
        # and the checks after it fetch nothing
        man = self.three_archives()
        ea = man.files["data/a.pk3"]
        _segs, json_a = parts.read_index(read(os.path.join(self.store, "parts", ea.parts)), ea, sized=True)
        parsed = []
        real = parts.read_index

        def read_index(data, entry, sized=False):
            parsed.append(entry.path)
            return real(data, entry, sized)

        with mock.patch.object(rebuild, "PLAN_INDEX_MAX", json_a), mock.patch.object(parts, "read_index", read_index):
            for _ in range(3):
                said = []
                plan = client.update(self.served.url, self.game, check_only=True, log=said.append)
                self.assertEqual([rb.entry.path for rb in plan.rebuilds.values()], ["data/a.pk3"])
                self.assertIn("data/b.pk3 and 1 more archive come whole: this update has all the parts indexes it may "
                              "read", said)
                self.assertEqual(len(self.served.of("parts")), 1)                   # a.pk3's, the first time only
        self.assertEqual(parsed, ["data/a.pk3"] * 3)

    def test_a_plan_fetches_at_most_its_share_of_index_bytes(self):
        # a.pk3's index is refused (its bytes still count), b.pk3's would take the plan past the cap: no more of it is
        # read than the cap leaves, and c.pk3's is not asked for
        man = self.three_archives()
        ea, eb, ec = (man.files["data/%s.pk3" % n] for n in "abc")
        write(os.path.join(self.store, "parts", ea.parts), os.urandom(3000))
        index_b = os.path.getsize(os.path.join(self.store, "parts", eb.parts))
        cap = 3000 + index_b - 1
        asked = []
        real = Source.get_parts

        def get_parts(src, sha, limit=parts.INDEX_MAX):
            data = real(src, sha, limit)
            asked.append((sha, len(data)))
            return data

        said = []
        with mock.patch.object(rebuild, "PLAN_FETCH_MAX", cap), mock.patch.object(Source, "get_parts", get_parts):
            plan = client.update(self.served.url, self.game, check_only=True, log=said.append)
        self.assertEqual(plan.rebuilds, {})
        self.assertEqual(asked, [(ea.parts, 3000), (eb.parts, index_b - 1)])          # 3000 + index_b - 1 bytes
        self.assertEqual(sum(n for _sha, n in asked), cap)
        self.assertIn("data/b.pk3: its parts index would take this update past 0 MiB of indexes fetched: it comes "
                      "whole", said)
        self.assertIn("data/c.pk3 comes whole: this update has all the parts indexes it may read", said)
        self.assertNotIn(ec.parts, [sha for sha, _n in asked])

    def test_a_refused_index_is_fetched_once(self):
        _two, data = self.two_releases()
        e = self.manifest().files["data/pak0.pk3"]
        write(os.path.join(self.store, "parts", e.parts), zlib.compress(b'{"format": 1}'))
        record = os.path.join(self.game, ".deltapatcher", rebuild.PARTS_DIR, e.parts + rebuild.REFUSED_SUFFIX)
        said = []
        for _ in range(2):
            plan = client.update(self.served.url, self.game, check_only=True, log=said.append)
            self.assertEqual((plan.rebuilds, plan.fetch_bytes()), ({}, len(data)))
        self.assertEqual(len(self.served.of("parts")), 1)                           # the second check asks for none
        self.assertTrue(os.path.isfile(record))
        self.assertIn("the parts index of data/pak0.pk3 was refused before: it comes whole", said)
        # a release that names another index: the record goes, and the new index is fetched
        self.put("data/pak0.pk3", zip_bytes(changed(textures(), "textures/t01.tga")))
        self.release("3")
        e3 = self.manifest().files["data/pak0.pk3"]
        client.update(self.served.url, self.game, check_only=True)
        self.assertFalse(os.path.exists(record))
        self.assertEqual(os.listdir(os.path.dirname(record)), [e3.parts])

    def test_a_check_below_the_margin_for_keeping_an_index_fetches_none(self):
        _two, data = self.two_releases()
        e = self.manifest().files["data/pak0.pk3"]
        index = os.path.getsize(os.path.join(self.store, "parts", e.parts))
        need = client._space_needed(len(data))                                      # what the update keeps free
        state = os.path.join(self.game, ".deltapatcher")
        kept = os.path.join(state, rebuild.PARTS_DIR, e.parts)
        said = []
        with mock.patch.object(rebuild, "_free", return_value=need + index - 1):
            plan = client.update(self.served.url, self.game, check_only=True, log=said.append)
            self.assertEqual((plan.rebuilds, plan.fetch_bytes()), ({}, len(data)))   # the whole amounts
            self.assertEqual(self.served.of("parts"), [])                            # no index fetched
            self.assertIn("this check counts data/pak0.pk3 whole: the drive has too little free space to keep its "
                          "parts index (an update reads it as it runs)", said)
            plan = client.update(self.served.url, self.game)                        # the update rebuilds as ever
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual((len(plan.rebuilds), plan.fetch_bytes()), (1, TEXTURE))
        self.assertEqual(len(self.served.of("parts")), 1)
        self.assertFalse(os.path.exists(kept))                                     # used from memory
        # a check fetches only with room for the largest index after the update's free space; kept with room for it
        src = Source(self.served.url)
        with mock.patch.object(rebuild, "_free", return_value=need + parts.INDEX_MAX - 1):
            self.assertEqual(rebuild._index(src, e, os.path.dirname(kept), said.append, need, check=True),
                             (None, 0, "room"))
        with mock.patch.object(rebuild, "_free", return_value=need + index - 1):
            got, n, why = rebuild._index(src, e, os.path.dirname(kept), said.append, need)   # an update: fetched
        self.assertEqual((got is not None, n, why, os.path.exists(kept)), (True, index, "", False))
        with mock.patch.object(rebuild, "_free", return_value=need + parts.INDEX_MAX) as free:
            got, n, why = rebuild._index(src, e, os.path.dirname(kept), said.append, need, check=True)
        self.assertEqual((got is not None, n, why, os.path.isfile(kept)), (True, index, "", True))
        free.assert_called_with(state)


# ---- the check -----------------------------------------------------------------------------------------------------

class Checking(Base):
    def test_a_check_says_the_smaller_amount_and_reads_the_index_once(self):
        _two, data = self.two_releases()
        rc, ev = run_json(["update", self.game, "--source", self.served.url, "--check"])
        self.assertEqual(rc, 2, ev[-1])
        d = ev[-1]["data"]
        self.assertEqual((d["fetch_bytes"], d["download_bytes"], d["rebuilt"], d["download_blobs"]),
                         (TEXTURE, len(data), 1, 1))
        self.assertIn("to download: 1 blobs, %s (1 archive rebuilt from what is here: %s whole)"
                      % (publish.human(TEXTURE), publish.human(len(data))), d["summary"])
        self.assertEqual(len(self.served.of("parts")), 1)
        self.assertEqual(self.served.of("blobs"), [])                               # a check fetches no blob
        rc, ev = run_json(["update", self.game, "--source", self.served.url, "--check"])
        self.assertEqual((rc, ev[-1]["data"]["fetch_bytes"]), (2, TEXTURE))
        self.assertEqual(len(self.served.of("parts")), 1)                           # the second check: none again
        rc, ev = run_json(["update", self.game, "--source", self.served.url])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual((ev[-1]["data"]["fetch_bytes"], ev[-1]["data"]["rebuilt"]), (TEXTURE, 1))
        self.assertEqual(len(self.served.of("parts")), 1)                           # nor the update
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        progress = [e for e in ev if e["event"] == "progress" and e["label"] == "downloading"]
        self.assertEqual((progress[-1]["done"], progress[-1]["total"]), (TEXTURE, TEXTURE))   # bytes fetched, of those

    def test_update_all_says_the_smaller_amount(self):
        row = {"ok": True, "data": {"empty": False, "add": 0, "replace": 1, "remove": 0, "download_bytes": 9 << 20,
                                    "fetch_bytes": TEXTURE}}
        self.assertEqual(cli.outcome(row, True)[0], "an update is waiting: 0 new, 1 changed, %s to download"
                         % publish.human(TEXTURE))
        del row["data"]["fetch_bytes"]                                              # a result without it
        self.assertEqual(cli.outcome(row, False)[0], "updated: 0 new, 1 changed, 9.0 MB downloaded")


# ---- deploys, backups, statistics ----------------------------------------------------------------------------------

class Sending(Base):
    def releases(self, target, members, *changes):
        """A release of `members` for each of `changes` (the texture with new data in it, or None for none), each
        deployed to `target`: the index each one names."""
        out = []
        for change in changes:
            self.version = getattr(self, "version", 0) + 1
            self.put("data/pak0.pk3", zip_bytes(members if change is None else
                                                changed(members, "textures/t%02d.tga" % change)))
            self.release(str(self.version))
            self.assertEqual(deploy.deploy(self.store, target).parts_sent, 1)
            out.append(self.manifest().files["data/pak0.pk3"].parts)
        return out

    def test_deploy_sends_and_removes_parts_with_their_manifests(self):
        web = self.p("web")
        tgt = deploy.FolderTarget(web)
        indexes = self.releases(tgt, textures(), None, 0, 1)
        self.assertEqual(sorted(os.listdir(os.path.join(web, "parts"))), sorted(indexes))   # new, current, previous
        self.put("readme.txt", "four\n")
        self.release("4")                                                           # the archive of release three
        dry = deploy.deploy(self.store, tgt, dry_run=True)
        self.assertEqual((dry.parts_sent, dry.parts_removed), (0, 1))
        self.assertEqual(sorted(os.listdir(os.path.join(web, "parts"))), sorted(indexes))   # a dry run changes nothing
        r = deploy.deploy(self.store, tgt)
        self.assertEqual((r.parts_sent, r.parts_removed), (0, 1))                   # release one's: named by none now
        self.assertEqual(sorted(os.listdir(os.path.join(web, "parts"))), sorted(indexes[1:]))
        said = deploy.DeployResult()
        said.parts_sent, said.dry_run = 2, True
        self.assertTrue(said.summary().endswith("; would send 2 parts indexes"))

    def test_deploy_through_sftp_and_a_player_rebuilds_from_there(self):
        disk = self.p("server")
        os.makedirs(disk)
        fake = FakeSftp(disk)
        one = textures()
        with mock.patch.object(ssh, "sftp", fake):
            tgt = deploy.SftpTarget(ssh.Target("game.example.com", "deploy", 22, "", REMOTE))
            indexes = self.releases(tgt, one, None)
            srv = Served(disk)
            try:
                client.update(srv.url, self.game)
                indexes += self.releases(tgt, one, 5)
                del srv.answers[:]
                client.update(srv.url, self.game)
            finally:
                srv.close()
        self.assertEqual(self.installed("data/pak0.pk3"), read(self.p("src", "data", "pak0.pk3")))
        self.assertEqual([(a[2], a[3]) for a in srv.of("blobs")], [(206, TEXTURE)])
        self.assertEqual(sorted(os.listdir(os.path.join(disk, "parts"))), sorted(set(indexes)))
        for batch in fake.batches:                                                  # no local path ever reaches sftp
            for line in batch:
                self.assertNotIn(self.tmp.replace("\\", "/"), line)
                self.assertNotIn("\\", line)

    def test_a_missing_index_is_not_sent_and_the_release_still_goes(self):
        self.put("data/pak0.pk3", zip_bytes(textures()))
        self.release("1")
        e = self.manifest().files["data/pak0.pk3"]
        os.remove(os.path.join(self.store, "parts", e.parts))
        said = []
        r = deploy.deploy(self.store, deploy.FolderTarget(self.p("web")), log=said.append)
        self.assertEqual(r.parts_sent, 0)
        self.assertTrue(os.path.isfile(self.p("web", "manifest.json")))
        self.assertTrue([s for s in said if s.startswith("Warning: the parts index of data/pak0.pk3 is missing")])

    def test_a_backup_and_its_restore_keep_them(self):
        config.save({"publish_src": self.src, "name": "Game", "deploy_mode": "folder", "deploy_folder": self.p("www")})
        project = projects.main_project()
        www = deploy.FolderTarget(self.p("www"))
        indexes = self.releases(www, textures(), None, 0)
        r = backup.take(project, config.load(), www)
        self.assertTrue(r["made"])
        pool = os.path.join(r["folder"], "parts")
        self.assertEqual(sorted(os.listdir(pool)), sorted(indexes))                 # the release's and the one before's
        os.remove(os.path.join(pool, indexes[1]))
        r = backup.take(project, config.load(), www)                                # unchanged: topped up
        self.assertFalse(r["made"])
        self.assertEqual(sorted(os.listdir(pool)), sorted(indexes))
        backup.restore(project, config.load(), r["name"], deploy.FolderTarget(self.p("fresh")))
        self.assertEqual(sorted(os.listdir(self.p("fresh", "parts"))), [indexes[1]])
        self.assertEqual(read(self.p("fresh", "parts", indexes[1])), read(self.p("www", "parts", indexes[1])))
        write(os.path.join(pool, "f" * 64), b"no backup names it")
        backup.apply_limits(project, config.load())
        self.assertEqual(sorted(os.listdir(pool)), sorted(indexes))                 # what no backup names goes

    def test_a_damaged_index_in_the_backup_pool_is_read_again(self):
        config.save({"publish_src": self.src, "name": "Game", "deploy_mode": "folder", "deploy_folder": self.p("www")})
        project = projects.main_project()
        www = deploy.FolderTarget(self.p("www"))
        indexes = self.releases(www, textures(), None, 0)
        r = backup.take(project, config.load(), www)
        pool = os.path.join(r["folder"], "parts")
        good = read(os.path.join(pool, indexes[1]))
        write(os.path.join(pool, indexes[1]), good[:-1] + bytes([good[-1] ^ 1]))  # its size as it was, one bit off
        r = backup.take(project, config.load(), www)                                # unchanged: topped up
        self.assertFalse(r["made"])
        self.assertEqual(read(os.path.join(pool, indexes[1])), good)
        backup.restore(project, config.load(), r["name"], deploy.FolderTarget(self.p("fresh")))
        self.assertEqual(read(self.p("fresh", "parts", indexes[1])), good)

    def test_a_batch_of_indexes_read_through_sftp_gets_the_time_it_may_need(self):
        disk = self.p("server")
        names = ["%064x" % i for i in range(9)]
        for n in names[:-1]:                                                        # the last one is not there
            write(os.path.join(disk, "parts", n), n.encode("ascii"))
        fake = FakeSftp(disk)
        batches = []

        def sftp(target, commands, cwd=None, timeout=0):
            batches.append((len(commands), timeout))
            return fake(target, commands, cwd, timeout)

        into = self.p("into")
        os.makedirs(into)
        with mock.patch.object(ssh, "sftp", sftp):
            deploy.SftpTarget(ssh.Target("game.example.com", "deploy", 22, "", REMOTE)).get_parts(names, into,
                                                                                                 lambda sha: None)
        self.assertEqual(sorted(os.listdir(into)), names[:-1])
        # as a batch of blobs: at most 256 MiB a batch, each index counted at 64 MiB (no manifest gives its size),
        # and 600 s plus its bytes at 50 KiB/s
        rate = deploy.SftpTarget.MIN_RATE
        self.assertEqual((parts.INDEX_MAX, deploy.SftpTarget.CHUNK_BYTES, rate), (64 << 20, 256 << 20, 50 << 10))
        self.assertEqual(batches, [(4, 600 + 4 * parts.INDEX_MAX // rate), (4, 600 + 4 * parts.INDEX_MAX // rate),
                                   (1, 600 + parts.INDEX_MAX // rate)])
        self.assertEqual(batches[0][1], 5842)                                       # as long as a full batch of blobs


class Statistics(unittest.TestCase):
    def test_a_parts_index_is_a_file_and_a_rebuilt_archive_no_download(self):
        now = 1795000000
        ua = "DeltaPatcher/0.3.0 release/1"
        c = stats.Counter("/game/", "game.example.com", 1, now)
        sha = "ab" * 32
        self.assertEqual(c.kind("/game/parts/" + sha), "file")
        self.assertIsNone(c.kind("/game/parts/" + sha[:10]))
        # a player who only checks: the manifest, and the parts index the check reads to say the smaller amount
        c.add((now - 90, "GET", "/game/manifest.json", 200, 900, "203.0.113.5", ua, None))
        c.add((now - 80, "GET", "/game/parts/" + sha, 200, 1500, "203.0.113.5", ua, None))
        total = c.section()["total"]
        self.assertEqual((total["checks"], total["downloads"], total["bytes"], total["players"]), (1, 0, 2400, 1))
        # then the update: the archive rebuilt from the player's files (ranges) is no download; a whole file is one
        c.add((now - 50, "GET", "/game/blobs/" + sha, 206, 700000, "203.0.113.5", ua, None))
        c.add((now - 40, "GET", "/game/blobs/" + "cd" * 32, 200, 5000, "203.0.113.5", ua, None))
        total = c.section()["total"]
        self.assertEqual((total["checks"], total["downloads"], total["bytes"], total["players"]), (1, 1, 707400, 1))

    def test_the_release_line_says_what_its_number_is(self):
        current = {"version": "2", "name": "Game", "published": "2026-11-20T18:30:04Z", "files": 3, "bytes": 1 << 30,
                   "blobs": 3}
        change = {"from": {"name": "Game", "version": "1", "published": ""}, "new": 1, "changed": 2, "removed": 0,
                  "download_bytes": 1503238553}
        doc = {"project": {"project": "Game"}, "days": 30, "problems": {},
               "sections": {"releases": {"current": current, "change": change, "store_files": 9, "store_bytes": 1 << 31,
                                         "history_total": 2}}}
        lines = [line for line, _style in stats.text(doc)]
        self.assertIn("  from 1: 1 new, 2 changed, 0 removed: 1.4 GB of new and changed files", lines)
        self.assertFalse([line for line in lines if "downloads" in line])


# ---- the publish warning -------------------------------------------------------------------------------------------

class Warnings(Base):
    def test_big_archives_that_cannot_be_patched_inside_are_said_once(self):
        big = parts.PARTS_MIN + 5000
        kinds = {"maps.rar": b"Rar!\x1a\x07\x01\x00", "pack.7z": b"7z\xbc\xaf\x27\x1c", "music.tar.gz": b"\x1f\x8b\x08"}
        for name, magic in kinds.items():
            self.put("data/" + name, magic + os.urandom(big - len(magic)))
        self.put("data/small.rar", b"Rar!\x1a\x07\x00" + os.urandom(5000))           # small: no line
        self.put("data/video.bin", os.urandom(big))                                  # not an archive: no line
        self.put("data/pak0.pk3", zip_bytes(textures()))                             # an index: no line
        config.save({"publish_src": self.src, "store_dir": self.store})
        rc, ev = run_json(["publish"])
        self.assertEqual(rc, 0, ev[-1])
        # a RAR or 7z this reader cannot take apart says so, as a zip does; a gzip keeps what would let it patch inside
        why = {"maps.rar": "not a RAR this reader knows.", "pack.7z": "not a 7z this reader knows.",
               "music.tar.gz": "pack it as .zip or .pk3 so only the files that changed inside it travel."}
        lines = ["Warning: data/%s (%s) is sent whole whenever it changes: %s" % (name, publish.human(big), why[name])
                 for name in sorted(kinds)]
        self.assertEqual(ev[-1]["data"]["warnings"], lines)
        logged = [e for e in ev if e["event"] == "log" and "is sent whole" in e["text"]]
        self.assertEqual([(e["text"], e["style"]) for e in logged], [(line, "warn") for line in lines])   # once each
        self.assertEqual(ev[-1]["data"]["parts"], 1)
        # the release is published as ever, and players update from it, told nothing of it
        self.assertEqual(sorted(self.manifest().files), sorted("data/" + n for n in list(kinds) +
                                                               ["small.rar", "video.bin", "pak0.pk3"]))
        rc, ev = run_json(["update", self.game, "--source", self.serve()])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(snapshot(self.game), snapshot(self.src))
        self.assertFalse([e for e in ev if e["event"] == "log" and "sent whole" in e["text"]])


# ---- what an older DeltaPatcher reads ------------------------------------------------------------------------------

def read_as_0_2_0(raw):
    """What DeltaPatcher 0.2.0's Manifest.from_json takes of a manifest (git show v0.2.0:deltapatcher/manifest.py):
    format 1, and of each file exactly item.get("path"), item.get("sha256") and item.get("size"), given to an Entry
    made from those three."""
    doc = json.loads(raw.decode("utf-8"))
    if doc.get("format") != 1 or not isinstance(doc.get("files"), list):
        raise ManifestError("0.2.0 refuses it")
    return [Entry(item.get("path"), item.get("sha256"), item.get("size")) for item in doc["files"]]


class WholeValues(Base):
    def test_a_sha256_or_parts_with_a_newline_after_it_is_refused(self):
        # a SHA-256 is the whole value: "$" took one with a newline after it
        _two, data = self.two_releases()
        mpath = os.path.join(self.store, "manifest.json")
        doc = json.loads(read(mpath))
        item = [f for f in doc["files"] if f["path"] == "data/pak0.pk3"][0]
        item["parts"] += "\n"
        write(mpath, json.dumps(doc).encode("utf-8"))
        self.assertIsNone(self.manifest().files["data/pak0.pk3"].parts)               # passed over, as any bad one
        plan = client.update(self.served.url, self.game, check_only=True)
        self.assertEqual((plan.rebuilds, plan.fetch_bytes(), self.served.of("parts")), ({}, len(data), []))
        item["parts"] = item["parts"][:-1]
        item["sha256"] += "\n"
        with self.assertRaises(ManifestError):
            Manifest.from_json(json.dumps(doc).encode("utf-8"))
        self.assertFalse(any(m.HEX64.match("ab" * 32 + "\n") for m in (manifest_module, torrent_folder)))
        self.assertTrue(all(m.HEX64.match("ab" * 32) for m in (manifest_module, torrent_folder)))


class OlderReaders(Base):
    def test_a_manifest_with_parts_reads_as_0_2_0_reads_it(self):
        self.put("data/pak0.pk3", zip_bytes(textures()))
        self.put("readme.txt", "hi\n")
        self.release("1")
        raw = read(os.path.join(self.store, "manifest.json"))
        man = Manifest.from_json(raw)
        self.assertIn('"parts": "%s"' % man.files["data/pak0.pk3"].parts, raw.decode("utf-8"))
        self.assertEqual(json.loads(raw)["format"], 1)                              # the format stays 1
        old = read_as_0_2_0(raw)
        self.assertEqual([(e.path, e.sha256, e.size) for e in old],
                         [(e.path, e.sha256, e.size) for e in (man.files[p] for p in sorted(man.files))])
        self.assertEqual([e.parts for e in old], [None, None])                      # three fields: parts never needed
        # a manifest without "parts" (every one an older publisher wrote) reads, and one with a bad value too
        doc = json.loads(raw)
        for item in doc["files"]:
            item.pop("parts", None)
        plain = Manifest.from_json(json.dumps(doc).encode("utf-8"))
        self.assertEqual(plain.files, man.files)                                    # equal: parts is not compared
        self.assertEqual([e.parts for e in plain.files.values()], [None, None])
        doc["files"][0]["parts"] = "not a sha"
        doc["files"][1]["parts"] = 12
        self.assertEqual([e.parts for e in Manifest.from_json(json.dumps(doc)).files.values()], [None, None])
        self.assertNotIn("parts", Entry("a", "c" * 64, 1).to_json())
        compat = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "compat", "0.3.0", "release",
                              "manifest.json")
        self.assertTrue(Manifest.from_json(read(compat)).files)


# ---- serving, and the pieces of the machinery ----------------------------------------------------------------------

class Serving(Base):
    def test_the_built_in_server_serves_parts_also_beside_a_torrent_named_parts(self):
        self.put("data/pak0.pk3", zip_bytes(textures()))
        self.release("1")
        index = self.manifest().files["data/pak0.pk3"].parts
        for named in (False, True):
            if named:
                write(os.path.join(self.store, "release.torrent"), b"d4:infod4:name5:partsee")
            src = Source(self.serve())
            self.assertEqual(src.get_parts(index), read(os.path.join(self.store, "parts", index)))
            self.assertIsNone(src.get_parts("0" * 64))
            if named:                                                               # the web seed's path still works
                self.assertEqual(Source(self.served.url + "parts/").get_manifest(),
                                 read(os.path.join(self.store, "manifest.json")))

    def test_a_player_at_the_web_seeds_address_reads_indexes_there_and_rebuilds(self):
        # a release shared by torrent: its store answers under /<the torrent's name>/ too, parts indexes included
        one = textures()
        self.put("data/pak0.pk3", zip_bytes(one))
        self.release("1")
        write(os.path.join(self.store, "release.torrent"), b"d4:infod4:name4:Gameee")
        seed = self.serve() + "Game/"
        client.update(seed, self.game)
        data = zip_bytes(changed(one, "textures/t03.tga"))
        self.put("data/pak0.pk3", data)
        self.release("2")
        e = self.manifest().files["data/pak0.pk3"]
        self.assertEqual(Source(seed).get_parts(e.parts), read(os.path.join(self.store, "parts", e.parts)))
        del self.served.answers[:]
        plan = client.update(seed, self.game)
        self.assertEqual(self.installed("data/pak0.pk3"), data)
        self.assertEqual(len(plan.rebuilds), 1)
        self.assertEqual([(a[0], a[2]) for a in self.served.answers if "/parts/" in a[0] or "/blobs/" in a[0]],
                         [("/Game/parts/" + e.parts, 200), ("/Game/blobs/" + e.sha256, 206)])

    def test_fetch_range_takes_only_exactly_the_bytes_asked_for(self):
        data = zip_bytes(textures())
        self.put("data/pak0.pk3", data)
        self.release("1")
        sha = self.manifest().files["data/pak0.pk3"].sha256
        out = self.p("out.bin")
        write(out, b"\0" * len(data))
        got = []
        Source(self.serve()).fetch_range(sha, 1000, 5000, len(data), out, got.append)
        self.assertEqual((read(out)[1000:5000], sum(got)), (data[1000:5000], 4000))
        for kw, args in (({}, (sha, 1000, 5000, len(data) + 1)),                    # another blob's size
                         ({"ignore_range": True}, (sha, 0, 10, len(data))),
                         ({"wrong_range": True}, (sha, 0, 10, len(data))),
                         ({}, ("0" * 64, 0, 10, len(data)))):                          # no such blob
            with self.assertRaises(RangeError, msg=(kw, args)):
                Source(self.serve(**kw)).fetch_range(*(args + (out,)))
        stop = threading.Event()
        stop.set()
        with self.assertRaises(SourceError) as cm:
            Source(self.served.url).fetch_range(sha, 0, 10, len(data), out, cancel=stop)
        self.assertEqual(str(cm.exception), "stopped")


class Machinery(unittest.TestCase):
    def test_ranges_join_neighbours_and_stay_few(self):
        self.assertEqual(rebuild.coalesce([(100, 200), (0, 10)]), [(0, 200)])
        far = [(0, 10), (rebuild.GAP + 10, rebuild.GAP + 20)]
        self.assertEqual(rebuild.coalesce(far), far)
        many = [(i * 2 * rebuild.GAP, i * 2 * rebuild.GAP + 5) for i in range(1000)]
        ranges = rebuild.coalesce(many)
        self.assertLessEqual(len(ranges), rebuild.MAX_RANGES)
        for a, b in many:
            self.assertTrue(any(x <= a and b <= y for x, y in ranges))
        self.assertEqual(rebuild.coalesce([]), [])

    def test_an_index_is_refused_past_every_limit(self):
        # literals of exactly a quarter of the file: as much as one may hold
        sha, size = "c" * 64, 4000
        good = {"format": 1, "kind": "zip", "file": sha, "size": size,
                "segments": [["l", "QUJD"], ["p", "d" * 64, 3000], ["l", base64.b64encode(b"x" * 997).decode()]]}

        def index(doc):
            data = zlib.compress(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode("utf-8"), 9)
            return data, Entry("a.pk3", sha, size, hashlib.sha256(data).hexdigest())

        data, e = index(good)
        self.assertEqual([(k, o, n) for k, o, n, _p in parts.read_index(data, e)],
                         [("l", 0, 3), ("p", 3, 3000), ("l", 3003, 997)])
        bad = [index(dict(good, **change)) for change in (
            {"format": 2}, {"format": True}, {"kind": "cab"}, {"file": "e" * 64}, {"size": 4001}, {"size": True},
            {"segments": good["segments"][:2]}, {"segments": good["segments"] + [["l", "QQ=="]]},
            {"segments": [["l", "not base64!"]] + good["segments"][1:]},
            {"segments": [["p", "d" * 64, -1]] + good["segments"]},
            {"segments": [["p", "D" * 64, 3]] + good["segments"][1:]},
            {"segments": [["l", base64.b64encode(b"x" * 1001).decode()], ["p", "d" * 64, 2999]]},   # a quarter, +1
            {"segments": [["l", ""]] + good["segments"]}, {"segments": [["x"]]}, {"segments": "none"})]
        bad.append((data, Entry("a.pk3", sha, size, "f" * 64)))                     # not the index the entry names
        bad.append((b"not zlib", Entry("a.pk3", sha, size, hashlib.sha256(b"not zlib").hexdigest())))
        trailing = data + b"more"
        bad.append((trailing, Entry("a.pk3", sha, size, hashlib.sha256(trailing).hexdigest())))
        for data_, e_ in bad:
            with self.assertRaises(parts.PartsError, msg=data_[:40]):
                parts.read_index(data_, e_)
        for limit, value in (("INDEX_MAX", len(data) - 1), ("JSON_MAX", 100), ("SEGMENTS_MAX", 2)):
            with mock.patch.object(parts, limit, value):
                self.assertRaises(parts.PartsError, parts.read_index, data, e)

    def test_a_small_hostile_index_is_refused_before_it_is_parsed(self):
        sha, size = "c" * 64, 1000

        def index(segments, size_text=str(size), key="file"):
            text = ('{"%s":"%s","format":1,"kind":"zip","segments":[%s],"size":%s}'
                    % (key, sha, segments, size_text)).encode("ascii")
            data = zlib.compress(text, 9)
            return text, data, Entry("a.pk3", sha, size, hashlib.sha256(data).hexdigest())

        n = parts.SEGMENTS_MAX + 100000
        # a few KiB of zlib that is MiB of JSON: a flat list of empty lists, of empty objects, of lists of a number,
        # one list of more numbers than an index has commas; a key written with an escape; a number of 21 digits
        good = '["l","QUJD"],["p","%s",997]' % ("d" * 64)
        for segments, size_text, key in (("[]," * n + "[]", str(size), "file"), ("{}," * n + "{}", str(size), "file"),
                                         ("[0]," * n + "[0]", str(size), "file"),
                                         ("0," * (3 * n) + "0", str(size), "file"), (good, str(size), "fi\\u006ce"),
                                         (good, "1" * 21, "file")):
            text, data, e = index(segments, size_text, key)
            with mock.patch.object(parts.json, "loads", wraps=json.loads) as loads:
                tracemalloc.start()
                try:
                    with self.assertRaises(parts.PartsError) as cm:
                        parts.read_index(data, e)
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
            self.assertIn("not the shape of an index", str(cm.exception))
            self.assertEqual(loads.call_count, 0, segments[:12])                      # never parsed
            self.assertLess(peak, 4 * len(text) + (4 << 20), segments[:12])          # the text, and little more
        # an index has no float: the parser makes none
        for size_text in ("1000.0", "1e3", "NaN", "-Infinity"):
            _text, data, e = index(good, size_text)
            with self.assertRaises(parts.PartsError) as cm:
                parts.read_index(data, e)
            self.assertIn("a number no index has", str(cm.exception))
        _text, data, e = index(good, "1" * 20)                                      # 20 digits: read, then refused
        with self.assertRaises(parts.PartsError) as cm:
            parts.read_index(data, e)
        self.assertEqual(str(cm.exception), "the index of a file of another size")
        _text, data, e = index(good)
        self.assertEqual(len(parts.read_index(data, e)), 2)

    def test_anything_before_or_after_the_object_is_refused_before_it_is_parsed(self):
        # one long number before the object passes the other counts, and the parser would copy it before it sees
        # that the text is no object; the writer puts nothing before "{" or after "}"
        sha, mib = "c" * 64, 1 << 20
        n = 8 * mib
        good = ('{"file":"%s","format":1,"kind":"zip","segments":[["l","QUJD"],["p","%s",997]],"size":1000}'
                % (sha, "d" * 64)).encode("ascii")
        for text in (b"1" * n + b"{}", b"1.5e" + b"1" * n + b"{}", b" \n" + b"1" * n + b"{}", b"-" + b"1" * n + b"{}",
                     b" " + good, good + b"\n"):
            data = zlib.compress(text, 9)
            e = Entry("a.pk3", sha, 1000, hashlib.sha256(data).hexdigest())
            with mock.patch.object(parts.json, "loads", wraps=json.loads) as loads:
                tracemalloc.start()
                try:
                    with self.assertRaises(parts.PartsError) as cm:
                        parts.read_index(data, e)
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
            self.assertEqual((str(cm.exception), loads.call_count), ("not the shape of an index (one object)", 0))
            self.assertLess(peak, 2.75 * len(text) + (4 << 20))
        data = zlib.compress(good, 9)
        self.assertEqual(len(parts.read_index(data, Entry("a.pk3", sha, 1000, hashlib.sha256(data).hexdigest()))), 2)

    def test_one_long_string_or_number_is_refused_before_it_is_parsed(self):
        # the text of one literal or one number can be the whole JSON: refused before the parser copies it again.
        # A literal's base64 is at most that of a quarter of the file (and of LITERAL_MAX): for 24 MiB, 8 MiB of it
        sha, mib = "c" * 64, 1 << 20

        def index(body, size, size_text=None):
            text = ('{"file":"%s","format":1,"kind":"zip","segments":[%s],"size":%s}'
                    % (sha, body, size_text or size)).encode("ascii")
            data = zlib.compress(text, 9)
            return len(text), data, Entry("a.pk3", sha, size, hashlib.sha256(data).hexdigest())

        cases = [index('["l","%s"]' % ("A" * (32 * mib)), 24 * mib),                # 24 MiB of literal, 24 MiB file
                 index('["l","QUJD"]', 3, "1" * (32 * mib)),                         # one number of 32 Mi digits
                 index('["l","%s"]' % ("!" * (9 * mib)), 24 * mib),                  # a long string of anything
                 index('["l","QUJD"],["p","%s",%d]' % ("d" * 64, 24 * mib - 3), 24 * mib,
                       "%d.%s" % (24 * mib, "0" * 30))]                                # a float of 39 characters
        for length, data, e in cases:
            with mock.patch.object(parts.json, "loads", wraps=json.loads) as loads:
                tracemalloc.start()
                try:
                    with self.assertRaises(parts.PartsError) as cm:
                        parts.read_index(data, e)
                    peak = tracemalloc.get_traced_memory()[1]
                finally:
                    tracemalloc.stop()
            self.assertIn("not the shape of an index", str(cm.exception))
            self.assertEqual(loads.call_count, 0, str(cm.exception))
            # the text as zlib makes it (2.4 to 2.5 times it, measured): no copy of it, nor of the string
            self.assertLess(peak, 2.75 * length + (4 << 20), str(cm.exception))
        longest = 4 * -(-6 * mib // 3)                                              # a quarter of 24 MiB, in base64
        for b64, refused in ((longest, False), (longest + 4, True)):
            _length, data, e = index('["l","%s"],["p","%s",%d]' % ("A" * b64, "d" * 64, 24 * mib - b64 // 4 * 3),
                                     24 * mib)
            if refused:
                self.assertRaises(parts.PartsError, parts.read_index, data, e)
            else:
                self.assertEqual(len(parts.read_index(data, e)), 2)

    def test_literals_past_what_publishing_allows_are_refused(self):
        # more literal bytes than LITERAL_MAX in a file big enough for them to be under a quarter of it, then more
        # than a quarter of a file in a small one: publishing makes neither
        sha, mib = "c" * 64, 1 << 20
        half = parts.LITERAL_MAX // 2
        for literals, piece in (((half, half + 1), 4 * parts.LITERAL_MAX), ((half, half), 4 * parts.LITERAL_MAX),
                                ((1000, 1), 3001), ((1000,), 3000)):
            size = sum(literals) + piece
            body = ",".join('["l","%s"]' % base64.b64encode(bytes(n)).decode("ascii") for n in literals)
            text = ('{"file":"%s","format":1,"kind":"zip","segments":[%s,["p","%s",%d]],"size":%d}'
                    % (sha, body, "d" * 64, piece, size)).encode("ascii")
            data = zlib.compress(text, 9)
            del text
            e = Entry("a.pk3", sha, size, hashlib.sha256(data).hexdigest())
            if sum(literals) > min(parts.LITERAL_MAX, size // parts.LITERAL_SHARE):
                with self.assertRaises(parts.PartsError, msg=literals) as cm:
                    parts.read_index(data, e)
                self.assertIn("more literal bytes than a quarter of the file or 48 MiB", str(cm.exception))
            else:
                self.assertEqual(len(parts.read_index(data, e)), len(literals) + 1, literals)
        self.assertEqual((parts.LITERAL_MAX, parts.LITERAL_SHARE), (48 * mib, 4))

    def test_the_largest_index_reads_and_what_it_takes(self):
        # the largest an index can be: SEGMENTS_MAX segments, every one but the last a piece with as many digits as
        # the file's size leaves room for, the last a literal of LITERAL_MAX bytes. It is within JSON_MAX, by less
        # than 5 MiB; it reads; and reading it peaks at 436 MiB on Python 3.12 (Windows, tracemalloc), 293 MiB of
        # it kept. One segment more is refused before it is parsed.
        n = parts.SEGMENTS_MAX - 1
        piece = b'["p","%s",100000000000000]' % (b"d" * 64)
        size = n * 100000000000000 + parts.LITERAL_MAX                             # 20 digits
        for extra in (0, 1):
            text = b"".join([b'{"file":"', b"c" * 64, b'","format":1,"kind":"zip","segments":[',
                             b",".join([piece] * (n + extra)), b',["l","', b"A" * parts.B64_MAX, b'"]],"size":',
                             str(size + extra * 100000000000000).encode("ascii"), b"}"])
            length = len(text)
            self.assertEqual((text.count(b"["), len(str(size))), (parts.SEGMENTS_MAX + 1 + extra, 20))
            data = zlib.compress(text, 9)
            del text
            e = Entry("a.pk3", "c" * 64, size + extra * 100000000000000, hashlib.sha256(data).hexdigest())
            if extra:
                with self.assertRaises(parts.PartsError) as cm:
                    parts.read_index(data, e)
                self.assertIn("not the shape of an index", str(cm.exception))
                continue
            self.assertLessEqual(length, parts.JSON_MAX)
            self.assertLess(parts.JSON_MAX - length, 5 << 20)                       # the limit is what one can reach
            tracemalloc.start()
            try:
                segs, json_bytes = parts.read_index(data, e, sized=True)
                kept, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertEqual((len(segs), segs[-1][:3], json_bytes),
                             (parts.SEGMENTS_MAX, ("l", n * 100000000000000, parts.LITERAL_MAX), length))
            del segs
            self.assertLess(peak, 500 << 20)                                         # measured: 436 MiB
            self.assertLess(kept, 350 << 20)                                         # measured: 293 MiB

    def test_archive_kinds_by_their_first_bytes(self):
        with tempfile.TemporaryDirectory(prefix="dp-test-") as folder:
            for name, head, kind in (("a", b"Rar!\x1a\x07\x00", "RAR"), ("b", b"7z\xbc\xaf\x27\x1c", "7z"),
                                     ("c", b"\x1f\x8b\x08", "gzip"), ("d", b"PK\x03\x04", "zip"),
                                     ("e", b"MZ\x90\x00", ""), ("f", b"", "")):
                p = os.path.join(folder, name)
                write(p, head + os.urandom(100))
                self.assertEqual(parts.archive_kind(p), kind, name)
            p = os.path.join(folder, "sfx")
            write(p, b"MZ" + os.urandom(100) + zip_bytes([("a.txt", b"a", False, WHEN)]))
            self.assertEqual(parts.archive_kind(p), "zip")


if __name__ == "__main__":
    unittest.main()
