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
release a new version, set up where releases go (your server, a folder, or this computer), update a folder
(with several projects: update them all), Projects, open the wiki, update DeltaPatcher, or type commands at
a prompt. **Projects** holds the rest: choose another project, add projects from a project file, share
projects as a project file, and remove one or all of them. Every answer is saved, so the second time is
mostly Enter. A project file dropped onto the program (its path the only argument) is imported, as
`project import FILE`.

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
| `--unattended` | Nobody is there (a scheduler runs it): never ask (what would be asked fails at once, saying why) and never wait for Enter |
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
`manifest.previous.json`, `blobs/<hash>` and `sigs/<hash>.sig` (and, for a release shared by torrent,
`release.torrent` and `release.item`, also under `/<torrent name>/`), never a listing. `--bind 0.0.0.0` makes it reachable
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

A project shared by torrent also downloads from other players ([Torrents](#torrents)). Its address stays the
floor: whatever no peer has comes from it, and an update never fails because of torrents.

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

## Projects that follow their publisher

| Command or option | |
| --- | --- |
| `publish` / `release --notes TEXT` or `--notes-file FILE` | Release notes, shown to players with what changed (at most 4000 characters) |
| `update --check --files` | Also the release's whole file list, when the publisher shows file names |
| `update FOLDER --project-file FILE [--only NAME]` | The address and publisher key from a project file (its signature checked); no project is added, nothing is saved. For containers. |
| `project export ... --updates` / `--no-updates` | An update capable file (players are asked when you change the project later) or a one-off one. The default: the project's *share_updates*. |
| `project import FILE --as-new` | Add it as a new project even when it updates one you have |
| `project changes NAME` | What the publisher changed about the project, waiting for you |
| `project accept NAME` | Apply it (asks first; `--yes` does not). `--not-now` puts it off until the publisher changes the project again; `--expect HASH` applies only the change you saw. A new address or publisher key is taken only when it is signed with the key you trust now. |

With `--json`, a check carries `data.publisher_change` and, when file names are shown, `data.changes`.

## Torrents

```
deltapatcher seed [--project NAME | --all] [--json]
deltapatcher version --torrent
```

A publisher can share a project's releases by BitTorrent too (off by default; it needs signed releases):
players' DeltaPatchers then download from each other as well as from the address, and a project with no
address updates by torrent only. See [Sharing by torrent](PUBLISHING.md#sharing-by-torrent), and for players
[Downloading from other players](PLAYERS.md#downloading-from-other-players).

| Command or option | |
| --- | --- |
| `config set torrent=yes seeds=game.example.com:6881` | Share the project by torrent (it needs `sign = yes`), then release with `--sign` as before. Its torrent id is made once and never changes. |
| `seed --all` | Share every project set to seed (`seed = yes`) with other players until stopped: Ctrl+C, or with `--json` a line `cancel` or the end of standard input (it ends within two seconds). Only files DeltaPatcher has checked are offered. A player's project seeds from a copy of its release kept for seeding (`seed_copy = yes`, the default): seeding reads only the copy, so updates do not pause it; filling the copy reads the folder, and stops at once when an update asks. With `seed_copy = no` it seeds straight from the folder and pauses while an update changes it. |
| `seed --project NAME` | Seed that project for this run: the command is the yes. For a project not set to `seed = yes`, its copy is kept while the run lasts, and deleted by a later clean-up once nothing needs it; the next such run uses it again while it is still there. |
| `config set seed_copy=no` | Seed the project straight from its folder, with no copy; its copy is deleted. |
| `config set seed_copies=DIR` | Keep every seed copy in DIR, one folder per project. DIR must be a real folder that is there (not a symbolic link or junction), not a drive's root, and not a folder a project keeps up to date or releases (nor inside or holding one): otherwise nothing changes, and it says why. The copies at the old place are deleted, and seed runs make them again in DIR. A DIR set by hand in the settings file that cannot be used leaves each copy where it is, and its line says why. |
| `version --torrent` | Test this DeltaPatcher's BitTorrent support: a session on 127.0.0.1, a small torrent made and checked. Exit 1 when it fails. |
| `project import FILE --seed` | Seed the file's projects shared by torrent (otherwise a terminal asks; the default is no) |

`seed` prints what it seeds, then a line for each project every 5 seconds: its peers, its upload rate and
what it sent; for a project seeded from a copy, `from a copy`, and while the copy fills
`copying for seeding: 4.2 MB of 20.0 MB`. A seed run looks for a newer release every 5 minutes, reading only the
small signed `release.item` when nothing changed (a project with no address has its release item put in the
DHT again every 30 minutes). A copy is deleted once its project stops seeding from it: `seed = no`,
`seed_copy = no`, torrents refused, the project removed. A command deletes it at once, in 2 seconds at most; a
seed run (also for what it finds at its start) once two reads of the settings, 3 seconds apart, agree, or at its end
when it ends on torrents refused, its deletes stopping 1.5 seconds after its stop; what is left goes at the next
clean-up. Never while another project needs it, and never when a settings file cannot be read whole (said once,
naming the file). `projects` shows which projects a seed run is seeding. `torrents` takes `allow` or `refuse`
only (in the settings file, anything but `allow` refuses). A publish of a project with no address says how
players find it: once a seed run of its store has put its release item in the DHT. Publishing with
`torrent = no` takes `release.torrent` and `release.item` out of the store. The settings are in
[Settings and logs](#settings-and-logs).

## stats

```
deltapatcher stats [--days N]
```

The project's statistics for the last N days (1 to 366, default 30): its releases, from the store, and
update checks, downloads, bytes, players and the releases they are on, from the web server's access log
(the `access_log` setting). Only counts, never an address. `--json` gives the whole document
(`"format": "deltapatcher-stats"`); a part that cannot be made says why under `problems`, and the rest is
still there. See [Publishing, Statistics](PUBLISHING.md#statistics).

## backup

| Command | |
| --- | --- |
| `deltapatcher backup [now]` | Back up the live release, read from the server or folder it is sent to, onto this computer. An unchanged release makes no new backup. |
| `deltapatcher backup list` | The backups: date (UTC), release, files, size, and the last scheduled run |
| `deltapatcher backup restore DATE [--dry-run] [--yes]` | Put a backup (`YYYY-MM-DD`) back where players download from: its blobs first, its manifest last; nothing there is removed. Asks first. |
| `deltapatcher backup prune` | Apply the limits now (`backup_keep_daily`, `_weekly`, `_monthly`, `backup_max_size`) |
| `deltapatcher backup schedule daily\|hourly\|off` | The system runs the backup without the app: it starts every hour and makes a backup when the last one is about a day (or an hour) old. Task Scheduler on Windows (hidden), the user's crontab elsewhere. `off` removes only this project's entry. |

See [Publishing, Backups](PUBLISHING.md#backups).

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
deltapatcher project import FILE [--folder DIR [--as-is]] [--only NAME]... [--name NEW] [--dry-run] [--yes] [--as-new] [--seed]
deltapatcher project import FILE [--publish-src DIR] [--signing-key FILE] [--ssh-key FILE] [--deploy-folder DIR] [--trust-host-keys | --host-key-fingerprints FINGERPRINTS] [--apply-older] [--as-player]
deltapatcher project export NAME... --out FILE [--sign [KEYFILE]] [--updates | --no-updates] [--private]
deltapatcher project changes NAME
deltapatcher project accept NAME [--yes] [--not-now] [--expect HASH]
deltapatcher project add FILE [--name NAME]
deltapatcher project rename NAME NEW
deltapatcher project remove NAME [--yes]
deltapatcher project remove --all [--yes]
```

- `new`: a new project, its settings file in the `projects` folder beside the main settings file. Fill it in
  with `deltapatcher --project NAME config set install_dir=... source_url=...`, or in the app.
- `import`: add the projects of a project file (`.deltapatch` or `.html`) a publisher gave you. It says
  who signed the file: one changed after it was signed is refused, and an unsigned one is added with a
  warning. Each project keeps one folder up to date. `--folder DIR` says which, so it does not ask: one
  project goes into `DIR` (into `DIR` plus the folder name its publisher gave, when they gave one), and with
  several projects each gets a folder of its own inside `DIR`. It is needed once: the folder is saved in the
  project's settings, and every `update` and `update --all` after that uses it. A project file never holds
  a folder. Without `--folder` it asks (showing where the publisher says it goes), or the folder is chosen
  later. `--as-is`, with `--folder` and one project, uses `DIR` itself, without the publisher's folder name.
  `--only NAME`
  adds only that project of the file (repeatable), `--name NEW` gives a single project another name here,
  and `--dry-run` says what the file holds, adding nothing. `--seed` seeds the file's projects shared by
  torrent (otherwise a terminal asks; the default is no).
- `export`: write a project file holding these projects, to give to players. `FILE.deltapatch` opens
  DeltaPatcher with a double-click; `FILE.html` is a page anyone can read in a browser. For each project it
  holds the name, the http(s) address players update from (`public_url`, else `source_url`), the publisher
  key, and a description and website when set; for a project shared by torrent, also its torrent id, name
  and seeds (the file must then name the publisher key). Never a folder, a server login or a key file. `--sign` signs
  it with your signing key (it asks the passphrase; `--passphrase-stdin` for programs).
- `export --private`: a private project file instead, for you, to keep releasing these projects from another
  computer of yours: what a public file holds, and what releasing needs (the release's rules, the server's address,
  SSH port, login name and remote folder, the address players update from, the access log, the torrent id and
  seeds, how many backups are kept and their size limit, the server's host keys this computer trusts), never a
  folder, a key, a password or the backup schedule (each computer's own). It is always signed with the projects' own
  signing key (`signing_key`), asked as `--sign` asks: a project that does not sign its releases (`sign = no`, or no
  key there) cannot go into one, and the projects of one file share one signing key; each project exported gets that
  key as its `signing_public_key`, so this computer knows its own project in a later file, and the time the file was
  made as its `project_file_made`, so opening this file (or an older one) here again applies nothing by itself. FILE
  may not be inside a folder one of these projects releases, its store or the folder its releases are copied to
  (`publish` leaves out any private project file it finds in the folder it releases, with a warning). Carry the key to the other computer yourself,
  apart from the file. See [Releasing from another computer](PUBLISHING.md#releasing-from-another-computer).
- `import` of a private project file sets up releasing each of its projects from this computer, after one
  question (or `--yes`); a terminal asks for what the options give. `--publish-src DIR` is the folder to release,
  `--signing-key FILE` your signing key's file (checked against the key the file is signed with: now when it has no
  passphrase, else at the first release; another key is refused; no key file is read when none is given),
  `--ssh-key FILE` your SSH key's file, and `--deploy-folder DIR` the folder releases are copied to in folder mode;
  they are for a project it adds, and `--seed` seeds one shared by torrent. `--trust-host-keys` (a switch) adds the
  server's host keys from the file to this computer's known_hosts (they are printed first);
  `--host-key-fingerprints SHA256:...,SHA256:...` adds only those with these fingerprints (what the app passes: the
  keys it showed). Either adds nothing while this computer trusts any key for that server and port: when they
  differ, both are shown. Without them the first release asks about the server, as for any server. The project this
  computer releases with the very key the file is signed with (the same torrent id, else the same name) is updated
  in place instead, unless `--as-new`: only what the file changes, never its folders, its key files, its backup
  schedule or whether it seeds. A file made before the one applied to it (`project_file_made`), or that same file
  again, is not applied: both times are shown, as two computers' clocks give them, and `--apply-older` applies it
  anyway (`--yes` alone never does). A project that only plays the game (a player's) is never changed: it is named,
  and the file's project is set up as one of its own. So is a project of the file's name this computer releases with
  a signing key DeltaPatcher does not know yet (a key with a passphrase, never released or exported here): release
  it once or export a private file from it, then open the file again. `--as-player` adds a private file as a player
  would: only what a public file holds.
- `add`: list a project's settings file (`.ini`) kept somewhere else (in a game's folder, on a share).
  `--name` gives it a name, written into that file. A project file is added with `import`.
- `rename`: the project's name changes; its file stays where it is.
- `remove`: a project in the `projects` folder is deleted with its settings file; an added one only leaves
  the list, and its file stays. The folders it updates or releases are never touched. `--all` removes every
  project but Default. It asks first in a terminal; `--yes` answers for scripts. Default cannot be removed.

A name has letters, digits, spaces and `- _ . ( )`, up to 60 characters, cannot start with a dash, and no
two projects share one (letter case aside).

## reset

```
deltapatcher reset [--yes]
```

DeltaPatcher as it was just installed: every project removed (as `project remove --all`), the main settings
file back to its defaults, and the cached check for a newer DeltaPatcher cleared. A copy of the settings
files is kept first, in a dated folder beside them (`settings-before-reset-...`), so a reset can be undone
by hand. Never touched: the folders projects update or release and their files, the signing key and SSH
keys. Trusted servers, the log and prepared releases stay too. Adding a project back later finds its
folder's files, checks them, and downloads only what differs.

## wiki

```
deltapatcher wiki [GUIDE]
```

Open the guides on the wiki in your browser (the address is printed too). `GUIDE` opens one of them:
`quickstart`, `players`, `publishing`, `vps`, `cli`, `docker`, `mcp`, `format`, `troubleshooting` or
`security`.

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
`--passphrase-stdin` the first line is the passphrase; a later line `cancel` stops the run at its next
safe point: an update during its download or before it moves its first file (the folder stays as it
was), a publish, release, deploy or backup restore before its new manifest goes up (players keep the
release before); closing standard input stops `serve`. A `seed` run ends within two seconds of `cancel` or
the end of standard input; it sends `{"event": "seeding", ...}` for each project every 5 seconds (its state,
peers, upload rate and what it sent; for a project seeded from a copy, `"kind": "copy"` and `"copy": {"path",
"bytes", "state", "done", "total"}`: where the copy is, what it holds on disk, `copying`, `ready`, `full` or
`waiting` (with `why` while it is not ready), and how much of the release's blobs it holds).

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
| `version --torrent` | Test BitTorrent support (exit 1 when it fails) |

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
project, and so is any settings file listed in `project_files`. A project's file holds its own settings only:
`[project]` (`title`, and the `description`, `website`, `install_hint` and `install_folder` shown with it and
carried by project files), `[update]`,
`[publish]`, `[server]`, `[backup]` and `[torrent]` (`torrent`: share it by torrent; `torrent_id`, kept by
DeltaPatcher; `torrent_name`; `seeds`; `seed`: seed it here; `seed_copy`: `yes`, the default, seeds from a copy
of its release kept for seeding, `no` straight from its folder). DeltaPatcher's own stay in the main file:
`[window]` (`close_to_tray`, `minimize_to_tray`, `start_with_system`, `check_updates`) and `[general]`:
`parallel_updates` (how many projects Update all runs at once), `check_every_hours` (how often the window
checks every project; 0: only when it starts), `auto_update` (update what a check finds, by itself),
`project_files` and `current_project` (the project the app shows), `torrents` (`allow`, or `refuse`: never
BitTorrent, whatever a project says), `torrent_port` (6881: the port seeding listens on),
`seed_upload_kbps` (0: no limit), `torrent_port_mapping` (`no`: nothing asks the router to forward the
port) and `seed_copies` (where seed copies are kept: a folder that is there, of its own; empty: each inside its
project's folder, in `.deltapatcher/seedstore/<torrent id>`; a relative path starts at the main file's folder).
`seeding.json` beside the main file says what the seed runs are seeding (they take turns writing it through
`seeding.json.lock`), and `seed-copies.json` lists every seed copy this DeltaPatcher made: only a copy on it is
ever deleted, so another DeltaPatcher's (in a shared seed copies folder, or in a game folder both update) is never
taken. None is lost: a clean-up puts back on it a marked copy at a project's own place whose marker names this
DeltaPatcher's settings file of that project, makes a list that cannot be read again (keeping it as
`seed-copies.json.bad`), and keeps the entry of a copy whose drive is away until it is back.

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
