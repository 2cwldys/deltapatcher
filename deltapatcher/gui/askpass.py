"""The passphrase dialog that ssh-add shows through SSH_ASKPASS.

ssh-add runs this program with the prompt as its argument and reads the answer from its standard
output. The passphrase goes from the dialog straight to that pipe: it is not logged or kept.
Exit 1 means the person cancelled.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk

from . import set_window_icon, theme


def ask(prompt):
    root = tk.Tk()
    root.withdraw()
    theme.apply(root)
    set_window_icon(root)
    win = tk.Toplevel(root)
    win.title("DeltaPatcher - unlock key")
    win.configure(background=theme.PANEL)
    win.resizable(False, False)
    win.attributes("-topmost", True)
    result = {"value": None}

    frm = ttk.Frame(win, style="Panel.TFrame", padding=18)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text=prompt or "Passphrase for the SSH key:", style="Panel.TLabel", wraplength=380).pack(anchor="w")
    ttk.Label(frm, text="ssh-agent keeps the key unlocked until you log out. The passphrase is not saved.",
              style="Muted.TLabel", wraplength=380).pack(anchor="w", pady=(4, 10))
    var = tk.StringVar()
    ent = ttk.Entry(frm, textvariable=var, show="*", width=44)
    ent.pack(fill="x")
    row = ttk.Frame(frm, style="Panel.TFrame")
    row.pack(fill="x", pady=(14, 0))

    def ok(_e=None):
        result["value"] = var.get()
        var.set("")
        root.quit()

    def cancel(_e=None):
        var.set("")
        root.quit()

    ttk.Button(row, text="Cancel", command=cancel).pack(side="right")
    ttk.Button(row, text="Unlock", style="Accent.TButton", command=ok).pack(side="right", padx=(0, 8))
    win.bind("<Return>", ok)
    win.bind("<Escape>", cancel)
    win.protocol("WM_DELETE_WINDOW", cancel)
    win.update_idletasks()
    x = (win.winfo_screenwidth() - win.winfo_width()) // 2
    y = (win.winfo_screenheight() - win.winfo_height()) // 3
    win.geometry("+%d+%d" % (x, y))
    ent.focus_force()
    root.mainloop()
    root.destroy()
    return result["value"]


def main(argv):
    value = ask(" ".join(argv).strip())
    if value is None:
        return 1
    data = (value + "\n").encode("utf-8")
    value = None
    try:
        os.write(1, data)     # fd 1 is ssh-add's pipe, also in a windowed build
    except OSError:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
