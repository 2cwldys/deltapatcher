"""The manifest: what a release holds, and the rules every path in it must follow.

A manifest is JSON:

    {
      "format": 1,
      "name": "My Game",
      "version": "1.4",
      "published": "2026-11-23T20:00:00Z",
      "count": 2,
      "bytes": 1234,
      "protect": ["config/*.cfg"],
      "files": [
        {"path": "data/pak0.pk3", "sha256": "<64 hex>", "size": 1000},
        {"path": "readme.txt",    "sha256": "<64 hex>", "size": 234}
      ]
    }

Blobs live beside it as blobs/<sha256>. A client treats every manifest as untrusted input: a path
that could leave the install folder, or name a device on Windows, is refused before anything is
written.
"""

import fnmatch
import json
import re
import time

FORMAT = 1
MANIFEST_NAME = "manifest.json"
PREVIOUS_NAME = "manifest.previous.json"
BLOB_DIR = "blobs"
HEX64 = re.compile(r"^[0-9a-f]{64}$")

_WINDOWS_DEVICES = {"con", "prn", "aux", "nul", "conin$", "conout$"}
# COM1-9 and LPT1-9, and the superscript digits Windows also treats as ports (COM1 written with U+00B9, ...)
for _d in "123456789\u00b9\u00b2\u00b3":
    _WINDOWS_DEVICES.update(("com" + _d, "lpt" + _d))
_BAD_CHARS = set('<>:"\\|?*')
STATE_DIR_NAMES = re.compile(r"^(\.deltapatcher|deltap~\d+)$", re.I)   # the client's own folder, and its 8.3 name


class ManifestError(ValueError):
    """A manifest, or a path in one, that must not be used."""


def check_path(path):
    """Raise ManifestError unless `path` is a safe relative path with forward slashes.

    Refused: empty, absolute, a drive letter, backslashes, '.' or '..' parts, empty parts,
    characters Windows cannot store, control characters, a part ending in a dot or a space
    (Windows silently strips those, so two names could land on one file), device names
    such as CON or LPT1 with any extension, and the client's own .deltapatcher folder.
    """
    if not isinstance(path, str) or not path:
        raise ManifestError("an empty path")
    if len(path) > 1024:
        raise ManifestError("a path longer than 1024 characters: %r" % path[:80])
    if path.startswith("/") or path.startswith("~"):
        raise ManifestError("an absolute path: %r" % path)
    for ch in path:
        if ord(ch) < 32 or ord(ch) == 127:
            raise ManifestError("a control character in %r" % path)
        if ch in _BAD_CHARS:
            raise ManifestError("the character %r in %r" % (ch, path))
    parts = path.split("/")
    if STATE_DIR_NAMES.match(parts[0]):
        raise ManifestError("a path inside the client's own .deltapatcher folder: %r" % path)
    for part in parts:
        if part in ("", ".", ".."):
            raise ManifestError("an empty, '.' or '..' part in %r" % path)
        if part.endswith(".") or part.endswith(" "):
            raise ManifestError("a part ending in a dot or a space in %r" % path)
        # Windows ignores spaces before the extension: "con .txt" is the console too
        if part.split(".")[0].rstrip(" ").lower() in _WINDOWS_DEVICES:
            raise ManifestError("a Windows device name in %r" % path)
    return path


def check_sha(sha):
    if not isinstance(sha, str) or not HEX64.match(sha):
        raise ManifestError("not a lowercase SHA-256: %r" % (sha,))
    return sha


class Entry(object):
    __slots__ = ("path", "sha256", "size")

    def __init__(self, path, sha256, size):
        self.path = check_path(path)
        self.sha256 = check_sha(sha256)
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ManifestError("a bad size for %r: %r" % (path, size))
        self.size = size

    def to_json(self):
        return {"path": self.path, "sha256": self.sha256, "size": self.size}

    def __eq__(self, other):
        return isinstance(other, Entry) and (self.path, self.sha256, self.size) == (other.path, other.sha256, other.size)

    def __repr__(self):
        return "Entry(%r, %s..., %d)" % (self.path, self.sha256[:12], self.size)


class Manifest(object):
    def __init__(self, name="", version="", published=None, files=None, protect=None):
        self.name = str(name or "")
        self.version = str(version or "")
        self.published = published or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.files = {}
        self.protect = [str(p) for p in (protect or []) if str(p).strip()]
        lowered = {}
        for e in files or []:
            key = e.path.lower()
            if key in lowered:
                raise ManifestError("two paths that are one file on Windows: %r and %r" % (lowered[key], e.path))
            lowered[key] = e.path
            self.files[e.path] = e
        # a file and a folder of the same name cannot both exist
        folders = set()
        for p in lowered:
            parts = p.split("/")
            for i in range(1, len(parts)):
                folders.add("/".join(parts[:i]))
        clash = folders.intersection(lowered)
        if clash:
            raise ManifestError("a path that is both a file and a folder: %r" % sorted(clash)[0])

    @property
    def bytes(self):
        return sum(e.size for e in self.files.values())

    def blobs(self):
        """{sha256: size} of every distinct file."""
        return {e.sha256: e.size for e in self.files.values()}

    def is_protected(self, path):
        return any(fnmatch.fnmatchcase(path, pat) for pat in self.protect)

    def to_json(self):
        doc = {
            "format": FORMAT,
            "name": self.name,
            "version": self.version,
            "published": self.published,
            "count": len(self.files),
            "bytes": self.bytes,
            "protect": list(self.protect),
            "files": [self.files[p].to_json() for p in sorted(self.files)],
        }
        return (json.dumps(doc, indent=1, sort_keys=False) + "\n").encode("utf-8")

    @classmethod
    def from_json(cls, data):
        try:
            doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        except (ValueError, UnicodeDecodeError) as e:
            raise ManifestError("not a manifest (%s)" % e)
        if not isinstance(doc, dict):
            raise ManifestError("not a manifest (no JSON object)")
        if doc.get("format") != FORMAT:
            raise ManifestError("manifest format %r; this program reads format %d" % (doc.get("format"), FORMAT))
        raw = doc.get("files")
        if not isinstance(raw, list):
            raise ManifestError("a manifest with no file list")
        files = []
        for item in raw:
            if not isinstance(item, dict):
                raise ManifestError("a file entry that is not an object")
            files.append(Entry(item.get("path"), item.get("sha256"), item.get("size")))
        protect = doc.get("protect") or []
        if not isinstance(protect, list) or not all(isinstance(p, str) for p in protect):
            raise ManifestError("'protect' must be a list of patterns")
        return cls(doc.get("name", ""), doc.get("version", ""), doc.get("published"), files, protect)
