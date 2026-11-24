"""The notification-area (tray) icon, with the same .ico as the window. Optional: without pystray
and Pillow the program runs the same, just without the tray icon."""

import threading


class Tray(object):
    def __init__(self, ico_path, on_show, on_quit, title="DeltaPatcher"):
        self.icon = None
        self.error = None
        try:
            import pystray
            from PIL import Image
        except ImportError as e:
            self.error = "no tray icon (%s)" % e
            return
        try:
            with Image.open(ico_path) as ico:
                try:
                    ico.size = (64, 64)   # pick the 64 px frame of the .ico
                except Exception:
                    pass
                ico.load()
                img = ico.copy()
            menu = pystray.Menu(
                pystray.MenuItem("Show DeltaPatcher", lambda icon, item: on_show(), default=True),
                pystray.MenuItem("Quit", lambda icon, item: on_quit()),
            )
            self.icon = pystray.Icon("deltapatcher", img, title, menu)
        except Exception as e:
            self.icon = None
            self.error = "no tray icon (%s)" % e

    def start(self):
        if not self.icon:
            return False
        try:
            self.icon.run_detached()
        except (NotImplementedError, AttributeError):
            threading.Thread(target=self.icon.run, daemon=True).start()
        except Exception as e:
            self.error = "no tray icon (%s)" % e
            self.icon = None
            return False
        return True

    def notify(self, text):
        if self.icon:
            try:
                self.icon.notify(text, "DeltaPatcher")
            except Exception:
                pass

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
            self.icon = None
