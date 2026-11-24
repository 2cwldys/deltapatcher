# DeltaPatcher documentation

Pick the page for what you want to do:

| I want to... | Read |
| --- | --- |
| Try it on my own PC in five minutes | [Quick start](QUICKSTART.md) |
| Keep my game folder up to date (I am a player) | [For players](PLAYERS.md) |
| Release my game, mod or asset pack with it | [Publishing a release](PUBLISHING.md) |
| Host the release on a VPS and deploy to it over SSH | [Setting up a VPS](VPS-SETUP.md) |
| Use it from scripts, a launcher or a server | [Command line reference](CLI.md) |
| Understand the files, or write my own client | [The store and manifest format](FORMAT.md) |
| Fix an error message | [Troubleshooting](TROUBLESHOOTING.md) |

## The idea in one paragraph

You **publish** a folder: DeltaPatcher hashes every file (SHA-256) and writes a *store*, which is a
`manifest.json` listing every file plus a `blobs/` folder holding each distinct file once, named by
its hash. You put the store somewhere clients can read it: your own PC, a VPS, any web host or a
shared folder. Clients **update** by comparing their folder with the manifest and downloading only
the blobs they do not have. The file type does not matter: a `.pk3`, a `.zip`, a texture or a `.dll`
is just bytes with a hash.

## The window at a glance

| Update | Publish | Server |
| --- | --- | --- |
| ![Update tab](images/1-update.png) | ![Publish tab](images/2-publish.png) | ![Server tab](images/3-server.png) |

- **Update**: bring a folder up to date from a source address.
- **Publish**: turn a folder into a store, and serve it from this PC to test.
- **Server**: send a store to a VPS over SSH (SFTP) or to a folder.

The log at the bottom shows exactly what each step did. **Stop** cancels a running download.
