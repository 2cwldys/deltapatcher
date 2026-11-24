# Command line reference

```
deltapatcher <command> [options]
```

`deltapatcher` is any of:
- `cli\deltapatcher.bat` / `cli/deltapatcher.sh` in the repository ([cli/README.md](../cli/README.md)): the
  built program when there is one, else Python from source
- the command line from a release: `deltapatcher-cli-windows-x64.exe` (rename it
  `deltapatcher-cli.exe` if you like: the examples here use that name), or `deltapatcher-cli` from the Linux
  or macOS `.tar.gz`
- `python -m deltapatcher` from source, or the `deltapatcher` command after `pip install .`

With no command it prints the help. `deltapatcher menu` asks plain questions instead of taking commands:
release a new version, set up where releases go (your server, a folder, or this computer), update a folder,
or type commands at a prompt. Every answer is saved, so the second time is mostly Enter.

**Its window.** In a terminal it runs and returns, as any command does. In a window of its own on Windows
it keeps that window open, so nothing vanishes before it is read:
- double-clicked (the `.exe`, or `cli\deltapatcher.bat`): the menu above. Its *Type commands yourself* is a
  prompt, `deltapatcher>`, that takes one command after another (`help`, `help update`, `update ...`)
- started with a command from a shortcut or a launcher's own window: it waits for Enter when it is done
- `--close`: it closes as soon as it is done, with no prompt and no waiting. Use it in shortcuts and
  launchers that should not wait for anyone.

It never waits when a program reads its output (a pipe, a file, `--json`).

The DeltaPatcher app (`app/`) runs this same command line, in its machine mode (`--json`, below).

The settings file (`deltapatcher.ini`, see [Settings and logs](#settings-and-logs)) fills in whatever you
leave out: the folder to release, its name and version, what to leave out and what players keep, where
releases go (the server, a folder, or this computer), `--source`, `--trust-key`, the signing key and the SSH
options. The app and the command line share it.

**Projects.** Each game, mod or server gets a project: a settings file of its own, with the folder it keeps
up to date and where from, and the folder it releases and where that goes. `--project NAME`, before or
after any command, makes it work on that project. Without it, commands use Default, the main settings
file, so a script never follows what the window has picked. `deltapatcher projects` lists them; see
[project](#project) to make, add, rename and remove them.

## Options for every command

| Option | |
| --- | --- |
| `--log FILE` | Write the log here instead of the usual place |
| `--no-log` | Write no log file |
| `--no-color` | Plain text. Colour is used only in a terminal that shows it, never in files or pipes; `NO_COLOR=1` works too. |
| `--close` | Close as soon as the command is done: no prompt, no waiting for Enter in a window of its own (see above) |
| `--project NAME` | The project to work on: its name or its settings file. Left out: the environment variable `DELTAPATCHER_PROJECT`, else Default. |

In a terminal the output uses the app's colours: white for what was done, beige-yellow for what needs
your attention (and amber, in bold, for errors), the app's blue for things to copy or compare (keys,
fingerprints, commands), and its teal for the progress bar.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Done (or already up to date) |
| 1 | Failed. For `update`, the folder is as it was before. With `--all`: at least one project failed; every other one finished. |
| 2 | `check` or `update --check`: an update is available (with `--all`: for at least one project) |
| 130 | Stopped with Ctrl+C |

## release

```
deltapatcher release [SRC] [--version VERSION] [--name NAME] [--leave-out PATH]... [--protect PATH]... [--sign [KEYFILE]]
                     [--to sftp|folder|local] [--to-folder FOLDER] [--host HOST] [--user USER] [--port 22] [--key KEYFILE]
                     [--remote-root PATH] [--dry-run] [--no-htaccess] [--no-prune]
```

Publish the folder, then send it where players download it from, in one step: what `publish` and then
`deploy` (or `serve`) do. Everything left out comes from the settings file, so once the server is saved
a new version is just:

```
deltapatcher release --version 1.1
```

| Option | |
| --- | --- |
| `SRC` | The folder to release. Without it: the saved `publish_src`. |
| `--to sftp` | The saved server over SSH (`ssh_host`, `ssh_user`, `remote_root`, or the options). |
| `--to folder` | A folder: `--to-folder`, or the saved `deploy_folder`. |
| `--to local` | This computer, to test: serves it at `http://localhost:<serve_port>/` until Ctrl+C. |
| `--dry-run` | Publish, then say what would be sent; send nothing. |

Without `--to`: the saved `deploy_mode`. The store is the saved `store_dir`, else a folder DeltaPatcher keeps
beside its settings file, one per folder released. The same version again with changed files is fine (a
*shadow patch*): only what changed is added and sent, and players get it. After a release it prints the
saved `public_url`, the address players update from.

## publish

```
deltapatcher publish [SRC] [STORE] [--name NAME] [--version VERSION] [--leave-out PATH]... [--protect PATH]... [--sign [KEYFILE]] [--no-htaccess] [--no-prune]
```

Hash the folder `SRC` and write the store `STORE` (`manifest.json`, `blobs/`, `sigs/`, `.htaccess`), sent
nowhere: `release` does this and the sending. Without `SRC`: the saved folder; without `STORE`: the saved
store, else DeltaPatcher's own one for that folder. `--name` and `--version` default to the saved ones. See
[Publishing a release](PUBLISHING.md).

| Option | |
| --- | --- |
| `--name` | Shown to players and in the log |
| `--version` | Shown to players and in the log |
| `--leave-out PATH` | A file or folder inside SRC to leave out of the release (a folder: everything in it). Repeat it for more. A pattern such as `*.log` works too. Without it: the saved `leave_out` list. |
| `--protect PATH` | A file or folder inside SRC that players keep once they have it, such as their settings. Repeat it for more; patterns work too. Without it: the saved `protect` list. |
| `--sign [KEYFILE]` | Sign the release (optional). Without a file: the settings file's `signing_key`, else the default key. Asks the key's passphrase. |
| `--no-htaccess` | Do not write the `.htaccess` for Apache |
| `--no-prune` | Keep blobs and signatures no manifest names any more |

## serve

```
deltapatcher serve [STORE] [--bind 127.0.0.1] [--port 8000] [--verbose]
```

Serve a store over HTTP, for testing or a LAN. It serves only `manifest.json`,
`manifest.previous.json`, `blobs/<hash>` and `sigs/<hash>.sig`, never a listing. `--bind 0.0.0.0` makes it reachable
from other machines. For the internet, use a real web server with HTTPS.

## update

```
deltapatcher update [FOLDER] [--source URL] [--check] [--trust-key KEY] [--workers 4] [--header "Name: value"]...
deltapatcher update --all [--jobs N]
```

Bring `FOLDER` (default: the project's saved folder) up to the release at `URL` (an `http(s)://` address, a
folder path or a `file://` URL; default: the remembered one, else `http://localhost:8000/`).

| Option | |
| --- | --- |
| `--check` | Say what would change, change nothing. Exit 2 when out of date. |
| `--trust-key KEY` | The publisher's key (`ed25519:...`): the release must be signed with it. Without it, unsigned releases are accepted, and the first signed one's key is trusted (and only that key after it). `off` skips signature checks. |
| `--workers N` | Downloads at the same time (default 4) |
| `--header` | Extra request header, for a host that needs one (an access token, for example). It is sent only to that host, never after a redirect to another one. |
| `--all` | Every project with a folder to update, each from its own settings. A folder, `--source`, `--trust-key` and `--header` are refused with it, so each project's settings, and any token, stay its own. |
| `--jobs N` | With `--all`: how many projects at the same time (default: the setting `parallel_updates`, 3) |

With `--all`, each project's lines start with its name, and a summary closes the run: up to date, updated,
or failed and why. One project failing never stops the others.

## check

```
deltapatcher check [FOLDER] [--source URL] [--trust-key KEY]
deltapatcher check --all [--jobs N]
```

What an update would change, changing nothing: the files new, changed and removed, and how much would be
downloaded. The same as `update --check`. Exit code 2 when an update is available.

## rollback

```
deltapatcher rollback [FOLDER]
```

Undo the last completed update of `FOLDER` (default: the project's saved folder).

## deploy

```
deltapatcher deploy [STORE] --to-folder FOLDER [--dry-run] [--no-prune]
deltapatcher deploy [STORE] [--host HOST] [--user USER] [--port 22] [--key KEYFILE] [--remote-root PATH] [--dry-run] [--no-prune]
```

Send a store to a folder, or to a server over SFTP. Only missing blobs are sent; the manifest goes in
last. The first time a server is used, its host key is shown for you to confirm.

| Option | |
| --- | --- |
| `--to-folder` | Deploy to a folder (local web root, network share) |
| `--host`, `--user`, `--port` | The server |
| `--key` | The private key file (default: the remembered one, else `~/.ssh/deltapatcher_ed25519`) |
| `--remote-root` | The folder on the server its web server serves, with forward slashes |
| `--dry-run` | Say what would be sent and removed, change nothing |
| `--no-prune` | Keep blobs no manifest on the target names any more |

## ssh

| Command | |
| --- | --- |
| `deltapatcher ssh keygen [--path FILE]` | Make a new ed25519 key with a passphrase. An existing file is never replaced. |
| `deltapatcher ssh pubkey [--path FILE] [--full]` | Print the line for the server's `authorized_keys`. Without `--full` it allows SFTP only. |
| `deltapatcher ssh unlock [--path FILE]` | Add the key to `ssh-agent` (asks for the passphrase once) |
| `deltapatcher ssh trust --host HOST [--port N] [--forget]` | Show the server's host key fingerprints and remember them after you confirm. `--forget` removes them, for after a server reinstall. |
| `deltapatcher ssh test --host HOST [--user U] [--remote-root PATH]` | Log in with SFTP and look at the remote folder |
| `deltapatcher ssh install-key [--host HOST] [--user U] [--key FILE]` | Put your key on the server: checks the host key first, then logs in once with the user's password (asked, or the first line of standard input with `--passphrase-stdin`) and adds the key's line, never twice. The password is used for that login only and never kept. Linux servers and Windows' OpenSSH server. |

## sign

| Command | |
| --- | --- |
| `deltapatcher sign keygen [--path FILE]` | Make a signing key (asks a passphrase) and print its public key, the `ed25519:...` text players use. An existing file is never replaced. |
| `deltapatcher sign pubkey [--path FILE]` | Print the public key of a signing key |

## config

```
deltapatcher config [--show]
```

Print where the settings file and the log are (the settings file is made, with every setting
explained, if it is missing). `--show` prints the file too.

## projects

```
deltapatcher projects
```

Every project: its name and settings file, the folder it keeps up to date and where from, and the folder
it releases and where that goes.

## project

```
deltapatcher project new NAME
deltapatcher project add FILE [--name NAME]
deltapatcher project rename NAME NEW
deltapatcher project remove NAME [--yes]
```

- `new`: a new project, its settings file in the `projects` folder beside the main settings file. Fill it in
  with `deltapatcher --project NAME config set install_dir=... source_url=...`, or in the app.
- `add`: list a project's settings file kept somewhere else (in a game's folder, on a share). `--name` gives
  it a name, written into that file.
- `rename`: the project's name changes; its file stays where it is.
- `remove`: a project in the `projects` folder is deleted with its settings file; an added one only leaves
  the list, and its file stays. The folders it updates or releases are never touched. It asks first in a
  terminal; `--yes` answers for scripts. Default cannot be removed.

A name has letters, digits, spaces and `- _ . ( )`, up to 60 characters, cannot start with a dash, and no
two projects share one (letter case aside).

## self-update

```
deltapatcher self-update [--check]
```

DeltaPatcher itself: is a newer release out on GitHub, and if so, put it in place of this command line
(compared by version number: 0.2.0 < 0.3.0 < 0.10.0). The file is checked against the SHA-256 GitHub gives
for it before it is used. `--check` only says (exit code 2 when a newer one is out). On Windows the old
program is kept as `<name>.old` until the next start, because a running program cannot be replaced there.
From source (Python), it says to use `git pull` instead.

Once a day, a command run in a terminal also says when a newer DeltaPatcher is out. The setting
`check_updates = no` turns that off; nothing is ever downloaded without asking.

## mcp

```
deltapatcher mcp
```

DeltaPatcher as an MCP server, for AI assistants such as Claude Code, Claude Desktop or VS Code: it speaks
the Model Context Protocol on standard input and output until the assistant closes them. Its tools check,
update, publish and release projects, show settings and keys, read these guides and run a self-test; none
of them ever takes a passphrase or a password. How to connect an assistant, and every tool:
[AI assistants (MCP)](../mcp/README.md).

## Machine mode (--json)

With `--json` every command writes one JSON object a line, which is how the app drives the engine:

```
{"event": "log", "text": "...", "style": "ok"}
{"event": "progress", "done": 123, "total": 456, "label": "downloading"}
{"event": "result", "ok": true, "data": {...}}
{"event": "error", "message": "...", "code": "host-unknown", "data": {...}}
```

The last line is `result` or `error`. Standard input carries what a person would type: with
`--passphrase-stdin` the first line is the passphrase; a later line `cancel` stops a download (the
folder stays as it was); closing standard input stops `serve`.

`update --all` and `check --all` add `queue`, `project-start` and `project-end` events, and every line
about one project carries its name (`project`) and settings file (`file`); the full list is at the top of
`deltapatcher/cli.py`. Commands made for programs:

| Command | |
| --- | --- |
| `config get` / `config set key=value ...` | Read every setting (with its explanation) / change settings |
| `ssh status` | The key (passphrase, permissions, ssh-agent) and whether the host is trusted |
| `ssh scan` | The host keys a server offers |
| `ssh trust --accept FP,FP` | Trust a server only if it still offers exactly these fingerprints |
| `ssh fix-permissions` | Make the private key readable by this account only |
| `sign status` | Is there a signing key, and does it have a passphrase |
| `projects` | Every project and what is set up in it |
| `version` | The version |

Error codes the app acts on: `host-unknown` (with the host's keys), `host-changed`, `key-permissions`,
`passphrase-needed`, `passphrase-short`, `no-signing-key`, `exists`, `stopped`.

## Settings and logs

**Settings**: `deltapatcher.ini`, made the first time DeltaPatcher runs, every setting with its default
and a line saying what it does. Edit it with any text editor; the window saves into it too, keeping
your comments. Which file is used:
1. the file named by the environment variable `DELTAPATCHER_CONFIG`
2. `deltapatcher.ini` beside the DeltaPatcher app or `deltapatcher-cli` (a portable copy: ship it with
   your game with `source_url` and `trust_key` filled in)
3. otherwise `%APPDATA%\DeltaPatcher\deltapatcher.ini` (Windows) or `~/.config/deltapatcher/deltapatcher.ini`

**Projects**: that file is also the Default project. Every `.ini` in the `projects` folder beside it is a
project, and so is any settings file listed in `project_files`. A project's file holds its own settings only
(`[project]`, `[update]`, `[publish]`, `[server]`); DeltaPatcher's own stay in the main file: `[window]`, and
`[general]` with `parallel_updates` (how many projects Update all runs at once), `project_files` and
`current_project` (the project the app shows).

**Logs**:
- everything DeltaPatcher shows, with the time: `%APPDATA%\DeltaPatcher\logs\deltapatcher.log` (Windows)
  or `~/.config/deltapatcher/logs/deltapatcher.log`; 1 MB, three old ones kept. `deltapatcher config`
  prints where. Header values given with `--header` are written as `***`. A line about a project other
  than Default starts with its name in brackets.
- each updated folder's own record: `<folder>/.deltapatcher/update.log`, every file each update added,
  replaced or removed.

## Examples

Your releases on a VPS. Once: save the server, make a key, put its line on the server, check the server's
host key (the server itself: [Setting up a VPS](VPS-SETUP.md)):

```
deltapatcher config set publish_src=./game ssh_host=game.example.com ssh_user=deploy remote_root=/var/www/game
deltapatcher config set public_url=https://game.example.com/
deltapatcher ssh keygen
deltapatcher ssh pubkey          (add the line it prints to ~/.ssh/authorized_keys on the server)
deltapatcher ssh trust
deltapatcher ssh test            (optional: can it log in?)
```

Then for every release, one line: it publishes `./game` and sends the server only what changed:

```
deltapatcher release --version 1.1
```

Players update from the address the web server gives that folder:

```
deltapatcher update ./MyGame --source https://game.example.com/
```

Several games, mods or servers, each with its own settings, then all of them at once:

```
deltapatcher project new "Map Pack"
deltapatcher --project "Map Pack" config set install_dir=D:/Games/MapPack source_url=https://maps.example.com/
deltapatcher update --all          (every project, three at the same time)
```

A launcher's update button:

```
deltapatcher-cli.exe update "C:\Games\MyGame" --source https://game.example.com/
if errorlevel 1 echo The update failed; the game folder was left as it was.
```

A nightly release from a build server:

```
deltapatcher publish build/output store --name "My Game" --version %BUILD_NUMBER%
deltapatcher deploy store --host game.example.com --user deploy --remote-root /var/www/game
```

(A build server needs a key it can use without typing a passphrase: run `ssh-agent` with the key
loaded, or use a separate key made only for that server, without a passphrase and limited to SFTP.)
