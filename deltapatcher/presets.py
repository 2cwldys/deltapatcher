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
each project's folder.

Signed with the publisher's signing key (Ed25519, the key that signs releases), the signature is a second
element, <script type="application/json" id="deltapatcher-signature">. It covers the whole page but that
element (line endings aside), so a file changed anywhere after it was signed is refused.
"""

import base64
import html
import json
import os
import re
import time
import unicodedata
import urllib.parse

from . import REPO, __version__, config, projects, signing
from .source import DEFAULT_URL

FORMAT = "deltapatcher-preset"
VERSION = 1
EXTENSIONS = (".deltapatch", ".html", ".htm")
LIMIT = 1 << 20
MAX_PROJECTS = 100
DATA_OPEN = '<script type="application/json" id="deltapatcher-preset">'
SIG_OPEN = '<script type="application/json" id="deltapatcher-signature">'
CLOSE = "</script>"
DOMAIN = b"DeltaPatcher project file 1\n"          # a signature over a project file is never one over a manifest
RELEASES = "https://github.com/%s/releases" % REPO
FIELDS = ("title", "source_url", "trust_key", "description", "website", "install_hint", "install_folder")
HINT_MAX = 300
LOOPBACK = ("localhost", "127.0.0.1", "::1")
# bidirectional controls, which can make a text read differently from what it holds
_BIDI = set(chr(c) for c in list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)))
_MADE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


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
    if not isinstance(text, str) or len(text) > most:
        return "a text of at most %d characters" % most
    if any(unicodedata.category(c) in ("Cc", "Cs") or c in _BIDI for c in text):
        return "a text without control characters"
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
    if version != VERSION:
        bad("version %d" % version)
    if set(data) != {"format", "version", "made", "projects"}:
        bad("fields missing or unknown")
    if not isinstance(data["made"], str) or not _MADE.match(data["made"]):
        bad("when it was made")
    entries = data["projects"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_PROJECTS:
        bad("between 1 and %d projects" % MAX_PROJECTS)
    seen = set()
    for e in entries:
        if not isinstance(e, dict) or not {"title", "source_url"} <= set(e) or not set(e) <= set(FIELDS):
            bad("a project with unknown or missing fields")
        if not isinstance(e["title"], str) or projects.name_problem(e["title"]) or e["title"] != e["title"].strip():
            bad("a project's name: %s" % (projects.name_problem(e["title"]) or "not a name"))
        if e["title"].lower() in seen:
            bad("two projects named %s" % e["title"])
        seen.add(e["title"].lower())
        problem = address_problem(e["source_url"])
        if problem:
            bad("the address of %s: %s" % (e["title"], problem))
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
    when its own releases are signed with that key."""
    source = address(cfg)
    if not source:
        raise PresetError("%s has no http(s) address players update from: set where players download it "
                          "(public_url) or where it updates from (source_url)" % project.title, "no-address", project.tag())
    e = {"title": project.title, "source_url": source}
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
            e[k] = str(cfg[k]).strip()
    return e


def _data_json(data):
    """The projects as JSON that can sit inside the page: no <, > or & that could end the element."""
    text = json.dumps(data, indent=1, ensure_ascii=True)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _esc(text):
    return html.escape(text, quote=True)


def _page(data, signer_text):
    """The page, in two parts: a signature goes between them."""
    entries = data["projects"]
    heading = entries[0]["title"] if len(entries) == 1 else "%d projects for DeltaPatcher" % len(entries)
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
        rows.append('<p class="line"><span>Updates from</span><code>%s</code></p>' % _esc(e["source_url"]))
        if e.get("trust_key"):
            rows.append('<p class="line"><span>Publisher key</span><code>%s</code></p>' % _esc(e["trust_key"]))
        else:
            rows.append('<p class="line"><span>Publisher key</span>none given: the key of the first signed release '
                        'is trusted</p>')
        if e.get("website"):
            rows.append('<p class="line"><span>Website</span><a href="%s">%s</a></p>' % (_esc(e["website"]), _esc(e["website"])))
        cards.append('<section class="card">\n%s\n</section>' % "\n".join(rows))
    if signer_text:
        signed = ('<p class="ok">Signed by the publisher key <code>%s</code> (%s).</p>'
                  % (_esc(signer_text), _esc(signing.fingerprint(signing.parse_key(signer_text)))))
    else:
        signed = ('<p class="warn">Not signed: anyone could have made or changed this file.<br>'
                  'Add it only if you trust where it came from.</p>')
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
<p class="lead">A DeltaPatcher project file: it sets DeltaPatcher up to keep these folders up to date.</p>
%(cards)s
%(signed)s
<h2>Add them to DeltaPatcher</h2>
<ol>
<li>Get DeltaPatcher from <a href="%(releases)s">its releases page</a>, if you do not have it yet.</li>
<li>Drag this file onto DeltaPatcher's window.<br>Or, on its Projects page, choose Add a project file... and pick this file.<br>A .deltapatch file opens DeltaPatcher with a double-click.</li>
<li>Pick the folder each project keeps up to date, then Add.</li>
</ol>
<p class="small">Made with DeltaPatcher %(version)s on %(made)s.</p>
</main>
%(open)s
%(json)s
%(close)s
""" % {"version": _esc(__version__), "title": _esc(heading), "cards": "\n".join(cards), "signed": signed,
       "releases": _esc(RELEASES), "made": _esc(data["made"].replace("T", " ").replace("Z", " UTC")),
       "open": DATA_OPEN, "json": _data_json(data), "close": CLOSE}
    return head, "</body>\n</html>\n"


def build(entries, signer=None, made=None):
    """The text of a project file holding these projects (entry() makes each), signed with `signer` (a private
    signing key) when one is given."""
    data = {"format": FORMAT, "version": VERSION, "made": made or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "projects": list(entries)}
    try:
        check_data(data)
    except PresetError as e:
        raise PresetError(str(e).replace("not a project file DeltaPatcher can read", "the project file cannot be made"),
                          e.code, e.data)
    signer_text = signing.public_key_text(signer) if signer is not None else ""
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
    """The projects of a project file's bytes, checked: {"projects", "made", "signer"} (signer: the key text,
    '' when it is not signed). PresetError when it is not one, or was changed after it was signed."""
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
    check_data(data)
    return {"projects": data["projects"], "made": data["made"], "signer": signer}


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


def add(e, name=None, install_dir=""):
    """A new project from one entry of a project file: its address and publisher key, its description, website
    and where it goes, the folder the person picked (none: it is chosen later). Returns the Project."""
    p = projects.new(name or e["title"])
    values = {"source_url": e["source_url"], "trust_key": e.get("trust_key", ""),
              "description": e.get("description", ""), "website": e.get("website", ""),
              "install_hint": e.get("install_hint", ""), "install_folder": e.get("install_folder", "")}
    if install_dir:
        values["install_dir"] = os.path.abspath(os.path.expanduser(install_dir))
    config.save(values, p)
    return p
