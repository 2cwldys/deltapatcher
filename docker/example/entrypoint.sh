#!/bin/sh
# Bring the files up to date from your DeltaPatcher release, then start the program the image runs.
# Only what changed is downloaded; files the container made itself (configs, saves) are kept; an update
# that fails part way leaves the folder as it was.
#
#   DP_SOURCE     where the release is, for example https://game.example.com/   (empty: no update)
#   DP_FOLDER     the folder kept up to date                                     (default /data)
#   DP_TRUST_KEY  your publisher key, ed25519:..., when you sign releases         (recommended)
#   DP_REQUIRED   1: do not start when the update fails                          (default: start anyway)
set -e
folder="${DP_FOLDER:-/data}"

if [ -z "$DP_SOURCE" ]; then
    echo "DP_SOURCE is not set: starting without an update" >&2
elif deltapatcher-cli --no-log update "$folder" --source "$DP_SOURCE" ${DP_TRUST_KEY:+--trust-key "$DP_TRUST_KEY"}; then
    :
elif [ "${DP_REQUIRED:-0}" = "1" ]; then
    echo "the update failed: not starting (DP_REQUIRED=1)" >&2
    exit 1
else
    echo "the update failed: starting with the files already there" >&2
fi

exec "$@"
