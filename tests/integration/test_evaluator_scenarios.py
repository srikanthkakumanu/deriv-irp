"""Evaluator-style runs: clean checkout layout, replacement fixtures dropped in the repo root."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from irp.bootstrap import RunConfig, build_pipeline, build_validator
from irp.infrastructure.offline_model import OfflineModel
from irp.interface.operator_console import ScriptedOperatorConsole

DATA = Path(__file__).resolve().parents[2] / "data"


@pytest.fixture
def checkout(tmp_path, monkeypatch) -> Path:
    """Repo-like layout: bundled fixtures in data/, cwd = repo root."""
    (tmp_path / "data").mkdir()
    for name in ("alerts.jsonl", "service_topology.json", "runbook_policy.json"):
        shutil.copy(DATA / name, tmp_path / "data" / name)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(commands=("",)):
    console = ScriptedOperatorConsole(list(commands), "test", output=lambda _: None)
    return build_pipeline(RunConfig(out_dir=Path("data/output"), provider="offline"),
                          console=console, model=OfflineModel()).run()


def regions(out: Path) -> set[str]:
    return {a["region"] for a in json.loads((out / "normalized_alerts.json").read_text())}


def test_default_run_uses_bundled_fixtures(checkout):
    assert run().status == "ok"
    manifest = json.loads((checkout / "data/output/run_manifest.json").read_text())
    assert {v["origin"] for v in manifest["inputs"].values()} == {"data_dir"}
    assert regions(checkout / "data/output") == {"eu-west-1"}


def test_replacement_fixtures_in_repo_root_are_used(checkout):
    replaced = (DATA / "alerts.jsonl").read_text().replace("eu-west-1", "ap-south-1")
    (checkout / "alerts.jsonl").write_text(replaced)
    result = run()
    assert result.status == "ok", [r for r in result.validation if not r.ok]
    out = checkout / "data/output"
    assert regions(out) == {"ap-south-1"}  # the replaced file, not the bundled sample
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["inputs"]["alerts"]["origin"] == "repo_root"
    assert manifest["inputs"]["policy"]["origin"] == "data_dir"
    assert any("alerts.jsonl" in n for n in manifest["input_notices"])


def test_standalone_validate_checks_the_files_the_run_used(checkout):
    (checkout / "alerts.jsonl").write_text((DATA / "alerts.jsonl").read_text().replace("eu-west-1", "ap-south-1"))
    run()
    results = build_validator(None, Path("data/output")).run()
    assert all(r.ok for r in results), [r for r in results if not r.ok]
    # replacing the input after the run must be detected, not silently re-validated
    (checkout / "alerts.jsonl").write_text((DATA / "alerts.jsonl").read_text())
    failed = {r.name for r in build_validator(None, Path("data/output")).run() if not r.ok}
    assert "input files were read from disk (hashes match)" in failed


def test_llm_call_log_uses_real_paths(checkout):
    (checkout / "runbook_policy.json").write_text((DATA / "runbook_policy.json").read_text())
    run()
    calls = [json.loads(l) for l in (checkout / "data/output/llm_calls.jsonl").read_text().splitlines()]
    s1 = next(c for c in calls if c["stage"] == "stage1_classification")
    assert "runbook_policy.json" in s1["input_artifacts"]            # read from the repo root
    assert "data/service_topology.json" in s1["input_artifacts"]     # bundled copy
    assert s1["output_artifact"] == "data/output/incident_analysis.json"
    assert s1["output_section"] == "stage1" and s1["input_sections"] == {}
    s2 = [c for c in calls if c["stage"] == "stage2_remediation"]
    assert {c["output_section"] for c in s2} == {"stage2.INC-001", "stage2.INC-002"}
    assert all(c["input_sections"] == {"data/output/incident_analysis.json": "stage1"} for c in s2)
    for c in calls:
        for path in [*c["input_artifacts"], c["output_artifact"]]:
            assert "#" not in path and Path(path).is_file(), path


def test_validator_flags_bogus_logged_path(checkout):
    run()
    log = checkout / "data/output/llm_calls.jsonl"
    log.write_text(log.read_text().replace('"output_artifact": "data/output/incident_brief.md"', '"output_artifact": "nowhere.md"'))
    failed = {r.name for r in build_validator(None, Path("data/output")).run() if not r.ok}
    assert "llm_calls.jsonl artifact paths exist on disk" in failed


def test_validate_without_any_inputs_reports_clearly(tmp_path, monkeypatch, capsys):
    from irp.interface import cli
    monkeypatch.chdir(tmp_path)
    assert cli.main(["validate"]) == 1
    assert "FAIL  input files" in capsys.readouterr().out


def test_corrupt_manifest_falls_back_to_normal_resolution(checkout):
    run()
    (checkout / "data/output/run_manifest.json").write_text("{not json")
    results = build_validator(None, Path("data/output")).run()
    failed = {r.name for r in results if not r.ok}
    assert "JSON artifacts are valid" in failed  # reported as a validation failure, not a crash
