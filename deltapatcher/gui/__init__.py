"""The window (tkinter), the tray icon and the passphrase dialog."""

import os
import sys


def asset(name):
    """A file in deltapatcher/assets, in the source tree or inside the PyInstaller bundle."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "deltapatcher", "assets", name)
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", name)


def set_window_icon(win):
    """The .ico on Windows (title bar and taskbar), the .png elsewhere. True when one was set."""
    try:
        if os.name == "nt":
            win.iconbitmap(default=asset("deltapatcher.ico"))
            return True
        import tkinter as tk
        img = tk.PhotoImage(file=asset("deltapatcher.png"))
        win.iconphoto(True, img)
        win._dp_icon = img   # keep a reference, or Tk drops the image
        return True
    except Exception:
        return False


def app_user_model_id():
    """Give the process its own taskbar identity on Windows, so the taskbar shows our icon and not
    Python's when run from source."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DeltaPatcher.DeltaPatcher")
    except Exception:
        pass
