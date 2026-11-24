"""A small web server for a store: manifest.json and blobs/<sha256>, with resumable downloads.

Meant for testing on this machine (the default address is 127.0.0.1:8000) or a LAN. For the
internet, serve the store with a real web server (Caddy, nginx, IIS) over HTTPS: the files are
plain static files.
"""

import http.server
import os
import re
import socketserver
import threading

from .manifest import BLOB_DIR, HEX64, MANIFEST_NAME, PREVIOUS_NAME

RANGE_RE = re.compile(r"^bytes=(\d+)-(\d*)$")


def make_handler(root, quiet=True):
    root = os.path.abspath(root)
    allowed_top = {MANIFEST_NAME, PREVIOUS_NAME}

    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "DeltaPatcher"
        sys_version = ""

        def log_message(self, fmt, *args):
            if not quiet:
                http.server.BaseHTTPRequestHandler.log_message(self, fmt, *args)

        def _resolve(self):
            path = self.path.split("?", 1)[0].split("#", 1)[0]
            parts = [p for p in path.split("/") if p]
            if len(parts) == 1 and parts[0] in allowed_top:
                return os.path.join(root, parts[0])
            if len(parts) == 2 and parts[0] == BLOB_DIR and HEX64.match(parts[1]):
                return os.path.join(root, BLOB_DIR, parts[1])
            return None   # nothing else is served: no listings, no other files

        def do_HEAD(self):
            self._send(head=True)

        def do_GET(self):
            self._send(head=False)

        def _send(self, head):
            full = self._resolve()
            if not full or not os.path.isfile(full):
                self.send_error(404)
                return
            size = os.path.getsize(full)
            start, end = 0, size - 1
            status = 200
            rng = self.headers.get("Range")
            if rng:
                m = RANGE_RE.match(rng.strip())
                if not m or int(m.group(1)) >= size or (m.group(2) and int(m.group(2)) < int(m.group(1))):
                    self.send_response(416)
                    self.send_header("Content-Range", "bytes */%d" % size)
                    self.end_headers()
                    return
                start = int(m.group(1))
                if m.group(2):
                    end = min(end, int(m.group(2)))
                status = 206
            self.send_response(status)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Type", "application/json" if full.endswith(".json") else "application/octet-stream")
            self.send_header("Cache-Control", "no-cache" if full.endswith(".json") else "public, max-age=31536000, immutable")
            if status == 206:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            self.end_headers()
            if head:
                return
            with open(full, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    b = f.read(min(1 << 16, left))
                    if not b:
                        break
                    try:
                        self.wfile.write(b)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    left -= len(b)

    return Handler


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    # on Windows SO_REUSEADDR lets a second server take a port already in use, silently
    allow_reuse_address = os.name != "nt"


def make_server(root, bind="127.0.0.1", port=8000, quiet=True):
    if not os.path.isfile(os.path.join(root, MANIFEST_NAME)):
        raise OSError("no %s in %s: publish first" % (MANIFEST_NAME, root))
    return Server((bind, port), make_handler(root, quiet))


def serve_in_background(root, bind="127.0.0.1", port=8000):
    """(server, thread); server.shutdown() stops it."""
    srv = make_server(root, bind, port)
    t = threading.Thread(target=srv.serve_forever, name="deltapatcher-serve", daemon=True)
    t.start()
    return srv, t
