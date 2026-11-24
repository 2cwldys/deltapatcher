"""DeltaPatcher.exe: the window (also the passphrase dialog ssh-add calls, and --selftest)."""

import os
import sys

from deltapatcher.__main__ import main

if __name__ == "__main__":
    if os.environ.get("DELTAPATCHER_ASKPASS") != "1" and not (len(sys.argv) > 1 and sys.argv[1] == "--selftest"):
        sys.argv = sys.argv[:1]     # a windowed program has no console to print to: always the window
    sys.exit(main())
