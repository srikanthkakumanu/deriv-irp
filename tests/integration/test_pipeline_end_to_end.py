"""End-to-end runs of the full pipeline with the offline model and a scripted operator."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from irp.application.validation import RunValidator
from irp.bootstrap import RunConfig, build_pipeline
from irp.infrastructure.filesystem import FileSystemArtifactStore
from irp.infrastructure.offline_model import OfflineModel
from irp.interface.operator_console import ScriptedOperatorConsole

REQUIRED = ["normalized_alerts.json", "correlated_incidents.json", "incident_analysis.json", "operator_decisions.json",
            "incident_brief.md", "blast_radius_scores.json", "safe_automation_plan.md", "postmortem_template.md",
            "policy_simulation.json", "llm_calls.jsonl", "run_manifest.json"]


def run(workspace: Path, commands: list[str], replay: bool = False, out: Path | None = None):
    out = out or workspace
    console = ScriptedOperatorConsole(commands, "test", output=lambda _: None)
    config = RunConfig(input_dir=workspace, out_dir=out, provider="offline", replay=replay)
    return build_pipeline(config, console=console, model=OfflineModel()).run()


def load(path: Path, name: str):
    return json.loads((path / name).read_text())


def calls(path: Path):
    return [json.loads(l) for l in (path / "llm_calls.jsonl").read_text().splitlines()]


def test_full_run_produces_all_artifacts_and_passes_validation(workspace):
    result = run(workspace, ["approve INC-002 disable_ai_assist_path", "reject INC-001 switch_to_cached_quotes", "urgency INC-002 sev3", ""])
    assert result.status == "ok", [r for r in result.validation if not r.ok]
    for name in REQUIRED:
        assert (workspace / name).exists(), name
    stages = [c["stage"] for c in calls(workspace)]
    assert stages.count("stage1_classification") == 1
    assert stages.count("stage2_remediation") == 2
    assert stages.count("stage3_brief") == 1
    manifest = load(workspace, "run_manifest.json")
    assert manifest["stages"][-1]["stage"] == "RESULTS_FINALISED"


def test_operator_decisions_change_downstream_outputs(tmp_path, workspace):
    a, b = tmp_path / "a", tmp_path / "b"
    run(workspace, ["approve INC-001 switch_to_cached_quotes", ""], out=a)
    run(workspace, ["reject INC-001 switch_to_cached_quotes", "urgency INC-001 sev3", ""], out=b)
    brief_a, brief_b = (a / "incident_brief.md").read_text(), (b / "incident_brief.md").read_text()
    assert "| `switch_to_cached_quotes` | **approved** |" in brief_a
    assert "| `switch_to_cached_quotes` | **rejected** |" in brief_b
    assert "final urgency **sev3**" in brief_b and "final urgency **sev3**" not in brief_a
    assert "| `page_platform_lead` | **invalidated_by_override** |" in brief_b
    stage3_a = [c for c in calls(a) if c["stage"] == "stage3_brief"][0]["prompt_hash"]
    stage3_b = [c for c in calls(b) if c["stage"] == "stage3_brief"][0]["prompt_hash"]
    assert stage3_a != stage3_b


def test_deterministic_artifacts_are_stable_across_runs(tmp_path, workspace):
    a, b = tmp_path / "a", tmp_path / "b"
    run(workspace, [""], out=a)
    run(workspace, [""], out=b)
    for name in ("normalized_alerts.json", "correlated_incidents.json", "blast_radius_scores.json", "policy_simulation.json"):
        assert (a / name).read_text() == (b / name).read_text(), name


def test_replay_reuses_cached_llm_responses(workspace):
    run(workspace, ["reject INC-001 freeze_deployments", ""])
    first = load(workspace, "incident_analysis.json")
    result = run(workspace, ["reject INC-001 freeze_deployments", ""], replay=True)
    assert result.status == "ok"
    assert load(workspace, "incident_analysis.json") == first
    assert all(c["provider"].endswith("(replay)") for c in calls(workspace))


def test_replay_with_different_decisions_fails_cleanly(workspace):
    from irp.application.llm_gateway import LLMGatewayError
    run(workspace, [""])
    with pytest.raises(LLMGatewayError, match="no cached response"):
        run(workspace, ["urgency INC-001 sev0", ""], replay=True)  # Stage 3 input differs -> not cached


def test_rerun_removes_stale_artifacts(workspace):
    run(workspace, [""])
    (workspace / "llm_raw" / "stale.json").write_text("{}")
    run(workspace, [""])
    assert not (workspace / "llm_raw" / "stale.json").exists()
    assert len(calls(workspace)) == 7  # stage1 + 2x stage2 + stage3 + automation + 2x postmortem; not accumulated


def test_malformed_rows_are_skipped_and_reported(workspace):
    with (workspace / "alerts.jsonl").open("a") as fh:
        fh.write("this is not json\n")
        fh.write('{"ts":"2026-03-03T09:00:30Z","service":"api-edge","severity":"bogus","metric":"m","value":1,"threshold":0,"region":"eu-west-1","message":"x"}\n')
    result = run(workspace, [""])
    assert result.status == "ok"
    rejected = next(s for s in load(workspace, "run_manifest.json")["stages"] if s["stage"] == "ALERTS_NORMALIZED")["details"]["rejected_rows"]
    assert [r["line_no"] for r in rejected] == [11, 12]


def test_no_valid_alerts_aborts_before_llm(workspace):
    (workspace / "alerts.jsonl").write_text("\n")
    with pytest.raises(ValueError, match="no valid alerts"):
        run(workspace, [""])
    assert not (workspace / "llm_calls.jsonl").exists()


class TestValidatorDetectsTampering:
    @pytest.fixture
    def done(self, workspace):
        run(workspace, ["reject INC-001 switch_to_cached_quotes", "urgency INC-002 sev3", ""])
        return workspace

    def failures(self, ws):
        store = FileSystemArtifactStore(ws)
        return {r.name for r in RunValidator(store, store).run() if not r.ok}

    def test_clean_run_passes(self, done):
        assert self.failures(done) == set()

    def test_edited_brief_fails(self, done):
        brief = done / "incident_brief.md"
        brief.write_text(brief.read_text().replace("| `switch_to_cached_quotes` | **rejected** |", "| `switch_to_cached_quotes` | **approved** |"))
        assert "incident brief reflects post-review urgencies and actions" in self.failures(done)

    def test_changed_input_fails_provenance(self, done):
        with (done / "service_topology.json").open("a") as fh:
            fh.write("\n")
        assert "input files were read from disk (hashes match)" in self.failures(done)

    def test_missing_stage2_record_fails(self, done):
        lines = [l for l in (done / "llm_calls.jsonl").read_text().splitlines() if '"INC-002"' not in l or "stage2" not in l]
        (done / "llm_calls.jsonl").write_text("\n".join(lines) + "\n")
        assert "each incident has its own Stage 2 call (not batched)" in self.failures(done)

    def test_disallowed_action_in_plan_fails(self, done):
        analysis = load(done, "incident_analysis.json")
        analysis["stage2"]["INC-002"]["recommended_actions"][0]["action"] = "switch_to_cached_quotes"
        (done / "incident_analysis.json").write_text(json.dumps(analysis))
        assert "action policy compatibility is enforced" in self.failures(done)

    def test_tampered_decisions_fail(self, done):
        doc = load(done, "operator_decisions.json")
        doc["final_state"]["INC-002"]["final_urgency"] = "sev0"
        (done / "operator_decisions.json").write_text(json.dumps(doc))
        assert "operator decisions saved and applied to Stage 3 inputs" in self.failures(done)

    def test_missing_artifact_fails(self, done):
        (done / "incident_brief.md").unlink()
        assert "required artifacts exist" in self.failures(done)
