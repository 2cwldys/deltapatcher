"""The window builds, loads its icon and tray icon, and closes. Shows a window for under a second.
Set DP_SKIP_GUI_TEST=1 to skip (for a machine with no display)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@unittest.skipIf(os.environ.get("DP_SKIP_GUI_TEST") == "1", "DP_SKIP_GUI_TEST=1")
class Window(unittest.TestCase):
    def test_selftest(self):
        from deltapatcher.gui.app import selftest
        fd, out = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            rc = selftest(out)
            with open(out, "r", encoding="utf-8") as f:
                rep = json.load(f)
        finally:
            os.remove(out)
        self.assertEqual(rc, 0, rep)
        self.assertTrue(rep["window_icon"])
        self.assertTrue(rep["ico"] and rep["png"])
        self.assertEqual(rep["tabs"], ["Update", "Publish", "Server"])


if __name__ == "__main__":
    unittest.main()
