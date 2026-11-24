"""Where a client reads a release from: an http(s) URL or a folder (the source of truth).

http(s)://host/path/   manifest at <base>manifest.json, blobs at <base>blobs/<sha256>
file:///C:/store/      or a plain folder path: the same layout on disk
"""

import http.client
import os
import re
import shutil
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from . import USER_AGENT
from .manifest import BLOB_DIR, MANIFEST_NAME, check_sha
from .signing import SIG_DIR

DEFAULT_URL = "http://localhost:8000/"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")
# request headers that may follow a redirect to another host
FOLLOW_ANYWHERE = ("user-agent", "range")


class SourceError(Exception):
    pass


class _ShortRead(Exception):
    """The connection ended before the blob did: retried, resuming from what arrived."""


def is_http(url):
    return url.lower().startswith(("http://", "https://"))


def insecure_remote(url):
    """True for plain http to a host that is not this machine: the manifest travels unprotected.
    (Every blob is still checked against the manifest's SHA-256; the manifest itself is not.)"""
    if not url.lower().startswith("http://"):
        return False
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host not in LOCAL_HOSTS


def normalise(url):
    url = (url or "").strip()
    if not url:
        raise SourceError("no source address")
    if is_http(url):
        return url if url.endswith("/") else url + "/"
    if url.lower().startswith("file:"):
        parts = urllib.parse.urlsplit(url)
        p = urllib.request.url2pathname(parts.path)
        if parts.netloc and parts.netloc.lower() != "localhost" and os.name == "nt":
            p = "\\\\" + parts.netloc + p        # file://server/share/store -> \\server\share\store
        return os.path.abspath(p)
    return os.path.abspath(os.path.expanduser(url))


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but never from https to http, and send extra headers (an Authorization
    token, say) only to the host they were given for."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = urllib.request.HTTPRedirectHandler.redirect_request(self, req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        a, b = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if a.scheme == "https" and b.scheme != "https":
            raise urllib.error.HTTPError(newurl, code, "refused a redirect from https to http", headers, fp)
        if (a.hostname, a.port) != (b.hostname, b.port):
            for k in list(new.headers):
                if k.lower() not in FOLLOW_ANYWHERE:
                    del new.headers[k]
        return new


def _connect_loopback(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    """Connect to "localhost" on 127.0.0.1 first, then ::1. The system's order is usually ::1 first,
    `deltapatcher serve` listens on 127.0.0.1, and Windows waits about two seconds before it gives up
    on a refused address: every request to the default source paid that wait."""
    last = None
    for ip in ("127.0.0.1", "::1"):
        try:
            return socket.create_connection((ip, address[1]), timeout, source_address)
        except OSError as e:
            last = e
    raise last


class _LoopbackFirst(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        http.client.HTTPConnection.__init__(self, *args, **kwargs)
        if (self.host or "").lower() == "localhost":
            self._create_connection = _connect_loopback


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_LoopbackFirst, req)


# a release's version as it goes in a User-Agent: letters, digits and . _ + - only, at most 40 of them
_TOKEN_OUT = re.compile(r"[^A-Za-z0-9._+-]+")


def user_agent(release=""):
    """The User-Agent an updater sends: "DeltaPatcher/<version>", then "release/<version>" of the release the
    folder has, when it has one. That is all it says (no name, no folder, no id): publishers count from it
    which releases players are on. Characters a header cannot carry become _."""
    token = _TOKEN_OUT.sub("_", str(release or "")).strip("_")[:40]
    return "%s release/%s" % (USER_AGENT, token) if token else USER_AGENT


class Source(object):
    def __init__(self, url, headers=None, timeout=30, tries=4):
        self.base = normalise(url)
        self.http = is_http(self.base)
        self.headers = {"User-Agent": USER_AGENT}
        self.own_agent = any(str(k).lower() == "user-agent" for k in (headers or {}))
        self.headers.update(headers or {})
        self.timeout = timeout
        self.tries = tries
        self.opener = urllib.request.build_opener(_Redirects(), _HTTPHandler())

    def __str__(self):
        return self.base

    def for_release(self, version):
        """From now on the User-Agent names the release the folder has (user_agent()), unless the caller gave
        a User-Agent of its own."""
        if not self.own_agent:
            self.headers["User-Agent"] = user_agent(version)

    def _open(self, rel, start=0):
        req = urllib.request.Request(self.base + rel, headers=dict(self.headers))
        if start:
            req.add_header("Range", "bytes=%d-" % start)
        return self.opener.open(req, timeout=self.timeout)

    def get_manifest(self):
        """The manifest's bytes."""
        if not self.http:
            path = os.path.join(self.base, MANIFEST_NAME)
            try:
                with open(path, "rb") as f:
                    return f.read()
            except OSError as e:
                raise SourceError("cannot read %s: %s" % (path, e))
        last = None
        for attempt in range(self.tries):
            try:
                with self._open(MANIFEST_NAME) as r:
                    return r.read(64 << 20)
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 404):
                    raise SourceError("%s%s answered %d %s" % (self.base, MANIFEST_NAME, e.code, e.reason))
                last = e
            except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
                last = e
            time.sleep(min(8, 1 << attempt))
        raise SourceError("cannot reach %s: %s" % (self.base, getattr(last, "reason", last)))

    def get_signature(self, manifest_sha):
        """The bytes of sigs/<manifest sha256>.sig, or None when the release has no signature."""
        check_sha(manifest_sha)
        name = manifest_sha + ".sig"
        if not self.http:
            path = os.path.join(self.base, SIG_DIR, name)
            if not os.path.isfile(path):
                return None
            try:
                with open(path, "rb") as f:
                    return f.read(1 << 16)
            except OSError as e:
                raise SourceError("cannot read %s: %s" % (path, e))
        last = None
        for attempt in range(self.tries):
            try:
                with self._open(SIG_DIR + "/" + name) as r:
                    return r.read(1 << 16)
            except urllib.error.HTTPError as e:
                if e.code in (404, 410):
                    return None
                if e.code in (401, 403):
                    raise SourceError("%s%s/%s answered %d %s" % (self.base, SIG_DIR, name, e.code, e.reason))
                last = e
            except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
                last = e
            time.sleep(min(8, 1 << attempt))
        raise SourceError("cannot read the release's signature from %s: %s" % (self.base, getattr(last, "reason", last)))

    def fetch_blob(self, sha, dest, size, progress=None, cancel=None):
        """Put blob `sha` at `dest`, resuming from dest + '.part'. The caller checks the hash."""
        check_sha(sha)
        part = dest + ".part"
        progress = progress or (lambda n: None)
        if not self.http:
            if cancel is not None and cancel.is_set():
                raise SourceError("stopped")
            src = os.path.join(self.base, BLOB_DIR, sha)
            try:
                shutil.copyfile(src, part)
            except OSError as e:
                raise SourceError("cannot read %s: %s" % (src, e))
            progress(size)
            os.replace(part, dest)
            return
        last = None
        reported = 0   # bytes of this blob already passed to progress(), so a retry never counts twice
        for attempt in range(self.tries + 2):
            have = os.path.getsize(part) if os.path.exists(part) else 0
            if have > size:
                os.remove(part)
                have = 0
            if have == size and size > 0:
                progress(size - reported)
                os.replace(part, dest)
                return
            try:
                with self._open(BLOB_DIR + "/" + sha, have) as r:
                    resumed = bool(have) and r.status == 206
                    got = have if resumed else 0
                    progress(got - reported)
                    reported = got
                    with open(part, "ab" if resumed else "wb") as f:
                        while True:
                            if cancel is not None and cancel.is_set():
                                raise SourceError("stopped")
                            b = r.read(1 << 16)
                            if not b:
                                break
                            f.write(b)
                            got += len(b)
                            reported += len(b)
                            progress(len(b))
                            if got > size:
                                raise SourceError("blob %s is longer than the manifest says" % sha[:12])
                if got < size:
                    raise _ShortRead("the connection closed after %d of %d bytes" % (got, size))
                os.replace(part, dest)
                return
            except _ShortRead as e:
                last = e                 # .part keeps what arrived; the next try resumes from it
            except urllib.error.HTTPError as e:
                if e.code == 416 and have:
                    os.remove(part)
                    continue
                if e.code in (401, 403, 404, 410):
                    raise SourceError("%s%s/%s answered %d %s" % (self.base, BLOB_DIR, sha, e.code, e.reason))
                last = e
            except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
                last = e
            time.sleep(min(8, 1 << min(attempt, 3)))
        raise SourceError("download of %s failed: %s" % (sha[:12], getattr(last, "reason", last)))
