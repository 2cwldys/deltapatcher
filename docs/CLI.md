# Command line reference

```
deltapatcher <command> [options]
```

`deltapatcher` is `deltapatcher-cli.exe` from a release, `python -m deltapatcher` from source, or the
`deltapatcher` command after `pip install .`. With no command, `python -m deltapatcher` opens the
window and `deltapatcher-cli.exe` prints the help. `deltapatcher gui` always opens the window.

Remembered settings (the ones the window saves, in `%APPDATA%\DeltaPatcher\config.json` or
`~/.config/deltapatcher/config.json`) fill in `--source` and the SSH options when you leave them out.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Done (or already up to date) |
| 1 | Failed. For `update`, the folder is as it was before. |
| 2 | `update --check`: an update is available |
| 130 | Stopped with Ctrl+C |

## publish

```
deltapatcher publish SRC STORE [--name NAME] [--version VERSION] [--protect GLOB]... [--no-prune]
```

Hash the folder `SRC` and write the store `STORE` (`manifest.json`, `blobs/`). See
[Publishing a release](PUBLISHING.md).

| Option | |
| --- | --- |
| `--name` | Shown to players and in the log |
| `--version` | Shown to players and in the log |
| `--protect GLOB` | Files clients create once and never overwrite. Repeat it for more patterns. |
| `--no-prune` | Keep blobs no manifest names any more |

## serve

```
deltapatcher serve STORE [--bind 127.0.0.1] [--port 8000] [--verbose]
```

Serve a store over HTTP, for testing or a LAN. It serves only `manifest.json`,
`manifest.previous.json` and `blobs/<hash>`, never a listing. `--bind 0.0.0.0` makes it reachable
from other machines. For the internet, use a real web server with HTTPS.

## update

```
deltapatcher update FOLDER [--source URL] [--check] [--workers 4] [--header "Name: value"]...
```

Bring `FOLDER` up to the release at `URL` (an `http(s)://` address, a folder path or a `file://` URL;
default: the remembered one, else `http://localhost:8000/`).

| Option | |
| --- | --- |
| `--check` | Say what would change, change nothing. Exit 2 when out of date. |
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
