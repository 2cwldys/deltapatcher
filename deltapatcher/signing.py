"""Signed releases: the publisher signs each manifest with an Ed25519 key, clients check it.

This is the "handshake" a static host can have: no server program is involved, so the client
proves who made the release from the release itself.

- The publisher makes a signing key once (`deltapatcher sign keygen`) and keeps the private half.
  Its public half is a short text, "ed25519:<base64>", that can be given to players or put in a
  launcher.
- Publishing with the key writes sigs/<sha256 of manifest.json>.sig next to the manifest. The name
  carries the manifest's hash, so a signature can never be paired with another manifest, even while
  a deploy is replacing it.
- A client checks the signature against the key it trusts: one given to it in advance (--trust-key),
  or the key of the first signed release it saw, pinned from then on (trust on first use). After that
  a release signed by another key, or not signed at all, is refused.

Checking needs no extra package: `cryptography` is used when it is installed, and otherwise a small
implementation of Ed25519 verification (RFC 8032) below. Making keys and signing needs `cryptography`.
"""

import base64
import hashlib
import json
import os

PREFIX = "ed25519:"
SIG_DIR = "sigs"
FORMAT = 1


class SignError(Exception):
    pass


# ---- the public key as text ------------------------------------------------------------------

def key_text(raw):
    return PREFIX + base64.b64encode(raw).decode("ascii")


def parse_key(text):
    """The 32 raw bytes of a public key given as 'ed25519:<base64>' (or bare base64)."""
    t = (text or "").strip()
    if t.lower().startswith(PREFIX):
        t = t[len(PREFIX):]
    try:
        raw = base64.b64decode(t, validate=True)
    except (ValueError, TypeError):
        raise SignError("not a publisher key: %r" % text)
    if len(raw) != 32:
        raise SignError("not a publisher key (an Ed25519 key is 32 bytes): %r" % text)
    return raw


def fingerprint(raw):
    """A short name for a key in logs: SHA256:<base64 of its hash>, as OpenSSH shows keys."""
    return "SHA256:" + base64.b64encode(hashlib.sha256(raw).digest()).decode("ascii").rstrip("=")


# ---- Ed25519 verification without dependencies (RFC 8032, section 5.1.7) ---------------------

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _add(a, b):
    """Point addition in extended coordinates (X, Y, Z, T)."""
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s, pt):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, pt)
        pt = _add(pt, pt)
        s >>= 1
    return q


def _same(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _decompress(s):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def verify_pure(public, message, signature):
    if len(public) != 32 or len(signature) != 64:
        return False
    a = _decompress(public)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % _L
    return _same(_mul(s, _G), _add(r, _mul(h, a)))


def verify(public, message, signature):
    """True when `signature` is the Ed25519 signature of `message` by `public` (32 raw bytes)."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return verify_pure(public, message, signature)
    try:
        Ed25519PublicKey.from_public_bytes(public).verify(signature, message)
        return True
    except (InvalidSignature, ValueError):
        return False


# ---- signature files --------------------------------------------------------------------------

def sig_name(manifest_sha):
    return manifest_sha + ".sig"


def make_sig(private_key, manifest_bytes):
    """The bytes of a signature file for this manifest."""
    from cryptography.hazmat.primitives import serialization
    pub = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    doc = {
        "format": FORMAT,
        "alg": "ed25519",
        "key": key_text(pub),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "signature": base64.b64encode(private_key.sign(manifest_bytes)).decode("ascii"),
    }
    return (json.dumps(doc, indent=1) + "\n").encode("ascii")


def check_sig(sig_bytes, manifest_bytes):
    """The signing key (32 raw bytes) of a valid signature file for this manifest; SignError if not valid."""
    try:
        doc = json.loads(sig_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise SignError("the signature file is not readable")
    if not isinstance(doc, dict) or doc.get("format") != FORMAT or doc.get("alg") != "ed25519":
        raise SignError("the signature file has an unknown format or algorithm")
    if doc.get("manifest_sha256") != hashlib.sha256(manifest_bytes).hexdigest():
        raise SignError("the signature file is for another manifest")
    key = parse_key(doc.get("key", ""))
    try:
        sig = base64.b64decode(doc.get("signature", ""), validate=True)
    except (ValueError, TypeError):
        raise SignError("the signature is not base64")
    if not verify(key, manifest_bytes, sig):
        raise SignError("the signature does not match the manifest: the release was altered, or not signed by %s"
                        % fingerprint(key))
    return key


# ---- signing keys -------------------------------------------------------------------------------

def default_key_path():
    from .ssh import config_dir
    return os.path.join(config_dir(), "signing-key.pem")


def can_sign():
    try:
        import cryptography.hazmat.primitives.asymmetric.ed25519  # noqa: F401
        return True
    except ImportError:
        return False


def generate_key(path, passphrase):
    """Write a new signing key (PKCS#8 PEM, encrypted with the passphrase when there is one, readable
    by this account only). Never overwrites. Returns the public key as text."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from .ssh import _owner_only
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.exists(path):
        raise SignError("%s already exists: an existing signing key is never replaced" % path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key = ed25519.Ed25519PrivateKey.generate()
    enc = (serialization.BestAvailableEncryption(passphrase.encode("utf-8")) if passphrase
           else serialization.NoEncryption())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, pem)
    finally:
        os.close(fd)
    _owner_only(path)
    return public_key_text(key)


def needs_passphrase(path):
    try:
        with open(path, "rb") as f:
            return b"ENCRYPTED" in f.read(64)
    except OSError as e:
        raise SignError("cannot read the signing key %s: %s" % (path, e))


def load_key(path, passphrase=None):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise SignError("cannot read the signing key %s: %s" % (path, e))
    try:
        key = serialization.load_pem_private_key(data, passphrase.encode("utf-8") if passphrase else None)
    except (ValueError, TypeError) as e:
        raise SignError("the signing key could not be opened (wrong passphrase?): %s" % e)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise SignError("%s is not an Ed25519 signing key" % path)
    return key


def public_key_text(private_key):
    from cryptography.hazmat.primitives import serialization
    return key_text(private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
