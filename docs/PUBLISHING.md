# Publishing a release

You have a folder with your game, mod or asset pack. Publishing turns it into a *store* that clients
update from.

## 1. Lay out the release folder

Put the files exactly as they should appear in the player's folder. Subfolders are kept. Any file type
works.

Leave out what players should not get with a `.deltaignore` file in the release folder:

```
# one pattern per line; a trailing / means a folder
*.log
screenshots/
dev-notes.txt
```

Always left out: `.git/`, `.svn/`, `.hg/`, `__pycache__/`, `Thumbs.db`, `desktop.ini`, `.DS_Store`,
`*.tmp`. Links and junctions are never followed.

Do not put DeltaPatcher itself, or player data (saves, settings the game writes), in the release
folder.

## 2. Publish

App: the **Publish** page: choose *Folder to release* and *Store*, fill in *Name* and *Version*, click **Publish**.

Command line:

```
deltapatcher publish ./release ./store --name "My Game" --version 1.4 --protect "config/*.cfg"
```

What happens:

- Every file is hashed. Unchanged files are recognised by size and date, so publishing again is fast.
- Each distinct file content is copied once into `store/blobs/`, named by its SHA-256. Two identical
  files are one blob.
- `store/manifest.json` is written. The one before it is kept as `manifest.previous.json`, so players
  who started updating a moment ago can still finish.
- Blobs that neither manifest names any more are deleted from the store (`--no-prune` keeps them).
- Publishing exactly the same files again changes nothing, and the manifest keeps its date.

## 3. Protect player files

A *protected* file is created when a player does not have it, and never overwritten after that.
Use it for default settings the game later changes:

- Window: *Protect* field, comma-separated: `config/*.cfg, settings.ini`
- Command line: `--protect "config/*.cfg" --protect settings.ini`

Patterns are matched against the whole path with `/` separators (`*` matches within a name and across
folders).

## 3b. Sign your releases (optional)

A signature lets players' updaters check that a release really comes from you, and refuse one that
does not. That covers a release tampered with on the server, or a manifest changed on the way over plain
http. Signing is your choice, and so is checking for players: an unsigned release still installs for
anyone who has not been told to expect a signature.

1. Make a signing key once:
   - App: **Publish** page > **New signing key** (asks a passphrase twice; the public key goes to the clipboard)
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

App: **Serve locally** (Publish page), then **Update** a test folder from `http://localhost:8000/`.
Command line: `deltapatcher serve ./store`, then `deltapatcher update ./test --source http://localhost:8000/`.

## 5. Put the store where players can read it

The store is plain static files: any web server can serve it. Choose one:

- **A VPS**: deploy over SSH from the **Server** page. See [Setting up a VPS](VPS-SETUP.md).
- **A folder**: the **Server** page, *A folder*, for a web root on this machine or a network share. Command
  line: `deltapatcher deploy ./store --to-folder D:/www/game`.
- **Any static host** (object storage, a CDN): upload `manifest.json`, `manifest.previous.json`,
  `blobs/` and `sigs/` (and `.htaccess` for Apache). Upload the blobs and signatures first and
  `manifest.json` last.

Deploying sends only the blobs the target does not have yet.

## 6. Give players the updater

Players need DeltaPatcher and your source address. Common ways:

- **Ship the DeltaPatcher app** (the portable `.exe` on Windows, the AppImage on Linux) next to the game, with a `deltapatcher.ini` beside it that already holds
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

## Versions

*Version* is shown to players and in the log. It does not decide anything: clients always move to
whatever the manifest lists, so publishing an older folder again rolls everyone back to it.
