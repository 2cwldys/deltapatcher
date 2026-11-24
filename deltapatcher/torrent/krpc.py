"""A small DHT client that gets release items and puts one exactly as it was signed (BEP 5 messages, BEP 44 get
and put).

libtorrent's own put numbers an item after whatever the DHT still holds, so after the DHT forgot the item (every
node restarted, or no put for its lifetime) it would number a new release 1, and every player who remembers a
higher number would refuse it. This client puts the engine's own signed item, unchanged: a get that walks from
the nodes it is given to the nodes closest to the item's target, collecting their write tokens, then a put to
the closest ones. Items are read with it too (libtorrent 2.0's Python binding cannot give back an item whose value
is a dictionary). It never answers queries (its messages say it is read-only, BEP 43), so no node keeps it.

A node can answer anything, so nothing it says is taken on its word: an answer counts only from the address it was
asked at, with the random transaction id it was asked with; and a number a node says it holds counts only with an
item signed by the publisher key for this torrent (item.check), never alone.
"""

import os
import socket
import time

from .. import signing
from . import TorrentError, bencode, item

K = 8                        # how many of the closest nodes get the item
ALPHA = 3                    # how many nodes are asked at the same time


def _distance(a, b):
    return int.from_bytes(bytes(x ^ y for x, y in zip(a, b)), "big")


def _nodes(blob):
    """(node id, (ip, port)) of each node in a compact node list (26 bytes each, IPv4)."""
    out = []
    if not isinstance(blob, bytes):
        return out
    for i in range(0, len(blob) - len(blob) % 26, 26):
        nid, ip, port = blob[i:i + 20], blob[i + 20:i + 24], int.from_bytes(blob[i + 24:i + 26], "big")
        if port:
            out.append((nid, (socket.inet_ntoa(ip), port)))
    return out


class Client(object):
    def __init__(self, bind_ip="0.0.0.0", wait=2.0, stop=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((bind_ip, 0))
        self.sock.settimeout(0.2)
        self.id = os.urandom(20)
        self.wait = wait
        self.stop = stop                    # a threading.Event: set, every wait ends within 0.2 seconds

    def stopped(self):
        return self.stop is not None and self.stop.is_set()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def ask(self, endpoints, q, args):
        """Send the query `q` to every endpoint at once; {endpoint: its answer ("r" dict), or None}. Each query has a
        random transaction id, and an answer counts only from the address its query went to."""
        sent = {}
        for ep in endpoints:
            t = os.urandom(4)
            while t in sent:
                t = os.urandom(4)
            a = dict(args)
            a[b"id"] = self.id
            msg = bencode.encode({b"t": t, b"y": b"q", b"q": q, b"a": a, b"ro": 1})
            for _attempt in range(2):
                try:
                    self.sock.sendto(msg, ep)
                    sent[t] = ep
                    break
                except ConnectionResetError:
                    continue                # Windows: about an earlier node gone, not this one: sent again
                except OSError:
                    break
        out = {ep: None for ep in endpoints}
        end = time.time() + self.wait
        while sent and time.time() < end and not self.stopped():
            try:
                data, frm = self.sock.recvfrom(65536)
            except socket.timeout:
                continue
            except ConnectionResetError:
                continue                    # Windows: a node asked earlier has gone (port unreachable); others answer
            except OSError:
                break
            try:
                msg = bencode.decode(data)
            except (bencode.BencodeError, ValueError):     # a datagram that is not a message: dropped
                continue
            if not isinstance(msg, dict):
                continue
            ep = sent.get(msg.get(b"t"))
            if ep is None or (frm[0], frm[1]) != (ep[0], ep[1]):
                continue                    # not an answer to a query of ours, or not from the node it went to
            del sent[msg[b"t"]]
            if msg.get(b"y") == b"r" and isinstance(msg.get(b"r"), dict):
                out[ep] = msg[b"r"]
            elif msg.get(b"y") == b"e":
                out[ep] = {b"error": msg.get(b"e")}
        return out


def valid_seq(r, k, salt):
    """The number of the item a node's answer holds when it is an item signed by the key `k` (32 bytes) for the
    salt `salt` (item.check: the key, the salt, the value and the signature); None otherwise: no item, another
    key's, or one whose signature does not hold."""
    if not all(x in r for x in (b"k", b"seq", b"sig", b"v")) or r[b"k"] != k:
        return None
    try:
        it = item.check(r[b"k"], salt, r[b"seq"], r[b"sig"], r[b"v"], signing.key_text(k), salt.decode("ascii"))
    except (TorrentError, UnicodeDecodeError, TypeError, AttributeError):
        return None
    return it.seq


def _walk(client, target, start, seconds, k=None, salt=None):
    """A get of `target` that walks from the nodes `start` to the ones closest to it: ({endpoint: (node id, write
    token)} of the nodes that answered, [the items they hold: {b"k", b"seq", b"sig", b"v"}, as they gave them], the
    highest number of an item signed by `k` for `salt` that a node holds, checked (0 for none, and when no k is
    given))."""
    known = {}
    for ep in start:
        known.setdefault(tuple(ep), None)
    answered, items = {}, []
    asked = set()
    end = time.time() + seconds
    higher = 0
    while time.time() < end and not client.stopped():
        fresh = [ep for ep in known if ep not in asked]
        if not fresh:
            break
        close = sorted((_distance(nid, target), ep) for ep, (nid, _t) in answered.items())[:K]
        fresh.sort(key=lambda ep: (known[ep] is not None, _distance(known[ep], target) if known[ep] else 0))
        if len(close) >= K:
            worst = close[-1][0]
            fresh = [ep for ep in fresh if known[ep] is None or _distance(known[ep], target) < worst]
            if not fresh:
                break
        batch = fresh[:ALPHA * 2]
        for ep, r in client.ask(batch, b"get", {b"target": target}).items():
            asked.add(ep)
            if not r or b"error" in r:
                continue
            nid = r.get(b"id")
            if isinstance(nid, bytes) and len(nid) == 20 and isinstance(r.get(b"token"), bytes):
                answered[ep] = (nid, r[b"token"])
                known[ep] = nid
            seq = r.get(b"seq")
            if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0 and all(x in r for x in (b"k", b"sig", b"v")):
                items.append({b"k": r[b"k"], b"seq": seq, b"sig": r[b"sig"], b"v": r[b"v"]})
                if k is not None:
                    good = valid_seq(r, k, salt)
                    if good is not None:
                        higher = max(higher, good)
            for other_id, other_ep in _nodes(r.get(b"nodes")):
                if other_ep not in known:
                    known[other_ep] = other_id
    return answered, items, higher


def get_items(target, start, bind_ip="0.0.0.0", seconds=10, stop=None):
    """The items the nodes closest to `target` hold ({b"k", b"seq", b"sig", b"v"} each, as they gave them: the
    caller checks each one), highest number first, starting from the nodes `start` ((ip, port) pairs). `stop`: a
    threading.Event that ends it early."""
    client = Client(bind_ip, stop=stop)
    try:
        _answered, items, _higher = _walk(client, target, start, seconds)
    finally:
        client.close()
    return sorted(items, key=lambda d: -d[b"seq"])


def put_item(item_doc, target, start, bind_ip="0.0.0.0", seconds=20, log=None, stop=None):
    """Put a signed item (its decoded parts: {b"k", b"salt", b"seq", b"sig", b"v"}) on the nodes closest to its
    target, starting from the nodes `start` ((ip, port) pairs). Returns (how many nodes stored it, the highest
    number of an item signed by the same key for the same torrent that a node holds, checked; 0 for none). When a
    node holds a higher one, that is a newer release: nothing is put. A number a node gives without such an item
    (or with a forged one) changes nothing: the item goes to every one of the closest nodes. `stop`: a
    threading.Event that ends it early."""
    log = log or (lambda s: None)
    client = Client(bind_ip, stop=stop)
    try:
        answered, _items, higher = _walk(client, target, start, seconds, item_doc[b"k"], item_doc[b"salt"])
        if higher > item_doc[b"seq"]:
            log("the DHT holds a newer release item, numbered %d and signed by the publisher key: this one (number %d) "
                "is not put" % (higher, item_doc[b"seq"]))
            return 0, higher
        closest = sorted(answered.items(), key=lambda kv: _distance(kv[1][0], target))[:K]
        args = {b"k": item_doc[b"k"], b"salt": item_doc[b"salt"], b"seq": item_doc[b"seq"], b"sig": item_doc[b"sig"],
                b"v": item_doc[b"v"]}
        stored = 0
        for ep, (_nid, token) in closest:
            if client.stopped():
                break
            asked = dict(args)
            asked[b"token"] = token
            r = client.ask([ep], b"put", asked)[ep]
            if r is not None and b"error" not in r:
                stored += 1
        return stored, higher
    finally:
        client.close()
