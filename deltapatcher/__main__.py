"""python -m deltapatcher [command ...]   (the command line; the window is the DeltaPatcher app in app/)"""

import os
import sys


def main():
    # ssh-add runs this program as its SSH_ASKPASS helper while the app unlocks a key (ssh.agent_add_with)
    if os.environ.get("DELTAPATCHER_ASKPASS") == "relay":
        from .ssh import askpass_relay
        return askpass_relay()
    from .cli import main as cli_main
    return cli_main()


if __name__ == "__main__":
    sys.exit(main())
