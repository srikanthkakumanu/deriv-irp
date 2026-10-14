#!/usr/bin/env bash
# Show whether a pipeline run is in progress and the stage reached by the latest run.
#
# Usage: scripts/status.sh [-o|--out-dir DIR]   (default: data/output)
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

OUT_DIR="$ROOT_DIR/data/output"
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--out-dir) OUT_DIR="${2:?}"; shift 2 ;;
    -h|--help)    sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)            die "unknown option: $1" ;;
  esac
done

pid="$(running_pid)"
if [[ -n "$pid" ]]; then
  info "RUNNING (PID $pid, elapsed $(ps -o etime= -p "$pid" | tr -d ' '))"
  [[ -f "$META_FILE" ]] && sed 's/^/        /' "$META_FILE"
  if [[ -f "$META_FILE" ]] && grep -q '^out_dir=' "$META_FILE"; then OUT_DIR="$(sed -n 's/^out_dir=//p' "$META_FILE")"; fi
else
  info "IDLE (no pipeline run in progress)"
fi

MANIFEST="$OUT_DIR/run_manifest.json"
if [[ -f "$MANIFEST" ]]; then
  python3 - "$MANIFEST" <<'PY'
import json, sys
m = json.load(open(sys.argv[1]))
stages = m.get("stages", [])
last = stages[-1] if stages else {}
print(f"        latest run started : {m.get('run_started_at')}")
print(f"        provider           : {m.get('llm_provider', {}).get('provider')}:{m.get('llm_provider', {}).get('model')}")
print(f"        last stage         : {last.get('stage')} at {last.get('entered_at')}")
term = m.get("termination")
if term:
    print(f"        termination        : {term['status']} after {term['last_completed_stage']} ({term['reason']})")
elif last.get("stage") == "RESULTS_FINALISED":
    print(f"        result             : {last.get('details', {}).get('status')}")
PY
else
  info "no run_manifest.json in $OUT_DIR yet."
fi

if [[ -n "$pid" && -f "$LOG_FILE" ]] && grep -q '^background=1' "$META_FILE" 2>/dev/null; then
  echo "        --- last log lines ($LOG_FILE) ---"
  tail -n 5 "$LOG_FILE" | sed 's/^/        /'
fi
