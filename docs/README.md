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
| Understand the files, or write my own client | [The store and manifest format](FORMAT.md) |
| Know what it protects, or report a security problem | [Security](../SECURITY.md) |
| Fix an error message | [Troubleshooting](TROUBLESHOOTING.md) |

## The idea in one paragraph

You **release** a folder: DeltaPatcher hashes every file (SHA-256) and writes a *store*, which is a
`manifest.json` listing every file plus a `blobs/` folder holding each distinct file once, named by
its hash. You put the store somewhere clients can read it: your own PC, a VPS, any web host or a
shared folder. Clients **update** by comparing their folder with the manifest and downloading only
the blobs they do not have. The file type does not matter: a `.pk3`, a `.zip`, a texture or a `.dll`
is just bytes with a hash.

## The window at a glance

| Update | Release | Settings |
| --- | --- | --- |
| ![Update](images/app-update.png) | ![Release](images/app-release.png) | ![Settings](images/app-settings.png) |

- **Update**: bring a folder up to date from the address players use.
- **Release**: what to release, where players download it from (your server over SSH, a folder, or this
  computer to test), and one button that sends only what changed.
- **Settings**: every setting of `deltapatcher.ini`, and the log.

The log at the bottom shows exactly what each step did. **Stop** cancels a running download.
