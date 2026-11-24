import atexit
import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

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


NOT_FOUND = "ERROR: The system cannot find the file specified.\r\r\n"


def empty_scheduler(argv, stdin_text=None):
    """The system's scheduler as a test sees it: no task and no crontab. Anything that would change it fails
    the test: a test never reaches the real one."""
    if list(argv[:2]) == ["schtasks", "/Query"]:
        return subprocess.CompletedProcess(argv, 1, "", NOT_FOUND)
    if list(argv[:2]) == ["crontab", "-l"]:
        return subprocess.CompletedProcess(argv, 1, "", "no crontab for tester\n")
    raise AssertionError("a test reached the system's scheduler: %r" % (argv,))


class TempCase(unittest.TestCase):
    """A folder of its own for each test, with its own settings file: what one test saves, the next
    never sees. The system's scheduler is never reached: backup.run_tool answers as an empty one."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dp-test-")
        self._settings = os.environ.get("DELTAPATCHER_CONFIG")
        os.environ["DELTAPATCHER_CONFIG"] = os.path.join(self.tmp, "settings", "deltapatcher.ini")
        from deltapatcher import backup
        patcher = mock.patch.object(backup, "run_tool", side_effect=empty_scheduler)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        if self._settings is None:
            os.environ.pop("DELTAPATCHER_CONFIG", None)
        else:
            os.environ["DELTAPATCHER_CONFIG"] = self._settings
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, *parts):
        return os.path.join(self.tmp, *parts)
