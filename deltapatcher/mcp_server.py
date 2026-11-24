"""DeltaPatcher as an MCP server (the Model Context Protocol), so AI assistants such as Claude Code, Claude
Desktop or VS Code can work with it: check and update projects, publish and release them, look at settings
and keys, read the guides and the log, and run a self-test of the whole cycle.

    deltapatcher mcp                       (from a clone: python -m deltapatcher.mcp_server)

MCP over standard input and output: one JSON-RPC message a line, with Python's standard library only. Every
tool runs the engine as a program of its own with --json, as the DeltaPatcher app does, so a tool that fails
or is cancelled never takes the server down, and the engine's output never mixes with the protocol.

It never handles a passphrase or a password (signing, unlocking an SSH key, putting a key on a server) and
never replaces DeltaPatcher itself: those wait for a person at the window or the command line, because a
secret must not pass through an AI assistant. Tools that change files or servers say so in their
annotations, so a client asks before it runs them.
"""

import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import APP_NAME, REPO, __version__

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")
WIKI = "https://github.com/%s/wiki" % REPO
TEXT_LIMIT = 40000
NO_WINDOW = 0x08000000                  # CREATE_NO_WINDOW: an engine run opens no console window on Windows

# (name, the file in the repository, its wiki page, its title, what it covers)
GUIDES = [
    ("index", "docs/README.md", "Home", "Documentation", "Where to start, the idea in one paragraph, the window"),
    ("quickstart", "docs/QUICKSTART.md", "Quick-start", "Quick start", "Try it on one computer in five minutes"),
    ("players", "docs/PLAYERS.md", "For-players", "For players", "Keeping a game folder up to date"),
    ("publishing", "docs/PUBLISHING.md", "Publishing-a-release", "Publishing a release",
     "Releasing, protecting player files, signing, giving players the updater"),
    ("vps", "docs/VPS-SETUP.md", "Setting-up-a-VPS", "Setting up a VPS",
     "A server: deploy user, SSH key, web server, with or without a domain name"),
    ("cli", "docs/CLI.md", "Command-line", "Command line", "Every command and option, exit codes, settings, logs"),
    ("docker", "docker/README.md", "Docker-and-Pterodactyl", "Docker and Pterodactyl",
     "Keeping the files inside containers up to date"),
    ("mcp", "mcp/README.md", "MCP-server", "MCP server",
     "This server: connecting an assistant, the tools, what it never does"),
    ("format", "docs/FORMAT.md", "Store-and-manifest-format", "The store and manifest",
     "The store, the manifest, signatures, the update algorithm"),
    ("troubleshooting", "docs/TROUBLESHOOTING.md", "Troubleshooting", "Troubleshooting",
     "Every error message and what to do"),
    ("security", "SECURITY.md", "Security", "Security", "What DeltaPatcher protects, and who answers for what is installed"),
]
GUIDE = {g[0]: g for g in GUIDES}

INSTRUCTIONS = (
    "DeltaPatcher keeps folders up to date from releases (players) and turns folders into releases that only "
    "send what changed (publishers). Its settings are projects: list_projects shows them, and most tools take "
    "project=NAME (left out: the Default project). Before update, release or deploy, run check, or the tool "
    "with dry_run, and tell the person what would change. Never ask anyone for a passphrase or a password: "
    "signing a release, unlocking an SSH key and putting a key on a server are done in the DeltaPatcher app or "
    "on its command line. The guide tool reads the documentation; selftest checks the whole cycle on this "
    "computer without touching any project.")


class McpError(Exception):
    def __init__(self, code, message):
        Exception.__init__(self, message)
        self.code = code
        self.message = message


class ToolProblem(Exception):
    """A tool that cannot run as asked: its message goes back to the assistant as the tool's result."""


# ---------------------------------------------------------------------------------------------------------
# the engine, run as a program of its own


def engine_command():
    """The engine as a program: this program itself when it is the built command line, else python -m."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "deltapatcher"]


def engine_env(extra=None, drop=()):
    env = dict(os.environ)
    if not getattr(sys, "frozen", False):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env["PYTHONPATH"] = os.pathsep.join([root] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    env["PYTHONIOENCODING"] = "utf-8"
    for key in drop:
        env.pop(key, None)
    env.update(extra or {})
    return env


def start_engine(args, extra_env=None, drop_env=()):
    """An engine run. Its standard input stays open: in --json mode a line "cancel" stops a download and
    leaves the folder as it was, and the end of the input stops a local server."""
    kw = {"creationflags": NO_WINDOW} if os.name == "nt" else {}
    return subprocess.Popen(engine_command() + ["--json"] + list(args), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=engine_env(extra_env, drop_env), **kw)


def close_input(p):
    try:
        p.stdin.close()
    except (OSError, ValueError):
        pass


def stop_engine(p, gently, wait=10):
    """Ask an engine run to stop (a "cancel" line, or the end of its input), and end it if it has not
    stopped after `wait` seconds."""
    if p.poll() is not None:
        return
    try:
        if gently == "cancel":
            p.stdin.write(b"cancel\n")
            p.stdin.flush()
        else:
            p.stdin.close()
    except (OSError, ValueError):
        pass

    def finish():
        try:
            p.wait(wait)
        except subprocess.TimeoutExpired:
            p.terminate()
    threading.Thread(target=finish, daemon=True).start()


def read_event(raw):
    line = raw.decode("utf-8", "replace").strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except ValueError:
        return {"event": "log", "text": line}
    return event if isinstance(event, dict) else None


class Outcome(object):
    """One engine run: its events, and whether it ended in a result or an error."""

    def __init__(self, rc, events, stderr):
        self.rc = rc
        self.events = events
        last = None
        for e in reversed(events):
            if e.get("event") in ("result", "error"):
                last = e
                break
        self.ok = bool(last) and last.get("event") == "result"
        self.data = (last or {}).get("data") or {}
        if self.ok:
            self.message, self.code = "", ""
        elif last:
            self.message, self.code = last.get("message") or "it failed", last.get("code") or ""
        else:
            tail = [l for l in stderr.strip().splitlines() if l.strip()][-3:]
            self.message = "the engine stopped without an answer (exit %s)%s" % (rc, (": " + " / ".join(tail)) if tail else "")
            self.code = "no-answer"

    def lines(self):
        out = []
        for e in self.events:
            tag = "[%s] " % e["project"] if e.get("project") else ""
            if e.get("event") == "log" and e.get("text"):
                out.append(tag + e["text"])
            elif e.get("event") == "project-end":
                out.append(tag + ("done" if e.get("ok") else "FAILED: %s" % e.get("message", "")))
        return out


class Engine(object):
    """The engine runs a server has going, so a cancelled request can stop its own."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = {}

    def run(self, args, key=None, progress=None, extra_env=None, drop_env=()):
        p = start_engine(args, extra_env, drop_env)
        if key is not None:
            with self.lock:
                self.running[key] = p
        err = []
        reader = threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True)
        reader.start()
        events = []
        try:
            for raw in p.stdout:
                event = read_event(raw)
                if event is None:
                    continue
                events.append(event)
                if progress and event.get("event") == "progress":
                    progress(event)
            p.wait()
        finally:
            close_input(p)
            if key is not None:
                with self.lock:
                    self.running.pop(key, None)
        reader.join(5)
        return Outcome(p.returncode, events, (err[0] if err else b"").decode("utf-8", "replace"))

    def stop(self, key):
        with self.lock:
            p = self.running.get(key)
        if p is not None:
            stop_engine(p, "cancel")

    def stop_all(self):
        with self.lock:
            procs = list(self.running.values())
        for p in procs:
            stop_engine(p, "cancel", wait=3)


# ---------------------------------------------------------------------------------------------------------
# what a tool hands back


def text_result(text, data=None, error=False):
    if len(text) > TEXT_LIMIT:
        text = text[:TEXT_LIMIT] + "\n... (cut: %d more characters)" % (len(text) - TEXT_LIMIT)
    res = {"content": [{"type": "text", "text": text}], "isError": bool(error)}
    if data is not None:
        res["structuredContent"] = data if isinstance(data, dict) else {"value": data}
    return res


def engine_result(outcome):
    lines = outcome.lines()
    if outcome.ok:
        body = "\n".join(lines)
        text = (body + "\n\n" if body else "") + "Result: " + json.dumps(outcome.data, indent=1, sort_keys=True)
        return text_result(text, outcome.data)
    text = "Failed: %s%s" % (outcome.message, " (%s)" % outcome.code if outcome.code else "")
    if lines:
        text += "\n\n" + "\n".join(lines)
    return text_result(text, {"ok": False, "message": outcome.message, "code": outcome.code}, error=True)


# ---------------------------------------------------------------------------------------------------------
# arguments


def path_of(value):
    return os.path.abspath(os.path.expanduser(value))


def source_of(value):
    return value if "://" in value else path_of(value)


def opt(argv, flag, value, convert=str):
    if value is not None and value != "":
        argv += [flag, convert(value)]


def project_first(a, argv):
    """--project goes before the command: the engine takes it there for every command."""
    return (["--project", a["project"]] if a.get("project") else []) + argv


def ssh_options(argv, a, root=True):
    opt(argv, "--host", a.get("host"))
    opt(argv, "--user", a.get("user"))
    opt(argv, "--port", a.get("port"))
    opt(argv, "--key", a.get("key"), path_of)
    if root:
        opt(argv, "--remote-root", a.get("remote_root"))


def release_options(argv, a):
    opt(argv, "--name", a.get("name"))
    opt(argv, "--version", a.get("version"))
    for p in a.get("protect") or []:
        argv += ["--protect", p]
    for p in a.get("leave_out") or []:
        argv += ["--leave-out", p]
    if a.get("htaccess") is False:
        argv.append("--no-htaccess")
    if a.get("prune") is False:
        argv.append("--no-prune")


def check_args(schema, args):
    """A short reason the arguments do not fit the tool's schema, or None."""
    if not isinstance(args, dict):
        return "arguments must be an object"
    props = schema.get("properties", {})
    for key in args:
        if key not in props:
            return "unknown argument %r (known: %s)" % (key, ", ".join(sorted(props)) or "none")
    for key in schema.get("required", []):
        if args.get(key) in (None, ""):
            return "%s is needed" % key
    for key, value in args.items():
        want = props[key].get("type")
        if value is None:
            continue
        ok = {"string": lambda v: isinstance(v, str),
              "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
              "boolean": lambda v: isinstance(v, bool),
              "array": lambda v: isinstance(v, list) and all(isinstance(i, str) for i in v),
              "object": lambda v: isinstance(v, dict)}.get(want, lambda v: True)(value)
        if not ok:
            return "%s must be %s" % (key, {"array": "a list of strings"}.get(want, "a" + ("n " if want[0] in "aeiou" else " ") + want))
        if "enum" in props[key] and value not in props[key]["enum"]:
            return "%s must be one of %s" % (key, ", ".join(props[key]["enum"]))
        if want == "integer":
            lo, hi = props[key].get("minimum"), props[key].get("maximum")
            if (lo is not None and value < lo) or (hi is not None and value > hi):
                return "%s must be between %s and %s" % (key, lo, hi)
    return None


def s_str(text, **more):
    return dict({"type": "string", "description": text}, **more)


def s_int(text, lo=None, hi=None):
    d = {"type": "integer", "description": text}
    if lo is not None:
        d["minimum"] = lo
    if hi is not None:
        d["maximum"] = hi
    return d


def s_bool(text):
    return {"type": "boolean", "description": text}


def s_list(text):
    return {"type": "array", "items": {"type": "string"}, "description": text}


def schema(required=(), **props):
    d = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        d["required"] = list(required)
    return d


PROJECT = s_str("The project: its name or its settings file. Left out: the Default project.")
FOLDER = s_str("The folder to keep up to date (left out: the project's saved one). Absolute paths are safest.")
SOURCE = s_str("Where the release is: an http(s) address or a folder (left out: the project's saved one).")
HOST = s_str("The server's address (left out: the project's saved one).")
SSH = dict(host=HOST, user=s_str("The user deploys log in as (left out: the saved one)."),
           port=s_int("The server's SSH port (left out: the saved one, else 22).", 1, 65535),
           key=s_str("The private key's file (left out: the saved one). The key itself is never read here."))
RELEASE = dict(name=s_str("The release's name, shown to players (left out: the saved one)."),
               version=s_str("The release's version, shown to players (left out: the saved one). The same version "
                             "again is fine: only changed files travel."),
               protect=s_list("Files or folders players keep once they have them (their settings): paths inside "
                              "the folder, or patterns like *.ini. Left out: the saved list."),
               leave_out=s_list("Files or folders left out of the release: paths inside the folder, or patterns "
                                "like *.log. Left out: the saved list."),
               htaccess=s_bool("Write the .htaccess for Apache (default yes)."))

RO = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
# reads the destination and writes only on this computer, where the limits it applies can remove old backups
BACKS_UP = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": True}
RO_NET = dict(RO, openWorldHint=True)
WRITES = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
CHANGES = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False, "openWorldHint": False}
CHANGES_NET = dict(CHANGES, openWorldHint=True)


class Tool(object):
    def __init__(self, name, title, description, input_schema, annotations, build=None, run=None):
        self.name, self.title, self.description = name, title, description
        self.schema, self.annotations, self.build, self.run_native = input_schema, annotations, build, run

    def listing(self):
        return {"name": self.name, "title": self.title, "description": self.description,
                "inputSchema": self.schema, "annotations": dict(self.annotations, title=self.title)}

    def run(self, server, args, key, progress):
        if self.run_native:
            return self.run_native(server, args, key, progress)
        return engine_result(server.engine.run(self.build(args), key=key, progress=progress))


# ---------------------------------------------------------------------------------------------------------
# tools that run one engine command


def b_update(cmd):
    def build(a):
        argv = [cmd]
        if a.get("folder"):
            argv.append(path_of(a["folder"]))
        opt(argv, "--source", a.get("source"), source_of)
        opt(argv, "--trust-key", a.get("trust_key"))
        if a.get("all"):
            argv.append("--all")
        opt(argv, "--jobs", a.get("jobs"))
        return project_first(a, argv)
    return build


def b_publish(a):
    if a.get("store") and not a.get("folder"):
        raise ToolProblem("give the folder too when you give the store")
    argv = ["publish"] + [path_of(a[k]) for k in ("folder", "store") if a.get(k)]
    release_options(argv, a)
    return project_first(a, argv)


def b_deploy(a):
    argv = ["deploy"] + ([path_of(a["store"])] if a.get("store") else [])
    opt(argv, "--to-folder", a.get("to_folder"), path_of)
    ssh_options(argv, a)
    if a.get("dry_run"):
        argv.append("--dry-run")
    if a.get("prune") is False:
        argv.append("--no-prune")
    return project_first(a, argv)


def b_settings_set(a):
    values = a["values"]
    if not values:
        raise ToolProblem("values is empty: give at least one setting")
    argv = ["config", "set"]
    for key, value in values.items():
        if not re.match(r"^[a-z][a-z0-9_]*$", key):
            raise ToolProblem("%r is not a setting's name" % key)
        if isinstance(value, bool):
            value = "yes" if value else "no"
        elif isinstance(value, list):
            value = ";".join(str(v) for v in value)
        elif value is None:
            value = ""
        argv.append("%s=%s" % (key, value))
    return project_first(a, argv)


def b_backup_restore(a):
    name = str(a["name"])
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", name):
        raise ToolProblem("name is a backup's date, YYYY-MM-DD (backup_list names them)")
    return project_first(a, ["backup", "restore", name] + (["--dry-run"] if a.get("dry_run") else ["--yes"]))


def b_stats(a):
    argv = ["stats"]
    opt(argv, "--days", a.get("days"))
    return project_first(a, argv)


def simple(*argv):
    return lambda a: project_first(a, list(argv))


def with_path(*argv):
    def build(a):
        out = list(argv)
        opt(out, "--path", a.get("path"), path_of)
        return out
    return build


def b_ssh(cmd, root):
    def build(a):
        argv = ["ssh", cmd]
        ssh_options(argv, a, root)
        return project_first(a, argv)
    return build


def b_ssh_trust(a):
    argv = ["ssh", "trust", "--host", a["host"], "--accept", a["accept"]]
    opt(argv, "--port", a.get("port"))
    return argv


def b_project_remove(a):
    """One project, named: removing every project (or resetting DeltaPatcher) is for a person to do."""
    if a["project"].strip().startswith("-"):
        raise ToolProblem("give one project, by its name or its settings file")
    return ["project", "remove", a["project"], "--yes"]


def b_project_import(a):
    argv = ["project", "import", path_of(a["file"])]
    for name in a.get("projects") or []:
        argv += ["--only", name]
    opt(argv, "--folder", a.get("folder"), path_of)
    argv.append("--dry-run" if a.get("dry_run") else "--yes")
    return argv


# ---------------------------------------------------------------------------------------------------------
# tools of their own


def t_release(server, a, key, progress):
    if a.get("to") == "folder" and not a.get("to_folder"):
        pass                                                    # the saved deploy_folder is used
    if not a.get("to"):
        cfg = server.engine.run(project_first(a, ["config", "get"]))
        mode = ((cfg.data or {}).get("settings") or {}).get("deploy_mode") if cfg.ok else None
        if mode == "local":
            raise ToolProblem("this project releases to this computer (deploy_mode local), which serves until it "
                              "is stopped: use publish and then serve_start for that, or give to=sftp or to=folder")
    argv = ["release"] + ([path_of(a["folder"])] if a.get("folder") else [])
    release_options(argv, a)
    opt(argv, "--to", a.get("to"))
    opt(argv, "--to-folder", a.get("to_folder"), path_of)
    ssh_options(argv, a)
    if a.get("dry_run"):
        argv.append("--dry-run")
    return engine_result(server.engine.run(project_first(a, argv), key=key, progress=progress))


def free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def t_serve_start(server, a, key, progress):
    argv = ["serve"] + ([path_of(a["store"])] if a.get("store") else [])
    argv += ["--port", str(a.get("port") or free_port())]
    url, store = server.start_server(project_first(a, argv))
    return text_result("Serving at %s (on this computer only). serve_stop stops it." % url, {"url": url, "store": store})


def t_serve_stop(server, a, key, progress):
    stopped = server.stop_servers(a.get("url"))
    if a.get("url") and not stopped:
        raise ToolProblem("nothing of this server serves %s (serving: %s)"
                          % (a["url"], ", ".join(server.serving()) or "nothing"))
    return text_result("Stopped: %s" % (", ".join(stopped) or "nothing was being served"), {"stopped": stopped})


def t_guide(server, a, key, progress):
    g = GUIDE[a["name"]]
    text = read_guide(g)
    if text is None:
        raise ToolProblem("the guide's file is not here; it is on the wiki: %s/%s" % (WIKI, g[2]))
    return text_result("# %s\n(wiki: %s/%s)\n\n%s" % (g[3], WIKI, g[2], text), {"name": g[0], "wiki": "%s/%s" % (WIKI, g[2])})


def t_log(server, a, key, progress):
    path, lines = log_tail(a.get("lines") or 200)
    return text_result("%s\n\n%s" % (path, "\n".join(lines) if lines else "(the log is empty or not made yet)"),
                       {"path": path, "lines": len(lines)})


def t_selftest(server, a, key, progress):
    return SelfTest(server).run()


TOOLS_LIST = [
    Tool("version", "DeltaPatcher's version", "The version of DeltaPatcher this server runs, and whether it can sign.",
         schema(), RO, build=simple("version")),
    Tool("list_projects", "List projects",
         "Every project: the folder it keeps up to date and where from, the folder it releases and where it goes. "
         "A project is one settings file; the Default one is the main settings file.",
         schema(), RO, build=simple("projects")),
    Tool("get_settings", "Read a project's settings",
         "Every setting of a project (and DeltaPatcher's own), with the settings file's path. Nothing secret is "
         "ever in them: keys are named by their file's path.",
         schema(project=PROJECT), RO, build=simple("config", "get")),
    Tool("set_settings", "Change settings",
         "Change settings of a project, or DeltaPatcher's own (parallel_updates, check_updates...): values maps a "
         "setting's name to its new value (true/false for yes/no, lists as lists). Folders are best given as "
         "absolute paths. get_settings shows every name.",
         schema(["values"], values={"type": "object", "description": "setting name -> new value"}, project=PROJECT),
         WRITES, build=b_settings_set),
    Tool("check", "Check for an update",
         "What an update would change, changing nothing: files new, replaced and removed, and how much would be "
         "downloaded. all=true checks every project that has a folder and a source, several at once.",
         schema(folder=FOLDER, source=SOURCE,
                trust_key=s_str("The publisher key (ed25519:...) the release must be signed with; off: do not check."),
                all=s_bool("Every project with a folder and a source."),
                jobs=s_int("With all: how many projects at once (left out: the setting, else 3).", 1, 16),
                project=PROJECT),
         RO_NET, build=b_update("check")),
    Tool("update", "Update a folder",
         "Bring a folder up to the source's release: only what changed is downloaded, every file is checked "
         "before anything changes, and the update is journalled (rollback undoes it). all=true updates every "
         "project with a folder and a source, several at once. Run check first and say what will change.",
         schema(folder=FOLDER, source=SOURCE,
                trust_key=s_str("The publisher key (ed25519:...) the release must be signed with; off: do not check."),
                all=s_bool("Every project with a folder and a source."),
                jobs=s_int("With all: how many projects at once (left out: the setting, else 3).", 1, 16),
                project=PROJECT),
         CHANGES_NET, build=b_update("update")),
    Tool("rollback", "Undo the last update",
         "Undo the last update of a folder: the files it changed come back as they were.",
         schema(["folder"], folder=s_str("The updated folder."), project=PROJECT),
         CHANGES, build=lambda a: project_first(a, ["rollback", path_of(a["folder"])])),
    Tool("publish", "Publish a folder",
         "Turn a folder into a store (manifest.json plus one blob per distinct file), sent nowhere: release sends "
         "it too. A project set to sign its releases needs the passphrase, which this server never handles: "
         "release those from the DeltaPatcher app or its command line.",
         schema(folder=s_str("The folder to release (left out: the project's saved one)."),
                store=s_str("Where to write the store (left out: the saved store, else DeltaPatcher's own folder). "
                            "Needs folder too."),
                **RELEASE),
         WRITES, build=b_publish),
    Tool("release", "Release a folder",
         "Publish a folder and send it where players download it from: a server over SFTP (only the missing "
         "files travel, the manifest last) or a folder. dry_run=true says what would be sent and sends nothing: "
         "do that first. SFTP needs the project's SSH key unlocked in ssh-agent and the server's host key "
         "trusted (both done in the app). Signed releases need the passphrase: release those in the app.",
         schema(folder=s_str("The folder to release (left out: the project's saved one)."),
                to=s_str("sftp: the saved server; folder: to_folder or the saved one (left out: the saved choice).",
                         enum=["sftp", "folder"]),
                to_folder=s_str("With to=folder: the folder to copy the release to."),
                remote_root=s_str("The folder on the server its web server serves (left out: the saved one)."),
                dry_run=s_bool("Say what would be sent; send nothing."),
                project=PROJECT, **dict(RELEASE, **SSH)),
         CHANGES_NET, run=t_release),
    Tool("deploy", "Send a store",
         "Send an already published store to a server over SFTP or to a folder: only the missing files, the "
         "manifest last. dry_run=true sends nothing.",
         schema(store=s_str("The store (left out: the saved store, else the saved folder's)."),
                to_folder=s_str("Send it to this folder instead of a server."),
                remote_root=s_str("The folder on the server its web server serves (left out: the saved one)."),
                dry_run=s_bool("Say what would be sent; send nothing."),
                prune=s_bool("Remove files no release names any more (default yes)."),
                project=PROJECT, **SSH),
         CHANGES_NET, build=b_deploy),
    Tool("serve_start", "Serve a store on this computer",
         "Serve a store over http on 127.0.0.1, for testing updates on this computer: it gives the address to "
         "use as a source. It runs until serve_stop or until this server ends.",
         schema(store=s_str("The store (left out: the project's saved store)."),
                port=s_int("The port (left out: a free one).", 1, 65535), project=PROJECT),
         WRITES, run=t_serve_start),
    Tool("serve_stop", "Stop serving", "Stop a store served with serve_start (url), or every one.",
         schema(url=s_str("The address serve_start gave (left out: every one).")), WRITES, run=t_serve_stop),
    Tool("ssh_status", "SSH key and server status",
         "The project's SSH key (passphrase, permissions, unlocked in ssh-agent) and whether its server's host "
         "key is trusted.",
         schema(project=PROJECT, **SSH), RO, build=b_ssh("status", False)),
    Tool("ssh_scan", "A server's host keys",
         "The host keys a server offers, with their fingerprints, to compare with the ones the server's owner "
         "reads on the server itself (ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub).",
         schema(["host"], host=s_str("The server's address."), port=s_int("Its SSH port (default 22).", 1, 65535)),
         RO_NET, build=lambda a: ["ssh", "scan", "--host", a["host"]] + (["--port", str(a["port"])] if a.get("port") else [])),
    Tool("ssh_trust", "Trust a server's host key",
         "Keep a server's host key so deploys can reach it, but only if the server offers exactly the "
         "fingerprints given. Use fingerprints the person read on the server itself, never ones only ssh_scan "
         "showed: that is what makes the check worth anything.",
         schema(["host", "accept"], host=s_str("The server's address."),
                port=s_int("Its SSH port (default 22).", 1, 65535),
                accept=s_str("The fingerprints, comma-separated (SHA256:...).")),
         dict(WRITES, openWorldHint=True), build=b_ssh_trust),
    Tool("ssh_test", "Test the server login", "Log in to the project's server with SFTP and say whether it worked.",
         schema(project=PROJECT, remote_root=s_str("The folder on the server (left out: the saved one)."), **SSH),
         RO_NET, build=b_ssh("test", True)),
    Tool("ssh_pubkey", "The server line for the SSH key",
         "The authorized_keys line of the project's SSH key, limited to SFTP: what goes on the server. Public, "
         "not secret.",
         schema(path=s_str("The key's file (left out: the saved one).")), RO, build=with_path("ssh", "pubkey")),
    Tool("sign_status", "Signing key status", "Whether there is a signing key, and whether it has a passphrase.",
         schema(path=s_str("The signing key's file (left out: the saved one).")), RO, build=with_path("sign", "status")),
    Tool("sign_pubkey", "The publisher key",
         "The public key players use to check releases (ed25519:...): what a publisher gives players. Public, "
         "not secret.",
         schema(path=s_str("The signing key's file (left out: the saved one).")), RO, build=with_path("sign", "pubkey")),
    Tool("project_new", "New project", "Make a new project: its own settings file in the projects folder.",
         schema(["name"], name=s_str("Its name: letters, digits, spaces and - _ . ( ).")), WRITES,
         build=lambda a: ["project", "new", a["name"]]),
    Tool("project_add", "Add a project file",
         "List a project's settings file kept somewhere else (in a game's folder, on a share).",
         schema(["file"], file=s_str("The settings file."), name=s_str("Give it this name.")), WRITES,
         build=lambda a: ["project", "add", path_of(a["file"])] + (["--name", a["name"]] if a.get("name") else [])),
    Tool("project_rename", "Rename a project", "Give a project another name; its file stays where it is.",
         schema(["project", "new_name"], project=PROJECT, new_name=s_str("The new name.")), WRITES,
         build=lambda a: ["project", "rename", a["project"], a["new_name"]]),
    Tool("project_remove", "Remove a project",
         "Remove a project: one in the projects folder is deleted with its settings; an added file only leaves "
         "the list. The folders it updates or releases are not touched.",
         schema(["project"], project=PROJECT), CHANGES, build=b_project_remove),
    Tool("project_export", "Export projects to a project file",
         "Write a project file holding projects to give to players: their names, the http(s) address each updates "
         "from, their publisher keys, descriptions, websites and where each goes (install_hint, and install_folder: "
         "the folder it goes into inside the one a player picks); never a folder path, a server login or a key file. "
         "out ending in .deltapatch opens DeltaPatcher with a double-click; .html is a page anyone can read in a "
         "browser. It is not signed: signing needs the passphrase, which this server never handles (export it from "
         "the DeltaPatcher app or its command line to sign it).",
         schema(["projects", "out"], projects=s_list("The projects: their names or settings files."),
                out=s_str("The file to write: NAME.deltapatch or NAME.html.")),
         WRITES, build=lambda a: ["project", "export"] + [str(n) for n in a["projects"]] + ["--out", path_of(a["out"])]),
    Tool("project_import", "Import a project file",
         "Add the projects of a project file (.deltapatch or .html, from a publisher) as projects here. "
         "dry_run=true only reads it: what it holds and who signed it. A signed file changed after it was signed is "
         "refused; an unsigned one is added with a warning. Each project keeps the folder given (with several, a "
         "folder each inside it, named after the project), or one chosen later. A project whose file names a folder "
         "for it (install_folder) goes into that folder inside the one given, unless the one given already is it; its "
         "install_hint says where it goes.",
         schema(["file"], file=s_str("The project file."),
                folder=s_str("The folder to keep up to date (several projects: a folder each inside it, named by "
                             "install_folder or after the project). Left out: chosen later, with set_settings "
                             "install_dir."),
                projects=s_list("Only these projects of the file, by name (left out: every one)."),
                dry_run=s_bool("Read the file and say what it holds; add nothing.")),
         {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
         build=b_project_import),
    Tool("backup_now", "Back up the live release",
         "Back up a project's live release onto this computer: read from where it is deployed (the server over SFTP, "
         "with the checks a release makes, or the deploy folder), each blob checked against its SHA-256 as it arrives. "
         "Nothing at the destination changes. A release unchanged since the last backup makes no new one; a second "
         "backup the same day replaces that day's. The limits (backup_keep_daily, backup_keep_weekly, "
         "backup_keep_monthly, backup_max_size) are applied after it: older backups they do not keep are removed "
         "from this computer.",
         schema(project=PROJECT), BACKS_UP, build=lambda a: project_first(a, ["backup", "now"])),
    Tool("backup_list", "List the backups",
         "A project's backups of its live release, the newest first: each one's date (its name, a day in UTC), the "
         "release it holds (name, version, files, size), the folder they are in with its size and the limits, the "
         "backups that cannot be read, and how the system scheduler's last run went (backup schedule).",
         schema(project=PROJECT), RO, build=simple("backup", "list")),
    Tool("backup_restore", "Restore a backup",
         "Put a backup back where players download from, as a release goes: the blobs the destination lacks first, "
         "the manifest last, after every blob of it is checked against its SHA-256. Players get that release from "
         "then on; nothing at the destination is removed, except a file of the same name and the wrong size, which "
         "is replaced. dry_run=true only says what would be sent.",
         schema(["name"], name=s_str("The backup: its date, YYYY-MM-DD (backup_list names them)."),
                dry_run=s_bool("Say what would be sent; send nothing."), project=PROJECT),
         CHANGES_NET, build=b_backup_restore),
    Tool("stats", "Statistics of a project",
         "A project's statistics as one document (format deltapatcher-stats, version 1): its current release, what it "
         "changed and the release history, from its store; and, when its access_log is set, update checks, downloads, "
         "bytes and players per day and the releases players are on, counted from the web server's access log (read "
         "over the project's SFTP connection, or on this computer for a folder or local release). Counts only: never "
         "an address or a line of the log. A section that cannot be made says why under problems.",
         schema(days=s_int("How many days back (default 30).", 1, 366), project=PROJECT), RO_NET, build=b_stats),
    Tool("check_for_new_version", "Is DeltaPatcher up to date",
         "Whether a newer DeltaPatcher is out. Updating DeltaPatcher itself is done in its app or command line.",
         schema(), RO_NET, build=simple("self-update", "--check")),
    Tool("guide", "Read a guide",
         "Read one of DeltaPatcher's guides: " + "; ".join("%s (%s)" % (g[0], g[4]) for g in GUIDES) + ".",
         schema(["name"], name=s_str("The guide.", enum=[g[0] for g in GUIDES])), RO, run=t_guide),
    Tool("log", "Read the log", "The last lines of DeltaPatcher's log: what each command did, and every error.",
         schema(lines=s_int("How many lines (default 200).", 1, 5000)), RO, run=t_log),
    Tool("selftest", "Self-test",
         "Check the whole cycle on this computer, in a throwaway folder that touches no project and no setting: "
         "publish, serve, update an empty folder, publish a change, check, update (only the change travels), "
         "roll back. Says which step failed, if one does.",
         schema(), {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
         run=t_selftest),
]
TOOLS = {t.name: t for t in TOOLS_LIST}


# ---------------------------------------------------------------------------------------------------------
# guides and the log


def guide_root():
    """The guides: inside the built program (the packaging puts them in guides/), else the repository."""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return os.path.join(bundle, "guides")
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_guide(g):
    path = os.path.join(guide_root(), *g[1].split("/"))
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def log_tail(n):
    from . import logs
    path = logs.default_path()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    return path, lines[-n:]


PROMPTS = [
    ("release_new_version", "Release a new version",
     "Release a folder as a new version, after a dry run the person has seen.",
     [("folder", "The folder to release", False), ("version", "The new version", True),
      ("project", "The project (left out: Default)", False)],
     "Release {folder_text} as version {version} with DeltaPatcher{project_text}. First read the project's "
     "settings (get_settings) and tell me where the release will go. Then run release with dry_run true and "
     "show me what would be sent. Wait for my go-ahead before the real release. If the project signs its "
     "releases, stop and tell me to release it from the DeltaPatcher app or its command line, which ask for "
     "the passphrase."),
    ("keep_folder_updated", "Keep a folder up to date",
     "Make a project that keeps a folder up to date from a source, and update it once the person agrees.",
     [("source", "The address (or folder) the release is at", True), ("folder", "The folder to keep up to date", True),
      ("name", "A name for the project", False), ("publisher_key", "The publisher key, if the publisher gave one", False)],
     "Set DeltaPatcher up to keep {folder} up to date from {source}. Make a project for it (project_new, named "
     "{name_text}), then set its install_dir, source_url{key_text} with set_settings. Run check and tell me "
     "what an update would download. Update only after I agree."),
    ("diagnose", "Explain an error",
     "Explain a DeltaPatcher error in plain words, from the troubleshooting guide and the log.",
     [("error", "What DeltaPatcher said", True)],
     "DeltaPatcher said: {error}\nRead the troubleshooting guide (the guide tool, name troubleshooting) and the "
     "last lines of the log (the log tool). Explain what went wrong in plain words and what to do about it."),
]


# ---------------------------------------------------------------------------------------------------------
# the self-test


class SelfTest(object):
    """The whole cycle in a throwaway folder, with settings of its own: no project, setting or log is touched."""

    def __init__(self, server):
        self.server = server
        self.work = tempfile.mkdtemp(prefix="deltapatcher-selftest-")
        self.env = {"DELTAPATCHER_CONFIG": os.path.join(self.work, "deltapatcher.ini")}
        self.drop = ("DELTAPATCHER_PROJECT", "DELTAPATCHER_LOG")
        self.steps = []

    def engine(self, *argv):
        o = self.server.engine.run(["--no-log"] + list(argv), extra_env=self.env, drop_env=self.drop)
        if not o.ok:
            raise ToolProblem("%s: %s" % (argv[0], o.message))
        return o.data

    def step(self, name, fn):
        t0 = time.time()
        try:
            detail, ok = fn(), True
        except Exception as e:                                       # a failed step is reported, not raised
            detail, ok = str(e), False
        self.steps.append({"step": name, "ok": ok, "seconds": round(time.time() - t0, 2), "detail": detail or ""})
        return ok

    @staticmethod
    def write(root, files):
        for rel, data in files.items():
            path = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)

    @staticmethod
    def tree(root):
        out = {}
        for top, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d != ".deltapatcher"]
            for name in files:
                path = os.path.join(top, name)
                with open(path, "rb") as f:
                    out[os.path.relpath(path, root).replace(os.sep, "/")] = hashlib.sha256(f.read()).hexdigest()
        return out

    def same(self, folder, want, what):
        got = self.tree(folder)
        if got != want:
            missing = sorted(set(want) - set(got))
            extra = sorted(set(got) - set(want))
            wrong = sorted(k for k in set(got) & set(want) if got[k] != want[k])
            raise ToolProblem("the folder is not %s: missing %s, extra %s, different %s" % (what, missing, extra, wrong))

    def run(self):
        src, store, player = (os.path.join(self.work, n) for n in ("release", "store", "player"))
        url = None
        try:
            self.write(src, {"readme.txt": b"DeltaPatcher self-test\n", "data/pack.bin": os.urandom(300000),
                             "maps/one.map": b"map one\n" * 500})
            v1 = self.tree(src)

            def publish(version):
                d = self.engine("publish", src, store, "--name", "Self-test", "--version", version, "--no-htaccess")
                return "%s files" % d.get("files")

            def serve():
                nonlocal url
                url, _store = self.server.start_server(["--no-log", "serve", store, "--port", str(free_port())],
                                                       extra_env=self.env, drop_env=self.drop)
                return url

            def first_update():
                d = self.engine("update", player, "--source", url)
                self.same(player, v1, "release 1")
                return d.get("summary", "")

            def change():
                self.write(src, {"data/pack.bin": os.urandom(300000), "extra/new.txt": b"a new file\n"})
                os.remove(os.path.join(src, "maps", "one.map"))
                return publish("2")

            def check():
                d = self.engine("check", player, "--source", url)
                got = (d.get("add"), d.get("replace"), d.get("remove"))
                if got != (1, 1, 1):
                    raise ToolProblem("expected 1 new, 1 replaced, 1 removed; got %s" % (got,))
                return d.get("summary", "")

            def second_update():
                d = self.engine("update", player, "--source", url)
                if d.get("download_blobs") != 2:
                    raise ToolProblem("expected 2 files to travel, %s did" % d.get("download_blobs"))
                self.same(player, self.tree(src), "release 2")
                return "%s; %s files downloaded" % (d.get("summary", ""), d.get("download_blobs"))

            def rollback():
                self.engine("rollback", player)
                self.same(player, v1, "release 1 again")
                return "back to release 1"

            for name, fn in (("publish release 1", lambda: publish("1")), ("serve it on this computer", serve),
                             ("update an empty folder", first_update), ("publish release 2 (1 new, 1 changed, 1 removed)", change),
                             ("check what an update would change", check), ("update: only the changes travel", second_update),
                             ("roll back", rollback)):
                if not self.step(name, fn):
                    break
        finally:
            if url:
                self.server.stop_servers(url)
            shutil.rmtree(self.work, ignore_errors=True)
        passed = all(s["ok"] for s in self.steps) and len(self.steps) == 7
        lines = ["%s  %s (%.2f s)%s" % ("ok    " if s["ok"] else "FAILED", s["step"], s["seconds"],
                                         ": " + s["detail"] if s["detail"] else "") for s in self.steps]
        head = ("Self-test passed: %d steps." % len(self.steps)) if passed else "Self-test FAILED."
        return text_result(head + "\n" + "\n".join(lines), {"passed": passed, "steps": self.steps}, error=not passed)


# ---------------------------------------------------------------------------------------------------------
# the server


class Server(object):
    def __init__(self, rfile, wfile):
        self.rfile, self.wfile = rfile, wfile
        self.out_lock = threading.Lock()
        self.engine = Engine()
        self.pool = ThreadPoolExecutor(max_workers=8)
        self.cancelled = set()
        self.servers = {}
        self.servers_lock = threading.Lock()
        self.protocol = PROTOCOLS[0]

    # -- sending
    def send(self, msg):
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode("utf-8")
        with self.out_lock:
            self.wfile.write(data)
            self.wfile.flush()

    def reply(self, rid, result):
        self.send({"jsonrpc": "2.0", "id": rid, "result": result})

    def fail(self, rid, code, message):
        self.send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})

    def progress_sender(self, token):
        state = {"last": -1, "at": 0.0}

        def send(event):
            done, now = event.get("done") or 0, time.time()
            if done <= state["last"] or (now - state["at"] < 0.25 and done < (event.get("total") or 0)):
                return
            state["last"], state["at"] = done, now
            params = {"progressToken": token, "progress": done}
            if event.get("total"):
                params["total"] = event["total"]
            label = event.get("label") or ""
            if event.get("project"):
                label = "%s: %s" % (event["project"], label)
            if label:
                params["message"] = label
            self.send({"jsonrpc": "2.0", "method": "notifications/progress", "params": params})
        return send

    # -- the loop
    def serve_forever(self):
        try:
            for raw in self.rfile:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    self.fail(None, -32700, "parse error: not JSON")
                    continue
                for m in (msg if isinstance(msg, list) else [msg]):
                    self.dispatch(m)
        finally:
            self.close()

    def dispatch(self, m):
        if not isinstance(m, dict) or m.get("jsonrpc") != "2.0":
            if isinstance(m, dict) and m.get("id") is not None:
                self.fail(m.get("id"), -32600, "not a JSON-RPC 2.0 request")
            return
        method, rid = m.get("method"), m.get("id")
        params = m.get("params") if isinstance(m.get("params"), dict) else {}
        if method is None:
            return                                                   # an answer to a request this server never sends
        if rid is None:
            if method == "notifications/cancelled":
                self.cancel(params.get("requestId"))
            return
        if method == "tools/call":
            self.pool.submit(self.call_tool, rid, params)
            return
        handler = {"initialize": self.initialize, "ping": lambda p: {},
                   "tools/list": lambda p: {"tools": [t.listing() for t in TOOLS_LIST]},
                   "resources/list": self.resources_list, "resources/read": self.resources_read,
                   "resources/templates/list": lambda p: {"resourceTemplates": []},
                   "prompts/list": self.prompts_list, "prompts/get": self.prompts_get}.get(method)
        if handler is None:
            self.fail(rid, -32601, "no such method: %s" % method)
            return
        try:
            self.reply(rid, handler(params))
        except McpError as e:
            self.fail(rid, e.code, e.message)
        except Exception as e:                                       # an answer, never a dead server
            self.fail(rid, -32603, "internal error: %s" % e)

    def close(self):
        self.engine.stop_all()
        self.stop_servers(None)
        self.pool.shutdown(wait=False)

    # -- requests
    def initialize(self, params):
        asked = params.get("protocolVersion")
        self.protocol = asked if asked in PROTOCOLS else PROTOCOLS[0]
        return {"protocolVersion": self.protocol,
                "capabilities": {"tools": {"listChanged": False},
                                 "resources": {"listChanged": False, "subscribe": False},
                                 "prompts": {"listChanged": False}},
                "serverInfo": {"name": "deltapatcher", "title": APP_NAME, "version": __version__},
                "instructions": INSTRUCTIONS}

    def call_tool(self, rid, params):
        name, args = params.get("name"), params.get("arguments")
        args = {} if args is None else args
        tool = TOOLS.get(name)
        if tool is None:
            self.fail(rid, -32602, "no such tool: %s" % name)
            return
        problem = check_args(tool.schema, args)
        if problem:
            self.fail(rid, -32602, "%s: %s" % (name, problem))
            return
        meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
        progress = self.progress_sender(meta["progressToken"]) if meta.get("progressToken") is not None else None
        try:
            result = tool.run(self, args, rid, progress)
        except ToolProblem as e:
            result = text_result(str(e), {"ok": False, "message": str(e)}, error=True)
        except Exception as e:                                       # reported to the assistant, the server goes on
            result = text_result("unexpected: %s" % e, {"ok": False, "message": str(e)}, error=True)
        if rid in self.cancelled:
            self.cancelled.discard(rid)
            return
        self.reply(rid, result)

    def cancel(self, rid):
        if rid is not None:
            self.cancelled.add(rid)
            self.engine.stop(rid)

    def resources_list(self, params):
        out = [{"uri": "deltapatcher://guide/%s" % g[0], "name": g[0], "title": g[3], "description": g[4],
                "mimeType": "text/markdown"} for g in GUIDES if read_guide(g) is not None]
        out.append({"uri": "deltapatcher://log", "name": "log", "title": "DeltaPatcher's log",
                    "description": "The last 400 lines of the program log", "mimeType": "text/plain"})
        return {"resources": out}

    def resources_read(self, params):
        uri = params.get("uri") or ""
        if uri == "deltapatcher://log":
            path, lines = log_tail(400)
            return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": "\n".join(lines)}]}
        m = re.match(r"^deltapatcher://guide/([a-z]+)$", uri)
        text = read_guide(GUIDE[m.group(1)]) if m and m.group(1) in GUIDE else None
        if text is None:
            raise McpError(-32002, "no such resource: %s" % uri)
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}

    def prompts_list(self, params):
        return {"prompts": [{"name": n, "title": t, "description": d,
                             "arguments": [{"name": a, "description": ad, "required": r} for a, ad, r in args]}
                            for n, t, d, args, _text in PROMPTS]}

    def prompts_get(self, params):
        found = [p for p in PROMPTS if p[0] == params.get("name")]
        if not found:
            raise McpError(-32602, "no such prompt: %s" % params.get("name"))
        name, title, description, args, text = found[0]
        given = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        for a, _d, required in args:
            if required and not given.get(a):
                raise McpError(-32602, "%s needs %s" % (name, a))
        values = {a: str(given.get(a) or "") for a, _d, _r in args}
        values["folder_text"] = values.get("folder") or "the project's folder"
        values["project_text"] = (" (project %s)" % values["project"]) if values.get("project") else ""
        values["name_text"] = values.get("name") or "after the folder"
        values["key_text"] = (" and trust_key (%s)" % values["publisher_key"]) if values.get("publisher_key") else ""
        return {"description": description,
                "messages": [{"role": "user", "content": {"type": "text", "text": text.format(**values)}}]}

    # -- stores served on this computer
    def start_server(self, argv, extra_env=None, drop_env=()):
        p = start_engine(argv, extra_env, drop_env)
        found = {}
        ready = threading.Event()

        def follow():
            for raw in p.stdout:
                event = read_event(raw)
                if event and event.get("event") == "serving" and not ready.is_set():
                    found["url"] = event.get("url")
                    ready.set()
                elif event and event.get("event") == "error" and not ready.is_set():
                    found["error"] = event.get("message")
                    ready.set()
            ready.set()

        threading.Thread(target=follow, daemon=True).start()
        threading.Thread(target=p.stderr.read, daemon=True).start()
        ready.wait(60)
        if not found.get("url"):
            stop_engine(p, "close", wait=3)
            raise ToolProblem("the store could not be served: %s" % (found.get("error") or "no answer"))
        store = argv[argv.index("serve") + 1] if "serve" in argv and len(argv) > argv.index("serve") + 1 \
            and not argv[argv.index("serve") + 1].startswith("--") else ""
        with self.servers_lock:
            self.servers[found["url"]] = p
        return found["url"], store

    def serving(self):
        with self.servers_lock:
            return [u for u, p in self.servers.items() if p.poll() is None]

    def stop_servers(self, url):
        with self.servers_lock:
            urls = [u for u in self.servers if url is None or u.rstrip("/") == str(url).rstrip("/")]
            procs = [(u, self.servers.pop(u)) for u in urls]
        for _u, p in procs:
            stop_engine(p, "close", wait=5)
        return [u for u, _p in procs]


def main(argv=None):
    """Serve MCP on standard input and output until the client closes them."""
    rfile, wfile = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr                                           # a stray print can never reach the protocol
    Server(rfile, wfile).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
