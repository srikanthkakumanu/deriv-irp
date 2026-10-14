#!/usr/bin/env bash
# Stop an in-flight pipeline run.
#
# Usage: scripts/stop.sh [-t|--timeout SECONDS] [-f|--force]
#   Sends SIGTERM; the pipeline records `termination` (status=aborted, last completed stage)
#   in run_manifest.json and exits with code 130. If it is still alive after the timeout
#   (default 15 s) it is killed with SIGKILL. --force sends SIGKILL immediately.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

TIMEOUT=15; FORCE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -t|--timeout) TIMEOUT="${2:?}"; shift 2 ;;
    -f|--force)   FORCE=1; shift ;;
    -h|--help)    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            die "unknown option: $1" ;;
  esac
done

pid="$(running_pid)"
if [[ -z "$pid" ]]; then
  info "no pipeline run in progress."
  exit 0
fi

if [[ "$FORCE" -eq 1 ]]; then
  warn "force-killing PID $pid (no termination record will be written)."
  kill -KILL "$pid" 2>/dev/null || true
else
  info "sending SIGTERM to PID $pid (timeout ${TIMEOUT}s)..."
  kill -TERM "$pid" 2>/dev/null || true
  for ((i = 0; i < TIMEOUT * 10; i++)); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "$pid" 2>/dev/null; then
    warn "still running after ${TIMEOUT}s; sending SIGKILL."
    kill -KILL "$pid" 2>/dev/null || true
  fi
fi

sleep 0.2
if kill -0 "$pid" 2>/dev/null; then
  die "PID $pid did not exit."
fi
rm -f "$PID_FILE" "$META_FILE"
info "stopped (PID $pid). Partial artifacts are kept; run_manifest.json records where it stopped."
