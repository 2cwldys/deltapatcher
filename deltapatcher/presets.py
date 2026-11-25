"""Project files: one or more projects in one file, to share with other people.

A project file is an HTML page. In a browser it shows the projects it holds, who signed it and how to add
them. Saved as NAME.html it opens in any browser; saved as NAME.deltapatch it opens DeltaPatcher with a
double-click. The two are the same text. The projects are JSON inside the page:

    <script type="application/json" id="deltapatcher-preset">{"format": "deltapatcher-preset", ...}</script>

DeltaPatcher only reads that JSON out of the text: it never renders the page or runs anything in it. A file
over 1 MB is refused, the element must be there exactly once and exactly as written here, and every field
is checked.

What it holds: for each project its name, the http(s) address it updates from, the publisher key its
releases are signed with, a description and a website; when the file was made; who signed it. Never a
folder, a server login, a key file or anything else of the computer that made it: whoever imports it picks
each project's folder. A private file (version 4, below) also holds what releasing needs, the server's address
and login name among it, and still never a folder or a key.

Signed with the publisher's signing key (Ed25519, the key that signs releases), the signature is a second
element, <script type="application/json" id="deltapatcher-signature">. It covers the whole page but that
element (line endings aside), so a file changed anywhere after it was signed is refused.

Version 1 sets up a new project for each of its projects. Version 2 adds "updates": true, an update capable
file: the projects it sets up follow their publisher (publisher.py), and opening a newer one for a project
this computer has updates that project, after asking. A file that is not update capable is written as
version 1, so the DeltaPatchers before version 2 still read it; they say a version 2 file was made by a
newer DeltaPatcher.

Version 3 is a file with a project shared by torrent: that project's entry has "torrent": {"id", "name",
"seeds": ["host:port", ...]} (torrent/__init__.py), it must name its publisher key, and it may have no address
at all (it updates by torrent only). DeltaPatchers before version 3 say it was made by a newer DeltaPatcher.

Version 4 is a private project file: the publisher's own, to keep changing and releasing its projects from any
computer of theirs. It is a project file as above with "private": true at the top, and each project's entry has
one more part, "private": what releasing it needs, as an allow-list of its settings (PRIVATE_FIELDS), and the
server's host keys as DeltaPatcher's own known_hosts has them. Never a path of the computer that made it, a key
or a passphrase: whoever opens it gives their own folders and key files, and the signing key moves by hand. It is
always signed with its projects' signing key, which every private part names (signing_public_key); an unsigned
one is refused. A file without a private part is never version 4, and DeltaPatchers before version 4 say a
private file was made by a newer DeltaPatcher.
"""

import base64
import hashlib
import html
import json
import os
import re
import time
import unicodedata
import urllib.parse

from . import REPO, __version__, config, projects, signing, ssh
from .source import DEFAULT_URL

FORMAT = "deltapatcher-preset"
VERSION = 4                     # the newest version read and written; a file without anything new is version 1
EXTENSIONS = (".deltapatch", ".html", ".htm")
LIMIT = 1 << 20
MAX_PROJECTS = 100
DATA_OPEN = '<script type="application/json" id="deltapatcher-preset">'
SIG_OPEN = '<script type="application/json" id="deltapatcher-signature">'
CLOSE = "</script>"
DOMAIN = b"DeltaPatcher project file 1\n"          # a signature over a project file is never one over a manifest
RELEASES = "https://github.com/%s/releases" % REPO
FIELDS = ("title", "source_url", "trust_key", "description", "website", "install_hint", "install_folder")
TORRENT_FIELD = "torrent"           # version 3: how a project is shared by torrent
PRIVATE_FIELD = "private"           # version 4: what releasing a project needs (private_part())
# The private part, section by section: every field there is, each the setting of the same name (host_keys aside),
# and nothing else, whatever settings config.py has. access_log only with deploy_mode sftp, where it is a path on
# the server; elsewhere it would be one of this computer. The backups' limits travel, never their schedule
# (backup_every_hours): that is each computer's own, as its backup folder is.
PRIVATE_FIELDS = (
    ("publish", ("name", "version", "protect", "leave_out", "sign", "htaccess", "share_updates", "show_files",
                 "signing_public_key")),
    ("server", ("deploy_mode", "ssh_host", "ssh_user", "ssh_port", "remote_root", "public_url", "access_log",
                "host_keys")),
    ("torrent", ("torrent", "torrent_id", "torrent_name", "seeds")),
    ("backup", ("backup_keep_daily", "backup_keep_weekly", "backup_keep_monthly", "backup_max_size")),
)
# each setting a private file gives, as a change to it is shown
PRIVATE_LABELS = {
    "description": "description", "website": "website", "install_hint": "where it goes",
    "install_folder": "its folder name", "source_url": "the address it updates from", "trust_key": "the publisher key",
    "name": "the release's name", "version": "the release's version", "protect": "the files players keep",
    "leave_out": "the files left out", "sign": "signed releases", "htaccess": ".htaccess for Apache",
    "share_updates": "players follow it", "show_files": "file names shown to players",
    "signing_public_key": "the signing key", "deploy_mode": "where releases go", "ssh_host": "the server",
    "ssh_user": "the login name", "ssh_port": "the SSH port", "remote_root": "the folder on the server",
    "public_url": "the address players update from", "access_log": "the access log",
    "torrent": "shared by torrent", "torrent_id": "the torrent id", "torrent_name": "the torrent's name",
    "seeds": "the seeds", "backup_keep_daily": "daily backups kept",
    "backup_keep_weekly": "weekly backups kept", "backup_keep_monthly": "monthly backups kept",
    "backup_max_size": "the backups' size limit",
}
HOST_KEY_TYPES = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521", "ssh-rsa",
                  "sk-ssh-ed25519@openssh.com", "sk-ecdsa-sha2-nistp256@openssh.com")
MAX_HOST_KEYS = 16
RULES_MAX = 4000                    # protect, leave_out
COUNT_MAX = 100000                  # the backups kept a day, a week, a month
HINT_MAX = 300
LOOPBACK = ("localhost", "127.0.0.1", "::1")
# bidirectional controls, which can make a text read differently from what it holds
_BIDI = set(chr(c) for c in list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)))
# every character str.splitlines() ends a line at: none may be in a text that becomes a setting
_BREAKS = set("\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029")
# the line and paragraph separators a pasted text can hold (macOS Pages writes them): in a public file and in a
# release's details each is read and written as a space (one_line())
_SEPARATORS = ("\u2028", "\u2029")
_MADE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ\Z")


class PresetError(Exception):
    """A project file that cannot be written or read; `code` says why, for the app."""

    def __init__(self, message, code="not-a-project-file", data=None):
        Exception.__init__(self, message)
        self.code = code
        self.data = data or {}


def is_project_file(path):
    """A project file by its name: .deltapatch, .html or .htm."""
    return str(path).lower().endswith(EXTENSIONS)


# ---- the fields ---------------------------------------------------------------------------------

def address_problem(url):
    """What is wrong with an address in a project file ('' when nothing): plain ASCII, http or https, a host."""
    if not isinstance(url, str) or not url or len(url) > 500:
        return "an address of at most 500 characters"
    if any(ord(c) < 33 or ord(c) > 126 for c in url) or any(c in url for c in '"<>`\\'):
        return "an address in plain ASCII, without spaces or quotes"
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "an http(s) address with a host"
    return ""


def text_problem(text, most):
    """What is wrong with a text of a project file ('' when nothing): at most `most` characters, one line (none of the
    characters str.splitlines() splits at), no control or bidirectional character. A public file's texts are read
    and written through one_line() first; a private file's are taken as they are."""
    if not isinstance(text, str) or len(text) > most:
        return "a text of at most %d characters" % most
    if any(unicodedata.category(c) in ("Cc", "Cs") or c in _BIDI or c in _BREAKS for c in text):
        return "a text without control characters"
    return ""


def one_line(text):
    """A public text (a description, where a project goes) as public project files and a release's details read and
    write it: each line or paragraph separator (U+2028, U+2029) a space, so it stays one line. Anything else is left
    as it is (and text_problem() still refuses a control character)."""
    for c in _SEPARATORS:
        text = text.replace(c, " ")
    return text


def _public_texts(data):
    """A public file's data with its projects' texts (description, install_hint) read through one_line()."""
    for e in data.get("projects") if isinstance(data.get("projects"), list) else []:
        for k in ("description", "install_hint"):
            if isinstance(e, dict) and isinstance(e.get(k), str):
                e[k] = one_line(e[k])


def rules_problem(text):
    """What is wrong with protect or leave_out in a private file ('' when nothing): paths inside the folder released
    and patterns, separated by ;, never a path of a computer (a drive, a leading / \\ or ~, a :, a .. part)."""
    problem = text_problem(text, RULES_MAX)
    if problem:
        return problem
    for item in (i.strip() for i in text.split(";")):
        if item and (item.startswith(("/", "\\", "~")) or ":" in item or ".." in re.split(r"[\\/]", item)):
            return "paths inside the folder released, not a path of a computer: %s" % item
    return ""


def server_path_problem(path):
    """What is wrong with a path on the server (remote_root, access_log) in a private file ('' when nothing): one
    sftp can be given, as ssh.check_remote_path() takes it."""
    problem = text_problem(path, 500)
    if problem:
        return problem
    if path and (set(path) & ssh.SFTP_UNSAFE or path.startswith("-")):
        return "a path on the server without quotes, backslashes or wildcards, not starting with -"
    return ""


def host_key_problem(key):
    """What is wrong with one of the server's host keys in a private file ('' when nothing): exactly its type, one
    OpenSSH knows, and its key in base64, which starts with that type as OpenSSH writes it."""
    if not isinstance(key, dict) or set(key) != {"type", "key"}:
        return "a host key's type and key"
    kind, blob = key["type"], key["key"]
    if kind not in HOST_KEY_TYPES:
        return "a host key of a type OpenSSH knows"
    if not isinstance(blob, str) or not 16 <= len(blob) <= 4096 or not re.match(r"^[A-Za-z0-9+/]+={0,2}$", blob):
        return "a host key in base64"
    try:
        raw = base64.b64decode(blob, validate=True)
    except (ValueError, TypeError):
        return "a host key in base64"
    if raw[:4] != len(kind).to_bytes(4, "big") or raw[4:4 + len(kind)] != kind.encode("ascii"):
        return "a host key of the type it names"
    return ""


def _whole(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def private_problem(part, e):
    """What is wrong with the private part of a project file's project e ('' when nothing), field by field: exactly
    the fields of PRIVATE_FIELDS, each of its kind; unknown ones are refused, never passed over."""
    from . import backup, torrent
    if not isinstance(part, dict) or set(part) != set(s for s, _f in PRIVATE_FIELDS):
        return "its sections"
    for section, fields in PRIVATE_FIELDS:
        given = part[section]
        wanted = set(fields)
        if section == "server" and isinstance(given, dict) and given.get("deploy_mode") != "sftp":
            if "access_log" in given:
                return "the access log: only for releases sent to a server over SFTP (elsewhere it is a path of a computer)"
            wanted.discard("access_log")
        if not isinstance(given, dict) or set(given) != wanted:
            return "the fields of %s missing or unknown" % section
    p, s, t, b = part["publish"], part["server"], part["torrent"], part["backup"]
    for k in ("name", "version"):
        if text_problem(p[k], 200):
            return "%s: %s" % (PRIVATE_LABELS[k], text_problem(p[k], 200))
    for k in ("protect", "leave_out"):
        if rules_problem(p[k]):
            return "%s: %s" % (PRIVATE_LABELS[k], rules_problem(p[k]))
    for k in ("sign", "htaccess", "share_updates", "show_files"):
        if not isinstance(p[k], bool):
            return "%s: yes or no" % PRIVATE_LABELS[k]
    try:
        if not isinstance(p["signing_public_key"], str) or \
                signing.key_text(signing.parse_key(p["signing_public_key"])) != p["signing_public_key"]:
            return "the signing key: a public key (ed25519:...)"
    except signing.SignError:
        return "the signing key: a public key (ed25519:...)"
    if s["deploy_mode"] not in config.CHOICES["deploy_mode"]:
        return "where releases go: %s" % " ".join(config.CHOICES["deploy_mode"])
    host = s["ssh_host"]
    if not isinstance(host, str) or (host and (len(host) > 253 or not ssh.HOST_RE.match(host))):
        return "the server: a host name or address"
    if not isinstance(s["ssh_user"], str) or (s["ssh_user"] and (len(s["ssh_user"]) > 100 or not ssh.USER_RE.match(s["ssh_user"]))):
        return "the login name: a user name"
    if not _whole(s["ssh_port"], 1, 65535):
        return "the SSH port: from 1 to 65535"
    for k in ("remote_root", "access_log"):
        if k in s and server_path_problem(s[k]):
            return "%s: %s" % (PRIVATE_LABELS[k], server_path_problem(s[k]))
    if not isinstance(s["public_url"], str) or (s["public_url"] and address_problem(s["public_url"])):
        return "the address players update from: an http(s) address"
    keys = s["host_keys"]
    if not isinstance(keys, list) or len(keys) > MAX_HOST_KEYS or (keys and not host):
        return "the server's host keys: at most %d, with its address" % MAX_HOST_KEYS
    for k in keys:
        if host_key_problem(k):
            return "the server's host keys: %s" % host_key_problem(k)
    if len(set((k["type"], k["key"]) for k in keys)) != len(keys):
        return "the server's host keys: one listed twice"
    if not isinstance(t["torrent"], bool):
        return "shared by torrent: yes or no"
    if not isinstance(t["torrent_id"], str) or (t["torrent_id"] and torrent.id_problem(t["torrent_id"])):
        return "the torrent id: %s" % torrent.id_problem(t["torrent_id"])
    if t["torrent"] and not t["torrent_id"]:
        return "shared by torrent with no torrent id"
    if not isinstance(t["torrent_name"], str) or (t["torrent_name"] and torrent.name_problem(t["torrent_name"])):
        return "the torrent's name: %s" % torrent.name_problem(t["torrent_name"])
    try:
        if not isinstance(t["seeds"], str) or len(t["seeds"]) > 6000:
            raise ValueError("host:port, separated by commas")
        torrent.seeds_of(t["seeds"], strict=True)
    except ValueError as x:
        return "the seeds: %s" % x
    shared = e.get(TORRENT_FIELD)
    if (shared is not None) != t["torrent"] or (shared is not None and shared.get("id") != t["torrent_id"]):
        return "how it is shared by torrent: its two parts differ"
    for k in ("backup_keep_daily", "backup_keep_weekly", "backup_keep_monthly"):
        if not _whole(b[k], 0, COUNT_MAX):
            return "%s: a number from 0 to %d" % (PRIVATE_LABELS[k], COUNT_MAX)
    try:
        if not isinstance(b["backup_max_size"], str) or len(b["backup_max_size"]) > 40:
            raise ValueError("a size such as 10 GB")
        backup.parse_size(b["backup_max_size"])
    except ValueError as x:
        return "the backups' size limit: %s" % x
    return ""


def check_data(data):
    """The projects part of a project file, checked field by field; PresetError at the first wrong one."""
    def bad(what):
        raise PresetError("not a project file DeltaPatcher can read: %s" % what)
    if not isinstance(data, dict):
        bad("no projects in it")
    if data.get("format") != FORMAT:
        bad("not DeltaPatcher's format")
    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        bad("no version")
    if version > VERSION:
        raise PresetError("this project file was made by a newer DeltaPatcher: update DeltaPatcher first", "newer")
    if version < 1:
        bad("version %d" % version)
    if set(data) != {"format", "version", "made", "projects"} | ({"updates"} if version >= 2 else set()) | \
            ({"private"} if version >= 4 else set()):
        bad("fields missing or unknown")
    if version >= 2 and not isinstance(data["updates"], bool):
        bad("whether it is update capable")
    if version >= 4 and data["private"] is not True:
        bad("whether it is a private project file")
    if not isinstance(data["made"], str) or not _MADE.match(data["made"]):
        bad("when it was made")
    entries = data["projects"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_PROJECTS:
        bad("between 1 and %d projects" % MAX_PROJECTS)
    seen = set()
    allowed = set(FIELDS) | ({TORRENT_FIELD} if version >= 3 else set()) | ({PRIVATE_FIELD} if version >= 4 else set())
    for e in entries:
        if not isinstance(e, dict) or "title" not in e or not set(e) <= allowed or \
                ("source_url" not in e and TORRENT_FIELD not in e):
            bad("a project with unknown or missing fields")
        if not isinstance(e["title"], str) or projects.name_problem(e["title"]) or e["title"] != e["title"].strip():
            bad("a project's name: %s" % (projects.name_problem(e["title"]) or "not a name"))
        if e["title"].lower() in seen:
            bad("two projects named %s" % e["title"])
        seen.add(e["title"].lower())
        if "source_url" in e:
            problem = address_problem(e["source_url"])
            if problem:
                bad("the address of %s: %s" % (e["title"], problem))
        if TORRENT_FIELD in e:
            from . import torrent
            problem = torrent.object_problem(e[TORRENT_FIELD])
            if problem:
                bad("how %s is shared by torrent: %s" % (e["title"], problem))
            if "trust_key" not in e:
                bad("%s is shared by torrent, and names no publisher key" % e["title"])
        if "trust_key" in e:
            try:
                if not isinstance(e["trust_key"], str) or signing.key_text(signing.parse_key(e["trust_key"])) != e["trust_key"]:
                    bad("the publisher key of %s" % e["title"])
            except signing.SignError:
                bad("the publisher key of %s" % e["title"])
        if "description" in e and text_problem(e["description"], 500):
            bad("the description of %s: %s" % (e["title"], text_problem(e["description"], 500)))
        if "website" in e and address_problem(e["website"]):
            bad("the website of %s: %s" % (e["title"], address_problem(e["website"])))
        if "install_hint" in e and text_problem(e["install_hint"], HINT_MAX):
            bad("where %s goes: %s" % (e["title"], text_problem(e["install_hint"], HINT_MAX)))
        if "install_folder" in e:
            folder = e["install_folder"]
            problem = projects.folder_name_problem(folder) if isinstance(folder, str) else "not a folder's name"
            if problem:
                bad("the folder of %s: %s" % (e["title"], problem))
        if version >= 4:
            if PRIVATE_FIELD not in e:
                bad("%s has no private part" % e["title"])
            problem = private_problem(e[PRIVATE_FIELD], e)
            if problem:
                bad("the private part of %s: %s" % (e["title"], problem))
    if version >= 4 and len(set(e[PRIVATE_FIELD]["publish"]["signing_public_key"] for e in entries)) != 1:
        bad("projects with different signing keys")


# ---- writing one --------------------------------------------------------------------------------

def address(cfg):
    """The http(s) address players update from: where this project's releases are (public_url), else where it
    updates from (source_url) when that is not just the default. '' when neither is an http(s) address."""
    for key in ("public_url", "source_url"):
        value = str(cfg.get(key) or "").strip()
        if key == "source_url" and value == DEFAULT_URL:
            continue                            # never set: the default every new settings file has
        if not address_problem(value):
            return value
    return ""


def entry(project, cfg, signer_text="", signer_path=""):
    """What a project file says about one project: its name, address, publisher key, description, website and
    where it goes (install_hint, install_folder). Its publisher key is the one it trusts, else the signer's
    when its own releases are signed with that key. A project shared by torrent says so too (its torrent id, name
    and seeds), needs a publisher key, and may have no address: it updates by torrent only."""
    from . import torrent
    source = address(cfg)
    tinfo = torrent.info(cfg)
    if not source and tinfo is None:
        raise PresetError("%s has no http(s) address players update from: set where players download it "
                          "(public_url) or where it updates from (source_url)" % project.title, "no-address", project.tag())
    e = {"title": project.title}
    if source:
        e["source_url"] = source
    key = str(cfg.get("trust_key") or "").strip()
    if key and key.lower() != "off":
        try:
            e["trust_key"] = signing.key_text(signing.parse_key(key))
        except signing.SignError:
            raise PresetError("the publisher key of %s is not a publisher key: %s" % (project.title, key), "bad-key")
    elif signer_text and cfg.get("sign") and signer_path and cfg.get("signing_key") and \
            os.path.normcase(os.path.abspath(cfg["signing_key"])) == os.path.normcase(os.path.abspath(signer_path)):
        e["trust_key"] = signer_text
    for k in ("description", "website", "install_hint", "install_folder"):
        if str(cfg.get(k) or "").strip():
            e[k] = one_line(str(cfg[k])).strip() if k in ("description", "install_hint") else str(cfg[k]).strip()
    if tinfo is not None:
        if "trust_key" not in e:
            raise PresetError("%s is shared by torrent, and its project file must name its publisher key: export it "
                              "with --sign (your signing key), or set trust_key" % project.title, "no-key", project.tag())
        e[TORRENT_FIELD] = {"id": tinfo["id"], "name": tinfo["name"], "seeds": list(tinfo["seeds"])}
    return e


# ---- a private file's part: what releasing a project needs -----------------------------------------------------

def _text(cfg, key):
    value = cfg.get(key)
    return "" if value is None else str(value).strip()


def _number(cfg, key, default):
    value = cfg.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def host_keys_of(cfg, log=None):
    """The server's host keys this computer trusts, from DeltaPatcher's own known_hosts for the project's host and
    port: [{"type", "key"}]; [] when it names no server, or none is trusted yet. A known_hosts that cannot be read
    gives none, with a warning: the other computer then asks about the server at its first release."""
    host = _text(cfg, "ssh_host")
    if not host:
        return []
    try:
        found = ssh.known_keys(ssh.Target(host, port=cfg.get("ssh_port") or 22))
    except ssh.SshError as e:
        (log or (lambda s: None))("Warning: the host keys of %s could not be read (%s): the computer that opens the "
                                  "file asks about the server at its first release" % (host, e))
        return []
    out = []
    for kind, blob in found:
        key = {"type": kind, "key": blob}
        if not host_key_problem(key) and key not in out:
            out.append(key)
    return out[:MAX_HOST_KEYS]


def private_part(cfg, signer_text, log=None):
    """What releasing a project needs, for a private project file: its settings of the allow-list (PRIVATE_FIELDS)
    as this computer has them, the server's host keys this computer trusts, and the public key of the signing key
    the file is signed with (signer_text). Never a path of this computer, a key or a passphrase: access_log only
    when releases go to a server over SFTP, where it is a path on the server; an address that is not an http(s)
    one, a torrent name and seeds that are not right, are left as what they mean (none). build() checks the rest."""
    from . import torrent
    publish = {k: _text(cfg, k) for k in ("name", "version", "protect", "leave_out")}
    publish.update({k: bool(cfg.get(k)) for k in ("sign", "htaccess", "share_updates", "show_files")})
    publish["signing_public_key"] = signer_text
    mode = cfg.get("deploy_mode") if cfg.get("deploy_mode") in config.CHOICES["deploy_mode"] else "sftp"
    url = _text(cfg, "public_url")
    server = {"deploy_mode": mode, "ssh_host": _text(cfg, "ssh_host"), "ssh_user": _text(cfg, "ssh_user"),
              "ssh_port": _number(cfg, "ssh_port", 22), "remote_root": _text(cfg, "remote_root"),
              "public_url": "" if address_problem(url) else url, "host_keys": host_keys_of(cfg, log)}
    if mode == "sftp":
        server["access_log"] = _text(cfg, "access_log")
    tid, name = _text(cfg, "torrent_id").lower(), _text(cfg, "torrent_name")
    shared = {"torrent": bool(cfg.get("torrent")), "torrent_id": tid if torrent.ID_RE.match(tid) else "",
              "torrent_name": "" if torrent.name_problem(name) else name,
              "seeds": ", ".join(torrent.seeds_of(cfg.get("seeds")))}
    backup = {k: _number(cfg, k, 0) for k in ("backup_keep_daily", "backup_keep_weekly", "backup_keep_monthly")}
    backup["backup_max_size"] = _text(cfg, "backup_max_size")
    return {"publish": publish, "server": server, "torrent": shared, "backup": backup}


def private_values(e, part, cfg=None):
    """The settings a private file's project gives the project it sets up or updates: its details as a public file
    gives them (an address or a key it leaves out is never taken away), and its private part, setting by setting
    (the host keys aside: they go to known_hosts, and only when the person says so). cfg: the settings of the
    project it updates, whose access log, a path on its server, is cleared when releases no longer go to one."""
    values = {k: e.get(k, "") for k in ("description", "website", "install_hint", "install_folder")}
    for k in ("source_url", "trust_key"):
        if e.get(k):
            values[k] = e[k]
    for section, fields in PRIVATE_FIELDS:
        for k in fields:
            if k in part[section] and k != "host_keys":
                values[k] = part[section][k]
    if cfg is not None and "access_log" not in part["server"] and cfg.get("deploy_mode") == "sftp" and \
            _text(cfg, "access_log"):
        values["access_log"] = ""
    return values


def _shown(value):
    if isinstance(value, bool):
        return "yes" if value else "no"
    return "" if value is None else str(value).strip()


def _same(key, old, new):
    """A setting as this computer has it is the one a private file gives: the same yes or no, number, list of seeds,
    address (letter case of the scheme and host, and a last / aside) or text."""
    from . import torrent
    if isinstance(new, bool):
        return bool(old) == new
    if isinstance(new, int):
        return isinstance(old, int) and not isinstance(old, bool) and old == new
    if key == "seeds":
        return torrent.seeds_of(old) == torrent.seeds_of(new)
    if key in ("source_url", "public_url"):
        def norm(url):
            parts = urllib.parse.urlsplit(_shown(url))
            return (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query)
        return norm(old) == norm(new)
    return _shown(old) == _shown(new)


def private_changes(cfg, e, part):
    """What a private file's project changes in the project of this computer it updates (cfg: its settings), setting
    by setting: [{"key", "label", "from", "to"}]. Nothing of this computer's own is ever in it: no folder, key file or
    seeding choice, nor a folder it updates from (source_url); and a torrent id it has is never changed."""
    out = []
    for key, new in private_values(e, part, cfg).items():
        old = cfg.get(key, config.DEFAULTS.get(key))
        if key == "source_url" and _shown(old) and _shown(old) != DEFAULT_URL and address_problem(_shown(old)):
            continue                                    # a folder of this computer
        if key == "torrent_id" and _shown(old):
            continue                                    # this project's own id: kept, whatever the file gives
        if not _same(key, old, new):
            out.append({"key": key, "label": PRIVATE_LABELS[key], "from": _shown(old), "to": _shown(new)})
    return out


def releases_here(cfg):
    """A project this computer releases: it has a folder to release (publish_src), signed releases with a signing key
    that is there, or the key its releases are signed with (signing_public_key). One that only plays (a player's,
    from a public file) does not."""
    if _text(cfg, "publish_src") or _text(cfg, "signing_public_key"):
        return True
    path = _text(cfg, "signing_key")
    return bool(cfg.get("sign")) and bool(path) and os.path.isfile(path)


def known_signing_keys(cfg):
    """The public keys a project of this computer is known to release with, found without a passphrase: the one its
    settings name (signing_public_key), its signing key's own when that file opens without one, and the one its
    prepared release (its store's signed manifest) is signed with. Never trust_key: that is the key it trusts as a
    player."""
    from . import manifest
    found = set()
    try:
        if _text(cfg, "signing_public_key"):
            found.add(signing.key_text(signing.parse_key(_text(cfg, "signing_public_key"))))
    except signing.SignError:
        pass
    path = _text(cfg, "signing_key")
    try:
        if path and os.path.isfile(path) and signing.can_sign() and not signing.needs_passphrase(path):
            found.add(signing.public_key_text(signing.load_key(path)))
    except signing.SignError:
        pass
    src = _text(cfg, "publish_src")
    store = _text(cfg, "store_dir") or (config.default_store(src) if src else "")
    if store:
        try:
            with open(os.path.join(store, manifest.MANIFEST_NAME), "rb") as f:
                raw = f.read(64 << 20)
            with open(os.path.join(store, signing.SIG_DIR, signing.sig_name(hashlib.sha256(raw).hexdigest())), "rb") as f:
                found.add(signing.key_text(signing.check_sig(f.read(4096), raw)))
        except (OSError, signing.SignError):
            pass
    return found


def _same_project(e, part, p, cfg):
    """How a project of this computer (p, its settings cfg) is a private file's project e: "id" (the same torrent id),
    "name" (the same name, and no torrent id of its own when the file's project has one: another id is another
    project, as players find a project by its id), or ''. Only names and ids are compared: no key file is read."""
    tid = part["torrent"]["torrent_id"]
    mine = _text(cfg, "torrent_id").lower()
    if tid and mine == tid:
        return "id"
    if p.title.lower() == e["title"].lower() and not (tid and mine):
        return "name"
    return ""


def private_match(e, part, candidates):
    """The project of this computer a private file's project updates in place (from (Project, settings) pairs): of
    the ones that are its project by torrent id or name (_same_project(), compared first), only one this computer
    releases (releases_here()) with the very key the file is signed with (known_signing_keys(), read for those
    alone): the one with the same torrent id, else the one with the same name. None when there is none: the file's
    project is then a new one, and a private file never changes a project it is not the releasing key of."""
    key = part["publish"]["signing_public_key"]
    for how in ("id", "name"):
        for p, cfg in candidates:
            if _same_project(e, part, p, cfg) == how and releases_here(cfg) and key in known_signing_keys(cfg):
                return p, cfg
    return None


def key_unknown(e, part, candidates):
    """A project of this computer that is a private file's project by torrent id or name (_same_project()) and
    releases here, but whose signing key DeltaPatcher cannot tell without a passphrase (known_signing_keys() finds
    none: a key with a passphrase, never released or exported here). None when there is none. The file never updates
    it: the person is told why, and how to let DeltaPatcher know the key."""
    for how in ("id", "name"):
        for p, cfg in candidates:
            if _same_project(e, part, p, cfg) == how and releases_here(cfg) and not known_signing_keys(cfg):
                return p, cfg
    return None


def private_older(made, applied):
    """A private file made at `made` comes before the one applied to a project here, made at `applied` (both UTC, as
    files write them), or is that same one again: equal times count as older. False when either is not known. The
    two times come from the clocks of two computers, which can differ: an older file is shown with both, and applied
    only when the person says so."""
    return bool(_MADE.match(made or "") and _MADE.match(applied or "")) and made <= applied


def made_words(made):
    """When a project file was made, as a person reads it: 2026-11-25 14:02 UTC."""
    return "%s %s UTC" % (made[:10], made[11:16]) if _MADE.match(made or "") else "at a time not known"


def played_here(e, part, candidates):
    """A project of this computer that plays a private file's project, one it does not release (a player's): the same
    torrent id, the same name, or the same address. None when there is none. The file never changes it; the person is
    told the file sets up releasing that project."""
    tid = part["torrent"]["torrent_id"]
    for p, cfg in candidates:
        if releases_here(cfg):
            continue
        if (tid and _text(cfg, "torrent_id").lower() == tid) or p.title.lower() == e["title"].lower() or \
                (e.get("source_url") and _same("source_url", cfg.get("source_url"), e["source_url"])):
            return p, cfg
    return None


def where_words(part):
    """Where a private file's project releases go, in words."""
    s = part["server"]
    if s["deploy_mode"] == "local":
        return "this computer, to test"
    if s["deploy_mode"] == "folder":
        return "a folder, the one you pick here"
    if not s["ssh_host"]:
        return "a server, not named yet"
    who = ("%s@" % s["ssh_user"]) if s["ssh_user"] else ""
    port = (":%d" % s["ssh_port"]) if s["ssh_port"] != 22 else ""
    return "%s%s%s%s" % (who, s["ssh_host"], port, (", " + s["remote_root"]) if s["remote_root"] else "")


def _data_json(data):
    """The projects as JSON that can sit inside the page: no <, > or & that could end the element."""
    text = json.dumps(data, indent=1, ensure_ascii=True)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _esc(text):
    return html.escape(text, quote=True)


LEAD = "A DeltaPatcher project file: it sets DeltaPatcher up to keep these folders up to date."
STEPS = """<h2>Add them to DeltaPatcher</h2>
<ol>
<li>Get DeltaPatcher from <a href="%(releases)s">its releases page</a>, if you do not have it yet.</li>
<li>Drag this file onto DeltaPatcher's window.<br>Or, on its Projects page, choose Add a project file... and pick this file.<br>A .deltapatch file opens DeltaPatcher with a double-click.</li>
<li>Pick the folder each project keeps up to date, then Add.</li>
</ol>"""
PRIVATE_LEAD = ("A private DeltaPatcher project file: it sets up releasing %(what)s on another computer of yours."
                "<br>It holds your server's address and login name, never a key or a password. Keep it to yourself.")
PRIVATE_STEPS = """<h2>Set %(them)s up on your other computer</h2>
<ol>
<li>Bring your signing key's file to that computer by hand: a USB stick, not chat or email.</li>
<li>Drag this file onto DeltaPatcher's window there.<br>Or, on its Projects page, choose Add a project file... and pick this file.<br>A .deltapatch file opens DeltaPatcher with a double-click.</li>
<li>Pick the folder to release, your signing key's file and your SSH key's file, then Set up releasing.</li>
</ol>"""


def _page(data, signer_text):
    """The page, in two parts: a signature goes between them. A private file says what it is, where each project's
    releases go, and how it is set up on another computer."""
    entries = data["projects"]
    private = data.get("private") is True
    heading = entries[0]["title"] if len(entries) == 1 else "%d projects for DeltaPatcher" % len(entries)
    if private:
        heading += " (private)"
    cards = []
    for e in entries:
        rows = ["<h2>%s</h2>" % _esc(e["title"])]
        if e.get("description"):
            rows.append("<p>%s</p>" % _esc(e["description"]))
        if e.get("install_hint"):
            rows.append('<p class="line"><span>Where it goes</span>%s</p>' % _esc(e["install_hint"]))
        if e.get("install_folder"):
            rows.append('<p class="line"><span>Its folder</span><code>%s</code> inside the folder you pick</p>'
                        % _esc(e["install_folder"]))
        if e.get("source_url"):
            rows.append('<p class="line"><span>Updates from</span><code>%s</code></p>' % _esc(e["source_url"]))
        if e.get(TORRENT_FIELD):
            t = e[TORRENT_FIELD]
            rows.append('<p class="line"><span>By torrent</span>%s; seeds: %s</p>' % (
                "too, beside its address" if e.get("source_url") else "only",
                _esc(", ".join(t["seeds"])) if t["seeds"] else "none listed"))
        if e.get("trust_key"):
            rows.append('<p class="line"><span>Publisher key</span><code>%s</code></p>' % _esc(e["trust_key"]))
        else:
            rows.append('<p class="line"><span>Publisher key</span>none given: the key of the first signed release '
                        'is trusted</p>')
        if e.get("website"):
            rows.append('<p class="line"><span>Website</span><a href="%s">%s</a></p>' % (_esc(e["website"]), _esc(e["website"])))
        if private and isinstance(e.get(PRIVATE_FIELD), dict):
            rows.append('<p class="line"><span>Released to</span>%s</p>' % _esc(where_words(e[PRIVATE_FIELD])))
        cards.append('<section class="card">\n%s\n</section>' % "\n".join(rows))
    if signer_text:
        signed = ('<p class="ok">Signed by the publisher key <code>%s</code> (%s).</p>'
                  % (_esc(signer_text), _esc(signing.fingerprint(signing.parse_key(signer_text)))))
    else:
        signed = ('<p class="warn">Not signed: anyone could have made or changed this file.<br>'
                  'Add it only if you trust where it came from.</p>')
    if private:
        signed = ('<p class="ok">Opening a newer one there updates %s in place, after showing what changes.</p>\n'
                  % ("this project" if len(entries) == 1 else "these projects")) + signed
    elif data.get("updates"):
        signed = ('<p class="ok">Update capable: the publisher can change %s later; DeltaPatcher asks you before '
                  'anything changes.</p>\n' % ("this project" if len(entries) == 1 else "these projects")) + signed
    head = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="generator" content="DeltaPatcher %(version)s">
<title>%(title)s - DeltaPatcher</title>
<style>
body{margin:0;padding:32px 16px;background:#0b0f15;color:#e2e8f0;font:15px/1.55 "Segoe UI",system-ui,-apple-system,Ubuntu,Roboto,sans-serif}
main{max-width:760px;margin:0 auto}
h1{font-size:26px;margin:0 0 4px}
h2{font-size:17px;margin:0 0 6px}
.lead{color:#8593a8;margin:0 0 20px}
.card{background:#131a24;border:1px solid #222c3b;border-radius:12px;padding:16px 20px;margin:12px 0}
.card p{margin:4px 0}
.line span{display:inline-block;min-width:120px;color:#8593a8;font-size:12px;text-transform:uppercase;letter-spacing:.05em}
code{font-family:"Cascadia Mono",Consolas,Menlo,monospace;font-size:13px;color:#c4b5fd;word-break:break-all}
a{color:#3fd1bd}
.ok{color:#3fd1bd}
.warn{color:#f7c06f}
ol{padding-left:22px}
li{margin:6px 0}
.small{color:#5b6a80;font-size:12px;margin-top:28px}
</style>
</head>
<body>
<main>
<h1>%(title)s</h1>
<p class="lead">%(lead)s</p>
%(cards)s
%(signed)s
%(steps)s
<p class="small">Made with DeltaPatcher %(version)s on %(made)s.</p>
</main>
%(open)s
%(json)s
%(close)s
""" % {"version": _esc(__version__), "title": _esc(heading), "cards": "\n".join(cards), "signed": signed,
       "lead": PRIVATE_LEAD % {"what": "this project" if len(entries) == 1 else "these projects"} if private else LEAD,
       "steps": PRIVATE_STEPS % {"them": "it" if len(entries) == 1 else "them"} if private
       else STEPS % {"releases": _esc(RELEASES)},
       "made": _esc(data["made"].replace("T", " ").replace("Z", " UTC")),
       "open": DATA_OPEN, "json": _data_json(data), "close": CLOSE}
    return head, "</body>\n</html>\n"


def build(entries, signer=None, made=None, updates=False):
    """The text of a project file holding these projects (entry() makes each), signed with `signer` (a private
    signing key) when one is given. updates=True: an update capable file (version 2); otherwise version 1, which
    every DeltaPatcher reads. A file with a project shared by torrent is version 3. A private file (each project with
    its private part: private_part()) is version 4, and must be signed with the signing key its parts name."""
    entries = list(entries)
    private = any(PRIVATE_FIELD in e for e in entries)
    shared = any(TORRENT_FIELD in e for e in entries)
    data = {"format": FORMAT, "version": 4 if private else (3 if shared else (2 if updates else 1))}
    if private:
        data["private"] = True
    data["made"] = made or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if updates or shared or private:
        data["updates"] = bool(updates)
    data["projects"] = list(entries)
    try:
        check_data(data)
    except PresetError as e:
        raise PresetError(str(e).replace("not a project file DeltaPatcher can read", "the project file cannot be made"),
                          e.code, e.data)
    signer_text = signing.public_key_text(signer) if signer is not None else ""
    if private and any(e[PRIVATE_FIELD]["publish"]["signing_public_key"] != signer_text for e in entries):
        raise PresetError("the project file cannot be made: a private project file is signed with its projects' "
                          "signing key", "unsigned" if signer is None else "other-key")
    head, tail = _page(data, signer_text)
    if signer is None:
        return head + tail
    # the signature covers the page as it reads with its own element taken out: head, a new line, tail
    signature = signer.sign(DOMAIN + (head + "\n" + tail).encode("utf-8"))
    element = SIG_OPEN + json.dumps({"signer": signer_text, "signature": base64.b64encode(signature).decode("ascii")}) + CLOSE
    return head + element + "\n" + tail


def write(path, text):
    """Write a project file whole (a temporary file first, so a reader never sees half of it)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


# ---- reading one ----------------------------------------------------------------------------------

def _element(text, opening, name):
    """(start, end) of the one element that opens with `opening`, or None; PresetError when there are more."""
    first = text.find(opening)
    if first < 0:
        return None
    if text.find(opening, first + len(opening)) >= 0:
        raise PresetError("%s holds more than one set of projects: not a project file DeltaPatcher can read" % name)
    close = text.find(CLOSE, first + len(opening))
    if close < 0:
        raise PresetError("%s is cut short: not a project file DeltaPatcher can read" % name)
    return first, close + len(CLOSE)


def _json(text, name):
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        raise PresetError("the projects in %s are not readable: not a project file DeltaPatcher can read" % name)


def parse(raw, name="the file"):
    """The projects of a project file's bytes, checked: {"projects", "made", "signer", "updates", "version",
    "private", "parts"} (signer: the key text, '' when it is not signed; updates: True for an update capable file;
    private: True for a private file, whose projects' private parts are in parts, in the order of projects, which
    are what a public file gives). PresetError when it is not one, or was changed after it was signed; a private
    file that is not signed, or not with the signing key its projects name, is refused."""
    if len(raw) > LIMIT:
        raise PresetError("%s is over 1 MB: not a project file" % name, "too-big")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PresetError("%s is not text: not a project file" % name)
    if text.startswith("\ufeff"):          # a byte order mark an editor may add
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    data_at = _element(text, DATA_OPEN, name)
    if data_at is None:
        raise PresetError("%s holds no projects: not a DeltaPatcher project file" % name)
    sig_at = _element(text, SIG_OPEN, name)
    signer = ""
    if sig_at is not None:
        if not (sig_at[1] <= data_at[0] or sig_at[0] >= data_at[1]):
            raise PresetError("%s is not a project file DeltaPatcher can read" % name)
        doc = _json(text[sig_at[0] + len(SIG_OPEN):sig_at[1] - len(CLOSE)], name)
        if not isinstance(doc, dict) or set(doc) != {"signer", "signature"} or \
                not all(isinstance(v, str) and len(v) < 200 for v in doc.values()):
            raise PresetError("the signature in %s is not readable" % name, "tampered")
        try:
            key = signing.parse_key(doc["signer"])
            signature = base64.b64decode(doc["signature"], validate=True)
        except (signing.SignError, ValueError, TypeError):
            raise PresetError("the signature in %s is not readable" % name, "tampered")
        message = DOMAIN + (text[:sig_at[0]] + text[sig_at[1]:]).encode("utf-8")
        if len(signature) != 64 or not signing.verify(key, message, signature):
            raise PresetError("%s was changed after it was signed: refused" % name, "tampered",
                              {"signer": signing.key_text(key)})
        signer = signing.key_text(key)
    data = _json(text[data_at[0] + len(DATA_OPEN):data_at[1] - len(CLOSE)], name)
    if isinstance(data, dict) and data.get("private") is not True:
        _public_texts(data)                     # a private file's texts are taken as they are: one is refused
    check_data(data)
    private = data.get("private") is True
    if private and not signer:
        raise PresetError("%s is a private project file that is not signed: refused (a private file is always signed "
                          "with its projects' signing key)" % name, "unsigned")
    if private and any(e[PRIVATE_FIELD]["publish"]["signing_public_key"] != signer for e in data["projects"]):
        raise PresetError("%s is signed with another key than the signing key of its projects: not a project file "
                          "DeltaPatcher can read" % name)
    return {"projects": [{k: v for k, v in e.items() if k != PRIVATE_FIELD} for e in data["projects"]],
            "made": data["made"], "signer": signer, "updates": bool(data.get("updates")), "version": data["version"],
            "private": private, "parts": [e[PRIVATE_FIELD] for e in data["projects"]] if private else []}


def is_private_file(path):
    """A private project file by what it holds: a .deltapatch, .html or .htm file of at most 1 MB whose page holds the
    projects of a version 4 file with "private": true (signed or not, changed or not: it may hold a server's address
    and login name either way). Publishing never sends one to players."""
    if not is_project_file(path):
        return False
    try:
        if os.path.getsize(path) > LIMIT:
            return False
        with open(path, "rb") as f:
            raw = f.read(LIMIT + 1)
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        at = _element(text, DATA_OPEN, os.path.basename(path))
        if at is None:
            return False
        data = _json(text[at[0] + len(DATA_OPEN):at[1] - len(CLOSE)], os.path.basename(path))
    except (OSError, UnicodeDecodeError, PresetError):
        return False
    version = data.get("version") if isinstance(data, dict) else None
    return isinstance(version, int) and not isinstance(version, bool) and version >= 4 and data.get("private") is True


def read(path):
    """A project file, read and checked (parse())."""
    try:
        with open(path, "rb") as f:
            raw = f.read(LIMIT + 1)
    except OSError as e:
        raise PresetError("cannot read %s: %s" % (path, e), "unreadable")
    return parse(raw, os.path.basename(path))


# ---- adding its projects --------------------------------------------------------------------------

def folder_for(e, given, name, several):
    """The folder a project of a project file keeps up to date, from the folder given for it. With several
    projects: a folder each inside it, named by the project's install_folder, else after the project. With one:
    its install_folder inside it, unless the folder given already is that one."""
    folder = e.get("install_folder") or ""
    if several:
        return os.path.join(given, folder or name)
    if folder and os.path.normcase(os.path.basename(os.path.normpath(given))) != os.path.normcase(folder):
        return os.path.join(given, folder)
    return given


def add(e, name=None, install_dir="", updates=False, made="", seed=False):
    """A new project from one entry of a project file: its address and publisher key, its description, website
    and where it goes, the folder the person picked (none: it is chosen later). From an update capable file
    (updates=True) it follows its publisher; either way the details it starts with, made when the file was
    (made), are the ones a later change is compared with. A project shared by torrent gets its torrent, and seeds
    only when the person said yes (seed). Returns the Project."""
    from .publisher import accepted_text
    from . import torrent
    p = projects.new(name or e["title"])
    values = {"source_url": e.get("source_url", ""), "trust_key": e.get("trust_key", ""),
              "description": e.get("description", ""), "website": e.get("website", ""),
              "install_hint": e.get("install_hint", ""), "install_folder": e.get("install_folder", ""),
              "follow_publisher": bool(updates), "publisher_accepted": accepted_text(e, made)}
    if e.get(TORRENT_FIELD):
        values.update(torrent.settings_of(e[TORRENT_FIELD]))
        values["seed"] = bool(seed)
    if install_dir:
        values["install_dir"] = os.path.abspath(os.path.expanduser(install_dir))
    config.save(values, p)
    return p


def add_private(e, part, name=None, here=None, install_dir="", updates=False, made="", seed=False):
    """A new project from a private file's project made at `made`: what add() takes from what a public file gives, then
    what releasing it needs (private_values()), when the file was made (project_file_made: an older one changes nothing
    later), and this computer's own folders and key files (here: publish_src, signing_key, ssh_key, deploy_folder, as
    the person gave them). Returns the Project."""
    p = add(e, name, install_dir, updates=updates, made=made, seed=seed)
    config.save(dict(private_values(e, part), project_file_made=made, **(here or {})), p)
    return p
