"""The guided menu: what a double-clicked deltapatcher-cli opens, and `deltapatcher menu` in a terminal.

Plain questions instead of commands. Every answer is saved in deltapatcher.ini (Enter keeps the saved
one, so the second time is mostly Enter), and every choice runs the same command a terminal would: the
menu is a way in, not a second program.

Its Projects entry opens a short list: choose another project, add projects from a project file, share
projects as one, remove one, remove all. With several projects the menu says which one it works on and
updates them all; its answers are saved in that project's settings, and its commands run with --project.
"""

import os
import sys

from . import APP_NAME, __version__, config, presets, projects, selfupdate, signing, ssh
from .color import paint
from .projects import where

_project = None             # the project the menu works on (None: Default)


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
    """One command, as it would be typed in a terminal, on the menu's project; True when it worked."""
    from . import cli
    argv = [str(a) for a in argv]
    if _project is not None and not _project.main:
        argv = ["--project", _project.path] + argv
    try:
        return cli.main(argv, prompt=False) == 0
    except SystemExit as e:
        return e.code in (0, None)


def load():
    return config.load(_project)


def save(values):
    config.save(values, _project)


def heading(text):
    print()
    print(paint(text, "head"))


def release():
    cfg = load()
    heading("Release a new version")
    src = ask("Folder to release (type its path, or drag it into this window)", cfg["publish_src"], "D:\\MyGame")
    if not src or not os.path.isdir(src):
        print(paint("  There is no folder %s." % (src or "there"), "err"))
        return pause()
    version = ask("Version (the same one again is fine)", cfg["version"], "1.0")
    save({"publish_src": src, "version": version})
    goes = where(load())
    if not goes:
        print(paint("  Where players download it from is not set up yet: choose 2 in the menu first.", "warn"))
        return pause()
    print("  It goes to %s, and only what changed is sent." % paint(goes, "code"))
    if yes("Release it now?"):
        run("release")
    pause()


def setup():
    cfg = load()
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
        save({"deploy_mode": "local", "serve_port": ask("Port", cfg["serve_port"])})
        print(paint("  Saved: releases are served on this computer, to test.", "ok"))
        return pause()
    if mode == "folder":
        folder = ask("The folder to copy releases to", cfg["deploy_folder"], "D:\\www\\game")
        url = ask("The address players use for that folder", cfg["public_url"], "https://game.example.com/")
        save({"deploy_mode": "folder", "deploy_folder": folder, "public_url": url})
        print(paint("  Saved: releases are copied to %s." % folder, "ok"))
        return pause()

    host = ask("The server's address", cfg["ssh_host"], "game.example.com")
    user = ask("The user to log in as", cfg["ssh_user"] or "deploy")
    root = ask("The folder its web server serves", cfg["remote_root"], "/var/www/game")
    url = ask("The address players use", cfg["public_url"] or ("https://%s/" % host if host else ""),
              "https://game.example.com/")
    save({"deploy_mode": "sftp", "ssh_host": host, "ssh_user": user, "remote_root": root, "public_url": url})
    print(paint("  Saved.", "ok"))

    key = load()["ssh_key"] or ssh.default_key_path()
    if not os.path.isfile(key):
        print()
        print("  DeltaPatcher logs in to the server with an SSH key (a pair of files on this computer).")
        if yes("Make one now?"):
            run("ssh", "keygen")
    print()
    if not yes("Check the server's host key now (once: it proves later that it is the same server)?"):
        return pause()
    if not run("ssh", "trust"):
        return pause()
    if os.path.isfile(key):
        print()
        print("  The server has to know your key. DeltaPatcher can put it there, logging in once with the")
        print("  password of %s on the server (used for that login only, never kept)." % user)
        if yes("Put it there now?"):
            run("ssh", "install-key")
        else:
            print("  Then add this line yourself to ~/.ssh/authorized_keys of %s on the server" % user)
            print("  (it lets the key send files, nothing else):")
            run("ssh", "pubkey")
    print()
    if yes("Try logging in now?"):
        run("ssh", "test")
    pause()


def update():
    cfg = load()
    heading("Update a folder")
    folder = ask("The folder to update", cfg["install_dir"], "D:\\Games\\MyGame")
    source = ask("Where it updates from (the address the game's maker gave you)", cfg["source_url"])
    if not folder:
        return pause()
    save({"install_dir": folder, "source_url": source})
    run("update", folder, "--source", source)
    pause()


def update_self():
    heading("Update DeltaPatcher")
    run("self-update")
    pause()


def commands():
    from . import cli
    cli.prompt_loop(back="goes back to the menu", project=_project)


def update_all():
    from . import cli
    heading("Update all projects")
    try:
        cli.main(["update", "--all"], prompt=False)
    except SystemExit:
        pass
    pause()


def choose_project():
    global _project
    every = projects.all_projects()
    heading("Choose a project")
    now = 1
    for i, p in enumerate(every, 1):
        if projects.same(p, _project):
            now = i
        print("  %s  %s%s" % (paint(str(i), "code"), p.title, "  (its file is gone)" if p.missing else ""))
    pick = ask("Number", str(now))
    if not pick.isdigit() or not 1 <= int(pick) <= len(every):
        print(paint("  Type one of the numbers above.", "warn"))
        return pause()
    chosen = every[int(pick) - 1]
    if chosen.missing:
        print(paint("  The settings file of %s is gone: %s" % (chosen.title, chosen.path), "err"))
        return pause()
    _project = chosen


def remove_project():
    """Remove a project, as project remove does: a projects-folder file is deleted, an added one only leaves the
    list, and the folders it updates or releases are not touched."""
    global _project
    every = projects.all_projects()[1:]
    heading("Remove a project")
    for i, p in enumerate(every, 1):
        print("  %s  %s  (%s)" % (paint(str(i), "code"), p.title,
                                  "its settings file is deleted" if p.kind == "folder" else
                                  "it only leaves the list; its file stays"))
    pick = ask("Number (Enter: none)")
    if not pick:
        return
    if not pick.isdigit() or not 1 <= int(pick) <= len(every):
        print(paint("  Type one of the numbers above.", "warn"))
        return pause()
    chosen = every[int(pick) - 1]
    print("  The folders it updates or releases are not touched.")
    if not yes("Remove %s?" % chosen.title, default=False):
        return pause()
    if run("project", "remove", chosen.path, "--yes") and projects.same(chosen, _project):
        _project = None                                     # the menu goes on with Default
    pause()


def remove_every():
    """Remove every project but Default, as project remove --all does: it names them and asks first."""
    global _project
    heading("Remove every project")
    if run("project", "remove", "--all"):
        _project = None                                     # the menu goes on with Default
    pause()


def import_file():
    """Add projects from a project file, as project import does: what the file holds and who signed it, one
    question, then the folder each project keeps up to date. The menu then works on the first one added."""
    global _project
    heading("Add projects from a project file")
    path = ask("The project file (type its path, or drag it into this window)",
               example="D:\\Downloads\\MyMod.deltapatch")
    if not path:
        return
    if not os.path.isfile(path):
        print(paint("  There is no file %s." % path, "err"))
        return pause()
    before = {os.path.normcase(p.path) for p in projects.all_projects()}
    run("project", "import", path)
    added = [p for p in projects.all_projects() if os.path.normcase(p.path) not in before]
    if added:
        _project = added[0]
    pause()


def near_at_hand():
    """Where a project file is saved unless another place is typed: the desktop when there is one, else home."""
    home = os.path.expanduser("~")
    desktop = os.path.join(home, "Desktop")
    return desktop if os.path.isdir(desktop) else home


def share():
    """Share projects as a project file, as project export does: which projects, which kind of file and where
    it goes. It is signed when the signing key of the first project (or the default one) is there."""
    every = [p for p in projects.all_projects() if not p.missing]
    heading("Share projects as a project file")
    chosen = every
    if len(every) > 1:
        for i, p in enumerate(every, 1):
            print("  %s  %s" % (paint(str(i), "code"), p.title))
        picks = ask("Which ones go in it (their numbers, separated by spaces; Enter: every one)")
        if picks:
            numbers = picks.replace(",", " ").split()
            if not all(n.isdigit() and 1 <= int(n) <= len(every) for n in numbers):
                print(paint("  Type numbers from the list above.", "warn"))
                return pause()
            chosen = [every[int(n) - 1] for n in dict.fromkeys(str(int(n)) for n in numbers)]
    print("  1  .deltapatch   opens DeltaPatcher with a double-click")
    print("  2  .html         a page anyone can read in a browser")
    ext = {"1": ".deltapatch", "2": ".html"}.get(ask("Choose 1 or 2", "1"))
    if not ext:
        print(paint("  Type 1 or 2.", "warn"))
        return pause()
    name = chosen[0].title if len(chosen) == 1 else "Projects"
    out = ask("Save it as", os.path.join(near_at_hand(), name + ext))
    if not presets.is_project_file(out):
        out += ext
    argv = ["project", "export"] + [p.path for p in chosen] + ["--out", out]
    key = config.load(chosen[0])["signing_key"] or signing.default_key_path()
    if os.path.isfile(key) and signing.can_sign():
        argv.append("--sign=" + key)
    run(*argv)
    pause()


def open_wiki():
    from . import cli
    heading("The wiki")
    run("wiki")
    print("  One guide:  %s" % paint(cli.prog_name() + " wiki NAME", "code"))
    print("  (quickstart, players, publishing, vps, cli, docker, mcp, format, troubleshooting, security)")
    pause()


# each choice: (label, hint, action); two hints are (with several projects, with one)
PROJECT_CHOICES = [
    ("Choose another project", "the menu then works on that one", choose_project),
    ("Add projects from a project file", "a .deltapatch or .html file from a publisher", import_file),
    ("Share projects as a project file", "to give to players; signed when you have a signing key", share),
    ("Remove a project", "its settings; never its folders", remove_project),
    ("Remove every project", "all but Default; never their folders", remove_every),
]
SEVERAL = (choose_project, remove_project, remove_every, update_all)     # shown when there is more than one project


def numbered(choices, several):
    """The choices shown, numbered from 1: [(number, label, hint, action)]."""
    shown = [c for c in choices if several or c[2] not in SEVERAL]
    return [(str(i), label, hint if isinstance(hint, str) else hint[0 if several else 1], action)
            for i, (label, hint, action) in enumerate(shown, 1)]


def projects_menu():
    """The Projects entry: what can be done with projects, one of them, then back to the menu."""
    heading("Projects")
    choices = numbered(PROJECT_CHOICES, len(projects.all_projects()) > 1)
    for key, label, hint, _action in choices:
        print("  %s  %-34s %s" % (paint(key, "code"), label, hint))
    print("  %s  Back to the menu" % paint("0", "code"))
    while True:
        pick = ask("Type a number and press Enter")
        if pick in ("", "0"):
            return
        action = next((a for k, _l, _h, a in choices if k == pick), None)
        if action is not None:
            return action()
        print(paint("  Type one of the numbers above.", "warn"))


CHOICES = [
    ("Release a new version", "sends players only what changed", release),
    ("Set up where releases go", "your server, a folder, or this computer", setup),
    ("Update a folder", "gets the latest release", update),
    ("Update all projects", "each one's folder, several at the same time", update_all),
    ("Projects", ("choose, add, share or remove projects", "add projects from a project file, or share them"),
     projects_menu),
    ("Open the wiki", "every guide, in your browser", open_wiki),
    ("Update DeltaPatcher", "itself, to the newest version", update_self),
    ("Type commands yourself", "every command, at a prompt", commands),
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


def run_menu(project=None):
    """The menu, working on `project` (None: Default) until another is chosen."""
    global _project
    before, _project = _project, project
    try:
        while True:
            cfg = load()
            several = len(projects.all_projects()) > 1
            shown = numbered(CHOICES, several)
            heading("%s %s" % (APP_NAME, __version__))
            newest = newer_out(cfg)
            if newest:
                print(paint("DeltaPatcher %s is out: choose %s to update." %
                            (newest, next(k for k, _l, _h, a in shown if a is update_self)), "warn"))
            if several:
                print("Project: %s" % paint(_project.title if _project is not None else projects.main_project().title,
                                            "head"))
            goes = where(cfg)
            if cfg["publish_src"] and goes:
                print("Your release: %s  ->  %s" % (cfg["publish_src"], goes))
            print()
            print("What do you want to do?")
            for key, label, hint, _action in shown:
                print("  %s  %-26s %s" % (paint(key, "code"), label, hint))
            print("  %s  Exit" % paint("0", "code"))
            try:
                choice = _input("\nType a number and press Enter: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if choice == "0":
                return
            action = next((a for k, _l, _h, a in shown if k == choice), None)
            if action is None:
                print(paint("Type one of the numbers above.", "warn"))
                continue
            try:
                action()
            except Back:
                pass
    finally:
        _project = before
