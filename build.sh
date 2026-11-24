#!/bin/sh
# Builds dist/DeltaPatcher (the window) and dist/deltapatcher-cli (the command line) on Linux or macOS.
#   ./build.sh          build
#   ./build.sh --test   run the tests first
# The tray icon on Linux needs an AppIndicator or Xorg tray (pystray's backends); without one the
# program runs the same, with no tray icon.
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
PY=.venv/bin/python
$PY -m pip install --disable-pip-version-check -q -r requirements-build.txt
if [ "$1" = "--test" ]; then
    $PY -m unittest discover -s tests -v
fi
$PY tools/make_icon.py
$PY -m PyInstaller packaging/deltapatcher.spec --noconfirm --distpath dist --workpath build/pyinstaller
ls -l dist
