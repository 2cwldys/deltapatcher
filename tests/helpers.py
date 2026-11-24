import atexit
import hashlib
import os
import shutil
import tempfile
import unittest

# the tests never touch the real settings or the real log: both go to a folder of their own, removed after
_SANDBOX = tempfile.mkdtemp(prefix="dp-test-config-")
atexit.register(lambda: shutil.rmtree(_SANDBOX, ignore_errors=True))
os.environ["DELTAPATCHER_CONFIG"] = os.path.join(_SANDBOX, "deltapatcher.ini")
os.environ["DELTAPATCHER_LOG"] = os.path.join(_SANDBOX, "deltapatcher.log")
os.environ["NO_COLOR"] = "1"


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def read(path):
    with open(path, "rb") as f:
        return f.read()


def snapshot(folder):
    """{relative path: sha256} of every file, leaving out DeltaPatcher's own .deltapatcher folder."""
    out = {}
    for dp, dn, fn in os.walk(folder):
        dn[:] = [d for d in dn if d != ".deltapatcher"]
        for f in fn:
            full = os.path.join(dp, f)
            out[os.path.relpath(full, folder).replace("\\", "/")] = hashlib.sha256(read(full)).hexdigest()
    return out


class TempCase(unittest.TestCase):
    """A folder of its own for each test, with its own settings file: what one test saves, the next
    never sees."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dp-test-")
        self._settings = os.environ.get("DELTAPATCHER_CONFIG")
        os.environ["DELTAPATCHER_CONFIG"] = os.path.join(self.tmp, "settings", "deltapatcher.ini")

    def tearDown(self):
        if self._settings is None:
            os.environ.pop("DELTAPATCHER_CONFIG", None)
        else:
            os.environ["DELTAPATCHER_CONFIG"] = self._settings
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.tmp, *parts)
