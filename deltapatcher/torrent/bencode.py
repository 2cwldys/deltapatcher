"""Bencoding (BEP 3): how torrents and DHT messages are written. A small writer and a strict reader that need no
other package, so torrents and release items are made and checked without libtorrent.

Strings come back as bytes, dictionary keys too. The writer takes str (as UTF-8) or bytes, ints, lists and dicts,
and always writes dictionary keys in order, so the same value is always the same bytes.
"""

import re

_INT = re.compile(rb"(0|-?[1-9][0-9]*)\Z")        # i...e: digits only, no sign but -, no leading zero, no -0
_LEN = re.compile(rb"(0|[1-9][0-9]*)\Z")          # a string's length: digits only, no leading zero
MAX_DIGITS = 20      # no number of a torrent or a DHT message is longer (2**64 has 20); Python refuses past 4300


class BencodeError(ValueError):
    pass


def encode(obj):
    out = []
    _encode(obj, out)
    return b"".join(out)


def _key(k):
    if isinstance(k, str):
        return k.encode("utf-8")
    if isinstance(k, bytes):
        return k
    raise BencodeError("a dictionary key that is not text: %r" % (k,))


def _encode(obj, out):
    if isinstance(obj, bool):
        raise BencodeError("yes or no is not a bencoded value")
    if isinstance(obj, int):
        out.append(b"i%de" % obj)
    elif isinstance(obj, (bytes, bytearray)):
        out.append(b"%d:" % len(obj))
        out.append(bytes(obj))
    elif isinstance(obj, str):
        _encode(obj.encode("utf-8"), out)
    elif isinstance(obj, (list, tuple)):
        out.append(b"l")
        for item in obj:
            _encode(item, out)
        out.append(b"e")
    elif isinstance(obj, dict):
        out.append(b"d")
        items = sorted((_key(k), v) for k, v in obj.items())
        for i in range(1, len(items)):
            if items[i][0] == items[i - 1][0]:
                raise BencodeError("a dictionary with the key %r twice" % items[i][0])
        for k, v in items:
            _encode(k, out)
            _encode(v, out)
        out.append(b"e")
    else:
        raise BencodeError("not a bencoded value: %r" % (type(obj).__name__,))


def decode(data, depth=64):
    """The value bencoded in `data` (bytes), all of it: BencodeError when it is not exactly one value, written the
    one way bencoding allows (keys in order and once, no leading zeros)."""
    if not isinstance(data, (bytes, bytearray)):
        raise BencodeError("not bytes")
    data = bytes(data)
    value, end = _decode(data, 0, depth)
    if end != len(data):
        raise BencodeError("data after the value")
    return value


def decode_at(data, start=0, depth=64):
    """(the value that starts at `start` in `data`, where it ends): for a part of a larger message."""
    return _decode(data, start, depth)


def _int(data, start, end, pattern=_INT):
    """The number written in data[start:end], only in the one way bencoding allows (Python's int() would also take
    +5, " 5" and 1_0), and at most MAX_DIGITS digits long: anything else is a BencodeError, never another error."""
    text = data[start:end]
    if len(text.lstrip(b"-")) > MAX_DIGITS or not pattern.match(text):
        raise BencodeError("a number written wrongly")
    try:
        return int(text)
    except ValueError:
        raise BencodeError("a number written wrongly")


def _decode(data, i, depth):
    if depth <= 0:
        raise BencodeError("nested too deep")
    if i >= len(data):
        raise BencodeError("cut short")
    c = data[i:i + 1]
    if c == b"i":
        end = data.find(b"e", i + 1)
        if end < 0:
            raise BencodeError("cut short")
        return _int(data, i + 1, end), end + 1
    if c == b"l":
        i += 1
        out = []
        while True:
            if i >= len(data):
                raise BencodeError("cut short")
            if data[i:i + 1] == b"e":
                return out, i + 1
            v, i = _decode(data, i, depth - 1)
            out.append(v)
    if c == b"d":
        i += 1
        out = {}
        last = None
        while True:
            if i >= len(data):
                raise BencodeError("cut short")
            if data[i:i + 1] == b"e":
                return out, i + 1
            k, i = _decode(data, i, depth - 1)
            if not isinstance(k, bytes):
                raise BencodeError("a dictionary key that is not text")
            if last is not None and k <= last:
                raise BencodeError("dictionary keys out of order or twice")
            last = k
            v, i = _decode(data, i, depth - 1)
            out[k] = v
    if c.isdigit():
        colon = data.find(b":", i)
        if colon < 0:
            raise BencodeError("cut short")
        n = _int(data, i, colon, _LEN)
        if colon + 1 + n > len(data):
            raise BencodeError("cut short")
        return data[colon + 1:colon + 1 + n], colon + 1 + n
    raise BencodeError("not a bencoded value at byte %d" % i)
