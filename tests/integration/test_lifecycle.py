"""Run lifecycle: graceful interruption and the start/stop/status scripts."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from irp.bootstrap import RunConfig, build_pipeline
from irp.infrastructure.offline_model import OfflineModel
from irp.interface import cli

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


class InterruptingConsole:
    """Operator console that is 'stopped' while the review is waiting for input."""

    mode = "test"

    def show(self, text: str) -> None:
        pass

    def commands(self):
        raise KeyboardInterrupt
        yield  # pragma: no cover


def test_interruption_is_recorded_in_manifest(workspace):
    pipeline = build_pipeline(RunConfig(input_dir=workspace, out_dir=workspace, provider="offline"),
                              console=InterruptingConsole(), model=OfflineModel())
    with pytest.raises(KeyboardInterrupt):
        pipeline.run()
    manifest = json.loads((workspace / "run_manifest.json").read_text())
    assert manifest["termination"]["status"] == "aborted"
    assert manifest["termination"]["last_completed_stage"] == "REMEDIATION_PLANS_DRAFTED"
    assert not (workspace / "incident_brief.md").exists()


def test_failure_is_recorded_in_manifest(workspace):
    (workspace / "alerts.jsonl").write_text("\n")
    with pytest.raises(ValueError):
        build_pipeline(RunConfig(input_dir=workspace, out_dir=workspace, provider="offline"),
                       console=InterruptingConsole(), model=OfflineModel()).run()
    manifest = json.loads((workspace / "run_manifest.json").read_text())
    assert manifest["termination"]["status"] == "failed"
    assert manifest["termination"]["last_completed_stage"] == "INPUTS_LOADED"


def test_cli_returns_130_on_interrupt(workspace, monkeypatch):
    monkeypatch.chdir(workspace)

    def boom(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "build_pipeline", boom)
    assert cli.main(["run", "--provider", "offline"]) == cli.EXIT_INTERRUPTED


def script(name: str, *args: str, run_dir: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "IRP_RUN_DIR": str(run_dir)}
    return subprocess.run([str(SCRIPTS / name), *args], capture_output=True, text=True, env=env, timeout=120)


@pytest.mark.skipif(not (ROOT / ".venv" / "bin" / "irp").exists(), reason="virtualenv not set up")
class TestScripts:
    def test_stop_when_idle(self, tmp_path):
        r = script("stop.sh", run_dir=tmp_path)
        assert r.returncode == 0 and "no pipeline run in progress" in r.stdout

    def test_stale_pid_file_is_cleaned(self, tmp_path):
        (tmp_path / "irp.pid").write_text("999999")
        r = script("status.sh", "-o", str(tmp_path), run_dir=tmp_path)
        assert "IDLE" in r.stdout and not (tmp_path / "irp.pid").exists()

    @pytest.mark.parametrize("args, message", [
        (("-m", "interactive", "-b"), "--background needs a non-interactive review"),
        (("-m", "bogus"), "invalid --mode"),
        (("-p", "llama"), "invalid --provider"),
        (("-m", "scripted", "-d", "/nope.txt"), "decisions file not found"),
        (("--wat",), "unknown option"),
    ])
    def test_start_rejects_bad_arguments(self, tmp_path, args, message):
        r = script("start.sh", *args, run_dir=tmp_path)
        assert r.returncode == 1 and message in r.stderr

    def test_start_rejects_missing_inputs(self, tmp_path):
        r = script("start.sh", "-m", "scripted", "-p", "offline", "-i", str(tmp_path), run_dir=tmp_path)
        assert r.returncode == 1 and "missing input file" in r.stderr

    def test_scripted_foreground_run_then_status(self, tmp_path, workspace):
        out = tmp_path / "out"
        r = script("start.sh", "-m", "scripted", "-p", "offline", "-i", str(workspace), "-o", str(out), run_dir=tmp_path)
        assert r.returncode == 0, r.stderr
        assert (out / "incident_brief.md").exists()
        status = script("status.sh", "-o", str(out), run_dir=tmp_path)
        assert "IDLE" in status.stdout and "RESULTS_FINALISED" in status.stdout and "result             : ok" in status.stdout
