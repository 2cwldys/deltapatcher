"""Deploy: send a published store to the place clients read it from, sending only missing blobs.

The order keeps clients safe while a deploy runs:
  1. ask the target which blobs and parts indexes it has (name and size), and read its current and previous
     manifests
  2. upload the missing ones as <sha>.part, renamed to <sha> when complete
  3. check that every blob the new manifest names is there at its size; then the same for its parts indexes
     (parts/<sha>, each checked against its SHA-256 here first: one missing or damaged here is not sent, and players
     download that file whole when it changes)
  4. the signature, the .htaccess and, for a release shared by torrent, release.torrent and release.item; then
     keep the target's current manifest as manifest.previous.json, upload the new one beside it,
     check its size, then put it in place (an atomic rename where the server supports it)
  5. remove blobs and parts indexes that none of the new, current and previous manifests name, and stale .part
     files; when a manifest on the target cannot be read, nothing is removed
A client that read the old manifest a moment ago can still fetch every blob it names. If anything
fails before step 4, the target still serves the release it served before.
"""

import os
import shutil
import tempfile

import re

from . import hashing, ssh
from .manifest import BLOB_DIR, HEX64, MANIFEST_NAME, PARTS_DIR, PREVIOUS_NAME, Manifest, ManifestError
from .parts import INDEX_MAX
from .publish import HTACCESS, human
from .signing import SIG_DIR, sig_name

SIG_RE = re.compile(r"^[0-9a-f]{64}\.sig$")
TORRENT_FILES = ("release.torrent", "release.item")      # a release shared by torrent: sent before its manifest


def _sig_name(n):
    return bool(SIG_RE.match(n))


class DeployError(Exception):
    pass


def _blob_name(n):
    return bool(HEX64.match(n) or (n.endswith(".part") and HEX64.match(n[:-5])))


class FolderTarget(object):
    """A folder: a local web root, a mounted share, or a test."""

    def __init__(self, root):
        self.root = os.path.abspath(root)

    def __str__(self):
        return self.root

    def list_blobs(self):
        d = os.path.join(self.root, BLOB_DIR)
        os.makedirs(d, exist_ok=True)
        # only blob files: anything else in the folder (a web.config, a README) is not ours
        return {fn: os.path.getsize(os.path.join(d, fn)) for fn in os.listdir(d)
                if _blob_name(fn) and os.path.isfile(os.path.join(d, fn))}

    def get_file(self, name):
        """The bytes of <root>/<name>, None when there is no such file; DeployError when it cannot be read."""
        p = os.path.join(self.root, name)
        if not os.path.exists(p):
            return None
        try:
            with open(p, "rb") as f:
                return f.read()
        except OSError as e:
            raise DeployError("cannot read %s: %s" % (p, e))

    def get_blobs(self, shas, sizes, into, progress):
        """Copy blobs/<sha> of each sha into the folder `into` (a backup reads them; the caller checks them)."""
        for sha in shas:
            if not HEX64.match(sha):
                raise DeployError("not a blob name: %r" % sha)
            try:
                shutil.copyfile(os.path.join(self.root, BLOB_DIR, sha), os.path.join(into, sha))
            except OSError as e:
                raise DeployError("cannot read the blob %s in %s: %s" % (sha[:12], self.root, e))
            progress(sha)

    def put_blobs(self, blob_dir, shas, progress):
        d = os.path.join(self.root, BLOB_DIR)
        for sha in shas:
            part = os.path.join(d, sha + ".part")
            shutil.copyfile(os.path.join(blob_dir, sha), part)
            os.replace(part, os.path.join(d, sha))
            progress(sha)

    def put_manifests(self, new, previous):
        if previous is not None:
            p = os.path.join(self.root, PREVIOUS_NAME)
            with open(p + ".new", "wb") as f:
                f.write(previous)
            os.replace(p + ".new", p)
        p = os.path.join(self.root, MANIFEST_NAME)
        with open(p + ".new", "wb") as f:
            f.write(new)
        os.replace(p + ".new", p)
        return True

    def remove(self, names):
        for n in names:
            if _blob_name(n):
                try:
                    os.remove(os.path.join(self.root, BLOB_DIR, n))
                except OSError:
                    pass

    def list_sigs(self):
        d = os.path.join(self.root, SIG_DIR)
        return sorted(fn for fn in os.listdir(d) if _sig_name(fn)) if os.path.isdir(d) else []

    def put_extra(self, rel, data):
        """A small file beside the release (.htaccess, sigs/<sha>.sig), replaced in one step."""
        p = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".new", "wb") as f:
            f.write(data)
        os.replace(p + ".new", p)

    def remove_sigs(self, names):
        for n in names:
            if _sig_name(n):
                try:
                    os.remove(os.path.join(self.root, SIG_DIR, n))
                except OSError:
                    pass

    def list_parts(self):
        """{name: size} of the parts indexes (and .part leftovers) in parts/; {} when there is no such folder."""
        d = os.path.join(self.root, PARTS_DIR)
        if not os.path.isdir(d):
            return {}
        return {fn: os.path.getsize(os.path.join(d, fn)) for fn in os.listdir(d)
                if _blob_name(fn) and os.path.isfile(os.path.join(d, fn))}

    def put_parts(self, parts_dir, shas, progress):
        d = os.path.join(self.root, PARTS_DIR)
        os.makedirs(d, exist_ok=True)
        for sha in shas:
            part = os.path.join(d, sha + ".part")
            shutil.copyfile(os.path.join(parts_dir, sha), part)
            os.replace(part, os.path.join(d, sha))
            progress(sha)

    def remove_parts(self, names):
        for n in names:
            if _blob_name(n):
                try:
                    os.remove(os.path.join(self.root, PARTS_DIR, n))
                except OSError:
                    pass

    def get_parts(self, shas, into, progress):
        """Copy parts/<sha> of each sha the folder has into the folder `into`; one it lacks is passed over (a backup
        reads them; the caller checks which came, and each one's SHA-256)."""
        for sha in shas:
            if not HEX64.match(sha):
                raise DeployError("not a parts index name: %r" % sha)
            p = os.path.join(self.root, PARTS_DIR, sha)
            if os.path.isfile(p):
                try:
                    shutil.copyfile(p, os.path.join(into, sha))
                except OSError as e:
                    raise DeployError("cannot read the parts index %s in %s: %s" % (sha[:12], self.root, e))
            progress(sha)


class SftpTarget(object):
    """A host reached with sftp (OpenSSH), the folder its web server serves."""

    CHUNK_FILES = 40
    CHUNK_BYTES = 256 << 20
    MIN_RATE = 50 * 1024      # bytes per second assumed when sizing a batch's time limit

    def __init__(self, target):
        if not target.remote_root:
            raise DeployError("no remote folder: the folder on the server that the web server serves")
        self.t = target
        self.root = target.remote_root

    def __str__(self):
        return "%s:%s" % (self.t, self.root)

    def _run(self, cmds, cwd=None, what="sftp", timeout=600):
        r = ssh.sftp(self.t, cmds, cwd=cwd, timeout=timeout)
        if r.returncode != 0:
            raise DeployError("%s failed: %s" % (what, ssh.explain(r)))
        return r

    def list_blobs(self):
        root, blobs = self.root, self.root + "/" + BLOB_DIR
        r = self._run(["-mkdir %s" % ssh.q(root), "-mkdir %s" % ssh.q(blobs), "ls -ln %s" % ssh.q(blobs)],
                      what="listing %s" % blobs)
        return ssh.parse_ls(r.stdout)

    def get_file(self, name):
        d = tempfile.mkdtemp(prefix="dp-get-")
        try:
            r = ssh.sftp(self.t, ["get %s %s" % (ssh.q(self.root + "/" + name), ssh.q("got.json"))], cwd=d, timeout=300)
            local = os.path.join(d, "got.json")
            if r.returncode == 0 and os.path.isfile(local):
                with open(local, "rb") as f:
                    return f.read()
            text = ((r.stderr or "") + (r.stdout or "")).lower()
            if "not found" in text or "no such file" in text:
                return None
            raise DeployError("cannot read %s on the server: %s" % (name, ssh.explain(r)))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def get_blobs(self, shas, sizes, into, progress):
        """Download blobs/<sha> of each sha into the folder `into`, a batch at a time; the local names are the
        bare hashes, so no local path reaches sftp (a backup reads them; the caller checks them)."""
        chunk, size = [], 0
        for sha in shas:
            if not HEX64.match(sha):
                raise DeployError("not a blob name: %r" % sha)
            chunk.append(sha)
            size += sizes.get(sha, 0)
            if len(chunk) >= self.CHUNK_FILES or size >= self.CHUNK_BYTES:
                self._get_chunk(chunk, size, into, progress)
                chunk, size = [], 0
        if chunk:
            self._get_chunk(chunk, size, into, progress)

    def _get_chunk(self, chunk, size, into, progress):
        cmds = ["get %s %s" % (ssh.q("%s/%s/%s" % (self.root, BLOB_DIR, sha)), ssh.q(sha)) for sha in chunk]
        self._run(cmds, cwd=into, what="download", timeout=600 + size // self.MIN_RATE)
        for sha in chunk:
            progress(sha)

    def put_blobs(self, blob_dir, shas, progress):
        chunk, size = [], 0
        sizes = {s: os.path.getsize(os.path.join(blob_dir, s)) for s in shas}
        for sha in shas:
            chunk.append(sha)
            size += sizes[sha]
            if len(chunk) >= self.CHUNK_FILES or size >= self.CHUNK_BYTES:
                self._put_chunk(blob_dir, chunk, size, progress)
                chunk, size = [], 0
        if chunk:
            self._put_chunk(blob_dir, chunk, size, progress)

    def _put_chunk(self, blob_dir, chunk, size, progress, folder=BLOB_DIR):
        # a parts folder may not be there yet: made first (blobs/ was made when the blobs were listed)
        cmds = [] if folder == BLOB_DIR else ["-mkdir %s" % ssh.q("%s/%s" % (self.root, folder))]
        for sha in chunk:
            if not HEX64.match(sha):
                raise DeployError("not a blob name: %r" % sha)
            remote = "%s/%s/%s" % (self.root, folder, sha)
            # put runs in the blob folder, so the local name is the bare hash: no local path reaches sftp
            cmds.append("put %s %s" % (ssh.q(sha), ssh.q(remote + ".part")))
            cmds.append("rename %s %s" % (ssh.q(remote + ".part"), ssh.q(remote)))
        self._run(cmds, cwd=blob_dir, what="upload", timeout=600 + size // self.MIN_RATE)
        for sha in chunk:
            progress(sha)

    def put_manifests(self, new, previous):
        """Returns True when the switch was one atomic rename."""
        d = tempfile.mkdtemp(prefix="dp-put-")
        try:
            with open(os.path.join(d, "manifest.json.new"), "wb") as f:
                f.write(new)
            m = self.root + "/" + MANIFEST_NAME
            # 1. upload everything beside the live files; a failure here changes nothing clients read
            cmds = []
            if previous is not None:
                with open(os.path.join(d, "manifest.previous.json.new"), "wb") as f:
                    f.write(previous)
                cmds.append("put %s %s" % (ssh.q("manifest.previous.json.new"), ssh.q(self.root + "/" + PREVIOUS_NAME + ".new")))
            cmds += ["put %s %s" % (ssh.q("manifest.json.new"), ssh.q(m + ".new")), "ls -ln %s" % ssh.q(self.root)]
            r = self._run(cmds, cwd=d, what="uploading the manifest (the server still serves the old one)")
            sizes = ssh.parse_ls(r.stdout, blobs_only=False)
            if sizes.get(MANIFEST_NAME + ".new") != len(new):
                raise DeployError("the uploaded manifest is %s bytes, not %d: the server still serves the old one"
                                  % (sizes.get(MANIFEST_NAME + ".new"), len(new)))
            # 2. the previous one (best effort: its loss only shortens the grace for clients mid-update)
            if previous is not None:
                p = self.root + "/" + PREVIOUS_NAME
                ssh.sftp(self.t, ["-rm %s" % ssh.q(p), "rename %s %s" % (ssh.q(p + ".new"), ssh.q(p))], cwd=d)
            # 3. the switch. OpenSSH's sftp renames with posix-rename@openssh.com when the server has it:
            #    the old manifest is replaced in one step
            r = ssh.sftp(self.t, ["rename %s %s" % (ssh.q(m + ".new"), ssh.q(m))], cwd=d)
            if r.returncode == 0:
                return True
            # a server without posix-rename cannot rename over a file: remove, then rename (the new file
            # is already complete on the server, so the gap is one round trip)
            r2 = ssh.sftp(self.t, ["rm %s" % ssh.q(m), "rename %s %s" % (ssh.q(m + ".new"), ssh.q(m))], cwd=d)
            if r2.returncode != 0:
                raise DeployError("the new manifest could not be put in place: %s" % ssh.explain(r2))
            return False
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def remove(self, names):
        names = [n for n in names if _blob_name(n)]
        for i in range(0, len(names), 200):
            cmds = ["-rm %s" % ssh.q("%s/%s/%s" % (self.root, BLOB_DIR, n)) for n in names[i:i + 200]]
            ssh.sftp(self.t, cmds)

    def list_sigs(self):
        d = self.root + "/" + SIG_DIR
        r = self._run(["-mkdir %s" % ssh.q(self.root), "-mkdir %s" % ssh.q(d), "ls -ln %s" % ssh.q(d)],
                      what="listing %s" % d)
        return sorted(n for n in ssh.parse_ls(r.stdout, blobs_only=False) if _sig_name(n))

    def put_extra(self, rel, data):
        """A small file beside the release (.htaccess, sigs/<sha>.sig, release.torrent, release.item): uploaded
        beside, then renamed in."""
        name = rel.rsplit("/", 1)[-1]
        if not (name == ".htaccess" or _sig_name(name) or rel in TORRENT_FILES):
            raise DeployError("not a file deploy sends: %r" % rel)
        d = tempfile.mkdtemp(prefix="dp-extra-")
        try:
            with open(os.path.join(d, "upload.tmp"), "wb") as f:
                f.write(data)
            remote = self.root + "/" + rel
            pre = ["-mkdir %s" % ssh.q(self.root + "/" + SIG_DIR)] if rel.startswith(SIG_DIR + "/") else []
            self._run(pre + ["put %s %s" % (ssh.q("upload.tmp"), ssh.q(remote + ".new"))], cwd=d, what="uploading %s" % rel)
            if ssh.sftp(self.t, ["rename %s %s" % (ssh.q(remote + ".new"), ssh.q(remote))]).returncode != 0:
                self._run(["-rm %s" % ssh.q(remote), "rename %s %s" % (ssh.q(remote + ".new"), ssh.q(remote))],
                          what="putting %s in place" % rel)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def remove_sigs(self, names):
        names = [n for n in names if _sig_name(n)]
        if names:
            ssh.sftp(self.t, ["-rm %s" % ssh.q("%s/%s/%s" % (self.root, SIG_DIR, n)) for n in names])

    def list_parts(self):
        """{name: size} of the parts indexes (and .part leftovers) in parts/ on the server; {} when it has no such
        folder (the listing may fail: it is never made just to be listed)."""
        d = self.root + "/" + PARTS_DIR
        r = ssh.sftp(self.t, ["-ls -ln %s" % ssh.q(d)], timeout=600)
        if r.returncode != 0:
            raise DeployError("listing %s failed: %s" % (d, ssh.explain(r)))
        return ssh.parse_ls(r.stdout)

    def put_parts(self, parts_dir, shas, progress):
        chunk, size = [], 0
        for sha in shas:
            chunk.append(sha)
            size += os.path.getsize(os.path.join(parts_dir, sha))
            if len(chunk) >= self.CHUNK_FILES or size >= self.CHUNK_BYTES:
                self._put_chunk(parts_dir, chunk, size, progress, PARTS_DIR)
                chunk, size = [], 0
        if chunk:
            self._put_chunk(parts_dir, chunk, size, progress, PARTS_DIR)

    def remove_parts(self, names):
        names = [n for n in names if _blob_name(n)]
        for i in range(0, len(names), 200):
            ssh.sftp(self.t, ["-rm %s" % ssh.q("%s/%s/%s" % (self.root, PARTS_DIR, n)) for n in names[i:i + 200]])

    def get_parts(self, shas, into, progress):
        """Download parts/<sha> of each sha the server has into the folder `into`, a batch at a time; one it lacks is
        passed over. The local names are the bare hashes, so no local path reaches sftp (a backup reads them; the
        caller checks which came, and each one's SHA-256). Batches and their time limits are a batch of blobs': at most
        CHUNK_FILES and CHUNK_BYTES a batch, 600 s plus its bytes at MIN_RATE; no manifest gives an index's size, so
        each counts as the largest an index may be (INDEX_MAX)."""
        for sha in shas:
            if not HEX64.match(sha):
                raise DeployError("not a parts index name: %r" % sha)
        per = max(1, min(self.CHUNK_FILES, self.CHUNK_BYTES // INDEX_MAX))
        for i in range(0, len(shas), per):
            chunk = shas[i:i + per]
            self._run(["-get %s %s" % (ssh.q("%s/%s/%s" % (self.root, PARTS_DIR, sha)), ssh.q(sha)) for sha in chunk],
                      cwd=into, what="download", timeout=600 + len(chunk) * INDEX_MAX // self.MIN_RATE)
            for sha in chunk:
                progress(sha)


class DeployResult(object):
    def __init__(self):
        self.named = 0
        self.had = 0
        self.sent = 0
        self.sent_bytes = 0
        self.removed = 0
        self.atomic = True
        self.dry_run = False
        self.parts_sent = 0      # parts indexes sent (or that would be)
        self.parts_removed = 0   # parts indexes removed: no manifest on the target names them

    def summary(self):
        verb = "would send" if self.dry_run else "sent"
        return ("%d blobs named, %d already there; %s %d (%s); %s %d no longer named"
                % (self.named, self.had, verb, self.sent, human(self.sent_bytes),
                   "would remove" if self.dry_run else "removed", self.removed)
                + ("; %s %d parts index%s" % (verb, self.parts_sent, "" if self.parts_sent == 1 else "es")
                   if self.parts_sent else ""))


def deploy(store, target, prune=True, dry_run=False, progress=None, log=None, blob_dir=None, cancel=None,
           parts_dir=None):
    """Send the store's release to `target` (FolderTarget or SftpTarget). Returns a DeployResult. `blob_dir`:
    where the blobs are, when not in the store's own blobs folder (a backup's pool); `parts_dir` the same for the
    parts indexes. `cancel`: an optional threading.Event; set, the deploy stops before the new manifest goes up, so
    players keep the release the target served before."""
    log = log or (lambda s: None)
    progress = progress or (lambda d, t, l: None)
    store = os.path.abspath(store)
    blob_dir = os.path.abspath(blob_dir) if blob_dir else os.path.join(store, BLOB_DIR)
    parts_dir = os.path.abspath(parts_dir) if parts_dir else os.path.join(store, PARTS_DIR)
    try:
        with open(os.path.join(store, MANIFEST_NAME), "rb") as f:
            new_raw = f.read()
        new = Manifest.from_json(new_raw)
    except (OSError, ManifestError) as e:
        raise DeployError("no usable manifest in %s (publish first): %s" % (store, e))
    want = new.blobs()
    missing_local = [s for s in want if not os.path.isfile(os.path.join(blob_dir, s))]
    if missing_local:
        raise DeployError("%d blobs the manifest names are not in %s (publish again): %s..."
                          % (len(missing_local), blob_dir, missing_local[0][:12]))
    # the parts indexes the manifest names, each checked here: one missing or damaged is not sent (an index is never
    # needed: players then download its file whole when it changes)
    want_parts = {}
    for psha, path in sorted(new.parts().items()):
        try:
            got, size = hashing.sha256_file(os.path.join(parts_dir, psha))
        except OSError:
            got, size = None, 0
        if got == psha:
            want_parts[psha] = size
        else:
            log("Warning: the parts index of %s is %s in %s: it is not sent, and players download that file whole "
                "when it changes (publish again to make it)" % (path, "damaged" if got else "missing", parts_dir))

    res = DeployResult()
    res.dry_run = dry_run
    res.named = len(want)
    log("asking %s which blobs it has" % target)
    have = target.list_blobs()
    send = sorted(s for s, size in want.items() if have.get(s) != size)
    wrong = [s for s in send if s in have]          # there, but the wrong size: removed before the upload
    res.had = len(want) - len(send)
    res.sent = len(send)
    res.sent_bytes = sum(want[s] for s in send)
    log("%d of %d blobs to send (%s)" % (len(send), len(want), human(res.sent_bytes)))
    have_parts = target.list_parts()
    send_parts = sorted(s for s, size in want_parts.items() if have_parts.get(s) != size)
    wrong_parts = [s for s in send_parts if s in have_parts]
    res.parts_sent = len(send_parts)
    if send_parts:
        log("%d of %d parts indexes to send" % (len(send_parts), len(want_parts)))

    # what the target serves now, and the release before it: their blobs and parts indexes stay
    old_raw = target.get_file(MANIFEST_NAME)
    prev_raw = target.get_file(PREVIOUS_NAME)
    keep = set(want)
    keep_parts = set(new.parts())
    prune_ok = prune
    old = None
    for raw, what in ((old_raw, "current"), (prev_raw, "previous")):
        if raw is None:
            continue
        try:
            m = Manifest.from_json(raw)
            keep.update(m.blobs())
            keep_parts.update(m.parts())
            if what == "current":
                old = m
        except ManifestError as e:
            log("the target's %s manifest cannot be read (%s): no blob is removed this time" % (what, e))
            prune_ok = False
    if old_raw is None and any(HEX64.match(n) for n in have):
        log("the target has blobs but no manifest: no blob is removed this time")
        prune_ok = False
    stale = sorted(n for n in have if n not in keep and n not in wrong)
    res.removed = len(stale) if prune_ok else 0
    stale_parts = sorted(n for n in have_parts if n not in keep_parts and n not in wrong_parts)
    res.parts_removed = len(stale_parts) if prune_ok else 0

    if dry_run:
        log("dry run: nothing was sent or removed")
        return res

    def stop_here(what):
        if cancel is not None and cancel.is_set():
            raise DeployError("stopped %s: players still get the release the target served before" % what)

    stop_here("before anything was sent")
    done = [0]

    def tick(sha):
        done[0] += want[sha]
        progress(done[0], res.sent_bytes, "uploading")

    if wrong:
        log("%d blobs on the target have the wrong size: replacing them" % len(wrong))
        target.remove(wrong)
    progress(0, res.sent_bytes, "uploading")
    target.put_blobs(blob_dir, send, tick)

    after = target.list_blobs()
    bad = [s for s, size in want.items() if after.get(s) != size]
    if bad:
        raise DeployError("%d blobs are still missing or the wrong size on the target after the upload (%s...): "
                          "the manifest was NOT changed" % (len(bad), bad[0][:12]))
    # the parts indexes go up with the blobs, before the manifest that names them
    if send_parts:
        stop_here("before the parts indexes were sent")
        if wrong_parts:
            target.remove_parts(wrong_parts)
        target.put_parts(parts_dir, send_parts, lambda sha: None)
        after_parts = target.list_parts()
        bad = [s for s in send_parts if after_parts.get(s) != want_parts[s]]
        if bad:
            raise DeployError("%d parts indexes are still missing or the wrong size on the target after the upload "
                              "(%s...): the manifest was NOT changed" % (len(bad), bad[0][:12]))
        log("%d parts index%s in place" % (len(send_parts), "" if len(send_parts) == 1 else "es"))

    # the release's signature goes up before the manifest it signs, and so does the .htaccess
    new_sig = sig_name(hashing.sha256_bytes(new_raw))
    sig_local = os.path.join(store, SIG_DIR, new_sig)
    if os.path.isfile(sig_local):
        with open(sig_local, "rb") as f:
            sig_data = f.read()
        if target.get_file(SIG_DIR + "/" + new_sig) != sig_data:
            target.put_extra(SIG_DIR + "/" + new_sig, sig_data)
            log("signature in place")
    ht_local = os.path.join(store, HTACCESS)
    if os.path.isfile(ht_local):
        with open(ht_local, "rb") as f:
            ht = f.read()
        if target.get_file(HTACCESS) != ht:
            target.put_extra(HTACCESS, ht)
            log("%s in place" % HTACCESS)
    for name in TORRENT_FILES:
        local = os.path.join(store, name)
        if os.path.isfile(local):
            with open(local, "rb") as f:
                data = f.read()
            if target.get_file(name) != data:
                target.put_extra(name, data)
                log("%s in place" % name)

    if old_raw == new_raw:
        log("the target already serves this manifest")
    else:
        stop_here("before the new manifest went up (its files are there: sending it again finishes it)")
        # the same files under a new date or name: the target's previous release stays the previous one
        rotate = old_raw if (old is not None and old.files != new.files) else None
        res.atomic = target.put_manifests(new_raw, rotate)
        log("manifest in place" + ("" if res.atomic else " (the server has no atomic rename: it was missing for a moment)"))
    if prune_ok and stale:
        target.remove(stale)
        log("removed %d blobs no release names" % len(stale))
    if prune_ok and stale_parts:
        target.remove_parts(stale_parts)
        log("removed %d parts index%s no release names" % (len(stale_parts), "" if len(stale_parts) == 1 else "es"))
    if prune_ok:
        sigs_keep = {sig_name(hashing.sha256_bytes(r)) for r in (new_raw, old_raw, prev_raw) if r is not None}
        old_sigs = [n for n in target.list_sigs() if n not in sigs_keep]
        if old_sigs:
            target.remove_sigs(old_sigs)
    log("deployed: " + res.summary())
    return res
