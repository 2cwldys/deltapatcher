#!/bin/sh
# Bring the files up to date from your DeltaPatcher release, then start the program the image runs.
# Only what changed is downloaded; files the container made itself (configs, saves) are kept; an update
# that fails part way leaves the folder as it was.
#
#   DP_PROJECT_FILE  a project file from the publisher (.deltapatch or .html) in the container: the address
#                    and the publisher key come from it, its signature checked, instead of DP_SOURCE and
#                    DP_TRUST_KEY. It is read at every start: hand the container a newer file, and the next
#                    start uses it
#   DP_PROJECT_NAME  with a project file that holds several projects: the one this container keeps
#   DP_SOURCE        where the release is, for example https://game.example.com/  (neither set: no update)
#   DP_FOLDER        the folder kept up to date                                    (default /data)
#   DP_TRUST_KEY     your publisher key, ed25519:..., when you sign releases        (recommended)
#   DP_REQUIRED      1: do not start when the update fails                         (default: start anyway)
set -e
folder="${DP_FOLDER:-/data}"

update() {
    if [ -n "$DP_PROJECT_FILE" ]; then
        deltapatcher-cli --no-log update "$folder" --project-file "$DP_PROJECT_FILE" ${DP_PROJECT_NAME:+--only "$DP_PROJECT_NAME"}
    else
        deltapatcher-cli --no-log update "$folder" --source "$DP_SOURCE" ${DP_TRUST_KEY:+--trust-key "$DP_TRUST_KEY"}
    fi
}

if [ -z "$DP_PROJECT_FILE" ] && [ -z "$DP_SOURCE" ]; then
    echo "neither DP_PROJECT_FILE nor DP_SOURCE is set: starting without an update" >&2
elif update; then
    :
elif [ "${DP_REQUIRED:-0}" = "1" ]; then
    echo "the update failed: not starting (DP_REQUIRED=1)" >&2
    exit 1
else
    echo "the update failed: starting with the files already there" >&2
fi

exec "$@"
