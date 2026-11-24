"""The guided menu: what a double-clicked deltapatcher-cli opens, and `deltapatcher menu` in a terminal.

Plain questions instead of commands. Every answer is saved in deltapatcher.ini (Enter keeps the saved
one, so the second time is mostly Enter), and every choice runs the same command a terminal would: the
menu is a way in, not a second program.
"""

import os
import sys

from . import APP_NAME, __version__, config, selfupdate, ssh
from .color import paint


class Back(Exception):
    """Ctrl+C, or the end of input, during a question: back to the menu."""


def _input(prompt):
    return input(prompt)


def ask(label, saved="", example=""):
    """One answer; Enter keeps the saved one. Quotes around a dragged-in path are dropped."""
    shown = (" [%s]" % saved) if saved else ((" (for example %s)" % example) if example else "")
    try:
        answer = _input("  %s%s: " % (label, shown))
    except (EOFError, KeyboardInterrupt):
        print()
        raise Back()
    answer = answer.strip().strip('"').strip()
    return answer or str(saved or "")


def yes(question, default=True):
    try:
        answer = _input("  %s %s " % (question, "[Y/n]" if default else "[y/N]")).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        raise Back()
    return default if not answer else answer.startswith("y")


def pause():
    try:
        _input("\n  Press Enter to go back to the menu.")
    except (EOFError, KeyboardInterrupt):
        print()


def run(*argv):
    """One command, as it would be typed in a terminal; True when it worked."""
    from . import cli
    try:
        return cli.main([str(a) for a in argv], prompt=False) == 0
    except SystemExit as e:
        return e.code in (0, None)


def where(cfg):
    """Where releases go, in words; None while that is not set up."""
    mode = cfg["deploy_mode"]
    if mode == "local":
        return "this computer, at http://localhost:%s/ (to test)" % cfg["serve_port"]
    if mode == "folder":
        return ("the folder %s" % cfg["deploy_folder"]) if cfg["deploy_folder"] else None
    if cfg["ssh_host"] and cfg["remote_root"]:
        user = (cfg["ssh_user"] + "@") if cfg["ssh_user"] else ""
        return "your server, %s%s:%s" % (user, cfg["ssh_host"], cfg["remote_root"])
    return None


def heading(text):
    print()
    print(paint(text, "head"))


def release():
    cfg = config.load()
    heading("Release a new version")
    src = ask("Folder to release (type its path, or drag it into this window)", cfg["publish_src"], "D:\\MyGame")
    if not src or not os.path.isdir(src):
        print(paint("  There is no folder %s." % (src or "there"), "err"))
        return pause()
    version = ask("Version (the same one again is fine)", cfg["version"], "1.0")
    config.save({"publish_src": src, "version": version})
    goes = where(config.load())
    if not goes:
        print(paint("  Where players download it from is not set up yet: choose 2 in the menu first.", "warn"))
        return pause()
    print("  It goes to %s, and only what changed is sent." % paint(goes, "code"))
    if yes("Release it now?"):
        run("release")
    pause()


def setup():
    cfg = config.load()
    heading("Where players download your releases from")
    print("  1  Your server, over SSH     a VPS: players download from its web server")
    print("  2  A folder                  a web root on this computer, or a network share")
    print("  3  This computer, to test    served at http://localhost:%s/ while it runs" % cfg["serve_port"])
    pick = ask("Choose 1, 2 or 3", {"sftp": "1", "folder": "2", "local": "3"}.get(cfg["deploy_mode"], "1"))
    mode = {"1": "sftp", "2": "folder", "3": "local"}.get(pick)
    if not mode:
        print(paint("  Type 1, 2 or 3.", "warn"))
        return pause()
    if mode == "local":
        config.save({"deploy_mode": "local", "serve_port": ask("Port", cfg["serve_port"])})
        print(paint("  Saved: releases are served on this computer, to test.", "ok"))
        return pause()
    if mode == "folder":
        folder = ask("The folder to copy releases to", cfg["deploy_folder"], "D:\\www\\game")
        url = ask("The address players use for that folder", cfg["public_url"], "https://game.example.com/")
        config.save({"deploy_mode": "folder", "deploy_folder": folder, "public_url": url})
        print(paint("  Saved: releases are copied to %s." % folder, "ok"))
        return pause()

    host = ask("The server's address", cfg["ssh_host"], "game.example.com")
    user = ask("The user to log in as", cfg["ssh_user"] or "deploy")
    root = ask("The folder its web server serves", cfg["remote_root"], "/var/www/game")
    url = ask("The address players use", cfg["public_url"] or ("https://%s/" % host if host else ""),
              "https://game.example.com/")
    config.save({"deploy_mode": "sftp", "ssh_host": host, "ssh_user": user, "remote_root": root, "public_url": url})
    print(paint("  Saved.", "ok"))

    key = config.load()["ssh_key"] or ssh.default_key_path()
    if not os.path.isfile(key):
        print()
        print("  DeltaPatcher logs in to the server with an SSH key (a pair of files on this computer).")
        if yes("Make one now?"):
            run("ssh", "keygen")
    if os.path.isfile(key):
        print()
        print("  The server has to know this key. Add this line to ~/.ssh/authorized_keys of %s on the server" % user)
        print("  (it lets the key send files, nothing else):")
        run("ssh", "pubkey")
    print()
    if yes("Check the server's host key now (once: it proves later that it is the same server)?"):
        run("ssh", "trust")
    if yes("Try logging in now?"):
        run("ssh", "test")
    pause()


def update():
    cfg = config.load()
    heading("Update a folder")
    folder = ask("The folder to update", cfg["install_dir"], "D:\\Games\\MyGame")
    source = ask("Where it updates from (the address the game's maker gave you)", cfg["source_url"])
    if not folder:
        return pause()
    config.save({"install_dir": folder, "source_url": source})
    run("update", folder, "--source", source)
    pause()


def update_self():
    heading("Update DeltaPatcher")
    run("self-update")
    pause()


def commands():
    from . import cli
    cli.prompt_loop(back="goes back to the menu")


CHOICES = [
    ("1", "Release a new version", "sends players only what changed", release),
    ("2", "Set up where releases go", "your server, a folder, or this computer", setup),
    ("3", "Update a folder", "gets the latest release", update),
    ("4", "Update DeltaPatcher", "itself, to the newest version", update_self),
    ("5", "Type commands yourself", "every command, at a prompt", commands),
    ("0", "Exit", "", None),
]


def newer_out(cfg):
    """A newer DeltaPatcher's version, asked of GitHub at most once a day; '' when none (or not a terminal)."""
    try:
        if not sys.stdout.isatty():
            return ""
    except (AttributeError, ValueError):
        return ""
    newest = selfupdate.cached_check(os.path.dirname(config.path()), enabled=cfg["check_updates"])
    return newest if selfupdate.is_newer(newest) else ""


def run_menu():
    while True:
        cfg = config.load()
        heading("%s %s" % (APP_NAME, __version__))
        newest = newer_out(cfg)
        if newest:
            print(paint("DeltaPatcher %s is out: choose 4 to update." % newest, "warn"))
        goes = where(cfg)
        if cfg["publish_src"] and goes:
            print("Your release: %s  ->  %s" % (cfg["publish_src"], goes))
        print()
        print("What do you want to do?")
        for key, label, hint, _action in CHOICES:
            print("  %s  %-26s %s" % (paint(key, "code"), label, hint))
        try:
            choice = _input("\nType a number and press Enter: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        action = next((a for k, _l, _h, a in CHOICES if k == choice), "?")
        if action is None:
            return
        if action == "?":
            print(paint("Type one of the numbers above.", "warn"))
            continue
        try:
            action()
        except Back:
            pass
