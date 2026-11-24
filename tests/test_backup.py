"""Backups of the live release (deltapatcher/backup.py): the blob pool and its sharing, a damaged blob refused,
one backup a day, an unchanged release making none, the limits with a fake clock, restore to a folder and
through a stand-in sftp server, the schedule command through its seam, and the command line's answers. Also
what must never happen: two projects in one folder, backups among a project's other folders, a prune that
removes what an unreadable backup needs, a day's backup lost to a failed rename, the backup just made removed
by a clock that is behind, a damaged pool blob sent, and a scheduled run that waits for an answer."""

import calendar
import contextlib
import io
import json
import ntpath
import os
import shlex
import shutil
import subprocess
import sys
import unittest
from unittest import mock
from xml.etree import ElementTree

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import NOT_FOUND, TempCase, read, snapshot, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import backup, cli, client, config, deploy, projects, publish, serve, ssh  # noqa: E402
from deltapatcher.manifest import Manifest  # noqa: E402
from test_deploy import REMOTE, FakeSftp  # noqa: E402

DAY = 86400
T0 = calendar.timegm((2026, 11, 2, 12, 0, 0))          # a Monday, noon UTC
REAL_RUN_TOOL = backup.run_tool                        # every test case stands in for it; one test reads it
TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def run_json(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(sys, "stdin", io.StringIO("")):
        rc = cli.main(["--json", "--no-log"] + argv)
    return rc, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def run_text(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), mock.patch.object(sys, "stdin", io.StringIO("")):
        rc = cli.main(["--no-log", "--no-color"] + argv)
    return rc, out.getvalue()


class Tty(io.StringIO):
    """Standard input that is a console nobody types in: what conhost --headless gives a scheduled run."""

    def isatty(self):
        return True


class StandIn(object):
    """The system's scheduler, stood in for: Task Scheduler (its tasks by name) and a crontab (None: none yet).
    `answers` makes one command answer as given."""

    def stand_in(self):
        self.calls = []
        self.answers = {}
        self.tasks = {}
        self.crontab = None

        def tool(argv, stdin_text=None):
            argv = list(argv)
            xml = read(argv[argv.index("/XML") + 1]).decode("utf-16") if argv[:2] == ["schtasks", "/Create"] else None
            self.calls.append((argv, stdin_text, xml))
            if tuple(argv[:2]) in self.answers:
                out, err, rc = self.answers[tuple(argv[:2])]
                return subprocess.CompletedProcess(argv, rc, out, err)
            if argv[0] == "schtasks":
                name = argv[argv.index("/TN") + 1]
                if argv[1] == "/Create":
                    self.tasks[name] = xml
                elif name not in self.tasks:
                    return subprocess.CompletedProcess(argv, 1, "", NOT_FOUND)
                elif argv[1] == "/Delete":
                    del self.tasks[name]
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[1] == "-l":
                if self.crontab is None:
                    return subprocess.CompletedProcess(argv, 1, "", "no crontab for player\n")
                return subprocess.CompletedProcess(argv, 0, self.crontab, "")
            self.crontab = stdin_text
            return subprocess.CompletedProcess(argv, 0, "", "")
        patcher = mock.patch.object(backup, "run_tool", side_effect=tool)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(backup, "program", return_value=[Schedule.PROGRAM])
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(setattr, cli, "UNATTENDED", False)

    def ours(self):
        """How many entries of DeltaPatcher's the stood-in scheduler of this system holds."""
        if sys.platform == "win32":
            return len(self.tasks)
        return sum(1 for l in (self.crontab or "").split("\n") if backup.CRON_MARK in l)


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
        self.target = deploy.FolderTarget(self.p("www"))

    def release(self, version):
        publish.publish(self.p("game"), self.p("store"), "Game", version)
        deploy.deploy(self.p("store"), deploy.FolderTarget(self.p("www")))

    def change(self, version):
        """A new release: one file changed."""
        write(self.p("game", "day.txt"), ("release %s" % version).encode())
        self.release(version)

    def take(self, **settings):
        if settings:
            config.save(settings)
        return backup.take(self.project, config.load(), self.target)

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
        pid = backup.project_id(self.project)
        self.assertEqual(self.folder(), os.path.join(os.path.dirname(config.path()), "backups", "deltapatcher-" + pid))
        day = os.path.join(self.folder(), "2026-11-02")
        self.assertEqual(read(os.path.join(day, "manifest.json")), read(self.p("www", "manifest.json")))
        info = json.loads(read(os.path.join(day, "backup.json")))
        self.assertEqual((info["from"], info["project_id"]), (self.p("www"), pid))
        self.assertEqual(json.loads(read(os.path.join(self.folder(), "project.json")))["project_id"], pid)
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

    def test_the_htaccess_is_kept_and_put_back(self):
        self.release("1.0")                                    # the store's .htaccess goes with the release
        self.assertTrue(os.path.isfile(self.p("www", ".htaccess")))
        self.take()
        day = os.path.join(self.folder(), "2026-11-02")
        self.assertEqual(read(os.path.join(day, ".htaccess")), read(self.p("www", ".htaccess")))
        backup.restore(self.project, config.load(), "2026-11-02", deploy.FolderTarget(self.p("fresh")))
        self.assertEqual(read(self.p("fresh", ".htaccess")), read(self.p("www", ".htaccess")))


class Folders(Base):
    def test_two_projects_never_share_a_folder(self):
        named = projects.new("DeltaPatcher")                  # named like the main settings file
        self.assertNotEqual(os.path.normcase(self.folder()), os.path.normcase(backup.folder_of(named, config.load(named))))
        shared = self.p("shared")
        mod = projects.new("Mod")
        write(self.p("modgame", "m.pak"), b"m" * 2500)
        config.save({"backup_dir": shared})
        config.save({"publish_src": self.p("modgame"), "deploy_mode": "folder", "deploy_folder": self.p("modwww"),
                     "backup_dir": shared}, mod)
        self.release("1.0")
        publish.publish(self.p("modgame"), self.p("modstore"), "Mod", "7.0")
        deploy.deploy(self.p("modstore"), deploy.FolderTarget(self.p("modwww")))
        self.take()
        r = backup.take(mod, config.load(mod), deploy.FolderTarget(self.p("modwww")))
        self.assertEqual((r["made"], r["name"], r["pruned"]["removed"]), (True, "2026-11-02", []))
        mine, its = backup.listing(self.project, config.load()), backup.listing(mod, config.load(mod))
        self.assertEqual([b["release"]["version"] for b in mine["backups"]], ["1.0"])
        self.assertEqual([b["release"]["version"] for b in its["backups"]], ["7.0"])
        self.assertEqual((os.path.dirname(mine["folder"]), os.path.dirname(its["folder"])), (shared, shared))
        self.assertNotEqual(mine["folder"], its["folder"])
        self.assertEqual(len(os.listdir(os.path.join(mine["folder"], "blobs"))), 2)       # a pool each
        self.assertEqual(len(os.listdir(os.path.join(its["folder"], "blobs"))), 1)
        backup.restore(self.project, config.load(), "2026-11-02", deploy.FolderTarget(self.p("check")))
        self.assertEqual(Manifest.from_json(read(self.p("check", "manifest.json"))).name, "Game")

    def test_a_folder_or_backup_of_another_project_is_refused(self):
        self.release("1.0")
        self.take()
        marker = os.path.join(self.folder(), "project.json")
        mine = json.loads(read(marker))
        write(marker, json.dumps(dict(mine, project_id="00000000", file="elsewhere.ini")))
        for attempt in (self.take, lambda: backup.apply_limits(self.project, config.load()),
                        lambda: backup.restore(self.project, config.load(), "2026-11-02", self.target, dry_run=True),
                        lambda: backup.listing(self.project, config.load())):
            with self.assertRaises(backup.BackupError) as e:
                attempt()
            self.assertEqual(e.exception.code, "not-ours")
        write(marker, json.dumps(mine))
        info = os.path.join(self.folder(), "2026-11-02", "backup.json")
        write(info, json.dumps(dict(json.loads(read(info)), project_id="00000000")))    # another project's backup in it
        with self.assertRaises(backup.BackupError) as e:
            backup.restore(self.project, config.load(), "2026-11-02", self.target, dry_run=True)
        self.assertEqual(e.exception.code, "not-ours")

    def test_backups_stay_out_of_the_projects_other_folders(self):
        config.save({"store_dir": self.p("store"), "install_dir": self.p("inst")})
        refused = (self.p("store"), self.p("store", "old"), self.p("game"), self.p("game", "backups"), self.tmp,
                   self.p("www"), self.p("www", "b"), self.p("inst"), self.p("inst", "b"))
        for value in refused:
            rc, ev = run_json(["config", "set", "backup_dir=" + value])
            self.assertEqual((rc, ev[-1]["code"]), (1, "bad-setting"), value)
            self.assertIn("give them a folder of their own", ev[-1]["message"])
        default_store = config.default_store(self.p("game"))
        config.save({"store_dir": ""})                                                   # DeltaPatcher's own store
        for value in (default_store, os.path.dirname(default_store)):
            self.assertEqual(run_json(["config", "set", "backup_dir=" + value])[1][-1]["code"], "bad-setting", value)
        self.assertEqual(config.load()["backup_dir"], "")                                # never saved
        self.assertEqual(run_json(["config", "set", "backup_dir=" + self.p("kept")])[0], 0)
        rc, ev = run_json(["config", "set", "publish_src=" + self.tmp])                 # around it, the other way
        self.assertEqual((rc, ev[-1]["code"]), (1, "bad-setting"))
        rc, ev = run_json(["config", "set", "deploy_mode=sftp", "backup_dir=" + self.p("www", "b")])
        self.assertEqual(rc, 0, ev[-1])                                                  # deploy_folder unused then
        self.assertEqual(run_json(["config", "set", "deploy_mode=folder"])[1][-1]["code"], "bad-setting")
        config.save({"deploy_mode": "folder", "store_dir": self.p("store"), "backup_dir": self.p("store")})   # by hand
        with self.assertRaises(backup.BackupError) as e:
            self.folder()
        self.assertEqual(e.exception.code, "bad-setting")
        self.release("1.0")
        store_before = snapshot(self.p("store"))
        self.assertEqual(run_json(["backup"])[1][-1]["code"], "bad-setting")
        self.assertEqual(snapshot(self.p("store")), store_before)                        # nothing went into the store
        rows = run_json(["projects"])[1][-1]["data"]["projects"]                         # the listing still works
        self.assertEqual(rows[0]["backup_last"], "")

    def test_a_relative_backup_dir_is_read_from_the_main_settings_files_folder(self):
        self.assertEqual(run_json(["config", "set", "backup_dir=kept"])[0], 0)
        self.assertEqual(os.path.dirname(self.folder()), os.path.join(os.path.dirname(config.path()), "kept"))


class Unreadable(Base):
    def setUp(self):
        Base.setUp(self)
        for i in range(3):                                     # 11-02, 11-03, 11-04
            self.change("1.%d" % i)
            self.take()
            self.clock[0] += DAY
        config.save({"backup_keep_daily": 1, "backup_keep_weekly": 0})   # only the newest would stay

    def test_a_backup_that_cannot_be_read_stops_the_prune(self):
        before = self.pool()
        manifest = os.path.join(self.folder(), "2026-11-02", "manifest.json")
        good = read(manifest)
        write(manifest, b"{not a manifest")
        logged = []
        r = backup.apply_limits(self.project, config.load(), log=logged.append)
        self.assertEqual((r["removed"], r["blobs_removed"]), ([], 0))
        self.assertEqual(r["unreadable"], [{"name": "2026-11-02", "why": "its manifest.json is damaged"}])   # plain words
        self.assertEqual(self.pool(), before)
        self.assertTrue(any("2026-11-02 cannot be read" in l for l in logged), logged)
        self.assertTrue(any("no backup or blob was removed" in l for l in logged), logged)
        listed = run_json(["backup", "list"])[1][-1]["data"]
        self.assertEqual(([b["name"] for b in listed["backups"]], [u["name"] for u in listed["unreadable"]]),
                         (["2026-11-04", "2026-11-03"], ["2026-11-02"]))
        with self.assertRaises(backup.BackupError) as e:
            backup.restore(self.project, config.load(), "2026-11-02", self.target, dry_run=True)
        self.assertEqual(e.exception.code, "unreadable")
        write(manifest, good)
        real_open = open

        def held(path, *a, **k):                               # a scanner or a sync tool holds a file of it
            if str(path).endswith(os.path.join("2026-11-03", "manifest.json")):
                raise PermissionError(13, "The process cannot access the file")
            return real_open(path, *a, **k)
        with mock.patch("builtins.open", side_effect=held):
            r = backup.apply_limits(self.project, config.load())
        self.assertEqual((r["unreadable"], r["removed"]),
                         ([{"name": "2026-11-03", "why": "its manifest.json cannot be read: The process cannot access the file"}],
                          []))
        self.assertEqual(self.pool(), before)
        r = backup.apply_limits(self.project, config.load())   # every one readable: the limits apply
        self.assertEqual(r["removed"], ["2026-11-03", "2026-11-02"])
        self.assertLess(len(self.pool()), len(before))


class Renames(Base):
    def test_a_replacement_that_fails_between_its_renames_keeps_the_day(self):
        self.release("1.0")
        self.take()
        write(self.p("game", "a.pak"), b"A" * 4000)
        self.release("1.1")
        real = os.replace

        def held(src, dst):
            if os.path.basename(src).startswith(".new-"):
                raise PermissionError(5, "Access is denied")   # a scanner holds the new folder
            return real(src, dst)
        with mock.patch.object(backup, "RENAME_WAIT", 0), mock.patch.object(backup.os, "replace", side_effect=held):
            with self.assertRaises(PermissionError):
                self.take()
        self.assertEqual(self.names(), ["2026-11-02"])
        self.assertEqual(backup.list_backups(self.folder())[0].manifest.version, "1.0")   # the day's backup is there
        self.assertFalse([n for n in os.listdir(self.folder()) if n.startswith(".")])
        r = self.take()                                        # the next run makes it
        self.assertEqual((r["made"], r["replaced"]), (True, True))
        self.assertEqual(backup.list_backups(self.folder())[0].manifest.version, "1.1")

    def test_a_day_left_aside_is_put_back(self):
        self.release("1.0")
        self.take()
        day, aside = os.path.join(self.folder(), "2026-11-02"), os.path.join(self.folder(), ".old-2026-11-02-4242")
        os.rename(day, aside)                                  # stopped between the two renames
        self.assertEqual(self.names(), [])
        backup.apply_limits(self.project, config.load())       # a prune puts it back first: its blobs stay
        self.assertEqual((self.names(), len(self.pool())), (["2026-11-02"], 2))
        os.rename(day, aside)
        r = self.take()                                        # so does the next backup
        self.assertEqual((r["made"], r["name"]), (False, "2026-11-02"))
        shutil.copytree(day, os.path.join(self.folder(), ".old-2026-11-02-99"))   # left over, its day in place
        self.take()
        self.assertFalse(os.path.exists(os.path.join(self.folder(), ".old-2026-11-02-99")))
        self.assertEqual(self.names(), ["2026-11-02"])

    def test_a_folder_held_for_a_moment_is_renamed_after_all(self):
        self.release("1.0")
        self.take()
        self.change("1.1")
        real, held = os.replace, [0]

        def busy_twice(src, dst):
            if os.path.basename(src).startswith(".new-") and held[0] < 2:
                held[0] += 1
                raise PermissionError(32, "The process cannot access the file")
            return real(src, dst)
        with mock.patch.object(backup, "RENAME_TRIES", 5), mock.patch.object(backup, "RENAME_WAIT", 0), \
                mock.patch.object(backup.os, "replace", side_effect=busy_twice):
            r = self.take()
        self.assertEqual((r["made"], r["replaced"], held[0]), (True, True, 2))
        self.assertEqual(backup.list_backups(self.folder())[0].manifest.version, "1.1")


class Clock(Base):
    def test_a_clock_behind_never_removes_the_backup_just_made(self):
        config.save({"backup_keep_daily": 2, "backup_keep_weekly": 0})
        self.clock[0] = calendar.timegm((2026, 11, 20, 12, 0, 0))
        for i in range(3):
            self.change("1.%d" % i)
            self.take()
            self.clock[0] += DAY
        self.assertEqual(self.names(), ["2026-11-22", "2026-11-21"])
        self.clock[0] = calendar.timegm((2026, 10, 21, 12, 0, 0))       # the clock a month back
        self.change("2.0")
        logged = []
        r = backup.take(self.project, config.load(), self.target, log=logged.append)
        self.assertEqual((r["made"], r["name"], r["pruned"]["removed"]), (True, "2026-10-21", []))
        self.assertEqual(r["pruned"]["dated_after"], ["2026-11-21", "2026-11-22"])
        self.assertEqual(self.names(), ["2026-11-22", "2026-11-21", "2026-10-21"])
        self.assertTrue(any("clock is behind" in l for l in logged), logged)
        config.save({"backup_max_size": "1 KB"})                        # nor by size: that goes oldest first
        self.assertEqual(backup.take(self.project, config.load(), self.target)["pruned"]["removed"], [])

    def test_a_clock_that_was_ahead(self):
        config.save({"backup_keep_daily": 2, "backup_keep_weekly": 0})
        self.release("1.0")
        self.take()                                                     # 2026-11-02
        self.clock[0] += 60 * DAY
        self.change("1.1")
        self.take()                                                     # 2027-01-01: the clock ran ahead
        self.clock[0] -= 59 * DAY
        self.change("1.2")
        r = self.take()                                                 # put right: 2026-11-03
        self.assertEqual((r["made"], r["name"], r["pruned"]["removed"]), (True, "2026-11-03", []))
        self.assertEqual(self.names(), ["2027-01-01", "2026-11-03", "2026-11-02"])
        self.assertEqual(backup.newest(self.project, config.load()), "2026-11-03T12:00:00Z")   # not a time to come


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

    def test_a_damaged_pool_blob_is_never_sent_and_is_read_again(self):
        self.release("1.0")
        self.take()
        sha = Manifest.from_json(read(self.p("www", "manifest.json"))).files["a.pak"].sha256
        blob = os.path.join(self.folder(), "blobs", sha)
        write(blob, b"x" * 4000)                                        # the right size, not the content
        for dry in (True, False):
            with self.assertRaises(backup.BackupError) as e:
                backup.restore(self.project, config.load(), "2026-11-02", deploy.FolderTarget(self.p("fresh")),
                               dry_run=dry)
            self.assertIn(e.exception.code, ("damaged", "incomplete"))
            self.assertFalse(os.path.exists(blob))                      # out of the pool
            self.assertFalse(os.path.exists(self.p("fresh", "manifest.json")))   # nothing was sent
        self.clock[0] += DAY
        r = self.take()                                                 # unchanged, but no longer whole: topped up
        self.assertEqual((r["made"], r["name"], r["fetched"]), (False, "2026-11-02", 1))
        self.assertEqual(read(blob), b"a" * 4000)
        backup.restore(self.project, config.load(), "2026-11-02", deploy.FolderTarget(self.p("fresh")))
        self.assertEqual(read(self.p("fresh", "manifest.json")), read(self.p("www", "manifest.json")))

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


class Schedule(StandIn, TempCase):
    PROGRAM = "C:\\Program Files\\DeltaPatcher\\deltapatcher-cli.exe"

    def setUp(self):
        TempCase.setUp(self)
        self.stand_in()

    def task(self, name):
        root = ElementTree.fromstring(self.tasks[name].encode("utf-16"))
        return lambda path: root.find(path, TASK_NS).text

    def test_windows_task_scheduler(self):
        mod = projects.new("Mod")
        name = "DeltaPatcher backup " + backup.project_id(mod)
        r = backup.schedule(mod, "daily", platform="win32")
        self.assertEqual(r["task"], name)
        self.assertEqual([c[0][:4] for c in self.calls], [["schtasks", "/Query", "/TN", name],
                                                          ["schtasks", "/Create", "/TN", name]])
        create = self.calls[-1][0]
        self.assertEqual((create[4], create[6]), ("/XML", "/F"))
        self.assertFalse(os.path.exists(create[5]))                                     # the definition file is gone
        field = self.task(name)
        self.assertEqual(ntpath.basename(field("t:Actions/t:Exec/t:Command")).lower(), "conhost.exe")
        self.assertEqual(field("t:Actions/t:Exec/t:Arguments"), "--headless " + subprocess.list2cmdline(
            [self.PROGRAM, "--unattended", "--project", mod.path, "backup", "now", "--scheduled", "daily"]))
        self.assertEqual(field("t:Triggers/t:TimeTrigger/t:Repetition/t:Interval"), "PT1H")    # daily too: the run decides
        self.assertEqual(field("t:Principals/t:Principal/t:LogonType"), "InteractiveToken")    # no password kept
        for path, value in (("DisallowStartIfOnBatteries", "false"), ("StopIfGoingOnBatteries", "false"),
                            ("StartWhenAvailable", "true"), ("MultipleInstancesPolicy", "IgnoreNew"),
                            ("ExecutionTimeLimit", backup.TASK_TIME_LIMIT)):
            self.assertEqual(field("t:Settings/t:" + path), value, path)
        self.assertNotIn("Password", self.tasks[name])
        projects.rename(mod, "A new name")                                               # one task, whatever its title
        backup.schedule(mod, "hourly", platform="win32")
        self.assertEqual(list(self.tasks), [name])
        self.assertTrue(self.task(name)("t:Actions/t:Exec/t:Arguments").endswith("--scheduled hourly"))
        self.calls[:] = []
        self.assertEqual(backup.schedule(mod, "off", platform="win32")["removed"], 1)
        self.assertEqual([c[0] for c in self.calls], [["schtasks", "/Query", "/TN", name],
                                                      ["schtasks", "/Delete", "/TN", name, "/F"]])
        self.assertEqual(backup.schedule(mod, "off", platform="win32")["removed"], 0)   # none: nothing to delete
        self.assertEqual([c[0][1] for c in self.calls[2:]], ["/Query"])
        main = projects.main_project()
        backup.schedule(main, "daily", platform="win32")                                 # Default names its file too
        args = self.task("DeltaPatcher backup " + backup.project_id(main))("t:Actions/t:Exec/t:Arguments")
        self.assertIn("--project %s backup" % subprocess.list2cmdline([main.path]), args)

    def test_off_fails_unless_the_task_is_surely_not_there(self):
        mod = projects.new("Mod")
        self.answers[("schtasks", "/Query")] = ("", "ERROR: Access is denied.\r\n", 1)
        with self.assertRaises(backup.BackupError) as e:
            backup.schedule(mod, "off", platform="win32")
        self.assertIn("Access is denied", str(e.exception))
        self.assertFalse([c for c in self.calls if c[0][1] != "/Query"])
        self.answers[("schtasks", "/Query")] = ("", NOT_FOUND, 1)
        self.assertEqual(backup.schedule(mod, "off", platform="win32")["removed"], 0)

    def test_the_tools_answers_are_read_whatever_their_bytes(self):
        mod = projects.new("Mod")
        given = []
        listing = b"# caf\xc3\xa9 \xff\xfe \x81 kept as it is\n*/5 * * * * echo hi\n"

        def fake_run(argv, **kw):
            given.append((argv, kw))
            if argv[0] == "schtasks":
                return subprocess.CompletedProcess(argv, 1, b"", b"ERROR: caf\xe9 \x81\x8d\xff \x90\r\n")
            if argv[1] == "-l":
                return subprocess.CompletedProcess(argv, 0, listing, b"")
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        with mock.patch.object(backup, "run_tool", REAL_RUN_TOOL), \
                mock.patch.object(backup.subprocess, "run", side_effect=fake_run):
            r = backup.run_tool(["schtasks", "/Query", "/TN", "x"])
            self.assertIn("ERROR: caf", r.stderr)
            with self.assertRaises(backup.BackupError):                                 # not "not found": said
                backup.schedule(mod, "off", platform="win32")
            backup.schedule(mod, "hourly", platform="linux")
        written = given[-1][1]["input"]
        self.assertTrue(written.startswith(listing), written)                           # the user's lines byte for byte
        with mock.patch.object(backup, "run_tool", REAL_RUN_TOOL), \
                mock.patch.object(backup.subprocess, "run", side_effect=subprocess.TimeoutExpired(["schtasks"], 60)):
            with self.assertRaises(backup.BackupError) as e:
                backup.schedule(mod, "off", platform="win32")
        self.assertIn("did not answer", str(e.exception))
        with mock.patch.object(backup, "run_tool", REAL_RUN_TOOL), \
                mock.patch.object(backup.subprocess, "run", side_effect=FileNotFoundError(2, "no such program")):
            with self.assertRaises(backup.BackupError):
                backup.schedule(mod, "off", platform="linux")

    def test_crontab(self):
        mod = projects.new("Mod")
        pid = backup.project_id(mod)
        mine = "0 3 * * * 'old' backup # deltapatcher-backup %s" % pid
        self.crontab = "# the user's own\n*/5 * * * * echo hi\r\n%s\n" % mine            # a CR inside a line stays
        r = backup.schedule(mod, "daily", platform="linux")
        lines = self.crontab.split("\n")
        self.assertEqual(lines[:2], ["# the user's own", "*/5 * * * * echo hi\r"])       # the user's lines stay
        self.assertNotIn(mine, lines)
        self.assertTrue(lines[2].endswith("# deltapatcher-backup %s" % pid))
        self.assertRegex(lines[2], r"^\d{1,2} \* \* \* \* ")                                # every hour; the run decides
        self.assertIn("--unattended --project %s backup now --scheduled daily" % shlex.quote(mod.path), lines[2])
        self.assertEqual((r["entry"], lines[3:]), (lines[2], [""]))
        count = len(self.calls)
        self.crontab = "# only the user's\n"
        self.assertEqual(backup.schedule(mod, "off", platform="linux")["removed"], 0)
        self.assertEqual([c[0] for c in self.calls[count:]], [["crontab", "-l"]])        # read, never written
        self.crontab = None                                                              # none yet
        backup.schedule(mod, "daily", platform="linux")
        self.assertEqual(len(self.crontab.split("\n")), 2)
        self.answers[("crontab", "-l")] = ("", "permission denied\n", 1)               # never written blind
        count = len(self.calls)
        with self.assertRaises(backup.BackupError):
            backup.schedule(mod, "off", platform="linux")
        self.assertEqual([c[0] for c in self.calls[count:]], [["crontab", "-l"]])
        with self.assertRaises(backup.BackupError):
            backup.schedule(mod, "weekly", platform="linux")

    def test_paths_the_scheduler_cannot_carry_are_refused(self):
        for folder, platform in (("a\nb", "linux"), ("a\rb", "win32"), ("x\x07y", "linux"), ("100%USERPROFILE%", "win32"),
                                 ("100% done", "win32")):
            odd = projects.Project(self.p(folder, "Mod.ini"), "given", "Odd")
            count = len(self.calls)
            with self.assertRaises(backup.BackupError, msg=repr(folder)) as e:
                backup.schedule(odd, "daily", platform=platform)
            self.assertEqual(e.exception.code, "bad-path")
            self.assertEqual(self.calls[count:], [])                                     # nothing asked of the system
        odd = projects.Project(self.p("My 100% game", "Mod.ini"), "given", "Odd")
        backup.schedule(odd, "daily", platform="linux")                                  # cron: % escaped
        self.assertIn("100\\% game", self.crontab)

    def test_cron_cannot_reach_ssh_agent_and_is_said_to(self):
        key = self.p("id_test")
        write(key, b"a key file")
        cfg = dict(config.load(), deploy_mode="sftp", ssh_key=key)
        with mock.patch.object(ssh, "key_has_passphrase", return_value=True):
            self.assertIn("backup_every_hours", backup.agent_warning(cfg))
            self.assertEqual(backup.agent_warning(dict(cfg, deploy_mode="folder")), "")
            config.save({"ssh_key": key})
            with mock.patch.object(sys, "platform", "linux"):
                rc, ev = run_json(["backup", "schedule", "daily"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertIn("cron cannot reach ssh-agent", ev[-1]["data"]["warning"])
        with mock.patch.object(ssh, "key_has_passphrase", return_value=False):
            self.assertEqual(backup.agent_warning(cfg), "")


class Scheduled(StandIn, Base):
    def setUp(self):
        Base.setUp(self)
        self.stand_in()

    def scheduled(self, how="daily"):
        return run_json(["--unattended", "backup", "now", "--scheduled", how])

    def test_a_backup_a_day_and_each_run_noted(self):
        self.release("1.0")
        rc, ev = self.scheduled()
        self.assertEqual((rc, ev[-1]["data"]["made"]), (0, True))
        noted = backup.last_scheduled(self.folder())
        self.assertEqual((noted["ok"], noted["made"], noted["name"], noted["schedule"], noted["time"]),
                         (True, True, "2026-11-02", "daily", "2026-11-02T12:00:00Z"))
        self.clock[0] += 3600
        self.change("1.1")
        rc, ev = self.scheduled()                                    # an hour later: not due
        self.assertEqual((rc, ev[-1]["data"]["skipped"]), (0, True))
        self.assertEqual(self.names(), ["2026-11-02"])
        self.clock[0] += 22 * 3600 + 50 * 60                         # 23 h 50 min after it: due
        rc, ev = self.scheduled()
        self.assertEqual((ev[-1]["data"]["made"], ev[-1]["data"]["name"]), (True, "2026-11-03"))
        self.clock[0] += 50 * 60
        rc, ev = self.scheduled("hourly")                            # hourly: the next hour's start
        self.assertEqual((ev[-1]["data"].get("skipped"), ev[-1]["data"]["made"]), (None, False))
        self.assertIn("no new backup", backup.last_scheduled(self.folder())["message"])
        self.clock[0] += 30 * 60
        self.assertTrue(self.scheduled("hourly")[1][-1]["data"]["skipped"])
        rc, text = run_text(["backup", "list"])
        self.assertIn("The system's last scheduled run: 2026-11-03 12:40:00 UTC: no new backup", text)

    def test_a_failure_is_noted_and_the_next_hour_tries_again(self):
        self.release("1.0")
        config.save({"deploy_folder": ""})                           # no destination: it fails
        rc, ev = self.scheduled()
        self.assertEqual((rc, ev[-1]["code"]), (1, "no-destination"))
        noted = run_json(["backup", "list"])[1][-1]["data"]["scheduled"]
        self.assertEqual((noted["ok"], noted["code"]), (False, "no-destination"))
        self.assertIn("set the folder releases are copied to", noted["message"])
        config.save({"deploy_folder": self.p("www")})
        self.clock[0] += 3600
        self.assertTrue(self.scheduled()[1][-1]["data"]["made"])     # a failure is no backup: tried again
        self.clock[0] += 2 * DAY
        with client.Lock(self.folder()):                             # another backup of it is running
            rc, ev = self.scheduled()
        self.assertEqual(ev[-1]["code"], "busy")
        self.assertTrue(backup.last_scheduled(self.folder())["ok"])  # not noted as a failure
        write(self.p("www", "manifest.json"), b"{not a manifest")    # a damaged release there: said plainly
        rc, ev = self.scheduled()
        noted = backup.last_scheduled(self.folder())
        self.assertEqual((rc, noted["code"]), (1, "bad-manifest"))
        self.assertIn("is damaged", noted["message"])
        self.assertNotIn("Expecting", noted["message"])
        self.assertEqual(cli.plain_reason(FileNotFoundError(2, "No such file or directory", "x.ini")),
                         "No such file or directory: x.ini")
        self.assertIn("did not expect (KeyError)", cli.plain_reason(KeyError("internal")))

    def test_an_unattended_run_never_asks(self):
        config.save({"deploy_mode": "sftp", "ssh_host": "game.example.com", "ssh_user": "deploy",
                     "remote_root": "/var/www/game", "ssh_key": self.p("key")})
        write(self.p("key"), b"a key file")
        asked = mock.Mock(side_effect=AssertionError("it asked"))

        def run(argv, typed=asked):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                    mock.patch.object(sys, "stdin", Tty("")), mock.patch("builtins.input", typed), \
                    mock.patch("getpass.getpass", asked):
                rc = cli.main(["--no-log", "--no-color"] + argv)
            return rc, out.getvalue()
        with mock.patch.object(ssh, "permission_problems", return_value=["others can read it"]):
            rc, text = run(["--unattended", "backup", "now", "--scheduled", "daily"])
            self.assertEqual(rc, 1)
            self.assertIn("can be read by other accounts", text)
            self.assertEqual(backup.last_scheduled(self.folder())["code"], "key-permissions")
            typed = mock.Mock(return_value="n")
            run(["backup", "now"], typed)                            # a person at a console would be asked
            typed.assert_called()
        with mock.patch.object(ssh, "permission_problems", return_value=[]), \
                mock.patch.object(ssh, "key_has_passphrase", return_value=False), \
                mock.patch.object(ssh, "is_trusted", return_value=False), \
                mock.patch.object(ssh, "scan_host", return_value=[("ssh-ed25519", "AAAA", "SHA256:test")]):
            rc, text = run(["--unattended", "backup", "now", "--scheduled", "daily"])
        self.assertEqual(rc, 1)
        self.assertEqual(backup.last_scheduled(self.folder())["code"], "host-unknown")
        asked.assert_not_called()

    def test_defaults_schedule_names_its_file_whatever_the_environment_says(self):
        mod = projects.new("Mod")
        config.save({"deploy_mode": "folder", "deploy_folder": self.p("modwww")}, mod)
        self.release("1.0")
        r = backup.schedule(self.project, "daily", platform="linux")
        argv = shlex.split(r["command"])
        self.assertEqual(argv[:4], [Schedule.PROGRAM, "--unattended", "--project", self.project.path])
        with mock.patch.dict(os.environ, {"DELTAPATCHER_PROJECT": "Mod"}):
            rc, ev = run_json(argv[1:])                              # the line the scheduler runs, by hand
        self.assertEqual((rc, ev[-1]["data"]["made"]), (0, True), ev[-1])
        self.assertEqual(self.names(), ["2026-11-02"])
        self.assertFalse(os.path.exists(backup.folder_of(mod, config.load(mod))))

    def test_a_project_that_leaves_takes_its_schedule_along(self):
        mod, other = projects.new("Mod"), projects.new("Other")
        for p in (mod, other, self.project):
            backup.schedule(p, "daily")
        self.assertEqual(self.ours(), 3)
        rc, ev = run_json(["project", "remove", "Mod", "--yes"])
        self.assertEqual((rc, ev[-1]["data"]["schedule_removed"]), (0, 1))
        self.assertTrue(any("scheduled backups of Mod were removed" in e.get("text", "") for e in ev), ev)
        self.assertEqual(self.ours(), 2)
        rc, ev = run_json(["project", "remove", "--all", "--yes"])
        self.assertEqual([p["schedule_removed"] for p in ev[-1]["data"]["removed"]], [1])
        self.assertEqual(self.ours(), 1)
        rc, ev = run_json(["reset", "--yes"])
        self.assertEqual((rc, ev[-1]["data"]["schedules_removed"]), (0, ["Default"]))
        self.assertEqual(self.ours(), 0)


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
        self.assertEqual((listed["base"], listed["unreadable"], listed["scheduled"]),
                         (os.path.join(os.path.dirname(config.path()), "backups"), [], None))
        rc, ev = run_json(["backup", "restore", "2026-11-02"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "confirm-needed"))                    # asks first
        self.assertEqual((ev[-1]["data"]["live"]["version"], ev[-1]["data"]["live_problem"]), ("1.0", ""))
        rc, ev = run_json(["backup", "restore", "2026-11-02", "--dry-run"])
        self.assertEqual((rc, ev[-1]["data"]["dry_run"]), (0, True))
        rc, ev = run_json(["backup", "restore", "2026-11-02", "--yes"])
        self.assertEqual(rc, 0, ev[-1])
        rc, ev = run_json(["backup", "prune"])
        self.assertEqual((rc, ev[-1]["data"]["kept"]), (0, ["2026-11-02"]))
        self.assertEqual(run_json(["config", "set", "backup_max_size=huge"])[1][-1]["code"], "bad-setting")
        self.assertEqual(run_json(["config", "set", "backup_max_size=2 GB"])[0], 0)
        rc, text = run_text(["backup", "list"])
        self.assertIn("each named by its day in UTC", text)
        config.save({"deploy_mode": "local"})
        self.assertEqual(run_json(["backup"])[1][-1]["code"], "no-destination")
        config.save({"deploy_mode": "sftp", "ssh_host": ""})
        self.assertEqual(run_json(["backup"])[1][-1]["code"], "no-destination")
        config.save({"deploy_mode": "folder", "deploy_folder": ""})
        self.assertEqual(run_json(["backup"])[1][-1]["code"], "no-destination")

    def test_a_restore_says_a_missing_backup_before_it_connects(self):
        config.save({"deploy_mode": "sftp", "ssh_host": "game.example.com", "ssh_user": "deploy",
                     "remote_root": "/var/www/game"})
        connect = mock.Mock(side_effect=AssertionError("it connected"))
        with mock.patch.object(ssh, "is_trusted", connect), mock.patch.object(ssh, "scan_host", connect), \
                mock.patch.object(ssh, "permission_problems", connect), mock.patch.object(ssh, "sftp", connect):
            rc, ev = run_json(["backup", "restore", "2020-01-01"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "no-such-backup"))
        connect.assert_not_called()

    def test_a_restore_says_when_the_release_there_cannot_be_read(self):
        self.release("1.0")
        self.take()
        write(self.p("www", "manifest.json"), b"{not a manifest")
        rc, ev = run_json(["backup", "restore", "2026-11-02"])
        self.assertEqual(ev[-1]["code"], "confirm-needed")
        self.assertIsNone(ev[-1]["data"]["live"])
        self.assertEqual(ev[-1]["data"]["live_problem"], "its manifest is damaged")     # no parser's words
        with mock.patch.object(cli, "interactive", return_value=True), mock.patch("builtins.input", return_value="n"):
            rc, text = run_text(["backup", "restore", "2026-11-02"])
        self.assertIn("The release there now could not be read", text)
        self.assertNotIn("instead of nothing", text)
        self.assertIn("except a file of the same name and the wrong size", text)
        os.remove(self.p("www", "manifest.json"))                                        # nothing there: said so
        with mock.patch.object(cli, "interactive", return_value=True), mock.patch("builtins.input", return_value="n"):
            rc, text = run_text(["backup", "restore", "2026-11-02"])
        self.assertIn("instead of nothing", text)

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


if __name__ == "__main__":
    unittest.main()
