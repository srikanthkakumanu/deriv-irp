#!/usr/bin/env bash
# Start a pipeline run.
#
# Usage: scripts/start.sh [options] [-- extra irp args]
#   -m, --mode MODE        interactive (default) | scripted | replay
#   -p, --provider NAME    auto (default) | openai | offline
#   -d, --decisions FILE   operator decisions file (default: fixtures/sample_decisions.txt for scripted/replay)
#   -b, --background       run detached; output goes to .run/irp.log (scripted/replay modes only)
#   -i, --input-dir DIR    directory with alerts.jsonl, service_topology.json, runbook_policy.json (default: data)
#   -o, --out-dir DIR      artifact output directory (default: data/output)
#   -s, --skip-optional    skip automation plan, postmortem and simulation outputs
#   -h, --help
#
# Examples:
#   scripts/start.sh                                   # interactive operator review
#   scripts/start.sh -m scripted -p offline            # scripted review, no network
#   scripts/start.sh -m scripted -b                    # background run; stop with scripts/stop.sh
#   scripts/start.sh -m replay                         # reuse cached LLM responses
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

MODE="interactive"; PROVIDER="${IRP_PROVIDER:-auto}"; DECISIONS=""; BACKGROUND=0
INPUT_DIR="$ROOT_DIR/data"; OUT_DIR="$ROOT_DIR/data/output"; EXTRA=()

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -m|--mode)          MODE="${2:?}"; shift 2 ;;
    -p|--provider)      PROVIDER="${2:?}"; shift 2 ;;
    -d|--decisions)     DECISIONS="${2:?}"; shift 2 ;;
    -b|--background)    BACKGROUND=1; shift ;;
    -i|--input-dir)     INPUT_DIR="${2:?}"; shift 2 ;;
    -o|--out-dir)       OUT_DIR="${2:?}"; shift 2 ;;
    -s|--skip-optional) EXTRA+=(--skip-optional); shift ;;
    -h|--help)          usage; exit 0 ;;
    --)                 shift; EXTRA+=("$@"); break ;;
    *)                  die "unknown option: $1 (see --help)" ;;
  esac
done

case "$MODE" in interactive|scripted|replay) ;; *) die "invalid --mode '$MODE'";; esac
case "$PROVIDER" in auto|openai|offline) ;; *) die "invalid --provider '$PROVIDER'";; esac
if [[ "$MODE" != "interactive" && -z "$DECISIONS" ]]; then
  DECISIONS="$ROOT_DIR/fixtures/sample_decisions.txt"
fi
if [[ -n "$DECISIONS" && ! -f "$DECISIONS" ]]; then die "decisions file not found: $DECISIONS"; fi
if [[ "$MODE" == "interactive" && "$BACKGROUND" -eq 1 ]]; then
  die "--background needs a non-interactive review: use --mode scripted or --mode replay."
fi
for f in alerts.jsonl service_topology.json runbook_policy.json; do
  [[ -f "$INPUT_DIR/$f" ]] || die "missing input file: $INPUT_DIR/$f"
done

if pid="$(running_pid)" && [[ -n "$pid" ]]; then
  die "a pipeline run is already in progress (PID $pid). Use scripts/stop.sh or scripts/status.sh."
fi

ensure_env
mkdir -p "$RUN_DIR" "$OUT_DIR"

ARGS=(run --provider "$PROVIDER" --input-dir "$INPUT_DIR" --out-dir "$OUT_DIR")
[[ -n "$DECISIONS" ]] && ARGS+=(--decisions-file "$DECISIONS")
[[ "$MODE" == "replay" ]] && ARGS+=(--replay)
ARGS+=("${EXTRA[@]+"${EXTRA[@]}"}")

cd "$ROOT_DIR"  # .env is loaded from the working directory
{
  echo "mode=$MODE"; echo "provider=$PROVIDER"; echo "background=$BACKGROUND"
  echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"; echo "out_dir=$OUT_DIR"; echo "args=${ARGS[*]}"
} > "$META_FILE"

if [[ "$BACKGROUND" -eq 1 ]]; then
  PYTHONUNBUFFERED=1 nohup "$IRP_BIN" "${ARGS[@]}" > "$LOG_FILE" 2>&1 < /dev/null &
  echo $! > "$PID_FILE"
  info "started in background (PID $(cat "$PID_FILE")), mode=$MODE provider=$PROVIDER"
  info "log: $LOG_FILE · status: scripts/status.sh · stop: scripts/stop.sh"
else
  info "starting (mode=$MODE provider=$PROVIDER). Ctrl-C or scripts/stop.sh to abort."
  echo $$ > "$PID_FILE"
  # exec keeps this PID, so the PID file points at the pipeline process itself.
  exec "$IRP_BIN" "${ARGS[@]}"
fi
