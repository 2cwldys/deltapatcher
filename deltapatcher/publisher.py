"""Projects that follow their publisher.

What a release says about its project. With share_updates on, publish puts the project's details into the
manifest's "project" object, as a project file gives them: its name (title), description, website, where it
goes (install_hint, install_folder), the address players update from (source_url) and its publisher key
(trust_key). The manifest is signed, so they are too. Beside them, always: show_files (whether DeltaPatcher
shows players the names of the files a release adds, changes and removes) and the release notes (notes, plain
text). With share_updates off, or no address players update from, the object holds only those two.
DeltaPatchers before this one never read it, and this one passes over fields it does not know, so a later one
can add to it.

A player's project compares the publisher's details with the ones it last accepted (publisher_accepted in its
settings file, with when the publisher made them: a release's published time, a project file's made time; with
none accepted yet, its own). Details made before the ones accepted are never offered, so a project file newer
than the last release is not undone by it. What the publisher changed waits for the person: Apply takes it,
Not now holds it (publisher_not_now, its hash) until the publisher changes the project again. Apply never
changes the folder, the project's name here or any other setting of the player's own. A new address or a new
publisher key is taken only from something signed with the key the project trusts now; a project that trusts
none is asked with a stronger warning. Nothing here asks: the command line and the app do.
"""

import hashlib
import json
import os
import re
import unicodedata
import urllib.parse

from . import client, config, presets, projects, signing

NOTES_MAX = 4000
DETAILS = ("description", "website", "install_hint", "install_folder")
MOVES = ("source_url", "trust_key")                 # the ones only something signed with the trusted key may change
FIELDS = DETAILS + MOVES
# each field as a line of a change names it, and as the short list of what changed says it
LABELS = {"description": "description", "website": "website", "install_hint": "where it goes",
          "install_folder": "its folder name", "source_url": "the address", "trust_key": "the publisher key"}
WORDS = {"description": "description", "website": "website", "install_hint": "where it goes",
         "install_folder": "where it goes", "source_url": "the address", "trust_key": "the publisher key"}
_TIME = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")


# ---- release notes --------------------------------------------------------------------------------

def notes_problem(text):
    """What is wrong with release notes ('' when nothing): plain text of at most NOTES_MAX characters, lines and
    tabs allowed, no other control character."""
    if not isinstance(text, str):
        return "not a text"
    if len(text) > NOTES_MAX:
        return "at most %d characters (these are %d)" % (NOTES_MAX, len(text))
    if any((c not in "\n\t" and unicodedata.category(c) in ("Cc", "Cs")) or c in presets._BIDI for c in text):
        return "plain text, without control characters"
    return ""


def clean_notes(text):
    """Release notes as a release keeps them: line ends as one \\n, no spaces at the end of a line, no empty
    lines before or after. ValueError when they are not plain text or too long."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n")).strip("\n")
    problem = notes_problem(text)
    if problem:
        raise ValueError("the release notes: %s" % problem)
    return text


# ---- what a release says about its project --------------------------------------------------------

def _detail_problem(key, value):
    if key in ("description", "install_hint"):
        return presets.text_problem(value, 500 if key == "description" else presets.HINT_MAX)
    if key == "website":
        return presets.address_problem(value)
    return projects.folder_name_problem(value)


def release_object(project, cfg, signer_text="", notes="", log=None):
    """The "project" object of a release of this project (cfg: its settings): its details, when share_updates is
    on and it has an address players update from; then show_files and the notes. The publisher key is the one
    the project names (trust_key: a new key is announced in a release signed with the old one), else the key
    signing this release. A detail a project file could not hold is left out, with a warning."""
    log = log or (lambda s: None)
    obj = {}
    address = presets.address(cfg) if cfg.get("share_updates", True) else ""
    if address:
        if not projects.name_problem(project.title) and project.title == project.title.strip():
            obj["title"] = project.title
        for k in DETAILS:
            value = str(cfg.get(k) or "").strip()
            if value:
                problem = _detail_problem(k, value)
                if problem:
                    log("Warning: %s is left out of the release's details: %s" % (LABELS[k], problem))
                else:
                    obj[k] = value
        obj["source_url"] = address
        key = str(cfg.get("trust_key") or "").strip()
        if key and key.lower() != "off":
            try:
                obj["trust_key"] = signing.key_text(signing.parse_key(key))
            except signing.SignError:
                log("Warning: trust_key is not a publisher key: the release names %s instead"
                    % ("the key signing it" if signer_text else "no publisher key"))
        if "trust_key" not in obj and signer_text:
            obj["trust_key"] = signer_text
        if (urllib.parse.urlsplit(address).hostname or "").lower() in presets.LOOPBACK:
            log("Warning: the release gives players %s, an address on this computer: set public_url to the address "
                "players update from" % address)
    obj["show_files"] = bool(cfg.get("show_files", True))
    if notes:
        obj["notes"] = notes
    return obj


def read_object(raw):
    """A manifest's "project" object, checked as the untrusted input it is: {} when there is none; ValueError
    when a field this DeltaPatcher knows is not right. Fields it does not know are passed over."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("it is not an object")
    out = {}
    for k, v in raw.items():
        if k == "title":
            problem = (projects.name_problem(v) or ("" if v == v.strip() else "spaces around it")) \
                if isinstance(v, str) else "not a name"
        elif k in ("source_url", "website"):
            problem = presets.address_problem(v)
        elif k == "trust_key":
            try:
                problem = "" if isinstance(v, str) and signing.key_text(signing.parse_key(v)) == v else "not a key"
            except signing.SignError:
                problem = "not a key"
        elif k in ("description", "install_hint", "install_folder"):
            problem = _detail_problem(k, v) if isinstance(v, str) else "not a text"
        elif k == "show_files":
            problem = "" if isinstance(v, bool) else "not yes or no"
        elif k == "notes":
            problem = notes_problem(v)
        else:
            continue
        if problem:
            raise ValueError("%s: %s" % (LABELS.get(k, k.replace("_", " ")), problem))
        out[k] = v
    return out


def has_details(obj):
    """The object says what the project is (share_updates was on), not only what players are shown."""
    return any(k in obj for k in ("title",) + FIELDS)


# ---- a player's project and the publisher's details -----------------------------------------------

def offered(obj):
    """The details a project object or a project file's project gives, as they are compared: each field,
    '' when it gives none."""
    return {k: str(obj.get(k) or "") for k in FIELDS}


def accepted_text(obj, at=""):
    """The details as publisher_accepted keeps them, one line of JSON, with when the publisher made them (at)."""
    doc = offered(obj)
    if _TIME.match(at or ""):
        doc["at"] = at
    return json.dumps(doc, sort_keys=True, ensure_ascii=True)


def _accepted(cfg):
    try:
        doc = json.loads(str(cfg.get("publisher_accepted") or ""))
    except ValueError:
        return None
    if not isinstance(doc, dict) or not all(isinstance(doc.get(k, ""), str) for k in FIELDS + ("at",)):
        return None
    return doc


def accepted_of(cfg):
    """The details a project last accepted (publisher_accepted), or None when it has accepted none yet."""
    doc = _accepted(cfg)
    return None if doc is None else {k: doc.get(k, "") for k in FIELDS}


def accepted_at(cfg):
    """When the publisher made the details a project last accepted ('' when that is not known)."""
    at = (_accepted(cfg) or {}).get("at", "")
    return at if _TIME.match(at) else ""


def older(when, since):
    """Details made at `when` come before the ones accepted, made at `since` (both UTC, as releases and project
    files write them); False when either is not known."""
    return bool(_TIME.match(when or "") and _TIME.match(since or "")) and when < since


def offer_hash(offer):
    """A short, stable name of a set of details: what Not now remembers, and what a person confirmed."""
    return hashlib.sha256(json.dumps(offered(offer), sort_keys=True).encode("utf-8")).hexdigest()


def _address_key(url):
    url = str(url or "").strip()
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() in ("http", "https"):
        path = parts.path if parts.path.endswith("/") else parts.path + "/"
        return "%s://%s%s%s" % (parts.scheme.lower(), parts.netloc.lower(), path, ("?" + parts.query) if parts.query else "")
    return os.path.normcase(os.path.abspath(os.path.expanduser(url)))


def same_address(a, b):
    """Two addresses (or folders) that are one source: letter case of the scheme and host and a last / aside."""
    return _address_key(a) == _address_key(b)


def trusted_key(trust_key, install=""):
    """The publisher key a project trusts now ("ed25519:..."): its trust_key, else the one its folder pinned
    from the first signed release it took; '' when it trusts none, or its checks are off."""
    t = str(trust_key or "").strip()
    if t.lower() == "off":
        return ""
    try:
        if t:
            return signing.key_text(signing.parse_key(t))
        pinned = client.State(os.path.abspath(install)).publisher_key if install else ""
        return signing.key_text(signing.parse_key(pinned)) if pinned else ""
    except signing.SignError:
        return ""


def current_of(cfg, trusted):
    """A project's details as they are now; its publisher key is the one it trusts."""
    now = {k: str(cfg.get(k) or "").strip() for k in DETAILS}
    now["source_url"] = str(cfg.get("source_url") or "").strip()
    now["trust_key"] = trusted
    return now


def _same(key, a, b):
    return same_address(a, b) if key == "source_url" else a == b


def _warning(trust, moves, via):
    lines = []
    if "source_url" in moves:
        lines.append("It moves the project to another address: it updates from there from now on.")
    if "trust_key" in moves:
        lines.append("It names another publisher key: only releases signed with it are installed from now on.")
    if trust == "signed":
        lines.append("It is signed with the publisher key this project trusts.")
    elif trust == "refused":
        lines.append("It is not signed with the publisher key this project trusts, so the new %s is not applied."
                     % (" and ".join(LABELS[m].replace("the ", "") for m in moves)))
    elif via == "file":
        lines.append("This project trusts no publisher key: anyone could have made or changed this file.")
        lines.append("Apply it only if you trust where it came from.")
    else:
        lines.append("This project trusts no publisher key: anyone who can change that server could have sent this.")
        lines.append("Apply it only if you trust where it came from.")
    return "\n".join(lines)


def assess(current, offer, accepted=None, signer="", trusted="", via="release"):
    """What the publisher's details change in a project, or None when nothing: each field the publisher changed
    since the details last accepted (none yet: the project's own) that the project does not have already. An
    address or a key the publisher leaves out is never taken away. via: "release" (a manifest) or "file" (a
    project file), for the words; signer: the key that signed it ('' unsigned); trusted: the key the project
    trusts now. "trust" says whether a new address or key may be applied: signed (by the trusted key), unsigned
    (the project trusts none: asked with a stronger warning) or refused."""
    base = accepted if accepted is not None else current
    items = []
    for k in FIELDS:
        new = offer.get(k, "")
        if k in MOVES and not new:
            continue
        if _same(k, new, base.get(k, "")) or _same(k, new, current.get(k, "")):
            continue
        items.append({"field": k, "label": LABELS[k], "from": current.get(k, ""), "to": new})
    if not items:
        return None
    moves = [i["field"] for i in items if i["field"] in MOVES]
    trust = ""
    if moves:
        trust = "signed" if trusted and signer == trusted else ("refused" if trusted else "unsigned")
    words = []
    for i in items:
        if WORDS[i["field"]] not in words:
            words.append(WORDS[i["field"]])
    return {"changes": items, "words": ", ".join(words), "hash": offer_hash(offer), "moves": moves, "trust": trust,
            "warning": _warning(trust, moves, via) if moves else "", "signer": signer, "trusted": trusted,
            "applicable": [i["field"] for i in items if not (trust == "refused" and i["field"] in MOVES)]}


def change_of(cfg, obj, signer="", trusted="", via="release", when=""):
    """What a project object (read_object), made at `when`, changes in a project (cfg: its settings), or None:
    also when it was made before the details the project accepted last."""
    if not has_details(obj) or older(when, accepted_at(cfg)):
        return None
    return assess(current_of(cfg, trusted), offered(obj), accepted_of(cfg), signer, trusted, via)


def held(cfg, change):
    """The person answered Not now to this very change."""
    return bool(change) and change["hash"] == str(cfg.get("publisher_not_now") or "").strip()


def apply(project, cfg, obj, change, trusted="", at=""):
    """Take what a change applies: the fields it changes (never a refused address or key), and the publisher's
    details, made at `at`, as the ones accepted. Nothing else of the project changes: its folder, its name here
    and every other setting stay. Returns the fields that changed."""
    offer = offered(obj)
    values = {}
    if change:
        values = {i["field"]: i["to"] for i in change["changes"] if i["field"] in change["applicable"]}
        base = accepted_of(cfg) or current_of(cfg, trusted)
        for i in change["changes"]:
            if i["field"] not in change["applicable"]:
                offer[i["field"]] = base[i["field"]]          # refused: asked again once it comes signed
    config.save(dict(values, publisher_accepted=accepted_text(offer, at), publisher_not_now=""), project)
    return values


def not_now(project, change):
    """Not now: this change is not asked again until the publisher changes the project again."""
    config.save({"publisher_not_now": change["hash"]}, project)


def match(entry, candidates):
    """The project of this computer a project file's project updates, from (Project, settings) pairs: the same
    address and the same publisher key or name, or the same publisher key and name. None when there is none."""
    found = None
    for p, cfg in candidates:
        key = trusted_key(cfg.get("trust_key"))
        same_key = bool(key) and key == entry.get("trust_key", "")
        same_name = p.title.lower() == entry["title"].lower()
        if (same_address(cfg.get("source_url"), entry["source_url"]) and (same_key or same_name)) or (same_key and same_name):
            if same_name:
                return p, cfg
            found = found or (p, cfg)
    return found


def shown(value):
    """A detail as a line of a change shows it."""
    return value if value else "(none)"
