"""Deploys: to a folder, and through SftpTarget against a stand-in for an sftp server.

The stand-in reads the batch files SftpTarget writes and carries out the commands on a local
folder, the way OpenSSH's sftp would, so the real command sequence is what gets tested.
"""

import os
import shlex
import shutil
import subprocess
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from deltapatcher import client, deploy, publish, serve, ssh  # noqa: E402
from deltapatcher.manifest import Manifest  # noqa: E402
from helpers import TempCase, read, write  # noqa: E402

REMOTE = "/srv/www/game"


class FakeSftp(object):
    """Carries out sftp batch commands on `disk` (the server's REMOTE folder)."""

    def __init__(self, disk, posix_rename=True):
        self.disk = disk
        self.posix_rename = posix_rename
        self.batches = []

    def local(self, remote):
        assert remote == REMOTE or remote.startswith(REMOTE + "/"), remote
        return os.path.join(self.disk, *[p for p in remote[len(REMOTE):].split("/") if p])

    def __call__(self, target, commands, cwd=None, timeout=0):
        self.batches.append(list(commands))
        out, err = [], []
        for line in commands:
            out.append("sftp> " + line)
            soft = line.startswith("-")
            argv = shlex.split(line[1:] if soft else line)
            try:
                self.do(argv, cwd, out)
            except Exception as e:
                err.append("%s: %s" % (argv[0], e))
                if not soft:
                    return subprocess.CompletedProcess([], 1, "\n".join(out), "\n".join(err))
        return subprocess.CompletedProcess([], 0, "\n".join(out), "\n".join(err))

    def do(self, argv, cwd, out):
        cmd = argv[0]
        if cmd == "mkdir":
            os.mkdir(self.local(argv[1]))
        elif cmd == "ls":
            d = self.local(argv[-1])
            for fn in sorted(os.listdir(d)):
                size = os.path.getsize(os.path.join(d, fn))
                out.append("-rw-r--r--    1 1000     1000     %10d Nov 23 20:00 %s/%s" % (size, argv[-1], fn))
        elif cmd == "put":
            shutil.copyfile(os.path.join(cwd, argv[1]), self.local(argv[2]))
        elif cmd == "get":
            shutil.copyfile(self.local(argv[1]), os.path.join(cwd, argv[2]))
        elif cmd == "rename":
            src, dst = self.local(argv[1]), self.local(argv[2])
            if os.path.exists(dst) and not self.posix_rename:
                raise OSError("Couldn't rename file: Failure")
            os.replace(src, dst)
        elif cmd == "rm":
            os.remove(self.local(argv[1]))
        elif cmd == "pwd":
            out.append("Remote working directory: /home/deploy")
        else:
            raise ValueError("unknown command " + cmd)


class Deploys(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        self.src, self.store = self.p("src"), self.p("store")
        write(self.p("src", "a.pk3"), os.urandom(70000))
        write(self.p("src", "b.zip"), os.urandom(30000))
        write(self.p("src", "c.txt"), "one\n")
        publish.publish(self.src, self.store, "G", "1")

    def release_two(self):
        write(self.p("src", "c.txt"), "two\n")
        os.remove(self.p("src", "b.zip"))
        write(self.p("src", "d.bin"), os.urandom(5000))
        publish.publish(self.src, self.store, "G", "2")

    def check_serves(self, root, inst):
        srv, _t = serve.serve_in_background(root, "127.0.0.1", 0)
        try:
            client.update("http://127.0.0.1:%d/" % srv.server_address[1], inst)
        finally:
            srv.shutdown()
            srv.server_close()
        for name in os.listdir(self.src):
            self.assertEqual(read(os.path.join(inst, name)), read(os.path.join(self.src, name)))

    def test_folder(self):
        web = self.p("web")
        tgt = deploy.FolderTarget(web)
        r = deploy.deploy(self.store, tgt)
        self.assertEqual((r.sent, r.had), (3, 0))
        self.check_serves(web, self.p("inst"))
        dry = deploy.deploy(self.store, tgt, dry_run=True)
        self.assertEqual(dry.sent, 0)
        self.release_two()
        r = deploy.deploy(self.store, tgt)
        self.assertEqual(r.sent, 2)                       # the new c.txt and d.bin only
        self.assertTrue(os.path.isfile(os.path.join(web, "manifest.previous.json")))
        self.check_serves(web, self.p("inst"))
        # release one's b.zip and c.txt stay while any manifest on the target names them (as the
        # previous one, after release three), and go with release four
        write(self.p("src", "e.txt"), "three\n")
        publish.publish(self.src, self.store, "G", "3")
        r = deploy.deploy(self.store, tgt)
        self.assertEqual(r.removed, 0)
        write(self.p("src", "f.txt"), "four\n")
        publish.publish(self.src, self.store, "G", "4")
        r = deploy.deploy(self.store, tgt)
        self.assertEqual(r.removed, 2)

    def _sftp(self, posix_rename):
        disk = self.p("server")
        os.makedirs(disk)
        fake = FakeSftp(disk, posix_rename)
        real = ssh.sftp
        ssh.sftp = fake
        try:
            t = ssh.Target("example.com", "deploy", 22, "", REMOTE)
            tgt = deploy.SftpTarget(t)
            r = deploy.deploy(self.store, tgt)
            self.assertEqual(r.sent, 3)
            self.check_serves(disk, self.p("inst"))
            self.release_two()
            r = deploy.deploy(self.store, tgt)
            self.assertEqual(r.sent, 2)
            self.assertEqual(r.atomic, posix_rename)
            self.check_serves(disk, self.p("inst"))
            self.assertEqual(Manifest.from_json(read(os.path.join(disk, "manifest.previous.json"))).version, "1")
            self.assertFalse([f for f in os.listdir(os.path.join(disk, "blobs")) if f.endswith(".part")])
            # no local path ever reaches sftp: put runs in the blob folder with bare hash names
            for batch in fake.batches:
                for line in batch:
                    self.assertNotIn(self.tmp.replace("\\", "/"), line)
                    self.assertNotIn("\\", line)
        finally:
            ssh.sftp = real

    def test_sftp_posix_rename(self):
        self._sftp(True)

    def test_sftp_legacy_rename(self):
        self._sftp(False)

    def test_upload_checked_before_manifest(self):
        web = self.p("web")

        class Lossy(deploy.FolderTarget):
            def put_blobs(self, blob_dir, shas, progress):
                deploy.FolderTarget.put_blobs(self, blob_dir, shas[1:], progress)   # loses one

        with self.assertRaises(deploy.DeployError):
            deploy.deploy(self.store, Lossy(web))
        self.assertFalse(os.path.exists(os.path.join(web, "manifest.json")))


class SshHelpers(unittest.TestCase):
    def test_quoting_refuses(self):
        self.assertEqual(ssh.q("/srv/www/a b"), '"/srv/www/a b"')
        for bad in ('a"b', "a\\b", "a*", "a?", "a[1]", "a\nb", "a'b"):
            with self.assertRaises(ssh.SshError, msg=bad):
                ssh.q(bad)

    def test_target(self):
        t = ssh.Target("example.com", "deploy", 2222, "", "/var/www/x/")
        self.assertEqual(t.dest(), "deploy@example.com")
        self.assertEqual(t.known_name(), "[example.com]:2222")
        self.assertEqual(t.remote_root, "/var/www/x")
        self.assertEqual(ssh.Target("2001:db8::1").known_name(), "2001:db8::1")
        self.assertEqual(ssh.Target("2001:db8::1").dest(), "[2001:db8::1]")
        o = t.options()
        self.assertIn("StrictHostKeyChecking=yes", o)
        self.assertIn("BatchMode=yes", o)
        for bad in ("", "a b", "-oProxyCommand=x", "host;rm"):
            with self.assertRaises(ssh.SshError, msg=bad):
                ssh.Target(bad)
        with self.assertRaises(ssh.SshError):
            ssh.Target("h", "-oProxyCommand")
        with self.assertRaises(ssh.SshError):
            ssh.Target("h", port=70000)
        with self.assertRaises(ssh.SshError):
            ssh.Target("h", remote_root="C:\\www")

    def test_parse_ls(self):
        sha = "ab" * 32
        text = "\n".join(["sftp> ls -ln /srv/www/game/blobs",
                          "-rw-r--r--    1 1000     1000         1234 Nov 23 20:00 /srv/www/game/blobs/" + sha,
                          "-rw-r--r--    1 1000     1000           10 Nov 23 20:00 /srv/www/game/blobs/%s.part" % sha,
                          "-rw-r--r--    1 1000     1000           10 Jan  2  2025 /srv/www/game/blobs/readme.txt",
                          "drwxr-xr-x    2 1000     1000         4096 Nov 23 20:00 /srv/www/game/blobs/sub"])
        self.assertEqual(ssh.parse_ls(text), {sha: 1234, sha + ".part": 10})


if __name__ == "__main__":
    unittest.main()
