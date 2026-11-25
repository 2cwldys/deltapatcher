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
| `... could not be rebuilt (...): it comes whole`, `the parts index of ... is refused`, `the parts index of ... was refused before`, `the release has no parts index of ...`, `... would be fetched: it comes whole`, `... would take this update past 256 MiB or 1,000,000 segments of indexes`, `... past 256 MiB of indexes fetched`, `... come whole: this update has all the parts indexes it may read` | Not an error: that archive is downloaded whole, as any changed file is, and checked the same way. Most often the web server ignores `Range` requests (it answers the whole file), or does not serve `parts/` (IIS needs its `web.config` there too: [Setting up a VPS](VPS-SETUP.md)): tell the publisher. A refused index is not asked for again while the release names it. `... would be fetched` means most of the archive changed, so downloading it whole is as quick. `... would take this update past ...` and `... has all the parts indexes it may read` mean the archives this update rebuilds have more index than one update reads: the one past it and every one after it come whole. |
| `this check counts X whole: the drive has too little free space to keep its parts index` | Not an error: a check reads an archive's parts index only when the drive has room for it after what the update needs free, so the check says the whole size. The update itself still rebuilds X. Free some space to see the smaller amount. |
| `X: a cut download of it lacks only ...: it is resumed, not rebuilt` | Not an error: an earlier download of X was cut, and finishing it fetches no more than rebuilding X would. |
| `the cut download of X was damaged: it is downloaded again, whole` | Not an error: what an earlier, cut download of X left was not the release's bytes (a damaged disk, or a proxy that answered other bytes), so X is downloaded again from the start and checked. If that fails too, the source serves the wrong file: tell the publisher. |
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
| `give the folder to release, or save it once` | Choose *Folder to release* (Release page, **What to release**), or `deltapatcher config set publish_src=FOLDER`. |
| `save the server once` / `give the folder to send it to` | Fill in **Where players get it** on the Release page (or the menu's *Set up where releases go*): the server's host, user and remote folder, or the folder to copy to. |
| `X: Y is not in Z (yet)` | A file or folder in *Leave out* or *Players keep their own copy* is not in the release folder. Harmless; remove it from the list if it is gone for good. |
| `X is not inside the folder you publish` | `--leave-out` and `--protect` take paths inside the release folder (or a pattern such as `*.log`). |
| `X cannot be published: ...` | A file name that cannot be used on every system (a device name like `CON`, a name ending in a dot or space, a `:`). Rename it. |
| `skipped X (a link or junction)` | Links are never followed. Copy the real files into the release folder. |
| `nothing changed since the last publish` | Not an error: the manifest stays as it was. |
| `Warning: X (1.2 GB) is sent whole whenever it changes: pack it as .zip or .pk3 ...` | Not an error, and nothing is blocked: X is released, served and updated as any file is, only whole. It is a RAR, 7z, gzip or `.tar.gz` archive, which DeltaPatcher cannot patch inside, so every change to it sends all of it again. Pack it as a `.zip` or `.pk3` if the game can read that (one archive, not a `.tar` inside a `.gz`), and players then download only the files inside it that changed. |
| `Warning: X (9.6 MB) is sent whole whenever it changes: ...` (a zip, with why) | Not an error, and nothing is blocked: X is a zip DeltaPatcher made no parts index for, so it is released, served and updated as any file is, only whole. The end of the line says why. `... of it would travel inside its index, more than a quarter` (or `more than 48 MiB`, for an archive over 192 MiB): an index carries inside it the archive's files under 1 KiB and every byte outside its files (their headers, its directory, gaps a zip tool left between files, a program before the first one), and none is made when those come to more than a quarter of the archive or to more than 48 MiB. The line says how much of each. Files under 1 KiB: nothing to do, it still updates, whole. Bytes outside its files: packing it again as a plain zip removes gaps and a program before the first file (a self-extracting archive), if the game takes a plain zip; its headers and directory stay. `its files overlap or are not where its directory says`: pack it again with any zip tool. `Python's zip reader cannot open it`: pack it again with another zip tool. `it has more files than an index can hold`: an index holds at most 1,000,000 parts, about two for each file (so about 500,000 files): split it into archives of fewer files, if the game allows. `its index would be larger than 64 MiB`: split it into smaller archives, if the game allows. |
| `could not read X to index it (...): players get it whole this time` | Not an error: X is released, and players get it whole when it changes. Its copy in the store could not be read while its parts index was made (another program, an antivirus say, held it, or it changed while it was read). The next publish reads it again. |
| `Warning: the parts index of X is missing ...` (deploying) | The store lost an archive's parts index: the release goes up without it, and players download X whole when it changes. Publish again to make it. |

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
| `... holds the backups of another project` | That folder is another project's backup folder. Leave **backup_dir** empty, or name a folder of its own. |
| `the backup ... cannot be read` | Nothing is removed until it can be read (an antivirus or a sync tool may hold it for a moment). If it stays damaged, delete that dated folder by hand. |

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
| `ssh-keygen cannot read ...` | The key file is damaged or not an SSH key. Make a new one with **New key** (Release page, **Where players get it**). |
| `the remote folder must not contain ...` | Use forward slashes and no quotes or wildcards: `/var/www/game`, `C:/www/game`. |
| `logged in; the remote folder ... does not exist yet` | Fine: the first deploy creates it (its parent folder must exist). |
| `... blobs are still missing or the wrong size on the target after the upload` | The upload did not complete (disk full, quota, connection). The server still serves the old release. Fix the cause and deploy again. |
| `the server has no atomic rename: it was missing for a moment` | Not an error: an older SFTP server could not replace the manifest in one step. It was absent for about a second. |
| A server without `internal-sftp` refuses the key | Use the line from `deltapatcher ssh pubkey --full` instead. |

## Torrents

| Message | What to do |
| --- | --- |
| `This project updates only by torrent, and torrents are refused in your settings` | *Torrents* is set to *refuse* (Settings; `torrents = refuse`). Set it to *allow*, or ask the publisher for an address to update from. |
| `the release of this project could not be found: no seed or DHT node had it` | None of the project's seeds answered, and no peer met before either. Ask the publisher to keep a seed running, and check the seeds the project file lists. |
| `This DeltaPatcher has no BitTorrent support (libtorrent is not installed)` | From source: `pip install -r requirements-torrent.txt` (Python 3.9 to 3.13), or `pip install ".[torrent]"`. The programs from a release have it built in; on a Mac it needs macOS 14 or later. `deltapatcher version --torrent` tests it. |
| `the swarm is slow (...): the address sends blobs too, and the first to bring one wins` | Nothing to do: the address and the other players race, and the update never takes much longer than the address alone. |
| `the release's torrent ...: everything comes from the address` | The address's `release.torrent` and `release.item` are missing, damaged or of another release (one going up while you updated): the update used the address alone. If it stays, the publisher publishes and deploys again. |
| `torrents: allow or refuse` | *Torrents* takes only these two words. In the settings file, anything but `allow` refuses. |
| `Warning: the settings file ... cannot be read (...): torrents are refused until it is fixed.` | A line in `deltapatcher.ini` is broken (each setting is `name = value`), or the file cannot be read. Fix the line the warning names; until then DeltaPatcher refuses torrents, to be safe. |
| `the release found (number ...) is older than the one this folder accepted meanwhile (...): update again` | Two updates of one folder ran at once, and the other one took a newer release. Update again. |
| `blob ... in the cache no longer matches its SHA-256` | A downloaded file changed on disk between its check and its install (a disk or antivirus problem). The update was undone; update again. |
| `torrents need signed releases` | Turn signing on first (`sign = yes`, with a key from `deltapatcher sign keygen`), then share by torrent. |
| `... is shared by torrent, and its project file must name its publisher key` (exporting) | Export it signed (`--sign`), or set the project's *Publisher key*. |
| `port 6881 is taken: seeding on port ... instead` | Another program uses the port. Set *Torrent port* (Settings) to a free one, and forward that one on your router. |
| `the swarm made no progress for 30 seconds` | Nothing to do: the rest comes from the address. |
| `... not seeded: its folder keeps no release to seed yet: update it first` | Update the project once; its folder then keeps what seeding needs. |
| `... is there and is not a seed copy: delete it or move seed copies (seed_copies)` | A folder that is not one of DeltaPatcher's seed copies is where the copy would go (a seed copy has a `seedstore.json` that says so). DeltaPatcher never uses or deletes it: delete or move it yourself if it is not yours to keep, or set *Seed copies* (Settings) to another folder. |
| `... not seeded: its seed copy was deleted: it is made again` | Nothing to do: its seed copy went while it was filled (deleted by hand or by a cleaner). Seeding makes a fresh one at its next start, a few seconds later. |
| `the copy needs ... more and its drive has ... free, but ... always stays free there` | The seed copy stopped filling: DeltaPatcher always leaves the larger of 1 GB and 5% of a drive free (never more than 20 GB). Free some space, turn *Seed from a copy* off for that project (it then seeds from its folder, with no copy), or set *Seed copies* (Settings) to a folder on a drive with room. Filling is tried again every minute. |
| `seed copies would go in ..., which ...: give them a folder of their own (seed_copies)`, `seed_copies: ... is not a folder that is there`, or `seed_copies: ... is a symlink or junction: pick a real folder` | *Seed copies* takes a real folder that is there (not a symbolic link or junction), not a drive's root, and not a folder a project keeps up to date or releases, nor one inside or holding one. Nothing was changed: pick another folder. |
| `... its seed copy stays at ...: ...` | *Seed copies* was set in the settings file by hand to a folder that cannot be used (the rest of the line says why). Seeding goes on from the copy the project has, which stays. Set *Seed copies* (Settings) to a folder that can be used, or empty it. |
| `Seed copies not deleted within 2 seconds go at the next clean-up ...` | Nothing to do: a big copy takes a while to delete. The next clean-up (the next seed run, or the next change of these settings) finishes it; until then the copy keeps its `seedstore.json`. |
| `Warning: the list of seed copies ... cannot be read (...)`, or `... could not be read (...): it is kept as ...bad` | `seed-copies.json` beside the settings file is damaged. Nothing to do: the next clean-up (a seed run's start, or a change of the settings) keeps it as `seed-copies.json.bad` and makes a new one from the seed copies at the projects' places. A copy elsewhere (in a seed copies folder used before) is no longer on it: delete such a folder yourself if you want the space back. |
| `Warning: the settings file ... cannot be read whole (...): no seed copy is deleted until it can be read again` | A project's settings file is damaged: where that project's copy is cannot be known, so no seed copy is deleted. Fix the file, or save it again. |
| `... its settings file cannot be read whole (...): it is seeded as it was` | The project's settings file cannot be read (damaged, or caught while an editor saved it). Seeding goes on as it was set, and no seed copy is deleted while it cannot be read. Fix the file, or save it again. |
| Windows (or macOS, with its firewall on) asks whether DeltaPatcher may accept connections | It asks the first time DeltaPatcher seeds. Allow it so other players can reach you; refusing still lets you download. On Linux, open the *Torrent port* in your firewall (ufw, firewalld) yourself. |

## The programs

| Problem | What to do |
| --- | --- |
| The antivirus quarantines the DeltaPatcher app or `deltapatcher-cli` | New, unsigned programs are flagged by reputation checks. Restore it and add an exception, run from source, or (for publishers) sign the programs. |
| No tray icon on Linux | The desktop needs a tray (AppIndicator or an X tray). Everything else works without one. |
| `could not ask GitHub: ...` (Update DeltaPatcher) | No connection to github.com, or GitHub limits how often one address may ask (60 times an hour): try again later. |
| `... does not match the SHA-256 GitHub gives for it: not used` | The download was damaged or changed on the way; nothing was installed. Try again, or download it from the release page. |
| `this DeltaPatcher runs from source` (self-update) | Update the repository instead: `git pull`. |
| The build says `The build needs Python 3.11 or later` | Install a newer Python, delete `.venv`, and run `build.ps1` / `build.sh` again (on Linux: `PYTHON=python3.12 ./build.sh`). |
| `Cannot serve: ... address already in use` | Another program uses the port. Pick another port (Release page, **Where players get it**, **This computer**), or `--port`. |
