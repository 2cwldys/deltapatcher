"""Statistics for publishers (deltapatcher/stats.py): the release history publish keeps (and deploy never sends),
the releases section, the access log counted (nginx and Apache, Caddy; hostile, broken, huge and wrongly dated
lines; only the project's path; players never named; the releases players are on), the updater's User-Agent,
a collector failing alone, and the command's document. Counts only: no address or user agent comes out."""

import calendar
import contextlib
import io
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import __version__, cli, client, config, deploy, logs, projects, publish, serve, stats  # noqa: E402
from deltapatcher.source import user_agent  # noqa: E402

NOW = calendar.timegm((2026, 11, 24, 12, 0, 0))       # the clock of these tests: noon UTC
DAY = 86400
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
SHA = "ab" * 32
UA = "DeltaPatcher/0.3.0"


def run_json(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(sys, "stdin", io.StringIO("")):
        rc = cli.main(["--json", "--no-log"] + argv)
    return rc, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def clf(address, when, path, agent=UA, status=200, size=100, method="GET"):
    """A line of the Combined Log Format, as nginx and Apache write it."""
    y, mo, d, h, mi, s = __import__("time").gmtime(when)[:6]
    stamp = "%02d/%s/%04d:%02d:%02d:%02d +0000" % (d, MONTHS[mo - 1], y, h, mi, s)
    return '%s - - [%s] "%s %s HTTP/1.1" %d %d "-" "%s"\n' % (address, stamp, method, path, status, size, agent)


def caddy(address, when, uri, agent=UA, status=200, size=100, host="game.example.com", ts=None):
    """A line of Caddy's JSON access log."""
    request = {"remote_ip": address, "client_ip": address, "proto": "HTTP/2.0", "method": "GET", "host": host,
               "uri": uri, "headers": {"User-Agent": [agent]} if agent else {}}
    return json.dumps({"level": "info", "ts": when if ts is None else ts, "logger": "http.log.access.log0",
                       "msg": "handled request", "request": request, "bytes_read": 0, "duration": 0.001,
                       "size": size, "status": status}) + "\n"


class History(TempCase):
    def test_publish_adds_a_line_for_each_new_release(self):
        write(self.p("game", "a.pak"), b"a" * 1000)
        write(self.p("game", "b.pak"), b"b" * 2000)
        publish.publish(self.p("game"), self.p("store"), "Game", "1.0")
        publish.publish(self.p("game"), self.p("store"), "Game", "1.0")           # the same release: no new line
        write(self.p("game", "b.pak"), b"B" * 3000)
        write(self.p("game", "c.pak"), b"a" * 1000)                              # the same content as a.pak
        publish.publish(self.p("game"), self.p("store"), "Game", "1.1")
        lines = [json.loads(l) for l in read(self.p("store", publish.HISTORY_NAME)).decode().splitlines()]
        self.assertEqual([(l["version"], l["files"], l["bytes"], l["new_blobs"], l["download_bytes"]) for l in lines],
                         [("1.0", 2, 3000, 2, None), ("1.1", 3, 5000, 1, 3000)])
        self.assertEqual(sorted(lines[0]), ["bytes", "download_bytes", "files", "name", "new_blobs", "time", "version"])

    def test_deploy_and_the_server_never_give_out_the_history(self):
        write(self.p("game", "a.pak"), b"a" * 1000)
        publish.publish(self.p("game"), self.p("store"), "Game", "1.0")
        self.assertTrue(os.path.isfile(self.p("store", publish.HISTORY_NAME)))
        deploy.deploy(self.p("store"), deploy.FolderTarget(self.p("www")))
        self.assertTrue(os.path.isfile(self.p("www", "manifest.json")))
        self.assertFalse(os.path.exists(self.p("www", publish.HISTORY_NAME)))
        srv = serve.make_server(self.p("store"), "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen("http://127.0.0.1:%d/%s" % (srv.server_address[1], publish.HISTORY_NAME), timeout=10)
        self.assertEqual(e.exception.code, 404)


class Releases(TempCase):
    def test_the_current_release_what_it_changed_and_the_store(self):
        write(self.p("game", "a.pak"), b"a" * 1000)
        write(self.p("game", "b.pak"), b"b" * 2000)
        write(self.p("game", "old.txt"), b"going")
        config.save({"publish_src": self.p("game"), "name": "Game", "version": "1.0"})
        self.assertEqual(run_json(["publish"])[0], 0)
        write(self.p("game", "b.pak"), b"B" * 2500)
        write(self.p("game", "new.pak"), b"n" * 400)
        os.remove(self.p("game", "old.txt"))
        config.save({"version": "1.1"})
        self.assertEqual(run_json(["publish"])[0], 0)
        doc = stats.build(projects.main_project(), config.load(), 7)
        rel = doc["sections"]["releases"]
        self.assertEqual(rel["store"], config.default_store(self.p("game")))
        self.assertEqual({k: rel["current"][k] for k in ("name", "version", "files", "bytes", "blobs")},
                         {"name": "Game", "version": "1.1", "files": 3, "bytes": 3900, "blobs": 3})
        self.assertEqual({k: v for k, v in rel["change"].items() if k != "from"},
                         {"new": 1, "changed": 1, "removed": 1, "download_bytes": 2900})
        self.assertEqual(rel["change"]["from"]["version"], "1.0")
        self.assertGreater(rel["store_bytes"], 3900)
        self.assertEqual([h["version"] for h in rel["history"]], ["1.1", "1.0"])   # the newest first
        self.assertEqual(rel["history_total"], 2)
        with open(os.path.join(rel["store"], publish.HISTORY_NAME), "a") as f:
            f.write('not json\n{"version": 5}\n[1, 2]\n')                                # lines not publish's: passed over
        self.assertEqual(stats.build(projects.main_project(), config.load(), 7)["sections"]["releases"]["history_total"], 2)

    def test_nothing_released_yet_is_said(self):
        doc = stats.build(projects.main_project(), config.load(), 7)
        self.assertNotIn("releases", doc["sections"])
        self.assertIn("publish_src", doc["problems"]["releases"])
        config.save({"publish_src": self.p("game")})
        self.assertIn("release once", stats.build(projects.main_project(), config.load(), 7)["problems"]["releases"])


class AccessLog(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        self.log = self.p("logs", "access.log")
        config.save({"deploy_mode": "folder", "public_url": "https://game.example.com/game/", "access_log": self.log})
        patcher = mock.patch.object(stats, "now", return_value=NOW)
        patcher.start()
        self.addCleanup(patcher.stop)

    def downloads(self, days=3):
        doc = stats.build(projects.main_project(), config.load(), days)
        self.assertNotIn("downloads", doc["problems"])
        return doc["sections"]["downloads"]

    def test_checks_downloads_players_and_releases_per_day(self):
        blob = "/game/blobs/" + SHA
        write(self.log, "".join([
            clf("203.0.113.10", NOW - 2 * DAY, "/game/manifest.json", UA + " release/1.1"),
            clf("203.0.113.10", NOW - 2 * DAY + 60, blob, UA + " release/1.1", size=5000),
            clf("203.0.113.10", NOW - DAY, "/game/manifest.json", UA + " release/1.2"),   # updated: its last says 1.2
            clf("198.51.100.7", NOW - DAY, "/game/manifest.json", UA),                    # an older updater
            clf("198.51.100.7", NOW - DAY + 5, blob, UA, status=206, size=700),
            clf("192.0.2.33", NOW - 600, "/game/manifest.json", UA + " release/1.1", status=304, size=0),
            clf("192.0.2.33", NOW - 500, "/game/manifest.json", UA + " release/1.1"),
            clf("192.0.2.33", NOW - 400, "/game/sigs/%s.sig" % SHA, UA + " release/1.1", size=90),
        ]))
        dl = self.downloads()
        self.assertEqual([d["date"] for d in dl["days"]], ["2026-11-22", "2026-11-23", "2026-11-24"])
        self.assertEqual([(d["checks"], d["downloads"], d["bytes"], d["players"]) for d in dl["days"]],
                         [(1, 1, 5100, 1), (2, 1, 900, 2), (2, 0, 190, 1)])
        self.assertEqual(dl["total"], {"checks": 5, "downloads": 2, "bytes": 6190, "players": 3})
        self.assertEqual(dl["releases"], [{"release": "1.1", "players": 1}, {"release": "1.2", "players": 1},
                                          {"release": None, "players": 1}])
        self.assertEqual((dl["lines"], dl["unreadable"], dl["counted"], dl["path"], dl["read_from"]),
                         (8, 0, 8, "/game/", "this computer"))
        text = json.dumps(dl)
        for never in ("203.0.113.10", "198.51.100.7", "192.0.2.33", "DeltaPatcher/", "manifest.json HTTP"):
            self.assertNotIn(never, text)                                     # counts only: no address, no agent

    def test_only_the_projects_requests_count(self):
        write(self.log, "".join([
            clf("203.0.113.10", NOW - 60, "/other/manifest.json"),              # another site on the same server
            clf("203.0.113.10", NOW - 60, "/manifest.json"),
            clf("203.0.113.10", NOW - 60, "/game/readme.txt"),
            clf("203.0.113.10", NOW - 60, "/game/blobs/not-a-hash"),
            clf("203.0.113.10", NOW - 60, "/game/blobs/%s/x" % SHA),
            clf("203.0.113.10", NOW - 60, "/game/manifest.json", method="POST"),
            clf("203.0.113.10", NOW - 60, "/game/manifest.json", method="HEAD"),
            clf("203.0.113.10", NOW - 60, "/game/manifest.json", status=404),
            clf("203.0.113.10", NOW - 60, "/game/blobs/" + SHA, status=304),
            clf("203.0.113.10", NOW - 60, "/gamex/manifest.json"),
            clf("198.51.100.7", NOW - 60, "/game/manifest.json?x=1"),             # a query is not part of the path
            clf("198.51.100.7", NOW - 60, "https://game.example.com/game/blobs/" + SHA),
            clf("198.51.100.7", NOW - 60, "/game/%62lobs/" + SHA),               # percent-encoded: the same path
        ]))
        dl = self.downloads(1)
        self.assertEqual(dl["total"], {"checks": 1, "downloads": 2, "bytes": 300, "players": 1})
        self.assertEqual((dl["lines"], dl["unreadable"]), (13, 0))

    def test_hostile_broken_huge_and_wrongly_dated_lines(self):
        good = clf("203.0.113.10", NOW - 60, "/game/manifest.json", UA + " release/1.0")
        bad = [
            "garbage\n", "\n", "   \n",
            '203.0.113.10 - - [32/Nov/2026:10:00:00 +0000] "GET /game/manifest.json HTTP/1.1" 200 1 "-" "-"\n',
            '203.0.113.10 - - [24/Foo/2026:10:00:00 +0000] "GET /game/manifest.json HTTP/1.1" 200 1 "-" "-"\n',
            '203.0.113.10 - - [24/Nov/2026:25:00:00 +0000] "GET /game/manifest.json HTTP/1.1" 200 1 "-" "-"\n',
            '203.0.113.10 - - [29/Feb/2026:10:00:00 +0000] "GET /game/manifest.json HTTP/1.1" 200 1 "-" "-"\n',
            '203.0.113.10 - - [yesterday] "GET /game/manifest.json HTTP/1.1" 200 1 "-" "-"\n',
            '203.0.113.10 - - [24/Nov/2026:10:00:00 +0000] "GET" 200 1 "-" "-"\n',
            '203.0.113.10 - - [24/Nov/2026:10:00:00 +0000] "GET /game/manifest.json HTTP/1.1" abc 1 "-" "-"\n',
            '203.0.113.10 - - [24/Nov/2026:10:00:00 +0000] "GET relative/manifest.json HTTP/1.1" 200 1\n',
            '{"ts": "not a time", "request": {"remote_ip": "1", "method": "GET", "uri": "/game/manifest.json"}, "status": 200}\n',
            '{"ts": Infinity, "request": {"remote_ip": "1", "method": "GET", "uri": "/game/manifest.json"}, "status": 200}\n',
            '{"ts": 1795000000, "request": "nope"}\n', "[" * 5000 + "]" * 5000 + "\n", '{"unclosed": \n',
            "\x00\x01\x02 \"GET /game/manifest.json\"\n",
            '203.0.113.10 - - [24/Nov/2026:10:00:00 +0000] "GET /game/manifest.json HTTP/1.1\\" 200 1 "-" "' + '\\"' * 3000 + '\n',
        ]
        outside = [clf("203.0.113.10", NOW - 40 * DAY, "/game/manifest.json"),          # before the period
                   clf("203.0.113.10", NOW + 2 * DAY, "/game/manifest.json"),           # a date still to come
                   caddy("203.0.113.10", NOW - 60, "/game/manifest.json", ts=-5.0)]
        os.makedirs(os.path.dirname(self.log))
        with open(self.log, "wb") as f:
            f.write(good.encode())
            f.write(b"x" * (stats.MAX_LINE * 3) + b"\n")                                 # a huge line
            f.write(b"y" * (stats.MAX_LINE + 1) + b"\n")
            f.write("".join(bad).encode("utf-8") + b"\xff\xfe broken bytes\n")
            f.write("".join(outside).encode())
            f.write(good.encode())
            f.write(b"z" * (stats.MAX_LINE * 2))                                         # the last line, huge, no end
        dl = self.downloads(1)
        self.assertEqual(dl["total"]["checks"], 2)
        self.assertEqual(dl["releases"], [{"release": "1.0", "players": 1}])
        self.assertEqual(dl["unreadable"], 2 + len(bad) - 2 + 1 + 1)       # the blank lines are not lines at all
        self.assertEqual(dl["lines"], 2 + 2 + len(bad) - 2 + 1 + len(outside) + 1)

    def test_caddys_json_log(self):
        write(self.log, "".join([
            caddy("203.0.113.10", NOW - 100, "/game/manifest.json", UA + " release/2.0"),
            caddy("203.0.113.10", NOW - 90, "/game/blobs/" + SHA, UA + " release/2.0", size=1234),
            caddy("198.51.100.7", NOW - 80, "/game/manifest.json", None),                 # no user agent: unknown
            caddy("198.51.100.7", NOW - 70, "/game/manifest.json", UA, host="other.example.com"),  # another site
            caddy("192.0.2.33", NOW - 60, "/game/manifest.json", UA + " release/2.0", host="GAME.example.com:443",
                  ts="2026-11-24T11:59:00.5+00:00"),
        ]))
        dl = self.downloads(1)
        self.assertEqual(dl["total"], {"checks": 3, "downloads": 1, "bytes": 1534, "players": 3})
        self.assertEqual(dl["releases"], [{"release": "2.0", "players": 2}, {"release": None, "players": 1}])
        old = {"ts": NOW - 50, "request": {"remote_addr": "203.0.113.99:51234", "method": "GET",
                                           "uri": "/game/manifest.json", "headers": {}}, "status": 200, "size": 9}
        with open(self.log, "a") as f:
            f.write(json.dumps(old) + "\n" + json.dumps(dict(old, ts=NOW - 40, request=dict(old["request"],
                                                                     remote_addr="203.0.113.99:60000"))) + "\n")
        self.assertEqual(self.downloads(1)["total"]["players"], 4)        # the port of an address is not a new player

    def test_many_releases_and_a_compressed_log(self):
        write(self.log, "".join(clf("203.0.113.%d" % i, NOW - 60, "/game/manifest.json", UA + " release/1.%d" % (i % 25))
                                for i in range(50)))
        dl = self.downloads(1)
        self.assertEqual(len(dl["releases"]), stats.MAX_RELEASES)
        self.assertEqual(sum(r["players"] for r in dl["releases"]) + dl["releases_more"], 50)
        write(self.log, b"\x1f\x8b\x08\x00compressed")
        doc = stats.build(projects.main_project(), config.load(), 1)
        self.assertIn("compressed", doc["problems"]["downloads"])

    def test_what_is_missing_is_said(self):
        config.save({"access_log": ""})
        self.assertIn("access_log is not set", stats.build(projects.main_project(), config.load(), 1)["problems"]["downloads"])
        config.save({"access_log": self.p("nothing.log")})
        self.assertIn("there is no file", stats.build(projects.main_project(), config.load(), 1)["problems"]["downloads"])
        config.save({"access_log": self.log, "public_url": ""})
        write(self.log, "")
        self.assertIn("public_url is not set", stats.build(projects.main_project(), config.load(), 1)["problems"]["downloads"])
        config.save({"deploy_mode": "sftp", "public_url": "https://game.example.com/"})
        self.assertIn("ssh_host", stats.build(projects.main_project(), config.load(), 1)["problems"]["downloads"])
        config.save({"ssh_host": "game.example.com", "ssh_user": "deploy", "access_log": "/var/log/nginx/*.log"})
        self.assertIn("cannot be used", stats.build(projects.main_project(), config.load(), 1)["problems"]["downloads"])
        config.save({"access_log": "/var/log/nginx/access.log"})
        self.assertIn("not trusted yet", stats.build(projects.main_project(), config.load(), 1)["problems"]["downloads"])

    def test_the_command_never_logs_an_address(self):
        write(self.log, clf("203.0.113.10", NOW - 60, "/game/manifest.json", UA + " release/1.0"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(sys, "stdin", io.StringIO("")), \
                mock.patch.dict(os.environ, {"DELTAPATCHER_LOG": self.p("deltapatcher.log")}):
            self.assertEqual(cli.main(["stats", "--days", "1"]), 0)
            logs.close()
            logged = read(logs.default_path()).decode("utf-8", "replace")
        self.assertIn("== deltapatcher stats --days 1", logged)
        self.assertIn("1 update check, 0 downloads (100 bytes), 1 player", out.getvalue())
        self.assertIn("players are on: 1.0 (1)", out.getvalue())
        for text in (out.getvalue(), logged):
            self.assertNotIn("203.0.113.10", text)
            self.assertNotIn("DeltaPatcher/0.3.0 release", text)


class Collectors(TempCase):
    def test_one_failing_never_stops_the_others(self):
        before = list(stats.COLLECTORS)
        self.addCleanup(lambda: stats.COLLECTORS.__setitem__(slice(None), before))

        def broken(project, cfg, days):
            raise RuntimeError("it broke")

        def unavailable(project, cfg, days):
            raise stats.StatsUnavailable("not here: set something")
        stats.register("broken", broken)
        stats.register("later", unavailable)
        stats.register("works", lambda project, cfg, days: {"days_asked": days})
        doc = stats.build(projects.main_project(), config.load(), 5)
        self.assertEqual(doc["sections"], {"works": {"days_asked": 5}})
        self.assertEqual(doc["problems"]["broken"], "broken failed: RuntimeError: it broke")
        self.assertEqual(doc["problems"]["later"], "not here: set something")
        self.assertEqual(list(doc["problems"])[:2], ["releases", "downloads"])        # in the order they ran
        for bad in ("Bad Name", "", "x" * 40, "releases"):
            with self.assertRaises(ValueError, msg=bad):
                stats.register(bad, broken)


class Command(TempCase):
    def test_the_documents_shape(self):
        config.save({"deploy_mode": "folder", "public_url": "https://game.example.com/", "access_log": self.p("a.log")})
        write(self.p("a.log"), clf("203.0.113.10", calendar.timegm(__import__("time").gmtime()), "/manifest.json"))
        rc, ev = run_json(["stats", "--days", "7"])
        self.assertEqual(rc, 0, ev[-1])
        doc = ev[-1]["data"]
        self.assertEqual(sorted(doc), ["days", "format", "made", "problems", "project", "sections", "version"])
        self.assertEqual((doc["format"], doc["version"], doc["days"], doc["project"]["project"]),
                         ("deltapatcher-stats", 1, 7, "Default"))
        self.assertRegex(doc["made"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(len(doc["sections"]["downloads"]["days"]), 7)
        self.assertEqual(sorted(doc["sections"]["downloads"]), ["counted", "days", "lines", "log", "path", "read_from",
                                                                "releases", "releases_more", "total", "unreadable"])
        self.assertIn("releases", doc["problems"])
        mod = projects.new("Mod")
        rc, ev = run_json(["--project", "Mod", "stats"])
        self.assertEqual((ev[-1]["data"]["project"]["file"], ev[-1]["data"]["days"]), (mod.path, 30))
        for days in ("0", "367", "x"):
            with contextlib.redirect_stderr(io.StringIO()):
                try:
                    rc, ev = run_json(["stats", "--days", days])
                except SystemExit:
                    continue
            self.assertEqual((rc, ev[-1]["code"]), (1, "bad-arguments"))


class UserAgent(TempCase):
    def test_the_updater_says_which_release_it_has_and_nothing_more(self):
        self.assertEqual(user_agent(""), "DeltaPatcher/" + __version__)
        self.assertEqual(user_agent("1.2"), "DeltaPatcher/%s release/1.2" % __version__)
        self.assertEqual(user_agent("1.0 beta\r\nX-Evil: 1"), "DeltaPatcher/%s release/1.0_beta_X-Evil_1" % __version__)
        self.assertEqual(user_agent("x" * 100), "DeltaPatcher/%s release/%s" % (__version__, "x" * 40))
        self.assertEqual(stats.release_in(user_agent("2026.11-rc1")), "2026.11-rc1")
        write(self.p("game", "a.pak"), b"a" * 500)
        publish.publish(self.p("game"), self.p("store"), "Game", "1.0")
        agents = []
        base = serve.make_handler(self.p("store"))

        class Recording(base):
            def do_GET(self):
                agents.append((self.path, self.headers.get("User-Agent")))
                base.do_GET(self)
        srv = serve.Server(("127.0.0.1", 0), Recording)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        url = "http://127.0.0.1:%d/" % srv.server_address[1]
        client.update(url, self.p("player"))
        self.assertEqual({a for _p, a in agents}, {"DeltaPatcher/" + __version__})       # nothing installed yet
        del agents[:]
        client.update(url, self.p("player"), check_only=True)
        self.assertIn("/manifest.json", [p for p, _a in agents])
        self.assertEqual({a for _p, a in agents}, {"DeltaPatcher/%s release/1.0" % __version__})
        del agents[:]
        client.update(url, self.p("player"), check_only=True, headers={"User-Agent": "MyLauncher/2"})
        self.assertEqual({a for _p, a in agents}, {"MyLauncher/2"})                     # the caller's own is kept


if __name__ == "__main__":
    unittest.main()
