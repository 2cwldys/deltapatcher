"""SHA-256 of files, and a cache that skips files whose size and modification time did not move."""

import hashlib
import json
import os

CHUNK = 1 << 20


def sha256_file(path):
    """(sha256 hex, size) of a file, read in 1 MB pieces."""
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
            size += len(b)
    return h.hexdigest(), size


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def copy_hashing(src, dst):
    """Copy src to dst and return (sha256 hex, size) of what was copied, from the same read."""
    h = hashlib.sha256()
    size = 0
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while True:
            b = fi.read(CHUNK)
            if not b:
                break
            h.update(b)
            fo.write(b)
            size += len(b)
        fo.flush()
        os.fsync(fo.fileno())
    return h.hexdigest(), size


class HashCache(object):
    """{key: [size, mtime_ns, sha256]}, saved as JSON. A hit needs the same size AND mtime."""

    def __init__(self, path):
        self.path = path
        self.data = {}
        self.dirty = False
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self.data = {k: v for k, v in raw.items() if isinstance(v, list) and len(v) == 3}
        except (OSError, ValueError):
            pass

    def get(self, key, st):
        v = self.data.get(key)
        if v and v[0] == st.st_size and v[1] == st.st_mtime_ns:
            return v[2]
        return None

    def put(self, key, st, sha):
        self.data[key] = [st.st_size, st.st_mtime_ns, sha]
        self.dirty = True

    def hash(self, key, path):
        """The file's SHA-256, from the cache when it still holds."""
        st = os.stat(path)
        sha = self.get(key, st)
        if sha is None:
            sha, _ = sha256_file(path)
            st2 = os.stat(path)
            if (st2.st_size, st2.st_mtime_ns) == (st.st_size, st.st_mtime_ns):
                self.put(key, st, sha)
        return sha

    def save(self):
        if not self.dirty:
            return
        tmp = self.path + ".tmp"
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, separators=(",", ":"))
        os.replace(tmp, self.path)
        self.dirty = False
