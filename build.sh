#!/bin/sh
# Builds DeltaPatcher on Linux or macOS:
#   1. the engine: dist/deltapatcher-cli, the command line in one file, and dist/engine/, the same
#      engine as a folder for the app (PyInstaller, self-contained: no Python needed to run either)
#   2. the app:    dist/app/ (Linux: AppImage and tar.gz; macOS: dmg and zip), with the engine inside
#   3. the release files: dist/release/, both named for a GitHub release
#      (dist/ is not tracked by git: upload the files in dist/release/ to a release yourself)
#
#   ./build.sh                 all of it
#   ./build.sh --test          run the tests first
#   ./build.sh --engine-only
#
# Needs Python 3.11+ (python3, with venv; PYTHON=python3.12 ./build.sh picks another) and Node.js 20+
# with npm: the build tools need 3.11, the engine itself runs on Python 3.8 or later. Python packages go into .venv,
# Node packages into app/node_modules (from app/package-lock.json): nothing is installed system-wide.
set -e
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || "${PYTHON:-python3}" -m venv .venv
PY=.venv/bin/python
$PY -c "import sys; sys.exit(sys.version_info < (3, 11))" || {
    echo "The build needs Python 3.11 or later (its tools do; the engine itself runs on 3.8+)." >&2
    echo "Install a newer Python, delete .venv and run again, e.g.  PYTHON=python3.12 ./build.sh" >&2
    exit 1
}
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
# electron-builder packs a Linux .tar.gz at 7-Zip's slowest gzip level (minutes, on one core); level 6
# takes seconds for a file a few percent bigger. The AppImage has its own compressor.
[ "$(uname)" = "Linux" ] && export ELECTRON_BUILDER_COMPRESSION_LEVEL="${ELECTRON_BUILDER_COMPRESSION_LEVEL:-6}"
npm run typecheck
npm run dist
cd ..
echo "== release files"
$PY tools/release_files.py
