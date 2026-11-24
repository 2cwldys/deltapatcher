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
  .htaccess                for Apache (optional): serve the release, nothing else
```

A blob's name is the SHA-256 of its bytes, so a blob never changes once written. It can be cached
forever and checked by anyone.

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
 "protect": ["config/*.cfg"],
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
| `protect` | Glob patterns (`fnmatch`, case-sensitive, against the whole path): files a client creates once and never overwrites |
| `files` | Every file: `path` (relative, `/` separators), `sha256`, `size` in bytes |

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

## Serving

Clients make plain `GET` requests:
- `<base>manifest.json`
- `<base>sigs/<sha256 of manifest.json>.sig` (404 when the release is not signed)
- `<base>blobs/<sha256>`, with `Range: bytes=N-` to resume (a server without Range support still works;
  the download just restarts)

They send `User-Agent: DeltaPatcher/<version>`. No directory listing is needed or wanted. Recommended
headers: `Cache-Control: no-cache` on the manifests, and `Cache-Control: public, max-age=31536000,
immutable` on blobs and signatures (the `.htaccess` sets exactly this on Apache).

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
