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

With no command it prints the help.

**Its window.** In a terminal it runs and returns, as any command does. In a window of its own on Windows
it keeps that window open, so nothing vanishes before it is read:
- double-clicked (the `.exe`, or `cli\deltapatcher.bat`): the help, then a prompt, `deltapatcher>`, that
  takes one command after another (`help`, `help update`, `update ...`) until `exit`
- started with a command from a shortcut or a launcher's own window: it waits for Enter when it is done
- `--close`: it closes as soon as it is done, with no prompt and no waiting. Use it in shortcuts and
  launchers that should not wait for anyone.

It never waits when a program reads its output (a pipe, a file, `--json`).

The DeltaPatcher app (`app/`) runs this same command line, in its machine mode (`--json`, below).

The settings file (`deltapatcher.ini`, see [Settings and logs](#settings-and-logs)) fills in `--source`,
`--trust-key`, the signing key and the SSH options when you leave them out.

## Options for every command

| Option | |
| --- | --- |
| `--log FILE` | Write the log here instead of the usual place |
| `--no-log` | Write no log file |
| `--no-color` | Plain text. Colour is used only in a terminal that shows it, never in files or pipes; `NO_COLOR=1` works too. |
| `--close` | Close as soon as the command is done: no prompt, no waiting for Enter in a window of its own (see above) |

In a terminal the output uses the app's colours: white for what was done, beige-yellow for what needs
your attention (and amber, in bold, for errors), the app's blue for things to copy or compare (keys,
fingerprints, commands), and its teal for the progress bar.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Done (or already up to date) |
| 1 | Failed. For `update`, the folder is as it was before. |
| 2 | `update --check`: an update is available |
| 130 | Stopped with Ctrl+C |

## publish

```
deltapatcher publish SRC STORE [--name NAME] [--version VERSION] [--protect GLOB]... [--sign [KEYFILE]] [--no-htaccess] [--no-prune]
```

Hash the folder `SRC` and write the store `STORE` (`manifest.json`, `blobs/`, `sigs/`, `.htaccess`). See
[Publishing a release](PUBLISHING.md).

| Option | |
| --- | --- |
| `--name` | Shown to players and in the log |
| `--version` | Shown to players and in the log |
| `--protect GLOB` | Files clients create once and never overwrite. Repeat it for more patterns. |
| `--sign [KEYFILE]` | Sign the release (optional). Without a file: the settings file's `signing_key`, else the default key. Asks the key's passphrase. |
| `--no-htaccess` | Do not write the `.htaccess` for Apache |
| `--no-prune` | Keep blobs and signatures no manifest names any more |

## serve

```
deltapatcher serve STORE [--bind 127.0.0.1] [--port 8000] [--verbose]
```

Serve a store over HTTP, for testing or a LAN. It serves only `manifest.json`,
`manifest.previous.json`, `blobs/<hash>` and `sigs/<hash>.sig`, never a listing. `--bind 0.0.0.0` makes it reachable
from other machines. For the internet, use a real web server with HTTPS.

## update

```
deltapatcher update FOLDER [--source URL] [--check] [--trust-key KEY] [--workers 4] [--header "Name: value"]...
```

Bring `FOLDER` up to the release at `URL` (an `http(s)://` address, a folder path or a `file://` URL;
default: the remembered one, else `http://localhost:8000/`).

| Option | |
| --- | --- |
| `--check` | Say what would change, change nothing. Exit 2 when out of date. |
| `--trust-key KEY` | The publisher's key (`ed25519:...`): the release must be signed with it. Without it, unsigned releases are accepted, and the first signed one's key is trusted (and only that key after it). `off` skips signature checks. |
| `--workers N` | Downloads at the same time (default 4) |
| `--header` | Extra request header, for a host that needs one (an access token, for example). It is sent only to that host, never after a redirect to another one. |

## rollback

```
deltapatcher rollback FOLDER
```

Undo the last completed update of `FOLDER`.

## deploy

```
deltapatcher deploy STORE --to-folder FOLDER [--dry-run] [--no-prune]
deltapatcher deploy STORE [--host HOST] [--user USER] [--port 22] [--key KEYFILE] [--remote-root PATH] [--dry-run] [--no-prune]
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
folder stays as it was); closing standard input stops `serve`. Commands made for programs:

| Command | |
| --- | --- |
| `config get` / `config set key=value ...` | Read every setting (with its explanation) / change settings |
| `ssh status` | The key (passphrase, permissions, ssh-agent) and whether the host is trusted |
| `ssh scan` | The host keys a server offers |
| `ssh trust --accept FP,FP` | Trust a server only if it still offers exactly these fingerprints |
| `ssh fix-permissions` | Make the private key readable by this account only |
| `sign status` | Is there a signing key, and does it have a passphrase |
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

**Logs**:
- everything DeltaPatcher shows, with the time: `%APPDATA%\DeltaPatcher\logs\deltapatcher.log` (Windows)
  or `~/.config/deltapatcher/logs/deltapatcher.log`; 1 MB, three old ones kept. `deltapatcher config`
  prints where. Header values given with `--header` are written as `***`.
- each updated folder's own record: `<folder>/.deltapatcher/update.log`, every file each update added,
  replaced or removed.

## Examples

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
