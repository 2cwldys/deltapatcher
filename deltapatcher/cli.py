"""The command line. `deltapatcher` with no command opens the window."""

import argparse
import getpass
import os
import socket
import sys
import time

from . import APP_NAME, __version__, client, config, deploy, publish, serve, ssh
from .manifest import ManifestError
from .source import DEFAULT_URL, SourceError, insecure_remote

EPILOG = """examples:
  deltapatcher publish ./game ./store --name "My Game" --version 1.0
  deltapatcher serve ./store                         (http://localhost:8000/)
  deltapatcher update ./my-install --source http://localhost:8000/
  deltapatcher ssh keygen                            (a new ed25519 key, with a passphrase)
  deltapatcher ssh trust --host example.com          (check and keep the server's host key)
  deltapatcher ssh pubkey                            (the line for the server's authorized_keys)
  deltapatcher deploy ./store --host example.com --user deploy --remote-root /var/www/game
  deltapatcher deploy ./store --to-folder D:/www/game
"""


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
        sys.stderr.write("\r  %-12s [%s%s] %3d%%  %s   " % (label[:12], "#" * n, "." * (30 - n), frac * 100, amount))
        sys.stderr.flush()
        self.shown = True

    def end(self):
        if self.shown:
            sys.stderr.write("\n")
            self.shown = False


def say(text):
    print(text)


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
    say("The server %s is not known yet. It offers these host keys:" % target.known_name())
    for ktype, _b, fp in keys:
        say("  %-22s %s" % (ktype, fp))
    say("Compare them with the fingerprints your hosting provider shows, or run on the server:\n"
        "  ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub")
    if not interactive or not ask_yes("Do they match? Trust this server", default=False):
        say("Not trusted: nothing was sent.")
        return False
    ssh.trust(target, keys)
    say("Trusted; kept in %s" % target.known_hosts)
    return True


def cmd_publish(a, cfg):
    protect = [p for p in (a.protect or []) if p]
    res = publish.publish(a.src, a.store, a.name, a.version, protect, prune=not a.no_prune, progress=Bar(), log=say)
    return 0 if res else 1


def cmd_serve(a, cfg):
    srv = serve.make_server(a.store, a.bind, a.port, quiet=not a.verbose)
    say("Serving %s at http://%s:%d/  (Ctrl+C stops it)" % (os.path.abspath(a.store), a.bind if a.bind != "0.0.0.0" else "localhost", a.port))
    if a.bind not in ("127.0.0.1", "localhost", "::1"):
        say("Note: this is reachable from other machines on the network. Use a real web server with HTTPS for the internet.")
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
            "but the manifest itself could be changed on the way. Use https for anything but a trusted network." % url)
    headers = dict(h.split(":", 1) for h in (a.header or []) if ":" in h)
    headers = {k.strip(): v.strip() for k, v in headers.items()}
    bar = Bar()
    try:
        plan = client.update(url, a.install, check_only=a.check, workers=a.workers, headers=headers,
                             progress=bar, log=lambda s: (bar.end(), say(s)))
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
                say("Your private key %s is readable by others: %s." % (t.key, "; ".join(probs)))
                if ask_yes("Restrict it to your account now", default=True):
                    ssh.fix_permissions(t.key)
                else:
                    return 1
        if not confirm_host(t):
            return 1
        tgt = deploy.SftpTarget(t)
    bar = Bar()
    try:
        res = deploy.deploy(a.store, tgt, prune=not a.no_prune, dry_run=a.dry_run, progress=bar,
                            log=lambda s: (bar.end(), say(s)))
    finally:
        bar.end()
    return 0 if res else 1


def cmd_ssh(a, cfg):
    if a.ssh_cmd == "keygen":
        path = a.path or cfg["ssh_key"] or ssh.default_key_path()
        if ssh.can_generate() and sys.stdin.isatty():
            say("A new ed25519 key at %s." % path)
            say("A passphrase protects the key if the file is ever copied. You type it once per session (ssh-add).")
            while True:
                p1 = getpass.getpass("Passphrase (empty for none, not recommended): ")
                if not p1:
                    if ask_yes("Really make a key without a passphrase", default=False):
                        break
                    continue
                if len(p1) < 10:
                    say("Use at least 10 characters.")
                    continue
                if getpass.getpass("Again: ") == p1:
                    break
                say("They differ; try again.")
            ssh.generate_key(path, p1, "deltapatcher@" + socket.gethostname())
        else:
            ssh.generate_key_interactive(path)
        say("Made %s and %s.pub" % (path, path))
        say("Next: put this line in ~/.ssh/authorized_keys on the server (it only allows SFTP):")
        say("  " + ssh.authorized_keys_line(path))
        return 0
    if a.ssh_cmd == "pubkey":
        path = a.path or cfg["ssh_key"]
        say(ssh.authorized_keys_line(path, sftp_only=not a.full))
        return 0
    if a.ssh_cmd == "unlock":
        path = a.path or cfg["ssh_key"]
        if not ssh.key_has_passphrase(path):
            say("%s has no passphrase: nothing to unlock." % path)
            return 0
        ssh.agent_add(path, use_dialog=False)
        say("The key is in ssh-agent until you log out.")
        return 0
    t = target_from(a, cfg)
    if a.ssh_cmd == "trust":
        if a.forget:
            ssh.forget(t)
            say("Forgot the host key of %s" % t.known_name())
            return 0
        return 0 if confirm_host(t) else 1
    if a.ssh_cmd == "test":
        if not confirm_host(t):
            return 1
        ok, text = ssh.test_connection(t)
        say(text)
        return 0 if ok else 1
    return 1


def build_parser():
    p = argparse.ArgumentParser(prog="deltapatcher", description="%s %s: delta patching with SHA-256 blobs." % (APP_NAME, __version__),
                                epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version="%s %s" % (APP_NAME, __version__))
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("publish", help="turn a folder into a store (manifest.json + blobs/)")
    s.add_argument("src")
    s.add_argument("store")
    s.add_argument("--name", default="")
    s.add_argument("--version", dest="version", default="")
    s.add_argument("--protect", action="append", help="a glob of files clients keep once they have them (repeatable)")
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

    sub.add_parser("gui", help="open the window")
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    p = build_parser()
    a = p.parse_args(argv)
    if not a.cmd or a.cmd == "gui":
        from .gui.app import run as run_gui
        return run_gui()
    cfg = config.load()
    handlers = {"publish": cmd_publish, "serve": cmd_serve, "update": cmd_update, "rollback": cmd_rollback,
                "deploy": cmd_deploy, "ssh": cmd_ssh}
    if a.cmd == "ssh" and not a.ssh_cmd:
        p.parse_args(["ssh", "--help"])
    try:
        return handlers[a.cmd](a, cfg)
    except (client.UpdateError, deploy.DeployError, publish.PublishError, ssh.SshError, SourceError, ManifestError,
            OSError) as e:
        sys.stderr.write("\n%s: %s\n" % (APP_NAME, e))
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("\nstopped\n")
        return 130
