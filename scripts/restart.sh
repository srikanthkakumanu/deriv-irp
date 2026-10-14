#!/usr/bin/env bash
# Stop any in-flight run, then start a new one with the given options (same options as start.sh).
#
# Usage: scripts/restart.sh [start.sh options]
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$DIR/stop.sh"
exec "$DIR/start.sh" "$@"
