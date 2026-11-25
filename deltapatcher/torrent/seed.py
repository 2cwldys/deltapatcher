"""deltapatcher seed: projects' releases shared with other players, until the run is stopped.

One session for the whole run, listening on every interface at general.torrent_port, with local discovery on and
the DHT on when a project in the run has no address. For each project:
  - the publisher's own project seeds its store as it is (manifest.json, sigs/, blobs/, release.torrent);
  - a player's project with seed_copy = yes (the default) seeds from a copy of its release kept for seeding, laid
    out like a store (seedcopy.py): the run fills it from the folder (only what it lacks, only files the engine has
    checked, never a protected or changed one; an update that asks stops the filling at once) and its seeding reads
    only the copy, so updates never pause it;
  - a player's project with seed_copy = no seeds from its installed folder, without copying: each blob is a file the
    release put there, the manifest and signature the copies the folder keeps in .deltapatcher/release/ (folder.py
    says which files are offered: only ones the engine has checked, never a protected or changed one).
Only the newest release is seeded: for a project with an address, the one the address serves now; with none, the
release item with the highest number. The run looks for a newer one every LOOK seconds (5 minutes), reading only the
small signed release item when nothing changed, and switches to it, offering what the folder has of it, plus its
manifest and signature (fetched and checked). A project with no address also has its release item put in the DHT
again every NEWER seconds (30 minutes: items expire), unchanged.

Every few seconds the run reads the settings again (a project set to seed = no leaves at once, refusing torrents
ends the run, seed_copy and seed_copies move a project between its folder and a copy), looks at the files it offers
(a file that changed is offered again only once it is checked again), answers updates that ask it to pause
(pause.py; a project seeded from a copy answers at once and goes on seeding), and says how each project is doing: in
the output, and in seeding.json beside the main settings file for the window and `deltapatcher projects`. Nothing is
ever downloaded into a folder or a copy: seeds are in upload mode. A copy goes once its project stops seeding from it
and two reads of the settings, at least SETTINGS seconds apart, say nothing needs it (a settings file caught empty or
half saved for one read deletes nothing), unless another project needs it or a settings file could not be read
whole (seedcopy.Needs).
"""

import hashlib
import os
import shutil
import threading
import time

from .. import config, projects, publisher, signing
from ..source import Source
from . import (ITEM_FILE, RELEASE_DIR, TORRENT_FILE, Kept, TorrentError, address_of, bencode, folder, info, item,
               krpc, pause, publisher_address, refused, seedcopy, write_seeding)
from . import session as S

STATUS = 5.0                 # seconds between the lines that say how each project is doing
SETTINGS = 3.0               # seconds between reads of the settings
WATCH = 5.0                  # seconds between looks at the files offered
NEWER = 1800.0               # seconds between puts of the release item in the DHT
LOOK = 300.0                 # seconds between looks for a newer release
RETRY = 60.0                 # seconds before a put no DHT node took is tried again (and a copy's filling)
LEAVE = 1.5                  # seconds after its stop was read that the deletes of a run's end stop, so its session
                             # closes and its result is written within two seconds: the rest goes at the next clean-up
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


def seeding_kind(cfg, general):
    """How a project is seeded, as its settings and general.seed_copies say now: ("store", its store) for the
    publisher's own project once its store has a torrent (a store is never copied); ("copy", where its seed copy is)
    for a player's project shared by torrent with seed_copy = yes; ("folder", its installed folder) otherwise."""
    store = store_of(cfg)
    if store and os.path.isfile(os.path.join(store, TORRENT_FILE)):
        return "store", store
    install = os.path.abspath(cfg["install_dir"]) if cfg.get("install_dir") else ""
    if install and cfg.get("seed_copy") and info(cfg) is not None:
        return "copy", seedcopy.place(cfg, general)
    return "folder", install


def _same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)) if a and b else a == b


def _key(path):
    return os.path.normcase(os.path.abspath(path))


def _cannot_move(general, root):
    """Why a project's copy cannot be made at `root`, where the settings put it now ('' when it can): seed_copies
    names a folder that cannot be used (set by hand: seedcopy.copies_problem), or the place itself cannot be
    (seedcopy.place_problem)."""
    return seedcopy.copies_problem(general.get("seed_copies", "")) or seedcopy.place_problem(root)


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
    """One project of a seed run. self.root is what libtorrent reads (the store, the installed folder, or the seed
    copy); self.folder is where the run says it seeds and keeps the folder's peers and kept release (the store, or
    the installed folder, also for a copy)."""

    def __init__(self, run, project, cfg):
        self.run = run
        self.project = project
        self.cfg = cfg
        # seed = yes at some time in this run: seed = no drops it then, also from a run that named it (--project,
        # where the command is the yes)
        self.seed_then = bool(cfg.get("seed"))
        self.tinfo = info(cfg)
        general = getattr(run, "general", None)
        self.kind, self.root = seeding_kind(cfg, general if isinstance(general, dict) else {})
        if self.kind == "store":
            self.only = not publisher_address(cfg)
        else:
            self.only = not address_of(cfg)
        self.folder = os.path.abspath(cfg["install_dir"]) if self.kind == "copy" else self.root
        self.state_dir = os.path.join(self.folder, ".deltapatcher") if self.folder else ""
        self.seat = pause.Seat(self.folder) if self.folder else None
        self.kept = Kept(self.state_dir, self.tinfo["id"]) if self.folder and self.tinfo else None
        # a seed copy (kind copy): how far it is, and whether an update of the folder holds its copying
        self.holding = False
        self.copy_state, self.copy_why = "ready", ""
        self.copy_done = self.copy_total = self.copy_bytes = 0
        self.stamps = {}                      # the folder's kept release when the copy last switched to it
        self.fill_after = 0.0                 # when the copy is filled again (0: when the release changes)
        self.stays = ""                       # why its copy stays where it is, though the settings put it elsewhere
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
        self.look_failed = set()              # why looks failed, each said once until a look works again
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
        k = publisher.trusted_key(self.cfg.get("trust_key"), self.folder if self.kind in ("folder", "copy") else "")
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
        step, the last one just before the torrent goes in. A project seeded from a copy: _start_copy."""
        if self.kind == "copy":
            return self._start_copy()
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

    # ---- seeding from a copy

    def _start_copy(self):
        """Seed the release now from the project's seed copy (seedcopy.py): first the blobs the copy lacks go in from
        the folder, while a torrent of the release before (if any) keeps seeding; then that torrent goes out, the
        release's own files go into the copy, it is mapped as a store and seeded, and what no release seeded now
        needs goes from it. A switch to a newer release a look found (next/) copies nothing: the folder has nothing
        new of it. An update that asks stops the copying until it ends, and never the seeding. Blobs that could not
        be copied now are tried again every RETRY seconds. Whenever a fill put blobs in, whatever ended it (a full
        drive, an update that asks, files that could not be copied), the copy is mapped again so they are offered;
        only the run's stop leaves them for its next start. Returns True when it is seeded."""
        if self.holding:
            if self.handle is None:
                self.state, self.why = "waiting", "an update is changing its folder"
            return self.handle is not None
        if not os.path.isdir(self.folder):
            return self._not_seeded("its folder is not there")
        self.seat.beat(force=True)
        stamps = self._release_stamps()
        try:
            raw, sig, tor, it, manifests = self.release_now()
            ti, files = S.read_torrent(tor)             # the item, signed by the publisher key, names the torrent
            ih = S.infohash_of(ti)
            if ih != it.ih or it.m != hashlib.sha256(raw).hexdigest():
                raise TorrentError("its torrent and its release item are not of one release", "nothing-to-seed")
        except TorrentError as e:
            return self._not_seeded(str(e))
        other = self._same_release(ih)
        if other is not None:
            return self._not_seeded("%s seeds the same release in this run" % other.project.title)
        general = self.run.general if isinstance(getattr(self.run, "general", None), dict) else {}
        copies_at = seedcopy.copies_dir(general)
        if copies_at and _same_path(os.path.dirname(self.root), copies_at):    # seed_copies set by hand: checked
            problem = seedcopy.copies_problem(general.get("seed_copies", ""))
            if problem:
                return self._not_seeded("its seed copy cannot be made: %s" % problem)
        try:
            seedcopy.prepare(self.root, self.tinfo["id"], self.folder, self.project.path)
        except (seedcopy.CopyError, OSError) as e:
            return self._not_seeded("its seed copy cannot be made: %s" % e)
        added = 0
        switching = self.handle is not None and len(manifests) > 1 and ih != self.ih
        if switching:
            pass                                        # a newer release a look found: nothing new to copy
        elif os.path.exists(os.path.join(self.state_dir, "rollback.new")):
            self.fill_after = time.time() + RETRY       # an update is changing the folder: it is copied after it
        else:
            self.fill_after = 0.0
            self.copy_state, self.copy_why = "copying", ""
            base = seedcopy.size_of(self.root)

            def copied(done, total, written):
                self.copy_done, self.copy_total, self.copy_bytes = done, total, base + written
            try:
                added, failed, why = seedcopy.fill(self.root, files, self.folder, manifests,
                                                   check=self.run.checker(self), progress=copied)
            except folder.Interrupted as e:             # the run stops, an update asks, or the settings changed
                added = getattr(e, "added", 0)          # blobs that went in before: offered unless the run stops
                self.answer_if_asked()                  # the folder's file it copied is closed now
                reason = "an update is changing its folder" if self.holding else ""
                self.copy_state, self.copy_why = "waiting", reason
                self.fill_after = time.time() + RETRY   # filled again once the update ends, else in a minute
                if not added or self.run.stop_event.is_set():
                    if self.handle is None:
                        self.state, self.why = "waiting", reason
                    return self.handle is not None
            except seedcopy.Full as e:
                added = e.added                         # blobs that went in before the drive was full: offered
                self.copy_state, self.copy_why = "full", str(e)
                self.fill_after = time.time() + RETRY
            except seedcopy.CopyGone as e:
                return self._not_seeded(str(e))
            except OSError as e:
                return self._not_seeded("its seed copy could not be filled: %s" % e)
            else:
                if failed:                              # held open by the game or an antivirus: tried again
                    self.copy_state = "copying"
                    self.copy_why = "%d file%s could not be copied now (%s): tried again in a minute" % (
                        failed, "" if failed == 1 else "s", why)
                    self.fill_after = time.time() + RETRY
                else:
                    self.copy_state, self.copy_why = "ready", ""
            if added:
                self.run.log("%s: %d files copied into its seed copy (%s)" % (self.project.title, added, self.root),
                             self)
        if self.handle is not None and ih == self.ih and not added:
            self.stamps = stamps                        # the same release, nothing new in the copy
            self.copy_bytes = seedcopy.size_of(self.root)
            return True
        if self.handle is not None:
            self.stop(leave=False)                      # Windows refuses to replace a file a torrent has open
        try:
            seedcopy.write_release(self.root, raw, sig, tor, it.to_bytes())
            m = folder.for_store(files, ti.num_pieces(), self.root, seedcopy.hash_cache(self.root),
                                 check=self.run.checker(self, reads_folder=False))
        except folder.Interrupted:                      # the run stops
            self.state, self.why = "waiting", ""
            return False
        except seedcopy.CopyGone as e:
            return self._not_seeded(str(e))
        except OSError as e:
            return self._not_seeded("its seed copy could not be written: %s" % e)
        self.seat.beat(force=True)
        peers = S.resolve(self.tinfo["seeds"] + (self.kept.peers if self.kept else []), time.time() + 3)
        try:
            handle = self.run.swarm.add(ti, save_path=self.root, mapping=m, peers=peers, seed=True, dht=self.only)
        except RuntimeError as e:                       # libtorrent refuses it (a torrent it has already, ...)
            self.state, self.why = "waiting", str(e) or "the torrent could not be added"
            self.say_once("%s: not seeded: %s" % (self.project.title, self.why))
            return False
        seedcopy.prune(self.root, files, raw, stop=self.run.stop_event.is_set)     # the release before is out
        self.copy_bytes = seedcopy.size_of(self.root)
        man = S._manifest_of(raw)
        self.title_of_release = " ".join(x for x in ((man.name, man.version) if man else ()) if x) or "its release"
        self.ih = ih
        self.mapping, self.it = m, it
        self.stamps = stamps
        self.state, self.why = "seeding", ""
        self.handle = handle                            # last: a project with a handle is seeded, all of it set
        real = [f for f in files if not f.pad]
        self.said = ""
        self.run.log("seeding %s (%s) from its seed copy: %d of %d files offered%s" % (
            self.project.title, self.title_of_release, len(m.offered), len(real),
            ("; not offered: %d (missing here, changed, or the player's own)" % len(m.held)) if m.held else ""), self)
        if self.copy_state != "ready" and self.copy_why:
            self.run.log("%s: its seed copy is not complete: %s" % (self.project.title, self.copy_why), self)
        if self.only:
            self.next_put = 0.0
        return True

    def _not_seeded(self, why):
        """Not seeded now, and why, said once; a torrent of a release before goes out, and a copy waits, saying why
        (never a frozen "copying")."""
        if self.handle is not None:
            self.stop(leave=False)
        if self.kind == "copy":
            self.copy_state, self.copy_why = "waiting", why
        self.state, self.why = "waiting", why
        self.say_once("%s: not seeded: %s" % (self.project.title, why))
        return False

    def _release_stamps(self):
        """The size and time of the files of the folder's kept release and of a newer one seeded from next/: when they
        move (an update ended, a look found a newer release), the copy switches."""
        out = {}
        for d in (os.path.join(self.state_dir, RELEASE_DIR), os.path.join(self.state_dir, pause.DIR, NEXT)):
            for name in ("manifest.json", TORRENT_FILE, ITEM_FILE):
                p = os.path.join(d, name)
                try:
                    st = os.stat(p)
                    out[p] = (st.st_size, st.st_mtime_ns)
                except OSError:
                    out[p] = None
        return out

    def _watch_copy(self):
        """A project seeded from a copy: the copy's offered files (one that changed or went: the copy is filled and
        mapped again) and the folder's kept release (an update ended, or a look found a newer release: the copy
        switches to it, filled while the torrent of the release before seeds). The game folder's own files are never
        looked at: the copy is apart from them."""
        if self.holding:
            return
        if self.handle is None:
            self.start()
            return
        moved = folder.changed(self.root, self.mapping.watch)
        if moved:
            self.run.log("%s: %d files of its seed copy changed: looking at them again" % (self.project.title,
                                                                                           len(moved)), self)
            self.stop(leave=False)
            self.start()
        elif self._release_stamps() != self.stamps or (self.fill_after and time.time() >= self.fill_after):
            self.start()

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
        paused, so an update always finds it. A project seeded from a copy is never paused: once the update ends, the
        copy takes what it changed (_start_copy)."""
        if self.seat is None:
            return
        if self.kind == "copy":
            self.answer_if_asked()
            if self.holding and not self.seat.asked():
                self.holding = False
                self.seat.resumed()
                self.start()
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
        the update ends is tick_pause's. A project seeded from a copy answers at once and holds its copying until the
        update ends: while its torrent is in, that its seeding goes on from the copy (it reads only the copy); with
        no torrent in, as a folder's seeding answers (nothing of the folder is open)."""
        if self.seat is None:
            return
        asked = self.seat.request()
        if asked is not None and self.kind == "copy":
            seeding = self.handle is not None
            if not self.holding or self.seat.answered < asked:
                self.seat.answer(copy=seeding)
            if not self.holding:
                self.holding = True
                self.run.log("%s: an update changes its folder: %s" % (
                    self.project.title, "seeding goes on from its copy" if seeding else "its copying waits for it"),
                    self)
        elif asked is not None:
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
        files too (an update, or a new publish, changed them). A project seeded from a copy: _watch_copy."""
        if self.kind == "copy":
            self._watch_copy()
            return
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
        """Look for a newer release every LOOK seconds (the address's release item, or the DHT's; a manifest only when
        the item names another release) in a thread of its own, so the run stays quick to pause and to stop, and
        switch to it here once one is found; put the release item in the DHT again every NEWER seconds for a project
        with no address."""
        now = time.time()
        if self.found is not None and not self.paused and not self.holding:
            files, self.found = self.found, None
            try:
                newer = item.read(files[ITEM_FILE], self.key(), self.tinfo["id"])
                if self.kept is not None and self.only:
                    self.kept.accept(newer.seq)
            except TorrentError:
                files = None
            if files:
                self._switch(files)
        if self.kind in ("folder", "copy") and now >= self.next_newer and self.folder and not self.paused and \
                (self.looking is None or not self.looking.is_alive()):
            self.next_newer = now + LOOK
            self.looking = threading.Thread(target=self._look, args=(stop_event,), name="deltapatcher-seed-look",
                                            daemon=True)
            self.looking.start()
        if self.only and self.it is not None and now >= self.next_put and \
                (self.putting is None or not self.putting.is_alive()):
            self.next_put = now + NEWER
            self.put_item(stop_event)

    def _look(self, stop_event):
        """One look for a newer release, in a thread of its own. A look that fails is looked at again later, its
        reason said once (not every LOOK seconds) until a look works again."""
        try:
            files = self._newer_by_dht(stop_event) if self.only else self._newer_by_address()
        except Exception as e:              # a thread of its own: said, and looked at again later
            why = str(e) or type(e).__name__
            if why not in self.look_failed:
                self.look_failed.add(why)
                self.run.log("%s: no newer release could be read: %s" % (self.project.title, why), self)
            return
        self.look_failed.clear()
        if files and not stop_event.is_set():
            self.found = files

    def _switch(self, files_by_name):
        """Seed a newer release a look found: this folder's torrent goes out first (Windows refuses to replace a file
        a torrent has open, and the torrent may be seeding next/ itself), then the newer release's files go into
        <state>/seeding/next/ and it is seeded. A failure is said and looked at again in a minute. A project seeded
        from a copy keeps seeding while next/ is written (its torrent reads only the copy), and its copy switches."""
        if self.kind != "copy":
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
        """The release the address serves now, when it is not the one seeded and the folder does not have it. Its
        release item (a few hundred bytes, signed by the publisher key) is read first: when it names the release
        seeded now, nothing more is read. Otherwise its manifest and signature are checked against the publisher key,
        and its torrent and item must belong to it (a release still going up is looked at again later). The files to
        seed it with, or None (also for an address with no release item)."""
        src = Source(address_of(self.cfg))
        key = self.key()
        item_bytes = src.get_file(ITEM_FILE, 4096)
        if not item_bytes:
            return None
        it = item.read(item_bytes, key, self.tinfo["id"])
        if self.it is not None and it.m == self.it.m:
            return None                                # the release seeded now: no manifest is fetched
        raw = src.get_manifest()
        sha = hashlib.sha256(raw).hexdigest()
        if self.it is not None and self.it.m == sha:
            return None
        sig = src.get_signature(sha)
        if sig is None:
            raise TorrentError("the address's release is not signed", "unsigned")
        if signing.key_text(signing.check_sig(sig, raw)) != key:
            raise TorrentError("the address's release is not signed with the publisher key", "bad-signature")
        tor = src.get_file(TORRENT_FILE, 64 << 20)
        if not tor:
            return None
        if it.m != sha or it.ih != S.make.infohash(tor):
            return None                                # a release still going up: looked at again later
        kept_raw = _read(os.path.join(self.state_dir, RELEASE_DIR, "manifest.json"), 64 << 20)
        if kept_raw is not None and hashlib.sha256(kept_raw).hexdigest() == sha:
            return None                                # the folder has it: the run seeds it once it looks again
        self.run.log("%s: the address serves a newer release: seeding what this folder has of it"
                     % self.project.title, self)
        return {"manifest.json": raw, "sigs/%s.sig" % sha: sig, TORRENT_FILE: tor, ITEM_FILE: item_bytes}

    def _newer_by_dht(self, stop_event):
        """A newer release item in the DHT, found and checked first: with the run's own session when its DHT is on
        (its nodes, asked with the small DHT client: Swarm.get_items), so a look that finds nothing newer makes no
        session; else with a session of this look's own (read-only in the DHT). Only an item numbered above the one
        seeded has its manifest and signature fetched from peers (a session of the look's own), checked: the files
        to seed it with, or None."""
        key = self.key()
        if not key or refused(config.load()):         # refused since the run began: no session is made for a look
            return None
        items = None
        swarm = self.run.swarm
        if swarm is not None and swarm.settings.get("enable_dht"):
            items = swarm.get_items(signing.parse_key(key), self.tinfo["id"], key,
                                    self.tinfo["seeds"] + (self.kept.peers if self.kept else []), cancel=stop_event,
                                    pump=False)
            if self.it is not None and all(i.seq <= self.it.seq for i in items):
                return None                            # nothing newer: no session is made, no manifest fetched
        src = S.TorrentSource(self.tinfo, key, self.folder, self.run.general, log=lambda s: None, cancel=stop_event,
                              ip=self.run.ip)
        try:
            found = src.find_item(items)
            if self.it is not None and found.seq <= self.it.seq:
                return None                            # nothing newer: no manifest is fetched
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
        """The project's row: its state, why, release, peers, rates; for a project seeded from a copy also "copy":
        {"path", "bytes" (what it holds), "state": copying, ready, full or waiting, "done" and "total" (the bytes of
        the release's blobs it holds, of all of them)}, and while it is not ready, why (unless the row says a reason
        of its own); a ready copy that cannot move where the settings put it (seed_copies) waits, saying why."""
        row = dict(self.tag(), folder=self.folder, kind=self.kind, state=self.state, why=self.why,
                   release=self.title_of_release, peers=0, upload_rate=0, sent=self.sent_before)
        if self.handle is not None:
            try:
                st = self.handle.status()
                row.update(peers=st.num_peers, upload_rate=st.upload_payload_rate,
                           sent=self.sent_before + st.total_payload_upload)
            except RuntimeError:
                pass
        if self.kind == "copy":
            row["copy"] = {"path": self.root, "bytes": self.copy_bytes, "state": self.copy_state,
                           "done": self.copy_done, "total": self.copy_total}
            if self.copy_state != "ready" and self.copy_why and not row["why"]:
                row["why"] = self.copy_why
            elif self.stays and not row["why"]:         # it cannot move where the settings put it: it waits
                row["copy"]["state"] = "waiting"
                row["why"] = self.stays
        return row


class Run(object):
    """A seed run: `chosen` is [(Project, settings)]; every_project: the run is `seed --all` (projects set to seed
    later join it, and a project set to seed = no leaves). log(text, seeded or None) and status(rows) say what
    happens; it runs until `stop` is set. stop_read: a list whose first item, once there, is when its stop was read
    (the command line's STOP_READ): its end's deletes stop LEAVE seconds after it."""

    def __init__(self, chosen, general, log, status, stop, every_project=False, ip=None, stop_read=None):
        self.general = general
        self.log_cb = log
        self.status_cb = status
        self.stop_event = stop
        self.every = every_project
        self.ip = ip
        self.stop_read = stop_read
        self.swarm = None
        self.seeded = [Seeded(self, p, c) for p, c in chosen]
        self.started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.status_at = 0.0
        self.left = {}               # copies this run stopped seeding from, until they go or are needed: {key: path}
        self.since = {}              # when a read of the settings first found nothing needs a copy: {key: time}
        self.saw_stop = None         # when the run saw its stop (its end's deletes stop LEAVE seconds after its read)
        self.refused = False         # it ends on torrents refused, read from a main settings file read whole

    def log(self, text, seeded=None):
        self.log_cb(text, seeded)

    def checker(self, mapping, reads_folder=True):
        """The check the mapping of `mapping` (a Seeded) asks between files and 1 MB pieces (folder.py): at most every
        CHECK seconds, every project of the run says again that it seeds its folder and answers an update that asks
        it to pause (one loop serves them all, and a mapping can take a while); True, the mapping stops, when its own
        folder is asked to pause or the run stops. For a project seeded from a copy, the copying (reads_folder) stops
        when an update asks (answered as soon as it has closed the folder's file: its seeding goes on) or its settings
        no longer copy it there, and the run's rows are written every STATUS seconds meanwhile, saying how far it is;
        its mapping of the copy (reads_folder False) answers an update at once and goes on. What the run writes
        meanwhile (its rows, its log lines) never reaches the copying as a file error, which would drop or delete a
        good blob; each project answers in a try of its own, so an error answering one never skips another."""
        last = [0.0]
        looked = [time.time()]

        def answers():
            stop = False
            for s in list(self.seeded):
                try:
                    if s is not mapping:
                        s.answer_if_asked()
                    elif s.seat is not None and s.kind == "copy" and not reads_folder:
                        s.answer_if_asked()             # its mapping reads only the copy: it goes on
                    elif s.seat is not None and s.kind == "copy":
                        s.seat.beat()
                        stop = s.seat.asked()           # answered once the copying has closed the folder's file
                    elif s.seat is not None:
                        s.seat.beat()
                        stop = s.seat.asked()
                except (OSError, ValueError):           # its pause files, or the output: the others still answer
                    pass
            return stop

        def check():
            if self.stop_event.is_set():
                return True
            now = time.time()
            if now - last[0] < CHECK:
                return False
            last[0] = now
            if mapping.kind != "copy":
                return answers()
            try:
                stop = answers()
                self._status()
                if reads_folder and not stop and now - looked[0] >= SETTINGS:
                    looked[0] = now
                    stop = not self._copies_on(mapping)
            except (OSError, ValueError):               # standard output closed or broken: the copying goes on
                stop = False
            return stop
        return check

    def _deleting(self):
        """The stop a delete of the run asks between files: the run's stop; every CHECK seconds meanwhile, every project
        of the run answers an update that asks it to pause (a big copy can take a while to delete)."""
        last = [0.0]

        def stop():
            if self.stop_event.is_set():
                return True
            now = time.time()
            if now - last[0] >= CHECK:
                last[0] = now
                for s in list(self.seeded):
                    try:
                        s.answer_if_asked()
                    except (OSError, ValueError):
                        pass
            return False
        return stop

    def _copies(self):
        """The copies this run's projects seed from: never deleted while it runs."""
        return [s.root for s in self.seeded if s.kind == "copy" and s.root]

    def _copies_on(self, s):
        """While a copy fills: its project still seeds from that copy as the settings are now (otherwise the copying
        stops, and the next read of the settings acts on it). Settings that cannot be read whole change nothing, and
        a copy that cannot move where they put it now stays where it is (_recast)."""
        main = config.load()
        if config.unreadable(config.path()) or config.unreadable(s.project.path):
            return True
        if refused(main) or s.project.missing:
            return False
        cfg = config.load(s.project)
        if info(cfg) is None or (not cfg.get("seed") and (self.every or s.seed_then)):
            return False
        general = dict(self.general, seed_copies=main.get("seed_copies", ""))
        kind, root = seeding_kind(cfg, general)
        if kind == "copy" and not _same_path(root, s.root):
            return bool(_cannot_move(general, root))
        return kind == "copy"

    def _status(self, force=False):
        """The rows of every project, in seeding.json and to status(): every STATUS seconds, or now with force."""
        now = time.time()
        if not force and now - self.status_at < STATUS:
            return
        self.status_at = now
        rows = [s.status() for s in self.seeded]
        write_seeding({"started": self.started, "port": self.swarm.port, "projects": rows})
        self.status_cb(rows)

    def _clean_copies(self):
        """At the run's start, a first read about the seed copies nothing needs (seedcopy.unneeded, which also looks
        after the list): none is deleted on this single read; each is noted, and goes once a later read at least
        SETTINGS seconds after it says so too (_judge), as any other copy. This run's own stay, and the run's stop ends
        the look between two copies."""
        try:
            found = seedcopy.unneeded(self.general, keep=self._copies(), but=os.getpid(), stop=self.stop_event.is_set)
        except OSError as e:
            found = []
            self.log("Warning: seed copies could not be cleaned up: %s" % e)
        now = time.time()
        for path in found:
            self.left[_key(path)] = path
            self.since.setdefault(_key(path), now)
        self._news()

    def _news(self):
        """What seed copies have to say once in a process (seedcopy.news: a list of them that cannot be read, a
        settings file that holds back every delete), said."""
        for text in seedcopy.news():
            self.log("Warning: %s" % text)

    def _judge(self, stop, ending=False, at_once=False):
        """A read of the settings about this run's copies: those it stopped seeding from or found at its start
        (self.left), and at its end (`ending`: every torrent out, its files closed) its projects' own. Needs says which
        of them nothing needs, this run's own use not counted; one it said so of at a read at least SETTINGS seconds
        before (self.since) goes now, so a settings file caught empty or half saved for one read deletes nothing, and a
        read that finds a copy needed forgets the first. While a copy is in use, by this run or another one going on,
        no first read is kept, so a later single misread never deletes it. at_once: no second read is needed (the run
        ends on torrents refused, read from a main settings file read whole: the person's no). stop(): asked between
        files; a delete cut short is finished by a later one (its marker goes last). Returns the copies deleted."""
        using = set() if ending else {_key(p) for p in self._copies()}
        paths = dict(self.left)
        if ending:
            paths.update({_key(p): p for p in self._copies()})
        if not paths:
            return []
        needs = seedcopy.Needs(config.load(), but=os.getpid())
        now = time.time()
        deleted = []
        for k in sorted(paths):
            path = paths[k]
            if k in using or not os.path.lexists(path) or needs.spares(path):
                self.left.pop(k, None)                  # in use, gone, or needed: the next clean-up decides on it
                self.since.pop(k, None)
                continue
            first = self.since.setdefault(k, now)
            if not at_once and now - first < SETTINGS:
                continue
            if stop():
                break
            if seedcopy.delete(path, stop):
                deleted.append(path)
                self.log("deleted the seed copy %s: no project seeds from it any more" % path)
            elif stop():
                break                                   # cut short: finished by a later read, or the next clean-up
            self.left.pop(k, None)                      # a file of it still open: the next clean-up finishes it
            self.since.pop(k, None)
        return deleted

    def _leave_copies(self):
        """At the run's end, every torrent out and its files closed: one more read of the settings about this run's
        copies (_judge, ending). A copy a read at least SETTINGS seconds before found nothing needs, and this one too
        (seed = no, seed_copy = no, moved by seed_copies), goes; when the run ends on torrents refused, read from a
        main settings file read whole, every copy nothing needs goes on this read. The deletes stop LEAVE seconds after
        the stop was read (stop_read, else when the run saw it): none left, nothing is deleted. What is left goes at
        the next clean-up (its marker stays to the last); a copy still wanted stays for the next run."""
        read = self.stop_read[0] if self.stop_read else self.saw_stop
        end = (read if read is not None else time.time()) + LEAVE

        def stop():
            return time.time() >= end
        if stop() or not (self.left or self._copies()):
            return
        try:
            self._judge(stop, ending=True, at_once=self.refused)
        except OSError as e:
            self.log("Warning: seed copies could not be cleaned up: %s" % e)
        self._news()

    def _dht_needed(self):
        return any(s.only for s in self.seeded)

    def run(self):
        g = self.general
        port = int(g.get("torrent_port") or 0)
        self._clean_copies()                      # the run's stop ends it between two copies
        if self.stop_event.is_set():              # stopped meanwhile: it ends here (no session, seat file or copy)
            self.saw_stop = time.time()
            return [s.status() for s in self.seeded]
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
            if any(s.kind == "copy" for s in self.seeded):
                self._status(force=True)          # the copies this run uses are in seeding.json before they fill
            for s in self.seeded:
                if s.only:
                    S.add_dht_nodes(self.swarm, s.tinfo["seeds"] + (s.kept.peers if s.kept else []))
                s.start()
            self._loop()
        finally:
            self.saw_stop = time.time()           # its end's deletes stop LEAVE seconds after its stop was read
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
            self._leave_copies()
        return [s.status() for s in self.seeded]

    def _loop(self):
        last = {"settings": time.time(), "watch": time.time(), "pause": 0.0}
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
            self._status()

    def _settings(self):
        """Read the settings again: True when the run must end (torrents refused, or nothing left to seed and no copy
        it left waiting for its second read). A project whose seed_copy changed, or whose copy moved (seed_copies, its
        folder), goes out and comes back as it is set now; one whose settings file cannot be read whole stays as it
        is. Then a read about the run's copies (_judge): a copy it left goes once two reads, at least SETTINGS seconds
        apart, say nothing needs it."""
        main = config.load()
        if refused(main):
            # read from a file read whole, it is the person's no: the run's end deletes its copies on this read
            self.refused = not config.unreadable(config.path())
            self.log("torrents are refused in the settings now: seeding stops")
            return True
        self.general = dict(self.general, seed_copies=main.get("seed_copies", ""))
        listed = None
        if self.every and any(s.kind == "copy" for s in self.seeded):
            listed = {os.path.normcase(p.path) for p in projects.all_projects() if not p.missing}
        for s in list(self.seeded):
            if s.project.missing:
                self._drop(s, "its settings file is gone")
                continue
            unreadable = config.unreadable(s.project.path)
            if unreadable:                          # what it chose now is not known: nothing changes
                s.say_once("%s: its settings file cannot be read whole (%s): it is seeded as it was"
                           % (s.project.title, unreadable))
                continue
            cfg = config.load(s.project)
            now_seed = bool(cfg.get("seed"))
            if info(cfg) is None:
                self._drop(s, "it is not shared by torrent any more")
            elif not now_seed and (self.every or s.seed_then):
                self._drop(s, "seed is off for it now")
            elif listed is not None and s.kind == "copy" and os.path.normcase(s.project.path) not in listed:
                self._drop(s, "it is not one of the projects any more")
            else:
                s.seed_then = s.seed_then or now_seed
                self._recast(s, cfg)
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
        try:
            self._judge(self._deleting())
        except OSError as e:
            self.log("Warning: seed copies could not be cleaned up: %s" % e)
        self._news()
        return not self.seeded and not self.left

    def _drop(self, s, why):
        s.stop(why)
        self.seeded.remove(s)
        self.log("%s leaves this run: %s" % (s.project.title, why), s)
        # its files are closed: the copy goes once two reads of the settings say nothing needs it (_judge), unless
        # another project (of this run or not) needs it, or a settings file could not be read whole
        if s.kind == "copy" and s.root:
            self.left[_key(s.root)] = s.root

    def _recast(self, s, cfg):
        """A project seeded from a copy that should not be any more (seed_copy = no), or the other way round, or whose
        copy moved (seed_copies, its folder): it goes out and comes back as it is set now, and a copy it leaves goes
        once two reads of the settings say nothing needs it (_judge). A copy that cannot be made where the settings
        put it now (seed_copies set by hand to a folder that cannot be used: _cannot_move) is checked first: the
        project then seeds on from the copy it has, and its row says why. Anything else stays as it is."""
        kind, root = seeding_kind(cfg, self.general)
        if "copy" not in (kind, s.kind) or (kind == s.kind and _same_path(root, s.root)):
            s.stays = ""
            return
        if kind == "copy" and s.kind == "copy":
            problem = _cannot_move(self.general, root)
            if problem:
                stays = "its seed copy stays at %s: %s" % (s.root, problem)
                if stays != s.stays:
                    self.log("%s: %s" % (s.project.title, stays), s)
                s.stays = stays
                return
        fresh = Seeded(self, s.project, cfg)
        fresh.seed_then, fresh.sent_before = s.seed_then, s.sent_before
        s.stop(leave=not _same_path(s.folder, fresh.folder))
        self.seeded[self.seeded.index(s)] = fresh
        if fresh.kind == "store":
            self.log("%s seeds its store now" % s.project.title, fresh)
        elif fresh.kind != "copy":
            self.log("%s seeds from its folder now (seed_copy = no)" % s.project.title, fresh)
        elif s.kind != "copy":
            self.log("%s seeds from a copy of its release now (seed_copy = yes): %s" % (s.project.title, fresh.root),
                     fresh)
        else:
            self.log("%s: its seed copy moves to %s" % (s.project.title, fresh.root), fresh)
        if s.kind == "copy" and s.root:
            self.left[_key(s.root)] = s.root        # its files are closed: it goes once two reads say so (_judge)
        if fresh.only:
            S.add_dht_nodes(self.swarm, fresh.tinfo["seeds"] + (fresh.kept.peers if fresh.kept else []))
        fresh.start()
