# Troubleshooting

Messages are shown in the window's log and, from the command line, on the screen. Find yours below.

## Updating

| Message | What to do |
| --- | --- |
| `another update or rollback is running in this folder` | Another DeltaPatcher (window or command line) is working on the same folder. Wait for it or close it. A crash never leaves this behind: the lock ends with the program. |
| `...manifest.json answered 404 Not Found` | The source address is wrong, or nothing has been published or deployed there yet. Open the address in a browser and add `manifest.json` to check. |
| `cannot reach ...` | No connection to the source: check the address, your internet connection, and that the server is running. |
| `the source's manifest is refused: ...` | The manifest has a path that is not allowed (see [the format](FORMAT.md)). Fix the release and publish again. |
| `blob ... does not match its SHA-256` | The server serves a damaged file. Deploy again: it replaces blobs of the wrong size. Your folder was not changed. |
| `not enough free space` | An update needs about twice its download size free while it runs. Free some space and update again. |
| `... is a folder here; the release has a file there` (or a link) | Something in your folder has the name the release uses for a file. Move it away. |
| The update failed with "Access is denied" or "being used by another process" | A file is open, usually because the game is running. Close it and update again. The failed update was already undone. |
| `cannot clear ... (is a file in it open in another program?)` | A program holds a file in `.deltapatcher`. Close it (or restart) and try again. |
| `there is no update to roll back` | No completed update is recorded for this folder. |
| Warning about `plain http to another machine` | The source uses `http://` across a network. Files are still checked by hash, but the manifest could be altered on the way. Use `https://`, or ask the publisher to sign releases. |
| `the release is not signed, but this folder trusts publisher key ...` | This folder installed a signed release before, so it now refuses unsigned ones. Ask the publisher. If you know the release is genuine: `--trust-key off` (or `trust_key = off` in the settings file) skips the check. |
| `the release is signed by ..., but this folder trusts ...` | Someone other than the publisher you trust signed it, or the publisher changed keys. Get the new key from the publisher directly, then update once with `--trust-key ed25519:...` (or paste it into *Publisher key*). |
| `the release's signature is refused: ...` | The signature does not match the manifest: the release was changed after it was signed. Do not install it; tell the publisher. |
| Where is the log? | **Logs** at the bottom right of the app, or `deltapatcher config`. Each game folder also has `.deltapatcher/update.log`. |

## Project files

| Message | What to do |
| --- | --- |
| `... was changed after it was signed: refused` | Someone changed the file after the publisher signed it. Do not add it: get a fresh copy from the publisher. |
| `this project file was made by a newer DeltaPatcher` | Update DeltaPatcher first (**Update DeltaPatcher** in the app, or `deltapatcher self-update`), then add the file again. |
| `not a project file DeltaPatcher can read: ...`, `... holds no projects`, `... is over 1 MB` | It is not a DeltaPatcher project file, or it was damaged on the way. Ask for it again. |
| `there is a project named ... already` | The app suggests another name in its dialog. On the command line, give one with `--name NEW`, or leave that project out with `--only`. |
| `... has no http(s) address players update from` (exporting) | Set where players download it (*public_url*, on the **Release** page), then export again. |
| `project add` refuses a `.deltapatch` or `.html` file | Project files are imported: `deltapatcher project import FILE`. `project add` is for a project's `.ini` settings file. |

## Publishing

| Message | What to do |
| --- | --- |
| `the signing key could not be opened (wrong passphrase?)` | Type the signing key's passphrase again. If it is lost, make a new key and give players the new public key. |
| `no signing key at ...` | Make one: **New signing key** (Release page) or `deltapatcher sign keygen`. Or turn off **Sign each release**. |
| `signing needs the 'cryptography' package` | From source: `pip install cryptography`. The built programs include it. |
| `a passphrase on a new SSH key needs the bcrypt package` | From source: `pip install bcrypt`. The built programs include it. |
| `the store must not be inside the folder it publishes` | Leave Settings' `store_dir` empty (DeltaPatcher then keeps the store in its own folder), or choose one outside the release folder. |
| `give the folder to release, or save it once` | Choose *Folder to release* (Release page, step 1), or `deltapatcher config set publish_src=FOLDER`. |
| `save the server once` / `give the folder to send it to` | Fill in step 2 of the Release page (or the menu's *Set up where releases go*): the server's host, user and remote folder, or the folder to copy to. |
| `X: Y is not in Z (yet)` | A file or folder in *Leave out* or *Players keep their own copy* is not in the release folder. Harmless; remove it from the list if it is gone for good. |
| `X is not inside the folder you publish` | `--leave-out` and `--protect` take paths inside the release folder (or a pattern such as `*.log`). |
| `X cannot be published: ...` | A file name that cannot be used on every system (a device name like `CON`, a name ending in a dot or space, a `:`). Rename it. |
| `skipped X (a link or junction)` | Links are never followed. Copy the real files into the release folder. |
| `nothing changed since the last publish` | Not an error: the manifest stays as it was. |

## Statistics and backups

| Message | What to do |
| --- | --- |
| `access_log is not set` | Statistics count downloads from the web server's access log: set **access_log** to its path (on the server, or on this computer for a folder). The releases part works without it. |
| `public_url is not set` | Set the address players update from (`public_url`): it tells which lines of the log are your project's. |
| `the access log is compressed` | Name the log the web server writes now (`access.log`), not a rotated `.gz` one. |
| `the access log ... is ..., more than 512 MB` | Rotate the log more often (daily, say): statistics read only the one file named. |
| `the server does not let ... read ...` | Give the deploy user read access to the log (for example the `adm` group on Debian and Ubuntu). |
| `the SSH key ... is locked` | Unlock it (**Unlock key**, or `deltapatcher ssh unlock`). A scheduled backup on Linux or macOS runs from cron, which cannot reach your ssh-agent: let the app make backups instead (`backup_every_hours`). |
| `a release served from this computer ... has no destination to back up` | Backups read the release where players download it: set your server or folder on the Release page. |
| `nothing is released at ... yet` | Release once; there is nothing to back up before that. |
| `the blob ... is damaged` | A file on the server is not what its name says. No backup was kept. Release again to put good files back, then back up. |
| `another backup of this project is running` | Wait for it (the app, or a scheduled one) and try again. |

## Deploying and SSH

| Message | What to do |
| --- | --- |
| `ssh-keyscan was not found` (or sftp, ssh-keygen, ssh-add) | Install the OpenSSH client. Windows: Settings > Apps > Optional features > OpenSSH Client. |
| `The OpenSSH Authentication Agent is not running` | Windows, administrator PowerShell, once: `Get-Service ssh-agent \| Set-Service -StartupType Automatic` then `Start-Service ssh-agent`. |
| `the server refused the key` | The key's server line is not in `~/.ssh/authorized_keys` of that user on the server, or the key is not unlocked. Copy the server line again (**Copy server line**) and click **Unlock key**. On the server, `.ssh` must be mode 700 and `authorized_keys` 600, owned by the user. |
| `the server's host key is not trusted yet: check it first` | **Check host key** (or `deltapatcher ssh trust`) before **Put key on server**: the password goes only to a server you trust. |
| `... refused the password for ...` | The user or the password is wrong, or the server lets this user log in with a key only. Then add the key's line yourself (**Copy server line**). |
| `... does not take a password for ...` | The server has passwords switched off: add the key's line yourself (**Copy server line**, then into `~/.ssh/authorized_keys` on the server). |
| `putting a key on a server needs the paramiko package` | From source: `pip install paramiko`. The built programs include it. |
| `the server's host key is not the one you confirmed` | Either the server was reinstalled (check its new fingerprint, then `deltapatcher ssh trust --host HOST --forget` and confirm again), or something is intercepting the connection. In that case, do not deploy. |
| `the private key can be read by other users` | Click **Fix permissions** (or answer yes on the command line). OpenSSH refuses keys that others can read. |
| `ssh-keygen cannot read ...` | The key file is damaged or not an SSH key. Make a new one with **New key** (Release page, step 2). |
| `the remote folder must not contain ...` | Use forward slashes and no quotes or wildcards: `/var/www/game`, `C:/www/game`. |
| `logged in; the remote folder ... does not exist yet` | Fine: the first deploy creates it (its parent folder must exist). |
| `... blobs are still missing or the wrong size on the target after the upload` | The upload did not complete (disk full, quota, connection). The server still serves the old release. Fix the cause and deploy again. |
| `the server has no atomic rename: it was missing for a moment` | Not an error: an older SFTP server could not replace the manifest in one step. It was absent for about a second. |
| A server without `internal-sftp` refuses the key | Use the line from `deltapatcher ssh pubkey --full` instead. |

## The programs

| Problem | What to do |
| --- | --- |
| The antivirus quarantines the DeltaPatcher app or `deltapatcher-cli` | New, unsigned programs are flagged by reputation checks. Restore it and add an exception, run from source, or (for publishers) sign the programs. |
| No tray icon on Linux | The desktop needs a tray (AppIndicator or an X tray). Everything else works without one. |
| `could not ask GitHub: ...` (Update DeltaPatcher) | No connection to github.com, or GitHub limits how often one address may ask (60 times an hour): try again later. |
| `... does not match the SHA-256 GitHub gives for it: not used` | The download was damaged or changed on the way; nothing was installed. Try again, or download it from the release page. |
| `this DeltaPatcher runs from source` (self-update) | Update the repository instead: `git pull`. |
| The build says `The build needs Python 3.11 or later` | Install a newer Python, delete `.venv`, and run `build.ps1` / `build.sh` again (on Linux: `PYTHON=python3.12 ./build.sh`). |
| `Cannot serve: ... address already in use` | Another program uses the port. Pick another port (Release page, step 2, **This computer**), or `--port`. |
