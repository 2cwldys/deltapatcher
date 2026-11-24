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

## Publishing

| Message | What to do |
| --- | --- |
| `the signing key could not be opened (wrong passphrase?)` | Type the signing key's passphrase again. If it is lost, make a new key and give players the new public key. |
| `no signing key at ...` | Make one: **New signing key** (Publish page) or `deltapatcher sign keygen`. Or turn off **Sign each release**. |
| `signing needs the 'cryptography' package` | From source: `pip install cryptography`. The built programs include it. |
| `the store must not be inside the folder it publishes` | Choose a store folder outside the release folder. |
| `X cannot be published: ...` | A file name that cannot be used on every system (a device name like `CON`, a name ending in a dot or space, a `:`). Rename it. |
| `skipped X (a link or junction)` | Links are never followed. Copy the real files into the release folder. |
| `nothing changed since the last publish` | Not an error: the manifest stays as it was. |

## Deploying and SSH

| Message | What to do |
| --- | --- |
| `ssh-keyscan was not found` (or sftp, ssh-keygen, ssh-add) | Install the OpenSSH client. Windows: Settings > Apps > Optional features > OpenSSH Client. |
| `The OpenSSH Authentication Agent is not running` | Windows, administrator PowerShell, once: `Get-Service ssh-agent \| Set-Service -StartupType Automatic` then `Start-Service ssh-agent`. |
| `the server refused the key` | The key's server line is not in `~/.ssh/authorized_keys` of that user on the server, or the key is not unlocked. Copy the server line again (**Copy server line**) and click **Unlock key**. On the server, `.ssh` must be mode 700 and `authorized_keys` 600, owned by the user. |
| `the server's host key is not the one you confirmed` | Either the server was reinstalled (check its new fingerprint, then `deltapatcher ssh trust --host HOST --forget` and confirm again), or something is intercepting the connection. In that case, do not deploy. |
| `the private key can be read by other users` | Click **Fix permissions** (or answer yes on the command line). OpenSSH refuses keys that others can read. |
| `ssh-keygen cannot read ...` | The key file is damaged or not an SSH key. Make a new one with **New key** (Server page). |
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
| `Cannot serve: ... address already in use` | Another program uses the port. Pick another port next to **Serve locally**, or `--port`. |
