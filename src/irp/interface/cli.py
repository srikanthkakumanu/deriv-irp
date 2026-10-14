"""Command-line interface: `irp run` and `irp validate`."""
from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

from pydantic import ValidationError

from ..application.llm_gateway import LLMGatewayError
from ..bootstrap import DEFAULT_OUTPUT_DIR, RunConfig, build_pipeline, build_validator
from ..domain.errors import DomainError
from ..infrastructure.filesystem import InputFileError


def load_dotenv(path: Path) -> None:
    """Minimal .env loader (no dependency). Variables already in the environment win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="irp", description="Incident-response orchestration pipeline (planning only; executes nothing).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the full staged pipeline")
    run.add_argument("--input-dir", type=Path, default=None,
                     help="folder with the 3 input files (default: each file from the repo root if present, else data/)")
    run.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="artifact folder (default: data/output)")
    run.add_argument("--provider", choices=["auto", "openai", "offline"], default=os.environ.get("IRP_PROVIDER", "auto"))
    run.add_argument("--decisions-file", type=Path, help="replay operator review commands from a file")
    run.add_argument("--replay", action="store_true", help="serve LLM responses from llm_cache/ (no network)")
    run.add_argument("--window", type=int, default=int(os.environ.get("IRP_CORRELATION_WINDOW_S", "120")))
    run.add_argument("--strict", action="store_true", help="fail on the first malformed alert row")
    run.add_argument("--skip-optional", action="store_true")
    val = sub.add_parser("validate", help="validate the artifacts of the last run")
    val.add_argument("--input-dir", type=Path, default=None,
                     help="folder with the 3 input files (default: the files recorded in the run manifest)")
    val.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="artifact folder (default: data/output)")
    return ap


def validate(input_dir: Path | None, out_dir: Path) -> int:
    try:
        results = build_validator(input_dir, out_dir).run()
    except InputFileError as exc:
        print(f"FAIL  input files: {exc}")
        return 1
    width = max(len(r.name) for r in results)
    for r in results:
        print(f"{'PASS' if r.ok else 'FAIL'}  {r.name:<{width}}  {r.detail}")
    failed = sum(not r.ok for r in results)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


EXIT_INTERRUPTED = 130


def _terminate_gracefully(signum: int, _frame: object) -> None:
    """Turn SIGTERM (sent by scripts/stop.sh) into KeyboardInterrupt so the run is recorded as aborted."""
    raise KeyboardInterrupt(f"signal {signum}")


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path.cwd() / ".env")
    signal.signal(signal.SIGTERM, _terminate_gracefully)
    args = _parser().parse_args(argv)
    if args.cmd == "validate":
        return validate(args.input_dir, args.out_dir)
    config = RunConfig(input_dir=args.input_dir, out_dir=args.out_dir, provider=args.provider,
                       decisions_file=args.decisions_file, replay=args.replay, correlation_window_s=args.window,
                       strict=args.strict, include_optional_outputs=not args.skip_optional)
    try:
        result = build_pipeline(config).run()
    except KeyboardInterrupt:
        print("\n[irp] run aborted; partial artifacts kept, see run_manifest.json 'termination'", file=sys.stderr)
        return EXIT_INTERRUPTED
    except (InputFileError, DomainError, LLMGatewayError, ValidationError, RuntimeError, ValueError) as exc:
        print(f"[irp] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0 if result.status == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
