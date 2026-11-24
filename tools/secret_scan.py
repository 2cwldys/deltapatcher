"""Refuse secrets in the repository: private keys, signing keys, known_hosts and settings files.

    python tools/secret_scan.py      scan every file git tracks or has staged; exit 1 on a finding

The secret-scan workflow runs this on every push to main and every pull request, then gitleaks with
its default rules and .gitleaks.toml. Run it yourself before committing. It reads only the files
git knows about, so ignored folders (.venv, dist, build) are not scanned.
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# a file with one of these names must never be in the repository, whatever it holds
NAME_RULES = [
    (r"(^|/)id_(rsa|dsa|ecdsa|ed25519)(\.pub)?$", "an SSH key file"),
    (r"_(ed25519|rsa|ecdsa|dsa)(\.pub)?$", "an SSH key file"),
    (r"\.(pem|ppk|p12|pfx|key|keystore|jks)$", "a key or certificate store"),
    (r"(^|/)signing-key[^/]*$", "a DeltaPatcher signing key"),
    (r"(^|/)known_hosts[^/]*$", "a known_hosts file"),
    (r"(^|/)authorized_keys[^/]*$", "an authorized_keys file"),
    (r"(^|/)deltapatcher\.ini$", "a DeltaPatcher settings file (it holds this PC's paths)"),
    (r"(^|/)\.env(\..+)?$", "an environment file"),
]

# content that is a secret wherever it is
CONTENT_RULES = [
    (rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----", "a private key"),
    (rb"-----BEGIN OPENSSH PRIVATE KEY-----", "an OpenSSH private key"),
    (rb"PuTTY-User-Key-File-\d", "a PuTTY private key"),
    (rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b", "a GitHub token"),
    (rb"\bAKIA[0-9A-Z]{16}\b", "an AWS access key"),
]

# the scanner's own patterns, quoted in this file, are not findings
SELF = "tools/secret_scan.py"


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                         stdout=subprocess.PIPE, check=True).stdout
    return sorted({p for p in out.decode("utf-8", "replace").split("\0") if p})


def main():
    findings = []
    names = [(re.compile(p), why) for p, why in NAME_RULES]
    contents = [(re.compile(p), why) for p, why in CONTENT_RULES]
    files = tracked_files()
    for rel in files:
        for rx, why in names:
            if rx.search(rel):
                findings.append((rel, why + " (by its name)"))
        if rel == SELF:
            continue
        full = os.path.join(ROOT, rel)
        try:
            with open(full, "rb") as f:
                data = f.read(8 << 20)
        except OSError:
            continue
        for rx, why in contents:
            if rx.search(data):
                findings.append((rel, why))
    if findings:
        for rel, why in findings:
            print("SECRET? %s: %s" % (rel, why))
        print("\n%d finding(s). Remove the file from git (git rm --cached <file>), make sure .gitignore covers it, "
              "and if it was ever pushed, treat the secret as leaked: replace the key." % len(findings))
        return 1
    print("secret scan: %d files, nothing found" % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())
