"""DeltaPatcher's MCP server, started from a clone of the repository: python mcp/server.py

The built command line starts the same server with `deltapatcher mcp`; see mcp/README.md.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from deltapatcher import mcp_server  # noqa: E402

if __name__ == "__main__":
    sys.exit(mcp_server.main())
