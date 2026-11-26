# DeltaPatcher: context

How DeltaPatcher fits together, why it is built the way it is, and where it is going. How to change it is
in **CLAUDE.md**; how to use it is in the guides (`docs/`, also on the wiki).

## What it is

Delta patching for any game and any file. A publisher releases a folder; players' updaters download only the files
that changed. A file is bytes with a SHA-256: a `.map` or an `.exe` that changed travels whole. A big archive (a
`.pk3` or `.zip`, a `.tar`, a `.7z` or a `.rar`) that changed is the one exception: it is rebuilt from the files
inside it a player already has, only the changed ones travel, and the result is checked against the same SHA-256.
Two kinds of people use it:

- **Publishers** release a folder (a game, a mod, an asset pack), host it anywhere, and may sign it.
- **Players** keep a folder up to date from the address a publisher gives them.

## The parts

| Part | Where | What it is |
| --- | --- | --- |
| Engine | `deltapatcher/` | Python, standard library first; everything happens here |
| Command line | `deltapatcher/cli.py`, built by `packaging/deltapatcher.spec` | the engine as one program: commands for scripts, a menu for beginners (`menu.py`), `--json` for programs |
| App | `app/` | Electron, React, Tailwind, zustand; it runs the engine for every job (`app/src/main/engine.ts`) and reads its `--json` events |
| Packaging | `packaging/`, `app/electron-builder.yml` | the engine as one file (the command line) and as a folder (inside the app); the app as setup and zip, AppImage and tar.gz, dmg and zip |
| CI | `.github/workflows/` | build (the tests, the engine, the app and the release files on Windows, Linux and macOS), CodeQL, secret scan |
| Tools | `tools/` | the version, the release files, screenshots, the social preview, the wiki, a README preview, the secret scan, the icon |
| Containers | `docker/` | an example image and a Pterodactyl egg that bring their files up to date at every start |
| MCP server | `deltapatcher/mcp_server.py`, `mcp/` | DeltaPatcher for AI assistants (`deltapatcher mcp`): the Model Context Protocol on standard input and output, standard library only; every tool runs the engine with `--json` |

The engine's modules: `config.py` (the settings files), `publish.py`, `manifest.py` and `hashing.py` (a folder
becomes a store; the rules for leaving files out and for files players keep), `client.py` and `source.py` (updates,
rollback), `parts.py`, `layouts.py` and `rebuild.py` (the parts index of a big archive, the readers of a tar's,
7z's and RAR's layout beside zip's, and an update's rebuild of a changed archive from what the folder has),
`serve.py` (the built-in server, for testing on one computer), `deploy.py` and `ssh.py` (sending a store over SFTP
or to a folder; keys and host keys), `signing.py` (Ed25519), `selfupdate.py` (DeltaPatcher's own updates),
`logs.py`, `color.py`, `menu.py`, `cli.py`.

## How a release travels

1. **Publish.** Every file is hashed. The store gets `manifest.json` (every path, size and SHA-256),
   `blobs/<sha256>` (each distinct file once) and, when signed, `sigs/<sha256 of the manifest>.sig`.
   `manifest.previous.json` keeps the release before. A file of 8 MiB or more that is a zip, tar, 7z or RAR also
   gets `parts/<sha256 of the index>`, its parts index, named by its entry's `parts`.
2. **Deploy.** Only the blobs and parts indexes the target lacks are sent, each as `.part` and then renamed;
   then the signature; the manifest last, so players never see half a release. Blobs and parts indexes no
   manifest names go afterwards.
3. **Update.** A player's folder is compared with the manifest. Missing blobs are downloaded and checked
   before anything changes, and the swap is journalled with a rollback copy. A changed archive with a parts
   index, from an http(s) address, is first rebuilt from the pieces the folder has, the rest fetched with Range
   requests. Everything an update keeps (the journal, the lock, the pinned publisher key, the record of what
   changed) is in `<folder>/.deltapatcher/`, so different folders never share state.

## The --json protocol

The app (and any program) drives the engine with `--json`: one JSON object a line. `log` and `progress`
events while it works; the last line is either `{"event": "result", "ok": true, "data": {...}}` or
`{"event": "error", "message": "...", "code": "..."}`. Standard input carries what a person would type,
such as a passphrase with `--passphrase-stdin`. The full list is at the top of `deltapatcher/cli.py`.

## Decisions, and why

- **Static hosting only.** A release is plain files any web server can serve: no server program to
  install, run or secure. It is also why download statistics read the web server's own access log.
- **Content addressing.** A blob is named by its SHA-256: identical files are stored once, unchanged files
  never travel, and a damaged download is caught before it is used.
- **Manifest last, journal always.** A deploy or an update that stops halfway changes nothing anyone sees,
  or is undone.
- **Signing is optional.** Unsigned releases work. Signed ones let updaters refuse anything the publisher
  did not sign; a player is either given the publisher key or trusts the first signed release's key.
- **SSH through the system's OpenSSH.** DeltaPatcher never reads a private key: keys stay in `~/.ssh`,
  unlocked in ssh-agent, and host keys are confirmed once and checked strictly. paramiko is used only to put a
  key on a server with its password, once; the password is never kept.
- **Settings in plain text files, shared by the window and the command line.** Whatever one saves, the
  other uses. Nothing secret is ever in them.
- **A domain name is optional.** Players can use a server's IP address over plain http; signing then
  protects the manifest.
- **Self-contained programs.** The command line is one file and the app carries its engine. Both update
  themselves from the GitHub releases, checked against the SHA-256 GitHub gives, so release file names are
  an interface: app files carry the version, command-line files do not.
- **Releasing the same version again** with new files (a shadow patch) is an ordinary update: only the
  changed files travel.
- **An upgrade never breaks what people have.** Settings and project files, releases and updated folders
  written by an earlier version keep working: settings live outside the program's folder, new settings
  come with defaults, and `tests/test_compat.py` proves it against a frozen copy of what each released
  version wrote.
- **AI assistants get tools, never secrets.** The MCP server runs the engine the way the app does, and no
  tool takes a passphrase or a password: signing, unlocking a key and putting a key on a server stay with a
  person. Tools that change files or servers are marked so a client asks first.
- **Force stop holds until it is lifted.** One click ends every engine run the app started ("cancel" on its
  input, then its process tree two seconds later), and the main process refuses every run but the version
  (not its BitTorrent test), the project list, the backup list and settings until the stop is lifted, also after a restart
  (`force-stop.json` in the app's data folder). Within those two seconds the engine stops at its next safe
  point: an update before it moves its first file, a publish, deploy or restore before its new manifest
  goes up. Lifting it starts nothing that was stopped; the automatic schedules count from then. It covers
  what the app runs, not the command line or the MCP server. It also turns seeding off for every project
  that had it, until the person turns it on again.
- **Torrents need nothing outside DeltaPatcher.** Peers come from the project file's seeds, peers met
  before, peer exchange and the local network, and a project with no address finds its release in a DHT
  started only from those: no tracker, no public DHT router. The DHT protocol cannot be closed to an
  outside node that reaches it, so integrity never depends on it: every item is signed and every file
  hashed. libtorrent's own check of a seeded file is not used (it misses a file changed in place): the
  engine hashes, and tells libtorrent which pieces it has.
- **Two types of project file: public for players, private for the publisher.** Exporting one asks its type
  first. A public file is what players need, as it always was. A private file (version 4, `"private": true`) is
  the same signed page with one more part per project: what releasing it needs, as an allow-list of settings
  (`presets.PRIVATE_FIELDS`, so a setting added later never slips in) and the server's host keys this computer
  trusts; never a path of the computer that made it, a key, a passphrase or anything that is each computer's own
  choice (where it seeds from, when it backs up). It is always signed with the projects' signing key, which moves
  by hand (a USB stick, never chat or email): the file alone cannot release anything. Opening it sets up releasing
  on that computer with its own folders and key files, or updates in place the project that computer releases with
  the very key the file is signed with (`presets.private_match()`: never by the key a player trusts, so a private
  file can never change a player's project; one of its name whose key is not known yet is named, never updated),
  after showing what changes. A file made before the one applied (`project_file_made`, which an export records too),
  or that same file again, is applied only when the person says so: the two times come from two computers' clocks,
  so both are shown. The server's host keys are added only with the person's yes, and only while that computer
  trusts no key for that server. A signing key that is not the file's is refused, at the first release when it has
  a passphrase (`signing_public_key` remembers which key that is; an export records it too). Every text in a
  project file is one line (a public file's line and paragraph separators read as spaces), and settings files end a
  line at `\r\n`, `\r` or `\n` only. The MCP server makes and sets up none: they need the passphrase or the person's
  yes.
- **Players seed from a copy, by default.** A player's seeding reads a copy of the release kept for
  seeding, laid out like a store (`deltapatcher/torrent/seedcopy.py`), not the game folder: the game's
  files are not held open while it seeds (Windows refuses to replace an open file), so updates never wait
  for seeding, and an edit in the folder never reaches the copy. Filling the copy reads the folder, and stops
  at once when an update asks. The copy sits in the game folder's own `.deltapatcher/seedstore/<torrent id>`
  (the same drive, gone with the folder), or every copy in the folder `seed_copies` names (one that is there,
  of its own); it is a space-sharing clone where the drive can make one, and always leaves the larger of
  1 GiB and 5% of its drive free (never more than 20 GiB). It is deleted once its project stops seeding from
  it (a seed run, once two reads of the settings agree), never while another project needs it or a settings file
  cannot be read whole, and only ever a folder its marker says is a copy, never through a link (a symbolic link
  or a junction); `seed-copies.json` lists every copy made, and only a copy on it is deleted, so another
  DeltaPatcher's is never taken (a clean-up puts back on it a copy at a project's own place that this
  DeltaPatcher made, so a lost list loses none). Seeding straight from the folder stays as a choice per project
  (`seed_copy = no`). A publisher's own store is never copied.
- **Archives are rebuilt, never repacked.** A changed zip (`.pk3`, `.zip`, ...), tar, 7z or RAR (found by its
  structure, never by its name: `layouts.py` reads each format's documented layout with Python's standard library,
  every count and offset checked) is rebuilt byte for byte from pieces: the packed data of the files inside it the
  player already has (in the old archive, or another archive DeltaPatcher installed in the folder), the pieces that
  changed (fetched from the whole blob with HTTP Range requests), and the small structural bytes its parts index
  carries. Nothing is ever recompressed, so no compressor has to match the publisher's. The server keeps whole
  blobs as before (older DeltaPatchers update as they did, nothing is stored twice); each big archive gets one
  small extra file, `parts/<sha256>`, named by an optional `parts` in its manifest entry, which keeps the manifest
  at format 1 (DeltaPatcher 0.2.0 reads only path, sha256 and size). Correctness never depends on the rebuild, only
  the amount downloaded does: the rebuilt file must be the manifest's SHA-256, every index and every server answer
  is untrusted input, and anything that does not work out is the whole download of before. No new backup is needed:
  the rebuild writes into the update's cache, the old archive is untouched until the update applies and is kept in
  `rollback/` until the next one, and a failed update is undone by the journal. Big archives it cannot patch inside
  (gzip, a 7z of one folder, an archive with encrypted headers, any that gets no index) are tracked and served
  as any file is, whole, with one warning at publish that says why; players see nothing. Folder sources and
  projects with no address fetch archives whole: a copy from a folder is fast already, and a swarm has no ranges to
  fetch. A project with an address and torrents rebuilds first; its swarm gets only what is still missing. The
  files a player keeps as their own (protected, or kept as edited here) are never opened for pieces. A solid RAR
  gets an index all the same, and only an unchanged run from its start is reused (publish says so).

## State

Version 0.3.0: one Release page for publishing and sending, a menu of plain questions on the command line,
self-update, putting a key on a server with its password, Docker and Pterodactyl, the wiki. Since then:
several projects, project files to share them, Reset and Remove all, hourly checks with an optional
auto-update, minimize to tray and start with the computer, the wiki from the command line, and an MCP
server for AI assistants, statistics for publishers, backups of the live release, Force stop, projects
that follow their publisher, torrents beside HTTP, private project files to release a project from another
computer, and archives rebuilt from the files inside them a player has (zip, tar, 7z and RAR). A compatibility
test keeps what each released version wrote working.

## Where it is going

One project alone is too narrow a case: several projects is the normal way to use DeltaPatcher, as modular
as possible without becoming hard to use.

1. **Several projects** (built). A project is one settings file: what it updates (folder, source,
   publisher key) and what it releases (folder, server). The main `deltapatcher.ini` stays a project,
   "Default"; others live in `projects/` beside it, or anywhere and are added by path. App-wide settings
   stay in the main file: `[window]`, and `[general]` with `parallel_updates`, `project_files` and
   `current_project`. The command line takes `--project NAME` on every command and uses Default without
   it, so a script never follows the window's choice; `update --all` and `check --all` run up to
   `parallel_updates` projects at once, in one process. The app's sidebar has the Projects page (every
   project's state, Check all and Update all), then the project picker, then the picked project's pages.
2. **Project files** (built). One file with one or more projects (name, address, publisher key, and the
   description, website, where it goes and its folder name), to share, as an `.html` page anyone can read
   and as a `.deltapatch` file DeltaPatcher opens with a double-click: the same text either way
   (`deltapatcher/presets.py`). Signed when the publisher has a signing key; never a folder, a server login
   or a key path. Imported by drag-and-drop, the Projects page, a double-click, a file dropped on the
   command line, or `project import`. Setting a project up by hand stays. A private project file carries what
   releasing a project needs to another computer of its publisher's (`project export --private`), and keeps
   both in step when a newer one is opened.
3. **Telling players** (built). Every project is checked at start and every `check_every_hours` (1 by
   default); Projects shows a count, and the tray gives a desktop notification. `auto_update`, off by
   default, updates them unattended.
4. **The tray** (built): `minimize_to_tray` and `start_with_system`, both off by default.
5. **No sidebar bloat** (built). Projects live in the picker, which scrolls and searches when the list is
   long; Read the wiki and Report an issue stay as links at the bottom of the sidebar; the command line's
   menu keeps its project choices under one Projects entry.
6. **Starting over** (built): Reset and Remove all never touch a project's folders, and Reset keeps a copy
   of the settings.
7. **The wiki from the command line** (built): `deltapatcher wiki [guide]`.
8. **Statistics for publishers** (built). One versioned document from collectors that can be added to
   (`deltapatcher/stats.py`): the releases, from the store and its `history.jsonl`; the downloads, from the
   web server's access log, read here or over SFTP. Counts only, never an address. `deltapatcher stats`,
   the Release page's Statistics card, the MCP `stats` tool.
9. **Backups of the live release** (built). Read from where the release is sent, kept on this computer in a
   blob pool with one dated folder a day, thinned by day, week, month and size limits; restore is a release
   of the backup that removes nothing (`deltapatcher/backup.py`). By hand, by the app every
   `backup_every_hours`, or by the system (`backup schedule`).
10. **Force stop** (built). The panic button: see the decision above.
11. **Projects that follow their publisher** (built). A release carries its project's details, signed
    (`deltapatcher/publisher.py`); a player is asked before a change is applied, and a new address or key
    must be signed with the key trusted now; a project file can be update capable (version 2) or one-off;
    players see what changed (file names, release notes) when the publisher allows it; containers take a
    project file (`update --project-file`).
12. **Torrents beside HTTP** (built). Set by the publisher per project, off by default,
    signed releases only (`deltapatcher/torrent/`): the store as a BitTorrent v2 torrent
    (`release.torrent`), and for a project with no address its newest release found in a DHT by the
    publisher key (`release.item`, numbered so a player never goes back). libtorrent 2.0.15 is frozen into
    the engine; no tracker, no public DHT router, no port mapping unless asked. Players can refuse torrents
    completely; seeding is their yes per project, from a copy of the release kept for seeding (or, as
    their choice, from their own folder, paused while an update changes it), offering only files the engine
    checked; a seed run looks for a newer release every 5 minutes, reading only the small signed
    `release.item` when nothing changed. In the window: *Share by torrent too* on the Release
    page, the seed choice when a project file is added, a seeding line and Seed or Stop seeding on each
    row, the tray's count, and Force stop turning seeding off; assistants get a read-only `seed_status`.
13. **Inside archives** (phases A and B built; the owner chose, 11-25, to finish every archive format before the
    security audits). A changed archive downloads only the files inside it that changed, rebuilt byte for byte from
    what the player has (the decision above). Phase A: zip-structured files (`.pk3`, `.pk4`, `.zip`, `.jar`, ...)
    over an http(s) address (`deltapatcher/parts.py`, `rebuild.py`); the check says the smaller amount, the app
    shows it, publish warns about the big archives sent whole. Phase B: `.tar`, `.7z` and `.rar` by their layout,
    their packed data copied byte for byte (`deltapatcher/layouts.py`), with no extra server space and no RAR or 7z
    compressor used or bundled; a 7z of one solid block stays whole, a solid RAR reuses only its unchanged start.
    Phase C (next): `.tar.gz`/`.gz` and fully solid `.7z`, by repacking to the exact same bytes, which needs the
    server to keep the files inside them too: a per-project switch on the Release page, off by default. Each phase
    takes the archives it rebuilds off the publish warning.
