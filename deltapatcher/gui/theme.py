"""A plain, light ttk look: one accent colour for the actions, everything else quiet."""

import os
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

BG = "#f4f5f7"
PANEL = "#ffffff"
LINE = "#d9dde3"
TEXT = "#1d2330"
MUTED = "#5f6b7a"
ACCENT = "#0e8f80"
ACCENT_HOVER = "#0b7568"
GOOD = "#16794c"
WARN = "#a15c07"
BAD = "#b42318"
LOG_BG = "#1b1f27"
LOG_FG = "#d7dde6"

UI_FONT = ("Segoe UI", 10) if os.name == "nt" else ("Helvetica", 10)
MONO_FONT = ("Consolas", 9) if os.name == "nt" else ("Courier", 10)


def apply(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(background=BG)
    default = tkfont.nametofont("TkDefaultFont")
    default.configure(family=UI_FONT[0], size=UI_FONT[1])
    for name in ("TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=UI_FONT[0], size=UI_FONT[1])
        except tk.TclError:
            pass

    style.configure(".", background=BG, foreground=TEXT, fieldbackground=PANEL, bordercolor=LINE,
                    lightcolor=LINE, darkcolor=LINE, focuscolor=ACCENT)
    style.configure("TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Panel.TLabel", background=PANEL)
    style.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("Title.TLabel", background=BG, foreground=TEXT, font=(UI_FONT[0], 14, "bold"))
    style.configure("Sub.TLabel", background=BG, foreground=MUTED)
    style.configure("Good.TLabel", background=PANEL, foreground=GOOD)
    style.configure("Warn.TLabel", background=PANEL, foreground=WARN)
    style.configure("Bad.TLabel", background=PANEL, foreground=BAD)
    style.configure("TCheckbutton", background=PANEL)
    style.configure("TRadiobutton", background=PANEL)
    style.configure("TEntry", padding=4)
    style.configure("TSpinbox", padding=3)

    style.configure("TButton", padding=(10, 4), background=PANEL, foreground=TEXT)
    style.map("TButton", background=[("active", "#eef0f3"), ("disabled", BG)], foreground=[("disabled", MUTED)])
    style.configure("Accent.TButton", padding=(14, 5), background=ACCENT, foreground="#ffffff", bordercolor=ACCENT,
                    font=(UI_FONT[0], UI_FONT[1], "bold"))
    style.map("Accent.TButton", background=[("active", ACCENT_HOVER), ("disabled", "#9fcfc9")],
              foreground=[("disabled", "#eef7f6")])

    style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
    style.configure("TNotebook.Tab", padding=(16, 6), background=BG, foreground=MUTED, borderwidth=0)
    style.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", TEXT)])

    style.configure("Horizontal.TProgressbar", troughcolor="#e6e9ee", background=ACCENT, bordercolor=LINE,
                    lightcolor=ACCENT, darkcolor=ACCENT, thickness=8)
    return style
