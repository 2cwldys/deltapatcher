# Working on DeltaPatcher

DeltaPatcher is a delta patcher: a Python engine (`deltapatcher/`), an Electron app that drives it (`app/`),
and the same engine as a one-file command line. **CONTEXT.md** explains how the parts fit together, the
decisions behind them, and where the project is going. This file is how to change it.

## Commands

From the repository's root. The build makes `.venv`; use its Python.

| What | Command |
| --- | --- |
| Every test | `python -m unittest discover -s tests` |
| One test file | `python -m unittest tests.test_release` |
| The torrent tests | `pip install -r requirements-torrent.txt`, then `python -m unittest tests.test_torrent tests.test_torrent_swarm` (the second needs libtorrent; `DP_REQUIRE_TORRENT=1` turns its skips into failures) |
| The app: types, tests, then a build | `cd app && npm ci && npm run typecheck && npm test && npm run build` |
| The app from source | `cd app && npm run dev` (without a built engine it runs `python -m deltapatcher`) |
| Engine, app and release files | `powershell -ExecutionPolicy Bypass -File build.ps1` or `./build.sh` (`-EngineOnly`, `-Test`) |
| The version | `python tools/version.py X.Y.Z` (or `patch`, `minor`, `major`; alone: show and check) |
| Screenshots | `python tools/capture_screens.py --app <the packaged app> <an empty demo folder>` |
| Social preview | `python tools/make_preview.py`, after the screenshots |
| The wiki | `python tools/wiki.py <a clone of the wiki>`, then commit and push there |
| Preview a guide | `node tools/readme_preview/render.mjs [FILE.md] [--light]` (`npm ci` in that folder once) |
| Secret scan | `python tools/secret_scan.py` |
| The MCP server | Claude Code opened in this repository offers it by itself (`.mcp.json`; it asks once). Else `python mcp/server.py` (built: `deltapatcher mcp`); its tests: `python -m unittest tests.test_mcp` |

## A change, start to finish

1. **Engine**: change it, add tests for the new behaviour, run every test. The `.bat` tests run
   `dist/deltapatcher-cli.exe`, the last build: rebuild before trusting them with new command-line behaviour.
2. **App**: `npm run typecheck` and `npm run build`, then see it work in the real window, not only in types.
   Every engine call goes through `app/src/main/engine.ts`, which refuses an option not in `ALLOWED_OPTIONS`.
3. **The window looks different?** Rebuild, retake the screenshots from the packaged app, then the social
   preview. Look at every picture before committing it.
4. **Documentation**: the guides in `docs/` (with `docker/README.md` and `SECURITY.md`) are the one source.
   Update them, the README where it is affected, and `CONTEXT.md` when a decision or the state changes; then
   regenerate the wiki (`tests/test_wiki.py` checks that every link leads somewhere).
5. **Before a commit**: every test, the typecheck, the secret scan.
6. **Commits**: one topic each; a short title, then plain sentences on what changed and why. No
   `Co-Authored-By` or other credit lines. Commit only when asked, and never push: the maintainer pushes.

Releasing a new version of DeltaPatcher is in the README, under For developers.

## Rules

- **ASCII only** in source files. Text people read is plain English in short sentences, in the voice of the
  existing messages and guides.
- **Comments describe the code as it is**: no plan notes, no history, no version numbers.
- **Nothing secret is stored, logged or put on a command line.** Passphrases, server passwords and private
  keys never go into settings, logs, arguments or the environment. Keys are named by their file's path; a
  passphrase travels only on standard input (`--passphrase-stdin`).
- **MCP tools never take a secret.** No tool of `deltapatcher/mcp_server.py` takes a passphrase, a password
  or anything like one (`tests/test_mcp.py` checks the parameter names); keys are named by their file's path,
  as everywhere. A tool that changes files or servers says so in its annotations.
- **Untrusted input stays untrusted**: manifests, server answers, preset files, anything from the network.
  Paths from a manifest are checked before use; host keys are confirmed once and checked strictly after.
- **No personal data in the repository**: screenshots and examples use a neutral demo folder,
  `game.example.com` and `203.0.113.10`, never a real person's paths, names or addresses.
- **Windows, Linux and macOS** all have to work; CI builds and tests all three.
- **Release file names are an interface.** Self-update finds files by name (`pick()` in
  `deltapatcher/selfupdate.py`): app files carry the version (`DeltaPatcher-X.Y.Z-setup.exe`), command-line
  files do not (`deltapatcher-cli-windows-x64.exe`).
- **A settings file belongs to its user.** New settings come with defaults; never rewrite or migrate an
  existing file beyond what `config.save()` does.
- **What an earlier version wrote keeps working.** `tests/test_compat.py` loads the settings, projects,
  releases and game folders each released version wrote (`tests/fixtures/compat/`, kept byte for byte by
  `.gitattributes`). Never edit a fixture to make it pass: read the old form instead. Each release adds its
  own fixture (README, For developers, Release a new version of DeltaPatcher).
- **Pictures in the guides** open full size when clicked (the image links to its raw address) and stand one
  under another, never squeezed into table cells.
- **Text in the window**: a hint or a page's subtitle puts each sentence on its own line (`\n` between
  sentences; the `hint` class keeps line breaks), so a second sentence never wraps at a random word. Keep
  sentences short.
- **Everything the app runs goes through `runEngine`** (`app/src/main/engine.ts`), so Force stop can end it
  and refuse it. Anything that starts by itself (a timer) also checks the store's `stopped`, and counts from
  `restartSchedules()` once the stop is lifted.

## Traps

- VS Code sets `ELECTRON_RUN_AS_NODE=1` in its terminals: unset it before starting Electron, or the app runs
  as plain Node.
- On Windows, Electron finds its data folder through the system, not `APPDATA`: a test that starts the app
  passes `--user-data-dir=<a folder of its own>`, or it uses (and may change) the real app's data folder.
- A packaged app takes screenshots (`--capture`) only when `DELTAPATCHER_CAPTURE=1` is set.
- On Windows, a test or leftover process can hold `dist/deltapatcher-cli.exe` open and the build then fails
  with a permission error: find that process and stop it.
- `git commit -- <path>` drops a mode change made with `git update-index --chmod=+x` (the shell scripts need
  theirs): stage with `git add`, then commit.
- Images and binaries are in Git LFS (`.gitattributes`); without `git lfs install` the pictures are pointers.
- The build needs Python 3.11 to 3.13 (Pillow, and libtorrent 2.0.15's wheels); CI uses 3.12. The engine
  itself runs on 3.8 or later; torrents from source need 3.9 to 3.13.
- libtorrent's defaults reach the public network (listening on 0.0.0.0:6881, DHT routers, UPnP, NAT-PMP,
  local discovery): every session is made in `deltapatcher/torrent/session.py` from one settings pack.
  Tests listen on 127.0.0.x only (`DELTAPATCHER_TORRENT_TEST_IP`); local discovery binds 0.0.0.0:6771
  whatever the listen address, so it is off on loopback. Never run a seed on all interfaces on a desktop
  to test: Windows asks about the firewall. macOS answers only on 127.0.0.1 until
  `sudo ifconfig lo0 alias 127.0.0.N up`.
- Tests never share settings: `TempCase` in `tests/helpers.py` gives each test its own `DELTAPATCHER_CONFIG`.
  A test that touches settings or logs uses it.
