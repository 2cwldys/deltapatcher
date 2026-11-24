"""Screenshots of the app for the README, from a real demo release.

    python tools/capture_screens.py [DEMO_FOLDER]      (default: a folder named DeltaPatcherDemo beside the repository)

It makes a small demo game, publishes version 1.4 signed with a demo key, installs it, changes the
release to 1.5, then runs the app with --capture: the app serves the store, publishes 1.5, checks
the demo game against it and saves docs/images/app-<page>.png. The demo folder is removed after.
Needs the engine (dist/deltapatcher-cli, from build.ps1 -EngineOnly) and the app's packages (npm ci in app/).
"""

import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "dist", "deltapatcher-cli" + (".exe" if os.name == "nt" else ""))


def main():
    # --app EXE: run a packaged app (dist/app/win-unpacked/DeltaPatcher.exe, ...) instead of app/ from source;
    # --out DIR: write the pictures there instead of docs/images
    argv = sys.argv[1:]
    packaged = out_dir = None
    if "--app" in argv:
        i = argv.index("--app")
        packaged = os.path.abspath(argv[i + 1])
        del argv[i:i + 2]
    if "--out" in argv:
        i = argv.index("--out")
        out_dir = os.path.abspath(argv[i + 1])
        del argv[i:i + 2]
    out_dir = out_dir or os.path.join(ROOT, "docs", "images")
    demo = os.path.abspath(argv[0] if argv else os.path.join(os.path.dirname(ROOT), "DeltaPatcherDemo"))
    if not os.path.isfile(EXE):
        sys.exit("no engine at %s: build it first (build.ps1 -EngineOnly)" % EXE)
    shutil.rmtree(demo, ignore_errors=True)
    env = dict(os.environ, DELTAPATCHER_CONFIG=os.path.join(demo, "deltapatcher.ini"),
               DELTAPATCHER_LOG=os.path.join(demo, "logs", "deltapatcher.log"))
    env.pop("ELECTRON_RUN_AS_NODE", None)   # set inside VS Code: it would make Electron run as plain Node
    env["DELTAPATCHER_CAPTURE"] = "1"       # a packaged app takes --capture only with this set

    def engine(*args, stdin=""):
        r = subprocess.run([EXE, "--json"] + list(args), env=env, input=stdin.encode(), stdout=subprocess.PIPE)
        last = json.loads(r.stdout.decode().strip().splitlines()[-1])
        if not last.get("ok") and last.get("event") != "result":
            sys.exit("engine %s failed: %s" % (args[0], last))
        return last

    def put(rel, data):
        p = os.path.join(demo, "MyGame-release", *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)

    put("data/pak0.pk3", os.urandom(2 << 20))
    put("data/pak1.pk3", os.urandom(1 << 20))
    put("maps/arena.bsp", os.urandom(900 << 10))
    put("textures/hd.zip", os.urandom(3 << 20))
    put("config/user.cfg", b"sensitivity 3\n")
    put("readme.txt", b"My Game 1.4\n")
    key = os.path.join(demo, "keys", "signing-key.pem")
    engine("sign", "keygen", "--path", key, "--passphrase-stdin", stdin="\n")
    settings = {
        "source_url": "http://localhost:8765/", "install_dir": os.path.join(demo, "MyGame"),
        "publish_src": os.path.join(demo, "MyGame-release"), "store_dir": os.path.join(demo, "store"),
        "name": "My Game", "version": "1.4", "protect": "config/*.cfg", "sign": "yes", "signing_key": key,
        "serve_port": "8765", "ssh_host": "game.example.com", "ssh_user": "deploy",
        "ssh_key": os.path.join("C:\\Users\\you" if os.name == "nt" else "/home/you", ".ssh", "deltapatcher_ed25519"),
        "remote_root": "/var/www/game", "deploy_mode": "sftp",
    }
    engine("config", "set", *["%s=%s" % kv for kv in settings.items()])
    engine("publish", settings["publish_src"], settings["store_dir"], "--name", "My Game", "--version", "1.4", "--sign", key)
    engine("update", settings["install_dir"], "--source", settings["store_dir"])
    put("readme.txt", b"My Game 1.5\n")
    put("maps/arena2.bsp", os.urandom(700 << 10))
    engine("config", "set", "version=1.5")

    if packaged:
        subprocess.run([packaged, "--capture=" + out_dir], env=env, check=True, timeout=180)
    else:
        app = os.path.join(ROOT, "app")
        npm = "npm.cmd" if os.name == "nt" else "npm"
        subprocess.run([npm, "run", "build"], cwd=app, check=True, env=env)
        electron = os.path.join(app, "node_modules", ".bin", "electron.cmd" if os.name == "nt" else "electron")
        subprocess.run([electron, ".", "--capture=" + out_dir], cwd=app, env=env, check=True, timeout=180)
    for _ in range(40):                      # the app's last engine runs may still be finishing
        shutil.rmtree(demo, ignore_errors=True)
        if not os.path.exists(demo):
            break
        time.sleep(0.5)
    for name in ("update", "publish", "server", "settings"):
        p = os.path.join(out_dir, "app-%s.png" % name)
        print(p, os.path.getsize(p) if os.path.isfile(p) else "MISSING")


if __name__ == "__main__":
    main()
