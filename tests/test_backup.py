"""Backups of the live release (deltapatcher/backup.py): the blob pool and its sharing, a damaged blob refused,
one backup a day, an unchanged release making none, the limits with a fake clock, restore to a folder and
through a stand-in sftp server, the schedule command through its seam, and the command line's answers."""

import calendar
import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, snapshot, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import backup, cli, client, config, deploy, projects, publish, serve, ssh  # noqa: E402
from deltapatcher.manifest import Manifest  # noqa: E402
from test_deploy import REMOTE, FakeSftp  # noqa: E402

DAY = 86400
T0 = calendar.timegm((2026, 11, 2, 12, 0, 0))          # a Monday, noon UTC


def run_json(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(sys, "stdin", io.StringIO("")):
        rc = cli.main(["--json", "--no-log"] + argv)
    return rc, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


class Base(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        self.clock = [T0]
        patcher = mock.patch.object(backup, "now", side_effect=lambda: self.clock[0])
        patcher.start()
        self.addCleanup(patcher.stop)
        write(self.p("game", "a.pak"), b"a" * 4000)
        write(self.p("game", "b.pak"), b"b" * 3000)
        config.save({"publish_src": self.p("game"), "name": "Game", "deploy_mode": "folder",
                     "deploy_folder": self.p("www")})
        self.project = projects.main_project()

    def release(self, version):
        publish.publish(self.p("game"), self.p("store"), "Game", version)
        deploy.deploy(self.p("store"), deploy.FolderTarget(self.p("www")))

    def take(self, **settings):
        if settings:
            config.save(settings)
        return backup.take(self.project, config.load(), deploy.FolderTarget(self.p("www")))

    def folder(self):
        return backup.folder_of(self.project, config.load())

    def names(self):
        return [b.name for b in backup.list_backups(self.folder())]

    def pool(self):
        return sorted(os.listdir(os.path.join(self.folder(), "blobs")))


class Pool(Base):
    def test_each_blob_once_and_the_backup_beside_it(self):
        self.release("1.0")
        r = self.take()
        self.assertEqual((r["made"], r["name"], r["fetched"], r["fetched_bytes"]), (True, "2026-11-02", 2, 7000))
        self.assertEqual(self.folder(), os.path.join(os.path.dirname(config.path()), "backups", "deltapatcher"))
        day = os.path.join(self.folder(), "2026-11-02")
        self.assertEqual(read(os.path.join(day, "manifest.json")), read(self.p("www", "manifest.json")))
        self.assertEqual(json.loads(read(os.path.join(day, "backup.json")))["from"], self.p("www"))
        write(self.p("game", "c.pak"), b"c" * 500)                                       # a small change
        self.release("1.1")
        self.clock[0] += DAY
        r = self.take()
        self.assertEqual((r["made"], r["name"], r["fetched"], r["fetched_bytes"]), (True, "2026-11-03", 1, 500))
        self.assertEqual(len(self.pool()), 3)                                            # a.pak, b.pak, c.pak once
        self.assertEqual(self.names(), ["2026-11-03", "2026-11-02"])
        later = backup.list_backups(self.folder())[0]
        self.assertTrue(os.path.isfile(os.path.join(later.path, "manifest.previous.json")))   # the release before too

    def test_an_unchanged_release_makes_none_and_one_a_day(self):
        self.release("1.0")
        self.take()
        self.clock[0] += DAY
        r = self.take()
        self.assertEqual((r["made"], r["name"]), (False, "2026-11-02"))                 # the same release
        self.assertEqual(self.names(), ["2026-11-02"])
        self.clock[0] += 3600
        write(self.p("game", "a.pak"), b"A" * 4000)
        self.release("1.1")
        r = self.take()
        self.assertEqual((r["made"], r["name"], r["replaced"]), (True, "2026-11-03", False))
        write(self.p("game", "a.pak"), b"Z" * 4000)
        self.release("1.2")
        self.clock[0] += 3600
        r = self.take()                                                                  # the same day again
        self.assertEqual((r["made"], r["name"], r["replaced"]), (True, "2026-11-03", True))
        self.assertEqual(self.names(), ["2026-11-03", "2026-11-02"])
        self.assertEqual(backup.list_backups(self.folder())[0].manifest.version, "1.2")
        self.assertFalse([n for n in os.listdir(self.folder()) if n.startswith(".")])     # nothing left halfway

    def test_a_damaged_blob_is_refused_and_nothing_half_made_is_kept(self):
        self.release("1.0")
        self.take()
        write(self.p("game", "c.pak"), b"c" * 800)
        write(self.p("game", "d.pak"), b"d" * 900)
        self.release("1.1")
        man = Manifest.from_json(read(self.p("www", "manifest.json")))
        bad = man.files["d.pak"].sha256
        write(self.p("www", "blobs", bad), b"x" * 900)                                  # the same size, not the content
        before = self.pool()
        self.clock[0] += DAY
        with self.assertRaises(backup.BackupError) as e:
            self.take()
        self.assertEqual(e.exception.code, "damaged")
        self.assertEqual(self.names(), ["2026-11-02"])
        self.assertEqual(self.pool(), before)                                            # c.pak did not stay either
        self.assertFalse([n for n in os.listdir(self.folder()) if n.startswith(".")])
        os.remove(self.p("www", "blobs", bad))                                         # one missing: refused too
        with self.assertRaises(backup.BackupError) as e:
            self.take()
        self.assertEqual(e.exception.code, "read-failed")

    def test_a_backup_never_changes_the_destination(self):
        self.release("1.0")
        before = snapshot(self.p("www"))
        self.take()
        self.assertEqual(snapshot(self.p("www")), before)

    def test_one_backup_of_a_project_at_a_time(self):
        self.release("1.0")
        os.makedirs(self.folder())
        with client.Lock(self.folder()):                       # another backup (the system's scheduler's) is running
            with self.assertRaises(backup.BackupError) as e:
                self.take()
            self.assertEqual(e.exception.code, "busy")
        self.assertTrue(self.take()["made"])


class Limits(Base):
    def test_which_backups_the_limits_keep(self):
        days = ["2026-11-%02d" % d for d in range(30, 0, -1)] + ["2026-10-%02d" % d for d in range(31, 0, -1)]
        kept = backup.keep_names(days, 7, 0, 0)
        self.assertEqual(sorted(kept, reverse=True), days[:7])
        kept = backup.keep_names(days, 2, 3, 0)                   # 11-30 is a Monday: then the Sundays before it
        self.assertEqual(sorted(kept, reverse=True), ["2026-11-30", "2026-11-29", "2026-11-22"])
        kept = backup.keep_names(days, 0, 0, 2)
        self.assertEqual(sorted(kept, reverse=True), ["2026-11-30", "2026-10-31"])
        self.assertEqual(backup.keep_names(days, 0, 0, 0), {"2026-11-30"})                # the newest always
        sparse = ["2026-11-30", "2026-11-02", "2026-09-15"]                               # days with a backup count
        self.assertEqual(backup.keep_names(sparse, 2, 0, 0), {"2026-11-30", "2026-11-02"})
        self.assertEqual(backup.keep_names([], 7, 4, 1), set())

    def test_the_limits_after_each_backup_with_a_fake_clock(self):
        config.save({"backup_keep_daily": 3, "backup_keep_weekly": 2, "backup_keep_monthly": 0})
        for i in range(12):                                       # twelve days, a new release each day
            write(self.p("game", "day.txt"), ("day %d" % i).encode())
            self.release("1.%d" % i)
            self.take()
            self.clock[0] += DAY
        # 11-02 (Mon) to 11-13 (Fri): the last three days, and the newest of each of the last two weeks
        self.assertEqual(self.names(), ["2026-11-13", "2026-11-12", "2026-11-11", "2026-11-08"])
        named = set()
        for b in backup.list_backups(self.folder()):
            named.update(b.blobs())
        self.assertEqual(set(self.pool()), named)                 # blobs no backup names are gone

    def test_max_size_removes_the_oldest_and_keeps_the_newest(self):
        config.save({"backup_keep_daily": 30})
        for i in range(4):                                        # each release a new big.pak, 20000 bytes
            write(self.p("game", "big.pak"), os.urandom(20000))
            self.release("2.%d" % i)
            self.take()
            self.clock[0] += DAY
        self.assertEqual(len(self.names()), 4)
        config.save({"backup_max_size": "75 KB"})                 # two backups fit: five blobs and four bigs do not
        r = backup.apply_limits(self.project, config.load())
        self.assertLessEqual(r["bytes"], 75 << 10)
        self.assertEqual((r["kept"], r["removed"], r["over"]),
                         (["2026-11-05", "2026-11-04"], ["2026-11-03", "2026-11-02"], False))   # the oldest went
        self.assertEqual(self.names(), ["2026-11-05", "2026-11-04"])
        config.save({"backup_max_size": "1 KB"})
        logged = []
        r = backup.apply_limits(self.project, config.load(), log=logged.append)
        self.assertEqual((r["kept"], r["over"]), (["2026-11-05"], True))
        self.assertTrue(any("smaller than the newest backup" in l for l in logged))
        config.save({"backup_max_size": "lots"})
        with self.assertRaises(backup.BackupError):
            backup.apply_limits(self.project, config.load())
        self.assertEqual(backup.parse_size("1.5 GB"), int(1.5 * (1 << 30)))
        self.assertIsNone(backup.parse_size(""))
        for bad in ("10", "10 parsecs", "-1 GB", "GB"):
            with self.assertRaises(ValueError, msg=bad):
                backup.parse_size(bad)


class Restore(Base):
    def check_serves(self, root, inst):
        srv, _t = serve.serve_in_background(root, "127.0.0.1", 0)
        try:
            client.update("http://127.0.0.1:%d/" % srv.server_address[1], inst)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_restore_to_the_folder(self):
        self.release("1.0")
        self.take()
        one = read(self.p("www", "manifest.json"))
        write(self.p("game", "a.pak"), b"A" * 4100)
        self.release("1.1")
        two_blobs = set(os.listdir(self.p("www", "blobs")))
        dry = backup.restore(self.project, config.load(), "2026-11-02", deploy.FolderTarget(self.p("www")), dry_run=True)
        self.assertEqual((dry.dry_run, read(self.p("www", "manifest.json")) == one), (True, False))
        r = backup.restore(self.project, config.load(), "2026-11-02", deploy.FolderTarget(self.p("www")))
        self.assertEqual(read(self.p("www", "manifest.json")), one)
        self.assertEqual(Manifest.from_json(read(self.p("www", "manifest.previous.json"))).version, "1.1")
        self.assertTrue(two_blobs <= set(os.listdir(self.p("www", "blobs"))))             # nothing there removed
        self.assertEqual(r.removed, 0)
        self.check_serves(self.p("www"), self.p("player"))
        self.assertEqual(read(self.p("player", "a.pak")), b"a" * 4000)
        with self.assertRaises(backup.BackupError) as e:
            backup.restore(self.project, config.load(), "2020-01-01", deploy.FolderTarget(self.p("www")))
        self.assertEqual(e.exception.code, "no-such-backup")

    def test_backup_and_restore_through_sftp(self):
        disk = self.p("server")
        os.makedirs(disk)
        fake = FakeSftp(disk)
        with mock.patch.object(ssh, "sftp", fake):
            target = deploy.SftpTarget(ssh.Target("game.example.com", "deploy", 22, "", REMOTE))
            publish.publish(self.p("game"), self.p("store"), "Game", "1.0")
            deploy.deploy(self.p("store"), target)
            r = backup.take(self.project, config.load(), target)
            self.assertEqual((r["made"], r["fetched"]), (True, 2))
            self.assertTrue(any(line.startswith("get ") for batch in fake.batches for line in batch))
            for batch in fake.batches:                                   # no local path ever reaches sftp
                for line in batch:
                    self.assertNotIn(self.tmp.replace("\\", "/"), line)
                    self.assertNotIn("\\", line)
            one = read(os.path.join(disk, "manifest.json"))
            write(self.p("game", "b.pak"), b"B" * 3000)
            publish.publish(self.p("game"), self.p("store"), "Game", "1.1")
            deploy.deploy(self.p("store"), target)
            backup.restore(self.project, config.load(), "2026-11-02", target)
            self.assertEqual(read(os.path.join(disk, "manifest.json")), one)
        self.check_serves(disk, self.p("player"))
        self.assertEqual(read(self.p("player", "b.pak")), b"b" * 3000)


class Schedule(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        self.calls = []
        self.answers = {}

        def tool(argv, stdin_text=None):
            self.calls.append((list(argv), stdin_text))
            out, err, rc = self.answers.get(tuple(argv[:2]), ("", "", 0))
            return subprocess.CompletedProcess(argv, rc, out, err)
        for target, value in ((backup, "run_tool"), ):
            patcher = mock.patch.object(target, value, side_effect=tool)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(backup, "program", return_value=["C:\\Program Files\\DeltaPatcher\\deltapatcher-cli.exe"])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_windows_task_scheduler(self):
        mod = projects.new("Mod")
        pid = backup.project_id(mod)
        self.answers[("schtasks", "/Query")] = ('"\\DeltaPatcher backup Old name %s","N/A","Ready"\n'
                                                '"\\Someone else\'s task","N/A","Ready"\n' % pid, "", 0)
        r = backup.schedule(mod, "daily", platform="win32")
        argvs = [c[0] for c in self.calls]
        self.assertEqual(argvs[1], ["schtasks", "/Delete", "/TN", "\\DeltaPatcher backup Old name %s" % pid, "/F"])
        create = argvs[2]
        self.assertEqual(create[:4], ["schtasks", "/Create", "/TN", "DeltaPatcher backup Mod %s" % pid])
        self.assertEqual(create[4], "/TR")
        self.assertEqual(create[5], subprocess.list2cmdline(["C:\\Program Files\\DeltaPatcher\\deltapatcher-cli.exe",
                                                             "--close", "--project", mod.path, "backup"]))
        self.assertTrue(create[5].startswith('"C:\\Program Files\\DeltaPatcher\\deltapatcher-cli.exe" --close'))
        self.assertIn("DAILY", create)
        self.assertEqual(r["task"], "DeltaPatcher backup Mod %s" % pid)
        self.calls[:] = []
        self.answers[("schtasks", "/Query")] = ('"\\DeltaPatcher backup Mod %s","N/A","Ready"\n'
                                                '"\\DeltaPatcher backup Other 00000000","N/A","Ready"\n' % pid, "", 0)
        r = backup.schedule(mod, "off", platform="win32")
        deleted = [c[0][3] for c in self.calls if c[0][1] == "/Delete"]
        self.assertEqual(deleted, ["\\DeltaPatcher backup Mod %s" % pid])                # another project's stays
        self.assertFalse([c for c in self.calls if c[0][1] == "/Create"])
        backup.schedule(projects.main_project(), "hourly", platform="win32")
        create = [c[0] for c in self.calls if c[0][1] == "/Create"][-1]
        self.assertNotIn("--project", create[5])                                         # Default needs none
        self.assertIn("HOURLY", create)

    def test_crontab(self):
        mod = projects.new("Mod")
        pid = backup.project_id(mod)
        mine = "0 3 * * * 'old' backup # deltapatcher-backup %s" % pid
        self.answers[("crontab", "-l")] = ("# the user's own\n*/5 * * * * echo hi\n%s\n" % mine, "", 0)
        r = backup.schedule(mod, "hourly", platform="linux")
        written = self.calls[-1]
        self.assertEqual(written[0], ["crontab", "-"])
        lines = written[1].splitlines()
        self.assertEqual(lines[:2], ["# the user's own", "*/5 * * * * echo hi"])         # the user's lines stay
        self.assertNotIn(mine, lines)
        self.assertTrue(lines[2].endswith("# deltapatcher-backup %s" % pid))
        self.assertRegex(lines[2], r"^\d{1,2} \* \* \* \* ")                                # every hour
        self.assertIn("--close --project %s backup" % shlex.quote(mod.path), lines[2])    # quoted only when it needs it
        self.assertEqual(r["entry"], lines[2])
        self.answers[("crontab", "-l")] = ("", "no crontab for player\n", 1)            # none yet
        backup.schedule(mod, "daily", platform="linux")
        self.assertEqual(len(self.calls[-1][1].splitlines()), 1)
        self.answers[("crontab", "-l")] = ("", "permission denied\n", 1)               # never written blind
        count = len(self.calls)
        with self.assertRaises(backup.BackupError):
            backup.schedule(mod, "off", platform="linux")
        self.assertEqual([c[0] for c in self.calls[count:]], [["crontab", "-l"]])
        with self.assertRaises(backup.BackupError):
            backup.schedule(mod, "weekly", platform="linux")


class Command(Base):
    def test_what_the_command_line_says(self):
        rc, ev = run_json(["backup"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "nothing-released"))
        self.release("1.0")
        rc, ev = run_json(["backup"])
        self.assertEqual((rc, ev[-1]["data"]["made"]), (0, True))
        rc, ev = run_json(["backup", "list"])
        listed = ev[-1]["data"]
        self.assertEqual([b["name"] for b in listed["backups"]], ["2026-11-02"])
        self.assertEqual(listed["backups"][0]["release"]["version"], "1.0")
        self.assertEqual(listed["limits"], {"daily": 7, "weekly": 4, "monthly": 0, "max_size": None})
        rc, ev = run_json(["backup", "restore", "2026-11-02"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "confirm-needed"))                    # asks first
        self.assertEqual(ev[-1]["data"]["live"]["version"], "1.0")
        rc, ev = run_json(["backup", "restore", "2026-11-02", "--dry-run"])
        self.assertEqual((rc, ev[-1]["data"]["dry_run"]), (0, True))
        rc, ev = run_json(["backup", "restore", "2026-11-02", "--yes"])
        self.assertEqual(rc, 0, ev[-1])
        rc, ev = run_json(["backup", "prune"])
        self.assertEqual((rc, ev[-1]["data"]["kept"]), (0, ["2026-11-02"]))
        self.assertEqual(run_json(["config", "set", "backup_max_size=huge"])[1][-1]["code"], "bad-setting")
        self.assertEqual(run_json(["config", "set", "backup_max_size=2 GB"])[0], 0)
        config.save({"deploy_mode": "local"})
        self.assertEqual(run_json(["backup"])[1][-1]["code"], "no-destination")
        config.save({"deploy_mode": "sftp", "ssh_host": ""})
        self.assertEqual(run_json(["backup"])[1][-1]["code"], "no-destination")
        config.save({"deploy_mode": "folder", "deploy_folder": ""})
        self.assertEqual(run_json(["backup"])[1][-1]["code"], "no-destination")

    def test_the_project_listing_says_when_it_was_backed_up(self):
        self.release("1.0")
        config.save({"backup_every_hours": 24})
        rows = {r["title"]: r for r in run_json(["projects"])[1][-1]["data"]["projects"]}
        self.assertEqual((rows["Default"]["backup_every_hours"], rows["Default"]["backup_last"],
                          rows["Default"]["backup_ready"]), (24, "", True))
        self.take()
        rows = {r["title"]: r for r in run_json(["projects"])[1][-1]["data"]["projects"]}
        self.assertEqual(rows["Default"]["backup_last"], "2026-11-02T12:00:00Z")
        added = self.p("elsewhere", "Mod.ini")
        write(added, "[project]\ntitle = Elsewhere\n")
        p = projects.add(added)
        self.assertTrue(os.path.basename(backup.folder_of(p, config.load(p))).startswith("Mod-"))


if __name__ == "__main__":
    unittest.main()
