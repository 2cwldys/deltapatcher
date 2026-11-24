#!/bin/sh
# Builds DeltaPatcher on Linux or macOS:
#   1. the engine: dist/deltapatcher-cli, the command line in one file, and dist/engine/, the same
#      engine as a folder for the app (PyInstaller, self-contained: no Python needed to run either)
#   2. the app:    dist/app/ (Linux: AppImage and tar.gz; macOS: dmg and zip), with the engine inside
#
#   ./build.sh                 both
#   ./build.sh --test          run the tests first
#   ./build.sh --engine-only
#
# Needs Python 3.8+ (python3, with venv) and Node.js 20+ with npm. Python packages go into .venv,
# Node packages into app/node_modules (from app/package-lock.json): nothing is installed system-wide.
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || python3 -m venv .venv
PY=.venv/bin/python
$PY -m pip install --disable-pip-version-check -q -r requirements-build.txt
for arg in "$@"; do
    [ "$arg" = "--test" ] && $PY -m unittest discover -s tests
done
$PY tools/make_icon.py
$PY -m PyInstaller packaging/deltapatcher.spec --noconfirm --distpath dist --workpath build/pyinstaller
dist/deltapatcher-cli --json --no-log version | grep -q '"ok": true' || { echo "the built engine does not answer" >&2; exit 1; }
dist/engine/deltapatcher-engine --json --no-log version | grep -q '"ok": true' || { echo "the built engine folder does not answer" >&2; exit 1; }
for arg in "$@"; do
    [ "$arg" = "--engine-only" ] && exit 0
done
cd app
[ -d node_modules ] || npm ci --no-audit --no-fund
npm run typecheck
npm run dist
ls -l ../dist ../dist/app
