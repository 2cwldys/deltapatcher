"""Private project files: a publisher's own, to keep changing and releasing a project from any computer of theirs.
What one holds (an allow-list of settings, never a path, a key or a passphrase), what is refused, setting one up on
another computer and releasing from there, updating it in place (only by the project's own releasing key; an older
file only when the person says so), the server's host keys, the folder a release is made from, and a player given one.

No test here reads a key file or a settings file of this computer: the default key paths, DeltaPatcher's own folder
and known_hosts are the test's own, and a key file read outside the test's folder fails the test."""

import base64
import contextlib
import io
import json
import os
import shutil
import socket
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, snapshot, write  # noqa: E402  (first: settings and logs go to a sandbox)
from deltapatcher import (cli, config, deploy, mcp_server, presets, projects, publish, publisher, serve,  # noqa: E402
                          signing, ssh, torrent)
from deltapatcher.presets import PresetError  # noqa: E402
from deltapatcher.torrent import item as torrent_item  # noqa: E402

PASSPHRASE = "a long passphrase for the test key"
TID = "0123456789abcdef0123456789abcdef"
LS = "\u2028"                                   # LINE SEPARATOR: str.splitlines() ends a line at it
PS = "\u2029"                                   # PARAGRAPH SEPARATOR: so does it

# the settings whose values a private file may hold: the project's details as a public file carries them, and the
# allow-list of what releasing it needs (signing_public_key is the signing key's public key, which signs the file)
ALLOWED = {
    "title", "description", "website", "install_hint", "install_folder", "source_url", "trust_key", "public_url",
    "name", "version", "protect", "leave_out", "sign", "htaccess", "share_updates", "show_files", "signing_public_key",
    "deploy_mode", "ssh_host", "ssh_user", "ssh_port", "remote_root", "access_log",
    "torrent", "torrent_id", "torrent_name", "seeds",
    "backup_keep_daily", "backup_keep_weekly", "backup_keep_monthly", "backup_max_size",
}
# what a public file holds of a project
ENTRY = {"title", "source_url", "trust_key", "description", "website", "install_hint", "install_folder", "torrent"}
# the private part, section by section, exactly
PART = {
    "publish": {"name", "version", "protect", "leave_out", "sign", "htaccess", "share_updates", "show_files",
                "signing_public_key"},
    "server": {"deploy_mode", "ssh_host", "ssh_user", "ssh_port", "remote_root", "public_url", "access_log",
               "host_keys"},
    "torrent": {"torrent", "torrent_id", "torrent_name", "seeds"},
    "backup": {"backup_keep_daily", "backup_keep_weekly", "backup_keep_monthly", "backup_max_size"},
}
# what a computer keeps of its own: never in the file, never changed by one
OWN = ("publish_src", "store_dir", "signing_key", "ssh_key", "deploy_folder", "backup_dir", "install_dir", "seed",
       "seed_copy", "backup_every_hours")


def have(name):
    try:
        ssh.tool(name)
        return True
    except ssh.SshError:
        return False


HAVE_SSH_KEYGEN = have("ssh-keygen")


def run_json(argv, stdin=""):
    """cli.main in --json mode: (exit code, [events])."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), mock.patch.object(sys, "stdin", io.StringIO(stdin)):
        rc = cli.main(["--json", "--no-log"] + argv)
    return rc, [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def host_key():
    """A host key as OpenSSH writes it: (its type, its base64 blob)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    line = ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.OpenSSH,
                                                                         serialization.PublicFormat.OpenSSH)
    kind, blob = line.decode("ascii").split()[:2]
    return kind, blob


class Sent(object):
    """What a deploy answers, for a test that must not reach a server."""
    named, had, sent, sent_bytes, removed, atomic, dry_run = 1, 0, 1, 10, 0, True, False
    parts_sent, parts_removed = 0, 0

    def summary(self):
        return "sent 1 blob"


@unittest.skipUnless(signing.can_sign(), "needs cryptography")
class Base(TempCase):
    def setUp(self):
        # the environment goes back after the test as it was before it, DELTAPATCHER_CONFIG too (TempCase and
        # computer() set it): taken before TempCase.setUp, so no later test inherits this one's settings file
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        TempCase.setUp(self)
        # this computer's own folders stay out of reach: DeltaPatcher's folder, the default key paths, the home folder
        own = self.p("own")
        os.environ.update({"APPDATA": os.path.join(own, "AppData"), "XDG_CONFIG_HOME": os.path.join(own, "config"),
                           "HOME": os.path.join(own, "home"), "USERPROFILE": os.path.join(own, "home")})
        self.exports = 0
        keys = {"signing_key": os.path.join(own, "AppData", "DeltaPatcher", "signing-key.pem"),
                "ssh_key": os.path.join(own, "home", ".ssh", "deltapatcher_ed25519")}
        schema = [(s, k, keys.get(k, d), w) for s, k, d, w in config.SCHEMA]        # what a new settings file holds
        for patcher in (mock.patch.dict(config.DEFAULTS, keys), mock.patch.object(config, "SCHEMA", schema)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.keys_read = []
        for name in ("needs_passphrase", "load_key"):
            real = getattr(signing, name)

            def guarded(path, *args, _real=real):
                self.assertTrue(os.path.normcase(os.path.abspath(path)).startswith(os.path.normcase(self.tmp) + os.sep),
                                "a key file outside the test's folder was read: %s" % path)
                self.keys_read.append(os.path.abspath(path))
                return _real(path, *args)
            patcher = mock.patch.object(signing, name, side_effect=guarded)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.key = self.p("a", "keys", "signing-key.pem")
        self.public = signing.generate_key(self.key, PASSPHRASE)
        self.known_hosts = ""
        patcher = mock.patch.object(ssh, "default_known_hosts", side_effect=lambda: self.known_hosts)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.computer("a")

    def computer(self, name):
        """Settings and DeltaPatcher's own known_hosts of their own, as on another computer."""
        os.environ["DELTAPATCHER_CONFIG"] = self.p(name, "settings", "deltapatcher.ini")
        self.known_hosts = self.p(name, "settings", "known_hosts")

    def make(self, title, **values):
        p = projects.new(title)
        config.save(values, p)
        return p

    def publisher(self, title="Game", **values):
        settings = dict(public_url="https://game.example.com/", sign=True, signing_key=self.key,
                        publish_src=self.p("a", "src"), deploy_mode="sftp", ssh_host="game.example.com",
                        ssh_user="deploy", remote_root="/var/www/game", protect="config/user.cfg; saves/",
                        leave_out="screenshots/", name="Game", version="1.0", description="A game")
        settings.update(values)
        return self.make(title, **settings)

    def export(self, out, *names, **kw):
        """A private export; each one of a test made a minute after the one before (a file's time is to the second,
        and a file made at the time of the one applied is an older one)."""
        argv = ["project", "export"] + list(names) + ["--out", out, "--private", "--passphrase-stdin"] + kw.get("extra", [])
        self.exports += 1
        real, ahead = time.gmtime, 60 * self.exports
        with mock.patch("time.gmtime", side_effect=lambda *secs: real(*secs) if secs else real(time.time() + ahead)):
            return run_json(argv, kw.get("passphrase", PASSPHRASE) + "\n")

    def exported(self, out, *names, **kw):
        rc, ev = self.export(out, *names, **kw)
        self.assertEqual(rc, 0, ev[-1])
        return ev[-1]["data"]

    def remade(self, path, out, made, change=None, key=None, passphrase=PASSPHRASE):
        """The private file at path made again with another time (and a change to its projects), signed with the
        same key (or `key`) as an export signs it."""
        found = presets.read(path)
        entries = [dict(e, private=json.loads(json.dumps(part))) for e, part in zip(found["projects"], found["parts"])]
        if change:
            change(entries)
        signer = signing.load_key(key or self.key, passphrase)
        presets.write(out, presets.build(entries, signer, made=made, updates=found["updates"]))
        return out

    def trust(self, host, port, keys):
        """Host keys kept in this computer's known_hosts, as ssh trust keeps them once confirmed."""
        ssh.trust(ssh.Target(host, port=port), [(k, b, ssh.fingerprint(b)) for k, b in keys])

    def imported(self, path, *args):
        rc, ev = run_json(["project", "import", path] + list(args))
        self.assertEqual(rc, 0, ev[-1])
        return ev[-1]["data"]


class AllowList(Base):
    def marker(self, key, default, n):
        """A value for every setting that says which one it is: its name in it, where a text can carry one."""
        word = "marker-" + key.replace("_", "-")
        given = {
            "title": word, "website": "https://%s.example.com/" % word, "source_url": "https://%s.example.com/" % word,
            "public_url": "https://%s.example.com/" % word, "install_folder": word, "deploy_mode": "sftp",
            "ssh_host": word + ".example.com", "remote_root": "/srv/" + word, "access_log": "/var/log/" + word,
            "torrent_id": TID, "torrent_name": word, "seeds": word + ".example.com:6881", "backup_max_size": "777 MB",
            "torrents": "refuse", "protect": word + "/", "leave_out": word + "/", "project_file_made": "2026-01-02T03:04:05Z",
            "signing_key": self.key, "signing_public_key": self.public, "trust_key": self.other,
        }
        if key in given:
            return given[key]
        if isinstance(default, bool):
            return not default
        if isinstance(default, int):
            return 7000 + n
        if key in ("publish_src", "store_dir", "ssh_key", "deploy_folder", "backup_dir", "install_dir", "seed_copies",
                   "project_files", "current_project"):
            return self.p(word, "here")
        return word

    def test_only_the_allow_list_travels(self):
        moved = self.p("marker-signing-key", "signing-key.pem")       # the signing key's path says what it is too
        os.makedirs(os.path.dirname(moved))
        shutil.copyfile(self.key, moved)
        self.key = moved
        self.other = signing.generate_key(self.p("other", "signing-key.pem"), "")
        markers = {key: self.marker(key, default, n) for n, (_s, key, default, _w) in enumerate(config.SCHEMA)}
        config.save(markers)                                         # Default: every setting, project and main
        kind, blob = host_key()
        if HAVE_SSH_KEYGEN:                                          # known_hosts is read with ssh-keygen
            self.trust("marker-ssh-host.example.com", markers["ssh_port"], [(kind, blob)])
        out = self.p("out", "marker (private).deltapatch")
        data = self.exported(out, "marker-title")
        self.assertEqual((data["private"], data["signed"], data["signer"]), (True, True, self.public))
        text = read(out).decode("utf-8")

        found = presets.read(out)
        self.assertEqual((found["version"], found["private"], found["signer"]), (4, True, self.public))
        self.assertIn('"private": true', text.split(presets.DATA_OPEN)[1][:200])     # at the top
        entry = found["projects"][0]
        self.assertLessEqual(set(entry), ENTRY)                                       # what a public file holds
        part = found["parts"][0]
        self.assertEqual({section: set(fields) for section, fields in part.items()}, PART)   # the allow-list, exactly
        for section, keys in PART.items():
            for key in keys - {"host_keys"}:
                self.assertEqual(part[section][key], markers[key], key)                 # as this computer has them
        self.assertEqual(part["server"]["host_keys"], [{"type": kind, "key": blob}] if HAVE_SSH_KEYGEN else [])

        for _s, key, _d, _w in config.SCHEMA:                       # nothing else: no setting outside the allow-list
            if key not in ALLOWED and isinstance(markers[key], str):
                self.assertNotIn(markers[key] if key == "project_file_made" else "marker-" + key.replace("_", "-"), text, key)
        for never in (self.tmp, self.tmp.replace("\\", "\\\\"), self.tmp.replace("\\", "/"), PASSPHRASE, "PRIVATE KEY",
                      "BEGIN", read(self.key).decode("ascii").splitlines()[1]):
            self.assertNotIn(never, text)                           # no path at all, no key, no passphrase


class TwoComputers(Base):
    def serve(self, folder, port):
        srv = serve.make_server(folder, "127.0.0.1", port, quiet=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)

    def test_set_up_on_b_releases_what_a_players_take(self):
        src = self.p("a", "src")
        write(os.path.join(src, "game.pk3"), os.urandom(3000))
        write(os.path.join(src, "config", "user.cfg"), b"volume=5\n")
        www = self.p("www")                                       # the server both computers send to
        free = socket.socket()
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
        free.close()
        url = "http://127.0.0.1:%d/" % port
        self.publisher(public_url=url, deploy_mode="folder", deploy_folder=www, ssh_host="", ssh_user="", remote_root="",
                       protect="config/user.cfg", leave_out="")
        rc, ev = run_json(["--project", "Game", "config", "set", "torrent=yes", "seeds=203.0.113.10:6881"])
        self.assertEqual(rc, 0, ev[-1])
        tid = config.load(projects.resolve("Game"))["torrent_id"]
        rc, ev = run_json(["--project", "Game", "release", "--sign", "--passphrase-stdin", "--version", "1.0"],
                          PASSPHRASE + "\n")
        self.assertEqual(rc, 0, ev[-1])
        self.serve(www, port)
        rc, ev = run_json(["project", "export", "Game", "--out", self.p("game.deltapatch"), "--sign",
                           "--passphrase-stdin"], PASSPHRASE + "\n")
        self.assertEqual(rc, 0, ev[-1])
        self.exported(self.p("game (private).deltapatch"), "Game")
        self.assertEqual(config.load(projects.resolve("Game"))["signing_public_key"], self.public)  # A knows its own

        self.computer("player")                                   # a player of A's releases
        config.save({"torrents": "refuse"})                       # the address only: no BitTorrent session here
        self.imported(self.p("game.deltapatch"), "--folder", self.p("player", "game"), "--yes")
        rc, ev = run_json(["--project", "Game", "update"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(snapshot(self.p("player", "game")), snapshot(src))
        write(self.p("player", "game", "config", "user.cfg"), b"volume=9\n")     # the player's own setting

        self.computer("b")                                        # the publisher's other computer
        key_b = self.p("b", "keys", "signing-key.pem")
        os.makedirs(os.path.dirname(key_b))
        shutil.copyfile(self.key, key_b)                          # carried by hand
        src_b = self.p("b", "src")
        shutil.copytree(src, src_b)
        write(os.path.join(src_b, "maps", "harbour.map"), os.urandom(2000))
        write(os.path.join(src_b, "config", "user.cfg"), b"volume=7\n")
        data = self.imported(self.p("game (private).deltapatch"), "--publish-src", src_b, "--signing-key", key_b,
                             "--deploy-folder", www, "--yes")
        self.assertEqual([a["project"] for a in data["added"]], ["Game"])
        cfg = config.load(projects.resolve("Game"))
        self.assertEqual((cfg["publish_src"], cfg["signing_key"], cfg["deploy_folder"], cfg["deploy_mode"]),
                         (src_b, key_b, www, "folder"))
        self.assertEqual((cfg["torrent"], cfg["torrent_id"], cfg["seeds"], cfg["protect"], cfg["sign"]),
                         (True, tid, "203.0.113.10:6881", "config/user.cfg", True))
        self.assertEqual((cfg["signing_public_key"], cfg["public_url"], cfg["seed"]), (self.public, url, False))
        rc, ev = run_json(["--project", "Game", "release", "--sign", "--passphrase-stdin", "--version", "1.1"],
                          PASSPHRASE + "\n")
        self.assertEqual(rc, 0, ev[-1])
        item = torrent_item.read(read(os.path.join(www, "release.item")), self.public, tid)
        self.assertTrue(item.seq)                                 # B's release: the same key, the same torrent id

        self.computer("player")
        rc, ev = run_json(["--project", "Game", "update"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(read(self.p("player", "game", "maps", "harbour.map")), read(os.path.join(src_b, "maps", "harbour.map")))
        self.assertEqual(read(self.p("player", "game", "config", "user.cfg")), b"volume=9\n")   # A's rule holds on B's


class Refused(Base):
    def setUp(self):
        Base.setUp(self)
        self.publisher()
        self.out = self.p("game (private).deltapatch")
        self.exported(self.out, "Game")
        self.raw = read(self.out)
        self.data = json.loads(self.raw.decode("utf-8").split(presets.DATA_OPEN)[1].split(presets.CLOSE)[0])

    def signed(self, data):
        """A page holding `data`, signed with the project's signing key as a project file is: only what the data
        says can be refused, never its signature."""
        key = signing.load_key(self.key, PASSPHRASE)
        head = "<!doctype html><html><body>\n%s%s%s\n" % (presets.DATA_OPEN, presets._data_json(data), presets.CLOSE)
        tail = "</body></html>\n"
        signature = key.sign(presets.DOMAIN + (head + "\n" + tail).encode("utf-8"))
        element = presets.SIG_OPEN + json.dumps({"signer": signing.public_key_text(key),
                                                 "signature": base64.b64encode(signature).decode("ascii")}) + presets.CLOSE
        return (head + element + "\n" + tail).encode("utf-8")

    def changed(self, edit):
        data = json.loads(json.dumps(self.data))
        edit(data)
        return self.signed(data)

    def refused(self, raw, code=None):
        with self.assertRaises(PresetError) as e:
            presets.parse(raw)
        if code:
            self.assertEqual(e.exception.code, code)
        return str(e.exception)

    def test_a_private_file_is_signed_and_unchanged(self):
        self.assertTrue(presets.parse(self.raw)["private"])
        self.assertTrue(presets.parse(self.signed(self.data))["private"])           # the helper signs as export does
        at = self.raw.index(presets.SIG_OPEN.encode())
        end = self.raw.index(presets.CLOSE.encode(), at) + len(presets.CLOSE) + 1
        self.assertIn("not signed", self.refused(self.raw[:at] + self.raw[end:], "unsigned"))     # its signature gone
        changed = self.raw.replace(b"game.example.com", b"evil.example.com")
        self.refused(changed, "tampered")
        self.computer("b")
        rc, ev = run_json(["project", "import", self.out, "--signing-key", self.p("b", "keys", "nothing.pem"), "--yes"])
        self.assertEqual(rc, 0, ev[-1])
        write(self.out, changed)
        rc, ev = run_json(["project", "import", self.out, "--yes"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "tampered"))
        self.assertEqual([p.title for p in projects.all_projects()], ["Default", "Game"])    # nothing more

    def test_version_4_is_private_only(self):
        def top(**change):
            def edit(d):
                d.update(change)
            return edit

        def without(key):
            def edit(d):
                del d[key]
            return edit

        def no_parts(d):
            for e in d["projects"]:
                del e["private"]
        for edit in (without("private"), top(private=False), top(private="yes"), top(private=1), top(version=3),
                     top(version=2), no_parts, without("updates")):
            self.refused(self.changed(edit))
        self.data["version"] = 3                                  # a public file never holds a private part
        del self.data["private"]
        self.refused(self.signed(self.data))
        no_parts(self.data)
        self.assertFalse(presets.parse(self.signed(self.data))["private"])         # without one, it is public

    def test_the_private_part_is_checked_field_by_field(self):
        def part(section, **change):
            def edit(d):
                d["projects"][0]["private"][section].update(change)
            return edit

        def drop(section, key):
            def edit(d):
                del d["projects"][0]["private"][section][key]
            return edit
        for change in (part("publish", publish_src=self.p("src")),               # unknown: refused, not ignored
                       part("server", ssh_key=self.p("id_ed25519")), part("server", deploy_folder=self.p("www")),
                       part("torrent", seed=True), part("backup", backup_dir=self.p("backups")),
                       part("backup", backup_every_hours=24),                    # each computer's own schedule
                       part("server", password="x"), drop("publish", "sign"), drop("server", "host_keys"),
                       part("publish", protect="C:\\game\\saves"), part("publish", protect="saves; D:/x"),   # paths
                       part("publish", leave_out="/home/me/game/notes.txt"), part("publish", leave_out="../x"),
                       part("publish", protect="\\\\server\\share"), part("publish", protect="~/saves"),
                       part("server", deploy_mode="folder"),                    # access_log: a path on this computer
                       part("server", ssh_host="game example com"), part("server", ssh_user="de ploy"),
                       part("server", ssh_host="game.example.com\n"), part("server", ssh_user="deploy\n"),
                       part("server", ssh_port=0), part("server", ssh_port="22"), part("server", ssh_port=True),
                       part("server", remote_root="/var/www/'game'"), part("server", public_url="ftp://x.example.com/"),
                       part("server", host_keys=[{"type": "ssh-ed25519", "key": "AAAA\nx"}]),
                       part("server", host_keys=[{"type": "ssh-rsa", "key": host_key()[1]}]),   # not of its type
                       part("server", host_keys=[{"type": "ssh-ed25519", "key": host_key()[1], "comment": "x"}]),
                       part("server", host_keys="ssh-ed25519 AAAA"),
                       part("torrent", torrent_id="not an id"), part("torrent", seeds="no port"),
                       part("torrent", torrent_id=TID + "\n"), part("torrent", torrent_name="release\n"),
                       part("torrent", torrent_name="../x"), part("torrent", torrent="yes"),
                       part("backup", backup_keep_daily=-1), part("backup", backup_max_size="lots"),
                       part("publish", name="two\nlines"), part("publish", sign="yes"),
                       part("publish", signing_public_key=signing.generate_key(self.p("k2.pem"), ""))):
            self.refused(self.changed(change))
        kind, blob = host_key()                                    # and a good one reads
        found = presets.parse(self.changed(part("server", host_keys=[{"type": kind, "key": blob}])))
        self.assertEqual(found["parts"][0]["server"]["host_keys"], [{"type": kind, "key": blob}])

        def folder_mode(d):                                        # a folder release: no access log in the file
            server = d["projects"][0]["private"]["server"]
            server["deploy_mode"] = "folder"
            del server["access_log"]
        self.assertEqual(presets.parse(self.changed(folder_mode))["parts"][0]["server"]["deploy_mode"], "folder")

    def test_no_text_can_start_a_line_of_its_own(self):
        planted = "Game" + LS + "publish_src = C:/x"               # would be a setting of its own in the settings file
        for section, key in (("publish", "name"), ("server", "remote_root"), ("publish", "version")):
            def edit(d, section=section, key=key):
                d["projects"][0]["private"][section][key] = planted
            self.assertIn("control characters", self.refused(self.changed(edit)))
        for c in "\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029\r\n":           # every separator str.splitlines() knows
            self.assertTrue(presets.text_problem("a%sb" % c, 100), repr(c))
            self.assertEqual(len(("a%sb" % c).splitlines()), 2)

        def entry(d):                                              # the public part too
            d["projects"][0]["description"] = planted
        self.refused(self.changed(entry))

    def test_what_cannot_be_exported_says_why(self):
        rc, ev = run_json(["--project", "Game", "config", "set", "protect=" + self.p("a", "src", "saves")])
        self.assertEqual(rc, 0, ev[-1])
        rc, ev = self.export(self.p("no.deltapatch"), "Game")      # a path of this computer: never in a file
        self.assertEqual(rc, 1)
        self.assertIn("players keep", ev[-1]["message"])
        self.assertFalse(os.path.exists(self.p("no.deltapatch")))
        config.save({"protect": "saves/"}, projects.resolve("Game"))

        self.publisher("Unsigned", sign=False, public_url="https://unsigned.example.com/")
        rc, ev = self.export(self.p("no.deltapatch"), "Unsigned")
        self.assertEqual((rc, ev[-1]["code"]), (1, "not-signed"))
        self.publisher("Keyless", signing_key=self.p("nowhere", "signing-key.pem"), public_url="https://keyless.example.com/")
        rc, ev = self.export(self.p("no.deltapatch"), "Keyless")
        self.assertEqual((rc, ev[-1]["code"]), (1, "no-signing-key"))
        other = self.p("b", "keys", "signing-key.pem")
        signing.generate_key(other, PASSPHRASE)
        self.publisher("Other", signing_key=other, public_url="https://other.example.com/")
        rc, ev = self.export(self.p("no.deltapatch"), "Game", "Other")
        self.assertEqual((rc, ev[-1]["code"]), (1, "mixed-keys"))
        rc, ev = self.export(self.p("no.deltapatch"), "Game", extra=["--sign", other])
        self.assertEqual((rc, ev[-1]["code"]), (1, "other-key"))
        self.assertFalse(os.path.exists(self.p("no.deltapatch")))
        with self.assertRaises(PresetError):                       # and never unsigned, whoever builds it
            found = presets.read(self.out)
            presets.build([dict(found["projects"][0], private=found["parts"][0])])

    def test_an_older_deltapatcher_asks_to_be_updated(self):
        with mock.patch.object(presets, "VERSION", 3):              # the reader before private files
            with self.assertRaises(PresetError) as e:
                presets.read(self.out)
        self.assertEqual(e.exception.code, "newer")
        self.assertIn("made by a newer DeltaPatcher", str(e.exception))


class PublicStaysPublic(Base):
    def test_a_public_file_never_holds_a_private_part(self):
        self.publisher(torrent=True, torrent_id=TID, seeds="203.0.113.10:6881", backup_every_hours=24,
                       trust_key=self.public)
        for extra in ([], ["--sign", self.key, "--passphrase-stdin"]):
            rc, ev = run_json(["project", "export", "Game", "--out", self.p("public.deltapatch")] + extra, PASSPHRASE + "\n")
            self.assertEqual(rc, 0, ev[-1])
            self.assertFalse(ev[-1]["data"]["private"])
            found = presets.read(self.p("public.deltapatch"))
            self.assertEqual((found["private"], found["parts"]), (False, []))
            self.assertLessEqual(found["version"], 3)
            text = read(self.p("public.deltapatch")).decode("utf-8")
            for never in ('"private"', "/var/www/game", "config/user.cfg", "host_keys", "signing_public_key"):
                self.assertNotIn(never, text)
        self.assertEqual(config.load(projects.resolve("Game"))["signing_public_key"], "")   # a public export sets none
        rc, ev = run_json(["project", "import", self.p("public.deltapatch"), "--signing-key", self.key, "--yes"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "bad-arguments"))   # the options of a private file only

    def test_a_line_separator_in_a_public_file_reads_as_a_space(self):
        def page(description):                                       # an old public file, as earlier DeltaPatchers took
            return ("<html>%s%s%s</html>" % (presets.DATA_OPEN, json.dumps(
                {"format": presets.FORMAT, "version": 1, "made": "2026-11-24T10:00:00Z",
                 "projects": [{"title": "Mod", "source_url": "https://mod.example.com/", "description": description,
                               "install_hint": "Pick" + PS + "the game"}]}), presets.CLOSE)).encode("utf-8")
        write(self.p("old.deltapatch"), page("A mod" + LS + "install_dir = C:/x"))
        e = presets.read(self.p("old.deltapatch"))["projects"][0]
        self.assertEqual((e["description"], e["install_hint"]), ("A mod install_dir = C:/x", "Pick the game"))
        self.imported(self.p("old.deltapatch"), "--folder", self.p("mod"), "--yes")
        cfg = config.load(projects.resolve("Mod"))
        self.assertEqual((cfg["description"], cfg["install_dir"]), ("A mod install_dir = C:/x", self.p("mod")))
        for c in "\n\x85":                                           # a line break is refused, as it always was
            with self.assertRaises(PresetError):
                presets.parse(page("A mod%sinstall_dir = C:/x" % c))

        p = self.publisher()                                         # and written: a description with one, by hand
        write(p.path, read(p.path).decode("utf-8").replace("\ndescription = A game\n", "\ndescription = A" + LS + "game\n"))
        self.assertEqual(config.load(p)["description"], "A" + LS + "game")
        rc, ev = run_json(["project", "export", "Game", "--out", self.p("public.deltapatch")])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(presets.read(self.p("public.deltapatch"))["projects"][0]["description"], "A game")

    def test_a_line_separator_in_a_release_detail_is_a_space(self):
        p = self.publisher()
        cfg = dict(config.load(p), description="A" + LS + "game", install_hint="Pick" + PS + "the game")
        obj = publisher.release_object(p, cfg, self.public)                          # written
        self.assertEqual((obj["description"], obj["install_hint"]), ("A game", "Pick the game"))
        got = publisher.read_object({"description": "A" + LS + "game", "install_hint": "Pick" + PS + "the game"})
        self.assertEqual(got, {"description": "A game", "install_hint": "Pick the game"})    # read
        with self.assertRaises(ValueError):
            publisher.read_object({"description": "A\x85game"})                     # a line break is refused


class Settings(TempCase):
    """The settings file is read and written line by line, a line ending at "\\r\\n", "\\r" or "\\n" and nothing else."""

    def test_a_file_whose_lines_end_in_a_carriage_return_alone_reads(self):
        p = projects.new("Game")
        config.save({"name": "Game", "version": "2.0"}, p)
        write(p.path, read(p.path).replace(b"\n", b"\r"))         # as an old Mac wrote it
        cfg = config.load(p)
        self.assertEqual((cfg["name"], cfg["version"]), ("Game", "2.0"))
        config.save({"description": "Read again"}, p)
        cfg = config.load(p)
        self.assertEqual((cfg["name"], cfg["version"], cfg["description"]), ("Game", "2.0", "Read again"))

    def test_a_line_separator_inside_a_value_stays_in_it(self):
        p = projects.new("Game")
        text = read(p.path).decode("utf-8").replace("\nname = \n", "\nname = Game" + LS + "publish_src = C:/x\n")
        self.assertIn(LS, text)
        write(p.path, text)
        cfg = config.load(p)
        self.assertEqual((cfg["name"], cfg["publish_src"]), ("Game" + LS + "publish_src = C:/x", ""))   # one value
        config.save({"version": "2.0"}, p)                          # writing another setting keeps it one line
        cfg = config.load(p)
        self.assertEqual((cfg["name"], cfg["publish_src"], cfg["version"]), ("Game" + LS + "publish_src = C:/x", "", "2.0"))
        self.assertEqual(read(p.path).decode("utf-8").count("\npublish_src = "), 1)

    def test_a_value_that_would_not_stay_on_one_line_is_not_saved_and_said(self):
        p = projects.new("Game")
        for value in ("a" + LS + "publish_src = C:/x", "a\x85b", "a\nb", "a\x0bb"):
            self.assertEqual(config.save({"description": value, "version": "3"}, p), ["description"], repr(value))
            cfg = config.load(p)
            self.assertEqual((cfg["description"], cfg["publish_src"], cfg["version"]), ("", "", "3"))
        rc, ev = run_json(["--project", "Game", "config", "set", "description=a" + LS + "b"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(ev[-1]["data"]["not_saved"], ["description"])
        self.assertTrue(any(e["event"] == "log" and "description was not saved" in e["text"] for e in ev), ev)
        self.assertEqual(config.save({"description": "one line"}, p), [])

    def test_patterns_take_the_whole_value(self):
        for good, bad in ((ssh.HOST_RE, "game.example.com\n"), (ssh.USER_RE, "deploy\n"), (torrent.ID_RE, TID + "\n")):
            self.assertTrue(good.match(bad[:-1]))
            self.assertIsNone(good.match(bad))
        self.assertTrue(torrent.name_problem("release\n"))
        self.assertTrue(torrent.seed_problem("a.example.com\n:6881"))
        made = "2026-11-25T14:02:00Z"
        for pattern in (presets._MADE, publisher._TIME):                # when a file, a release or details were made
            self.assertTrue(pattern.match(made))
            self.assertIsNone(pattern.match(made + "\n"))


class SetUpOnB(Base):
    """A's private file, to be set up on B (host_keys: A trusts its server's host key first)."""
    host_keys = False

    def setUp(self):
        Base.setUp(self)
        self.kind, self.blob = host_key()
        if self.host_keys:
            self.trust("game.example.com", 2222, [(self.kind, self.blob)])
        self.publisher(ssh_port=2222, torrent=True, torrent_id=TID, seeds="203.0.113.10:6881",
                       access_log="/var/log/nginx/access.log", backup_every_hours=24, backup_keep_daily=9)
        self.out = self.p("game (private).deltapatch")
        self.exported(self.out, "Game")
        self.key_b = self.p("b", "keys", "signing-key.pem")
        os.makedirs(os.path.dirname(self.key_b))
        shutil.copyfile(self.key, self.key_b)
        self.computer("b")

    def set_up(self, *args):
        return self.imported(self.out, "--publish-src", self.p("b", "src"), "--signing-key", self.key_b, "--ssh-key",
                             self.p("b", "ssh", "deltapatcher_ed25519"), "--seed", "--yes", *args)

    def terminal(self, answers):
        out = io.StringIO()
        with mock.patch.object(cli, "interactive", return_value=True), contextlib.redirect_stdout(out), \
                mock.patch("builtins.input", side_effect=answers) as keys:
            rc = cli.main(["--no-log", "project", "import", self.out])
        self.assertEqual(rc, 0, out.getvalue())
        return out.getvalue(), [c[0][0] for c in keys.call_args_list]


class OnB(SetUpOnB):
    """A's private file, set up on B."""

    def test_a_dry_run_says_what_it_sets_up(self):
        data = self.imported(self.out, "--dry-run")
        self.assertEqual((data["private"], data["signer"]), (True, self.public))
        row = data["projects"][0]
        self.assertNotIn("updates", row)
        self.assertEqual((row["private"]["ssh_host"], row["private"]["ssh_user"], row["private"]["signing_public_key"]),
                         ("game.example.com", "deploy", self.public))
        self.assertEqual(row["host_keys"]["state"], "")             # A trusted no key for its server
        self.assertEqual([p.title for p in projects.all_projects()], ["Default"])    # a dry run adds nothing
        rc, ev = run_json(["project", "import", self.out])
        self.assertEqual((rc, ev[-1]["code"]), (1, "confirm-needed"))               # asks first

    def test_set_up_with_this_computers_folders_and_keys(self):
        data = self.set_up()
        self.assertEqual([a["project"] for a in data["added"]], ["Game"])
        cfg = config.load(projects.resolve("Game"))
        self.assertEqual((cfg["publish_src"], cfg["signing_key"], cfg["ssh_key"], cfg["seed"]),
                         (self.p("b", "src"), self.key_b, self.p("b", "ssh", "deltapatcher_ed25519"), True))
        self.assertEqual((cfg["ssh_host"], cfg["ssh_user"], cfg["ssh_port"], cfg["remote_root"], cfg["access_log"]),
                         ("game.example.com", "deploy", 2222, "/var/www/game", "/var/log/nginx/access.log"))
        self.assertEqual((cfg["protect"], cfg["leave_out"], cfg["name"], cfg["version"], cfg["sign"]),
                         ("config/user.cfg; saves/", "screenshots/", "Game", "1.0", True))
        self.assertEqual((cfg["backup_keep_daily"], cfg["torrent_id"], cfg["seed_copy"]), (9, TID, True))
        self.assertEqual(cfg["backup_every_hours"], 0)              # backups are set on each computer
        self.assertEqual((cfg["description"], cfg["trust_key"], cfg["signing_public_key"]), ("A game", self.public, self.public))
        self.assertEqual((cfg["install_dir"], cfg["store_dir"], cfg["deploy_folder"], cfg["backup_dir"]), ("", "", "", ""))
        self.assertEqual(cfg["project_file_made"], presets.read(self.out)["made"])

    def test_no_key_file_is_read_unless_one_is_given(self):
        default = config.DEFAULTS["signing_key"]                    # a key at the default place, without a passphrase
        signing.generate_key(default, "")
        data = self.imported(self.out, "--publish-src", self.p("b", "src"), "--yes")
        self.assertEqual(data["added"][0]["key_check"], "first release")
        self.assertNotIn(os.path.abspath(default), self.keys_read)

    def test_a_signing_key_that_is_not_the_files(self):
        plain = self.p("b", "keys", "plain.pem")
        signing.generate_key(plain, "")
        rc, ev = run_json(["project", "import", self.out, "--signing-key", plain, "--yes"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "wrong-key"))                    # read now: it has no passphrase
        self.assertEqual([p.title for p in projects.all_projects()], ["Default"])
        locked = self.p("b", "keys", "locked.pem")
        signing.generate_key(locked, PASSPHRASE)
        data = self.imported(self.out, "--signing-key", locked, "--publish-src", self.p("b", "src"), "--yes")
        self.assertEqual(data["added"][0]["key_check"], "first release")            # checked when it is opened
        write(self.p("b", "src", "game.pk3"), b"x" * 100)
        rc, ev = run_json(["--project", "Game", "publish", "--sign", "--passphrase-stdin"], PASSPHRASE + "\n")
        self.assertEqual((rc, ev[-1]["code"]), (1, "wrong-key"))
        self.assertIn(self.public, ev[-1]["message"])
        self.assertFalse(os.path.exists(os.path.join(config.default_store(self.p("b", "src")), "manifest.json")))

    def test_a_newer_file_updates_it_in_place(self):
        self.set_up()
        config.save({"install_dir": self.p("b", "game"), "seed_copy": False, "store_dir": self.p("b", "store"),
                     "backup_dir": self.p("b", "backups"), "deploy_folder": self.p("b", "www"), "backup_every_hours": 6},
                    projects.resolve("Game"))
        mine = config.load(projects.resolve("Game"))
        self.computer("a")                                         # the publisher changes it on A
        config.save({"description": "A bigger game", "protect": "config/user.cfg", "ssh_host": "203.0.113.10",
                     "seeds": "203.0.113.10:6881, 203.0.113.11:6881", "backup_keep_daily": 3, "version": "1.1",
                     "publish_src": self.p("a", "elsewhere"), "backup_every_hours": 1}, projects.resolve("Game"))
        self.exported(self.out, "Game")
        self.computer("b")
        data = self.imported(self.out, "--dry-run")
        row = data["projects"][0]
        self.assertEqual((row["updates"]["project"], row["older"]), ("Game", False))
        changed = {c["key"]: (c["from"], c["to"]) for c in row["changes"]}
        self.assertEqual(changed, {"description": ("A game", "A bigger game"), "protect": ("config/user.cfg; saves/", "config/user.cfg"),
                                   "ssh_host": ("game.example.com", "203.0.113.10"), "version": ("1.0", "1.1"),
                                   "seeds": ("203.0.113.10:6881", "203.0.113.10:6881, 203.0.113.11:6881"),
                                   "backup_keep_daily": ("9", "3")})
        data = self.imported(self.out, "--yes")
        self.assertEqual(([u["project"] for u in data["updated"]], data["added"]), (["Game"], []))
        now = config.load(projects.resolve("Game"))
        self.assertEqual((now["description"], now["protect"], now["ssh_host"], now["seeds"], now["backup_keep_daily"]),
                         ("A bigger game", "config/user.cfg", "203.0.113.10", "203.0.113.10:6881, 203.0.113.11:6881", 3))
        for key in OWN:
            self.assertEqual(now[key], mine[key], key)              # this computer's folders, keys and choices stay
        self.assertEqual([p.title for p in projects.all_projects()], ["Default", "Game"])
        again = self.imported(self.out, "--yes")["updated"][0]                 # the same file again: an older one
        self.assertEqual((again["applied"], again["older"]), ({}, True))

    def test_an_older_file_is_applied_only_when_asked(self):
        newer = self.remade(self.out, self.p("newer.deltapatch"), "2026-06-01T16:40:00Z",
                            lambda entries: entries[0].update(description="The newer one"))
        older = self.remade(self.out, self.p("older.deltapatch"), "2026-06-01T14:02:00Z",
                            lambda entries: entries[0].update(description="The older one"))
        self.imported(newer, "--publish-src", self.p("b", "src"), "--yes")
        self.assertEqual(config.load(projects.resolve("Game"))["project_file_made"], "2026-06-01T16:40:00Z")
        row = self.imported(older, "--dry-run")["projects"][0]
        self.assertEqual((row["older"], row["made"], row["applied_made"]),
                         (True, "2026-06-01T14:02:00Z", "2026-06-01T16:40:00Z"))
        self.assertEqual(row["changes"], [{"key": "description", "label": "description", "from": "The newer one",
                                           "to": "The older one"}])                  # what it would put back
        both = "made 2026-06-01 14:02 UTC; the one applied here was made 2026-06-01 16:40 UTC: opening it puts back " \
               "what it says"
        rc, ev = run_json(["project", "import", older, "--yes"])                   # --yes alone: not applied
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual((ev[-1]["data"]["updated"][0]["older"], ev[-1]["data"]["updated"][0]["applied"]), (True, {}))
        self.assertTrue(any(both in e.get("text", "") for e in ev), ev)
        cfg = config.load(projects.resolve("Game"))
        self.assertEqual((cfg["description"], cfg["project_file_made"]), ("The newer one", "2026-06-01T16:40:00Z"))

        data = self.imported(older, "--apply-older", "--yes")                       # asked for: applied
        self.assertEqual(data["updated"][0]["applied"], {"description": "The older one"})
        cfg = config.load(projects.resolve("Game"))
        self.assertEqual((cfg["description"], cfg["project_file_made"]), ("The older one", "2026-06-01T14:02:00Z"))
        self.imported(newer, "--yes")                                               # the newer one is newer again
        self.assertEqual(config.load(projects.resolve("Game"))["description"], "The newer one")
        self.assertTrue(self.imported(newer, "--yes")["updated"][0]["older"])       # the same file again: older

    def test_its_own_export_opened_again_is_caught(self):
        self.computer("a")                                         # A exported self.out, then changed the project
        config.save({"description": "Changed after the export"}, projects.resolve("Game"))
        row = self.imported(self.out, "--dry-run")["projects"][0]
        self.assertEqual((row["updates"]["project"], row["older"]), ("Game", True))
        self.assertEqual(row["made"], row["applied_made"])           # this very file: the time the export recorded
        data = self.imported(self.out, "--yes")
        self.assertEqual((data["updated"][0]["applied"], data["updated"][0]["older"]), ({}, True))
        self.assertEqual(config.load(projects.resolve("Game"))["description"], "Changed after the export")
        self.imported(self.out, "--apply-older", "--yes")
        self.assertEqual(config.load(projects.resolve("Game"))["description"], "A game")

    def test_a_project_whose_key_is_not_known_yet_is_named(self):
        locked = self.p("b", "keys", "locked.pem")                   # B releases Game too, with a key never used here
        signing.generate_key(locked, PASSPHRASE)
        self.make("Game", publish_src=self.p("b", "src"), sign=True, signing_key=locked)
        row = self.imported(self.out, "--dry-run")["projects"][0]
        self.assertNotIn("updates", row)
        self.assertEqual(row["key_unknown"]["project"], "Game")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["--no-log", "project", "import", self.out, "--dry-run"]), 0)
        self.assertIn("Game releases here too, but DeltaPatcher does not know its signing key yet: release it once or "
                      "export a private file from it, then open this file again; or set this one up as a separate "
                      "project", " ".join(out.getvalue().split()))
        rc, ev = run_json(["project", "import", self.out, "--yes"])                  # never updated: a new one
        self.assertEqual((rc, ev[-1]["code"], ev[-1]["data"]["updated"]), (1, "not-added", []))

    def test_keys_are_read_only_for_the_projects_it_could_be(self):
        plain = self.p("b", "keys", "other.pem")                     # another project here, released with a key
        signing.generate_key(plain, "")                              # that opens without a passphrase
        self.make("Other", publish_src=self.p("b", "other"), sign=True, signing_key=plain, torrent_id="f" * 32)
        del self.keys_read[:]
        self.imported(self.out, "--dry-run")
        self.assertNotIn(os.path.abspath(plain), self.keys_read)

    def test_a_server_access_log_goes_when_releases_no_longer_go_to_a_server(self):
        self.set_up()
        self.computer("a")
        config.save({"deploy_mode": "folder", "deploy_folder": self.p("a", "www")}, projects.resolve("Game"))
        self.exported(self.out, "Game")
        self.computer("b")
        row = self.imported(self.out, "--dry-run")["projects"][0]
        self.assertIn({"key": "access_log", "label": "the access log", "from": "/var/log/nginx/access.log", "to": ""},
                      row["changes"])
        self.imported(self.out, "--yes")
        cfg = config.load(projects.resolve("Game"))
        self.assertEqual((cfg["deploy_mode"], cfg["access_log"], cfg["deploy_folder"]), ("folder", "", ""))

    def test_a_terminal_can_add_it_as_a_player_instead(self):
        text, asked = self.terminal(["n", "y", "y", "", "n"])                       # not mine: as a player
        self.assertTrue(asked[1].startswith("Add it as a player instead"))
        cfg = config.load(projects.resolve("Game"))
        self.assertEqual((cfg["source_url"], cfg["publish_src"], cfg["ssh_host"], cfg["signing_public_key"]),
                         ("https://game.example.com/", "", "", ""))
        self.assertFalse(os.path.exists(self.known_hosts))

    def test_a_player_given_it_adds_it_as_a_player(self):
        data = self.imported(self.out, "--as-player", "--folder", self.p("b", "game"), "--yes")
        self.assertEqual(([a["project"] for a in data["added"]], data["private"]), (["Game"], True))
        cfg = config.load(projects.resolve("Game"))
        self.assertEqual((cfg["install_dir"], cfg["source_url"], cfg["trust_key"], cfg["torrent_id"]),
                         (self.p("b", "game"), "https://game.example.com/", self.public, TID))
        for key in ("publish_src", "ssh_host", "ssh_user", "remote_root", "protect", "leave_out", "access_log",
                    "public_url", "signing_public_key", "project_file_made"):
            self.assertEqual(cfg[key], config.DEFAULTS[key], key)       # only what a public file holds
        self.assertEqual((cfg["sign"], cfg["backup_every_hours"], cfg["ssh_port"]), (False, 0, 22))
        rc, ev = run_json(["project", "import", self.out, "--as-player", "--publish-src", self.p("x"), "--yes"])
        self.assertEqual((rc, ev[-1]["code"]), (1, "bad-arguments"))


@unittest.skipUnless(HAVE_SSH_KEYGEN, "needs ssh-keygen (known_hosts is read with it)")
class HostKeys(SetUpOnB):
    """The server's host keys from the file: added only after the person's yes, and only while this computer trusts
    no key for that server."""
    host_keys = True

    def deploy(self, scan=None):
        """A deploy of B's project as a release sends it: the host key checked first; nothing reaches a server."""
        store = self.p("b", "store")
        write(os.path.join(store, "manifest.json"), b"{}")
        scanned = mock.patch.object(ssh, "scan_host", side_effect=scan or AssertionError("the server was asked"))
        with scanned, mock.patch.object(deploy, "deploy", return_value=Sent()):
            return run_json(["--project", "Game", "deploy", store])

    def test_a_dry_run_shows_them(self):
        row = self.imported(self.out, "--dry-run")["projects"][0]
        self.assertEqual(row["host_keys"]["host"], "[game.example.com]:2222")
        self.assertEqual(row["host_keys"]["keys"], [{"type": self.kind, "fingerprint": ssh.fingerprint(self.blob)}])
        self.assertEqual((row["host_keys"]["state"], row["host_keys"]["here"]), ("new", []))

    def test_host_keys_only_when_confirmed(self):
        data = self.set_up()
        self.assertEqual(data["added"][0]["host_keys"], "not added")
        target = ssh.Target("game.example.com", port=2222)
        self.assertFalse(ssh.is_trusted(target))
        offered = [(self.kind, self.blob, ssh.fingerprint(self.blob))]
        rc, ev = self.deploy(scan=lambda t, timeout=15: offered)
        self.assertEqual((rc, ev[-1]["code"]), (1, "host-unknown"))                 # the first release asks, as today
        data = self.imported(self.out, "--trust-host-keys", "--apply-older", "--yes")   # the same file again
        self.assertEqual(data["updated"][0]["host_keys"], "added")
        self.assertTrue(ssh.is_trusted(target))
        rc, ev = self.deploy()
        self.assertEqual(rc, 0, ev[-1])                                             # and now it asks nothing
        self.assertEqual(self.imported(self.out, "--trust-host-keys", "--apply-older", "--yes")["updated"][0]["host_keys"],
                         "known")
        self.assertEqual(self.imported(self.out, "--trust-host-keys", "--yes")["updated"][0]["host_keys"], "")  # older

    def test_host_keys_of_another_server_are_never_added(self):
        other = host_key()
        self.trust("game.example.com", 2222, [other])                               # B trusts another key there
        row = self.imported(self.out, "--dry-run")["projects"][0]
        self.assertEqual((row["host_keys"]["state"], row["host_keys"]["here"]),
                         ("other", [{"type": other[0], "fingerprint": ssh.fingerprint(other[1])}]))
        data = self.set_up("--trust-host-keys")
        self.assertEqual(data["added"][0]["host_keys"], "other")
        self.assertNotIn(self.blob, read(self.known_hosts).decode())

    def test_a_partial_overlap_adds_nothing(self):
        second = host_key()
        self.computer("a")                                          # A trusts two keys of the server
        self.trust("game.example.com", 2222, [second])
        self.exported(self.out, "Game")
        self.computer("b")
        self.trust("game.example.com", 2222, [(self.kind, self.blob)])               # B trusts one of them
        row = self.imported(self.out, "--dry-run")["projects"][0]
        self.assertEqual(row["host_keys"]["state"], "other")
        data = self.set_up("--trust-host-keys")
        self.assertEqual(data["added"][0]["host_keys"], "other")
        self.assertNotIn(second[1], read(self.known_hosts).decode())

    def test_only_the_keys_shown_are_added(self):
        second = host_key()
        self.computer("a")
        self.trust("game.example.com", 2222, [second])
        self.exported(self.out, "Game")
        self.computer("b")
        shown = [k["fingerprint"] for k in self.imported(self.out, "--dry-run")["projects"][0]["host_keys"]["keys"]]
        self.assertEqual(len(shown), 2)
        data = self.set_up("--host-key-fingerprints=" + shown[0])                   # the person saw one of them
        self.assertEqual(data["added"][0]["host_keys"], "added")
        kept = read(self.known_hosts).decode()
        self.assertEqual(len([k for k in (self.blob, second[1]) if k in kept]), 1)

    def test_a_file_swapped_after_the_dry_run_pins_nothing_it_did_not_show(self):
        shown = [k["fingerprint"] for k in self.imported(self.out, "--dry-run")["projects"][0]["host_keys"]["keys"]]
        swapped = host_key()                                         # the same file, its host key replaced since

        def swap(entries):
            entries[0]["private"]["server"]["host_keys"] = [{"type": swapped[0], "key": swapped[1]}]
        self.remade(self.out, self.out, presets.read(self.out)["made"], swap, key=self.key_b)
        data = self.set_up("--host-key-fingerprints", ",".join(shown))
        self.assertEqual(data["added"][0]["host_keys"], "not added")
        self.assertFalse(os.path.exists(self.known_hosts))

    def test_the_options_before_the_file(self):
        rc, ev = run_json(["project", "import", "--trust-host-keys", self.out, "--publish-src", self.p("b", "src"), "--yes"])
        self.assertEqual(rc, 0, ev[-1])                                             # a switch: it takes no value
        self.assertEqual(ev[-1]["data"]["added"][0]["host_keys"], "added")
        self.assertTrue(ssh.is_trusted(ssh.Target("game.example.com", port=2222)))
        self.computer("c")
        shown = ssh.fingerprint(self.blob)
        rc, ev = run_json(["project", "import", "--host-key-fingerprints", shown, self.out, "--publish-src",
                           self.p("c", "src"), "--yes"])
        self.assertEqual(rc, 0, ev[-1])
        self.assertEqual(ev[-1]["data"]["added"][0]["host_keys"], "added")

    def test_a_terminal_asks_for_this_computers_folders_and_keys(self):
        text, asked = self.terminal(["y", self.p("b", "src"), self.key_b, "", "n", "y"])
        self.assertIn("is a private project file, signed by", text)
        self.assertIn(self.public, text)
        self.assertIn("a USB stick, not chat or email", text)
        self.assertIn(ssh.fingerprint(self.blob), text)                             # the host keys, to compare
        self.assertTrue(asked[0].startswith("Set it up for releasing from this computer"))
        self.assertTrue(asked[-1].startswith("Are these the host keys of your server [game.example.com]:2222?"))
        cfg = config.load(projects.resolve("Game"))
        self.assertEqual((cfg["publish_src"], cfg["signing_key"], cfg["ssh_key"], cfg["seed"]),
                         (self.p("b", "src"), self.key_b, config.DEFAULTS["ssh_key"], False))
        self.assertTrue(ssh.is_trusted(ssh.Target("game.example.com", port=2222)))


class OnlyByItsOwnKey(Base):
    """A private file updates in place only the project this computer releases with the very key it is signed with."""

    def setUp(self):
        Base.setUp(self)
        self.publisher(torrent=True, torrent_id=TID, seeds="203.0.113.10:6881", trust_key=self.public)
        rc, ev = run_json(["project", "export", "Game", "--out", self.p("game.deltapatch"), "--sign",
                           "--passphrase-stdin"], PASSPHRASE + "\n")
        self.assertEqual(rc, 0, ev[-1])
        self.computer("e")                                           # someone else: another key, the same torrent id
        self.evil_key = self.p("e", "keys", "signing-key.pem")
        signing.generate_key(self.evil_key, PASSPHRASE)
        self.make("Game", public_url="https://evil.example.com/", sign=True, signing_key=self.evil_key,
                  publish_src=self.p("e", "src"), deploy_mode="sftp", ssh_host="evil.example.com", ssh_user="evil",
                  remote_root="/srv/evil", torrent=True, torrent_id=TID, seeds="198.51.100.7:6881")
        self.evil = self.p("evil (private).deltapatch")
        self.exported(self.evil, "Game")

    def player(self):
        self.computer("player")                                      # a player who has the game
        self.imported(self.p("game.deltapatch"), "--folder", self.p("player", "game"), "--yes")
        return config.load(projects.resolve("Game"))

    def test_a_players_project_never_changes(self):
        before = self.player()
        rc, ev = run_json(["project", "import", self.evil, "--yes"])                # with --yes: refused as a new one
        self.assertEqual((rc, ev[-1]["code"]), (1, "not-added"))
        self.assertEqual(ev[-1]["data"]["updated"], [])
        self.assertEqual(config.load(projects.resolve("Game")), before)

        row = self.imported(self.evil, "--dry-run")["projects"][0]                  # the window's flow
        self.assertNotIn("updates", row)
        self.assertEqual(row["player_here"]["project"], "Game")
        data = self.imported(self.evil, "--only=Game", "--yes", "--name=Game 2", "--publish-src=" + self.p("player", "x"),
                             "--signing-key=" + self.p("player", "keys", "none.pem"))
        self.assertEqual(([a["project"] for a in data["added"]], data["updated"]), (["Game 2"], []))
        after = config.load(projects.resolve("Game"))
        self.assertEqual(after, before)
        self.assertEqual((after["source_url"], after["trust_key"], after["torrent_id"], after["seeds"]),
                         ("https://game.example.com/", self.public, TID, "203.0.113.10:6881"))

    def test_the_releasing_project_wins_over_a_player_copy(self):
        self.computer("a")
        self.exported(self.p("game (private).deltapatch"), "Game")
        self.computer("b")                                           # the publisher's other computer
        self.imported(self.p("game.deltapatch"), "--name", "A Game copy", "--folder", self.p("b", "copy"), "--yes")
        copy = config.load(projects.resolve("A Game copy"))           # their own player copy, listed first
        self.assertEqual(copy["torrent_id"], TID)
        self.imported(self.p("game (private).deltapatch"), "--yes")   # and the project they release, set up beside it
        self.computer("a")                                           # changed on A
        config.save({"description": "Changed"}, projects.resolve("Game"))
        changed = self.p("changed (private).deltapatch")
        self.exported(changed, "Game")
        self.computer("b")
        data = self.imported(changed, "--yes")
        self.assertEqual([u["project"] for u in data["updated"]], ["Game"])
        self.assertEqual(config.load(projects.resolve("Game"))["description"], "Changed")
        self.assertEqual(config.load(projects.resolve("A Game copy")), copy)

        rc, ev = run_json(["project", "import", self.evil, "--yes"])                # another key: no update of either
        self.assertEqual((rc, ev[-1]["code"]), (1, "not-added"))
        self.assertEqual(config.load(projects.resolve("Game"))["ssh_host"], "game.example.com")


class BackAndForth(Base):
    def test_changed_on_b_and_opened_on_a(self):
        self.publisher(ssh_host="", deploy_mode="folder", deploy_folder=self.p("a", "www"))
        self.exported(self.p("game (private).deltapatch"), "Game")   # A now knows Game's key, and this file's time
        self.computer("b")
        key_b = self.p("b", "keys", "signing-key.pem")
        os.makedirs(os.path.dirname(key_b))
        shutil.copyfile(self.key, key_b)
        self.imported(self.p("game (private).deltapatch"), "--signing-key", key_b, "--yes")
        rc, ev = run_json(["--project", "Game", "config", "set", "description=Changed on B", "leave_out=logs/"])
        self.assertEqual(rc, 0, ev[-1])
        self.exported(self.p("game (private).deltapatch"), "Game")       # B signs it with the same key

        self.computer("a")                                       # A has the project, and no torrent id: by name and key
        data = self.imported(self.p("game (private).deltapatch"), "--yes")
        self.assertEqual([u["project"] for u in data["updated"]], ["Game"])
        cfg = config.load(projects.resolve("Game"))
        self.assertEqual((cfg["description"], cfg["leave_out"], cfg["publish_src"], cfg["signing_key"]),
                         ("Changed on B", "logs/", self.p("a", "src"), self.key))
        self.assertEqual([p.title for p in projects.all_projects()], ["Default", "Game"])


class ReleaseFolder(Base):
    def test_a_private_file_never_goes_into_a_release(self):
        src = self.p("a", "src")
        write(os.path.join(src, "game.pk3"), b"the game")
        self.publisher(store_dir=self.p("a", "store"), deploy_mode="folder", deploy_folder=self.p("a", "www"))
        for inside in (os.path.join(src, "Game (private).deltapatch"), self.p("a", "store", "x.html"),
                       self.p("a", "www", "sub", "x.deltapatch")):
            rc, ev = self.export(inside, "Game")
            self.assertEqual((rc, ev[-1]["code"]), (1, "released-folder"), inside)
            self.assertFalse(os.path.exists(inside))

        self.exported(self.p("out", "Game (private).deltapatch"), "Game")      # put there by hand: left out
        shutil.copyfile(self.p("out", "Game (private).deltapatch"), os.path.join(src, "Game (private).deltapatch"))
        rc, ev = run_json(["project", "export", "Game", "--out", os.path.join(src, "Game.deltapatch")])
        self.assertEqual(rc, 0, ev[-1])                                              # a public file is released
        write(os.path.join(src, "page.html"), b"<html>an ordinary page</html>")
        lines = []
        res = publish.publish(src, self.p("a", "store"), log=lines.append)
        names = set(res.manifest.files)
        self.assertEqual(names, {"game.pk3", "Game.deltapatch", "page.html"})
        warned = [line for line in lines if "private project file" in line]
        self.assertEqual(len(warned), 1, lines)
        self.assertIn("Game (private).deltapatch", warned[0])


class Assistants(Base):
    def test_no_private_export_and_no_private_set_up(self):
        from test_mcp import Client
        self.publisher(deploy_mode="folder", ssh_host="", deploy_folder=self.p("a", "www"))
        self.exported(self.p("game (private).deltapatch"), "Game")
        self.computer("b")
        c = Client(self)
        c.start()
        tools = {t["name"]: t for t in c.request("tools/list")["result"]["tools"]}
        self.assertIn("private", tools["project_export"]["description"])
        self.assertNotIn("private", tools["project_export"]["inputSchema"]["properties"])
        r = c.tool("project_import", file=self.p("game (private).deltapatch"), dry_run=True)
        self.assertFalse(r.get("isError"), r)
        r = c.tool("project_import", file=self.p("game (private).deltapatch"))
        self.assertTrue(r["isError"])
        self.assertIn("private project file", r["content"][0]["text"])
        self.assertEqual([p.title for p in projects.all_projects()], ["Default"])

    def test_its_import_runs_are_both_as_a_player(self):
        for a in ({"file": "x.deltapatch"}, {"file": "x.deltapatch", "dry_run": True}):
            self.assertIn("--as-player", mcp_server.b_project_import(a), a)


class LeavesNoTrace(unittest.TestCase):
    """A test of this file leaves the environment as it found it: no later test inherits its settings file."""

    @unittest.skipUnless(signing.can_sign(), "needs cryptography")
    def test_the_environment_after_a_test_is_as_before_it(self):
        class One(Base):
            def test_it(self):
                self.computer("b")
        before = dict(os.environ)
        result = unittest.TestResult()
        One("test_it").run(result)
        self.assertEqual((result.errors, result.failures, result.skipped), ([], [], []))
        self.assertEqual(dict(os.environ), before)


if __name__ == "__main__":
    unittest.main()
