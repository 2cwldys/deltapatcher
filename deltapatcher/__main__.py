"""python -m deltapatcher [command ...]   (no command opens the window)"""

import os
import sys


def main():
    # ssh-add runs this program as its SSH_ASKPASS helper: show the passphrase dialog, print the answer
    if os.environ.get("DELTAPATCHER_ASKPASS") == "1":
        from .gui.askpass import main as askpass_main
        return askpass_main(sys.argv[1:])
    if len(sys.argv) >= 2 and sys.argv[1] == "--selftest":
        from .gui.app import selftest
        return selftest(sys.argv[2] if len(sys.argv) > 2 else None)
    from .cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
