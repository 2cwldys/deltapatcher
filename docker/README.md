# DeltaPatcher in Docker and Pterodactyl

DeltaPatcher is not something to run as its own container. It is a small tool to put *in yours*: a game
server, a mod pack, an asset server, anything whose files come from a release you publish. Each start (or
each build) it brings the files to the release's version, downloading only what changed:

- **Only what changed travels.** Files are compared by SHA-256; an unchanged file is never downloaded again.
- **What the container made itself stays.** Configs, saves and logs that the release does not name are never
  removed. Files you mark *Players keep their own copy* when you release (a server config, say) are written
  once and never overwritten.
- **An update that fails leaves the folder as it was.** Every update is journalled, and undone when it does
  not finish.
- **Any file type**: `.pk3`, `.zip`, maps, textures, programs.

Publish your releases as usual (the app's **Release** page, or `deltapatcher release`); the container only
needs the address players use, such as `https://game.example.com/`.

## Put the command line in your image

The command line is one program, `deltapatcher-cli`, from the project's releases. It needs glibc 2.35 or
later: Debian 12, Ubuntu 22.04 and newer, and the images built on them.

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
 && curl -fsSL https://github.com/2cwldys/deltapatcher/releases/latest/download/deltapatcher-cli-linux-x64.tar.gz \
    | tar -xz -C /usr/local/bin \
 && deltapatcher-cli --no-log version
```

`latest/download/` is always the newest release; use `download/v0.3.0/` instead to pin one version. Keep
`ca-certificates`: it is what https checks against.

**Alpine, ARM, or an older system**: install the Python package instead. Updating needs nothing but Python
3.8 or later (no other packages):

```dockerfile
RUN apk add --no-cache python3 py3-pip \
 && pip install --break-system-packages "deltapatcher @ git+https://github.com/2cwldys/deltapatcher@v0.3.0"
```

Then the command is `deltapatcher` instead of `deltapatcher-cli`.

## Update when the container starts

[`example/`](example/) is a whole example to copy: [`entrypoint.sh`](example/entrypoint.sh) updates the
folder and then starts your program, [`Dockerfile`](example/Dockerfile) installs both, and
[`compose.yaml`](example/compose.yaml) runs it with the files on a volume.

| Variable | |
| --- | --- |
| `DP_SOURCE` | Where the release is: `https://game.example.com/`. Empty: start without updating. |
| `DP_FOLDER` | The folder kept up to date (`/data`). Put it on a volume, so the next start downloads only what changed since. |
| `DP_TRUST_KEY` | Your publisher key (`ed25519:...`) when you sign releases: anything not signed with it is refused. |
| `DP_PROJECT_FILE` | Instead of the two above: a project file you exported (`.deltapatch` or `.html`), put in the container (a volume or the image). Its address and publisher key are used, its signature checked; hand the container a newer file when you move. |
| `DP_PROJECT_NAME` | With a project file that holds several projects: the one this container keeps. |
| `DP_REQUIRED` | `1`: do not start when the update fails. Otherwise the program starts with the files it already has. |

The update itself is one line, for your own entrypoint:

```sh
deltapatcher-cli --no-log update /data --source "$DP_SOURCE" --trust-key "$DP_TRUST_KEY"
```

## Or bake the files into the image

To ship an image that already holds a release, update while building:

```dockerfile
RUN deltapatcher-cli --no-log update /opt/game --source https://game.example.com/
```

Docker keeps that layer until the release changes; add `--build-arg CACHE_BUST=$(date +%s)` with an
`ARG CACHE_BUST` above the line to fetch a new release on purpose. Updating at start, with a volume, is
usually the better choice: the image stays small, and a new release needs no new image.

## Checks and exit codes

| Code | Meaning |
| --- | --- |
| 0 | Up to date (or brought up to date) |
| 1 | The update failed; the folder is as it was |
| 2 | `update --check` only: a newer release is waiting |

`deltapatcher-cli update /data --check` changes nothing, so it works as a health check or in a cron job
that restarts the container when a release is waiting.

## Pterodactyl

[`pterodactyl/egg-deltapatcher-example.json`](pterodactyl/egg-deltapatcher-example.json) is an example egg
to adapt (admin panel, **Nests**, **Import Egg**):

- **The installation script** fetches `deltapatcher-cli` into the server folder and installs the release's
  files once.
- **The startup command** updates the folder on every start, then starts the server: from the project
  file when *Project file* is set, else from the address.
- **Variables**: *Release address* (`DP_SOURCE`), *Publisher key* (`DP_TRUST_KEY`, optional), *Project file*
  (`DP_PROJECT_FILE`: a project file you uploaded to the server folder with the panel's file manager),
  *Project in the file* (`DP_PROJECT_NAME`, optional) and *Start command* (`SERVER_START`).

Change the docker image (it needs glibc 2.35 or later, as above), the start command and the *done* text to
fit your game. `--close` makes sure nothing waits for a key press.

## Seeding from a container

A container can be the always-on seed of a project shared by torrent:

```
deltapatcher-cli --no-log --close project import /data/game.deltapatch --folder /srv/game --yes --seed
deltapatcher-cli --no-log --close update --all
deltapatcher-cli --no-log --close seed --all
```

Publish the torrent port (`-p 6881:6881 -p 6881:6881/udp`) and list `<host>:6881` in the project's seeds.
`seed` runs until the container stops. The command-line programs from a release have BitTorrent built in;
installed with pip from the repository, use `pip install ".[torrent]"`.

`seed` reads a copy of the release kept for seeding, inside the project's folder (`/srv/game`, or the folder in it
that the project file names) under `.deltapatcher/seedstore`: it doubles what the volume holds of the game (not on
a filesystem that shares space between copies, such as Btrfs or XFS). To seed straight from the folder instead and
save that space, add this before `seed --all` (with the project's name, as `projects` lists it):

```
deltapatcher-cli --no-log --close --project NAME config set seed_copy=no
```

## Good to know

- **Settings and logs**: `--no-log` writes no log file. The settings file goes to `~/.config/deltapatcher/`;
  set `DELTAPATCHER_CONFIG=/tmp/deltapatcher.ini` when that is read-only.
- **Certificate errors over https** on an unusual base image: point `SSL_CERT_FILE` at its CA bundle (for
  example `/etc/ssl/certs/ca-certificates.crt`).
- **The folder's own records** live in `<folder>/.deltapatcher/` (the journal and the rollback copy): keep
  it on the same volume as the files.
- **Updating the command line itself**: `deltapatcher-cli self-update`, or a new image.
