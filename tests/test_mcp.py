"""The MCP server (deltapatcher/mcp_server.py), spoken to over its real standard input and output."""

import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import __version__, mcp_server  # noqa: E402


class Client(object):
    """A minimal MCP client: the server as a program of its own, one JSON-RPC message a line."""

    def __init__(self, case, command=("-m", "deltapatcher.mcp_server")):
        self.p = subprocess.Popen([sys.executable] + list(command), cwd=ROOT, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ))
        self.inbox = queue.Queue()
        self.notes = []
        self.seq = 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self.p.stderr.read, daemon=True).start()
        case.addCleanup(self.close)

    def _read(self):
        for line in self.p.stdout:
            self.inbox.put(json.loads(line))
        self.inbox.put(None)

    def send(self, obj):
        self.p.stdin.write((json.dumps(obj) + "\n").encode())
        self.p.stdin.flush()

    def raw(self, text):
        self.p.stdin.write(text.encode() + b"\n")
        self.p.stdin.flush()

    def wait_for(self, rid, timeout=120):
        while True:
            msg = self.inbox.get(timeout=timeout)
            if msg is None:
                raise AssertionError("the server closed its output")
            if msg.get("id") == rid and "method" not in msg:
                return msg
            self.notes.append(msg)

    def request(self, method, params=None, timeout=120):
        self.seq += 1
        self.send({"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params or {}})
        return self.wait_for(self.seq, timeout)

    def start(self):
        r = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                        "clientInfo": {"name": "test", "version": "1"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return r["result"]

    def tool(self, _tool, _timeout=120, **arguments):
        r = self.request("tools/call", {"name": _tool, "arguments": arguments}, _timeout)
        self.last = r
        return r["result"]

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            self.p.wait(30)
        except subprocess.TimeoutExpired:
            self.p.kill()
        self.p.stdout.close()
        self.p.stderr.close()


def tree(root):
    out = {}
    for top, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".deltapatcher"]
        for name in files:
            path = os.path.join(top, name)
            out[os.path.relpath(path, root).replace(os.sep, "/")] = read(path)
    return out


class Mcp(TempCase):
    def test_handshake_and_what_it_offers(self):
        c = Client(self)
        info = c.start()
        self.assertEqual(info["protocolVersion"], "2025-06-18")
        self.assertEqual(info["serverInfo"]["version"], __version__)
        self.assertIn("tools", info["capabilities"])
        self.assertIn("passphrase", info["instructions"])                  # it says why it never asks for one
        old = c.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        self.assertEqual(old["result"]["protocolVersion"], "2024-11-05")     # an older client keeps its version
        odd = c.request("initialize", {"protocolVersion": "1999-01-01", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
        self.assertEqual(odd["result"]["protocolVersion"], mcp_server.PROTOCOLS[0])

        tools = c.request("tools/list")["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertEqual(len(names), len(set(names)))
        for t in tools:
            self.assertEqual(t["inputSchema"]["type"], "object", t["name"])
            self.assertTrue(t["description"], t["name"])
            self.assertIn("readOnlyHint", t["annotations"], t["name"])
            props = " ".join(t["inputSchema"].get("properties", {}))
            self.assertNotRegex(props, "passphrase|password|secret", t["name"])   # no secret ever passes through
        by = {t["name"]: t for t in tools}
        self.assertTrue(by["check"]["annotations"]["readOnlyHint"])
        self.assertTrue(by["update"]["annotations"]["destructiveHint"])          # a client asks before these
        self.assertTrue(by["release"]["annotations"]["destructiveHint"])
        self.assertNotIn("self_update", names)

        uris = [r["uri"] for r in c.request("resources/list")["result"]["resources"]]
        self.assertIn("deltapatcher://guide/quickstart", uris)
        self.assertIn("deltapatcher://log", uris)
        text = c.request("resources/read", {"uri": "deltapatcher://guide/security"})["result"]["contents"][0]["text"]
        self.assertIn("Who answers for what is installed", text)
        self.assertEqual(c.request("resources/read", {"uri": "deltapatcher://nothing"})["error"]["code"], -32002)
        self.assertEqual(c.request("ping")["result"], {})

    def test_the_command_line_starts_it(self):
        for command in (("-m", "deltapatcher", "mcp"), (os.path.join(ROOT, "mcp", "server.py"),)):
            c = Client(self, command)
            self.assertEqual(c.start()["serverInfo"]["name"], "deltapatcher", command)
            self.assertEqual(c.request("ping")["result"], {})
            self.assertFalse(c.tool("version")["isError"])

    def test_answers_to_mistakes(self):
        c = Client(self)
        c.start()
        self.assertEqual(c.request("no/such/method")["error"]["code"], -32601)
        self.assertEqual(c.request("tools/call", {"name": "nope", "arguments": {}})["error"]["code"], -32602)
        bad = c.request("tools/call", {"name": "check", "arguments": {"jobs": "three"}})
        self.assertIn("jobs must be an integer", bad["error"]["message"])
        unknown = c.request("tools/call", {"name": "version", "arguments": {"colour": "red"}})
        self.assertIn("unknown argument", unknown["error"]["message"])
        c.raw("this is not json")
        self.assertEqual(c.inbox.get(timeout=30)["error"]["code"], -32700)
        c.send({"jsonrpc": "2.0", "method": "notifications/whatever"})        # a notification gets no answer
        self.assertEqual(c.request("ping")["result"], {})
        problem = c.tool("publish", store=self.p("store"))                       # a store needs its folder
        self.assertTrue(problem["isError"])
        self.assertIn("give the folder too", problem["content"][0]["text"])

    def test_a_release_through_the_tools(self):
        write(self.p("src", "game.pk3"), os.urandom(40000))
        write(self.p("src", "maps", "a.map"), b"map a\n" * 100)
        c = Client(self)
        c.start()
        r = c.tool("publish", folder=self.p("src"), store=self.p("store"), name="Game", version="1", htaccess=False)
        self.assertFalse(r["isError"], r["content"][0]["text"])
        self.assertEqual(r["structuredContent"]["files"], 2)

        r = c.tool("check", folder=self.p("player"), source=self.p("store"))          # a folder works as a source
        self.assertFalse(r["isError"], r["content"][0]["text"])
        self.assertEqual(r["structuredContent"]["add"], 2)
        self.assertFalse(os.path.exists(self.p("player", "game.pk3")))                  # a check changes nothing

        served = c.tool("serve_start", store=self.p("store"))
        self.assertFalse(served["isError"], served["content"][0]["text"])
        url = served["structuredContent"]["url"]
        self.assertTrue(url.startswith("http://127.0.0.1:"))
        r = c.tool("update", folder=self.p("player"), source=url)
        self.assertFalse(r["isError"], r["content"][0]["text"])
        self.assertEqual(tree(self.p("player")), tree(self.p("src")))

        write(self.p("src", "game.pk3"), os.urandom(40000))
        self.assertFalse(c.tool("publish", folder=self.p("src"), store=self.p("store"), version="2")["isError"])
        r = c.tool("update", folder=self.p("player"), source=url)
        self.assertEqual(r["structuredContent"]["download_blobs"], 1)                  # only the change travels
        self.assertFalse(c.tool("rollback", folder=self.p("player"))["isError"])
        self.assertNotEqual(tree(self.p("player")), tree(self.p("src")))                # back to release 1

        stopped = c.tool("serve_stop")
        self.assertEqual(stopped["structuredContent"]["stopped"], [url])
        self.assertTrue(c.tool("serve_stop", url=url)["isError"])                       # it is gone

    def test_selftest(self):
        c = Client(self)
        c.start()
        r = c.tool("selftest", _timeout=300)
        self.assertFalse(r["isError"], r["content"][0]["text"])
        self.assertTrue(r["structuredContent"]["passed"])
        self.assertEqual(len(r["structuredContent"]["steps"]), 7)

    def test_guides_and_prompts(self):
        c = Client(self)
        c.start()
        r = c.tool("guide", name="vps")
        self.assertIn("No domain name", r["content"][0]["text"])
        self.assertIn("/wiki/Setting-up-a-VPS", r["content"][0]["text"])
        names = [p["name"] for p in c.request("prompts/list")["result"]["prompts"]]
        self.assertIn("release_new_version", names)
        got = c.request("prompts/get", {"name": "release_new_version", "arguments": {"version": "2.1"}})["result"]
        self.assertIn("version 2.1", got["messages"][0]["content"]["text"])
        self.assertIn("dry_run", got["messages"][0]["content"]["text"])
        self.assertEqual(c.request("prompts/get", {"name": "release_new_version", "arguments": {}})["error"]["code"], -32602)

    def test_project_files_export_and_import(self):
        c = Client(self)
        c.start()
        self.assertFalse(c.tool("project_new", name="Mod")["isError"])
        r = c.tool("set_settings", project="Mod", values={"public_url": "https://mod.example.com/",
                                                          "description": "A mod", "install_dir": self.p("mine"),
                                                          "install_hint": "Pick the game's folder.",
                                                          "install_folder": "Mods"})
        self.assertFalse(r["isError"], r["content"][0]["text"])
        r = c.tool("project_export", projects=["Mod"], out=self.p("mod.deltapatch"))
        self.assertFalse(r["isError"], r["content"][0]["text"])
        self.assertEqual((r["structuredContent"]["signed"], r["structuredContent"]["projects"]), (False, ["Mod"]))
        text = read(self.p("mod.deltapatch")).decode()
        self.assertIn("https://mod.example.com/", text)
        self.assertNotIn(self.tmp.replace("\\", "\\\\"), text)                         # never a folder of this computer
        self.assertFalse(c.tool("project_remove", project="Mod")["isError"])

        r = c.tool("project_import", file=self.p("mod.deltapatch"), dry_run=True)
        self.assertFalse(r["isError"], r["content"][0]["text"])
        found = r["structuredContent"]["projects"][0]
        self.assertEqual((found["title"], found["install_hint"], found["install_folder"]),
                         ("Mod", "Pick the game's folder.", "Mods"))
        self.assertIn("not signed", r["content"][0]["text"])
        r = c.tool("project_import", file=self.p("mod.deltapatch"), folder=self.p("player"))
        self.assertFalse(r["isError"], r["content"][0]["text"])
        r = c.tool("get_settings", project="Mod")
        settings = r["structuredContent"]["settings"]
        self.assertEqual((settings["source_url"], settings["description"], settings["install_dir"],
                          settings["install_hint"], settings["install_folder"]),
                         ("https://mod.example.com/", "A mod", self.p("player", "Mods"), "Pick the game's folder.", "Mods"))
        write(self.p("broken.html"), b"<html>no projects</html>")
        self.assertTrue(c.tool("project_import", file=self.p("broken.html"))["isError"])

    def test_no_tool_removes_every_project_or_resets(self):
        c = Client(self)
        c.start()
        self.assertFalse(c.tool("project_new", name="Mod")["isError"])
        for value in ("--all", " --all", "-x"):
            r = c.tool("project_remove", project=value)
            self.assertTrue(r["isError"], value)
        self.assertTrue(os.path.isfile(os.path.join(os.path.dirname(os.environ["DELTAPATCHER_CONFIG"]), "projects",
                                                    "Mod.ini")))
        names = [t["name"] for t in c.request("tools/list")["result"]["tools"]]
        self.assertFalse([n for n in names if "reset" in n or "remove_all" in n])
        export = next(t for t in c.request("tools/list")["result"]["tools"] if t["name"] == "project_export")
        self.assertNotIn("sign", export["inputSchema"]["properties"])                 # signing needs a passphrase

    def test_stats_reads_and_never_names_a_player(self):
        c = Client(self)
        c.start()
        tool = next(t for t in c.request("tools/list")["result"]["tools"] if t["name"] == "stats")
        self.assertEqual((tool["annotations"]["readOnlyHint"], tool["annotations"]["destructiveHint"]), (True, False))
        write(self.p("game", "a.pak"), b"a" * 100)
        r = c.tool("set_settings", values={"publish_src": self.p("game"), "version": "1.0", "deploy_mode": "folder",
                                           "public_url": "https://game.example.com/", "access_log": self.p("access.log")})
        self.assertFalse(r["isError"], r["content"][0]["text"])
        self.assertFalse(c.tool("publish")["isError"])
        stamp = time.strftime("%d/{}/%Y:%H:%M:%S +0000", time.gmtime()).format(
            "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()[time.gmtime().tm_mon - 1])
        write(self.p("access.log"), '203.0.113.10 - - [%s] "GET /manifest.json HTTP/1.1" 200 100 "-" '
                                    '"DeltaPatcher/0.3.0 release/1.0"\n' % stamp)
        r = c.tool("stats", days=7)
        self.assertFalse(r["isError"], r["content"][0]["text"])
        doc = r["structuredContent"]
        self.assertEqual((doc["format"], doc["version"], doc["days"]), ("deltapatcher-stats", 1, 7))
        self.assertEqual(doc["sections"]["releases"]["current"]["version"], "1.0")
        self.assertEqual(doc["sections"]["downloads"]["total"]["checks"], 1)
        self.assertEqual(doc["sections"]["downloads"]["releases"], [{"release": "1.0", "players": 1}])
        self.assertNotIn("203.0.113.10", json.dumps(r))
        self.assertIn("error", c.request("tools/call", {"name": "stats", "arguments": {"days": 0}}))   # out of range

    def test_backups_now_list_and_restore(self):
        c = Client(self)
        c.start()
        by = {t["name"]: t for t in c.request("tools/list")["result"]["tools"]}
        self.assertTrue(by["backup_list"]["annotations"]["readOnlyHint"])
        self.assertEqual((by["backup_now"]["annotations"]["readOnlyHint"], by["backup_now"]["annotations"]["destructiveHint"]),
                         (False, True))                                        # the limits it applies can remove backups
        self.assertTrue(by["backup_restore"]["annotations"]["destructiveHint"])  # a client asks before it
        self.assertFalse([n for n in by if n.startswith("backup_") and n not in ("backup_now", "backup_list",
                                                                                 "backup_restore")])
        write(self.p("game", "a.pak"), b"a" * 100)
        r = c.tool("set_settings", values={"publish_src": self.p("game"), "version": "1.0", "deploy_mode": "folder",
                                           "deploy_folder": self.p("www")})
        self.assertFalse(r["isError"], r["content"][0]["text"])
        self.assertFalse(c.tool("release")["isError"])
        r = c.tool("backup_now")
        self.assertFalse(r["isError"], r["content"][0]["text"])
        self.assertTrue(r["structuredContent"]["made"])
        listed = c.tool("backup_list")["structuredContent"]["backups"]
        self.assertEqual([b["release"]["version"] for b in listed], ["1.0"])
        r = c.tool("backup_restore", name=listed[0]["name"], dry_run=True)
        self.assertFalse(r["isError"], r["content"][0]["text"])
        self.assertTrue(r["structuredContent"]["dry_run"])
        self.assertFalse(c.tool("backup_restore", name=listed[0]["name"])["isError"])
        self.assertTrue(c.tool("backup_restore", name="--all")["isError"])

    def test_a_cancelled_request_gets_no_answer(self):
        silent = socket.socket()                                       # accepts, never answers: a check would hang
        silent.bind(("127.0.0.1", 0))
        silent.listen(5)
        self.addCleanup(silent.close)
        c = Client(self)
        c.start()
        c.seq += 1
        hung = c.seq
        c.send({"jsonrpc": "2.0", "id": hung, "method": "tools/call",
                "params": {"name": "check", "arguments": {"folder": self.p("player"),
                                                          "source": "http://127.0.0.1:%d/" % silent.getsockname()[1]}}})
        c.send({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": hung, "reason": "test"}})
        self.assertEqual(c.request("ping")["result"], {})                 # the server goes on
        self.assertEqual(c.request("tools/call", {"name": "version", "arguments": {}})["result"]["isError"], False)
        self.assertFalse(any(n.get("id") == hung for n in c.notes))


if __name__ == "__main__":
    unittest.main()
