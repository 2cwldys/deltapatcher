"""The command line, for people and for the DeltaPatcher app.

In a terminal the output is text, in colour where the terminal shows it. With --json it is one JSON
object a line, which is how the app (app/, Electron) drives this program:

    {"event": "log", "text": "...", "style": "ok|warn|err|code|head|null"}
    {"event": "progress", "done": 123, "total": 456, "label": "downloading"}
    {"event": "result", "ok": true, "data": {...}}                  the last line on success
    {"event": "error", "message": "...", "code": "...", "data": {...}}   the last line on failure

update --all (and check --all) runs several projects at the same time. Every log and progress line
about one of them carries its name and its settings file ("project": "...", "file": "..."), and
these follow each one:
    {"event": "queue", "run": [{"project": ..., "file": ...}], "skipped": [...], "jobs": 3, "check": false}
    {"event": "project-start", "project": ..., "file": ...}
    {"event": "project-end", "project": ..., "file": ..., "ok": true, "data": {...}}
    {"event": "project-end", "project": ..., "file": ..., "ok": false, "message": "...", "code": "..."}

In --json mode standard input carries what a person would type: with --passphrase-stdin the first
line is the passphrase (never put on a command line or in the environment), and a later line
"cancel" stops a download, leaving the folder as it was.
"""

import argparse
import concurrent.futures
import getpass
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
import traceback

from . import APP_NAME, __version__, client, config, deploy, logs, projects, publish, selfupdate, serve, signing, ssh
from .color import enable as color_enable
from .color import paint, plain
from .manifest import ManifestError
from .source import DEFAULT_URL, SourceError, insecure_remote

# %(prog)s is the name to type (see prog_name); argparse fills it in
EPILOG = """examples:

put releases on your VPS (the first lines once; then one line for every version):
  %%(prog)s config set publish_src=./game ssh_host=game.example.com ssh_user=deploy remote_root=/var/www/game
  %%(prog)s config set public_url=https://game.example.com/   (where players update from)
  %%(prog)s ssh keygen                  (a key for the server, with a passphrase)
  %%(prog)s ssh pubkey                  (the line for the server's ~/.ssh/authorized_keys)
  %%(prog)s ssh trust                   (check the server's host key, and keep it)
  %%(prog)s release --version 1.0       (publishes ./game, sends the server only what changed)

players update from it:
  %%(prog)s update ./MyGame --source https://game.example.com/

try a release on this computer first:
  %%(prog)s release --to local          (publishes, then serves it at http://localhost:8000/)
  %%(prog)s update ./MyGame --source http://localhost:8000/

more:
  %%(prog)s ssh test                    (can it log in to the server?)
  %%(prog)s sign keygen                 (once: a signing key; then release with --sign)
  %%(prog)s release --to folder --to-folder D:/www/game
  %%(prog)s publish ./game ./store      (only the release's files, sent nowhere)
  %%(prog)s config                      (where the settings file and the log are)

several games or mods: a project each, with settings of its own (commands use Default otherwise):
  %%(prog)s project new "My Mod"        (then: --project "My Mod" before or after any command)
  %%(prog)s projects                    (every project, and what each one updates and releases)
  %%(prog)s update --all                (every project's folder, several at the same time)

The log: %s (--log FILE elsewhere, --no-log for none).
""" % logs.default_path().replace("%", "%%")

# which lines get which colour, by how they start: the library's messages are plain text
STYLE_BY_START = (
    ("published", "ok"), ("updated to", "ok"), ("up to date", "ok"), ("deployed", "ok"), ("downloaded and checked", "ok"),
    ("signed by", "ok"), ("signed with", "ok"), ("rolled back", "ok"), ("manifest in place", "ok"),
    ("the release is not signed", "warn"), ("trusting this publisher key", "warn"), ("skipped", "warn"),
    ("  kept", "warn"), ("the last update stopped", "warn"), ("an update stopped", "warn"), ("dry run", "warn"),
    ("the target", "warn"), ("Warning", "warn"), ("Not trusted", "warn"), ("signature checks are off", "warn"),
)


class CliError(Exception):
    """A failure with a code the app can act on (host-unknown, key-permissions, ...)."""

    def __init__(self, message, code="failed", data=None):
        Exception.__init__(self, message)
        self.code = code
        self.data = data or {}


class Output(object):
    """Where messages go: a terminal (text, colour, a progress bar) or the app (JSON lines). update --all
    shares it between projects running in threads: each line is written whole, under one lock, and a
    project's lines carry its name (and, for the app, its file)."""

    def __init__(self, json_mode=False):
        self.json = json_mode
        self.last = {}           # when progress was last shown, by project file (None: a command's only one)
        self.shown = False
        self.lock = threading.RLock()
        self.bars = None         # update --all in a terminal: {file: (name, done, total)}, shown on one line
        self.drawn = 0           # how long that line is on the screen

    def _emit(self, obj, project=None):
        """One JSON line to the app; a project's carries its name and file. When the app has gone away (its
        end of the pipe is closed), there is nobody to tell: the line is dropped instead of turning a finished
        job into a failure."""
        if project is not None:
            obj = dict(obj, **project.tag())
        with self.lock:
            if getattr(self, "gone", False):
                return
            try:
                sys.stdout.write(json.dumps(obj) + "\n")
                sys.stdout.flush()
            except (OSError, ValueError):
                self.gone = True

    def say(self, text, style=None, project=None):
        if style is None:
            for start, s in STYLE_BY_START:
                if text.startswith(start):
                    style = s
                    break
        logs.write(plain(text), None if project is None else ("" if project.main else project.title))
        if self.json:
            self._emit({"event": "log", "text": plain(text), "style": style}, project)
            return
        with self.lock:
            if project is None:
                self.end()
                print(paint(text, style) if style else text)
                return
            self._clear_bars()
            print("%s %s" % (paint("[%s]" % project.title, "head"), paint(text, style) if style else text))
            sys.stdout.flush()
            self._draw_bars()

    def progress(self, done, total, label, project=None):
        key = None if project is None else project.path
        with self.lock:
            now = time.time()
            if now - self.last.get(key, 0.0) < (0.1 if self.json else 0.2) and done < total:
                return
            self.last[key] = now
            if self.json:
                self._emit({"event": "progress", "done": done, "total": total, "label": label}, project)
                return
            if project is not None:
                if self.bars is not None:
                    self.bars[key] = (project.title, done, total)
                    self._draw_bars()
                return
            frac = (float(done) / total) if total else 1.0
            n = int(frac * 30)
            if label in ("downloading", "uploading"):
                amount = "%s / %s" % (publish.human(done), publish.human(total))
            else:
                amount = "%d / %d files" % (done, total)
                label = "hashing" if label not in ("installing", "done") else label
            meter = paint("[%s%s]" % ("#" * n, "." * (30 - n)), "bar", "err")
            sys.stderr.write("\r  %-12s %s %3d%%  %s   " % (label[:12], meter, frac * 100, amount))
            sys.stderr.flush()
            self.shown = True

    def end(self):
        with self.lock:
            if self.shown:
                sys.stderr.write("\n")
                self.shown = False

    def begin_projects(self):
        """update --all in a terminal: from now on the running projects' progress shares one line (none when
        the output goes to a file or another program)."""
        with self.lock:
            try:
                shown = not self.json and sys.stderr.isatty()
            except (AttributeError, ValueError):
                shown = False
            if shown:
                self.bars = {}

    def project_done(self, project):
        with self.lock:
            if self.bars is not None and self.bars.pop(project.path, None) is not None:
                self._clear_bars()
                self._draw_bars()

    def end_projects(self):
        with self.lock:
            self._clear_bars()
            self.bars = None

    def _draw_bars(self):
        if not self.bars:
            return
        parts = ["%s %d%%" % (name, int(100.0 * done / total) if total else 100) for name, done, total in self.bars.values()]
        width = max(20, shutil.get_terminal_size((80, 24)).columns - 1)
        line = "  " + "   ".join(parts)
        if len(line) > width:
            line = line[:width - 3] + "..."
        sys.stderr.write("\r" + line + " " * max(0, self.drawn - len(line)))
        sys.stderr.flush()
        self.drawn = len(line)

    def _clear_bars(self):
        if self.drawn:
            sys.stderr.write("\r" + " " * self.drawn + "\r")
            sys.stderr.flush()
            self.drawn = 0

    def result(self, data):
        if self.json:
            self._emit({"event": "result", "ok": True, "data": data or {}})

    def error(self, message, code="failed", data=None):
        logs.write("FAILED: %s" % message)
        if self.json:
            self._emit({"event": "error", "message": message, "code": code, "data": data or {}})
            return
        self.end()
        try:
            sys.stdout.flush()              # the lines before it first, also when the output is a pipe
        except (OSError, ValueError):
            pass
        sys.stderr.write("\n%s\n" % paint("%s: %s" % (APP_NAME, message), "err", "err"))


class ProjectOutput(object):
    """One project's share of the output in update --all: its lines and its progress, with its name."""

    def __init__(self, out, project):
        self.out = out
        self.project = project

    def say(self, text, style=None):
        self.out.say(text, style, self.project)

    def progress(self, done, total, label):
        self.out.progress(done, total, label, self.project)

    def end(self):
        pass


OUT = Output()
CANCEL = threading.Event()           # stops a download: set by the person (USER_CANCEL) or by a failed download
USER_CANCEL = threading.Event()      # the person asked to stop ("cancel" on standard input)
STDIN_CLOSED = threading.Event()     # --json: the app closed our standard input (asked us to stop, or went away)


class ProjectCancel(object):
    """One project's stop in update --all: its own failed download stops only its own other downloads,
    while the person's stop (USER_CANCEL) stops every project."""

    def __init__(self):
        self.own = threading.Event()

    def is_set(self):
        return self.own.is_set() or USER_CANCEL.is_set()

    def set(self):
        self.own.set()


# what a command can fail with, short of a bug: said in a line, no traceback
FAILURES = (client.UpdateError, deploy.DeployError, publish.PublishError, ssh.SshError, signing.SignError,
            SourceError, ManifestError, OSError)


def say(text, style=None):
    OUT.say(text, style)


def interactive():
    return not OUT.json and sys.stdin is not None and sys.stdin.isatty()


def ask_yes(question, default=False):
    if not interactive():
        return default
    a = input("%s [%s] " % (question, "Y/n" if default else "y/N")).strip().lower()
    return default if not a else a.startswith("y")


def read_passphrase(a, prompt):
    """The passphrase: the first line of standard input with --passphrase-stdin, else asked at the terminal."""
    if getattr(a, "passphrase_stdin", False):
        line = sys.stdin.readline()
        return line.rstrip("\r\n")
    if not interactive():
        raise CliError("a passphrase is needed: give it on standard input with --passphrase-stdin", "passphrase-needed")
    return getpass.getpass(prompt)


def start_control_reader():
    """In --json mode, a line "cancel" on standard input stops the running download, and the end of
    standard input (the app closed it, or exited) stops the local server."""
    def read():
        try:
            for line in sys.stdin:
                if line.strip().lower() == "cancel":
                    USER_CANCEL.set()
                    CANCEL.set()
        except (OSError, ValueError):
            pass
        STDIN_CLOSED.set()
    threading.Thread(target=read, name="deltapatcher-stdin", daemon=True).start()


def target_from(args, cfg):
    return ssh.Target(args.host or cfg["ssh_host"], args.user or cfg["ssh_user"], args.port or cfg["ssh_port"],
                      args.key if args.key is not None else cfg["ssh_key"],
                      getattr(args, "remote_root", None) or cfg["remote_root"])


def host_keys_data(keys):
    return [{"type": t, "fingerprint": fp} for t, _b, fp in keys]


def confirm_host(target):
    """Make sure the host key is trusted: asked at the terminal the first time; in --json mode an
    unknown host stops with code host-unknown and the fingerprints, for the app to ask."""
    if ssh.is_trusted(target):
        return
    keys = ssh.scan_host(target)
    if OUT.json or not interactive():
        raise CliError("the server %s is not known yet: confirm its host key first" % target.known_name(),
                       "host-unknown", {"host": target.known_name(), "keys": host_keys_data(keys)})
    say("The server %s is not known yet. It offers these host keys:" % target.known_name(), "warn")
    for ktype, _b, fp in keys:
        say("  %-22s %s" % (ktype, paint(fp, "code")))
    say("Compare them with the fingerprints your hosting provider shows, or run on the server:\n"
        "  " + paint("ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub", "code"))
    if not ask_yes("Do they match? Trust this server", default=False):
        raise CliError("not trusted: nothing was sent", "host-refused")
    ssh.trust(target, keys)
    say("Trusted; kept in %s" % target.known_hosts, "ok")


def load_signer(a, path):
    """The signing key at path, with its passphrase when it has one."""
    if not signing.can_sign():
        raise signing.SignError("signing needs the 'cryptography' package (pip install cryptography)")
    if not os.path.isfile(path):
        raise CliError("no signing key at %s: make one with  deltapatcher sign keygen" % path, "no-signing-key")
    passphrase = None
    if signing.needs_passphrase(path):
        passphrase = read_passphrase(a, "Passphrase of the signing key %s: " % path)
    return signing.load_key(path, passphrase)


# ---- commands: each returns the data of its result ------------------------------------------------

def rule_for(src, value):
    """A --protect or --leave-out as a rule (manifest.rule_matches). A path inside the folder being
    published (typed, pasted or dragged in; relative to that folder, or absolute) is that exact file
    or folder; text with * ? or [ in it is a pattern, kept as it is."""
    v = value.strip().strip('"').strip()
    if not v:
        return None
    if any(c in v for c in "*?["):
        return v.replace("\\", "/")
    root = os.path.abspath(src)
    full = os.path.abspath(v if os.path.isabs(v) else os.path.join(root, v))
    try:
        rel = os.path.relpath(full, root)
    except ValueError:                                   # another drive
        rel = ".."
    if rel == "." or rel == ".." or rel.startswith(".." + os.sep):
        raise CliError("%s is not inside the folder you publish (%s): give a file or folder in it" % (value, root),
                       "outside")
    folder = os.path.isdir(full) or v.endswith(("/", "\\"))
    return "/" + rel.replace("\\", "/") + ("/" if folder else "")


def path_list(given, saved):
    """The paths given on the command line, else the saved ones (separated by ;)."""
    items = given if given is not None else str(saved or "").split(";")
    return [x for x in (i.strip() for i in items) if x]


def release_folder(a, cfg):
    """The folder to release: given, else the saved one."""
    src = getattr(a, "src", None) or cfg["publish_src"]
    if not src:
        raise CliError("give the folder to release, or save it once:  deltapatcher config set publish_src=FOLDER",
                       "no-folder")
    return src


def store_of(a, cfg, src=None):
    """The store: given, else the saved one, else DeltaPatcher's own folder for the folder released."""
    store = getattr(a, "store", None) or cfg["store_dir"]
    if store:
        return store
    src = src or cfg["publish_src"]
    if not src:
        raise CliError("give the store, or save the folder to release (its store then lives in DeltaPatcher's own "
                       "folder):  deltapatcher config set publish_src=FOLDER", "no-store")
    return config.default_store(src)


def cmd_publish(a, cfg):
    src = release_folder(a, cfg)
    store = store_of(a, cfg, src)
    rules = {}
    for kind, given, saved in (("protect", a.protect, cfg["protect"]), ("leave out", a.leave_out, cfg["leave_out"])):
        rules[kind] = [r for r in (rule_for(src, p) for p in path_list(given, saved)) if r]
        for r in rules[kind]:
            if r.startswith("/") and not os.path.exists(os.path.join(src, *r.strip("/").split("/"))):
                say("%s: %s is not in %s (yet)" % (kind, r.strip("/"), os.path.abspath(src)), "warn")
    signer = None
    if a.sign is not None:
        signer = load_signer(a, a.sign or cfg["signing_key"] or signing.default_key_path())
    name = a.name if a.name is not None else cfg["name"]
    version = a.version if a.version is not None else cfg["version"]
    res = publish.publish(src, store, name, version, rules["protect"], prune=not a.no_prune,
                          progress=OUT.progress, log=say, signer=signer, htaccess=not a.no_htaccess,
                          leave_out=rules["leave out"])
    OUT.end()
    if signer is None:
        say("Not signed. Players can check who made a release when you publish with --sign.", "warn")
    return {"files": res.files, "bytes": res.bytes, "blobs": res.blobs, "new_blobs": res.new_blobs,
            "new_bytes": res.new_bytes, "pruned": res.pruned, "signed": signer is not None,
            "left_out": res.left_out, "store": os.path.abspath(store), "summary": res.summary()}


def cmd_release(a, cfg):
    """Publish the folder, then send it where players download it from (or serve it from here)."""
    out = {"publish": cmd_publish(a, cfg)}
    store = out["publish"]["store"]
    mode = a.to or cfg["deploy_mode"] or "sftp"
    if mode == "local":
        port = int(cfg["serve_port"] or 8000)
        out["players_url"] = "http://localhost:%d/" % port
        say("Players on this computer update from %s" % paint(out["players_url"], "code"), "head")
        out["serve"] = cmd_serve(argparse.Namespace(store=store, bind="127.0.0.1", port=port, verbose=False), cfg)
        return out
    a.store = store
    if mode == "folder":
        a.to_folder = a.to_folder or cfg["deploy_folder"]
        if not a.to_folder:
            raise CliError("give the folder to send it to (--to-folder), or save it once:  "
                           "deltapatcher config set deploy_folder=FOLDER", "no-target")
    else:
        a.to_folder = None
        if not (a.host or cfg["ssh_host"]) or not (a.remote_root or cfg["remote_root"]):
            raise CliError("save the server once:  deltapatcher config set ssh_host=HOST ssh_user=USER "
                           "remote_root=/var/www/game", "no-target")
    out["deploy"] = cmd_deploy(a, cfg)
    out["players_url"] = cfg["public_url"]
    if cfg["public_url"]:
        say("Players update from %s" % paint(cfg["public_url"], "code"), "head")
    else:
        say("Save the address players update from to see it here:  deltapatcher config set public_url=https://...")
    return out


def cmd_self_update(a, cfg):
    """Is a newer DeltaPatcher out; and, unless --check, put it in place (the command line) or download it
    (--app: the app's file for this system, for the app to install)."""
    try:
        found = selfupdate.latest()
    except selfupdate.UpdateCheckError as e:
        raise CliError(str(e), "offline")
    if not found:
        say("No release of DeltaPatcher is published yet.")
        return {"current": __version__, "latest": "", "newer": False}
    newer = selfupdate.is_newer(found["version"])
    data = {"current": __version__, "latest": found["version"], "newer": newer, "url": found["url"],
            "notes": found["notes"]}
    if not newer:
        say("DeltaPatcher %s is the newest." % __version__, "ok")
        return data
    say("DeltaPatcher %s is out (you have %s): %s" % (found["version"], __version__, paint(found["url"], "code")), "head")
    if a.check:
        data["exit"] = 2
        return data
    kind = a.app or "cli"
    if kind == "cli" and not getattr(sys, "frozen", False):
        raise CliError("this DeltaPatcher runs from source (Python): update it with git pull, or pip install -U",
                       "from-source")
    asset = selfupdate.pick(found["assets"], kind)
    if not asset:
        raise CliError("the release has no %s for this system: download it from %s"
                       % ("app" if a.app else "command line", found["url"]), "no-asset")
    try:
        path = selfupdate.download(asset, progress=OUT.progress)
        OUT.end()
        data["file"] = path
        if kind == "cli":
            selfupdate.replace_running_program(selfupdate.cli_from(path))
            say("Updated to %s: the next start runs it." % found["version"], "ok")
        else:
            say("Downloaded %s" % path, "ok")
    except selfupdate.UpdateCheckError as e:
        raise CliError(str(e), "download-failed")
    return data


def update_notice(cfg):
    """A line when a newer DeltaPatcher is out: asked of GitHub at most once a day, only in a terminal."""
    try:
        if not sys.stdout.isatty():
            return
    except (AttributeError, ValueError):
        return
    newest = selfupdate.cached_check(os.path.dirname(config.path()), enabled=cfg["check_updates"])
    if selfupdate.is_newer(newest):
        say("DeltaPatcher %s is out (you have %s):  %s" % (newest, __version__, paint(prog_name() + " self-update", "code")),
            "warn")


def cmd_serve(a, cfg):
    store = store_of(a, cfg)
    port = a.port if a.port is not None else int(cfg["serve_port"] or 8000)
    srv = serve.make_server(store, a.bind, port, quiet=not a.verbose)
    url = "http://%s:%d/" % (a.bind if a.bind != "0.0.0.0" else "localhost", srv.server_address[1])
    say("Serving %s at %s  (Ctrl+C stops it)" % (os.path.abspath(store), paint(url, "code")))
    if OUT.json:
        OUT._emit({"event": "serving", "url": url})
    if a.bind not in ("127.0.0.1", "localhost", "::1"):
        say("Note: this is reachable from other machines on the network. Use a real web server with HTTPS for the internet.", "warn")
    try:
        if OUT.json:
            # the app stops the server by closing our standard input; so does the app going away
            threading.Thread(target=srv.serve_forever, name="deltapatcher-serve", daemon=True).start()
            STDIN_CLOSED.wait()
            srv.shutdown()
            say("stopped serving")
        else:
            srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return {"url": url}


def cmd_update(a, cfg):
    if getattr(a, "all", False):
        return update_all(a, cfg)
    return update_one(a, cfg, OUT, CANCEL)


def cmd_check(a, cfg):
    a.check = True
    return cmd_update(a, cfg)


def update_one(a, cfg, out, cancel):
    """Update (or check) one folder: the one given, else the project's. Its messages go to `out`; `cancel`
    stops its downloads. cfg is the project's settings: nothing here reads any other."""
    install = a.install or cfg["install_dir"]
    if not install:
        raise CliError("give the folder to update, or save it once:  deltapatcher config set install_dir=FOLDER",
                       "no-folder")
    url = a.source or cfg["source_url"] or DEFAULT_URL
    if insecure_remote(url):
        out.say("Warning: %s is plain http to another machine. Every file is checked against the manifest's SHA-256, "
                "but the manifest itself could be changed on the way. Use https, or a signed release, for anything but "
                "a trusted network." % url)
    headers = dict(h.split(":", 1) for h in (a.header or []) if ":" in h)
    headers = {k.strip(): v.strip() for k, v in headers.items()}
    workers = a.workers or int(cfg.get("workers") or 4)
    plan = client.update(url, install, check_only=a.check, workers=max(1, workers), headers=headers,
                         progress=out.progress, log=out.say, cancel=cancel,
                         trust_key=a.trust_key or cfg.get("trust_key") or None)
    out.end()
    need = plan.download()
    data = {"empty": plan.empty(), "summary": plan.summary(), "add": len(plan.add), "replace": len(plan.replace),
            "remove": len(plan.remove), "same": plan.same, "download_blobs": len(need),
            "download_bytes": sum(need.values()), "kept": plan.kept_changed + plan.protected,
            "publisher_key": client.State(os.path.abspath(install)).publisher_key}
    if a.check and not plan.empty():
        data["exit"] = 2
    return data


def project_args(a):
    """update --all: the arguments of one project's update, its folder and source left to its own settings."""
    pa = argparse.Namespace(**vars(a))
    pa.install, pa.source, pa.trust_key, pa.header, pa.all = None, None, None, None, False
    return pa


def outcome(row, check):
    """A project's line in the summary of update --all, and its colour."""
    if not row["ok"]:
        return ("stopped" if row["code"] == "stopped" else "FAILED: %s" % row["message"]), "err"
    d = row["data"]
    if d["empty"]:
        return "up to date", "ok"
    parts = ["%d new" % d["add"], "%d changed" % d["replace"]] + (["%d removed" % d["remove"]] if d["remove"] else [])
    if check:
        return "an update is waiting: %s, %s to download" % (", ".join(parts), publish.human(d["download_bytes"])), "warn"
    return "updated: %s, %s downloaded" % (", ".join(parts), publish.human(d["download_bytes"])), "ok"


def update_all(a, cfg):
    """update --all and check --all: every project with a folder to update, up to --jobs of them at the same
    time, each in a thread of its own with its own settings, output and stop, so one failing leaves the
    others running. One failed project makes the whole run fail, after every other one has finished."""
    if a.install or a.source or a.trust_key or a.header:
        raise CliError("--all updates each project from its own settings: leave out the folder, --source, "
                       "--trust-key and --header", "all-with-folder")
    run, skipped = [], []
    for p in projects.all_projects():
        if p.missing:
            skipped.append((p, "its settings file is gone"))
            continue
        pcfg = config.load(p)
        if pcfg["install_dir"] and pcfg["source_url"]:
            run.append((p, pcfg))
        else:
            skipped.append((p, "no folder to update"))
    check = bool(a.check)
    jobs = a.jobs if a.jobs is not None else cfg["parallel_updates"]
    jobs = max(1, min(int(jobs or 1), max(1, len(run))))
    data = {"check": check, "jobs": jobs, "projects": [], "skipped": [dict(p.tag(), reason=why) for p, why in skipped]}
    if OUT.json:
        OUT._emit({"event": "queue", "check": check, "jobs": jobs, "run": [p.tag() for p, _c in run],
                   "skipped": data["skipped"]})
    if not run:
        say("No project has a folder to update yet:  deltapatcher --project NAME config set install_dir=FOLDER "
            "source_url=ADDRESS", "warn")
        return data
    say("%s %d project%s, %d at a time" % ("Checking" if check else "Updating", len(run), "" if len(run) == 1 else "s",
                                            jobs), "head")
    for p, why in skipped:
        say("  skipped %s: %s" % (p.title, why))

    def one(p, pcfg):
        if USER_CANCEL.is_set():
            res = {"ok": False, "message": "stopped before it started", "code": "stopped"}
        else:
            if OUT.json:
                OUT._emit({"event": "project-start"}, p)
            try:
                res = {"ok": True, "data": update_one(project_args(a), pcfg, ProjectOutput(OUT, p), ProjectCancel())}
            except CliError as e:
                res = {"ok": False, "message": str(e), "code": e.code}
            except FAILURES as e:
                res = {"ok": False, "message": str(e), "code": "stopped" if USER_CANCEL.is_set() else "failed"}
            except Exception as e:          # a surprise in one project must not stop the others
                logs.write(traceback.format_exc(), "" if p.main else p.title)
                res = {"ok": False, "message": "%s: %s" % (type(e).__name__, e), "code": "internal"}
        if not res["ok"]:
            logs.write("FAILED: %s" % res["message"], "" if p.main else p.title)
        OUT.project_done(p)
        if OUT.json:
            OUT._emit(dict({"event": "project-end"}, **res), p)
        return res

    OUT.begin_projects()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=jobs)
    futures = [pool.submit(one, p, pcfg) for p, pcfg in run]
    try:
        waiting = set(futures)
        while waiting:                      # in short waits, so Ctrl+C is seen
            _done, waiting = concurrent.futures.wait(waiting, timeout=0.25)
    except KeyboardInterrupt:
        USER_CANCEL.set()
        CANCEL.set()
        concurrent.futures.wait(futures)
        raise
    finally:
        pool.shutdown(wait=True)
        OUT.end_projects()

    failed = []
    for (p, _c), f in zip(run, futures):
        row = dict(p.tag(), **f.result())
        data["projects"].append(row)
        if not row["ok"]:
            failed.append(p.title)
    width = max(len(r["project"]) for r in data["projects"])
    for row in data["projects"]:
        text, style = outcome(row, check)
        say("  %-*s  %s" % (width, row["project"], text), style)
    data["failed"] = len(failed)
    if failed:
        if USER_CANCEL.is_set():
            raise CliError("stopped: %d of %d projects did not finish" % (len(failed), len(run)), "stopped", data)
        raise CliError("%d of %d projects failed: %s" % (len(failed), len(run), ", ".join(failed)),
                       "projects-failed", data)
    if check and any(not r["data"]["empty"] for r in data["projects"]):
        data["exit"] = 2
    return data


def cmd_rollback(a, cfg):
    install = a.install or cfg["install_dir"]
    if not install:
        raise CliError("give the folder to roll back, or save it once:  deltapatcher config set install_dir=FOLDER",
                       "no-folder")
    n = client.rollback(install, log=say)
    return {"undone": n}


def cmd_deploy(a, cfg):
    store = store_of(a, cfg)
    if a.to_folder:
        tgt = deploy.FolderTarget(a.to_folder)
    else:
        t = target_from(a, cfg)
        if t.key and os.path.isfile(t.key):
            probs = ssh.permission_problems(t.key)
            if probs:
                if OUT.json or not ask_yes("Your private key %s is readable by others (%s). Restrict it to your account now"
                                          % (t.key, "; ".join(probs)), default=True):
                    raise CliError("the private key %s can be read by other accounts: %s" % (t.key, "; ".join(probs)),
                                   "key-permissions", {"key": t.key, "problems": probs})
                ssh.fix_permissions(t.key)
        confirm_host(t)
        tgt = deploy.SftpTarget(t)
    res = deploy.deploy(store, tgt, prune=not a.no_prune, dry_run=a.dry_run, progress=OUT.progress, log=say)
    OUT.end()
    return {"named": res.named, "had": res.had, "sent": res.sent, "sent_bytes": res.sent_bytes,
            "removed": res.removed, "atomic": res.atomic, "dry_run": res.dry_run, "summary": res.summary()}


def _new_passphrase(a, what):
    if getattr(a, "passphrase_stdin", False):
        p = read_passphrase(a, "")
        if p and len(p) < 10:
            raise CliError("use at least 10 characters for the passphrase", "passphrase-short")
        return p
    if not interactive():
        raise CliError("a passphrase is needed: run this in a terminal, or give it with --passphrase-stdin", "passphrase-needed")
    say("A passphrase protects the %s if the file is ever copied." % what)
    while True:
        p1 = getpass.getpass("Passphrase (empty for none, not recommended): ")
        if not p1:
            if ask_yes("Really make it without a passphrase", default=False):
                return ""
            continue
        if len(p1) < 10:
            say("Use at least 10 characters.", "warn")
            continue
        if getpass.getpass("Again: ") == p1:
            return p1
        say("They differ; try again.", "warn")


def cmd_sign(a, cfg):
    path = a.path or cfg["signing_key"] or signing.default_key_path()
    if a.sign_cmd == "keygen":
        if not signing.can_sign():
            raise signing.SignError("making a signing key needs the 'cryptography' package (pip install cryptography)")
        if os.path.exists(path):
            raise CliError("%s already exists: an existing signing key is never replaced" % path, "exists")
        say("A new signing key at %s. Only you need it; it never leaves this computer." % path, "head")
        pub = signing.generate_key(path, _new_passphrase(a, "signing key"))
        say("Made %s. Its public key, to give to players and put in launchers:" % path, "ok")
        say("  " + paint(pub, "code"))
        return {"path": path, "public_key": pub}
    if a.sign_cmd == "pubkey":
        key = load_signer(a, path)
        pub = signing.public_key_text(key)
        if not OUT.json:
            print(pub)
        return {"path": path, "public_key": pub}
    if a.sign_cmd == "status":
        exists = os.path.isfile(path)
        return {"path": path, "exists": exists, "can_sign": signing.can_sign(),
                "has_passphrase": signing.needs_passphrase(path) if exists else False}
    raise CliError("sign what? keygen, pubkey or status")


def cmd_ssh(a, cfg):
    c = a.ssh_cmd
    if c == "install-key":
        t = target_from(a, cfg)
        if not t.key:
            t.key = ssh.default_key_path()
        if not os.path.isfile(t.key):
            raise CliError("there is no key at %s yet: make one first (ssh keygen)" % t.key, "no-key")
        if not t.user:
            raise CliError("give the user to log in as (--user), or save it:  deltapatcher config set ssh_user=USER",
                           "no-user")
        confirm_host(t)                    # the host key first: the password goes only to the server you trust
        password = read_passphrase(a, "Password of %s on %s (used once, to add the key; never kept): "
                                   % (t.user, t.host))
        result = ssh.install_key(t, password)
        say("Your key is on %s (%s): DeltaPatcher logs in with it from now on." % (t.host, result), "ok")
        return {"host": t.host, "user": t.user, "result": result}
    if c in ("keygen", "pubkey", "unlock", "fix-permissions"):
        path = a.path or cfg["ssh_key"] or ssh.default_key_path()
    if c == "keygen":
        if os.path.exists(path):
            raise CliError("%s already exists: an existing key is never replaced" % path, "exists")
        if ssh.can_generate() and (interactive() or a.passphrase_stdin):
            say("A new ed25519 key at %s." % path, "head")
            ssh.generate_key(path, _new_passphrase(a, "key"), "deltapatcher@" + socket.gethostname())
        else:
            ssh.generate_key_interactive(path)
        line = ssh.authorized_keys_line(path)
        say("Made %s and %s.pub" % (path, path), "ok")
        say("Next: put this line in ~/.ssh/authorized_keys on the server (it only allows SFTP):")
        say("  " + paint(line, "code"))
        return {"path": path, "authorized_keys_line": line}
    if c == "pubkey":
        line = ssh.authorized_keys_line(path, sftp_only=not a.full)
        if not OUT.json:
            print(line)
        return {"path": path, "authorized_keys_line": line}
    if c == "unlock":
        if not ssh.key_has_passphrase(path):
            say("%s has no passphrase: nothing to unlock." % path)
            return {"path": path, "unlocked": False}
        if a.passphrase_stdin:
            ssh.agent_add_with(path, read_passphrase(a, ""))
        else:
            ssh.agent_add(path, use_dialog=False)
        say("The key is in ssh-agent until you log out.", "ok")
        return {"path": path, "unlocked": True}
    if c == "fix-permissions":
        ssh.fix_permissions(path)
        say("Restricted %s to your account." % path, "ok")
        return {"path": path}
    if c == "status":
        path = a.key if a.key is not None else cfg["ssh_key"]
        data = {"key": path, "key_exists": bool(path) and os.path.isfile(path), "agent": ssh.agent_state()}
        if data["key_exists"]:
            try:
                data["has_passphrase"] = ssh.key_has_passphrase(path)
            except ssh.SshError as e:
                data["key_error"] = str(e)
            data["permission_problems"] = ssh.permission_problems(path)
            data["in_agent"] = data["agent"] == "keys" and ssh.agent_has(path)
        host = a.host or cfg["ssh_host"]
        if host:
            # the key's status must not depend on the host fields: a half-typed host is reported, not fatal
            try:
                t = ssh.Target(host, a.user or cfg["ssh_user"], a.port or cfg["ssh_port"], path)
                data["host"] = t.known_name()
                data["host_trusted"] = ssh.is_trusted(t)
            except ssh.SshError as e:
                data["host_error"] = str(e)
        say(json.dumps(data) if not OUT.json else "key and host status")
        return data
    t = target_from(a, cfg)
    if c == "scan":
        keys = ssh.scan_host(t)
        state = ssh.host_key_state(t, keys)
        for ktype, _b, fp in keys:
            say("  %-22s %s" % (ktype, paint(fp, "code")))
        if state == "changed":
            say("The server's host key is NOT the one you confirmed before. If it was reinstalled, check its new "
                "fingerprint; otherwise do not connect.", "err")
        return {"host": t.known_name(), "state": state, "trusted": state == "trusted", "keys": host_keys_data(keys)}
    if c == "trust":
        if a.forget:
            ssh.forget(t)
            say("Forgot the host key of %s" % t.known_name(), "ok")
            return {"host": t.known_name(), "forgotten": True}
        if a.accept:
            # the fingerprints the person was shown: the host is trusted only if it still offers exactly those
            wanted = set(x.strip() for x in a.accept.split(",") if x.strip())
            keys = ssh.scan_host(t)
            offered = set(fp for _t, _b, fp in keys)
            if not wanted or not wanted <= offered:
                raise CliError("the server now offers other host keys than the ones you confirmed: not trusted",
                               "host-changed", {"keys": host_keys_data(keys)})
            ssh.trust(t, [k for k in keys if k[2] in wanted])
            say("Trusted %s; kept in %s" % (t.known_name(), t.known_hosts), "ok")
            return {"host": t.known_name(), "trusted": True}
        confirm_host(t)
        return {"host": t.known_name(), "trusted": True}
    if c == "test":
        confirm_host(t)
        ok, text = ssh.test_connection(t)
        if not ok:
            raise CliError(text, "connect-failed")
        say(text, "ok")
        return {"ok": True, "text": text}
    raise CliError("ssh what? keygen, pubkey, unlock, status, scan, trust, test or fix-permissions")


def project_of(a):
    """The project a command works on (main() picks it); Default for a command run without main()."""
    return getattr(a, "proj", None) or projects.main_project()


def cmd_config(a, cfg):
    project = project_of(a)
    p = config.ensure()
    own = p if project.main else project.path           # the project's own settings file
    if a.config_cmd == "set":
        changes = {}
        for item in a.values or []:
            if "=" not in item:
                raise CliError("config set takes key=value pairs: %r" % item)
            k, v = item.split("=", 1)
            k = k.strip()
            if k not in config.DEFAULTS:
                raise CliError("there is no setting %r" % k, "no-such-setting")
            changes[k] = config._convert(k, v)
        if changes.get("title"):
            changes["title"] = projects.check_name(changes["title"], project)
        config.save(changes, project)     # only these keys change, each in its own file; everything else stays
        return {"path": own, "settings": config.load(project)}
    data = {"path": own, "main_path": p, "project": dict(project.tag(), kind=project.kind),
            "log": logs.path() or logs.default_path(), "log_dir": os.path.dirname(logs.path() or logs.default_path()),
            "settings": config.load(project), "schema": [{"section": s, "key": k, "default": d, "what": w}
                                                         for s, k, d, w in config.SCHEMA]}
    if not OUT.json:
        if not project.main:
            say("Project:  %s, %s" % (project.title, paint(own, "code")))
        say("Settings: %s" % paint(p, "code"))
        say("Log:      %s" % paint(data["log"], "code"))
        if a.show:
            with open(own, "r", encoding="utf-8") as f:
                print(f.read().rstrip())
    return data


def cmd_projects(a, cfg):
    every = projects.all_projects()
    chosen = projects.current()
    rows = []
    for p in every:
        row = projects.describe(p)
        row["current"] = projects.same(p, chosen)
        rows.append(row)
    data = {"projects": rows, "main": every[0].path, "folder": projects.folder(), "current": chosen.path,
            "parallel_updates": max(1, int(cfg["parallel_updates"] or 1))}
    if not OUT.json:
        say("%d project%s; DeltaPatcher's own settings are in %s" % (len(rows), "" if len(rows) == 1 else "s",
                                                                    paint(every[0].path, "code")))
        for p, row in zip(every, rows):
            print()
            say("%s  %s%s" % (paint(p.title, "head"), paint(p.path, "code"),
                              "  (its file is gone)" if row["missing"] else "  (added)" if p.kind == "added" else ""))
            say("  update   %s" % (("%s  from  %s" % (row["install_dir"], row["source_url"])) if row["install_dir"]
                                   else "not set up"))
            say("  release  %s" % (("%s  to  %s" % (row["publish_src"], row["where"] or "nowhere yet"))
                                   if row["publish_src"] else "not set up"))
        print()
        if len(rows) > 1:
            say("Commands work on Default unless --project names another:  %s"
                % paint('%s --project "%s" update' % (prog_name(), rows[1]["title"]), "code"))
        else:
            say("Another game or mod gets a project of its own:  %s" % paint(prog_name() + " project new NAME", "code"))
    return data


def cmd_project(a, cfg):
    c = a.project_cmd
    if c == "new":
        p = projects.new(a.name)
        say("Made the project %s: %s" % (p.title, paint(p.path, "code")), "ok")
        if not OUT.json:
            say("Set it up:  %s" % paint('%s --project "%s" config set install_dir=FOLDER source_url=ADDRESS'
                                         % (prog_name(), p.title), "code"))
        return projects.describe(p)
    if c == "add":
        p = projects.add(a.file, a.name)
        say("Added the project %s: %s" % (p.title, paint(p.path, "code")), "ok")
        return projects.describe(p)
    if c == "rename":
        p = projects.resolve(a.target)
        old = p.title
        projects.rename(p, a.new)
        say("Renamed the project %s to %s" % (old, p.title), "ok")
        return projects.describe(p)
    if c == "remove":
        p = projects.resolve(a.target, allow_missing=True)
        projects.check_removable(p)
        if not a.yes:
            if not interactive():
                raise CliError("removing a project asks first: add --yes to remove %s without asking" % p.title,
                               "confirm-needed", p.tag())
            if p.kind == "folder":
                say("Remove the project %s? Its settings file is deleted: %s" % (p.title, p.path), "warn")
            else:
                say("Remove the project %s from the list? Its file stays where it is: %s" % (p.title, p.path), "warn")
            try:
                sure = ask_yes("Remove it", default=False)
            except EOFError:
                sure = False
            if not sure:
                raise CliError("not removed", "cancelled")
        done = projects.remove(p)
        if done == "deleted":
            say("Removed the project %s and deleted its settings file." % p.title, "ok")
        else:
            say("Took the project %s off the list; its file stays where it is." % p.title, "ok")
        return dict(p.tag(), removed=done)
    raise CliError("project what? new, add, rename or remove")


def cmd_version(a, cfg):
    if not OUT.json:
        print("%s %s" % (APP_NAME, __version__))
    return {"name": APP_NAME, "version": __version__, "can_sign": signing.can_sign()}


# ---- arguments ----------------------------------------------------------------------------------

def prog_name():
    """The name to type: the built program's own file name (deltapatcher-cli-windows-x64, say), else deltapatcher."""
    if getattr(sys, "frozen", False):
        return os.path.splitext(os.path.basename(sys.executable))[0]
    return "deltapatcher"


def colorize_help(text):
    """The help in colour, painted after argparse has laid it out (so its columns stay aligned):
    headings white, commands, options and examples purple. Unchanged when colour is off."""
    out = []
    for line in text.splitlines(True):
        s = line.rstrip("\r\n")
        end = line[len(s):]
        if s.startswith("usage:"):
            s = paint("usage:", "head") + s[len("usage:"):]
        elif s and not s[0].isspace() and (s.endswith(":") or s.startswith(APP_NAME + " ")):
            s = paint(s, "head")                       # a section heading, or the title line
        else:
            m = (re.match(r"^( {2})(\S.*?)( {2,}\(.*\))?$", s) if s.startswith("  " + prog_name() + " ") else
                 re.match(r"^( {2}| {4})(\{[^}]*\}|-[^ ,]+(?: [A-Z_.\[\]]+)?(?:, -[^ ,]+(?: [A-Z_.\[\]]+)?)*|[a-z][\w-]*)(?=  |$)(.*)$", s))
            if m:
                s = m.group(1) + paint(m.group(2), "code") + (m.group(3) or "")
        out.append(s + end)
    return "".join(out)


class _Parser(argparse.ArgumentParser):
    def format_help(self):
        return colorize_help(argparse.ArgumentParser.format_help(self))


def _own_console():
    """True on Windows when this program has its console window to itself: it was started from Explorer
    (a double-click) or a shortcut, not typed in a terminal, and its window closes the moment it ends."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetConsoleProcessList.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                   ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        pids = (wintypes.DWORD * 16)()
        n = k32.GetConsoleProcessList(pids, 16)
        if not 0 < n <= 16:
            return False
        # Every process on this console is this program (a built one runs as a launcher and its child), the
        # py launcher that started it from source, or the cmd.exe of a double-clicked cli\deltapatcher.bat
        # (the .bat says so: DELTAPATCHER_WINDOW=1). Anything else is a shell someone types in.
        bat_window = os.environ.get("DELTAPATCHER_WINDOW") == "1"
        for pid in pids[:n]:
            h = k32.OpenProcess(0x1000, False, pid)           # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            try:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return False
                name = os.path.basename(buf.value).lower()
                if os.path.samefile(buf.value, sys.executable):
                    continue
                if name in ("py.exe", "pyw.exe") and not getattr(sys, "frozen", False):
                    continue
                if name == "cmd.exe" and bat_window:
                    continue
                return False
            finally:
                k32.CloseHandle(h)
        return True
    except (AttributeError, OSError, ValueError):
        return False


def window_of_its_own():
    """A console window this program has to itself and a person reads: double-clicked, or started from a
    shortcut or a launcher's own window. Not a terminal (it stays open anyway), and never when another
    program reads the output (it would wait for an Enter nobody can press)."""
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
    except (AttributeError, ValueError):
        return False
    return _own_console()


def wait_to_close():
    try:
        input("\n" + paint("Press Enter to close.", "head") + " (--close closes it without waiting.)")
    except (EOFError, KeyboardInterrupt):
        pass


def build_parser():
    extra = {"color": False} if sys.version_info >= (3, 14) else {}     # 3.14's own colours: colorize_help does it
    p = _Parser(prog=prog_name(), description="%s %s: delta patching with SHA-256 blobs." % (APP_NAME, __version__),
                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter, **extra)
    p.add_argument("--version", action="version", version="%s %s" % (APP_NAME, __version__))
    p.add_argument("--log", metavar="FILE", help="write the log here (default: %s)" % logs.default_path())
    p.add_argument("--no-log", action="store_true", help="write no log file")
    p.add_argument("--no-color", action="store_true", help="plain text (colour is used only in a terminal that shows it)")
    p.add_argument("--json", action="store_true", help="JSON lines on standard output, for programs (the DeltaPatcher app)")
    p.add_argument("--close", action="store_true", help="close as soon as the command is done: a window of its own "
                                                        "(double-clicked, a shortcut) otherwise waits for Enter")
    p.add_argument("--project", metavar="NAME", help=PROJECT_HELP + "; also DELTAPATCHER_PROJECT")
    sub = p.add_subparsers(dest="cmd")

    def pass_stdin(sp):
        sp.add_argument("--passphrase-stdin", action="store_true", help="read the passphrase from the first line of standard input")

    def ssh_args(sp, root=True):
        sp.add_argument("--host")
        sp.add_argument("--user")
        sp.add_argument("--port", type=int)
        sp.add_argument("--key", help="private key file (default: the saved one, else %s)" % ssh.default_key_path())
        if root:
            sp.add_argument("--remote-root", help="the folder on the server the web server serves")

    def release_args(sp):
        sp.add_argument("--name", help="the release's name, shown to players (default: the saved one)")
        sp.add_argument("--version", dest="version", help="the release's version, shown to players (default: the saved "
                                                          "one); the same version again is fine (a shadow patch)")
        sp.add_argument("--protect", action="append", metavar="PATH",
                        help="a file or folder players keep once they have it, such as their settings: a path inside "
                             "SRC, or a pattern like *.ini (repeatable; default: the saved list)")
        sp.add_argument("--leave-out", action="append", metavar="PATH",
                        help="a file or folder to leave out of the release: a path inside SRC, or a pattern like "
                             "*.log (repeatable; default: the saved list)")
        sp.add_argument("--sign", nargs="?", const="", metavar="KEYFILE",
                        help="sign the release (optional; default key: the saved one, else %s)"
                             % signing.default_key_path())
        sp.add_argument("--no-htaccess", action="store_true", help="do not write the .htaccess for Apache")
        sp.add_argument("--no-prune", action="store_true", help="keep blobs no release names")
        pass_stdin(sp)

    sub.add_parser("menu", help="plain questions instead of commands: release, set up where releases go, update a folder")

    s = sub.add_parser("self-update", help="update DeltaPatcher itself to the newest release")
    s.add_argument("--check", action="store_true", help="only say whether a newer version is out (exit 2 when it is)")
    s.add_argument("--app", choices=("setup", "zip", "appimage", "dmg"),
                   help="download the app's file for this system instead (the app's own updater uses it)")

    s = sub.add_parser("release", help="publish a folder and send it where players download it from (the saved "
                                       "server, a folder, or this computer)")
    s.add_argument("src", nargs="?", help="the folder to release (default: the saved one)")
    release_args(s)
    s.add_argument("--to", choices=("sftp", "folder", "local"),
                   help="sftp: the saved server; folder: --to-folder or the saved one; local: serve it here for "
                        "testing (default: the saved choice)")
    s.add_argument("--to-folder", help="with --to folder: the folder to copy it to")
    ssh_args(s)
    s.add_argument("--dry-run", action="store_true", help="say what would be sent; send nothing")

    s = sub.add_parser("publish", help="turn a folder into a store (manifest.json + blobs/), sent nowhere")
    s.add_argument("src", nargs="?", help="the folder to release (default: the saved one)")
    s.add_argument("store", nargs="?", help="where to write it (default: the saved store, else DeltaPatcher's own "
                                            "folder)")
    release_args(s)

    s = sub.add_parser("serve", help="serve a store over http (default http://127.0.0.1:8000/)")
    s.add_argument("store", nargs="?", help="default: the saved store, else the saved folder's")
    s.add_argument("--bind", default="127.0.0.1")
    s.add_argument("--port", type=int, help="default: the saved serve_port, else 8000")
    s.add_argument("--verbose", action="store_true")

    def update_args(sp, check_option):
        sp.add_argument("install", nargs="?", help="the folder (default: the saved one)")
        sp.add_argument("--source", help="http(s) URL or folder of the store (default: the saved one, else %s)"
                                         % DEFAULT_URL)
        if check_option:
            sp.add_argument("--check", action="store_true",
                            help="say what would change; change nothing (exit 2 when out of date)")
        sp.add_argument("--trust-key", metavar="KEY", help="the publisher key (ed25519:...) the release must be signed "
                                                          "with; off: do not check signatures")
        sp.add_argument("--workers", type=int, default=None,
                        help="downloads at the same time (default: the setting, else 4)")
        sp.add_argument("--header", action="append", help="an extra request header, 'Name: value' (repeatable)")
        sp.add_argument("--all", action="store_true", help="every project with a folder to update, several at the same "
                                                           "time, each from its own settings")
        sp.add_argument("--jobs", type=int, metavar="N", help="with --all: how many projects at the same time (default: "
                                                              "the setting parallel_updates, else 3)")

    s = sub.add_parser("update", help="bring a folder up to the source's release")
    update_args(s, True)

    s = sub.add_parser("check", help="say what an update would change; change nothing (exit 2 when out of date)")
    update_args(s, False)

    s = sub.add_parser("rollback", help="undo the last update of a folder")
    s.add_argument("install", nargs="?", help="the folder (default: the saved one)")

    s = sub.add_parser("deploy", help="send a store to a server (sftp) or a folder, only the missing blobs")
    s.add_argument("store", nargs="?", help="default: the saved store, else the saved folder's")
    s.add_argument("--to-folder", help="deploy to this folder instead of a server")
    ssh_args(s)
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--no-prune", action="store_true")

    s = sub.add_parser("sign", help="the key that signs your releases")
    ss = s.add_subparsers(dest="sign_cmd")
    for name, helptext in (("keygen", "make a signing key (asks a passphrase)"),
                           ("pubkey", "print the public key players use to check your releases"),
                           ("status", "is there a signing key, and does it have a passphrase")):
        k = ss.add_parser(name, help=helptext)
        k.add_argument("--path")
        pass_stdin(k)

    s = sub.add_parser("ssh", help="keys and host keys for deploys")
    ss = s.add_subparsers(dest="ssh_cmd")
    k = ss.add_parser("keygen", help="make a new ed25519 key with a passphrase")
    k.add_argument("--path")
    pass_stdin(k)
    k = ss.add_parser("pubkey", help="print the authorized_keys line for the key")
    k.add_argument("--path")
    k.add_argument("--full", action="store_true", help="allow more than SFTP (not needed by deltapatcher)")
    k = ss.add_parser("unlock", help="add the key to ssh-agent (asks the passphrase once)")
    k.add_argument("--path")
    pass_stdin(k)
    k = ss.add_parser("fix-permissions", help="make the private key readable by this account only")
    k.add_argument("--path")
    k = ss.add_parser("status", help="the key (passphrase, permissions, ssh-agent) and whether the host is trusted")
    ssh_args(k, root=False)
    k = ss.add_parser("scan", help="the host keys a server offers, with their fingerprints")
    ssh_args(k, root=False)
    k = ss.add_parser("trust", help="check and keep a server's host key")
    ssh_args(k, root=False)
    k.add_argument("--forget", action="store_true")
    k.add_argument("--accept", metavar="FINGERPRINTS", help="trust without asking, if the server offers exactly these (comma-separated)")
    k = ss.add_parser("test", help="log in with sftp")
    ssh_args(k)
    k = ss.add_parser("install-key", help="put your key on the server, logging in once with its password (never "
                                          "kept); the host key is checked first")
    ssh_args(k, root=False)
    pass_stdin(k)

    s = sub.add_parser("config", help="the settings file: where it is (made, every setting explained, if missing)")
    cs = s.add_subparsers(dest="config_cmd")
    s.add_argument("--show", action="store_true", help="print the file too")
    cs.add_parser("get", help="every setting (with --json, for programs)")
    k = cs.add_parser("set", help="change settings: key=value ...")
    k.add_argument("values", nargs="*")

    sub.add_parser("projects", help="every project: the folder it updates and where from, the folder it releases and "
                                    "where to")
    s = sub.add_parser("project", help="make, add, rename or remove a project (a settings file of its own)")
    ss = s.add_subparsers(dest="project_cmd")
    k = ss.add_parser("new", help="a new project, its settings file in the projects folder beside the main one")
    k.add_argument("name", help="its name: letters, digits, spaces and - _ . ( ), up to %d characters"
                                % projects.NAME_MAX)
    k = ss.add_parser("add", help="list a project file kept elsewhere (in a game's folder, on a share)")
    k.add_argument("file")
    k.add_argument("--name", help="give it this name (written into the file)")
    k = ss.add_parser("rename", help="give a project another name (its file stays where it is)")
    k.add_argument("target", metavar="NAME", help="the project: its name or its settings file")
    k.add_argument("new", metavar="NEW")
    k = ss.add_parser("remove", help="remove a project: one in the projects folder is deleted, an added one only "
                                     "leaves the list")
    k.add_argument("target", metavar="NAME", help="the project: its name or its settings file")
    k.add_argument("--yes", action="store_true", help="do not ask first")

    sub.add_parser("version", help="the version")
    _project_everywhere(p)
    return p


PROJECT_HELP = "the project whose settings to use: its name or its settings file (default: Default)"


def _project_everywhere(parser):
    """--project after the command works too (deltapatcher update --project NAME), at every level."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sp in action.choices.values():
                sp.add_argument("--project", metavar="NAME", default=argparse.SUPPRESS, help=PROJECT_HELP)
                _project_everywhere(sp)


HANDLERS = {"publish": cmd_publish, "release": cmd_release, "serve": cmd_serve, "update": cmd_update,
            "check": cmd_check, "rollback": cmd_rollback, "deploy": cmd_deploy, "ssh": cmd_ssh, "sign": cmd_sign,
            "config": cmd_config, "projects": cmd_projects, "project": cmd_project, "version": cmd_version,
            "self-update": cmd_self_update}
# commands that work on no single project
WITHOUT_PROJECT = ("version", "self-update", "projects", "project")


def split_line(line):
    """A typed command line as a program receives it: quotes keep a path with spaces together, and on
    Windows a backslash is part of the path, as it is for any Windows program."""
    if os.name != "nt":
        import shlex
        return shlex.split(line)
    import ctypes
    from ctypes import wintypes
    shell32 = ctypes.WinDLL("shell32")
    kernel32 = ctypes.WinDLL("kernel32")
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    n = ctypes.c_int()
    words = shell32.CommandLineToArgvW("x " + line, ctypes.byref(n))   # its first word is read as a program
    if not words:
        raise ValueError("that line could not be read")
    try:
        return [words[i] for i in range(1, n.value)]
    finally:
        kernel32.LocalFree(ctypes.cast(words, ctypes.c_void_p))


def prompt_loop(back="closes this window", project=None):
    """A prompt that takes one command after another until exit (the menu's "Type commands yourself").
    project: the one the menu works on, which the commands use unless they name another."""
    print()
    print(paint("Type a command and press Enter, for example:", "head"))
    print("  " + paint('update "D:\\My Game" --source https://example.com/game/', "code"))
    print("help lists the commands, help update explains one, exit %s." % back)
    if project is not None and not project.main:
        print("Commands work on the project %s (--project NAME picks another)." % paint(project.title, "head"))
    names = {prog_name().lower(), "deltapatcher", "deltapatcher-cli"}
    while True:
        try:
            line = input("\n" + paint("deltapatcher>", "code") + " ")
        except EOFError:
            return
        except KeyboardInterrupt:
            print()
            continue
        try:
            argv = split_line(line.strip()) if line.strip() else []
        except ValueError as e:
            print(paint(str(e), "err"))
            continue
        if argv and os.path.splitext(argv[0])[0].lower() in names:
            argv = argv[1:]                                 # copied from an example: the program's name first
        if not argv:
            continue
        if argv[0].lower() in ("exit", "quit"):
            return
        if argv[0].lower() == "help":
            argv = argv[1:] + ["--help"]
        if "--json" in argv:
            print(paint("--json is for programs (the DeltaPatcher app), not for typing here.", "warn"))
            continue
        try:
            main(argv, prompt=False, project=project)
        except SystemExit:
            pass                                            # the help was printed, or a mistake explained
        except KeyboardInterrupt:
            print()


def pick_project(a, given=None):
    """The project a command works on: --project, else `given` (the menu's, for a command typed at its
    prompt), else DELTAPATCHER_PROJECT, else Default. Never the app's choice: a launcher's command line
    means the same project whatever the app shows."""
    if getattr(a, "project", None):
        return projects.resolve(a.project)
    if given is not None:
        return given
    return projects.resolve(os.environ.get("DELTAPATCHER_PROJECT"))


def main(argv=None, prompt=True, project=None):
    """The command line. prompt=False: a command typed at the prompt, whose window already stays open.
    project: the menu's project, for a command typed at its prompt."""
    global OUT
    argv = sys.argv[1:] if argv is None else argv
    if prompt and getattr(sys, "frozen", False):
        selfupdate.remove_leftover()                            # the old program a Windows self-update left
    color_enable(not {"--no-color", "--json"} & set(argv))     # the help is printed while the arguments are read
    # in a window of its own the output would vanish with the window: it waits for Enter, unless --close
    stay = prompt and not {"--close", "--json"} & set(argv) and window_of_its_own()
    p = build_parser()
    try:
        a = p.parse_args(argv)
        if a.cmd == "ssh" and not a.ssh_cmd:
            p.parse_args(["ssh", "--help"])
        if a.cmd == "sign" and not a.sign_cmd:
            p.parse_args(["sign", "--help"])
        if a.cmd == "project" and not a.project_cmd:
            p.parse_args(["project", "--help"])
    except SystemExit:
        if stay:
            wait_to_close()                # the help was printed, or a mistake explained: let it be read
        raise
    if a.cmd == "menu" or (not a.cmd and stay):
        from . import menu
        try:
            chosen = pick_project(a, project)
        except projects.ProjectError as e:
            sys.stderr.write("%s\n" % paint("%s: %s" % (APP_NAME, e), "err", "err"))
            if stay:
                wait_to_close()
            return 1
        menu.run_menu(chosen)              # double-clicked (or asked for): plain questions, not commands
        return 0
    if not a.cmd:
        p.print_help()
        print()
        print("New to this? %s walks you through it with plain questions." % paint(prog_name() + " menu", "code"))
        return 0
    OUT = Output(json_mode=a.json)
    CANCEL.clear()
    USER_CANCEL.clear()
    STDIN_CLOSED.clear()
    color_enable(not a.no_color and not a.json)
    if not a.no_log:
        logs.setup(a.log)
    unknown = None
    try:
        a.proj = projects.main_project() if a.cmd in WITHOUT_PROJECT or getattr(a, "all", False) else \
            pick_project(a, project)
    except projects.ProjectError as e:
        a.proj, unknown = projects.main_project(), e
    if not a.proj.main:
        logs.set_project(a.proj.title)     # the log says which project each line is about
    logs.write("== deltapatcher %s" % logs.redact_argv(argv))
    if a.json and not getattr(a, "passphrase_stdin", False):
        start_control_reader()
    cfg = config.load(a.proj)
    rc = 0
    try:
        if unknown is not None:
            raise unknown
        data = HANDLERS[a.cmd](a, cfg) or {}
        rc = data.pop("exit", 0) if isinstance(data, dict) else 0
        OUT.result(data)
    except (CliError, projects.ProjectError) as e:
        OUT.error(str(e), e.code, e.data)
        rc = 1
    except FAILURES as e:
        # "stopped" only when the person asked; a failed download also stops the others, but it is a failure
        code = "stopped" if USER_CANCEL.is_set() else "failed"
        OUT.error(str(e), code)
        rc = 1
    except KeyboardInterrupt:
        OUT.error("stopped (Ctrl+C)", "stopped")
        rc = 130
    except Exception as e:      # anything unexpected still ends with an error line the app can show
        import traceback
        logs.write(traceback.format_exc())
        OUT.error("%s: %s" % (type(e).__name__, e), "internal")
        rc = 1
    logs.write("== exit %d" % rc)
    logs.close()
    if not a.json and a.cmd != "self-update":
        update_notice(cfg)
    if stay:
        wait_to_close()
    return rc
