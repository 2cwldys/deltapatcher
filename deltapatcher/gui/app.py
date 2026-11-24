"""The window: Update (players), Publish (makers), Server (deploys), and one log under them.

Long jobs run on a worker thread; it talks to the window only through a queue, which the window
reads every 100 ms (Tk must only be touched from its own thread).
"""

import json
import os
import queue
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk

from .. import APP_NAME, __version__, client, config, deploy, logs, publish, serve, signing, ssh
from ..manifest import ManifestError
from ..source import DEFAULT_URL, SourceError, insecure_remote
from . import app_user_model_id, asset, set_window_icon, theme
from .tray import Tray

ERRORS = (client.UpdateError, deploy.DeployError, publish.PublishError, ssh.SshError, signing.SignError, SourceError,
          ManifestError, OSError)


def open_path(p):
    """Show a file or folder in the system's own program (Explorer, the text editor)."""
    try:
        if os.name == "nt":
            os.startfile(p)
        else:
            import subprocess
            subprocess.Popen(["xdg-open" if sys.platform != "darwin" else "open", p])
    except OSError as e:
        messagebox.showerror(APP_NAME, "Cannot open %s: %s" % (p, e))


class App(object):
    def __init__(self, root, cfg, persist=True):
        self.root = root
        self.cfg = cfg
        self.persist = persist
        self._status_after = None
        self.q = queue.Queue()
        self.busy = False
        self.cancel = None
        self.server = None
        self.action_buttons = []
        self.vars = {}
        self.icon_ok = set_window_icon(root)
        root.title(APP_NAME)
        root.geometry("840x740")
        root.minsize(700, 640)
        theme.apply(root)

        head = ttk.Frame(root, padding=(18, 14, 18, 6))
        head.pack(fill="x")
        ttk.Label(head, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="  delta patching with SHA-256 blobs   v" + __version__, style="Sub.TLabel").pack(side="left", pady=(5, 0))

        self.nb = ttk.Notebook(root)
        self.nb.pack(fill="both", expand=False, padx=18)
        self._update_tab()
        self._publish_tab()
        self._server_tab()

        bottom = ttk.Frame(root, padding=(18, 10, 18, 14))
        bottom.pack(fill="both", expand=True)
        row = ttk.Frame(bottom)
        row.pack(fill="x")
        self.status = tk.StringVar(value="Ready.")
        ttk.Label(row, textvariable=self.status).pack(side="left")
        self.stop_btn = ttk.Button(row, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="right")
        ttk.Button(row, text="Settings file", command=lambda: open_path(config.ensure())).pack(side="right", padx=(0, 8))
        ttk.Button(row, text="Log folder", command=self.open_log_folder).pack(side="right", padx=(0, 8))
        self.bar = ttk.Progressbar(bottom, mode="determinate", maximum=1000)
        self.bar.pack(fill="x", pady=(6, 8))
        logf = ttk.Frame(bottom)
        logf.pack(fill="both", expand=True)
        self.log_text = tk.Text(logf, height=8, wrap="word", background=theme.LOG_BG, foreground=theme.LOG_FG,
                                insertbackground=theme.LOG_FG, relief="flat", font=theme.MONO_FONT, padx=8, pady=6)
        sb = ttk.Scrollbar(logf, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set, state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tray = Tray(asset("deltapatcher.ico"), lambda: self.q.put(("tray-show",)),
                         lambda: self.q.put(("tray-quit",)))
        self.tray_ok = self.tray.start()
        if not self.tray_ok and self.tray.error:
            self.log(self.tray.error)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll)
        root.after(300, self.refresh_server_status)

    # ---- building blocks ------------------------------------------------------------------

    def var(self, key, kind=tk.StringVar):
        if key not in self.vars:
            v = kind(value=self.cfg.get(key, config.DEFAULTS.get(key)))
            self.vars[key] = v
        return self.vars[key]

    def page(self, title):
        outer = ttk.Frame(self.nb, style="Panel.TFrame", padding=16)
        self.nb.add(outer, text=title)
        outer.columnconfigure(1, weight=1)
        return outer

    def field(self, parent, row, label, key, browse=None, hint=None, width=48, show=None):
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
        ent = ttk.Entry(parent, textvariable=self.var(key), width=width, show=show)
        ent.grid(row=row, column=1, sticky="ew", pady=4)
        if browse:
            ttk.Button(parent, text="Browse", command=lambda: self.browse(key, browse)).grid(row=row, column=2, padx=(8, 0))
        if hint:
            ttk.Label(parent, text=hint, style="Muted.TLabel").grid(row=row + 1, column=1, columnspan=2, sticky="w")
        return ent

    def buttons(self, parent, row, specs):
        f = ttk.Frame(parent, style="Panel.TFrame")
        f.grid(row=row, column=0, columnspan=3, sticky="w", pady=(14, 0))
        for text, cmd, accent in specs:
            b = ttk.Button(f, text=text, command=cmd, style="Accent.TButton" if accent else "TButton")
            b.pack(side="left", padx=(0, 8))
            self.action_buttons.append(b)
        return f

    def browse(self, key, kind):
        cur = self.var(key).get()
        start = cur if os.path.isdir(cur) else (os.path.dirname(cur) if cur else os.path.expanduser("~"))
        if kind == "dir":
            p = filedialog.askdirectory(initialdir=start, mustexist=False)
        else:
            p = filedialog.askopenfilename(initialdir=start)
        if p:
            self.var(key).set(os.path.normpath(p))

    # ---- tabs -----------------------------------------------------------------------------

    def _update_tab(self):
        p = self.page("  Update  ")
        self.field(p, 0, "Source", "source_url", hint="Where the release is: an http(s) address or a folder. Default " + DEFAULT_URL)
        self.field(p, 2, "Folder", "install_dir", browse="dir", hint="The folder to bring up to date (a game folder, for instance).")
        self.field(p, 4, "Publisher key", "trust_key",
                   hint="Optional: the key the publisher gave you (ed25519:...). Empty: the first signed release's key "
                        "is trusted, and only that key after it.")
        self.buttons(p, 6, [("Update", self.do_update, True), ("Check", self.do_check, False),
                            ("Roll back", self.do_rollback, False)])
        self.update_summary = tk.StringVar(value="")
        ttk.Label(p, textvariable=self.update_summary, style="Muted.TLabel", wraplength=640).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def _publish_tab(self):
        p = self.page("  Publish  ")
        self.field(p, 0, "Folder", "publish_src", browse="dir", hint="The files to release: any type (zip, pk3, textures, programs).")
        self.field(p, 2, "Store", "store_dir", browse="dir", hint="Where manifest.json and blobs/ are written. This is what gets served.")
        self.field(p, 4, "Name", "name", width=30)
        self.field(p, 5, "Version", "version", width=16)
        self.field(p, 6, "Protect", "protect", hint="Optional, comma-separated: files clients keep once they have them, e.g. config/*.cfg")
        self.field(p, 8, "Signing key", "signing_key", browse="file")
        sf = ttk.Frame(p, style="Panel.TFrame")
        sf.grid(row=9, column=1, columnspan=2, sticky="w")
        ttk.Checkbutton(sf, text="Sign the release", variable=self.var("sign", tk.BooleanVar)).pack(side="left", padx=(0, 12))
        for text, cmd in (("New signing key...", self.new_signing_key), ("Copy public key", self.copy_public_key)):
            b = ttk.Button(sf, text=text, command=cmd)
            b.pack(side="left", padx=(0, 6), pady=(2, 2))
            self.action_buttons.append(b)
        ttk.Checkbutton(sf, text=".htaccess for Apache", variable=self.var("htaccess", tk.BooleanVar)).pack(side="left", padx=(6, 0))
        f = self.buttons(p, 10, [("Publish", self.do_publish, True)])
        self.serve_btn = ttk.Button(f, text="Serve locally", command=self.toggle_serve)
        self.serve_btn.pack(side="left", padx=(0, 8))
        ttk.Label(f, text="port", style="Panel.TLabel").pack(side="left", padx=(8, 4))
        ttk.Spinbox(f, from_=1024, to=65535, width=7, textvariable=self.var("serve_port", tk.IntVar)).pack(side="left")
        self.serve_state = tk.StringVar(value="")
        ttk.Label(p, textvariable=self.serve_state, style="Good.TLabel").grid(row=11, column=0, columnspan=3, sticky="w", pady=(10, 0))

    def _server_tab(self):
        p = self.page("  Server  ")
        mode = self.var("deploy_mode")
        mf = ttk.Frame(p, style="Panel.TFrame")
        mf.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))
        ttk.Radiobutton(mf, text="A server over SSH (SFTP)", variable=mode, value="sftp",
                        command=self.refresh_mode).pack(side="left", padx=(0, 16))
        ttk.Radiobutton(mf, text="A folder (local web root or share)", variable=mode, value="folder",
                        command=self.refresh_mode).pack(side="left")

        self.sftp_frame = ttk.Frame(p, style="Panel.TFrame")
        self.sftp_frame.grid(row=1, column=0, columnspan=3, sticky="ew")
        s = self.sftp_frame
        s.columnconfigure(1, weight=1)
        self.field(s, 0, "Host", "ssh_host", width=30)
        uf = ttk.Frame(s, style="Panel.TFrame")
        uf.grid(row=1, column=1, sticky="w", pady=4)
        ttk.Label(s, text="User", style="Panel.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Entry(uf, textvariable=self.var("ssh_user"), width=20).pack(side="left")
        ttk.Label(uf, text="  Port", style="Panel.TLabel").pack(side="left")
        ttk.Entry(uf, textvariable=self.var("ssh_port", tk.IntVar), width=7).pack(side="left", padx=(6, 0))
        self.field(s, 2, "Key", "ssh_key", browse="file")
        kb = ttk.Frame(s, style="Panel.TFrame")
        kb.grid(row=3, column=1, columnspan=2, sticky="w")
        for text, cmd in (("New key...", self.new_key), ("Copy server line", self.copy_auth_line),
                          ("Unlock key", self.unlock_key), ("Fix permissions", self.fix_perms)):
            b = ttk.Button(kb, text=text, command=cmd)
            b.pack(side="left", padx=(0, 6), pady=(2, 4))
            self.action_buttons.append(b)
        self.field(s, 4, "Remote folder", "remote_root", hint="The folder on the server its web server serves, e.g. /var/www/game (forward slashes).")
        self.key_state = tk.StringVar(value="")
        self.host_state = tk.StringVar(value="")
        self.key_label = ttk.Label(s, textvariable=self.key_state, style="Muted.TLabel", wraplength=600)
        self.key_label.grid(row=6, column=1, columnspan=2, sticky="w", pady=(6, 0))
        self.host_label = ttk.Label(s, textvariable=self.host_state, style="Muted.TLabel", wraplength=600)
        self.host_label.grid(row=7, column=1, columnspan=2, sticky="w")

        self.folder_frame = ttk.Frame(p, style="Panel.TFrame")
        self.folder_frame.grid(row=1, column=0, columnspan=3, sticky="ew")
        self.folder_frame.columnconfigure(1, weight=1)
        self.field(self.folder_frame, 0, "Folder", "deploy_folder", browse="dir",
                   hint="The release is copied here: manifest.json and the blobs a web server then serves.")

        self.dry = tk.BooleanVar(value=False)
        f = self.buttons(p, 2, [("Deploy", self.do_deploy, True), ("Check host key", self.check_host, False),
                                ("Test connection", self.test_conn, False)])
        self.host_buttons = f.winfo_children()[1:]
        ttk.Checkbutton(f, text="Dry run", variable=self.dry).pack(side="left", padx=(8, 0))
        for k in ("ssh_key", "ssh_host", "ssh_port"):
            self.var(k).trace_add("write", lambda *a: self.status_soon())
        self.refresh_mode()

    def status_soon(self):
        """Refresh the key/host lines once typing pauses (they run small OpenSSH commands)."""
        if self._status_after is not None:
            self.root.after_cancel(self._status_after)
        self._status_after = self.root.after(600, self.refresh_server_status)

    def refresh_mode(self):
        sftp_mode = self.var("deploy_mode").get() == "sftp"
        if sftp_mode:
            self.folder_frame.grid_remove()
            self.sftp_frame.grid()
        else:
            self.sftp_frame.grid_remove()
            self.folder_frame.grid()
        for b in getattr(self, "host_buttons", []):
            b.configure(state="normal" if sftp_mode and not self.busy else "disabled")

    # ---- status -----------------------------------------------------------------------------

    def refresh_server_status(self):
        self._status_after = None
        try:
            self._refresh_server_status()
        except (ssh.SshError, OSError, tk.TclError) as e:
            self.key_state.set("Key: could not be checked (%s)." % e)

    def _refresh_server_status(self):
        key = self.var("ssh_key").get().strip()
        if not key:
            self.key_state.set("Key: none chosen. Without one, ssh uses your default keys and agent.")
        elif not os.path.isfile(key):
            self.key_state.set("Key: %s does not exist yet. Use New key... to make one." % key)
        else:
            words = []
            try:
                words.append("has a passphrase" if ssh.key_has_passphrase(key) else "NO passphrase (anyone who copies the file can use it)")
            except ssh.SshError as e:
                words.append(str(e))
            probs = ssh.permission_problems(key)
            words.append(("readable by others: " + "; ".join(probs) + " (use Fix permissions)") if probs else "owner-only permissions")
            agent = ssh.agent_state()
            if agent == "none":
                words.append("ssh-agent is not running")
            else:
                words.append("unlocked in ssh-agent" if ssh.agent_has(key) else "not in ssh-agent")
            self.key_state.set("Key: " + ", ".join(words) + ".")
        try:
            t = self.target(need_root=False)
            self.host_state.set("Host key: " + ("confirmed earlier." if ssh.is_trusted(t)
                                                else "not confirmed yet. Check host key does it once."))
        except ssh.SshError:
            self.host_state.set("Host key: enter the host first.")

    # ---- logging and the worker -----------------------------------------------------------------

    def log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        logs.write(text)

    def open_log_folder(self):
        p = os.path.dirname(logs.path() or logs.default_path())
        os.makedirs(p, exist_ok=True)
        open_path(p)

    def save_cfg(self):
        if not self.persist:
            return
        for k, v in self.vars.items():
            try:
                self.cfg[k] = v.get()
            except tk.TclError:
                pass
        try:
            config.save(self.cfg)
        except OSError as e:
            self.log("settings not saved: %s" % e)

    def set_busy(self, busy, label=""):
        self.busy = busy
        for b in self.action_buttons:
            b.configure(state="disabled" if busy else "normal")
        self.stop_btn.configure(state="normal" if busy and self.cancel is not None else "disabled")
        if not busy:
            self.refresh_mode()
        self.status.set(label if busy else "Ready.")

    def start(self, label, fn, cancellable=False, done=None):
        if self.busy:
            return
        self.save_cfg()
        self.cancel = threading.Event() if cancellable else None
        self.set_busy(True, label + "...")
        self.bar.configure(value=0)
        self.log("== " + label)

        def log(text):
            self.q.put(("log", text))

        def progress(d, t, l):
            self.q.put(("progress", d, t, l))

        def work():
            try:
                res = fn(log, progress)
                self.q.put(("done", label, res, None, done))
            except ERRORS as e:
                self.q.put(("done", label, None, e, done))
            except Exception as e:
                self.q.put(("done", label, None, RuntimeError("%s\n%s" % (e, traceback.format_exc())), done))

        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        if self.cancel is not None:
            self.cancel.set()
            self.status.set("Stopping...")

    def poll(self):
        """Read the worker's messages. Always comes back in 100 ms, whatever a handler does: a dead
        poll would leave the buttons disabled for good."""
        try:
            while True:
                try:
                    msg = self.q.get_nowait()
                except queue.Empty:
                    break
                try:
                    self.handle(msg)
                except Exception as e:
                    self.set_busy(False)
                    self.cancel = None
                    self.log("FAILED: %s\n%s" % (e, traceback.format_exc()))
        finally:
            self.root.after(100, self.poll)

    def handle(self, msg):
        kind = msg[0]
        if kind == "log":
            self.log(msg[1])
        elif kind == "progress":
            d, t, l = msg[1:]
            self.bar.configure(value=(1000.0 * d / t) if t else 1000)
            if l in ("downloading", "uploading"):
                self.status.set("%s  %s / %s" % (l.capitalize(), publish.human(d), publish.human(t)))
            elif l in ("installing", "done"):
                self.status.set("%s  %d / %d files" % (l.capitalize(), d, t))
            else:
                self.status.set("Hashing  %d / %d files  %s" % (d, t, l[-60:]))
        elif kind == "done":
            _, label, res, err, cb = msg
            self.set_busy(False)
            self.cancel = None
            if err is not None:
                self.bar.configure(value=0)
                self.log("FAILED: %s" % err)
                self.status.set(label + " failed.")
                messagebox.showerror(APP_NAME, "%s failed:\n\n%s" % (label, str(err).split("\n")[0]))
            else:
                self.bar.configure(value=1000)
                self.status.set(label + " done.")
                if cb:
                    cb(res)
        elif kind == "tray-show":
            self.show()
        elif kind == "tray-quit":
            self.quit()

    # ---- update ------------------------------------------------------------------------------

    def _update_args(self):
        url = self.var("source_url").get().strip() or DEFAULT_URL
        folder = self.var("install_dir").get().strip()
        if not folder:
            messagebox.showinfo(APP_NAME, "Choose the folder to update first.")
            return None
        if insecure_remote(url) and not messagebox.askyesno(APP_NAME,
                "%s is plain http to another machine.\n\nEvery file is checked against the manifest's SHA-256, "
                "but the manifest itself could be altered on the way. Use https unless this is a network you trust.\n\n"
                "Continue anyway?" % url, icon="warning"):
            return None
        return url, folder

    def do_check(self):
        a = self._update_args()
        if a:
            key = self.var("trust_key").get().strip() or None
            self.start("Check", lambda log, prog: client.update(a[0], a[1], check_only=True, log=log, progress=prog,
                                                                trust_key=key),
                       done=lambda plan: self.update_summary.set("Up to date." if plan.empty() else plan.summary()))

    def do_update(self):
        a = self._update_args()
        if a:
            w = int(self.cfg.get("workers", 4) or 4)
            key = self.var("trust_key").get().strip() or None
            self.start("Update", lambda log, prog: client.update(a[0], a[1], workers=w, log=log, progress=prog,
                                                                 cancel=self.cancel, trust_key=key),
                       cancellable=True, done=self._updated)

    def _updated(self, plan):
        self.update_summary.set("Up to date." if plan.empty() else "Updated: " + plan.summary())
        if not self.root.winfo_viewable():
            self.tray.notify("Update finished")

    def do_rollback(self):
        folder = self.var("install_dir").get().strip()
        if folder and messagebox.askyesno(APP_NAME, "Put back the files the last update replaced or removed in\n%s?" % folder):
            self.start("Roll back", lambda log, prog: client.rollback(folder, log=log))

    # ---- publish and serve ---------------------------------------------------------------------

    def do_publish(self):
        src, store = self.var("publish_src").get().strip(), self.var("store_dir").get().strip()
        if not src or not store:
            messagebox.showinfo(APP_NAME, "Choose the folder to publish and the store folder first.")
            return
        prot = [x.strip() for x in self.var("protect").get().split(",") if x.strip()]
        name, ver = self.var("name").get().strip(), self.var("version").get().strip()
        signer = None
        if self.var("sign", tk.BooleanVar).get():
            signer = self.load_signer()
            if signer is None:
                return
        ht = bool(self.var("htaccess", tk.BooleanVar).get())
        self.start("Publish", lambda log, prog: publish.publish(src, store, name, ver, prot, progress=prog, log=log,
                                                                signer=signer, htaccess=ht))

    # ---- signing ---------------------------------------------------------------------------------

    def load_signer(self):
        """The signing key, asking its passphrase in a dialog when it has one; None if cancelled or failed."""
        path = self.var("signing_key").get().strip()
        if not signing.can_sign():
            messagebox.showerror(APP_NAME, "Signing needs the 'cryptography' package.")
            return None
        if not path or not os.path.isfile(path):
            messagebox.showinfo(APP_NAME, "There is no signing key at\n%s\n\nMake one with New signing key..." % path)
            return None
        passphrase = None
        if signing.needs_passphrase(path):
            passphrase = ask_secret(self.root, "Signing key", "Passphrase of the signing key\n%s" % path)
            if passphrase is None:
                return None
        try:
            return signing.load_key(path, passphrase)
        except signing.SignError as e:
            messagebox.showerror(APP_NAME, str(e))
            return None
        finally:
            passphrase = None

    def new_signing_key(self):
        if not signing.can_sign():
            messagebox.showerror(APP_NAME, "Making a signing key needs the 'cryptography' package.")
            return
        path = self.var("signing_key").get().strip() or signing.default_key_path()
        if os.path.exists(path):
            messagebox.showinfo(APP_NAME, "A signing key already exists at\n%s\n\nAn existing key is never replaced. "
                                          "Choose another file name in the Signing key field first." % path)
            return
        p1 = ask_secret(self.root, "New signing key", "A passphrase for the new signing key (at least 10 characters).\n"
                                                      "It protects the key if the file is ever copied; it is not saved.")
        if p1 is None:
            return
        if len(p1) < 10:
            messagebox.showerror(APP_NAME, "Use at least 10 characters (a few words work well).")
            return
        if ask_secret(self.root, "New signing key", "The same passphrase again") != p1:
            messagebox.showerror(APP_NAME, "The two passphrases differ.")
            return
        try:
            pub = signing.generate_key(path, p1)
        except (signing.SignError, OSError) as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        finally:
            p1 = None
        self.var("signing_key").set(path)
        self.var("sign", tk.BooleanVar).set(True)
        self.save_cfg()
        self.root.clipboard_clear()
        self.root.clipboard_append(pub)
        self.log("made the signing key %s. Its public key (on the clipboard), to give to players:\n  %s" % (path, pub))
        messagebox.showinfo(APP_NAME, "Signing key made, and Sign the release is on.\n\nIts public key is on the "
                                      "clipboard. Give it to players (or put it in your launcher) so their updater "
                                      "checks your releases are yours.")

    def copy_public_key(self):
        key = self.load_signer()
        if key is None:
            return
        pub = signing.public_key_text(key)
        self.root.clipboard_clear()
        self.root.clipboard_append(pub)
        self.log("the publisher key (on the clipboard): %s" % pub)

    def toggle_serve(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
            self.serve_btn.configure(text="Serve locally")
            self.serve_state.set("")
            self.log("stopped serving")
            return
        store = self.var("store_dir").get().strip()
        try:
            port = int(self.var("serve_port").get())
            self.server, _t = serve.serve_in_background(store, "127.0.0.1", port)
        except (OSError, ValueError, tk.TclError) as e:
            messagebox.showerror(APP_NAME, "Cannot serve: %s" % e)
            return
        url = "http://localhost:%d/" % port
        self.serve_btn.configure(text="Stop serving")
        self.serve_state.set("Serving %s at %s (this computer only). Clients use it as their source." % (store, url))
        self.log("serving %s at %s" % (store, url))

    # ---- server ---------------------------------------------------------------------------------

    def target(self, need_root=True):
        root = self.var("remote_root").get().strip()
        if need_root and not root:
            raise ssh.SshError("enter the remote folder (the folder the server's web server serves)")
        try:
            port = int(self.var("ssh_port").get() or 22)
        except (ValueError, tk.TclError):
            raise ssh.SshError("the port must be a number")
        return ssh.Target(self.var("ssh_host").get(), self.var("ssh_user").get(), port,
                          self.var("ssh_key").get().strip(), root if root else "")

    def confirm_host_then(self, then):
        """Make sure the host key is confirmed, then run `then` on the window's thread."""
        try:
            t = self.target(need_root=False)
        except ssh.SshError as e:
            messagebox.showinfo(APP_NAME, str(e))
            return
        if ssh.is_trusted(t):
            then()
            return

        def got(keys):
            lines = "\n".join("  %-20s %s" % (k, fp) for k, _b, fp in keys)
            ok = messagebox.askyesno(APP_NAME,
                "First connection to %s.\n\nIt offers these host keys:\n%s\n\nCompare them with the fingerprints your "
                "hosting provider shows (or run  ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub  on the server). "
                "Trust this server only if they match.\n\nDo they match?" % (t.known_name(), lines), icon="warning")
            if ok:
                try:
                    ssh.trust(t, keys)
                except (ssh.SshError, OSError) as e:
                    messagebox.showerror(APP_NAME, "The host key could not be saved: %s" % e)
                    return
                self.log("trusted the host key of %s (%s)" % (t.known_name(), t.known_hosts))
                self.refresh_server_status()
                then()
            else:
                self.log("host key not trusted: nothing was sent")

        self.start("Read host key", lambda log, prog: ssh.scan_host(t), done=got)

    def check_host(self):
        self.confirm_host_then(lambda: self.log("the host key of %s is confirmed" % self.target(False).known_name()))

    def test_conn(self):
        def go():
            t = self.target(need_root=False)
            r = self.var("remote_root").get().strip()
            if r:
                t.remote_root = ssh.check_remote_path(r)

            def work(log, prog):
                ok, text = ssh.test_connection(t)
                log(text)
                if not ok:
                    raise ssh.SshError(text)
                return text
            self.start("Test connection", work)
        self.confirm_host_then(go)

    def key_path(self):
        k = self.var("ssh_key").get().strip()
        if not k:
            messagebox.showinfo(APP_NAME, "Choose a key file first (or make one with New key...).")
            return None
        return k

    def new_key(self):
        KeyDialog(self)

    def copy_auth_line(self):
        k = self.key_path()
        if not k:
            return
        try:
            line = ssh.authorized_keys_line(k)
        except ssh.SshError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(line)
        self.log("copied the server line to the clipboard. On the server, add it as one line to\n"
                 "  ~/.ssh/authorized_keys   of the deploy user (it allows SFTP only, nothing else):\n  " + line)

    def unlock_key(self):
        k = self.key_path()
        if not k:
            return
        if ssh.agent_state() == "none":
            messagebox.showinfo(APP_NAME, ssh.agent_help())
            return
        self.start("Unlock key", lambda log, prog: ssh.agent_add(k, use_dialog=True), done=lambda r: self.refresh_server_status())

    def fix_perms(self):
        k = self.key_path()
        if k:
            try:
                ssh.fix_permissions(k)
                self.log("restricted %s to your account" % k)
            except ssh.SshError as e:
                messagebox.showerror(APP_NAME, str(e))
            self.refresh_server_status()

    def do_deploy(self):
        store = self.var("store_dir").get().strip()
        if not store:
            messagebox.showinfo(APP_NAME, "Set the store folder on the Publish tab first.")
            return
        dry = self.dry.get()
        if self.var("deploy_mode").get() == "folder":
            folder = self.var("deploy_folder").get().strip()
            if not folder:
                messagebox.showinfo(APP_NAME, "Choose the folder to deploy to.")
                return
            tgt = deploy.FolderTarget(folder)
            self.start("Deploy", lambda log, prog: deploy.deploy(store, tgt, dry_run=dry, log=log, progress=prog))
            return
        try:
            t = self.target()
        except ssh.SshError as e:
            messagebox.showinfo(APP_NAME, str(e))
            return
        if t.key and os.path.isfile(t.key) and ssh.permission_problems(t.key):
            if not messagebox.askyesno(APP_NAME, "Other accounts can read your private key, and OpenSSH will refuse it.\n\n"
                                                  "Restrict it to your account now?"):
                return
            self.fix_perms()
        self.confirm_host_then(lambda: self.start(
            "Deploy", lambda log, prog: deploy.deploy(store, deploy.SftpTarget(t), dry_run=dry, log=log, progress=prog)))

    # ---- window and tray --------------------------------------------------------------------------

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_close(self):
        if self.cfg.get("close_to_tray") and self.tray_ok:
            self.save_cfg()
            self.root.withdraw()
            self.tray.notify("Still running here. Right-click the icon to quit.")
            return
        self.quit()

    def quit(self):
        if self.busy and not messagebox.askyesno(APP_NAME, "A job is still running. Quit anyway?"):
            return
        if self.cancel is not None:
            self.cancel.set()
        self.save_cfg()
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.tray.stop()
        self.root.destroy()


class KeyDialog(object):
    """Make a new ed25519 key: where, and a passphrase typed twice (never stored)."""

    def __init__(self, app):
        self.app = app
        if not ssh.can_generate():
            messagebox.showinfo(APP_NAME, "Making keys here needs the 'cryptography' package. "
                                          "In a terminal:  deltapatcher ssh keygen")
            return
        w = self.w = tk.Toplevel(app.root)
        w.title("New SSH key")
        w.configure(background=theme.PANEL)
        w.transient(app.root)
        w.resizable(False, False)
        f = ttk.Frame(w, style="Panel.TFrame", padding=18)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        default = app.var("ssh_key").get().strip() or ssh.default_key_path()
        if os.path.exists(default):
            base, n = default, 2
            while os.path.exists(default):
                default = "%s_%d" % (base, n)
                n += 1
        self.path = tk.StringVar(value=default)
        self.p1 = tk.StringVar()
        self.p2 = tk.StringVar()
        ttk.Label(f, text="File", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(f, textvariable=self.path, width=52).grid(row=0, column=1, sticky="ew")
        ttk.Label(f, text="Passphrase", style="Panel.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
        e1 = ttk.Entry(f, textvariable=self.p1, show="*")
        e1.grid(row=1, column=1, sticky="ew")
        ttk.Label(f, text="Again", style="Panel.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 10), pady=4)
        ttk.Entry(f, textvariable=self.p2, show="*").grid(row=2, column=1, sticky="ew")
        ttk.Label(f, text="The passphrase protects the key if the file is ever copied. It is not saved anywhere: "
                          "Unlock key asks for it once per session (ssh-agent). The file is made readable by your "
                          "account only, and an existing key is never replaced.",
                  style="Muted.TLabel", wraplength=420).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        row = ttk.Frame(f, style="Panel.TFrame")
        row.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(row, text="Cancel", command=self.close).pack(side="right")
        ttk.Button(row, text="Create key", style="Accent.TButton", command=self.create).pack(side="right", padx=(0, 8))
        w.bind("<Escape>", lambda e: self.close())
        w.protocol("WM_DELETE_WINDOW", self.close)
        e1.focus_set()
        w.grab_set()

    def close(self):
        self.p1.set("")
        self.p2.set("")
        self.w.grab_release()
        self.w.destroy()

    def create(self):
        p1, p2 = self.p1.get(), self.p2.get()
        if p1 != p2:
            messagebox.showerror(APP_NAME, "The two passphrases differ.", parent=self.w)
            return
        if not p1:
            if not messagebox.askyesno(APP_NAME, "A key without a passphrase works for anyone who gets a copy of the file.\n\n"
                                                  "Make it without one anyway?", icon="warning", parent=self.w):
                return
        elif len(p1) < 10:
            messagebox.showerror(APP_NAME, "Use at least 10 characters (a few words work well).", parent=self.w)
            return
        path = self.path.get().strip()
        try:
            import socket
            ssh.generate_key(path, p1, "deltapatcher@" + socket.gethostname())
        except (ssh.SshError, OSError) as e:
            messagebox.showerror(APP_NAME, str(e), parent=self.w)
            return
        finally:
            p1 = p2 = None
        self.app.var("ssh_key").set(path)
        self.app.save_cfg()
        self.close()
        self.app.log("made %s and %s.pub" % (path, path))
        self.app.copy_auth_line()
        self.app.refresh_server_status()
        messagebox.showinfo(APP_NAME, "Key made.\n\nThe line for the server is on the clipboard: add it to "
                                      "~/.ssh/authorized_keys of the deploy user on the server. Then Unlock key, "
                                      "Check host key, and Test connection.")


def ask_secret(parent, title, prompt):
    """A passphrase typed into a small dialog; None when cancelled. Nothing keeps it."""
    w = tk.Toplevel(parent)
    w.title(title)
    w.configure(background=theme.PANEL)
    w.transient(parent)
    w.resizable(False, False)
    result = {"value": None}
    f = ttk.Frame(w, style="Panel.TFrame", padding=18)
    f.pack(fill="both", expand=True)
    ttk.Label(f, text=prompt, style="Panel.TLabel", wraplength=420).pack(anchor="w")
    var = tk.StringVar()
    ent = ttk.Entry(f, textvariable=var, show="*", width=44)
    ent.pack(fill="x", pady=(10, 0))
    row = ttk.Frame(f, style="Panel.TFrame")
    row.pack(fill="x", pady=(14, 0))

    def ok(_e=None):
        result["value"] = var.get()
        var.set("")
        w.destroy()

    def cancel(_e=None):
        var.set("")
        w.destroy()

    ttk.Button(row, text="Cancel", command=cancel).pack(side="right")
    ttk.Button(row, text="OK", style="Accent.TButton", command=ok).pack(side="right", padx=(0, 8))
    w.bind("<Return>", ok)
    w.bind("<Escape>", cancel)
    w.protocol("WM_DELETE_WINDOW", cancel)
    ent.focus_set()
    w.grab_set()
    parent.wait_window(w)
    return result["value"]


def run():
    app_user_model_id()
    logs.setup()
    logs.write("== DeltaPatcher %s, the window (settings: %s)" % (__version__, config.ensure()))
    root = tk.Tk()
    App(root, config.load())
    root.mainloop()
    logs.close()
    return 0


def selftest(out=None):
    """Build the window, read back what loaded (icon, tray, tabs), close. For the built .exe, which
    has no console: the report goes to `out` as JSON."""
    app_user_model_id()
    root = tk.Tk()
    report = {"ok": False}
    try:
        app = App(root, dict(config.DEFAULTS), persist=False)
        root.update()
        report = {"ok": True, "version": __version__, "window_icon": app.icon_ok, "tray_icon": app.tray_ok,
                  "tray_error": app.tray.error, "tabs": [app.nb.tab(i, "text").strip() for i in app.nb.tabs()],
                  "ico": os.path.isfile(asset("deltapatcher.ico")), "png": os.path.isfile(asset("deltapatcher.png"))}
        root.after(800, app.quit)
        root.mainloop()
    except Exception as e:
        report = {"ok": False, "error": "%s\n%s" % (e, traceback.format_exc())}
        try:
            root.destroy()
        except Exception:
            pass
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    return 0 if report.get("ok") else 1
