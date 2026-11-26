# DeltaPatcher documentation

Pick the page for what you want to do:

| I want to... | Read |
| --- | --- |
| Try it on my own PC in five minutes | [Quick start](QUICKSTART.md) |
| Keep my game folder up to date (I am a player) | [For players](PLAYERS.md) |
| Release my game, mod or asset pack with it (and sign it) | [Publishing a release](PUBLISHING.md) |
| Host the release on a VPS (Caddy, nginx, Apache) and deploy to it over SSH | [Setting up a VPS](VPS-SETUP.md) |
| Use it from scripts, a launcher or a server; the settings file and logs | [Command line reference](CLI.md) |
| Keep the files in my Docker image or Pterodactyl server up to date | [Docker and Pterodactyl](../docker/README.md) |
| Let an AI assistant (Claude Code, Claude Desktop, VS Code) use DeltaPatcher | [AI assistants (MCP)](../mcp/README.md) |
| Understand the files, or write my own client | [The store and manifest format](FORMAT.md) |
| Know what it protects, or report a security problem | [Security](../SECURITY.md) |
| Fix an error message | [Troubleshooting](TROUBLESHOOTING.md) |

## The idea in one paragraph

You **release** a folder: DeltaPatcher hashes every file (SHA-256) and writes a *store*, which is a
`manifest.json` listing every file plus a `blobs/` folder holding each distinct file once, named by
its hash. You put the store somewhere clients can read it: your own PC, a VPS, any web host or a
shared folder. Clients **update** by comparing their folder with the manifest and downloading only
the blobs they do not have. The file type does not matter: a texture or a `.dll` is just bytes with
a hash. A big archive also gets a small index of the files inside it, so only the ones that changed
are downloaded.

## The window

Click a picture to see it full size.

The first time, a welcome screen offers the two ways in: keeping a game up to date, or publishing your own
releases (**Getting started** on the Projects page opens it again). Every box folds with the arrow at the
right of its header (up: folded, down: open), and DeltaPatcher remembers which. Less used actions are in
**...** menus, and the (i) beside a label holds its whole explanation.

**Projects**: every game, mod or server DeltaPatcher keeps up to date or releases, with its state. Check
all, Update all, and add, share or remove projects. The picker at the top left chooses the project the
other pages show. A project shared by torrent has a **By torrent** mark and, while DeltaPatcher seeds it,
a line with its peers and upload speed; its **...** menu has **Seed this project** or **Stop seeding**.

[![The Projects page](images/app-projects.png)](https://github.com/2cwldys/deltapatcher/raw/main/docs/images/app-projects.png)

**Update**: bring the picked project's folder up to date from the address players use.

[![The Update page](images/app-update.png)](https://github.com/2cwldys/deltapatcher/raw/main/docs/images/app-update.png)

**Release**: what to release (and **Share by torrent too**, so players also download from each other),
where players download it from (your server over SSH, a folder, or this computer to test), one button
that sends only what changed, and the project's statistics under it.

[![The Release page](images/app-release.png)](https://github.com/2cwldys/deltapatcher/raw/main/docs/images/app-release.png)

**Settings**: every setting of `deltapatcher.ini` (among them **Torrents**: allow, or refuse BitTorrent for
every project), the project's backups, the log, and a test of BitTorrent under About.

[![The Settings page](images/app-settings.png)](https://github.com/2cwldys/deltapatcher/raw/main/docs/images/app-settings.png)

The log at the bottom shows exactly what each step did. **Stop** cancels a running download.

**Force stop**, the red box at the bottom left, ends everything at once: updates, checks, releases,
downloads, seeding and the local server, and it turns seeding off for every project until you turn it on
again. The pages stay greyed out, and nothing starts (no automatic check, no
update by itself) until you click **Lift the stop**, even after DeltaPatcher restarts. Lifting it starts
nothing that was stopped.
