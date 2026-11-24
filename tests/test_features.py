"""Signed releases, .htaccess, the settings file, logs, colour and the cli/ wrappers."""

import configparser
import io
import os
import subprocess
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import TempCase, read, write  # noqa: E402  (first: it points settings and logs at a sandbox)
from deltapatcher import cli, client, color, config, deploy, hashing, logs, publish, serve, signing  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAVE_CRYPTO = signing.can_sign()


@unittest.skipUnless(HAVE_CRYPTO, "needs cryptography")
class PureVerify(unittest.TestCase):
    def test_matches_cryptography(self):
        from cryptography.hazmat.primitives import serialization as s
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        for i in range(40):
            k = Ed25519PrivateKey.generate()
            pub = k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)
            m = os.urandom(i * 7)
            sig = k.sign(m)
            self.assertTrue(signing.verify_pure(pub, m, sig))
            self.assertFalse(signing.verify_pure(pub, m + b"!", sig))
            self.assertFalse(signing.verify_pure(pub, m, sig[:63] + bytes([sig[63] ^ 0x20])))


@unittest.skipUnless(HAVE_CRYPTO, "needs cryptography")
class Signed(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        self.key1 = self.p("keys", "one.pem")
        self.key2 = self.p("keys", "two.pem")
        self.pub1 = signing.generate_key(self.key1, "first passphrase")
        self.pub2 = signing.generate_key(self.key2, "")
        write(self.p("src", "a.pk3"), os.urandom(5000))

    def pub(self, version, key=None, passphrase="first passphrase"):
        signer = signing.load_key(key, passphrase if signing.needs_passphrase(key) else None) if key else None
        publish.publish(self.p("src"), self.p("store"), "G", version, signer=signer)

    def test_trust_on_first_use_then_only_that_key(self):
        self.pub("1", self.key1)
        msgs = []
        client.update(self.p("store"), self.p("inst"), log=msgs.append)
        self.assertTrue(any("trusting this publisher key" in m for m in msgs), msgs)
        self.assertEqual(client.State(self.p("inst")).publisher_key, self.pub1)
        write(self.p("src", "a.pk3"), b"two")
        self.pub("2", self.key1)
        client.update(self.p("store"), self.p("inst"))
        self.assertEqual(read(self.p("inst", "a.pk3")), b"two")
        write(self.p("src", "a.pk3"), b"three, someone else's")
        self.pub("3", self.key2)
        with self.assertRaises(client.UpdateError) as cm:
            client.update(self.p("store"), self.p("inst"))
        self.assertIn("trusts", str(cm.exception))
        self.assertEqual(read(self.p("inst", "a.pk3")), b"two")
        write(self.p("src", "a.pk3"), b"four, unsigned")
        self.pub("4")
        with self.assertRaises(client.UpdateError) as cm:
            client.update(self.p("store"), self.p("inst"))
        self.assertIn("not signed", str(cm.exception))
        # the publisher really changed keys: the player says so once
        self.pub("5", self.key2)
        client.update(self.p("store"), self.p("inst"), trust_key=self.pub2)
        self.assertEqual(client.State(self.p("inst")).publisher_key, self.pub2)

    def test_given_key_refuses_unsigned_and_others(self):
        self.pub("1")
        with self.assertRaises(client.UpdateError):
            client.update(self.p("store"), self.p("inst"), trust_key=self.pub1)
        self.pub("1", self.key2)
        with self.assertRaises(client.UpdateError):
            client.update(self.p("store"), self.p("inst"), trust_key=self.pub1)
        self.assertFalse(os.path.exists(self.p("inst", "a.pk3")))

    def test_altered_manifest_or_signature_refused(self):
        self.pub("1", self.key1)
        client.update(self.p("store"), self.p("inst"))
        mpath = self.p("store", "manifest.json")
        raw = read(mpath)
        sig_path = os.path.join(self.p("store"), "sigs", signing.sig_name(hashing.sha256_bytes(raw)))
        # a signature file that claims this manifest but carries another's signature
        sig = read(sig_path).replace(b'"signature": "', b'"signature": "AAAA')
        write(sig_path, sig)
        with self.assertRaises(client.UpdateError) as cm:
            client.update(self.p("store"), self.p("inst"))
        self.assertIn("signature", str(cm.exception))

    def test_checking_can_be_switched_off(self):
        self.pub("1", self.key1)
        client.update(self.p("store"), self.p("inst"))
        write(self.p("src", "a.pk3"), b"unsigned now")
        self.pub("2")
        with self.assertRaises(client.UpdateError):
            client.update(self.p("store"), self.p("inst"))
        client.update(self.p("store"), self.p("inst"), trust_key="off")
        self.assertEqual(read(self.p("inst", "a.pk3")), b"unsigned now")

    def test_check_only_does_not_pin(self):
        self.pub("1", self.key1)
        client.update(self.p("store"), self.p("inst"), check_only=True)
        self.assertEqual(client.State(self.p("inst")).publisher_key, "")

    def test_over_http_and_deploy_carry_the_signature(self):
        self.pub("1", self.key1)
        web = self.p("web")
        deploy.deploy(self.p("store"), deploy.FolderTarget(web))
        self.assertTrue(os.path.isfile(os.path.join(web, ".htaccess")))
        self.assertEqual(len(os.listdir(os.path.join(web, "sigs"))), 1)
        srv, _t = serve.serve_in_background(web, "127.0.0.1", 0)
        try:
            url = "http://127.0.0.1:%d/" % srv.server_address[1]
            client.update(url, self.p("inst"), trust_key=self.pub1)
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(url + ".htaccess")
        finally:
            srv.shutdown()
            srv.server_close()
        for v in ("2", "3", "4"):
            write(self.p("src", "a.pk3"), v.encode())
            self.pub(v, self.key1)
            deploy.deploy(self.p("store"), deploy.FolderTarget(web))
        # the signatures of the manifests on the target stay; older ones go
        self.assertLessEqual(len(os.listdir(os.path.join(web, "sigs"))), 3)


class Htaccess(TempCase):
    def test_written_and_optional(self):
        write(self.p("src", "a.txt"), b"a")
        publish.publish(self.p("src"), self.p("store"))
        text = read(self.p("store", ".htaccess")).decode()
        self.assertIn("Options -Indexes", text)
        self.assertIn("Require all denied", text)
        publish.publish(self.p("src"), self.p("store2"), htaccess=False)
        self.assertFalse(os.path.exists(self.p("store2", ".htaccess")))


class Settings(TempCase):
    def setUp(self):
        TempCase.setUp(self)
        self.old = os.environ.get("DELTAPATCHER_CONFIG")
        os.environ["DELTAPATCHER_CONFIG"] = self.p("conf", "deltapatcher.ini")

    def tearDown(self):
        os.environ["DELTAPATCHER_CONFIG"] = self.old
        TempCase.tearDown(self)

    def test_placeholder_explains_every_setting(self):
        cfg = config.load()
        self.assertEqual(cfg, config.DEFAULTS)
        text = read(self.p("conf", "deltapatcher.ini")).decode()
        for _s, key, _d, what in config.SCHEMA:
            self.assertIn("\n%s = " % key, text)
            self.assertIn("; " + what, text)
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(text)
        self.assertEqual(sorted(parser.sections()), ["publish", "server", "update", "window"])

    def test_save_keeps_comments_and_hand_edits(self):
        config.load()
        p = self.p("conf", "deltapatcher.ini")
        write(p, read(p).decode().replace("; The port of 'Serve locally'.", "; my own note") + "\n; at the end\n")
        cfg = config.load()
        cfg.update(source_url="https://game.example.com/", serve_port=8123, sign=True, ssh_user="deploy")
        config.save(cfg)
        text = read(p).decode()
        self.assertIn("; my own note", text)
        self.assertIn("; at the end", text)
        again = config.load()
        self.assertEqual((again["source_url"], again["serve_port"], again["sign"], again["ssh_user"]),
                         ("https://game.example.com/", 8123, True, "deploy"))

    def test_bad_values_fall_back(self):
        config.load()
        p = self.p("conf", "deltapatcher.ini")
        write(p, read(p).decode().replace("serve_port = 8000", "serve_port = lots").replace("sign = no", "sign = maybe"))
        cfg = config.load()
        self.assertEqual((cfg["serve_port"], cfg["sign"]), (8000, False))

    def test_cli_config_command(self):
        out = io.StringIO()
        real, sys.stdout = sys.stdout, out
        try:
            self.assertEqual(cli.main(["--no-log", "config"]), 0)
        finally:
            sys.stdout = real
        self.assertIn(self.p("conf", "deltapatcher.ini"), out.getvalue())


class Logging(TempCase):
    def test_cli_writes_its_log_and_hides_header_values(self):
        write(self.p("src", "a.txt"), b"a")
        log = self.p("log", "dp.log")
        self.assertEqual(cli.main(["--log", log, "publish", self.p("src"), self.p("store")]), 0)
        self.assertEqual(cli.main(["--log", log, "update", self.p("inst"), "--source", self.p("store"),
                                   "--header", "Authorization: Bearer s3cret"]), 0)
        text = read(log).decode()
        self.assertIn("published 1 files", text)
        self.assertIn("updated to", text)
        self.assertIn("Authorization: ***", text)
        self.assertNotIn("s3cret", text)
        self.assertNotIn("\x1b[", text)
        record = read(self.p("inst", ".deltapatcher", "update.log")).decode()
        self.assertIn("added    a.txt", record)


class Colour(unittest.TestCase):
    def test_plain_when_off_codes_when_on(self):
        color._on.clear()
        color.enable(False)
        self.assertEqual(color.paint("x", "ok"), "x")
        color._on["out"] = True
        self.assertEqual(color.paint("x", "code"), "\x1b[38;5;141mx\x1b[0m")
        self.assertEqual(color.plain(color.paint("x", "warn")), "x")
        color._on.clear()


@unittest.skipUnless(os.name == "nt", "the .bat is for Windows")
class Wrappers(unittest.TestCase):
    def test_bat_runs_the_command_line(self):
        r = subprocess.run(["cmd", "/c", os.path.join(ROOT, "cli", "deltapatcher.bat"), "--version"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(b"DeltaPatcher", r.stdout)


if __name__ == "__main__":
    unittest.main()
