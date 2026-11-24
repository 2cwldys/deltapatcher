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
  install, run or secure. It is also why download statistics, if they come, would read the web server's own
  access log.
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
- **AI assistants get tools, never secrets.** The MCP server runs the engine the way the app does, and no
  tool takes a passphrase or a password: signing, unlocking a key and putting a key on a server stay with a
  person. Tools that change files or servers are marked so a client asks first.

## State

Version 0.3.0: one Release page for publishing and sending, a menu of plain questions on the command line,
self-update, putting a key on a server with its password, Docker and Pterodactyl, the wiki. Since then:
several projects, and an MCP server for AI assistants.

## Where it is going

One project alone is too narrow a case: several projects is the normal way to use DeltaPatcher, as modular
as possible without becoming hard to use.

1. **Several projects** (built). A project is one settings file: what it updates (folder, source,
   publisher key) and what it releases (folder, server). The main `deltapatcher.ini` stays a project,
   "Default"; others live in `projects/` beside it, or anywhere and are added by path. App-wide settings
   stay in the main file: `[window]`, and `[general]` with `parallel_updates`, `project_files` and
   `current_project`. The command line takes `--project NAME` on every command and uses Default without
   it, so a script never follows the window's choice; `update --all` and `check --all` run up to
   `parallel_updates` projects at once, in one process. The app has a project picker at the top of the
   sidebar and a Projects page with every project's state, Check all and Update all.
2. **Project presets.** One file with one or more projects (title, source, publisher key), like a modlist,
   as an `.html` page anyone can read and as a `.deltapatcher` file DeltaPatcher opens with a double-click.
   Signed when the publisher has a signing key; never a folder, a server login or a key path. Imported by
   drag-and-drop, from the Projects page, by double-click or on the command line. Setting a project up by
   hand stays.
3. **Telling players.** Every project is checked at start, Projects shows a count, and the tray gives a
   desktop notification. An `auto_update` setting, off by default, updates them unattended.
4. **Minimize to tray**, a setting, off by default.
5. **No sidebar bloat.** Projects live in the picker, which scrolls and searches when the list is long;
   Help gathers the wiki and issue links.
6. **The wiki from the command line**: `deltapatcher wiki [guide]` opens it in the browser.
7. **Perhaps: statistics for publishers.** Release statistics from the store; download statistics from the
   web server's access log, read over SSH. Not decided.
