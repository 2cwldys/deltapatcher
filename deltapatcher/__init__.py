"""DeltaPatcher: delta patching with SHA-256 blobs.

A publisher turns a folder into a manifest (every file's path, size and SHA-256) and a set of
blobs, one per distinct file, each named by its SHA-256. A client compares the manifest with its
own folder and downloads only the blobs it lacks. The file type does not matter: a texture or an
executable is just bytes with a hash, and a changed .zip or .pk3 is rebuilt from the files inside
it the client already has (parts.py, rebuild.py), so only those that changed travel.
"""

__version__ = "0.3.0"
APP_NAME = "DeltaPatcher"
USER_AGENT = "DeltaPatcher/" + __version__
REPO = "2cwldys/deltapatcher"          # on GitHub: its releases are where DeltaPatcher's own updates come from
