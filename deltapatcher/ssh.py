"""SSH for deploys, through the system's OpenSSH client (ssh, sftp, ssh-keygen, ssh-keyscan, ssh-add).

Security rules this module keeps:
- Private keys are read only by OpenSSH. This program never opens one; it asks ssh-keygen whether a
  key has a passphrase, and new keys are written once, with owner-only permissions, never
  overwriting an existing file.
- Passphrases are never stored, logged, put on a command line or in a config file. A key with a
  passphrase is unlocked into ssh-agent (ssh-add), which asks through a dialog (SSH_ASKPASS) or
  the terminal.
- Host keys are not accepted blindly. The first time a host is used, its fingerprints are shown
  and must be confirmed; they are kept in this program's own known_hosts file and every later
  connection uses StrictHostKeyChecking=yes against it.
- Only SFTP is needed, so the key can be limited to SFTP on the server
  (authorized_keys: restrict,command="internal-sftp").
"""

import base64
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

from .manifest import HEX64

NOWINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

# Windows looks for a program in the current folder before PATH; a planted ssh-add.exe there would get
# the passphrase relay's token. This makes shutil.which (and the programs this one starts) skip it.
if os.name == "nt":
    os.environ.setdefault("NoDefaultCurrentDirectoryInExePath", "1")

HOST_RE = re.compile(r"^(\[[0-9A-Fa-f:.]+\]|[0-9A-Fa-f:]+:[0-9A-Fa-f:.]*|[A-Za-z0-9]([A-Za-z0-9.\-_]*[A-Za-z0-9])?)$")
USER_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._\-$]*$")
SFTP_UNSAFE = set('"\'\\*?[]\r\n\t')


class SshError(Exception):
    pass


def tool(name):
    """The path of an OpenSSH program, or SshError saying how to get it."""
    p = shutil.which(name)
    if not p and os.name == "nt":
        cand = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "OpenSSH", name + ".exe")
        if os.path.isfile(cand):
            p = cand
    if not p:
        how = ("Settings > Apps > Optional features > OpenSSH Client" if os.name == "nt"
               else "your package manager (openssh-client)")
        raise SshError("%s was not found: install the OpenSSH client (%s)" % (name, how))
    return p


def run(args, timeout=120, env=None, cwd=None, stdin=subprocess.DEVNULL):
    try:
        return subprocess.run(args, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                              env=env, cwd=cwd, creationflags=NOWINDOW, universal_newlines=True,
                              encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise SshError("%s did not finish within %d s" % (os.path.basename(args[0]), timeout))
    except OSError as e:
        raise SshError("could not run %s: %s" % (args[0], e))


def config_dir():
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "DeltaPatcher")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "deltapatcher")


def default_key_path():
    return os.path.join(os.path.expanduser("~"), ".ssh", "deltapatcher_ed25519")


def default_known_hosts():
    return os.path.join(config_dir(), "known_hosts")


def _cfg_path(p):
    """A path as an ssh_config value: forward slashes, double-quoted (spaces are allowed)."""
    p = os.path.abspath(p).replace("\\", "/")
    if '"' in p:
        raise SshError("a path with a double quote cannot be used: %s" % p)
    return '"%s"' % p


def check_remote_path(p):
    if not p or not p.strip():
        raise SshError("no remote folder")
    bad = SFTP_UNSAFE.intersection(p)
    if bad:
        raise SshError("the remote folder must not contain %s (use forward slashes)" % " ".join(sorted(repr(c) for c in bad)))
    if p.startswith("-"):
        raise SshError("the remote folder must not start with '-'")
    return p.rstrip("/") or "/"


class Target(object):
    """Where a deploy goes: user@host:port, the key, and the folder the web server serves."""

    def __init__(self, host, user="", port=22, key="", remote_root="", known_hosts=""):
        host = (host or "").strip()
        if not host or not HOST_RE.match(host):
            raise SshError("not a host name or address: %r" % host)
        user = (user or "").strip()
        if user and not USER_RE.match(user):
            raise SshError("not a user name: %r" % user)
        try:
            port = int(port or 22)
        except ValueError:
            raise SshError("the port must be a number")
        if not 0 < port < 65536:
            raise SshError("the port must be between 1 and 65535")
        self.host = host.strip("[]") if host.startswith("[") else host
        self.user = user
        self.port = port
        self.key = os.path.abspath(os.path.expanduser(key)) if key else ""
        self.remote_root = check_remote_path(remote_root) if remote_root else ""
        self.known_hosts = known_hosts or default_known_hosts()

    def dest(self):
        h = ("[%s]" % self.host) if ":" in self.host else self.host
        return ("%s@%s" % (self.user, h)) if self.user else h

    def known_name(self):
        """The host as OpenSSH writes it in known_hosts: bare on port 22, [host]:port otherwise."""
        return self.host if self.port == 22 else "[%s]:%d" % (self.host, self.port)

    def options(self, batch=True):
        o = ["-o", "StrictHostKeyChecking=yes",
             "-o", "UserKnownHostsFile=" + _cfg_path(self.known_hosts),
             "-o", "GlobalKnownHostsFile=none",
             "-o", "ConnectTimeout=20",
             "-o", "ServerAliveInterval=15",
             "-o", "ServerAliveCountMax=4",
             "-o", "LogLevel=ERROR"]
        if batch:
            o += ["-o", "BatchMode=yes"]
        if self.key:
            o += ["-i", self.key, "-o", "IdentitiesOnly=yes"]
        return o

    def __str__(self):
        return "%s:%d" % (self.dest(), self.port)


# ---- host keys ------------------------------------------------------------------------------

def fingerprint(key_b64):
    """OpenSSH's SHA256 fingerprint of a base64 public key blob."""
    digest = hashlib.sha256(base64.b64decode(key_b64)).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def scan_host(target, timeout=15):
    """[(key type, base64 blob, fingerprint)] the host offers, as ssh-keyscan sees them."""
    r = run([tool("ssh-keyscan"), "-p", str(target.port), "-T", str(timeout), "-t", "ed25519,ecdsa,rsa", target.host],
            timeout=timeout + 10)
    keys = []
    for line in r.stdout.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            try:
                keys.append((parts[1], parts[2], fingerprint(parts[2])))
            except (ValueError, TypeError):
                continue
    if not keys:
        raise SshError("%s offered no host key (is SSH listening on port %d?)" % (target.host, target.port))
    return keys


def known_keys(target):
    """[(key type, base64 blob)] already trusted for this host in our known_hosts."""
    if not os.path.isfile(target.known_hosts):
        return []
    r = run([tool("ssh-keygen"), "-F", target.known_name(), "-f", target.known_hosts], timeout=30)
    out = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if line.startswith("#") or len(parts) < 3:
            continue
        out.append((parts[1], parts[2]))
    return out


def is_trusted(target):
    return bool(known_keys(target))


def host_key_state(target, scanned):
    """'trusted' (it offers a key confirmed before), 'changed' (keys were confirmed for this host, and it
    offers none of them: reinstalled, or someone in between) or 'unknown' (never confirmed)."""
    known = set(known_keys(target))
    if not known:
        return "unknown"
    offered = set((t, b) for t, b, _fp in scanned)
    return "trusted" if known & offered else "changed"


def trust(target, keys):
    """Add the confirmed host keys to our known_hosts (owner-only file)."""
    os.makedirs(os.path.dirname(target.known_hosts), exist_ok=True)
    new = not os.path.exists(target.known_hosts)
    with open(target.known_hosts, "a", encoding="ascii", newline="\n") as f:
        for ktype, blob, _fp in keys:
            f.write("%s %s %s\n" % (target.known_name(), ktype, blob))
    if new:
        _owner_only(target.known_hosts)


def forget(target):
    if os.path.isfile(target.known_hosts):
        run([tool("ssh-keygen"), "-R", target.known_name(), "-f", target.known_hosts], timeout=30)


# ---- keys -------------------------------------------------------------------------------------

def _owner_only(path):
    """Owner-only permissions. Windows OpenSSH refuses a private key others can read."""
    if os.name != "nt":
        os.chmod(path, 0o600)
        return
    user = os.environ.get("USERNAME", "")
    dom = os.environ.get("USERDOMAIN", "")
    who = ("%s\\%s" % (dom, user)) if dom and user else user
    if not who:
        raise SshError("cannot tell who the current Windows user is to lock %s down" % path)
    icacls = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe")
    steps = [[icacls, path, "/inheritance:r"],
             [icacls, path, "/grant:r", "%s:F" % who],
             [icacls, path, "/remove:g", "*S-1-1-0", "*S-1-5-11", "*S-1-5-32-545"]]
    for s in steps:
        r = run(s, timeout=30)
        if r.returncode != 0:
            raise SshError("icacls could not restrict %s: %s" % (path, (r.stderr or r.stdout).strip()))


def permission_problems(path):
    """Who else can read a private key, in words; [] when only the owner (and SYSTEM/Administrators) can."""
    if not os.path.isfile(path):
        return []
    if os.name != "nt":
        mode = os.stat(path).st_mode & 0o777
        return ["mode %o lets others read it" % mode] if mode & 0o077 else []
    icacls = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe")
    r = run([icacls, path], timeout=30)
    me = os.environ.get("USERNAME", "").lower()
    problems = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.lower().startswith(path.lower()):
            line = line[len(path):].strip()
        if ":(" not in line:
            continue
        who = line.split(":(", 1)[0].strip()
        low = who.lower()
        if low.endswith("\\" + me) or low == me or low in ("nt authority\\system", "builtin\\administrators"):
            continue
        problems.append("%s can read it" % who)
    return problems


def fix_permissions(path):
    _owner_only(path)


def key_has_passphrase(path):
    """True / False, asked of ssh-keygen with an empty passphrase (this program never reads the key)."""
    if not os.path.isfile(path):
        raise SshError("no key at %s" % path)
    r = run([tool("ssh-keygen"), "-y", "-P", "", "-f", path], timeout=30)
    if r.returncode == 0:
        return False
    err = (r.stderr or "").lower()
    if "passphrase" in err or "incorrect" in err or "decrypt" in err:
        return True
    raise SshError("ssh-keygen cannot read %s: %s" % (path, (r.stderr or r.stdout).strip()))


def can_generate():
    try:
        import cryptography.hazmat.primitives.asymmetric.ed25519  # noqa: F401
        return True
    except ImportError:
        return False


def generate_key(path, passphrase, comment="deltapatcher"):
    """Write a new ed25519 key pair at path / path.pub. Never overwrites. `passphrase` may be '' (not
    recommended) and is used once, in this process, to encrypt the key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.exists(path) or os.path.exists(path + ".pub"):
        raise SshError("%s already exists: choose another name (an existing key is never replaced)" % path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key = ed25519.Ed25519PrivateKey.generate()
    enc = (serialization.BestAvailableEncryption(passphrase.encode("utf-8")) if passphrase
           else serialization.NoEncryption())
    priv = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, enc)
    pub = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        os.write(fd, priv)
    finally:
        os.close(fd)
    _owner_only(path)
    with open(path + ".pub", "w", encoding="ascii", newline="\n") as f:
        f.write(pub.decode("ascii") + " " + comment + "\n")
    return path


def generate_key_interactive(path, comment="deltapatcher"):
    """ssh-keygen in this terminal: it asks for the passphrase itself."""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.exists(path):
        raise SshError("%s already exists" % path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rc = subprocess.call([tool("ssh-keygen"), "-t", "ed25519", "-a", "100", "-f", path, "-C", comment])
    if rc != 0:
        raise SshError("ssh-keygen failed (%d)" % rc)
    return path


def public_key(path):
    pub = path + ".pub"
    if os.path.isfile(pub):
        with open(pub, "r", encoding="ascii", errors="replace") as f:
            line = f.read().strip()
        if line.split()[0].startswith(("ssh-", "ecdsa-", "sk-")):
            return line
    raise SshError("no %s beside the key (ssh-keygen -y -f \"%s\" > \"%s\" writes it)" % (pub, path, pub))


def authorized_keys_line(path, sftp_only=True):
    """The line to add to ~/.ssh/authorized_keys on the server for this key."""
    opts = 'restrict,command="internal-sftp"' if sftp_only else "restrict"
    return "%s %s" % (opts, public_key(path))


# ---- the agent --------------------------------------------------------------------------------

def agent_state():
    """'keys', 'empty' or 'none' (no agent reachable)."""
    try:
        r = run([tool("ssh-add"), "-l"], timeout=15)
    except SshError:
        return "none"
    return {0: "keys", 1: "empty"}.get(r.returncode, "none")


def agent_has(path):
    try:
        pub = public_key(path).split()
        fp = fingerprint(pub[1])
    except (SshError, IndexError, ValueError):
        return False
    r = run([tool("ssh-add"), "-l"], timeout=15)
    return r.returncode == 0 and fp in r.stdout


def agent_help():
    if os.name == "nt":
        return ("The OpenSSH Authentication Agent is not running. In an administrator PowerShell:\n"
                "  Get-Service ssh-agent | Set-Service -StartupType Automatic\n"
                "  Start-Service ssh-agent")
    return "No ssh-agent is running: start one (eval \"$(ssh-agent -s)\") or log in with a desktop that runs one."


def askpass_program():
    """The program ssh-add runs as SSH_ASKPASS: this program again (the built one), or from source a
    small script that runs it with the same Python."""
    if getattr(sys, "frozen", False):
        return sys.executable
    pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = config_dir()
    os.makedirs(d, exist_ok=True)
    if os.name == "nt":
        script = os.path.join(d, "askpass.cmd")
        body = '@echo off\r\nset "PYTHONPATH=%s"\r\n"%s" -m deltapatcher %%*\r\n' % (pkg_parent, sys.executable)
    else:
        script = os.path.join(d, "askpass.sh")
        body = '#!/bin/sh\nPYTHONPATH="%s" exec "%s" -m deltapatcher "$@"\n' % (pkg_parent, sys.executable)
    with open(script, "w", encoding="utf-8", newline="") as f:
        f.write(body)
    if os.name != "nt":
        os.chmod(script, 0o700)
    return script


def agent_add(path, use_dialog=False, timeout=300):
    """ssh-add the key, asking the passphrase in this terminal."""
    if agent_state() == "none":
        raise SshError(agent_help())
    rc = subprocess.call([tool("ssh-add"), path])
    if rc != 0:
        raise SshError("ssh-add failed (%d)" % rc)


def agent_add_with(path, passphrase, timeout=60):
    """ssh-add the key with a passphrase this program already has (the app asked for it).

    ssh-add takes a passphrase only from a terminal or from its SSH_ASKPASS program, so this program
    becomes that program for one run: it listens on 127.0.0.1 for a single connection that must show a
    one-time token, and hands the passphrase to it (askpass_relay below). The passphrase is never on a
    command line, in the environment or in a file; the listener answers once and closes.
    """
    import hmac
    import secrets
    import socket
    import threading
    if agent_state() == "none":
        raise SshError(agent_help())
    token = secrets.token_hex(16)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    srv.settimeout(timeout)
    port = srv.getsockname()[1]
    secret = {"value": passphrase.encode("utf-8")}

    def serve_once():
        try:
            conn, _addr = srv.accept()
            with conn:
                conn.settimeout(10)
                got = conn.recv(64).decode("ascii", "replace").strip()
                if hmac.compare_digest(got, token):
                    conn.sendall(secret["value"] + b"\n")
                    secret["handed"] = True
        except OSError:
            pass
        finally:
            secret["value"] = b""
            srv.close()

    t = threading.Thread(target=serve_once, daemon=True)
    t.start()
    env = dict(os.environ)
    env.update({"SSH_ASKPASS": askpass_program(), "SSH_ASKPASS_REQUIRE": "force", "DELTAPATCHER_ASKPASS": "relay",
                "DELTAPATCHER_ASKPASS_PORT": str(port), "DELTAPATCHER_ASKPASS_TOKEN": token})
    env.setdefault("DISPLAY", ":0")
    try:
        r = run([tool("ssh-add"), path], timeout=timeout, env=env)
    finally:
        try:
            srv.close()
        except OSError:
            pass
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        # a wrong passphrase makes ssh-add ask again; the relay has closed, so it gives up without a word
        if "bad passphrase" in err.lower() or "incorrect" in err.lower() or (secret.get("handed") and not err):
            raise SshError("the passphrase is not the key's")
        raise SshError("ssh-add did not add the key: %s" % (err or "no reason given"))


def askpass_relay():
    """This program run by ssh-add as SSH_ASKPASS: fetch the passphrase from agent_add_with's listener
    and print it for ssh-add. Exit code 1 when there is nothing to give."""
    import socket
    try:
        port = int(os.environ.get("DELTAPATCHER_ASKPASS_PORT", "0"))
        token = os.environ.get("DELTAPATCHER_ASKPASS_TOKEN", "")
        with socket.create_connection(("127.0.0.1", port), timeout=10) as c:
            c.sendall(token.encode("ascii") + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                b = c.recv(4096)
                if not b:
                    break
                data += b
    except (OSError, ValueError):
        return 1
    if not data:
        return 1
    os.write(1, data)
    return 0


# ---- sftp ------------------------------------------------------------------------------------

def q(arg):
    """One sftp batch argument, quoted. Arguments with quotes, backslashes or wildcards are refused
    rather than escaped: sftp expands wildcards in put/get/rm/ls."""
    if SFTP_UNSAFE.intersection(arg):
        raise SshError("an sftp argument this program will not send: %r" % arg)
    return '"%s"' % arg


def sftp(target, commands, cwd=None, timeout=3600):
    """Run sftp commands in batch mode. Returns the CompletedProcess. A command starting with '-'
    may fail without stopping the batch."""
    fd, batch = tempfile.mkstemp(prefix="dp-sftp-", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(commands) + "\n")
        args = [tool("sftp"), "-b", batch, "-P", str(target.port)] + target.options(batch=True) + [target.dest()]
        return run(args, timeout=timeout, cwd=cwd)
    finally:
        try:
            os.remove(batch)
        except OSError:
            pass


LS_RE = re.compile(r"^[-dlrwxsStT]{10}\S*\s+\S+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(.+)$")


def parse_ls(text, blobs_only=True):
    """{name: size} from an `ls -ln` listing: the blob files (and .part leftovers), or with
    blobs_only=False every regular file."""
    out = {}
    for line in text.splitlines():
        if line.startswith("sftp>"):
            continue
        m = LS_RE.match(line.rstrip())
        if not m or not line.startswith("-"):
            continue
        name = m.group(2).rsplit("/", 1)[-1]
        if not blobs_only or HEX64.match(name) or (name.endswith(".part") and HEX64.match(name[:-5])):
            out[name] = int(m.group(1))
    return out


def explain(r):
    """A sentence for a failed ssh/sftp run, with the usual causes."""
    err = (r.stderr or r.stdout or "").strip()
    low = err.lower()
    if "host key verification failed" in low or "no matching host key" in low or "remote host identification has changed" in low:
        return ("the server's host key is not the one you confirmed. If the server was reinstalled, check its "
                "fingerprint and confirm it again; otherwise do not connect. (%s)" % err)
    if "permission denied" in low and "publickey" in low:
        return ("the server refused the key: add its public key line to ~/.ssh/authorized_keys on the server, "
                "and unlock the key if it has a passphrase. (%s)" % err)
    if "unprotected private key" in low or "bad permissions" in low:
        return "the private key can be read by other users: fix its permissions first. (%s)" % err
    if "connection timed out" in low or "could not resolve" in low or "connection refused" in low:
        return "the server could not be reached: check the address and port. (%s)" % err
    return err or "sftp failed (exit %d)" % r.returncode


def test_connection(target):
    """Log in with sftp and print the working folder; (ok, text)."""
    cmds = ["pwd"]
    if target.remote_root:
        cmds.append("ls -ln %s" % q(target.remote_root))
    r = sftp(target, cmds, timeout=60)
    if r.returncode != 0:
        if target.remote_root and "pwd" in r.stdout and ("not found" in (r.stderr or "").lower() or "no such" in (r.stderr or "").lower()):
            return True, "logged in; the remote folder %s does not exist yet (the first deploy creates it)" % target.remote_root
        return False, explain(r)
    lines = [l for l in r.stdout.splitlines() if l.strip() and not l.startswith("sftp>")]
    return True, "logged in. " + (lines[0] if lines else "")
