<p align="center">
  <img src="docs/images/logo.png" alt="DeltaPatcher" width="200">
</p>
<h1 align="center">Delta Patcher</h1>
<p align="center">Delta patching with SHA-256 blobs, for any game and any file.</p>

Delta patching with SHA-256 blobs. Publish a folder, serve it from anywhere (your own PC, a VPS, any
static web host), and let clients download only the files that changed.

It works for any game and any kind of file: `.pk3`, `.zip`, `.bsp`, textures, executables. DeltaPatcher
never looks inside a file. Every file is just bytes with a SHA-256 hash, so a changed file is fetched
again and an unchanged one never is, whatever its extension.

- **A window** (tkinter) with a tray icon, for people who just want it to work.
- **A command line** for scripts, servers and people who prefer one: `cli\deltapatcher.bat` and
  `cli/deltapatcher.sh` run it straight from the repository ([cli/README.md](cli/README.md)).
- **Self-contained programs** (PyInstaller): `DeltaPatcher.exe` and `deltapatcher-cli.exe`.
- **Signed releases**: players' updaters check that a release really comes from you.
- **Deploys to a VPS over SSH**, using your keys safely, and an `.htaccess` for Apache hosts
  (see [SECURITY.md](SECURITY.md)).
- **A settings file** (`deltapatcher.ini`, made on first run with every setting explained) and **logs**
  of everything it does.
- Licensed MIT.

![The window](docs/images/1-update.png)

## Documentation

| | |
| --- | --- |
| [Quick start](docs/QUICKSTART.md) | Try it on your own PC in five minutes |
| [For players](docs/PLAYERS.md) | Keeping a game folder up to date |
| [Publishing a release](docs/PUBLISHING.md) | Releasing a game, mod or asset pack, protecting player files, giving players the updater |
| [Setting up a VPS](docs/VPS-SETUP.md) | Deploy user, SSH key, host key, Caddy or nginx, Windows Server |
| [Command line reference](docs/CLI.md) | Every command and option, exit codes, launcher and build-server examples |
| [The store and manifest format](docs/FORMAT.md) | The files, the path rules, the update algorithm (for your own client) |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Every error message and what to do |

## How it works

```
 your files            publish               the store                 serve / deploy          clients
 ----------            ------->     manifest.json  (paths, sizes,      ------------->     update: compare,
 game/                              SHA-256 of each)                  any web server       download only the
   data/pak0.pk3                    blobs/<sha256>  (one per distinct  or a folder         missing blobs,
   maps/de_x.bsp                     file content)                                         check, then swap in
```

1. **Publish** hashes every file in a folder and writes a *store*: `manifest.json` plus `blobs/`, where
   each distinct file content is stored once under its SHA-256. Two identical files are one blob.
2. **Serve** the store: it is plain static files, so any web server can serve it. DeltaPatcher has a
   small built-in server for testing (`http://localhost:8000/` by default).
3. **Deploy** sends a store to a VPS (over SFTP) or a folder, uploading only the blobs the target
   lacks. The new manifest goes in last, so clients never see a half-uploaded release.
4. **Update** compares a client's folder with the manifest and downloads only the blobs it needs.
   Each one is checked against its SHA-256 *before* anything in the folder changes; the update is
   journalled and can be rolled back.

The *source of truth* is just an address: `http://localhost:8000/` (the default), an
`https://` URL anywhere, or a folder path (`D:\store`, `/mnt/share/store`, `file:///...`).

## Quick start

### With the window

Run `DeltaPatcher.exe` (or `python -m deltapatcher` from source).

1. **Publish** tab: choose the folder with your files and a *store* folder, then click **Publish**.
2. Click **Serve locally**: the store is now at `http://localhost:8000/` on this PC.
3. **Update** tab: the source is already `http://localhost:8000/`; choose a folder to update and click
   **Update**. Change a file in your published folder, publish again, update again: only that file
   travels.

### With the command line

```
deltapatcher publish ./game ./store --name "My Game" --version 1.0
deltapatcher serve ./store
deltapatcher update ./my-install --source http://localhost:8000/
deltapatcher update ./my-install --check          # what would change (exit 2 when out of date)
deltapatcher rollback ./my-install                # undo the last update
```

From source, `python -m deltapatcher <command>` is the same as `deltapatcher <command>`.

## Hosting on a VPS

You need: a VPS with SSH (OpenSSH), a web server on it that serves one folder (examples below),
and the OpenSSH client on your PC (built into Windows 10 and later, and into Linux and macOS).

### 1. A key for deploys

In the window: **Server** tab > **New key...**. In a terminal: `deltapatcher ssh keygen`.

This makes an ed25519 key at `~/.ssh/deltapatcher_ed25519` with a passphrase. The private key is
written once, readable by your account only, and an existing key is never overwritten. The
passphrase is not stored anywhere.

### 2. Let the key in on the server

**Copy server line** (window) or `deltapatcher ssh pubkey` (terminal) gives one line like:

```
restrict,command="internal-sftp" ssh-ed25519 AAAA... deltapatcher@my-pc
```

Add it to `~/.ssh/authorized_keys` of the user that deploys, on the server. `restrict,command="internal-sftp"`
limits the key to SFTP: it can upload files, but it cannot open a shell or forward ports. That is
all DeltaPatcher needs. Use a separate user that owns only the web folder, for example:

```
sudo adduser --disabled-password deploy
sudo mkdir -p /var/www/game && sudo chown deploy:deploy /var/www/game
sudo -u deploy mkdir -p ~deploy/.ssh && sudo -u deploy nano ~deploy/.ssh/authorized_keys
```

### 3. Unlock the key

**Unlock key** (window) or `deltapatcher ssh unlock` adds the key to `ssh-agent`, which asks for the
passphrase once and keeps the key unlocked until you log out. Deploys run without asking again.

On Windows the agent service is off by default. Turn it on once, in an administrator PowerShell:

```
Get-Service ssh-agent | Set-Service -StartupType Automatic
Start-Service ssh-agent
```

### 4. Confirm the server's host key

The first time, DeltaPatcher shows the server's host key fingerprints and asks you to confirm them.
Compare with what your hosting provider shows, or run this on the server:

```
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

Confirmed keys are kept in DeltaPatcher's own `known_hosts` (in your user config folder). Every later
connection refuses a server whose key changed.

### 5. Deploy

Window: **Server** tab, fill in host, user, the remote folder (`/var/www/game`), then **Test
connection** and **Deploy**. Terminal:

```
deltapatcher deploy ./store --host example.com --user deploy --remote-root /var/www/game
deltapatcher deploy ./store --to-folder D:/www/game        # or a folder (local web root, share)
```

Add `--dry-run` to see what would be sent. Blobs that none of the new manifest, the one the server
serves now and the one before it name are removed (`--no-prune` keeps them). If a manifest on the
server cannot be read, nothing is removed. If the upload fails at any point before the new manifest
is complete on the server, the server keeps serving the release it served before.

### 6. Serve the folder

The folder holds `manifest.json`, `manifest.previous.json` and `blobs/`. Serve it over HTTPS.

Caddy:

```
game.example.com {
    root * /var/www/game
    file_server
    @manifest path /manifest.json /manifest.previous.json
    header @manifest Cache-Control "no-cache"
    header /blobs/* Cache-Control "public, max-age=31536000, immutable"
}
```

nginx:

```
server {
    server_name game.example.com;
    root /var/www/game;
    autoindex off;
    location = /manifest.json { add_header Cache-Control "no-cache"; }
    location /blobs/ { add_header Cache-Control "public, max-age=31536000, immutable"; }
}
```

Clients then use `https://game.example.com/` as their source. Blobs never change once written (their
name is their hash), so they can be cached forever; only the manifest must not be cached.

**A Windows server** works too (OpenSSH Server for Windows plus IIS or Caddy). Give the remote folder
with forward slashes, for example `C:/www/game`. The `command="internal-sftp"` restriction needs
OpenSSH's own sshd; if your server does not accept it, use `deltapatcher ssh pubkey --full` (a key
with `restrict` only).

## What a client update does

- Downloads only blobs whose content it does not have. The same content under two names is fetched once.
- Checks every blob against its SHA-256 before the folder changes. A damaged or missing blob on the
  server stops the update with the folder untouched.
- Removes a file the new release no longer lists only if DeltaPatcher installed it and it is
  unchanged since. Files you added yourself, and files that were already in the folder before
  DeltaPatcher first installed them, are never removed. A release that only changes the letter case
  of a path (`Maps/` to `maps/`) keeps the file.
- Leaves *protected* files alone once they exist (`--protect "config/*.cfg"` when publishing), so a
  player's settings survive updates.
- Journals the update: if it fails, it is undone at once; if the program is killed or the PC loses
  power, the next run undoes it first. `rollback` undoes the last completed update, also after a
  later update failed.
- Resumes interrupted downloads (HTTP Range), including a connection that simply closes early.
- Refuses a manifest with any path that could leave the folder (`..`, absolute paths, drive letters,
  links or junctions on the way, Windows device names such as `CON` or `con .txt`) or reach its own
  `.deltapatcher` folder.
- Locks the folder with an operating-system lock while it works, so two updates never run at once,
  and a crash never leaves the folder locked.

Everything it keeps is in `<folder>/.deltapatcher/` (state, hash cache, download cache, rollback).

## Security

Releases can be signed, and players' updaters then refuse anything not signed by your key. Every
file is checked against its SHA-256 before it is used, and manifests are treated as untrusted input.
SSH keys are read only by OpenSSH, and passphrases are never stored. Server host keys are confirmed
once and checked strictly after. The full list, what is left to you, and how to report a problem
privately: **[SECURITY.md](SECURITY.md)**.

## The store's files

`manifest.json`:

```json
{
 "format": 1,
 "name": "My Game",
 "version": "1.0",
 "published": "2026-11-23T20:00:00Z",
 "count": 2,
 "bytes": 1234,
 "protect": ["config/*.cfg"],
 "files": [
  {"path": "data/pak0.pk3", "sha256": "<64 hex characters>", "size": 1000},
  {"path": "readme.txt", "sha256": "<64 hex characters>", "size": 234}
 ]
}
```

`blobs/<sha256>`: the file's bytes. `manifest.previous.json`: the release before, kept so clients that
started from it can finish.

A `.deltaignore` file in the published folder lists globs to leave out (one per line, `#` comments, a
trailing `/` for folders). `.git/`, `.svn/`, `Thumbs.db`, `desktop.ini` and `*.tmp` are always left out,
and links and junctions are never followed.

## Building the programs

Windows (makes `.venv`, installs what the build needs, builds, then runs the built window's self-test):

```
powershell -ExecutionPolicy Bypass -File build.ps1          # or: build.ps1 -Test to run the tests first
```

Linux or macOS: `./build.sh`. The results are in `dist/`:

- `DeltaPatcher.exe`: the window. Its icon is on the file, the window and the tray.
- `deltapatcher-cli.exe`: the command line (`deltapatcher-cli.exe --help`).

The icon is drawn by `tools/make_icon.py` (Pillow) into `deltapatcher/assets/`.

New, unsigned executables are sometimes flagged by antivirus reputation checks. If that happens,
sign the programs, or run from source.

## Running from source

Clone with [Git LFS](https://git-lfs.com) installed: images, archives and programs (`*.png`, `*.ico`,
`*.zip`, `*.exe`, `*.dll`, `*.so` and the like, see `.gitattributes`) are stored with LFS. Without it
the icon files are small text pointers and the window starts without its icon.

Python 3.8 or later. The core (publish, serve, update, deploy) uses only the standard library.
Optional packages: `pystray` and `Pillow` for the tray icon, `cryptography` to make keys inside the
window (`pip install -r requirements.txt`). Without `cryptography`, `deltapatcher ssh keygen` uses
`ssh-keygen` in the terminal instead.

```
python -m deltapatcher               # the window
python -m deltapatcher --help        # the command line
pip install .[all]                   # installs the deltapatcher and deltapatcher-gui commands
```

## Tests

```
python -m unittest discover -s tests -v
```

They publish, serve, update and roll back real folders over HTTP, and check that damaged blobs,
missing blobs and malicious manifests are refused without changing anything. They also run deploys
to a folder and through a stand-in sftp server, and make real keys checked against `ssh-keygen`.
`DP_SKIP_GUI_TEST=1` skips the window test on a machine without a display.

## Limitations

- SFTP uploads report progress per batch of files, not per byte, and a blob whose upload was cut
  is sent again whole (downloads resume; uploads do not).
- Publishing the same files again keeps the manifest as it was; publish only when something changed
  to make a new "previous release".
- The built-in server is for testing and LANs; use a real web server with HTTPS on the internet.
- On Linux the tray icon needs a desktop with a tray (AppIndicator or an X tray).

## License

MIT. See [LICENSE](LICENSE).
