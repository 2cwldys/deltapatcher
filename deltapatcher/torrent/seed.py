"""deltapatcher seed: projects' releases shared with other players, until the run is stopped.

One session for the whole run, listening on every interface at general.torrent_port, with local discovery on and
the DHT on when a project in the run has no address. For each project:
  - the publisher's own project seeds its store as it is (manifest.json, sigs/, blobs/, release.torrent);
  - a player's project seeds from its installed folder, without copying: each blob is a file the release put
    there, the manifest and signature the copies the folder keeps in .deltapatcher/release/ (folder.py says which
    files are offered: only ones the engine has checked, never a protected or changed one).
Only the newest release is seeded: for a project with an address, the one the address serves now; with none, the
release item with the highest number. The run looks for a newer one every NEWER seconds and switches to it,
offering what the folder has of it, plus its manifest and signature (fetched and checked). A project with no
address also has its release item put in the DHT again every NEWER seconds (items expire), unchanged.

Every few seconds the run reads the settings again (a project set to seed = no leaves at once, refusing torrents
ends the run), looks at the files it offers (a file that changed is offered again only once it is checked again),
answers updates that ask it to pause (pause.py), and says how each project is doing: in the output, and in
seeding.json beside the main settings file for the window and `deltapatcher projects`. Nothing is ever
downloaded into a folder: seeds are in upload mode.
"""

import hashlib
import os
import shutil
import threading
import time

from .. import config, projects, publisher, signing
from ..source import Source
from . import (ITEM_FILE, RELEASE_DIR, TORRENT_FILE, Kept, TorrentError, address_of, bencode, folder, info, item,
               krpc, pause, publisher_address, refused, write_seeding)
from . import session as S

STATUS = 5.0                 # seconds between the lines that say how each project is doing
SETTINGS = 3.0               # seconds between reads of the settings
WATCH = 5.0                  # seconds between looks at the files offered
NEWER = 1800.0               # seconds between looks for a newer release, and between puts of the release item
RETRY = 60.0                 # seconds before a put no DHT node took is tried again
POLL = 0.2
CHECK = 0.2                  # seconds between the looks for pause requests while a folder's files are looked at
NEXT = pause.NEXT            # <state>/seeding/next/: a release newer than the folder's, while it is seeded


def store_of(cfg):
    """The publisher's store of a project: store_dir, else the one DeltaPatcher keeps for its publish_src."""
    if cfg.get("store_dir"):
        return os.path.abspath(cfg["store_dir"])
    if cfg.get("publish_src"):
        return config.default_store(cfg["publish_src"])
    return ""


def _read(path, limit):
    try:
        with open(path, "rb") as f:
            return f.read(limit)
    except OSError:
        return None


def _write_files(d, files):
    """Write {relative path: bytes} into the folder d, each through a temporary file of this process and thread (two
    seed runs of one folder never share one)."""
    os.makedirs(os.path.join(d, "sigs"), exist_ok=True)
    for rel, data in files.items():
        p = os.path.join(d, *rel.split("/"))
        tmp = "%s.%d.%d.tmp" % (p, os.getpid(), threading.get_ident())
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)


class Seeded(object):
    """One project of a seed run."""

    def __init__(self, run, project, cfg):
        self.run = run
        self.project = project
        self.cfg = cfg
        # seed = yes at some time in this run: seed = no drops it then, also from a run that named it (--project,
        # where the command is the yes)
        self.seed_then = bool(cfg.get("seed"))
        self.tinfo = info(cfg)
        store = store_of(cfg)
        if store and os.path.isfile(os.path.join(store, TORRENT_FILE)):
            self.kind, self.root = "store", store
            self.only = not publisher_address(cfg)
        else:
            self.kind = "folder"
            self.root = os.path.abspath(cfg["install_dir"]) if cfg.get("install_dir") else ""
            self.only = not address_of(cfg)
        self.state_dir = os.path.join(self.root, ".deltapatcher") if self.root else ""
        self.seat = pause.Seat(self.root) if self.root else None
        self.kept = Kept(self.state_dir, self.tinfo["id"]) if self.root and self.tinfo else None
        self.handle = None
        self.ih = None                        # the infohash seeded now: one torrent per infohash in a run
        self.mapping = None
        self.it = None
        self.title_of_release = ""
        self.paused = False
        self.state = "waiting"
        self.why = ""
        self.sent_before = 0
        self.next_newer = 0.0
        self.next_put = 0.0
        self.putting = None
        self.looking = None
        self.found = None                     # a newer release a look found: {name: bytes}, switched to by the run
        self.said = ""

    def tag(self):
        return self.project.tag()

    def say_once(self, text):
        if text != self.said:
            self.said = text
            self.run.log(text, self)

    def key(self, own=None):
        """The publisher key the project trusts: its trust_key, else the one its folder pinned; for the publisher's
        store, the key its own release item names when it has none set."""
        k = publisher.trusted_key(self.cfg.get("trust_key"), self.root if self.kind == "folder" else "")
        if not k and self.kind == "store" and own is not None:
            k = signing.key_text(own)
        return k

    # ---- what to seed

    def release_now(self):
        """(manifest bytes, signature bytes, torrent bytes, Item, [Manifest] whose paths may hold blobs) of the
        release to seed now; TorrentError saying why when there is none."""
        tid = self.tinfo["id"]
        if self.kind == "store":
            raw = _read(os.path.join(self.root, "manifest.json"), 64 << 20)
            tor = _read(os.path.join(self.root, TORRENT_FILE), 64 << 20)
            item_bytes = _read(os.path.join(self.root, ITEM_FILE), 4096)
            if not raw or not tor or not item_bytes:
                raise TorrentError("its store has no release shared by torrent yet: publish it", "nothing-to-seed")
            sha = hashlib.sha256(raw).hexdigest()
            sig = _read(os.path.join(self.root, "sigs", sha + ".sig"), 1 << 16)
            try:
                own = bencode.decode(item_bytes).get(b"k")
            except (bencode.BencodeError, ValueError, AttributeError):
                own = None
            it = item.read(item_bytes, self.key(own if isinstance(own, bytes) and len(own) == 32 else None), tid)
            return raw, sig, tor, it, []
        nxt = os.path.join(self.state_dir, pause.DIR, NEXT)
        key = self.key()
        if not key:
            raise TorrentError("its folder trusts no publisher key yet: update it once first", "nothing-to-seed")
        raw, sig, tor, it = S.read_kept(self.state_dir, key, tid)
        if raw is None:
            raise TorrentError("its folder keeps no release to seed yet: update it first", "nothing-to-seed")
        installed = S._manifest_of(raw)
        manifests = [installed] if installed is not None else []
        nraw = _read(os.path.join(nxt, "manifest.json"), 64 << 20)
        if nraw:
            nsha = hashlib.sha256(nraw).hexdigest()
            ntor = _read(os.path.join(nxt, TORRENT_FILE), 64 << 20)
            nitem = _read(os.path.join(nxt, ITEM_FILE), 4096)
            nsig = _read(os.path.join(nxt, "sigs", nsha + ".sig"), 1 << 16)
            try:
                nit = item.read(nitem, key, tid) if nitem else None
            except TorrentError:
                nit = None
            newer = S._manifest_of(nraw)
            # only a release numbered above the folder's own: after an update to it, or past it, next/ is outdated
            if nit is not None and ntor and nit.m == nsha and newer is not None and \
                    nsha != hashlib.sha256(raw).hexdigest() and nit.seq > (it.seq if it is not None else 0):
                return nraw, nsig, ntor, nit, manifests + [newer]
            shutil.rmtree(nxt, ignore_errors=True)
        if not tor or it is None:
            raise TorrentError("its folder keeps no torrent of its release: update it again", "nothing-to-seed")
        return raw, sig, tor, it, manifests

    # ---- seeding

    def _held_off(self):
        """True when the folder must not be seeded now: an update asks this run to pause (answered here at once,
        and seeded again once it ends), or one is changing the folder (its rollback.new is there)."""
        if self.seat.asked():
            self.seat.answer()
            self.paused, self.state, self.why = True, "paused", "an update is changing its folder"
            return True
        if self.kind == "folder" and os.path.exists(os.path.join(self.state_dir, "rollback.new")):
            self.state, self.why = "waiting", "an update is changing its folder"
            return True
        return False

    def _same_release(self, ih):
        """Another project of this run seeding the torrent `ih` already (a store and an installed copy of it, two
        installs of one game): one torrent per infohash in a session."""
        for o in self.run.seeded:
            if o is not self and o.handle is not None and o.ih == ih:
                return o
        return None

    def start(self):
        """Seed the release now, if nothing stops it; returns True when it is seeded. The run says it seeds the
        folder (pause.py) before it looks at the files, and answers an update that asks it to pause between each
        step, the last one just before the torrent goes in."""
        if self.handle is not None or self.paused:
            return self.handle is not None
        if not self.root or not os.path.isdir(self.root):
            self.state, self.why = "waiting", "its folder is not there" if self.root else "it has no folder or store"
            self.say_once("%s: not seeded: %s" % (self.project.title, self.why))
            return False
        self.seat.beat(force=True)
        if self._held_off():
            return False
        try:
            raw, sig, tor, it, manifests = self.release_now()
            ti, files = S.read_torrent(tor)             # the item, signed by the publisher key, names the torrent
            ih = S.infohash_of(ti)
            if ih != it.ih or it.m != hashlib.sha256(raw).hexdigest():
                raise TorrentError("its torrent and its release item are not of one release", "nothing-to-seed")
        except TorrentError as e:
            self.state, self.why = "waiting", str(e)
            self.say_once("%s: not seeded: %s" % (self.project.title, e))
            return False
        other = self._same_release(ih)
        if other is not None:
            self.state, self.why = "waiting", "%s seeds the same release in this run" % other.project.title
            self.say_once("%s: not seeded: %s" % (self.project.title, self.why))
            return False
        if self._held_off():
            return False
        cache = folder.hash_cache(self.root)
        check = self.run.checker(self)                  # the run answers pauses while the files are looked at
        try:
            if self.kind == "store":
                m = folder.for_store(files, ti.num_pieces(), self.root, cache, check=check)
            else:
                rel = ".deltapatcher/" + RELEASE_DIR
                if len(manifests) > 1:
                    rel = ".deltapatcher/%s/%s" % (pause.DIR, NEXT)
                m = folder.for_folder(files, ti.num_pieces(), self.root, manifests, rel, cache, check=check)
        except folder.Interrupted:
            if not self._held_off():                    # not an update asking: the run stops
                self.state, self.why = "waiting", ""
            return False
        self.seat.beat(force=True)
        peers = S.resolve(self.tinfo["seeds"] + (self.kept.peers if self.kept else []), time.time() + 3)
        if self._held_off():                            # an update asked while the files were looked at: it goes first
            return False
        try:
            self.handle = self.run.swarm.add(ti, save_path=self.root, mapping=m, peers=peers, seed=True, dht=self.only)
        except RuntimeError as e:                       # libtorrent refuses it (a torrent it has already, ...)
            self.state, self.why = "waiting", str(e) or "the torrent could not be added"
            self.say_once("%s: not seeded: %s" % (self.project.title, self.why))
            return False
        self.ih = ih
        self.mapping, self.it = m, it
        man = S._manifest_of(raw)
        self.title_of_release = " ".join(x for x in ((man.name, man.version) if man else ()) if x) or "its release"
        self.state, self.why = "seeding", ""
        real = [f for f in files if not f.pad]
        self.said = ""
        self.run.log("seeding %s (%s): %d of %d files offered%s" % (
            self.project.title, self.title_of_release, len(m.offered), len(real),
            ("; not offered: %d (missing here, changed, or the player's own)" % len(m.held)) if m.held else ""), self)
        if self.only:
            self.next_put = 0.0
        return True

    def stop(self, why="", leave=True):
        """The torrent out of the session (its files closed); what it sent is kept for the totals. When an update asks
        this run to pause, the peers met are not looked at first: the files close at once."""
        if self.handle is not None:
            try:
                self.sent_before += self.handle.status().total_payload_upload
                if self.kept is not None and not (self.seat is not None and self.seat.asked()):
                    self.kept.met(self.run.swarm.snapshot(self.handle, 1.0), self.run.swarm.own())
            except RuntimeError:
                pass
            try:
                self.run.swarm.close_files(self.handle)
            except RuntimeError:
                pass
            self.handle = None
            self.ih = None
        if leave and self.seat is not None:
            self.seat.leave()
        if self.kept is not None:
            self.kept.save()
        if why:
            self.state, self.why = "stopped", why

    def tick_pause(self):
        """Answer an update that asks this folder's seeding to pause (a request made while already paused is
        answered too), and go on once it ends. The run says it seeds the folder all along, seeding, waiting or
        paused, so an update always finds it."""
        if self.seat is None:
            return
        if self.paused and not self.seat.asked():
            self.seat.resumed()
            self.paused = False
            self.state, self.why = "waiting", ""
            self.start()
        else:
            self.answer_if_asked()

    def answer_if_asked(self):
        """Answer at once an update that asks this folder's seeding to pause (also while the run is busy with
        another project's files: Run.checker), and say again that the run seeds the folder. Going on again once
        the update ends is tick_pause's."""
        if self.seat is None:
            return
        asked = self.seat.request()
        if asked is not None:
            if not self.paused:
                self.stop(leave=False)
                self.seat.answer()
                self.paused, self.state, self.why = True, "paused", "an update is changing its folder"
                self.run.log("%s: seeding paused while an update changes its folder" % self.project.title, self)
            elif self.seat.answered < asked:
                self.seat.answer()
        self.seat.beat()

    def tick_watch(self):
        """A file offered that changed (or went) is offered again only once it is checked again: the release
        files too (an update, or a new publish, changed them)."""
        if self.handle is None:
            if not self.paused:
                self.start()
            return
        moved = folder.changed(self.root, self.mapping.watch)
        if self.kind == "store":
            for name in ("manifest.json", TORRENT_FILE, ITEM_FILE):
                if name not in self.mapping.watch:
                    self.mapping.watch[name] = self._stamp(name)
                elif self._stamp(name) != self.mapping.watch[name]:
                    moved.append(name)
        if moved:
            self.run.log("%s: %d of its files changed: looking at them again" % (self.project.title, len(moved)), self)
            self.stop(leave=False)
            self.start()

    def _stamp(self, rel):
        try:
            st = os.stat(os.path.join(self.root, *rel.split("/")))
            return (st.st_size, st.st_mtime_ns)
        except OSError:
            return (None, None)

    def tick_newer(self, stop_event):
        """Look for a newer release (the address's manifest, or the DHT's release item) in a thread of its own, so
        the run stays quick to pause and to stop, and switch to it here once one is found; put the release item in
        the DHT again for a project with no address."""
        now = time.time()
        if self.found is not None and not self.paused:
            files, self.found = self.found, None
            try:
                newer = item.read(files[ITEM_FILE], self.key(), self.tinfo["id"])
                if self.kept is not None and self.only:
                    self.kept.accept(newer.seq)
            except TorrentError:
                files = None
            if files:
                self._switch(files)
        if self.kind == "folder" and now >= self.next_newer and self.root and not self.paused and \
                (self.looking is None or not self.looking.is_alive()):
            self.next_newer = now + NEWER
            self.looking = threading.Thread(target=self._look, args=(stop_event,), name="deltapatcher-seed-look",
                                            daemon=True)
            self.looking.start()
        if self.only and self.it is not None and now >= self.next_put and \
                (self.putting is None or not self.putting.is_alive()):
            self.next_put = now + NEWER
            self.put_item(stop_event)

    def _look(self, stop_event):
        try:
            files = self._newer_by_dht(stop_event) if self.only else self._newer_by_address()
        except Exception as e:              # a thread of its own: said, and looked at again later
            self.run.log("%s: no newer release could be read: %s" % (self.project.title, e), self)
            return
        if files and not stop_event.is_set():
            self.found = files

    def _switch(self, files_by_name):
        """Seed a newer release a look found: this folder's torrent goes out first (Windows refuses to replace a file
        a torrent has open, and the torrent may be seeding next/ itself), then the newer release's files go into
        <state>/seeding/next/ and it is seeded. A failure is said and looked at again in a minute."""
        self.stop(leave=False)
        nxt = os.path.join(self.state_dir, pause.DIR, NEXT)
        try:
            shutil.rmtree(nxt, ignore_errors=True)
            _write_files(nxt, files_by_name)
        except OSError as e:
            self.next_newer = time.time() + RETRY
            self.run.log("%s: the newer release could not be written here (%s): it is looked at again in a minute"
                         % (self.project.title, e), self)
        self.start()

    def _newer_by_address(self):
        """The release the address serves now, when it is not the one seeded and the folder does not have it:
        its manifest and signature checked against the publisher key, its torrent and item belonging to it (a
        release still going up is looked at again later). The files to seed it with, or None."""
        src = Source(address_of(self.cfg))
        raw = src.get_manifest()
        sha = hashlib.sha256(raw).hexdigest()
        if self.it is not None and self.it.m == sha:
            return None
        key = self.key()
        sig = src.get_signature(sha)
        if sig is None:
            raise TorrentError("the address's release is not signed", "unsigned")
        if signing.key_text(signing.check_sig(sig, raw)) != key:
            raise TorrentError("the address's release is not signed with the publisher key", "bad-signature")
        tor = src.get_file(TORRENT_FILE, 64 << 20)
        item_bytes = src.get_file(ITEM_FILE, 4096)
        if not tor or not item_bytes:
            return None
        it = item.read(item_bytes, key, self.tinfo["id"])
        if it.m != sha or it.ih != S.make.infohash(tor):
            return None                                # a release still going up: looked at again later
        kept_raw = _read(os.path.join(self.state_dir, RELEASE_DIR, "manifest.json"), 64 << 20)
        if kept_raw is not None and hashlib.sha256(kept_raw).hexdigest() == sha:
            return None                                # the folder has it: the run seeds it once it looks again
        self.run.log("%s: the address serves a newer release: seeding what this folder has of it"
                     % self.project.title, self)
        return {"manifest.json": raw, "sigs/%s.sig" % sha: sig, TORRENT_FILE: tor, ITEM_FILE: item_bytes}

    def _newer_by_dht(self, stop_event):
        """A newer release item in the DHT, its manifest and signature from peers (a session of this look's own,
        read-only in the DHT), checked: the files to seed it with, or None."""
        key = self.key()
        if not key or refused(config.load()):         # refused since the run began: no session is made for a look
            return None
        src = S.TorrentSource(self.tinfo, key, self.root, self.run.general, log=lambda s: None, cancel=stop_event,
                              ip=self.run.ip)
        try:
            raw = src.get_manifest()
            if self.it is not None and src.item.seq <= self.it.seq:
                return None
            sha = hashlib.sha256(raw).hexdigest()
            if not src.sig or signing.key_text(signing.check_sig(src.sig, raw)) != key:
                raise TorrentError("the newer release is not signed with the publisher key", "bad-signature")
            tor = src._torrent_bytes()
            if not tor:
                return None
            self.run.log("%s: the DHT names a newer release (number %d): seeding what this folder has of it"
                         % (self.project.title, src.item.seq), self)
            return {"manifest.json": raw, "sigs/%s.sig" % sha: src.sig, TORRENT_FILE: tor,
                    ITEM_FILE: src.item.to_bytes()}
        finally:
            src.close()

    def put_item(self, stop_event=None):
        """The release item put in the DHT again, unchanged, in a thread of its own (it can take a while)."""
        doc = {b"k": self.it.k, b"salt": self.it.salt, b"seq": self.it.seq, b"sig": self.it.sig, b"v": self.it.v}
        target = item.target(self.it.k, self.tinfo["id"])
        seeds = self.tinfo["seeds"] + (self.kept.peers if self.kept else [])
        bind = self.run.swarm.ip or "0.0.0.0"
        title, log = self.project.title, self.run.log

        def put():
            try:
                start = self.run.swarm.dht_nodes() + S.resolve(seeds, time.time() + 10, stop_event)
                stored, higher = krpc.put_item(doc, target, start, bind, log=lambda s: log("%s: %s" % (title, s), self),
                                               stop=stop_event)
            except Exception as e:          # a thread of its own: said, and the next put tries again
                log("%s: the release item could not be put in the DHT: %s" % (title, e), self)
                return
            if higher > doc[b"seq"]:
                # a newer release, signed by the publisher key (put_item said so): put again only after the next
                # look, which a folder's run makes now and then switches to that release
                self.next_put = time.time() + NEWER
                self.next_newer = 0.0
            elif stored:
                log("%s: the release item (number %d) is in the DHT on %d nodes" % (title, doc[b"seq"], stored), self)
            else:
                self.next_put = time.time() + RETRY
                log("%s: no DHT node took the release item yet: it is put again in a minute" % title, self)
        self.putting = threading.Thread(target=put, name="deltapatcher-dht-put", daemon=True)
        self.putting.start()

    def status(self):
        row = dict(self.tag(), folder=self.root, kind=self.kind, state=self.state, why=self.why,
                   release=self.title_of_release, peers=0, upload_rate=0, sent=self.sent_before)
        if self.handle is not None:
            try:
                st = self.handle.status()
                row.update(peers=st.num_peers, upload_rate=st.upload_payload_rate,
                           sent=self.sent_before + st.total_payload_upload)
            except RuntimeError:
                pass
        return row


class Run(object):
    """A seed run: `chosen` is [(Project, settings)]; every_project: the run is `seed --all` (projects set to seed
    later join it, and a project set to seed = no leaves). log(text, seeded or None) and status(rows) say what
    happens; it runs until `stop` is set."""

    def __init__(self, chosen, general, log, status, stop, every_project=False, ip=None):
        self.general = general
        self.log_cb = log
        self.status_cb = status
        self.stop_event = stop
        self.every = every_project
        self.ip = ip
        self.swarm = None
        self.seeded = [Seeded(self, p, c) for p, c in chosen]
        self.started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def log(self, text, seeded=None):
        self.log_cb(text, seeded)

    def checker(self, mapping):
        """The check the mapping of `mapping` (a Seeded) asks between files and 1 MB pieces (folder.py): at most every
        CHECK seconds, every project of the run says again that it seeds its folder and answers an update that asks
        it to pause (one loop serves them all, and a mapping can take a while); True, the mapping stops, when its own
        folder is asked to pause or the run stops."""
        last = [0.0]

        def check():
            if self.stop_event.is_set():
                return True
            now = time.time()
            if now - last[0] < CHECK:
                return False
            last[0] = now
            stop = False
            for s in list(self.seeded):
                if s is not mapping:
                    s.answer_if_asked()
                elif s.seat is not None:
                    s.seat.beat()
                    stop = s.seat.asked()
            return stop
        return check

    def _dht_needed(self):
        return any(s.only for s in self.seeded)

    def run(self):
        g = self.general
        port = int(g.get("torrent_port") or 0)
        self.swarm = S.Swarm("all", port=port, dht=self._dht_needed(), dht_read_only=False, lsd=True,
                             port_mapping=bool(g.get("torrent_port_mapping")), upload_kbps=g.get("seed_upload_kbps", 0),
                             ip=self.ip, log=lambda s: self.log(s))
        try:
            if port and not self.swarm.port:
                self.swarm.listen_anywhere()
            if port and self.swarm.port and self.swarm.port != port:
                self.log("Warning: port %d is taken: seeding on port %d instead (others reach it only if it is "
                         "forwarded to this computer)" % (port, self.swarm.port))
            if not self.swarm.port:
                raise TorrentError("seeding cannot listen on any port here: %s"
                                   % ("; ".join(self.swarm.listen_errors[:2]) or "no reason given"), "no-port")
            self.log("seeding on port %d%s" % (self.swarm.port, " (port mapping on)" if g.get("torrent_port_mapping") else ""))
            for s in self.seeded:
                if s.only:
                    S.add_dht_nodes(self.swarm, s.tinfo["seeds"] + (s.kept.peers if s.kept else []))
                s.start()
            self._loop()
        finally:
            self.stop_event.set()                 # a look or a put going on ends too
            for s in self.seeded:
                s.stop()
            for s in self.seeded:
                for t in (s.looking, s.putting):
                    if t is not None:
                        t.join(1.0)
            write_seeding(None)
            if self.swarm is not None:
                self.swarm.close()
        return [s.status() for s in self.seeded]

    def _loop(self):
        last = {"status": 0.0, "settings": time.time(), "watch": time.time(), "pause": 0.0}
        while not self.stop_event.is_set():
            self.swarm.pump(int(POLL * 1000))
            now = time.time()
            if now - last["pause"] >= 0.5:
                last["pause"] = now
                for s in self.seeded:
                    s.tick_pause()
            if now - last["settings"] >= SETTINGS:
                last["settings"] = now
                if self._settings():
                    return
            if now - last["watch"] >= WATCH:
                last["watch"] = now
                for s in self.seeded:
                    if not s.paused:
                        s.tick_watch()
            for s in self.seeded:
                if self.stop_event.is_set():
                    break
                s.tick_newer(self.stop_event)
            if now - last["status"] >= STATUS:
                last["status"] = now
                rows = [s.status() for s in self.seeded]
                write_seeding({"started": self.started, "port": self.swarm.port, "projects": rows})
                self.status_cb(rows)

    def _settings(self):
        """Read the settings again: True when the run must end (torrents refused, or nothing left to seed)."""
        main = config.load()
        if refused(main):
            self.log("torrents are refused in the settings now: seeding stops")
            return True
        for s in list(self.seeded):
            if s.project.missing:
                self._drop(s, "its settings file is gone")
                continue
            cfg = config.load(s.project)
            now_seed = bool(cfg.get("seed"))
            if info(cfg) is None:
                self._drop(s, "it is not shared by torrent any more")
            elif not now_seed and (self.every or s.seed_then):
                self._drop(s, "seed is off for it now")
            else:
                s.seed_then = s.seed_then or now_seed
        if self.every:
            have = {os.path.normcase(s.project.path) for s in self.seeded}
            for p in projects.all_projects():
                if p.missing or os.path.normcase(p.path) in have:
                    continue
                cfg = config.load(p)
                if cfg.get("seed") and info(cfg) is not None:
                    s = Seeded(self, p, cfg)
                    self.seeded.append(s)
                    if s.only:
                        if not self.swarm.settings.get("enable_dht"):
                            self.swarm.settings["enable_dht"] = True
                            self.swarm.ses.apply_settings({"enable_dht": True, "dht_bootstrap_nodes": ""})
                        S.add_dht_nodes(self.swarm, s.tinfo["seeds"] + (s.kept.peers if s.kept else []))
                    self.log("%s is set to seed now: it joins this run" % p.title, s)
                    s.start()
        return not self.seeded

    def _drop(self, s, why):
        s.stop(why)
        self.seeded.remove(s)
        self.log("%s leaves this run: %s" % (s.project.title, why), s)
