"""Settings, kept in the user's own config folder (never in the project folder, never in git).

Only addresses, folders and names are kept. No passphrase or private key ever goes in here; the
key is referred to by its path.
"""

import json
import os

from .ssh import config_dir, default_key_path
from .source import DEFAULT_URL

DEFAULTS = {
    "source_url": DEFAULT_URL,
    "install_dir": "",
    "publish_src": "",
    "store_dir": "",
    "name": "",
    "version": "",
    "protect": "",
    "serve_port": 8000,
    "ssh_host": "",
    "ssh_user": "",
    "ssh_port": 22,
    "ssh_key": default_key_path(),
    "remote_root": "",
    "deploy_mode": "sftp",
    "deploy_folder": "",
    "workers": 4,
    "close_to_tray": False,
}
FORBIDDEN = ("pass", "secret", "token", "private")


def path():
    return os.path.join(config_dir(), "config.json")


def load():
    cfg = dict(DEFAULTS)
    try:
        with open(path(), "r", encoding="utf-8") as f:
            raw = json.load(f)
        for k, v in raw.items():
            if k in DEFAULTS and isinstance(v, type(DEFAULTS[k])):
                cfg[k] = v
    except (OSError, ValueError, AttributeError):
        pass
    return cfg


def save(cfg):
    out = {}
    for k, v in cfg.items():
        if k not in DEFAULTS or any(f in k.lower() for f in FORBIDDEN):
            continue
        out[k] = v
    os.makedirs(config_dir(), exist_ok=True)
    tmp = path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, sort_keys=True)
    os.replace(tmp, path())
