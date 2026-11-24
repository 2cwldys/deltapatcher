"""Log files.

- The program's log: everything the window or the command line shows, with the time, in
  <config folder>/logs/deltapatcher.log (Windows: %APPDATA%\\DeltaPatcher\\logs). It rotates at 1 MB
  and keeps three old ones. The command line's --log FILE writes elsewhere, --no-log not at all.
  A line about a project other than Default starts with the project's name in brackets. Several
  DeltaPatcher programs may write it at the same time (the app runs a job beside an Update all).
- Each updated folder's own record: <folder>/.deltapatcher/update.log lists every file each update
  added, replaced or removed, so a player or a publisher can see exactly what changed and when.

Nothing secret is written: no passphrase or key ever passes through here, and the values of request
headers given on the command line (an access token, say) are replaced by "***".
"""

import logging
import logging.handlers
import os
import sys
import time

from .ssh import config_dir

_logger = None
_path = None
_tag = ""          # the project the lines are about, when it is not Default


class _SharedLog(logging.handlers.RotatingFileHandler):
    """The rotating log, written by several DeltaPatcher programs at once. Windows refuses to rename a file
    another program has open: the rotation is then left for later, quietly, and the lines keep coming."""

    def __init__(self, *args, **kwargs):
        logging.handlers.RotatingFileHandler.__init__(self, *args, **kwargs)
        self.retry_at = 0.0

    def shouldRollover(self, record):
        if time.time() < self.retry_at:
            return False
        return logging.handlers.RotatingFileHandler.shouldRollover(self, record)

    def doRollover(self):
        if self.stream:
            self.stream.close()
            self.stream = None
        moving = self.baseFilename + ".rotating"
        try:
            os.replace(self.baseFilename, moving)
        except OSError:
            moving = None
            self.retry_at = time.time() + 30
        if moving:
            for i in range(self.backupCount - 1, 0, -1):
                older = "%s.%d" % (self.baseFilename, i)
                if os.path.exists(older):
                    try:
                        os.replace(older, "%s.%d" % (self.baseFilename, i + 1))
                    except OSError:
                        pass
            try:
                os.replace(moving, self.baseFilename + ".1")
            except OSError:
                pass
        if not self.delay:
            self.stream = self._open()

    def handleError(self, record):
        if isinstance(sys.exc_info()[1], OSError):
            return              # a full disk, a file held by another program: the log is best effort
        logging.handlers.RotatingFileHandler.handleError(self, record)


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
        handler = _SharedLog(path, maxBytes=1 << 20, backupCount=3, encoding="utf-8")
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
    global _logger, _path, _tag
    if _logger is not None:
        for h in list(_logger.handlers):
            h.close()
            _logger.removeHandler(h)
    _logger, _path, _tag = None, None, ""


def path():
    return _path


def set_project(title):
    """The project the lines written from now on are about (empty: Default, whose lines carry no name)."""
    global _tag
    _tag = title or ""


def write(text, project=None):
    """One or more lines into the program's log (nothing when logging is off). project: the name of the
    project they are about ('' for Default); None: the one set_project() set."""
    if _logger is None or text is None:
        return
    name = _tag if project is None else project
    start = ("[%s] " % name) if name else ""
    for line in str(text).splitlines() or [""]:
        _logger.info(start + line.rstrip())


def redact_argv(argv):
    """The command line as text on one line (a line break in an argument, as release notes have, written as \\n),
    with the values of --header options hidden."""
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
            out.append(a.replace("\r", "\\r").replace("\n", "\\n"))
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
