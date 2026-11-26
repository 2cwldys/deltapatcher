# Publishing a release

You have a folder with your game, mod or asset pack. Releasing it publishes it as a *store* (what
players update from) and sends that where players download it. The app keeps the store in its own folder.

## 1. Lay out the release folder

Put the files exactly as they should appear in the player's folder. Subfolders are kept. Any file type
works.

Leave out what players should not get:

- App: on the **Release** page, *Leave out of the release*: **Add folder** or **Add file**, picked from
  inside the release folder. A folder leaves out everything in it; a file is that one file only.
- Command line: `--leave-out screenshots --leave-out notes.txt` (a path inside the release folder:
  typed, pasted or dragged into the window). Without it, the list saved by the app is used.

Always left out: `.git/`, `.svn/`, `.hg/`, `__pycache__/`, `Thumbs.db`, `desktop.ini`, `.DS_Store`,
`*.tmp`. Links and junctions are never followed. Publishing lists what it left out.

For patterns, a `.deltaignore` file in the release folder works too, one a line (`# comments`):
`*.log` is every .log in every folder, `docs/*.pdf` a path from the top, `cache/` every folder named
cache, and `/notes.txt` (a leading `/`) that one file only.

Do not put DeltaPatcher itself, or player data (saves, settings the game writes), in the release
folder.

## 2. Publish

App: the **Release** page, step **1. What to release**: the folder, *Name* and *Version*. **Release** (step
3) publishes it and sends it on; see section 5.

Command line:

```
deltapatcher publish ./release ./store --name "My Game" --version 1.4 --protect config/user.cfg
```

Publish into the **same store** for every new version (1.4, 1.5, ...): only the files that changed are
added, and players download only those.

What happens:

- Every file is hashed. Unchanged files are recognised by size and date, so publishing again is fast.
- Each distinct file content is copied once into `store/blobs/`, named by its SHA-256. Two identical
  files are one blob.
- `store/manifest.json` is written. The one before it is kept as `manifest.previous.json`, so players
  who started updating a moment ago can still finish.
- Blobs that neither manifest names any more are deleted from the store (`--no-prune` keeps them).
- Publishing exactly the same files again changes nothing, and the manifest keeps its date.

### Big archives

A file of 8 MiB or more that is a zip (a `.pk3`, `.zip`, `.jar` and any other), a tar, a 7z or a RAR (DeltaPatcher
reads its structure, never its name) gets a small *parts index* by itself, in `store/parts/`. When that archive
changes, players rebuild it from the files inside it they already have, and download only the files inside it that
changed: a texture changed in a 1.4 GB `.pk3` is a download of that texture. The rebuilt archive is checked byte
for byte against the release, and anything that does not work out downloads it whole, as before. Nothing is
recompressed and nothing needs setting up; publishing's summary says how many files got an index (`... 3 with a
parts index`). Making one reads a new archive once more; an archive already published is never read for it again.

**Solid archives.** 7-Zip and WinRAR can pack an archive *solid*: its files compressed as one stream, each after
the one before, which makes it smaller. DeltaPatcher reuses packed bytes as they are, so a solid archive patches
inside less, or not at all:

- 7-Zip packs a `.7z` solid by default: one solid block (a very big archive gets a few). A `.7z` of one solid block
  gets no index (the warning below says so), whatever packed streams the block has (7-Zip's BCJ2 filter for
  programs makes four), and neither does a `.7z` of one file; one of several blocks gets one, and a block with a
  changed file in it travels whole. With solid mode off (`7z a -ms=off`, or *Solid Block size: Non-solid* in
  7-Zip's window), only the files that changed travel; `-ms=8f` (blocks of 8 files) is a middle way.
- WinRAR does not pack solid unless asked (`rar a -s`, or *Create solid archive*). A solid `.rar` still gets an
  index, but players reuse only its files before the first one that changed: every file after it travels too.
  Publishing says so in one line (`data/maps.rar is a solid RAR: ...`, not a warning). Without `-s` (or with
  `-s-`), every file that did not change is reused.

Other big archives are released as any file is, and sent whole whenever they change: gzip (`.gz`, `.tar.gz`), and
an archive DeltaPatcher cannot index. Publishing says so once for each, in its log and on the **Release** page;
for a zip, tar, 7z or RAR, the line says why:

```
Warning: data/music.tar.gz (1.2 GB) is sent whole whenever it changes: pack it as .zip or .pk3 so only the files that changed inside it travel.
Warning: data/sounds.pk3 (9.6 MB) is sent whole whenever it changes: 100% of it would travel inside its index, more than a quarter: its files under 1 KiB (89%) and the bytes outside its files (11%).
Warning: data/maps.7z (300.0 MB) is sent whole whenever it changes: packed as one solid stream: with 7-Zip, pack it with solid mode off (-ms=off) so only the files that changed travel.
```

Nothing is blocked, and players see nothing of it. To make a gzip archive patch inside, pack it as a `.zip`, a
`.pk3` or a plain `.tar` instead (for a game that reads only `.gz`, leave it: it still updates, whole). A zip, tar,
7z or RAR gets no index when more than a quarter of it, or more than 48 MiB, would travel inside the index (its
files under 1 KiB and the bytes outside its files, together), when its reader cannot take it apart (a zip whose
files overlap or are not where its directory says, or that Python's zip reader cannot open; a 7z, RAR or tar this
reader does not know), when its headers are encrypted (a 7z made with `-mhe`, a RAR with `-hp`), when a 7z is one
solid block or holds one file, when a tar holds a sparse file, a header longer than 64 KiB (a pax header or a GNU
long name), a pax header of more than 256 records or more than 4,000,000 pax records in all, when a 7z's header is
packed and the Python running DeltaPatcher has no lzma, when it has more files than an index can hold (1,000,000
parts, about two for each file: about 500,000 files), or when its index would be larger than 64 MiB;
[Troubleshooting](TROUBLESHOOTING.md#publishing) says what helps for each (for files under 1 KiB, nothing: it still
updates, whole). An archive that cannot be read while its index is made gets no warning but one plain line (`could
not read data/x.pk3 to index it (...): players get it whole this time`), and the next publish reads it again.

## 3. Protect player files

A *protected* file is created when a player does not have it, and never overwritten after that.
Use it for default settings the game later changes:

- App: on the **Release** page, *Players keep their own copy*: **Add folder** or **Add file**. A folder
  covers everything in it (a `saves` folder, say); a file is that one file only.
- Command line: `--protect config/user.cfg --protect saves` (paths inside the release folder).
  Without it, the list saved by the app is used.

Patterns work too, for whoever wants them: `*.ini` is every .ini in every folder, and `config/*.cfg`
every .cfg under config. Upper and lower case are the same.

## 3b. Sign your releases (optional)

A signature lets players' updaters check that a release really comes from you, and refuse one that
does not. That covers a release tampered with on the server, or a manifest changed on the way over plain
http. Signing is your choice, and so is checking for players: an unsigned release still installs for
anyone who has not been told to expect a signature.

1. Make a signing key once:
   - App: **Release** page > **Set up your server** (or **Sign releases**, when releases go to a folder or this
     computer) > **New signing key** (asks a passphrase twice; the public key goes to the clipboard)
   - Command line: `deltapatcher sign keygen`

   The key is `%APPDATA%\DeltaPatcher\signing-key.pem` (Windows) or `~/.config/deltapatcher/signing-key.pem`,
   readable by your account only. Back it up somewhere safe: it cannot be made again.
2. Sign each release: turn on **Sign each release**, or add `--sign` on the command line. The passphrase is
   asked each time and not kept. The signature is written to `store/sigs/<hash of manifest.json>.sig`
   and deployed with the release.
3. Give players your **public key**, the `ed25519:...` text from step 1 (**Copy public key**, or
   `deltapatcher sign pubkey`). It is not secret. Put it where players get DeltaPatcher:
   - in a `deltapatcher.ini` beside the DeltaPatcher app: `trust_key = ed25519:...` (with `source_url`)
   - in a launcher: `deltapatcher-cli.exe update ... --trust-key ed25519:...`
   - or on your website, for players to paste into **Publisher key**.

A player who was not given the key trusts the key of the first signed release they download, and only
that key from then on. If your key is ever lost or leaked, make a new one and give players the new
public key; they accept it once with `--trust-key`.

## 3c. Apache hosting: the .htaccess

Every publish writes `store/.htaccess`, and deploys send it along. On an Apache server it makes the
folder serve only the release: no directory listings, a type for blobs (they have no extension), the
right cache headers, and everything else refused. Other web servers ignore it, and DeltaPatcher's own
server never serves it. Turn off **.htaccess for Apache**, or use `--no-htaccess`, to leave it out.

## 4. Test it on your PC

App: **Release** page, **Where players get it**: **This computer, to test**, then **Release**; then **Update** a test folder
from `http://localhost:8000/`.
Command line: `deltapatcher release --to local`, then `deltapatcher update ./test --source http://localhost:8000/`.

## 5. Put the store where players can read it

The store is plain static files: any web server can serve it. Choose one:

- **A VPS**: **Release** page, **Where players get it**: **Your server (SSH)**. See [Setting up a VPS](VPS-SETUP.md).
- **A folder**: **A folder** there, for a web root on this machine or a network share. Command line:
  `deltapatcher release --to folder --to-folder D:/www/game`.
- **Any static host** (object storage, a CDN): upload `manifest.json`, `manifest.previous.json`,
  `blobs/`, `parts/` and `sigs/` (and `.htaccess` for Apache). Upload the blobs, parts indexes and signatures
  first and `manifest.json` last.

Deploying sends only the blobs and parts indexes the target does not have yet. A big archive's rebuild needs a
web server that answers Range requests: every real one does (Caddy, nginx, Apache, IIS, object storage and CDNs).
One that does not still works, and the archive then comes whole.

**Force stop** (the red box at the bottom left of the app) ends a release at once. Players get a release
only once its `manifest.json` is in place, the last step, so a release stopped before that never reaches
them: the one before it stays live.

## 6. Give players the updater

Players need DeltaPatcher and your source address. Common ways:

- **A project file**, the easiest for players: on the **Projects** page, **Export...** (in the **...** menu at the top), choose
  **Public: for players**, tick the projects to put in it, and pick its file type: `.deltapatch` opens DeltaPatcher with a
  double-click, and `.html` is a page anyone can read in a browser (players drag it onto DeltaPatcher). (**Private: for you, on
  any computer** is the other type: see [Releasing from another computer](#releasing-from-another-computer).) Players then tick the projects and
  pick their folders; nothing to type. For each project the file holds its name, the address players update
  from (*public_url*, else *source_url*), its publisher key, and a description and website when set (the
  *description* and *website* settings); never a folder, a login or a key file. Say where it goes, too:
  *install_hint* is a line players read as they pick its folder ("Pick the game's folder; this goes into its
  Mods folder."), and *install_folder* the folder it goes into inside the one they pick ("Mods"). With a
  signing key, the
  file is signed: a copy changed after you signed it is refused, and players trust your key from their very
  first download. Command line:
  `deltapatcher project export "My Game" --out MyGame.deltapatch --sign`.
- **Ship the DeltaPatcher app** (the unzipped Windows `.zip` or the one-file `-portable.exe`, the AppImage on
  Linux) next to the game, with a `deltapatcher.ini` beside it that already holds
  `source_url` (and `trust_key` if you sign). Players just click **Update**. Without the file, tell them
  the address once; the window remembers it.
- **A one-click updater** for your launcher or a `.bat` file, using the command-line program:

  ```
  @echo off
  "%~dp0deltapatcher-cli.exe" update "%~dp0game" --source https://game.example.com/
  pause
  ```

  Exit code 0 means updated or already up to date, and 1 means it failed (the folder was left as it was).
  `--check` returns 2 when an update is available, which is useful to show an "update available" badge.

- **Your own code**: from Python, `deltapatcher.client.update(url, folder)`. Or write a client in any
  language from [the format](FORMAT.md).

Keep the updater outside the folder it updates (`game\` above), so a release never has to replace the
program that is running.

## Several projects

Each game, mod or pack you release is a project of its own: its release folder, name and version, the
files left out and the files players keep, and where it goes. Pick it in the project picker at the top
left (or make one with **New project...**), then use the **Release** page as above. On the command line,
`--project NAME` does the same:

```
deltapatcher --project "Map Pack" release --version 1.2
```

The signing key and the SSH key are DeltaPatcher's, shared by every project unless a project's settings
name others. Players can keep several of your projects up to date at once: see
[For players](PLAYERS.md#several-games).

## Releasing from another computer

A project file comes in two types, chosen first when you export one (**Projects** page, **Export...**):

- **Public: for players**: what players need to keep a project up to date ([section 6](#6-give-players-the-updater)).
- **Private: for you, on any computer**: what releasing the project needs, to keep changing and releasing it from
  another computer of yours: the release's rules (name, version, the files players keep and the files left out,
  signing), where it goes (the server's address, its SSH port, your login name and the remote folder, or a folder;
  the address players update from; the access log), how it is shared by torrent (its torrent id and seeds), how
  many backups are kept and their size limit, and your server's host keys. Never a folder of this computer, a key
  or a password, and never when backups are made: each computer sets its own schedule
  ([Backups](#backups)). Keep it to yourself.

To release from a second computer:

1. Export a private file: **Private: for you, on any computer**, the projects, its file type. It is saved as
   `My Game (private).deltapatch` and signed with the projects' own signing key (they must sign their releases,
   all with the same key). Save it outside the folder you release, its store and the folder releases are copied
   to: DeltaPatcher refuses a place there, and a release leaves out any private project file it finds in the
   folder it releases. Command line:
   `deltapatcher project export "My Game" --out "My Game (private).deltapatch" --private`.
2. Carry your signing key to the other computer yourself, separately: a USB stick, never chat or email. It is the
   file named by *signing_key*: `%APPDATA%\DeltaPatcher\signing-key.pem` (Windows) or
   `~/.config/deltapatcher/signing-key.pem`, unless you chose another. Players accept only releases signed with it,
   so the other computer must sign with this very key.
3. Open the private file there (drag it onto DeltaPatcher's window, or double-click it). DeltaPatcher shows the
   signing key it is signed with, and asks for that computer's own: the folder to release (or later), your signing
   key's file, your SSH key's file (**Make one** makes a key for that computer: add the line it gives to the
   server's `~/.ssh/authorized_keys`, or use **Put key on server** on the Release page), the folder releases are
   copied to when they go to a folder, and whether that computer seeds the project. The server's host keys from the
   file are shown with their fingerprints: trust them, and the first release does not ask about the server. They
   are added only while that computer trusts no key for the server; when it trusts others, both are shown and
   none is added.
   Command line:
   `deltapatcher project import "My Game (private).deltapatch" --publish-src D:\dev\game --signing-key D:\keys\signing-key.pem --trust-host-keys --yes`
   (`--trust-host-keys` adds the keys it prints; `--host-key-fingerprints SHA256:...,SHA256:...` only those).
4. Release as usual. Your signing key is checked against the file's: when you open the file if the key has no
   passphrase, otherwise at the first release, which asks for the passphrase anyway. Another key is refused, with
   a plain reason.

- **One source for the folder you release**: a shared drive, or git, so both computers release the same files.
- **One computer releasing at a time**: a release from one replaces what the other sent.
- **A change made on one computer** reaches the other with a newer private file: open it there, and the project
  that computer releases with the same signing key is updated in place, after DeltaPatcher shows what changes.
  That computer's folders, key files, backup schedule and whether it seeds stay as they are.
- **An older file is not applied unless you say so.** A file made before the one applied to the project there (or
  that same file again) shows both times, the file's and the one applied there: the two come from two computers'
  clocks, so a wrong clock shows. **Apply it anyway** (command line: `--apply-older`; `--yes` alone never does)
  puts back what it says. An export records its time on the computer that made it too, so opening your own
  earlier export there is caught the same way.
- **A private file only ever changes a project released with its own key.** Every other project stays as it is:
  one signed with another key is set up as a project of its own, never as an update of one you have. When that
  computer releases a project of the same name but DeltaPatcher does not know its signing key yet (a key with a
  passphrase, never released or exported there), it says so: release that project once or export a private file
  from it, then open the file again; or set the file's project up as a separate one.
- A player who is given a private file by mistake is told what it is, and adds it as a player: only what a public
  file holds. It never changes a project they already have: they are told they have it as a player.

## Changing a project later, and patch notes

Each release carries the project's details (description, website, where it goes, the address players
update from, the publisher key), signed with it, when *share_updates* is on (Settings, *Publish*; on by
default). Players whose project follows you are asked before a change is applied; a new address or key
reaches them only signed with the key they trust now. Change the details on the Settings page, then
release: a release with only new details is a new release too. Your own project, the one that releases, is
never asked about them, also when you test a release from this computer or a folder.

- **Export...** offers, for a public file, **update capable** (players are asked about your later changes) or a
  one-off file.
- **Patch notes**: *show_files* (on by default) lets DeltaPatcher show players the names of the files a
  release adds, changes and removes (the manifest is public either way). **What is new, for players** on the
  Release page, or `--notes`, adds your own notes to the release.

## Sharing by torrent

Players' DeltaPatchers can download a release from each other as well as from your address: less traffic
for your server, or no server at all. It is off by default, set per project, and needs signed releases
([3b](#3b-sign-your-releases-optional)).

1. Turn it on: *Share by torrent too* on the Release page (or Settings), and *Seeds*, the computers players
   connect to first
   (`deltapatcher config set torrent=yes seeds=game.example.com:6881`). DeltaPatcher makes the project's
   torrent id once; it never changes.
2. Release as before. The store also gets `release.torrent` (the release as a BitTorrent v2 torrent: the
   manifest, its signature and the blobs) and `release.item` (a signed pointer to it), sent before the
   manifest.
3. Export the project file again: it now carries the torrent's id, name and seeds, and must name your
   publisher key. Players who follow you are asked about a changed list of seeds, as about a new address.
4. Keep a seed running where players can reach it: *Seed it from this computer* on the Release page
   (DeltaPatcher seeds while it runs, in the tray too), or `deltapatcher seed --all` on a server or in a
   container ([docker/README.md](../docker/README.md#seeding-from-a-container)).
   At home, forward the *Torrent port* (6881, TCP and UDP) on your router to that computer, or turn on *Port
   mapping*, and let it through that computer's firewall.

- **With an address**, the address stays the floor: what no peer has comes from it, and an update never
  fails because of torrents.
- **With no address** (no `source_url`, no `public_url`), the project updates by torrent only: players find
  your newest release in a DHT, by your publisher key and the project's torrent id. A new player needs at
  least one seed they can reach: list several, and keep one always on. With nobody seeding, nobody new can
  start.
- **Peers are found** from the project file's seeds, peers met before, peer exchange, the local network
  while seeding, and (with no address) a DHT that starts only from those: no tracker, no public DHT router.
- **A new release reaches the seeds** within about 5 minutes: each seed run looks for one that often (reading
  only the small signed `release.item` when nothing changed), and with no address puts your release item in
  the DHT again every 30 minutes.
- **A restore** (Backups) of a project shared by torrent only does not move players to it: publish its
  release again.
- **Turning it off**: the next publish takes `release.torrent` and `release.item` out of the store. A copy
  left on the server names an older release, so players pass over it and use the address.
- **Players decide**: seeding is their yes, per project, and they can refuse torrents completely (then they
  update from your address only).

What you answer for: [Torrents: the law and your privacy](../SECURITY.md#torrents-the-law-and-your-privacy).

## Statistics

The **Statistics** card on the **Release** page, or on the command line:

```
deltapatcher stats [--days N]
```

The last 30 days by default (up to 366); `--json` gives the whole document, for your own tools.

- **Releases**: the live release, what it changed from the one before (its new, changed and removed files,
  and the size of the new and changed ones: `1.4 GB of new and changed files`, what a player on the release
  before downloads, less when it rebuilds a big archive from what it has), the store's size, and every release
  so far. Each release adds a line to `history.jsonl` in the store; it is never sent to your server.
- **Downloads**, from your web server's access log: update checks, downloads, bytes and players a day, and
  which release players are on. Set **access_log** (the card, the Settings page, or
  `deltapatcher config set access_log=/var/log/nginx/access.log`): its path on the server, read over SFTP
  the way a release is sent, or a path on this computer for a release sent to a folder. It reads the
  Combined Log Format of nginx and Apache, and Caddy's JSON log. Only requests under **public_url**'s path
  count. A download is a whole file (a `200`). A parts index is a file, as a signature is: a check reads it too,
  so it counts in the bytes and the players, never as a download. A player who rebuilds a big archive
  ([Big archives](#big-archives)) downloads less than the whole file, and it is no download either: the ranges
  fetched for it (`206`), like the rest of a cut download, count in the bytes only.

Only counts are kept. No address, user agent or log line is stored, logged or sent anywhere: players are
told apart in memory, by a salted hash, while the log is read, and a log copied from the server is deleted
as soon as it is read. An updater says only which release it has, in its User-Agent
(`DeltaPatcher/0.3.0 release/1.2`).

The log named is read, not the older rotated ones (`access.log.1`); a compressed log (`.gz`), or one over
512 MB, is refused.

## Backups

A backup is a copy of your **live release**, what players download now, read from where you send it (your
server over SFTP, or the folder) and kept on this computer. The **Backups** section of the **Settings** page has
**Back up now**, the list (open it from its summary line), and **Restore...**. On the command line:

```
deltapatcher backup                    one backup now
deltapatcher backup list               the backups: date (UTC), release, files, size; the last scheduled run
deltapatcher backup restore DATE       put that backup back where players download from (asks first)
deltapatcher backup prune              apply the limits now
deltapatcher backup schedule daily     the system makes backups without the app: daily, hourly or off
```

- **Many backups cost little.** Each file is kept once, however many backups have it, and checked against
  its SHA-256 as it arrives; so is the parts index of each big archive ([Big archives](#big-archives)), which a
  restore puts back with the files. A release that has not changed since the last backup makes no new one, and
  there is one backup a day at most: a second one that day replaces it.
- **Limits**, on the Settings page under *Backups*: **backup_keep_daily** (7), **backup_keep_weekly** (4),
  **backup_keep_monthly** (0) and **backup_max_size** (empty: no limit). After each backup the newest of each
  kept day, week and month stays and the others go; then the oldest go while the folder is over the size.
  The newest backup always stays.
- **Where they are**: a folder for each project, named after its settings file with a short id
  (`My Game-1a2b3c4d`), inside **backup_dir** (else inside `backups` beside the main settings file). Two
  projects never share one, and it can never be the project's store or a folder it releases, sends to or
  keeps up to date.
- **Restore** is a release of the backup: its files first and its manifest last, and nothing at your
  server or folder is removed. It asks first; `--dry-run` says what it would send.
- **By itself**: **backup_every_hours** (0, off, by default) has the app make backups while it runs, window
  shown or in the tray, never while something else runs. With the app closed, `backup schedule daily` (or
  `hourly`) has the system start it every hour, and it makes a backup when the last one is about a day (or
  an hour) old, so a computer that was asleep or off catches up: Task Scheduler on Windows (hidden, no
  window), your crontab on Linux and macOS. `off` removes only that project's entry; `backup list` and the
  Settings page show the last scheduled run. A cron job cannot reach your ssh-agent, so on Linux and macOS
  a scheduled backup over SFTP stops with *key-locked* (DeltaPatcher warns when you schedule it): let the
  app make them instead (**backup_every_hours**).
- **On each computer**: backups are set on each computer that makes them, as is their folder. A private project
  file carries how many are kept and the size limit, never when they are made.

A release sent only to this computer (*This computer, to test*) has nothing to back up.

## Versions

*Version* is shown to players and in the log. It does not decide anything: clients always move to
whatever the manifest lists, so publishing an older folder again rolls everyone back to it.
