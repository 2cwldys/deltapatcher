"""Several projects: each one a settings file of its own (Default is the main one), picked by name or file
with --project, listed, made, added, renamed and removed; update --all runs them at the same time."""

import contextlib
import io
import json
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, snapshot, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import cli, client, config, logs, projects, publish, signing  # noqa: E402
from deltapatcher.projects import ProjectError  # noqa: E402


def run_json(argv, env=None):
    """cli.main in --json mode: (exit code, [events]). Its standard input is empty, as the app's is between
    commands: the engine's reader of it ends at once."""
    out = io.StringIO()
    with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), \
            mock.patch.object(sys, "stdin", io.StringIO("")):
        rc = cli.main(["--json", "--no-log"] + argv)
    return rc, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def run_text(argv):
    """cli.main in a terminal's way (no --json): (exit code, what it printed)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = cli.main(["--no-log"] + argv)
    return rc, out.getvalue()


class Names(TempCase):
    def test_name_rules(self):
        for bad in ["", "   ", "a/b", "a\\b", "a:b", "tab\there", "x" * 61, "CON", "con", "Lpt1", "COM9.backup",
                    "nul.ini", "ends with a dot.", "caf\u00e9", "what?", "star*", "-mod"]:
            with self.assertRaises(ProjectError, msg=repr(bad)) as e:
                projects.check_name(bad)
            self.assertEqual(e.exception.code, "bad-name", bad)
        for good in ["My Game", "mod_2.0 (beta)", "x" * 60, "CONSOLE", "COM10", "a-b", "Default 2"]:
            self.assertEqual(projects.check_name(good), good)
        self.assertEqual(projects.check_name("  spaced  "), "spaced")

    def test_names_are_unique_letter_case_aside(self):
        projects.new("My Game")
        for taken in ("my game", "MY GAME", "default", "Default"):
            with self.assertRaises(ProjectError) as e:
                projects.check_name(taken)
            self.assertEqual(e.exception.code, "name-taken")
        mine = projects.resolve("My Game")
        self.assertEqual(projects.check_name("my GAME", mine), "my GAME")      # its own name, in other letters

    def test_resolve_by_name_file_or_file_name(self):
        a = projects.new("Alpha Game")
        ext = self.p("share", "deltapatcher.ini")
        write(ext, "[project]\ntitle = Shared\n\n[update]\ninstall_dir =\n")
        projects.add(ext)
        self.assertEqual(projects.resolve("alpha game").path, a.path)           # by name, letter case aside
        self.assertEqual(projects.resolve(a.path).path, a.path)                 # by its file
        self.assertEqual(projects.resolve("Alpha Game.ini").path, a.path)       # by its file's name in projects/
        self.assertEqual(projects.resolve("SHARED").path, ext)
        self.assertEqual(projects.resolve(ext).kind, "added")
        for default in ("", None, "default", "DEFAULT", config.path()):
            self.assertTrue(projects.resolve(default).main, default)
        elsewhere = self.p("elsewhere", "launcher.ini")                          # a file not on the list
        write(elsewhere, "[update]\nsource_url = https://game.example.com/\n")
        given = projects.resolve(elsewhere)
        self.assertEqual((given.kind, given.title), ("given", "launcher"))
        with self.assertRaises(ProjectError) as e:
            projects.resolve("nothing like it")
        self.assertEqual(e.exception.code, "no-such-project")
        notes = self.p("elsewhere", "win.ini")
        write(notes, "[fonts]\nsize = 3\n")
        with self.assertRaises(ProjectError) as e:
            projects.resolve(notes)
        self.assertEqual(e.exception.code, "not-a-project")

    def test_two_files_with_one_name_are_named_by_file(self):
        write(os.path.join(projects.folder(), "one.ini"), "[project]\ntitle = Twin\n")
        write(os.path.join(projects.folder(), "two.ini"), "[project]\ntitle = twin\n")
        with self.assertRaises(ProjectError) as e:
            projects.resolve("Twin")
        self.assertEqual(e.exception.code, "ambiguous")
        self.assertEqual(projects.resolve(os.path.join(projects.folder(), "two.ini")).title, "twin")


class Listing(TempCase):
    def test_the_projects_folder_and_added_files_are_listed(self):
        projects.new("Zeta")
        projects.new("alpha")
        write(os.path.join(projects.folder(), "hand made.ini"), "[update]\ninstall_dir =\n")   # no title: its file's name
        write(os.path.join(projects.folder(), "notes.txt"), "not a project")
        one = self.p("games", "one", "deltapatcher.ini")
        write(one, "[project]\ntitle = Game One\n")
        projects.add(one)
        every = projects.all_projects()
        self.assertEqual([p.title for p in every], ["Default", "alpha", "Game One", "hand made", "Zeta"])
        self.assertEqual([p.kind for p in every], ["main", "folder", "added", "folder", "folder"])
        self.assertEqual(projects.folder(), os.path.join(os.path.dirname(config.path()), "projects"))
        self.assertIn(one, config.load()["project_files"])                     # the main file lists it

        rc, ev = run_json(["projects"])
        self.assertEqual(rc, 0, ev)
        data = ev[-1]["data"]
        self.assertEqual([r["title"] for r in data["projects"]], [p.title for p in every])
        self.assertEqual((data["main"], data["current"], data["parallel_updates"]), (config.path(), config.path(), 3))
        self.assertEqual([r["current"] for r in data["projects"]], [True, False, False, False, False])

    def test_what_each_project_has_set_up(self):
        mod = projects.new("Mod")
        config.save({"install_dir": self.p("game"), "source_url": "https://mod.example.com/",
                     "publish_src": self.p("src"), "deploy_mode": "folder", "deploy_folder": self.p("www")}, mod)
        rows = {r["title"]: r for r in run_json(["projects"])[1][-1]["data"]["projects"]}
        self.assertEqual((rows["Mod"]["update_ready"], rows["Mod"]["release_ready"]), (True, True))
        self.assertEqual(rows["Mod"]["where"], "the folder %s" % self.p("www"))
        self.assertEqual((rows["Default"]["update_ready"], rows["Default"]["release_ready"]), (False, False))
        rc, text = run_text(["projects"])
        self.assertIn("2 projects", text)
        self.assertIn("update   %s  from  https://mod.example.com/" % self.p("game"), text)
        self.assertIn("release  %s  to  the folder %s" % (self.p("src"), self.p("www")), text)


class Settings(TempCase):
    def test_save_routes_each_setting_to_its_file(self):
        mod = projects.new("Mod")
        config.save({"install_dir": self.p("game"), "name": "The Mod", "parallel_updates": 5, "close_to_tray": True}, mod)
        own, main = config.file_values(mod.path), config.file_values(config.path())
        self.assertEqual((own["install_dir"], own["name"]), (self.p("game"), "The Mod"))
        self.assertNotIn("parallel_updates", own)
        self.assertNotIn("close_to_tray", own)
        self.assertEqual((main["parallel_updates"], main["close_to_tray"]), (5, True))
        self.assertEqual(main["install_dir"], "")
        text = read(mod.path).decode()
        self.assertNotIn("[window]", text)
        self.assertNotIn("[general]", text)
        cfg = config.load(mod)                       # its own settings, and DeltaPatcher's own from the main file
        self.assertEqual((cfg["install_dir"], cfg["parallel_updates"], cfg["close_to_tray"], cfg["title"]),
                         (self.p("game"), 5, True, "Mod"))
        cfg = config.load()                          # Default
        self.assertEqual((cfg["install_dir"], cfg["parallel_updates"], cfg["name"]), ("", 5, ""))

    def test_a_project_file_never_holds_deltapatchers_own_settings(self):
        mod = projects.new("Mod")
        with open(mod.path, "a", encoding="utf-8") as f:
            f.write("\n[window]\nclose_to_tray = yes\n\n[general]\nparallel_updates = 9\n")
        cfg = config.load(mod)
        self.assertEqual((cfg["close_to_tray"], cfg["parallel_updates"]), (False, 3))

    def test_config_command_with_a_project(self):
        mod = projects.new("Mod")
        rc, ev = run_json(["--project", "Mod", "config", "set", "install_dir=" + self.p("game"), "parallel_updates=4"])
        self.assertEqual(rc, 0, ev)
        self.assertEqual(ev[-1]["data"]["path"], mod.path)
        self.assertEqual(config.file_values(mod.path)["install_dir"], self.p("game"))
        self.assertEqual(config.file_values(config.path())["parallel_updates"], 4)
        rc, ev = run_json(["config", "get", "--project", "mod"])                 # after the command, any letters
        data = ev[-1]["data"]
        self.assertEqual((data["path"], data["main_path"]), (mod.path, config.path()))
        self.assertEqual((data["project"]["project"], data["project"]["kind"]), ("Mod", "folder"))
        self.assertEqual((data["settings"]["install_dir"], data["settings"]["parallel_updates"]), (self.p("game"), 4))
        rc, ev = run_json(["--project", "Mod", "config", "set", "title=default"])  # a name is checked here too
        self.assertEqual((rc, ev[-1]["code"]), (1, "name-taken"))
        rc, ev = run_json(["--project", "Nobody", "config", "get"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "no-such-project"))
        rc, text = run_text(["--project", "Mod", "config"])
        self.assertIn("Project:  Mod, %s" % mod.path, text)

    def test_project_after_any_command(self):
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["--project", "X", "update"]).project, "X")
        self.assertEqual(parser.parse_args(["update", "--project", "X"]).project, "X")
        self.assertEqual(parser.parse_args(["sign", "status", "--project", "X"]).project, "X")
        self.assertEqual(parser.parse_args(["ssh", "test", "--project", "X"]).project, "X")
        self.assertIsNone(parser.parse_args(["update"]).project)


class Commands(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        write(self.p("game", "game.exe"), b"the program")
        write(self.p("game", "maps", "a.bsp"), b"a map")
        self.mod = projects.new("Mod")
        rc, ev = run_json(["--project", "Mod", "config", "set", "publish_src=" + self.p("game"),
                           "store_dir=" + self.p("store"), "install_dir=" + self.p("player"),
                           "source_url=" + self.p("store"), "name=The Mod", "version=1.0"])
        self.assertEqual(rc, 0, ev)

    def test_publish_check_update_of_a_project(self):
        rc, ev = run_json(["publish", "--project", "Mod"])                        # its folder, its store
        self.assertEqual(rc, 0, ev)
        self.assertEqual(ev[-1]["data"]["store"], self.p("store"))
        rc, ev = run_json(["check", "--project", "Mod"])                          # its folder, its source
        self.assertEqual((rc, ev[-1]["data"]["add"]), (2, 2))
        self.assertFalse(os.path.exists(self.p("player", "game.exe")))
        rc, ev = run_json(["--project", "Mod", "update"])
        self.assertEqual(rc, 0, ev)
        self.assertEqual(snapshot(self.p("player")), snapshot(self.p("game")))
        rc, ev = run_json(["--project", "Mod", "update", "--check"])
        self.assertEqual((rc, ev[-1]["data"]["empty"]), (0, True))
        rc, ev = run_json(["update"])                                             # Default has no folder
        self.assertEqual((rc, ev[-1]["code"]), (1, "no-folder"))
        rc, ev = run_json(["rollback", "--project", "Mod"])                       # its folder, as it was before
        self.assertEqual((rc, ev[-1]["data"]["undone"]), (0, 2))
        self.assertEqual(snapshot(self.p("player")), {})
        self.assertEqual(run_json(["rollback"])[1][-1]["code"], "no-folder")

    def test_without_project_the_command_line_uses_default(self):
        config.save({"current_project": self.mod.ref()})                         # what the app shows
        self.assertEqual(projects.current().path, self.mod.path)
        rc, ev = run_json(["config", "get"])
        self.assertEqual(ev[-1]["data"]["path"], config.path())
        rc, ev = run_json(["update"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "no-folder"))
        rc, ev = run_json(["config", "get"], env={"DELTAPATCHER_PROJECT": "Mod"})
        self.assertEqual(ev[-1]["data"]["path"], self.mod.path)
        rc, ev = run_json(["--project", "Default", "config", "get"], env={"DELTAPATCHER_PROJECT": "Mod"})
        self.assertEqual(ev[-1]["data"]["path"], config.path())

    def test_new_add_rename_remove(self):
        rc, ev = run_json(["project", "new", "My Mod"])
        self.assertEqual(rc, 0, ev)
        made = ev[-1]["data"]["file"]
        self.assertEqual(made, os.path.join(projects.folder(), "My Mod.ini"))
        self.assertEqual(config.file_values(made)["title"], "My Mod")
        self.assertEqual(run_json(["project", "new", "my mod"])[1][-1]["code"], "name-taken")
        self.assertEqual(run_json(["project", "new", "AUX"])[1][-1]["code"], "bad-name")
        write(os.path.join(projects.folder(), "Other.ini"), "[project]\ntitle = Something\n")
        rc, ev = run_json(["project", "new", "Other"])                         # its file's name is in use
        self.assertEqual(ev[-1]["data"]["file"], os.path.join(projects.folder(), "Other (2).ini"))

        rc, ev = run_json(["project", "rename", "My Mod", "Mod Two"])
        self.assertEqual((rc, ev[-1]["data"]["file"]), (0, made))                # the file stays
        self.assertEqual(projects.resolve("mod two").path, made)
        self.assertEqual(run_json(["project", "rename", "Mod Two", "Something"])[1][-1]["code"], "name-taken")
        rc, ev = run_json(["project", "rename", "Default", "Main Game"])
        self.assertEqual(rc, 0, ev)
        self.assertTrue(projects.resolve("main game").main)
        self.assertEqual(config.file_values(config.path())["title"], "Main Game")

        server = self.p("share", "server.ini")
        write(server, "[update]\ninstall_dir =\n")
        rc, ev = run_json(["project", "add", server])
        self.assertEqual((rc, ev[-1]["data"]["title"], ev[-1]["data"]["kind"]), (0, "server", "added"))
        self.assertEqual(run_json(["project", "add", server])[1][-1]["code"], "listed")
        twin = self.p("share2", "server.ini")
        write(twin, "[update]\ninstall_dir =\n")
        rc, ev = run_json(["project", "add", twin])
        self.assertEqual((rc, ev[-1]["code"], ev[-1]["data"]["name"]), (1, "name-taken", "server"))
        rc, ev = run_json(["project", "add", twin, "--name", "Server B"])
        self.assertEqual((rc, ev[-1]["data"]["title"]), (0, "Server B"))
        self.assertEqual(config.file_values(twin)["title"], "Server B")
        write(self.p("share", "notes.ini"), "[fonts]\nsize = 3\n")
        self.assertEqual(run_json(["project", "add", self.p("share", "notes.ini")])[1][-1]["code"], "not-a-project")
        self.assertEqual(run_json(["project", "add", self.p("share", "nothing.ini")])[1][-1]["code"], "not-found")

        rc, ev = run_json(["project", "remove", "Mod Two"])                    # the app asks first, then --yes
        self.assertEqual((rc, ev[-1]["code"]), (1, "confirm-needed"))
        self.assertTrue(os.path.isfile(made))
        rc, ev = run_json(["project", "remove", "Mod Two", "--yes"])
        self.assertEqual((rc, ev[-1]["data"]["removed"]), (0, "deleted"))
        self.assertFalse(os.path.exists(made))
        rc, ev = run_json(["project", "remove", "server", "--yes"])
        self.assertEqual((rc, ev[-1]["data"]["removed"]), (0, "unlisted"))
        self.assertTrue(os.path.isfile(server))                                  # an added file is never deleted
        self.assertNotIn(server, config.load()["project_files"])
        self.assertEqual(run_json(["project", "remove", "Main Game", "--yes"])[1][-1]["code"], "default")

        config.save({"current_project": twin})                                   # the app shows Server B
        os.remove(twin)                         # its file is gone, its name with it: removable by its path
        self.assertEqual(run_json(["project", "remove", "Server B", "--yes"])[1][-1]["code"], "no-such-project")
        rc, ev = run_json(["project", "remove", twin, "--yes"])
        self.assertEqual((rc, ev[-1]["data"]["removed"]), (0, "unlisted"))
        self.assertEqual(config.load()["current_project"], "")                  # the app opens on Default again
        self.assertEqual([p.title for p in projects.all_projects()], ["Main Game", "Mod", "Other", "Something"])

    def test_remove_asks_in_a_terminal(self):
        with mock.patch.object(cli, "interactive", return_value=True), mock.patch("builtins.input", return_value="n"):
            rc, text = run_text(["project", "remove", "Mod"])
        self.assertEqual(rc, 1)
        self.assertIn("Its settings file is deleted", text)
        self.assertTrue(os.path.isfile(self.mod.path))
        with mock.patch.object(cli, "interactive", return_value=True), mock.patch("builtins.input", return_value="y"):
            rc, text = run_text(["project", "remove", "Mod"])
        self.assertEqual(rc, 0, text)
        self.assertFalse(os.path.exists(self.mod.path))


class UpdateAll(TempCase):
    NAMES = ("Default", "Alpha", "Beta")

    def setUp(self):
        TempCase.setUp(self)
        for name in self.NAMES:
            low = name.lower()
            write(self.p("src", low, "data", "pak0.pk3"), os.urandom(70000))
            write(self.p("src", low, "readme.txt"), "%s 1.0\n" % name)
            publish.publish(self.p("src", low), self.p("store", low), name, "1.0")
            project = None if name == "Default" else projects.new(name)
            config.save({"install_dir": self.p("install", low), "source_url": self.p("store", low)}, project)

    def updated(self, name):
        low = name.lower()
        return snapshot(self.p("install", low)) == snapshot(self.p("src", low))

    def timed(self, delay=0.4):
        """client.update, with when each folder's update started and ended."""
        spans, lock, real = {}, threading.Lock(), client.update

        def run(url, install, **kw):
            start = time.monotonic()
            time.sleep(delay)
            try:
                return real(url, install, **kw)
            finally:
                with lock:
                    spans[os.path.basename(install)] = (start, time.monotonic())
        return spans, mock.patch.object(cli.client, "update", side_effect=run)

    def test_three_projects_at_the_same_time(self):
        spans, patch = self.timed()
        with patch:
            rc, ev = run_json(["update", "--all"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(sorted(spans), ["alpha", "beta", "default"])
        starts, ends = [s for s, _e in spans.values()], [e for _s, e in spans.values()]
        self.assertLess(max(starts), min(ends))          # at one moment all three were running
        for name in self.NAMES:
            self.assertTrue(self.updated(name), name)
        data = ev[-1]["data"]
        self.assertEqual([(r["project"], r["ok"]) for r in data["projects"]], [(n, True) for n in self.NAMES])
        self.assertEqual((data["jobs"], data["failed"]), (3, 0))

    def test_jobs_one_runs_them_one_after_another(self):
        spans, patch = self.timed(0.1)
        with patch:
            rc, ev = run_json(["update", "--all", "--jobs", "1"])
        self.assertEqual(rc, 0, ev[-1])
        ordered = sorted(spans.values())
        for (_s1, e1), (s2, _e2) in zip(ordered, ordered[1:]):
            self.assertGreaterEqual(s2, e1)
        config.save({"parallel_updates": 2})
        rc, ev = run_json(["check", "--all"])
        self.assertEqual(next(e for e in ev if e["event"] == "queue")["jobs"], 2)

    def test_one_failing_project_fails_the_run_and_the_others_finish(self):
        config.save({"source_url": self.p("store", "missing")}, projects.resolve("Alpha"))
        rc, ev = run_json(["update", "--all"])
        self.assertEqual((rc, ev[-1]["event"], ev[-1]["code"]), (1, "error", "projects-failed"))
        self.assertIn("Alpha", ev[-1]["message"])
        rows = {r["project"]: r for r in ev[-1]["data"]["projects"]}
        self.assertEqual({n: r["ok"] for n, r in rows.items()}, {"Default": True, "Alpha": False, "Beta": True})
        self.assertIn("manifest.json", rows["Alpha"]["message"])
        self.assertTrue(self.updated("Default"))
        self.assertTrue(self.updated("Beta"))

    def test_a_failed_download_stops_only_its_own_project(self):
        blobs = self.p("store", "beta", "blobs")
        for name in os.listdir(blobs):                    # Beta's blobs are damaged: its download fails
            size = os.path.getsize(os.path.join(blobs, name))
            write(os.path.join(blobs, name), b"x" * size)
        failed, real = threading.Event(), client.update

        def ordered(url, install, **kw):                  # the others download only after Beta has failed
            if os.path.basename(install) == "beta":
                try:
                    return real(url, install, **kw)
                finally:
                    failed.set()
            failed.wait(20)
            return real(url, install, **kw)

        with mock.patch.object(cli.client, "update", side_effect=ordered):
            rc, ev = run_json(["update", "--all"])
        rows = {r["project"]: r for r in ev[-1]["data"]["projects"]}
        self.assertEqual(rc, 1)
        self.assertEqual((rows["Beta"]["ok"], rows["Beta"]["code"]), (False, "failed"))
        self.assertIn("SHA-256", rows["Beta"]["message"])
        self.assertTrue(rows["Default"]["ok"] and rows["Alpha"]["ok"])
        self.assertTrue(self.updated("Default") and self.updated("Alpha"))

    def test_events_carry_their_project(self):
        rc, ev = run_json(["update", "--all"])
        self.assertEqual(rc, 0)
        files = {p.title: p.path for p in projects.all_projects()}
        queue = next(e for e in ev if e["event"] == "queue")
        self.assertEqual([(r["project"], r["file"]) for r in queue["run"]], [(n, files[n]) for n in self.NAMES])
        for name in self.NAMES:
            mine = [e for e in ev if e.get("project") == name]
            self.assertTrue(all(e["file"] == files[name] for e in mine))
            kinds = [e["event"] for e in mine]
            self.assertEqual((kinds[0], kinds[-1]), ("project-start", "project-end"))
            self.assertIn("progress", kinds)
            self.assertTrue(any(e["event"] == "log" and e["text"].startswith("reading ") for e in mine))
            self.assertTrue(mine[-1]["ok"])
        # every line about a project is marked: the only unmarked ones are the run's own
        for e in ev:
            if e["event"] == "progress":
                self.assertIn("project", e)

    def test_check_all_changes_nothing(self):
        rc, ev = run_json(["check", "--all"])
        self.assertEqual(rc, 2)                           # out of date
        self.assertTrue(all(not r["data"]["empty"] for r in ev[-1]["data"]["projects"]))
        self.assertFalse(os.path.exists(self.p("install", "alpha", "readme.txt")))
        run_json(["update", "--all"])
        rc, ev = run_json(["check", "--all"])
        self.assertEqual(rc, 0)

    def test_projects_not_set_up_are_left_out(self):
        projects.new("Empty")
        rc, ev = run_json(["update", "--all"])
        self.assertEqual(rc, 0)
        self.assertEqual(ev[-1]["data"]["skipped"][0]["project"], "Empty")
        self.assertEqual(len(ev[-1]["data"]["projects"]), 3)
        rc, ev = run_json(["update", "--all", "--source", self.p("store", "alpha")])
        self.assertEqual((rc, ev[-1]["code"]), (1, "all-with-folder"))

    def test_in_a_terminal(self):
        config.save({"source_url": self.p("store", "missing")}, projects.resolve("Beta"))
        rc, text = run_text(["update", "--all"])
        self.assertEqual(rc, 1)
        self.assertIn("Updating 3 projects, 3 at a time", text)
        self.assertIn("[Alpha] reading ", text)
        self.assertIn("[Default] reading ", text)
        self.assertIn("  Alpha    updated: 2 new, 0 changed", text)
        self.assertIn("  Beta     FAILED: ", text)
        self.assertIn("1 of 3 projects failed: Beta", text)

    def test_the_log_says_which_project(self):
        log = self.p("logs", "deltapatcher.log")
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(sys, "stdin", io.StringIO("")):
            self.assertEqual(cli.main(["--json", "--log", log, "update", "--all"]), 0)
            self.assertEqual(cli.main(["--json", "--log", log, "--project", "Alpha", "check"]), 0)
        lines = read(log).decode().splitlines()
        alpha = [line for line in lines if self.p("store", "alpha") in line]
        default = [line for line in lines if self.p("store", "default") in line]
        self.assertEqual(len(alpha), 2)                   # "reading ..." in both runs
        self.assertTrue(all("  [Alpha] reading " in line for line in alpha), alpha)
        self.assertTrue(default and all("[" not in line.split("  ", 1)[1][:1] for line in default), default)
        check = lines[[i for i, line in enumerate(lines) if "--project Alpha check" in line][0]:]
        self.assertTrue(all("[Alpha] " in line for line in check), check)


class Logs(TempCase):
    def test_a_rotation_another_program_blocks_is_left_for_later(self):
        path = self.p("logs", "deltapatcher.log")
        logs.setup(path)
        try:
            handler = logs._logger.handlers[0]
            handler.maxBytes = 4000
            real = os.replace

            def held(src, dst):          # what Windows says while another DeltaPatcher has the log open
                if os.path.abspath(src) == os.path.abspath(path):
                    raise PermissionError(32, "The process cannot access the file: it is being used by another process")
                return real(src, dst)

            err = io.StringIO()
            with mock.patch("os.replace", side_effect=held), contextlib.redirect_stderr(err):
                for i in range(150):
                    logs.write("line %d %s" % (i, "x" * 60), "Alpha")
            self.assertEqual(err.getvalue(), "")                     # never a "--- Logging error ---"
            text = read(path).decode()
            self.assertIn("[Alpha] line 149 ", text)                 # every line is still written
            self.assertFalse(os.path.exists(path + ".1"))
            handler.retry_at = 0                                     # the file is free again
            for i in range(5):
                logs.write("after %d" % i)
            self.assertTrue(os.path.isfile(path + ".1"))
            self.assertIn("after 4", read(path).decode())
        finally:
            logs.close()


class DefaultAsBefore(TempCase):
    OLD = """; DeltaPatcher settings. Edit with any text editor; a line starting with ; is a comment.

[update]
; Where releases come from: an http(s) address or a folder.
source_url = https://game.example.com/
install_dir =

[publish]
name = Old Game
; my own note
version = 2.0

[server]
deploy_mode = folder

[window]
close_to_tray = no
check_updates = no
"""

    def test_an_older_settings_file_is_read_and_keeps_its_shape(self):
        write(config.path(), self.OLD)
        cfg = config.load()
        self.assertEqual((cfg["source_url"], cfg["name"], cfg["deploy_mode"], cfg["title"], cfg["parallel_updates"]),
                         ("https://game.example.com/", "Old Game", "folder", "", 3))
        self.assertEqual([p.title for p in projects.all_projects()], ["Default"])
        rc, ev = run_json(["config", "set", "version=2.1"])
        self.assertEqual(rc, 0, ev)
        self.assertEqual(read(config.path()).decode(), self.OLD.replace("version = 2.0", "version = 2.1"))
        rc, ev = run_json(["config", "get"])
        data = ev[-1]["data"]
        self.assertEqual((data["path"], data["main_path"], data["project"]["project"]),
                         (config.path(), config.path(), "Default"))
        self.assertFalse(os.path.exists(projects.folder()))       # nothing made that was not asked for

    def test_a_portable_settings_file_beside_the_program(self):
        write(self.p("release", "game.pk3"), os.urandom(5000))
        key = None
        if signing.can_sign():
            signing.generate_key(self.p("keys", "sign.pem"), "")
            key = signing.load_key(self.p("keys", "sign.pem"), None)
        publish.publish(self.p("release"), self.p("store"), "Game", "1.0", signer=key)
        trust = signing.public_key_text(key) if key else ""
        program = self.p("portable", "DeltaPatcher.exe")
        ini = self.p("portable", "deltapatcher.ini")
        shipped = "[update]\nsource_url = %s\ntrust_key = %s\n" % (self.p("store"), trust)
        write(ini, shipped)
        write(program, b"")
        with mock.patch.dict(os.environ), mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", program):
            os.environ.pop("DELTAPATCHER_CONFIG", None)
            os.environ.pop("DELTAPATCHER_PROJECT", None)
            self.assertEqual(config.path(), ini)
            cfg = config.load()
            self.assertEqual((cfg["source_url"], cfg["trust_key"]), (self.p("store"), trust))
            rc, ev = run_json(["update", self.p("player")])
            self.assertEqual(rc, 0, ev[-1])
            self.assertEqual(read(self.p("player", "game.pk3")), read(self.p("release", "game.pk3")))
            self.assertEqual(ev[-1]["data"]["publisher_key"], trust)
            self.assertEqual(projects.folder(), self.p("portable", "projects"))
            mod = projects.new("Mod")
            self.assertEqual(os.path.dirname(mod.path), self.p("portable", "projects"))
            self.assertTrue(projects.resolve("").main)
        self.assertEqual(read(ini).decode(), shipped)            # the shipped file is as it was


class Menu(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        write(self.p("game", "game.exe"), b"the program")
        publish.publish(self.p("game"), self.p("www"))

    def menu(self, typed, argv=()):
        out = io.StringIO()
        with mock.patch("builtins.input", side_effect=typed), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main(["--no-log"] + list(argv) + ["menu"])
        return rc, out.getvalue()

    def test_one_project_shows_no_project_choices(self):
        rc, text = self.menu(["7", "0"])
        self.assertEqual(rc, 0)
        self.assertNotIn("Choose another project", text)
        self.assertNotIn("Project:", text)
        self.assertIn("Type one of the numbers above.", text)

    def test_choose_a_project_and_update_it_then_all(self):
        mod = projects.new("Mod")
        rc, text = self.menu(["7", "2",                                      # choose Mod
                              "3", self.p("player"), self.p("www"), "",       # update a folder, in Mod
                              "6", "",                                        # update all projects
                              "0"])
        self.assertEqual(rc, 0)
        self.assertIn("Project: Default", text)
        self.assertIn("Project: Mod", text)
        self.assertEqual(read(self.p("player", "game.exe")), b"the program")
        own = config.file_values(mod.path)
        self.assertEqual((own["install_dir"], own["source_url"]), (self.p("player"), self.p("www")))
        self.assertEqual(config.load()["install_dir"], "")                    # Default's stayed empty
        self.assertIn("Updating 1 project", text)
        self.assertIn("  Mod  up to date", text)

    def test_the_menu_starts_on_the_project_given(self):
        projects.new("Mod")
        rc, text = self.menu(["0"], ["--project", "mod"])
        self.assertIn("Project: Mod", text)


if __name__ == "__main__":
    unittest.main()
