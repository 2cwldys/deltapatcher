# The store and manifest format

Everything here is plain files, so a store can be served by any web server and read by a client
written in any language.

## A store

```
store/
  manifest.json            the current release
  manifest.previous.json   the release before it (optional)
  blobs/
    3a7bd3e2360a3d29eea436fcfb7e44c735d117c42d1c1835420b6b9942dd4f1b
    ...                    one file per distinct content, named by its SHA-256 (64 lowercase hex characters)
  sigs/                    signed releases only
    <sha256 of manifest.json>.sig
  release.torrent          shared by torrent only: the release as a BitTorrent v2 torrent
  release.item             shared by torrent only: the signed pointer to that torrent
  .htaccess                for Apache (optional): serve the release, nothing else
```

A blob's name is the SHA-256 of its bytes, so a blob never changes once written. It can be cached
forever and checked by anyone. A store shared by torrent also holds `.deltapatcher-torrentcache.json` (what
each blob hashes to in the torrent, so publishing again hashes only new blobs) and, while it is seeded,
`.deltapatcher/seeding/`: neither is sent with the release.

## Signatures (sigs/)

A signed release has a signature file named by the SHA-256 of the exact bytes of its `manifest.json`:

```json
{
 "format": 1,
 "alg": "ed25519",
 "key": "ed25519:<base64 of the 32-byte public key>",
 "manifest_sha256": "<64 hex characters>",
 "signature": "<base64 of the 64-byte Ed25519 signature of manifest.json's bytes>"
}
```

Because the name carries the manifest's hash, a client always fetches the signature of the manifest
it actually received, even while a deploy is replacing it. A client:
1. fetches `manifest.json`, hashes its bytes, fetches `sigs/<that hash>.sig` (a 404 means unsigned);
2. checks `manifest_sha256`, then the Ed25519 signature over the manifest's bytes with `key`;
3. compares `key` with the publisher key it trusts (given in advance, or pinned from the first signed
   release) and refuses a mismatch, or an unsigned release once it trusts a key.

A publisher key is written `ed25519:` followed by the base64 of its 32 bytes.

## manifest.json

```json
{
 "format": 1,
 "name": "My Game",
 "version": "1.4",
 "published": "2026-11-23T20:00:00Z",
 "count": 2,
 "bytes": 1234,
 "protect": ["/config/user.cfg", "*.ini"],
 "files": [
  {"path": "data/pak0.pk3", "sha256": "3a7bd3e2...", "size": 1000},
  {"path": "readme.txt", "sha256": "9f86d081...", "size": 234}
 ]
}
```

| Field | |
| --- | --- |
| `format` | Always `1`. A client refuses any other value. |
| `name`, `version` | Free text, for people |
| `published` | UTC time of publishing (ISO 8601) |
| `count`, `bytes` | Number of files and their total size |
| `protect` | Rules for files a client creates once and never overwrites. `/config/user.cfg` is that file and `/saves/` that folder and everything in it (a leading `/`: an exact path from the top). Anything else is a pattern: without a `/` it matches a name in any folder (`*.ini`), with one the whole path (`config/*.cfg`); a trailing `/` means folders. Case is ignored. |
| `files` | Every file: `path` (relative, `/` separators), `sha256`, `size` in bytes |
| `project` | Optional: what the release says about its project (below). Older clients do not read it. |

### The project object

```json
"project": {
 "title": "My Game", "description": "...", "website": "https://game.example.com/",
 "install_hint": "Pick the game's folder", "install_folder": "Mods",
 "source_url": "https://game.example.com/", "trust_key": "ed25519:...",
 "show_files": true, "notes": "Map b replaces map a."
}
```

With the publisher's *share_updates* on, it holds the project's details as a project file gives them;
with it off (or no address players update from) only `show_files` and `notes`. It is part of the manifest,
so it is signed with it. `show_files` says whether DeltaPatcher shows players the names of the files a
release adds, changes and removes; `notes` are the release notes (plain text, at most 4000 characters).
A project shared by torrent also carries `"torrent": {"id", "name", "seeds"}`, as its project file does;
players who follow the publisher are asked about a change to it, as about a new address.
Readers pass over fields they do not know.

### Project files

A project file (`.deltapatch`, or the `.html` page that carries the same JSON) is version 1, or version 2
when it is update capable (`"updates": true`: players are asked when the publisher changes the project
later), or version 3 when a project in it is shared by torrent: that project's entry has `"torrent":
{"id": "<32 hex>", "name": "...", "seeds": ["host:port", ...]}`, must name its publisher key, and may
have no address (it updates by torrent only). A file is written with the lowest version its contents need,
and a DeltaPatcher asks to be updated for a version newer than it reads.

### Path rules

A client must refuse the whole manifest if any path:
- is empty, absolute (`/x`, `~x`), or has a drive letter or `:`
- has a backslash, `< > " | ? *` or a control character
- has an empty, `.` or `..` part
- has a part that ends in a dot or a space
- has a part that is a Windows device name before its first dot, ignoring spaces: `CON`, `PRN`, `AUX`,
  `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9` (also with the superscript digits 1-3), `CONIN$`, `CONOUT$`
- starts with the client's own folder `.deltapatcher` (or its 8.3 name `DELTAP~1`)
- is the same as another path ignoring case, or names a file that another path uses as a folder

## Torrents (release.torrent, release.item)

A release shared by torrent (its publisher's `[torrent] torrent = yes`, which needs signed releases) has two
more files beside its manifest, written before the manifest goes in place and sent before it:

- `release.torrent`: the store's own files as a BitTorrent v2 torrent (BEP 52): `manifest.json`,
  `sigs/<sha256 of manifest.json>.sig` and `blobs/<sha256>` for every blob the manifest names, under the
  torrent's name. It names no tracker, and a web seed (the address's parent folder) only when the address
  ends in the torrent's name.
- `release.item`: the signed pointer to that torrent as the DHT keeps it (a BEP 44 mutable item), bencoded:

| Key | |
| --- | --- |
| `k` | The publisher key's 32 bytes: the key the release is signed with |
| `salt` | The project's torrent id: 32 hexadecimal characters, made once and never changed |
| `seq` | The release's publish time in whole seconds, or the number before it + 1 when that is not larger |
| `v` | `{"ih": the torrent's v2 infohash (32 bytes), "m": the SHA-256 of manifest.json, hex}` |
| `sig` | The Ed25519 signature of `4:salt<n>:<salt>3:seqi<seq>e1:v<v bencoded>` |

A client takes nothing from a peer on trust. In order: the item's signature against the publisher key it
trusts, and its number against the highest it accepted before (a lower one is refused, from anyone); the
torrent's infohash against the item's `ih`; the manifest's SHA-256 against `m`, and its signature as in
[Signatures](#signatures-sigs); then every blob against its SHA-256, as over HTTP. Peers can only disagree
about bytes, never about names: every file of the torrent is named by its SHA-256, or is the signed manifest
and its signature. A project with an address keeps its address as the authority; the item matters to a
project with no address, which finds it in the DHT by the publisher key and the torrent id.

A player's seed run reads a copy of the release kept for seeding (`seed_copy = yes`, the default), laid out like
the store above and filled only from files of the folder whose SHA-256 it checked; the publisher's own store is
seeded as it is, never copied. The copy is the folder's `.deltapatcher/seedstore/<torrent id>/`
([The client's own folder](#the-clients-own-folder)), or with `seed_copies` set, one folder per project there:

| Path in `seed_copies` | |
| --- | --- |
| `<torrent id>-<8 hex>/` | One project's copy: the first 8 hexadecimal characters are of the SHA-256 of the full path of the folder it updates, in the system's letter case, so two folders of one release keep a copy each |
| `<torrent id>-<8 hex>/seedstore.json` | Its marker: `{"deltapatcher": "seed copy", "torrent_id", "folder", "project"}` (the folder and the project's settings file) |
| `<torrent id>-<8 hex>/manifest.json`, `sigs/`, `release.torrent`, `release.item`, `blobs/`, `.deltapatcher/hashes.json` | The release seeded now and the copy's hash cache, as in `seedstore/` |

`seed-copies.json` beside the main settings file lists every copy this DeltaPatcher made (`{path: {"torrent_id",
"folder", "project"}}`). A clean-up puts back on it a marked copy at a project's own place (in its folder, or in
`seed_copies`) whose marker names this DeltaPatcher's settings file of that project and its torrent id; a list that
cannot be read is never written over, and the next clean-up keeps it as `seed-copies.json.bad` and makes it again
that way; an entry whose copy's drive or folder is away stays until it is back. DeltaPatcher deletes a copy once
its project stops seeding from it (a seed run, once two reads of the settings at least 3 seconds apart agree), unless
another project needs it (two projects of one torrent and one folder share a copy) or a settings file cannot be
read whole; only a copy on that list, at either place (one that is not is another settings file's), and only a
folder whose marker says `"seed copy"`. A folder without the marker, or one that is or holds a link (a symbolic
link, or a junction on Windows; any other reparse point, such as a cloud placeholder, is an ordinary folder), is
never written into or deleted, whatever it is called.

## Serving

Clients make plain `GET` requests:
- `<base>manifest.json`
- `<base>sigs/<sha256 of manifest.json>.sig` (404 when the release is not signed)
- `<base>blobs/<sha256>`, with `Range: bytes=N-` to resume (a server without Range support still works;
  the download just restarts)
- `<base>release.torrent` and `<base>release.item`, for a project shared by torrent

They send `User-Agent: DeltaPatcher/<version>`. No directory listing is needed or wanted. Recommended
headers: `Cache-Control: no-cache` on the manifests, `release.torrent` and `release.item`, and
`Cache-Control: public, max-age=31536000, immutable` on blobs and signatures (the `.htaccess` sets exactly
this on Apache).

## The client's own folder

Inside every folder it updates, the client keeps `.deltapatcher/`:

| Path | |
| --- | --- |
| `state.json` | `{"source", "manifest_sha", "version", "publisher_key", "files": {path: [size, sha256]}}`: the files this client installed, and the publisher key it trusts |
| `update.log` | Every update's record: when, from where, signed or not, every file added, replaced, removed or kept |
| `hashes.json` | Cache of the folder's hashes, keyed by path, valid while size and modification time match |
| `cache/<sha256>` | Downloaded blobs waiting to be installed. `<sha256>.part` is a download to resume. |
| `rollback/` | The last completed update: `journal.jsonl`, `files/` (the replaced and removed files), `state.prev.json` |
| `rollback.new/` | The update in progress |
| `lock` | Held with an operating-system lock while an update or rollback runs |
| `release/` | A project shared by torrent: the release the folder has (`manifest.json`, `sigs/<sha256>.sig`, `release.torrent`, `release.item`), kept so it can be seeded |
| `torrent.json` | A project shared by torrent: `{"<torrent id>": {"seq", "peers", "saved"}}`, the highest release item number accepted and the peers met |
| `torrent/<pid>.<hex>/` | While a project with no address looks for its release: that run's own work folder (the manifest, signature and blobs from peers), removed when it ends |
| `seeding/` | While a seed run seeds the folder: `<pid>.seeding`; an update's request `<pid>.pause` (`{"by", "at", "lock"}`, with the `<pid>.<random>.asking` file the update holds locked while it asks) and the seed run's answer `<pid>.paused` (`{"pid", "at"}`: it lets go of the folder's files before they change; `{"pid", "at", "copy": true}`: it seeds from a copy, which the update never touches, and goes on); `next/`, a newer release it seeds before the folder has it |
| `seedstore/<torrent id>/` | A project set to seed from a copy (`seed_copy = yes`, the default) while `seed_copies` is empty: the copy of the release its seed runs read instead of the folder, one for each torrent, laid out like a store (`manifest.json`, `sigs/<sha256>.sig`, `release.torrent`, `release.item`, `blobs/<sha256>`, each blob written as `blobs/<sha256>.<pid>.part` until its SHA-256 is checked), with its marker `seedstore.json` (`{"deltapatcher": "seed copy", "torrent_id", "folder", "project"}`) and its own `.deltapatcher/hashes.json`. Deleted when the project stops seeding from it |

`journal.jsonl` has one JSON object per line: a header `{"started", "had_state", "source",
"version"}`, then one `{"op": "add"|"replace"|"remove", "path"}` before each change, and
`{"done": true}` at the end.

## The update, step by step

For anyone writing their own client:

1. Read the manifest and check every path (above). Check its signature as in [Signatures](#signatures-sigs).
2. For each listed file: missing here, add it. Different hash, replace it, unless it matches a
   `protect` pattern. Same hash, leave it.
3. For each file in `state.json` that the manifest no longer lists: remove it only if its hash still
   matches the recorded one. Skip it if the manifest lists the same file with different letter case.
4. Download every needed blob into the cache and check its SHA-256 and size. Stop on any mismatch,
   with the folder untouched.
5. Apply. Before each change, write the journal line, then move the old file into `rollback.new/files/`.
   Write new files beside their target and rename them into place.
6. Write `state.json`, then `{"done": true}`, then rename `rollback.new/` to `rollback/`.
7. On any failure in step 5 or 6, undo the journal newest-first.
