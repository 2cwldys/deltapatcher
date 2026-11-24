"""Make a compatibility fixture: the settings, projects, release and updated game folder one version of
DeltaPatcher writes, exactly as it writes them, for tests/test_compat.py.

    python tests/fixtures/compat/make_fixture.py CODE NAME

CODE is that version's code (a folder holding its deltapatcher/ package: git archive, or a checkout); NAME
the fixture's folder name, such as 0.3.0. Make one for every released version, and never change one once
it is committed: it stands for what people who used that version have on their computers.

What it does, with that version's command line: publish release 1 (a protected file among its files),
save the folder and the source, update the folder, change a protected file the way a player would,
publish release 2 (one file changed, one new, one removed), update again (so the folder holds that
version's rollback copy), and when the version has projects: a second project, a project file kept
elsewhere, a few of DeltaPatcher's own settings, and the project the app shows. Then a note and an unknown
section are added to the settings by hand, as a person might. Paths become @FIXTURE@/... (with / between
their parts, so a fixture made on Windows works on every system), the home folder is a neutral stand-in,
and names git or the secret scan keep out are stored under others (main.ini for deltapatcher.ini,
dot-deltapatcher for .deltapatcher).
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TEXT = (".ini", ".json", ".jsonl", ".log", ".txt", ".cfg", ".map")
SKIP = (".deltapatcher-hashcache.json",)        # a publish cache git ignores: a fixture holds what git keeps


def main(code, name):
    code = os.path.abspath(code)
    work = tempfile.mkdtemp(prefix="dp-compat-")
    home = os.path.join(work, "home")
    os.makedirs(home)
    env = dict(os.environ, PYTHONPATH=code, NO_COLOR="1",
               DELTAPATCHER_CONFIG=os.path.join(work, "settings", "deltapatcher.ini"),
               HOME=home, USERPROFILE=home, APPDATA=os.path.join(home, "AppData", "Roaming"),
               LOCALAPPDATA=os.path.join(home, "AppData", "Local"), XDG_CONFIG_HOME=os.path.join(home, ".config"))
    for key in ("DELTAPATCHER_PROJECT", "DELTAPATCHER_LOG"):
        env.pop(key, None)

    def dp(*args):
        r = subprocess.run([sys.executable, "-m", "deltapatcher", "--no-log"] + list(args), env=env, cwd=work,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("%s failed (%d):\n%s%s" % (" ".join(args), r.returncode, r.stdout, r.stderr))
        return r.stdout

    def write(rel, data):
        path = os.path.join(work, "src", *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    src, release, game = (os.path.join(work, n) for n in ("src", "release", "game"))
    write("readme.txt", b"DeltaPatcher compatibility fixture\n")
    write("data/pack.bin", bytes(range(256)) * 8)
    write("maps/one.map", b"map one\n" * 50)
    write("config/user.cfg", b"volume=5\n")
    v1 = tree(src)
    dp("publish", src, release, "--name", "Compat", "--version", "1", "--protect", "config/user.cfg", "--no-htaccess")
    dp("config", "set", "install_dir=" + game, "source_url=" + release)
    dp("update", game, "--source", release)                          # named: an older update needs them
    with open(os.path.join(game, "config", "user.cfg"), "wb") as f:
        f.write(b"volume=9\n")                                       # the player's own setting
    write("data/pack.bin", bytes(reversed(range(256))) * 8)
    write("extra/new.txt", b"a new file in release 2\n")
    os.remove(os.path.join(src, "maps", "one.map"))
    v2 = tree(src)
    dp("publish", src, release, "--name", "Compat", "--version", "2", "--protect", "config/user.cfg", "--no-htaccess")
    dp("update", game, "--source", release)

    projects = os.path.isfile(os.path.join(code, "deltapatcher", "projects.py"))
    if projects:
        dp("project", "new", "Map Pack")
        dp("--project", "Map Pack", "config", "set", "install_dir=" + os.path.join(work, "maps"), "source_url=" + release)
        external = os.path.join(work, "external", "Server.ini")
        os.makedirs(os.path.dirname(external))
        subprocess.run([sys.executable, "-c", "from deltapatcher import config; import sys; "
                        "open(sys.argv[1], 'w', encoding='utf-8', newline='\\n').write("
                        "config.placeholder(project=True, title='Dedicated Server'))", external],
                       env=env, check=True)
        dp("project", "add", external)
        dp("--project", "Dedicated Server", "config", "set", "install_dir=" + os.path.join(work, "server"),
           "source_url=" + release)
        dp("config", "set", "parallel_updates=2", "current_project=Map Pack.ini")

    main_ini = os.path.join(work, "settings", "deltapatcher.ini")
    with open(main_ini, "a", encoding="utf-8", newline="\n") as f:
        f.write("\n; a note the user wrote, which every later version must keep\n[mine]\nnote = kept\n")

    out = os.path.join(HERE, name)
    if os.path.exists(out):
        sys.exit("%s exists: a fixture is never made twice" % out)
    copy(os.path.join(work, "settings"), os.path.join(out, "settings"), work)
    os.rename(os.path.join(out, "settings", "deltapatcher.ini"), os.path.join(out, "settings", "main.ini"))
    for folder in ("release", "game", "external"):
        if os.path.isdir(os.path.join(work, folder)):
            copy(os.path.join(work, folder), os.path.join(out, folder), work)
    os.rename(os.path.join(out, "game", ".deltapatcher"), os.path.join(out, "game", "dot-deltapatcher"))
    del v1["config/user.cfg"], v2["config/user.cfg"]                 # protected: the player's edit stays
    with open(os.path.join(out, "expect.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"version": name, "projects": projects, "release1": v1, "release2": v2,
                   "user_cfg": "volume=9\n"}, f, indent=1, sort_keys=True)
        f.write("\n")
    shutil.rmtree(work, ignore_errors=True)
    print("made", out)


def tree(root):
    out = {}
    for top, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".deltapatcher"]
        for n in files:
            p = os.path.join(top, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root).replace(os.sep, "/")] = hashlib.sha256(f.read()).hexdigest()
    return out


def copy(src, dst, work):
    """Copy a folder, with every absolute path under `work` in its text files written as @FIXTURE@."""
    forms = sorted({work, work.replace("\\", "/"), work.replace("\\", "\\\\"), os.path.realpath(work)}, key=len, reverse=True)
    for top, _dirs, files in os.walk(src):
        for n in files:
            if n in SKIP:
                continue
            s = os.path.join(top, n)
            d = os.path.join(dst, os.path.relpath(s, src))
            os.makedirs(os.path.dirname(d), exist_ok=True)
            with open(s, "rb") as f:
                data = f.read()
            if n.lower().endswith(TEXT) or os.path.basename(top) == ".deltapatcher":
                text = data.decode("utf-8")
                for form in forms:
                    text = text.replace(form, "@FIXTURE@")
                # / between a path's parts (\\ in JSON, \ elsewhere, on Windows), so it works on every system
                text = re.sub(r'@FIXTURE@((?:\\\\|\\|/)[^"\r\n;]*)',
                              lambda m: "@FIXTURE@" + m.group(1).replace("\\\\", "/").replace("\\", "/"), text)
                data = text.encode("utf-8")
            with open(d, "wb") as f:
                f.write(data)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
