"""Keys, checked against the real ssh-keygen where it is installed."""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from deltapatcher import ssh  # noqa: E402
from helpers import TempCase  # noqa: E402


def have(name):
    try:
        ssh.tool(name)
        return True
    except ssh.SshError:
        return False


@unittest.skipUnless(ssh.can_generate() and have("ssh-keygen"), "needs cryptography and ssh-keygen")
class Keys(TempCase):
    def test_passphrase_key(self):
        path = self.p("keys", "k_ed25519")
        ssh.generate_key(path, "correct horse battery", "test@here")
        self.assertTrue(ssh.key_has_passphrase(path))
        self.assertEqual(ssh.permission_problems(path), [])
        with self.assertRaises(ssh.SshError):
            ssh.generate_key(path, "x" * 12)          # never overwrites
        line = ssh.authorized_keys_line(path)
        self.assertTrue(line.startswith('restrict,command="internal-sftp" ssh-ed25519 '), line)
        # our fingerprint is the one ssh-keygen prints
        r = subprocess.run([ssh.tool("ssh-keygen"), "-lf", path + ".pub"], stdout=subprocess.PIPE, universal_newlines=True)
        blob = ssh.public_key(path).split()[1]
        self.assertIn(ssh.fingerprint(blob), r.stdout)
        # and ssh-keygen opens it with the passphrase
        r = subprocess.run([ssh.tool("ssh-keygen"), "-y", "-P", "correct horse battery", "-f", path],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split()[1], blob)

    def test_no_passphrase_key(self):
        path = self.p("keys", "plain_ed25519")
        ssh.generate_key(path, "")
        self.assertFalse(ssh.key_has_passphrase(path))

    def test_known_hosts(self):
        kh = self.p("kh", "known_hosts")
        t = ssh.Target("example.com", port=2222, known_hosts=kh)
        self.assertFalse(ssh.is_trusted(t))
        blob = "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
        ssh.trust(t, [("ssh-ed25519", blob, ssh.fingerprint(blob))])
        self.assertTrue(ssh.is_trusted(t))
        self.assertFalse(ssh.is_trusted(ssh.Target("example.com", port=22, known_hosts=kh)))
        ssh.forget(t)
        self.assertFalse(ssh.is_trusted(t))


if __name__ == "__main__":
    unittest.main()
