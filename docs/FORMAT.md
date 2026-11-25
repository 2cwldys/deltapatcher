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
  parts/                   big archives only: their parts indexes, each named by its own SHA-256
    <sha256 of the index>
  sigs/                    signed releases only
    <sha256 of manifest.json>.sig
  release.torrent          shared by torrent only: the release as a BitTorrent v2 torrent
  release.item             shared by torrent only: the signed pointer to that torrent
  .htaccess                for Apache (optional): serve the release, nothing else
```

A blob's name is the SHA-256 of its bytes, so a blob never changes once written. It can be cached
forever and checked by anyone; so can a parts index. A store shared by torrent also holds
`.deltapatcher-torrentcache.json` (what each blob hashes to in the torrent, so publishing again hashes only new
blobs) and, while it is seeded, `.deltapatcher/seeding/`; a store with big archives holds
`.deltapatcher-partscache.json` (which blobs have a parts index, so publishing again reads only new ones): none of
them is sent with the release.

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
  {"path": "data/pak0.pk3", "sha256": "3a7bd3e2...", "size": 104857600, "parts": "5d41402a..."},
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
| `files` | Every file: `path` (relative, `/` separators), `sha256`, `size` in bytes, and for a big archive `parts` |
| `parts` | Optional, in a file's entry: the SHA-256 of its parts index, `parts/<that hash>` ([Parts indexes](#parts-indexes-parts)). A client reads it only when it is 64 lowercase hex characters and passes it over otherwise; DeltaPatcher 0.2.0 and other older clients read only `path`, `sha256` and `size`, and update as before. It never makes two entries differ. |
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

A private project file, the publisher's own (to keep releasing a project from another computer), is version 4:
`"private": true` at the top, beside `"updates"`, and every project's entry has one more part, `"private"`, what
releasing it needs. It must be signed, with the signing key every private part names; an unsigned file, one
signed with another key, version 4 without `"private": true`, and a public file with a private part are refused.
The private part has four sections, each holding exactly these fields, each the setting of the same name (a field
missing or unknown is refused, never passed over):

| Section | Fields |
| --- | --- |
| `publish` | `name`, `version` (text), `protect`, `leave_out` (paths inside the folder released and patterns, separated by `;`: never a drive, a leading `/`, `\` or `~`, a `:` or a `..`), `sign`, `htaccess`, `share_updates`, `show_files` (true or false), `signing_public_key` (`ed25519:...`: the key the file is signed with) |
| `server` | `deploy_mode` (`sftp`, `folder` or `local`), `ssh_host` (a host name or address, or empty), `ssh_user` (a user name, or empty), `ssh_port` (1 to 65535), `remote_root` (a path on the server), `public_url` (an http(s) address, or empty), `access_log` (only with `sftp`: a path on the server), `host_keys` (at most 16 `{"type", "key"}`: a key type OpenSSH knows, and the key in base64, which names that type; only with `ssh_host`) |
| `torrent` | `torrent` (true or false; true needs a torrent id, and the entry's `"torrent"` then has the same id), `torrent_id` (32 hex characters, or empty), `torrent_name` (a torrent's name, or empty), `seeds` (`host:port`, separated by commas) |
| `backup` | `backup_keep_daily`, `backup_keep_weekly`, `backup_keep_monthly` (0 to 100000), `backup_max_size` (a size such as `10 GB`, or empty); never `backup_every_hours`: when backups are made is each computer's own |

A host name, a user name and a torrent id or name are taken whole: one with a line end after it is refused.
The rest of the entry is what a public file holds, and a player who adds the file as a player gets only that.

In every project file, public or private, a text is one line: a character `str.splitlines()` ends a line at (a
line feed or carriage return, `\x0b`, `\x0c`, `\x1c` to `\x1e`, `\x85`, U+2028, U+2029) is refused, as is any other
control character. A public file's texts (`description`, `install_hint`) and a release's details are read and
written with each line or paragraph separator (U+2028, U+2029) as a space, so an older file holding one still
reads; in a private file one is refused. A settings file is read and written line by line, a line ending at `\r\n`,
`\r` or `\n` and at nothing else.

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

## Parts indexes (parts/)

A release file of 8 MiB or more whose bytes are a zip (a `.pk3`, `.pk4`, `.zip`, `.jar` or any other: found by
its structure, never by its name) gets a parts index when it is published, and its manifest entry's `parts` names
it: `parts/<sha256 of the index>`. With it, an update rebuilds a changed archive byte for byte from the files
inside it the player already has, and fetches only the ones that changed. Nothing is ever recompressed: the archive
is described as the pieces it is made of, and put back together from the same bytes.

An index is zlib-compressed JSON, its keys sorted and no spaces, so the same file always gives the same index:

```json
{"file":"<sha256 of the file>","format":1,"kind":"zip","segments":[["l","<base64>"],["p","<sha256>",716800]],"size":104857600}
```

The segments, in order, cover every byte of the file exactly once; each starts where the one before ended:

| Segment | |
| --- | --- |
| `["p", "<sha256>", size]` | A piece: one member's compressed data, from the end of its own local header (30 bytes, its name and its local extra field, which can differ from the one in the central directory) for its compressed size; `<sha256>` is of those bytes. A piece under 1 KiB is a literal instead. |
| `["l", "<base64>"]` | A literal: every other byte, as it is (local headers, data descriptors, gaps, a stub before the first member, the central directory, the end records and the comment), neighbouring literals joined. |

A file gets no index when zipfile cannot read its central directory (it reads zip64 archives too), a member's
local header is not where the central directory says, two members overlap, or the index would not stay small:
its literals (its members under 1 KiB and every byte that is no member's) more than a quarter of the file or more
than 48 MiB, more than 1,000,000 segments, or more than 64 MiB. Publishing says which in its warning.

An index has at most 161,109,005 bytes of JSON (`JSON_MAX`, the most one can have: 1,000,000 segments as long as a
piece's can be, 94 characters with its comma, the base64 of 48 MiB of literals, and the rest of the object);
publishing and clients hold to the same number. A client checks every index before it uses one, as untrusted input:
its SHA-256 is the entry's `parts`; it is at most 64 MiB, and at most `JSON_MAX` bytes of JSON; before the JSON is
parsed, its text has the shape of an index (one object from its first character to its last, exactly one `{` and
one `}`, at most 1,000,001 `[` and as many `]`, at most 3,000,004 commas, no `\`: an index never needs an escape;
nothing between two quotes longer than the base64 of the most literal bytes the file may have, and at least 64
characters; no number longer than 20 characters), and it is parsed with no float, `NaN` or `Infinity`; `format` is
1, `kind` is `zip`, `file` and `size` are the entry's; it has at most 1,000,000 segments, they cover exactly `size`
bytes, and its literals come to at most a quarter of the file and 48 MiB, as publishing makes them. In path order,
one update keeps at most 256 MiB (of JSON) and 1,000,000 segments of indexes, and fetches at most 256 MiB of them.
The archive past any of it comes whole: its index was fetched (and stays kept, past the first two) or fetched in
part (past the third); every archive after it comes whole too, and its index is never fetched. An index that fails
any of it is not used, and that file is downloaded whole; one cut on its way is not refused, and is fetched again
next time. Older clients never read `parts`, and update from the same blobs as before.

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
- `<base>parts/<sha256>`, the parts index of a big archive that changed (404: none, and the archive comes whole)
- `<base>blobs/<sha256>` with `Range: bytes=A-B`, for the pieces of an archive being rebuilt: only a `206` with
  exactly those bytes (`Content-Range: bytes A-B/<size of the blob>`) is used. A server that ignores Range and
  answers `200` still works: that archive is downloaded whole. Every real web server answers Range requests.
- `<base>release.torrent` and `<base>release.item`, for a project shared by torrent

They send `User-Agent: DeltaPatcher/<version>`. No directory listing is needed or wanted. Recommended
headers: `Cache-Control: no-cache` on the manifests, `release.torrent` and `release.item`, and
`Cache-Control: public, max-age=31536000, immutable` on blobs, parts indexes and signatures (the `.htaccess`
sets exactly this on Apache: a parts index has a blob's kind of name).

## The client's own folder

Inside every folder it updates, the client keeps `.deltapatcher/`:

| Path | |
| --- | --- |
| `state.json` | `{"source", "manifest_sha", "version", "publisher_key", "files": {path: [size, sha256]}}`: the files this client installed, and the publisher key it trusts |
| `update.log` | Every update's record: when, from where, signed or not, every file added, replaced, removed or kept |
| `hashes.json` | Cache of the folder's hashes, keyed by path, valid while size and modification time match |
| `cache/<sha256>` | Downloaded blobs waiting to be installed. `<sha256>.part` is a download to resume. `<sha256>.rebuild` is an archive being rebuilt from what the folder has: it becomes `<sha256>` only once it is that SHA-256, and a stopped one is never resumed (the next update starts it again). |
| `parts/<sha256>` | The parts indexes the last check or update needed, so a check and the update after it fetch each one once; kept only while the drive still has the update's free space after it (otherwise used from memory, and the update after a check fetches it again); removed once no update needs them. `parts/<sha256>.refused`: an index that was refused, so neither a check nor an update fetches it again while a release names it |
| `members.json` | `{"format": 1, "files": {path: {"size", "mtime_ns", "members": [[offset, size, sha256], ...]}}}`: the files of 1 KiB or more an update looked at for the pieces of an archive (those DeltaPatcher installed here, and the archive it replaces, never one the player keeps as their own: protected, or kept as edited here), with the members of each zip-structured one (1 KiB or more): where its compressed data is, and its SHA-256 once worked out (`""` until then; a file that is no zip has none). A file is read again when its size or modification time changed |
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
   with the folder untouched; a blob resumed from a cut download (`cache/<sha256>.part`) that fails is first
   downloaded once more, whole. A needed blob whose entry names a parts index, from an http(s) address, can be
   rebuilt instead (an optional step: skipping it downloads it whole, as before):
   1. Fetch `parts/<parts>` and check it ([Parts indexes](#parts-indexes-parts)).
   2. Find each piece in what the folder has: the members of the old file at the same path first (even when
      `state.json` does not list it), then of the other zip-structured files `state.json` lists, by the SHA-256 of
      each one's compressed data. A path the player keeps as their own (a `protect` pattern's, or one no longer
      listed and edited here) is never opened.
   3. The pieces it lacks are ranges of the blob to fetch; neighbours less than 256 KiB apart are fetched as one,
      and a file takes at most 256 range requests (that distance doubles until it does). When the ranges come to
      more than 70% of the file, download it whole instead; when a cut whole download of it
      (`cache/<sha256>.part`) lacks no more than the ranges, resume that download instead.
   4. Write the literals and the pieces the folder has at their offsets (each piece hashed as it is copied: one
      that no longer matches, or cannot be read for any reason, is fetched too; at most 32 of the folder's files
      open at once), fetch the ranges (`Range: bytes=A-B`; anything but a `206` of exactly those bytes ends the
      rebuild), and hash the whole file.
   5. Use it only when it is the entry's SHA-256 and size; anything that did not work out means the whole
      download of that blob.
5. Apply. Before each change, write the journal line, then move the old file into `rollback.new/files/`.
   Write new files beside their target and rename them into place.
6. Write `state.json`, then `{"done": true}`, then rename `rollback.new/` to `rollback/`.
7. On any failure in step 5 or 6, undo the journal newest-first.
