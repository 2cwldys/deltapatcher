"""The command line, for people and for the DeltaPatcher app.

In a terminal the output is text, in colour where the terminal shows it. With --json it is one JSON
object a line, which is how the app (app/, Electron) drives this program:

    {"event": "log", "text": "...", "style": "ok|warn|err|code|head|null"}
    {"event": "progress", "done": 123, "total": 456, "label": "downloading"}
    {"event": "result", "ok": true, "data": {...}}                  the last line on success
    {"event": "error", "message": "...", "code": "...", "data": {...}}   the last line on failure

In --json mode standard input carries what a person would type: with --passphrase-stdin the first
line is the passphrase (never put on a command line or in the environment), and a later line
"cancel" stops a download, leaving the folder as it was.
"""

import argparse
import getpass
import json
import os
import re
import socket
import sys
import threading
import time

from . import APP_NAME, __version__, client, config, deploy, logs, publish, serve, signing, ssh
from .color import enable as color_enable
from .color import paint, plain
from .manifest import ManifestError
from .source import DEFAULT_URL, SourceError, insecure_remote

# %(prog)s is the name to type (see prog_name); argparse fills it in
EPILOG = """examples:
  %%(prog)s publish ./game ./store --name "My Game" --version 1.0 --sign
  %%(prog)s serve ./store                         (http://localhost:8000/)
  %%(prog)s update ./my-install --source http://localhost:8000/
  %%(prog)s sign keygen                           (a signing key for your releases)
  %%(prog)s ssh keygen                            (a new ed25519 key, with a passphrase)
  %%(prog)s ssh trust --host example.com          (check and keep the server's host key)
  %%(prog)s ssh pubkey                            (the line for the server's authorized_keys)
  %%(prog)s deploy ./store --host example.com --user deploy --remote-root /var/www/game
  %%(prog)s deploy ./store --to-folder D:/www/game
  %%(prog)s config                                (where the settings file and the log are)

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
    """Where messages go: a terminal (text, colour, a progress bar) or the app (JSON lines)."""

    def __init__(self, json_mode=False):
        self.json = json_mode
        self.last = 0.0
        self.shown = False
        self.lock = threading.Lock()

    def _emit(self, obj):
        """One JSON line to the app. When the app has gone away (its end of the pipe is closed), there is
        nobody to tell: the line is dropped instead of turning a finished job into a failure."""
        with self.lock:
            if getattr(self, "gone", False):
                return
            try:
                sys.stdout.write(json.dumps(obj) + "\n")
                sys.stdout.flush()
            except (OSError, ValueError):
                self.gone = True

    def say(self, text, style=None):
        if style is None:
            for start, s in STYLE_BY_START:
                if text.startswith(start):
                    style = s
                    break
        logs.write(plain(text))
        if self.json:
            self._emit({"event": "log", "text": plain(text), "style": style})
            return
        self.end()
        print(paint(text, style) if style else text)

    def progress(self, done, total, label):
        now = time.time()
        if now - self.last < (0.1 if self.json else 0.2) and done < total:
            return
        self.last = now
        if self.json:
            self._emit({"event": "progress", "done": done, "total": total, "label": label})
            return
        frac = (float(done) / total) if total else 1.0
        n = int(frac * 30)
        if label in ("downloading", "uploading"):
            amount = "%s / %s" % (publish.human(done), publish.human(total))
        else:
            amount, label = "%d / %d files" % (done, total), ("hashing" if label not in ("installing", "done") else label)
        meter = paint("[%s%s]" % ("#" * n, "." * (30 - n)), "bar", "err")
        sys.stderr.write("\r  %-12s %s %3d%%  %s   " % (label[:12], meter, frac * 100, amount))
        sys.stderr.flush()
        self.shown = True

    def end(self):
        if self.shown:
            sys.stderr.write("\n")
            self.shown = False

    def result(self, data):
        if self.json:
            self._emit({"event": "result", "ok": True, "data": data or {}})

    def error(self, message, code="failed", data=None):
        logs.write("FAILED: %s" % message)
        if self.json:
            self._emit({"event": "error", "message": message, "code": code, "data": data or {}})
            return
        self.end()
        sys.stderr.write("\n%s\n" % paint("%s: %s" % (APP_NAME, message), "err", "err"))


OUT = Output()
CANCEL = threading.Event()           # stops a download: set by the person (USER_CANCEL) or by a failed download
USER_CANCEL = threading.Event()      # the person asked to stop ("cancel" on standard input)
STDIN_CLOSED = threading.Event()     # --json: the app closed our standard input (asked us to stop, or went away)


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

def cmd_publish(a, cfg):
    protect = [p for p in (a.protect or []) if p]
    signer = None
    if a.sign is not None:
        signer = load_signer(a, a.sign or cfg["signing_key"] or signing.default_key_path())
    res = publish.publish(a.src, a.store, a.name, a.version, protect, prune=not a.no_prune, progress=OUT.progress,
                          log=say, signer=signer, htaccess=not a.no_htaccess)
    OUT.end()
    if signer is None:
        say("Not signed. Players can check who made a release when you publish with --sign.", "warn")
    return {"files": res.files, "bytes": res.bytes, "blobs": res.blobs, "new_blobs": res.new_blobs,
            "new_bytes": res.new_bytes, "pruned": res.pruned, "signed": signer is not None,
            "summary": res.summary()}


def cmd_serve(a, cfg):
    srv = serve.make_server(a.store, a.bind, a.port, quiet=not a.verbose)
    url = "http://%s:%d/" % (a.bind if a.bind != "0.0.0.0" else "localhost", srv.server_address[1])
    say("Serving %s at %s  (Ctrl+C stops it)" % (os.path.abspath(a.store), paint(url, "code")))
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
    url = a.source or cfg["source_url"] or DEFAULT_URL
    if insecure_remote(url):
        say("Warning: %s is plain http to another machine. Every file is checked against the manifest's SHA-256, "
            "but the manifest itself could be changed on the way. Use https, or a signed release, for anything but a "
            "trusted network." % url)
    headers = dict(h.split(":", 1) for h in (a.header or []) if ":" in h)
    headers = {k.strip(): v.strip() for k, v in headers.items()}
    workers = a.workers or int(cfg.get("workers") or 4)
    plan = client.update(url, a.install, check_only=a.check, workers=max(1, workers), headers=headers,
                         progress=OUT.progress, log=say, cancel=CANCEL,
                         trust_key=a.trust_key or cfg.get("trust_key") or None)
    OUT.end()
    need = plan.download()
    data = {"empty": plan.empty(), "summary": plan.summary(), "add": len(plan.add), "replace": len(plan.replace),
            "remove": len(plan.remove), "same": plan.same, "download_blobs": len(need),
            "download_bytes": sum(need.values()), "kept": plan.kept_changed + plan.protected,
            "publisher_key": client.State(os.path.abspath(a.install)).publisher_key}
    if a.check and not plan.empty():
        data["exit"] = 2
    return data


def cmd_rollback(a, cfg):
    n = client.rollback(a.install, log=say)
    return {"undone": n}


def cmd_deploy(a, cfg):
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
    res = deploy.deploy(a.store, tgt, prune=not a.no_prune, dry_run=a.dry_run, progress=OUT.progress, log=say)
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


def cmd_config(a, cfg):
    p = config.ensure()
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
        config.save(changes)          # only these keys change in the file; everything else stays as it is
        return {"path": p, "settings": config.load()}
    data = {"path": p, "log": logs.path() or logs.default_path(), "log_dir": os.path.dirname(logs.path() or logs.default_path()),
            "settings": config.load(), "schema": [{"section": s, "key": k, "default": d, "what": w}
                                                  for s, k, d, w in config.SCHEMA]}
    if not OUT.json:
        say("Settings: %s" % paint(p, "code"))
        say("Log:      %s" % paint(data["log"], "code"))
        if a.show:
            with open(p, "r", encoding="utf-8") as f:
                print(f.read().rstrip())
    return data


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
    sub = p.add_subparsers(dest="cmd")

    def pass_stdin(sp):
        sp.add_argument("--passphrase-stdin", action="store_true", help="read the passphrase from the first line of standard input")

    s = sub.add_parser("publish", help="turn a folder into a store (manifest.json + blobs/)")
    s.add_argument("src")
    s.add_argument("store")
    s.add_argument("--name", default="")
    s.add_argument("--version", dest="version", default="")
    s.add_argument("--protect", action="append", help="a glob of files clients keep once they have them (repeatable)")
    s.add_argument("--sign", nargs="?", const="", metavar="KEYFILE",
                   help="sign the release (optional; default key: the saved one, else %s)" % signing.default_key_path())
    s.add_argument("--no-htaccess", action="store_true", help="do not write the .htaccess for Apache")
    s.add_argument("--no-prune", action="store_true", help="keep blobs no release names")
    pass_stdin(s)

    s = sub.add_parser("serve", help="serve a store over http (default http://127.0.0.1:8000/)")
    s.add_argument("store")
    s.add_argument("--bind", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--verbose", action="store_true")

    s = sub.add_parser("update", help="bring a folder up to the source's release")
    s.add_argument("install")
    s.add_argument("--source", help="http(s) URL or folder of the store (default: the saved one, else %s)" % DEFAULT_URL)
    s.add_argument("--check", action="store_true", help="say what would change; change nothing (exit 2 when out of date)")
    s.add_argument("--trust-key", metavar="KEY", help="the publisher key (ed25519:...) the release must be signed with; "
                                                     "off: do not check signatures")
    s.add_argument("--workers", type=int, default=None, help="downloads at the same time (default: the setting, else 4)")
    s.add_argument("--header", action="append", help="an extra request header, 'Name: value' (repeatable)")

    s = sub.add_parser("rollback", help="undo the last update of a folder")
    s.add_argument("install")

    def ssh_args(sp, root=True):
        sp.add_argument("--host")
        sp.add_argument("--user")
        sp.add_argument("--port", type=int)
        sp.add_argument("--key", help="private key file (default: the saved one, else %s)" % ssh.default_key_path())
        if root:
            sp.add_argument("--remote-root", help="the folder on the server the web server serves")

    s = sub.add_parser("deploy", help="send a store to a server (sftp) or a folder, only the missing blobs")
    s.add_argument("store")
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

    s = sub.add_parser("config", help="the settings file: where it is (made, every setting explained, if missing)")
    cs = s.add_subparsers(dest="config_cmd")
    s.add_argument("--show", action="store_true", help="print the file too")
    cs.add_parser("get", help="every setting (with --json, for programs)")
    k = cs.add_parser("set", help="change settings: key=value ...")
    k.add_argument("values", nargs="*")

    sub.add_parser("version", help="the version")
    return p


HANDLERS = {"publish": cmd_publish, "serve": cmd_serve, "update": cmd_update, "rollback": cmd_rollback,
            "deploy": cmd_deploy, "ssh": cmd_ssh, "sign": cmd_sign, "config": cmd_config, "version": cmd_version}


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


def prompt_loop():
    """Double-clicked on Windows: a prompt that takes one command after another, so the window stays
    open until exit (or until it is closed)."""
    print()
    print(paint("Type a command and press Enter, for example:", "head"))
    print("  " + paint('update "D:\\My Game" --source https://example.com/game/', "code"))
    print("help lists the commands, help update explains one, exit closes this window.")
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
            main(argv, prompt=False)
        except SystemExit:
            pass                                            # the help was printed, or a mistake explained
        except KeyboardInterrupt:
            print()


def main(argv=None, prompt=True):
    """The command line. prompt=False: a command typed at the prompt, whose window already stays open."""
    global OUT
    argv = sys.argv[1:] if argv is None else argv
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
    except SystemExit:
        if stay:
            wait_to_close()                # the help was printed, or a mistake explained: let it be read
        raise
    if not a.cmd:
        p.print_help()
        if stay:
            prompt_loop()                  # double-clicked: one command after another, until exit
        return 0
    OUT = Output(json_mode=a.json)
    CANCEL.clear()
    USER_CANCEL.clear()
    STDIN_CLOSED.clear()
    color_enable(not a.no_color and not a.json)
    if not a.no_log:
        logs.setup(a.log)
    logs.write("== deltapatcher %s" % logs.redact_argv(argv))
    if a.json and not getattr(a, "passphrase_stdin", False):
        start_control_reader()
    cfg = config.load()
    rc = 0
    try:
        data = HANDLERS[a.cmd](a, cfg) or {}
        rc = data.pop("exit", 0) if isinstance(data, dict) else 0
        OUT.result(data)
    except CliError as e:
        OUT.error(str(e), e.code, e.data)
        rc = 1
    except (client.UpdateError, deploy.DeployError, publish.PublishError, ssh.SshError, signing.SignError,
            SourceError, ManifestError, OSError) as e:
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
    if stay:
        wait_to_close()
    return rc
