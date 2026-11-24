"""deltapatcher-cli: the command line, and the engine the DeltaPatcher app runs. With no command it
prints the help (and, double-clicked on Windows, waits for Enter so the window stays open to read it).
Run by ssh-add as its SSH_ASKPASS helper, it hands over the app's passphrase."""

import sys

from deltapatcher.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
