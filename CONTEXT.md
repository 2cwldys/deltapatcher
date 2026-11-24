# DeltaPatcher: context

How DeltaPatcher fits together, why it is built the way it is, and where it is going. How to change it is
in **CLAUDE.md**; how to use it is in the guides (`docs/`, also on the wiki).

## What it is

Delta patching for any game and any file. A publisher releases a folder; players' updaters download only
the files that changed. It never looks inside a file: a `.pk3`, a `.zip` or an `.exe` is bytes with a
SHA-256. Two kinds of people use it:

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

The engine's modules: `config.py` (the settings files), `publish.py`, `manifest.py` and `hashing.py` (a
folder becomes a store; the rules for leaving files out and for files players keep), `client.py` and
`source.py` (updates, rollback), `serve.py` (the built-in server, for testing on one computer), `deploy.py`
and `ssh.py` (sending a store over SFTP or to a folder; keys and host keys), `signing.py` (Ed25519),
`selfupdate.py` (DeltaPatcher's own updates), `logs.py`, `color.py`, `menu.py`, `cli.py`.

## How a release travels

1. **Publish.** Every file is hashed. The store gets `manifest.json` (every path, size and SHA-256),
   `blobs/<sha256>` (each distinct file once) and, when signed, `sigs/<sha256 of the manifest>.sig`.
   `manifest.previous.json` keeps the release before.
2. **Deploy.** Only the blobs the target lacks are sent, each as `.part` and then renamed; then the
   signature; the manifest last, so players never see half a release. Blobs no manifest names go afterwards.
3. **Update.** A player's folder is compared with the manifest. Missing blobs are downloaded and checked
   before anything changes, and the swap is journalled with a rollback copy. Everything an update keeps (the
   journal, the lock, the pinned publisher key, the record of what changed) is in `<folder>/.deltapatcher/`,
   so different folders never share state.

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
  input, then its process tree two seconds later), and the main process refuses every run but the version,
  the project list, the backup list and settings until the stop is lifted, also after a restart
  (`force-stop.json` in the app's data folder). Within those two seconds the engine stops at its next safe
  point: an update before it moves its first file, a publish, deploy or restore before its new manifest
  goes up. Lifting it starts nothing that was stopped; the automatic schedules count from then. It covers
  what the app runs, not the command line or the MCP server.
- **Torrents need nothing outside DeltaPatcher.** Peers come from the project file's seeds, peers met
  before, peer exchange and the local network, and a project with no address finds its release in a DHT
  started only from those: no tracker, no public DHT router. The DHT protocol cannot be closed to an
  outside node that reaches it, so integrity never depends on it: every item is signed and every file
  hashed. libtorrent's own check of a seeded file is not used (it misses a file changed in place): the
  engine hashes, and tells libtorrent which pieces it has.

## State

Version 0.3.0: one Release page for publishing and sending, a menu of plain questions on the command line,
self-update, putting a key on a server with its password, Docker and Pterodactyl, the wiki. Since then:
several projects, project files to share them, Reset and Remove all, hourly checks with an optional
auto-update, minimize to tray and start with the computer, the wiki from the command line, and an MCP
server for AI assistants, statistics for publishers, backups of the live release, Force stop, projects
that follow their publisher, and torrents beside HTTP. A
compatibility test keeps what each released version wrote working.

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
   command line, or `project import`. Setting a project up by hand stays.
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
12. **Torrents beside HTTP** (the engine is built). Set by the publisher per project, off by default,
    signed releases only (`deltapatcher/torrent/`): the store as a BitTorrent v2 torrent
    (`release.torrent`), and for a project with no address its newest release found in a DHT by the
    publisher key (`release.item`, numbered so a player never goes back). libtorrent 2.0.15 is frozen into
    the engine; no tracker, no public DHT router, no port mapping unless asked. Players can refuse torrents
    completely; seeding is their yes per project, from their own folder, offering only files the engine
    checked, paused while an update changes them.
