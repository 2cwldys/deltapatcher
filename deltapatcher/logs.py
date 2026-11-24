"""Log files.

- The program's log: everything the window or the command line shows, with the time, in
  <config folder>/logs/deltapatcher.log (Windows: %APPDATA%\\DeltaPatcher\\logs). It rotates at 1 MB
  and keeps three old ones. The command line's --log FILE writes elsewhere, --no-log not at all.
- Each updated folder's own record: <folder>/.deltapatcher/update.log lists every file each update
  added, replaced or removed, so a player or a publisher can see exactly what changed and when.

Nothing secret is written: no passphrase or key ever passes through here, and the values of request
headers given on the command line (an access token, say) are replaced by "***".
"""

import logging
import logging.handlers
import os
import time

from .ssh import config_dir

_logger = None
_path = None


def log_dir():
    return os.path.join(config_dir(), "logs")


def default_path():
    """The program's log: DELTAPATCHER_LOG when set, else logs/deltapatcher.log in the config folder."""
    return os.environ.get("DELTAPATCHER_LOG") or os.path.join(log_dir(), "deltapatcher.log")


def setup(path=None):
    """Start writing the program's log; returns its path (None when it cannot be opened)."""
    global _logger, _path
    path = os.path.abspath(path or default_path())
    if _logger is not None and _path == path:
        return _path
    close()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=1 << 20, backupCount=3, encoding="utf-8")
    except OSError:
        return None
    handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger = logging.getLogger("deltapatcher")
    logger.handlers[:] = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    _logger, _path = logger, path
    return path


def close():
    global _logger, _path
    if _logger is not None:
        for h in list(_logger.handlers):
            h.close()
            _logger.removeHandler(h)
    _logger, _path = None, None


def path():
    return _path


def write(text):
    """One or more lines into the program's log (nothing when logging is off)."""
    if _logger is None or text is None:
        return
    for line in str(text).splitlines() or [""]:
        _logger.info(line.rstrip())


def redact_argv(argv):
    """The command line as text, with the values of --header options hidden."""
    out, hide = [], False
    for a in argv:
        if hide:
            name = a.split(":", 1)[0] if ":" in a else "header"
            out.append("%s: ***" % name)
            hide = False
        elif a == "--header":
            out.append(a)
            hide = True
        elif a.startswith("--header="):
            out.append("--header=***")
        else:
            out.append(a)
    return " ".join(('"%s"' % a) if " " in a else a for a in out)


def folder_record(state_dir, lines):
    """Add an update's record to <folder>/.deltapatcher/update.log (kept under 2 MB)."""
    p = os.path.join(state_dir, "update.log")
    try:
        os.makedirs(state_dir, exist_ok=True)
        if os.path.isfile(p) and os.path.getsize(p) > 2 << 20:
            os.replace(p, p + ".1")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(p, "a", encoding="utf-8") as f:
            for line in lines:
                f.write("%s  %s\n" % (stamp, line))
    except OSError:
        pass
