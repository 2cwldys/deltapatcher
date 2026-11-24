"""The command line. `deltapatcher` with no command opens the window."""

import argparse
import getpass
import os
import socket
import sys
import time

from . import APP_NAME, __version__, client, config, deploy, logs, publish, serve, signing, ssh
from .color import enable as color_enable
from .color import paint, plain
from .manifest import ManifestError
from .source import DEFAULT_URL, SourceError, insecure_remote

EPILOG = """examples:
  deltapatcher publish ./game ./store --name "My Game" --version 1.0 --sign
  deltapatcher serve ./store                         (http://localhost:8000/)
  deltapatcher update ./my-install --source http://localhost:8000/
  deltapatcher sign keygen                           (a signing key for your releases)
  deltapatcher ssh keygen                            (a new ed25519 key, with a passphrase)
  deltapatcher ssh trust --host example.com          (check and keep the server's host key)
  deltapatcher ssh pubkey                            (the line for the server's authorized_keys)
  deltapatcher deploy ./store --host example.com --user deploy --remote-root /var/www/game
  deltapatcher deploy ./store --to-folder D:/www/game

The log: %s (--log FILE elsewhere, --no-log for none).
""" % logs.default_path()


class Bar(object):
    """A one-line progress bar on stderr."""

    def __init__(self):
        self.last = 0.0
        self.shown = False

    def __call__(self, done, total, label):
        now = time.time()
        if now - self.last < 0.2 and done < total:
            return
        self.last = now
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


# which lines get which colour, by how they start: the library's messages are plain text
STYLE_BY_START = (
    ("published", "ok"), ("updated to", "ok"), ("up to date", "ok"), ("deployed", "ok"), ("downloaded and checked", "ok"),
    ("signed by", "ok"), ("signed with", "ok"), ("rolled back", "ok"), ("manifest in place", "ok"),
    ("the release is not signed", "warn"), ("trusting this publisher key", "warn"), ("skipped", "warn"),
    ("  kept", "warn"), ("the last update stopped", "warn"), ("an update stopped", "warn"), ("dry run", "warn"),
    ("the target", "warn"), ("Warning", "warn"), ("Not trusted", "warn"),
)


def say(text, style=None):
    """A line for the person at the terminal (in colour when it can show it) and for the log (plain)."""
    if style is None:
        for start, s in STYLE_BY_START:
            if text.startswith(start):
                style = s
                break
    print(paint(text, style) if style else text)
    logs.write(plain(text))


def ask_yes(question, default=False):
    if not sys.stdin.isatty():
        return default
    a = input("%s [%s] " % (question, "Y/n" if default else "y/N")).strip().lower()
    return default if not a else a.startswith("y")


def target_from(args, cfg):
    return ssh.Target(args.host or cfg["ssh_host"], args.user or cfg["ssh_user"], args.port or cfg["ssh_port"],
                      args.key if args.key is not None else cfg["ssh_key"],
                      getattr(args, "remote_root", None) or cfg["remote_root"])


def confirm_host(target, interactive=True):
    """Make sure the host key is trusted, asking the person at the terminal the first time."""
    if ssh.is_trusted(target):
        return True
    keys = ssh.scan_host(target)
    say("The server %s is not known yet. It offers these host keys:" % target.known_name(), "warn")
    for ktype, _b, fp in keys:
        say("  %-22s %s" % (ktype, paint(fp, "code")))
    say("Compare them with the fingerprints your hosting provider shows, or run on the server:\n"
        "  " + paint("ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub", "code"))
    if not interactive or not ask_yes("Do they match? Trust this server", default=False):
        say("Not trusted: nothing was sent.")
        return False
    ssh.trust(target, keys)
    say("Trusted; kept in %s" % target.known_hosts, "ok")
    return True


def load_signer(path):
    """The signing key at path, asking its passphrase at the terminal when it has one."""
    if not signing.can_sign():
        raise signing.SignError("signing needs the 'cryptography' package (pip install cryptography)")
    if not os.path.isfile(path):
        raise signing.SignError("no signing key at %s: make one with  deltapatcher sign keygen" % path)
    passphrase = None
    if signing.needs_passphrase(path):
        if not sys.stdin.isatty():
            raise signing.SignError("the signing key has a passphrase and there is no terminal to ask it in")
        passphrase = getpass.getpass("Passphrase of the signing key %s: " % path)
    return signing.load_key(path, passphrase)


def cmd_publish(a, cfg):
    protect = [p for p in (a.protect or []) if p]
    signer = None
    if a.sign is not None:
        signer = load_signer(a.sign or cfg["signing_key"] or signing.default_key_path())
    bar = Bar()
    try:
        publish.publish(a.src, a.store, a.name, a.version, protect, prune=not a.no_prune, progress=bar,
                        log=lambda s: (bar.end(), say(s)), signer=signer, htaccess=not a.no_htaccess)
    finally:
        bar.end()
    if signer is None:
        say("Not signed. Players can check who made a release when you publish with --sign (deltapatcher sign keygen).", "warn")
    return 0


def cmd_serve(a, cfg):
    srv = serve.make_server(a.store, a.bind, a.port, quiet=not a.verbose)
    say("Serving %s at %s  (Ctrl+C stops it)" % (os.path.abspath(a.store),
        paint("http://%s:%d/" % (a.bind if a.bind != "0.0.0.0" else "localhost", a.port), "code")))
    if a.bind not in ("127.0.0.1", "localhost", "::1"):
        say("Note: this is reachable from other machines on the network. Use a real web server with HTTPS for the internet.", "warn")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def cmd_update(a, cfg):
    url = a.source or cfg["source_url"] or DEFAULT_URL
    if insecure_remote(url):
        say("Warning: %s is plain http to another machine. Every file is checked against the manifest's SHA-256, "
            "but the manifest itself could be changed on the way. Use https, or a signed release, for anything but a "
            "trusted network." % url)
    headers = dict(h.split(":", 1) for h in (a.header or []) if ":" in h)
    headers = {k.strip(): v.strip() for k, v in headers.items()}
    bar = Bar()
    try:
        plan = client.update(url, a.install, check_only=a.check, workers=a.workers, headers=headers,
                             progress=bar, log=lambda s: (bar.end(), say(s)),
                             trust_key=a.trust_key or cfg.get("trust_key") or None)
    finally:
        bar.end()
    return 0 if (plan.empty() or not a.check) else 2


def cmd_rollback(a, cfg):
    client.rollback(a.install, log=say)
    return 0


def cmd_deploy(a, cfg):
    if a.to_folder:
        tgt = deploy.FolderTarget(a.to_folder)
    else:
        t = target_from(a, cfg)
        if t.key and os.path.isfile(t.key):
            probs = ssh.permission_problems(t.key)
            if probs:
                say("Your private key %s is readable by others: %s." % (t.key, "; ".join(probs)), "warn")
                if ask_yes("Restrict it to your account now", default=True):
                    ssh.fix_permissions(t.key)
                else:
                    return 1
        if not confirm_host(t):
            return 1
        tgt = deploy.SftpTarget(t)
    bar = Bar()
    try:
        deploy.deploy(a.store, tgt, prune=not a.no_prune, dry_run=a.dry_run, progress=bar,
                      log=lambda s: (bar.end(), say(s)))
    finally:
        bar.end()
    return 0


def _new_passphrase(what):
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
        if not sys.stdin.isatty():
            raise signing.SignError("run this in a terminal: it asks for a passphrase")
        say("A new signing key at %s. Only you need it; it never leaves this computer." % path, "head")
        pub = signing.generate_key(path, _new_passphrase("signing key"))
        say("Made %s. Its public key, to give to players and put in launchers:" % path, "ok")
        say("  " + paint(pub, "code"))
        say("Publish with --sign to sign your releases with it.")
        return 0
    if a.sign_cmd == "pubkey":
        key = load_signer(path)
        print(signing.public_key_text(key))
        return 0
    return 1


def cmd_ssh(a, cfg):
    if a.ssh_cmd == "keygen":
        path = a.path or cfg["ssh_key"] or ssh.default_key_path()
        if ssh.can_generate() and sys.stdin.isatty():
            say("A new ed25519 key at %s." % path, "head")
            say("You type its passphrase once per session (ssh-add).")
            ssh.generate_key(path, _new_passphrase("key"), "deltapatcher@" + socket.gethostname())
        else:
            ssh.generate_key_interactive(path)
        say("Made %s and %s.pub" % (path, path), "ok")
        say("Next: put this line in ~/.ssh/authorized_keys on the server (it only allows SFTP):")
        say("  " + paint(ssh.authorized_keys_line(path), "code"))
        return 0
    if a.ssh_cmd == "pubkey":
        path = a.path or cfg["ssh_key"]
        print(ssh.authorized_keys_line(path, sftp_only=not a.full))
        return 0
    if a.ssh_cmd == "unlock":
        path = a.path or cfg["ssh_key"]
        if not ssh.key_has_passphrase(path):
            say("%s has no passphrase: nothing to unlock." % path)
            return 0
        ssh.agent_add(path, use_dialog=False)
        say("The key is in ssh-agent until you log out.", "ok")
        return 0
    t = target_from(a, cfg)
    if a.ssh_cmd == "trust":
        if a.forget:
            ssh.forget(t)
            say("Forgot the host key of %s" % t.known_name(), "ok")
            return 0
        return 0 if confirm_host(t) else 1
    if a.ssh_cmd == "test":
        if not confirm_host(t):
            return 1
        ok, text = ssh.test_connection(t)
        say(text, "ok" if ok else "err")
        return 0 if ok else 1
    return 1


def build_parser():
    p = argparse.ArgumentParser(prog="deltapatcher", description="%s %s: delta patching with SHA-256 blobs." % (APP_NAME, __version__),
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version="%s %s" % (APP_NAME, __version__))
    p.add_argument("--log", metavar="FILE", help="write the log here (default: %s)" % logs.default_path())
    p.add_argument("--no-log", action="store_true", help="write no log file")
    p.add_argument("--no-color", action="store_true", help="plain text (colour is used only in a terminal that shows it)")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("publish", help="turn a folder into a store (manifest.json + blobs/)")
    s.add_argument("src")
    s.add_argument("store")
    s.add_argument("--name", default="")
    s.add_argument("--version", dest="version", default="")
    s.add_argument("--protect", action="append", help="a glob of files clients keep once they have them (repeatable)")
    s.add_argument("--sign", nargs="?", const="", metavar="KEYFILE",
                   help="sign the release (default key: the saved one, else %s)" % signing.default_key_path())
    s.add_argument("--no-htaccess", action="store_true", help="do not write the .htaccess for Apache")
    s.add_argument("--no-prune", action="store_true", help="keep blobs no release names")

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
    s.add_argument("--workers", type=int, default=4)
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
    k = ss.add_parser("keygen", help="make a signing key (asks a passphrase)")
    k.add_argument("--path")
    k = ss.add_parser("pubkey", help="print the public key players use to check your releases")
    k.add_argument("--path")

    s = sub.add_parser("ssh", help="keys and host keys for deploys")
    ss = s.add_subparsers(dest="ssh_cmd")
    k = ss.add_parser("keygen", help="make a new ed25519 key with a passphrase")
    k.add_argument("--path")
    k = ss.add_parser("pubkey", help="print the authorized_keys line for the key")
    k.add_argument("--path")
    k.add_argument("--full", action="store_true", help="allow more than SFTP (not needed by deltapatcher)")
    k = ss.add_parser("unlock", help="add the key to ssh-agent (asks the passphrase once)")
    k.add_argument("--path")
    k = ss.add_parser("trust", help="check and keep a server's host key")
    ssh_args(k, root=False)
    k.add_argument("--forget", action="store_true")
    k = ss.add_parser("test", help="log in with sftp")
    ssh_args(k)

    s = sub.add_parser("config", help="where the settings file is (it is made, with every setting explained, if missing)")
    s.add_argument("--show", action="store_true", help="print the file too")

    sub.add_parser("gui", help="open the window")
    return p


def cmd_config(a, cfg):
    p = config.ensure()
    say("Settings: %s" % paint(p, "code"))
    say("Log:      %s" % paint(logs.path() or logs.default_path(), "code"))
    if a.show:
        with open(p, "r", encoding="utf-8") as f:
            print(f.read().rstrip())
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = build_parser()
    a = p.parse_args(argv)
    if not a.cmd or a.cmd == "gui":
        from .gui.app import run as run_gui
        return run_gui()
    color_enable(not a.no_color)
    if not a.no_log:
        logs.setup(a.log)
    logs.write("== deltapatcher %s" % logs.redact_argv(argv))
    cfg = config.load()
    handlers = {"publish": cmd_publish, "serve": cmd_serve, "update": cmd_update, "rollback": cmd_rollback,
                "deploy": cmd_deploy, "ssh": cmd_ssh, "sign": cmd_sign, "config": cmd_config}
    if a.cmd == "ssh" and not a.ssh_cmd:
        p.parse_args(["ssh", "--help"])
    if a.cmd == "sign" and not a.sign_cmd:
        p.parse_args(["sign", "--help"])
    try:
        rc = handlers[a.cmd](a, cfg)
    except (client.UpdateError, deploy.DeployError, publish.PublishError, ssh.SshError, signing.SignError,
            SourceError, ManifestError, OSError) as e:
        sys.stderr.write("\n%s\n" % paint("%s: %s" % (APP_NAME, e), "err", "err"))
        logs.write("FAILED: %s" % e)
        rc = 1
    except KeyboardInterrupt:
        sys.stderr.write("\n%s\n" % paint("stopped", "warn", "err"))
        logs.write("stopped (Ctrl+C)")
        rc = 130
    logs.write("== exit %d" % rc)
    logs.close()
    return rc
