#!/usr/bin/env bash
# Shared helpers for the start/stop/status scripts. Sourced, not executed.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="${IRP_RUN_DIR:-$ROOT_DIR/.run}"
PID_FILE="$RUN_DIR/irp.pid"
LOG_FILE="$RUN_DIR/irp.log"
META_FILE="$RUN_DIR/irp.meta"
IRP_BIN="$ROOT_DIR/.venv/bin/irp"

info()  { printf '\033[1;34m[irp]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[irp]\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[1;31m[irp]\033[0m %s\n' "$*" >&2; exit 1; }

# Prints the PID of a live pipeline run, or nothing. Removes stale PID files.
running_pid() {
  [[ -f "$PID_FILE" ]] || return 0
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "$pid"
  else
    rm -f "$PID_FILE" "$META_FILE"
  fi
}

ensure_env() {
  command -v uv >/dev/null 2>&1 || die "uv is not installed (https://docs.astral.sh/uv/)."
  if [[ ! -x "$IRP_BIN" ]]; then
    info "Virtualenv missing; running 'uv sync'..."
    (cd "$ROOT_DIR" && uv sync --quiet)
  fi
  [[ -x "$IRP_BIN" ]] || die "irp entrypoint not found at $IRP_BIN after uv sync."
}
