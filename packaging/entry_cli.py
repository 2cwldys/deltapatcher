"""deltapatcher-cli: the command line, and the engine the DeltaPatcher app runs. With no command it
prints the help. Run by ssh-add as its SSH_ASKPASS helper, it hands over the app's passphrase."""

import sys

from deltapatcher.__main__ import main

if __name__ == "__main__":
    if len(sys.argv) == 1:
        sys.argv.append("--help")
    sys.exit(main())
