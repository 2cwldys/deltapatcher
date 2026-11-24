"""deltapatcher-cli.exe: the command line. With no command it prints the help (use `gui` for the window)."""

import sys

from deltapatcher.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["--help"]))
