"""Colour for the command line, and only where it can be seen.

Colour is used when standard output is a console that understands it: a terminal on Linux and
macOS, and on Windows 10 and later once virtual-terminal processing is switched on for the console.
Output that goes to a file, a pipe or a log is plain text. NO_COLOR (https://no-color.org),
TERM=dumb or --no-color turn it off.

The app's colours (app/tailwind.config.js, the icon's): white, its blue (the teal delta) and its
beige-yellow (the amber blob).
    head  bold white                 a title
    ok    white                      done, up to date, signed, checked
    warn  beige-yellow  #f7c06f      needs your attention
    err   bold amber    #f2a33a      stopped by an error
    code  blue          #3fd1bd      something to copy, type or compare: a key, a fingerprint, a command
    bar   teal          #19b3a0      the progress meter
A terminal that shows any colour exactly (Windows 10 and later, or one that sets COLORTERM=truecolor)
gets these; others get the nearest of the 256 standard ones.
"""

import os
import re
import sys


def _truecolor():
    return os.name == "nt" or os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


def _fg(rgb, nearest_256):
    if _truecolor():
        return "38;2;%d;%d;%d" % (int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16))
    return "38;5;%d" % nearest_256


STYLES = {
    "head": "1;97",
    "ok": "97",
    "warn": _fg("f7c06f", 222),
    "err": "1;" + _fg("f2a33a", 215),
    "code": _fg("3fd1bd", 79),
    "bar": _fg("19b3a0", 37),
}
ANSI = re.compile(r"\x1b\[[0-9;]*m")
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

_on = {}


def _can(stream):
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    try:
        if stream is None or not stream.isatty():
            return False
    except (AttributeError, ValueError):
        return False
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetStdHandle.restype = wintypes.HANDLE
        k32.GetStdHandle.argtypes = [wintypes.DWORD]
        k32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        which = -12 if stream is sys.stderr else -11          # STD_ERROR_HANDLE, STD_OUTPUT_HANDLE
        handle = k32.GetStdHandle(wintypes.DWORD(which & 0xFFFFFFFF))
        mode = wintypes.DWORD()
        if not k32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True
        return bool(k32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING))
    except (AttributeError, OSError, ValueError):
        return False


def enable(allowed=True):
    """Decide once, at start, for standard output and standard error."""
    _on["out"] = bool(allowed) and _can(sys.stdout)
    _on["err"] = bool(allowed) and _can(sys.stderr)


def paint(text, style, stream="out"):
    """text in a style, or unchanged when colour is off for that stream."""
    if stream not in _on:
        _on[stream] = _can(sys.stderr if stream == "err" else sys.stdout)
    if not text or style not in STYLES or not _on[stream]:
        return text
    return "\x1b[%sm%s\x1b[0m" % (STYLES[style], text)


def plain(text):
    return ANSI.sub("", text)
