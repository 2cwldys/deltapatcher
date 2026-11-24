#!/bin/sh
# DeltaPatcher's command line, straight from this repository (Linux, macOS, Git Bash).
#   cli/deltapatcher.sh <command> [options]    for example:  cli/deltapatcher.sh update ~/game --source https://game.example.com/
#   cli/deltapatcher.sh                        the help
# Uses the built program (dist/deltapatcher-cli) when there is one, otherwise Python 3.8 or later from
# source. Link it into your PATH to type just "deltapatcher":  ln -s "$PWD/cli/deltapatcher.sh" ~/.local/bin/deltapatcher
set -e
here=$(dirname "$0")
case "$0" in */*) ;; *) here=$(dirname "$(command -v "$0")") ;; esac
ROOT=$(cd "$here/.." && pwd)
[ $# -eq 0 ] && set -- --help

for exe in "$ROOT/dist/deltapatcher-cli" "$ROOT/dist/deltapatcher-cli.exe"; do
    if [ -x "$exe" ]; then
        exec "$exe" "$@"
    fi
done

for py in python3 python; do
    if command -v "$py" >/dev/null 2>&1; then
        PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" exec "$py" -m deltapatcher "$@"
    fi
done
echo "DeltaPatcher needs Python 3.8 or later (https://www.python.org), or the built dist/deltapatcher-cli (build.sh)." >&2
exit 1
