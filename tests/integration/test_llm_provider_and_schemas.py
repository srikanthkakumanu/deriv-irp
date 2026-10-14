"""Non-LLM runs are flagged (warn, or fail with --require-llm) and artifacts match the published JSON Schemas."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from irp.application.artifact_schemas import ARTIFACT_CONTRACTS, render_schema
from irp.application.validation import RunValidator
from irp.bootstrap import RunConfig, build_pipeline
from irp.infrastructure.filesystem import FileSystemArtifactStore
from irp.infrastructure.offline_model import OfflineModel
from irp.interface import cli
from irp.interface.operator_console import ScriptedOperatorConsole

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"
LLM_CHECK = "AI stages were answered by a real LLM"
SCHEMA_CHECK = "artifacts conform to the published JSON Schemas"
COMMANDS = ["reject INC-001 switch_to_cached_quotes", "urgency INC-002 sev3", ""]


class StandInFlaggedAsLLM(OfflineModel):
    """Same answers as the offline model, but reports itself as an LLM provider."""

    name = "fake-llm"
    model = "fake-llm-1"
    is_llm = True


def run(workspace: Path, model: OfflineModel, shown: list[str] | None = None):
    console = ScriptedOperatorConsole(COMMANDS, "test", output=shown.append if shown is not None else lambda _: None)
    config = RunConfig(input_dir=workspace, out_dir=workspace, provider="offline")
    return build_pipeline(config, console=console, model=model).run()


def check(results, name):
    return next(r for r in results if r.name == name)


class TestNonLLMRunsAreFlagged:
    def test_offline_run_passes_with_a_warning(self, workspace):
        shown: list[str] = []
        result = run(workspace, OfflineModel(), shown)
        assert result.status == "ok"
        llm = check(result.validation, LLM_CHECK)
        assert llm.ok and llm.warning and "not an LLM" in llm.detail
        assert sum("NOT a language model" in s for s in shown) == 2  # at the start and at the end of the run

    def test_offline_run_is_recorded_as_non_llm(self, workspace):
        run(workspace, OfflineModel())
        manifest = json.loads((workspace / "run_manifest.json").read_text())
        assert manifest["llm_provider"]["is_llm"] is False
        records = [json.loads(l) for l in (workspace / "llm_calls.jsonl").read_text().splitlines()]
        assert records and all(r["provider_is_llm"] is False for r in records)
        assert "NOT an LLM" in (workspace / "incident_brief.md").read_text()

    def test_require_llm_turns_the_warning_into_a_failure(self, workspace):
        run(workspace, OfflineModel())
        store = FileSystemArtifactStore(workspace)
        llm = check(RunValidator(store, store, require_llm=True).run(), LLM_CHECK)
        assert not llm.ok and not llm.warning and "--require-llm" in llm.detail

    def test_llm_provider_passes_without_warning(self, workspace):
        shown: list[str] = []
        result = run(workspace, StandInFlaggedAsLLM(), shown)
        llm = check(result.validation, LLM_CHECK)
        assert llm.ok and not llm.warning and "fake-llm:fake-llm-1" in llm.detail
        assert not any("NOT a language model" in s for s in shown)
        store = FileSystemArtifactStore(workspace)
        assert check(RunValidator(store, store, require_llm=True).run(), LLM_CHECK).ok

    def test_cli_validate_require_llm_exit_codes(self, workspace, monkeypatch, capsys):
        run(workspace, OfflineModel())
        io = ["--input-dir", str(workspace), "--out-dir", str(workspace)]
        assert cli.main(["validate", *io]) == 0
        out = capsys.readouterr().out
        assert f"WARN  {LLM_CHECK}" in out and "(1 with a warning)" in out
        assert cli.main(["validate", *io, "--require-llm"]) == 1
        assert f"FAIL  {LLM_CHECK}" in capsys.readouterr().out


class TestPublishedSchemas:
    def test_committed_schemas_are_up_to_date(self):
        for contract in ARTIFACT_CONTRACTS:
            path = SCHEMAS / contract.schema_file
            assert path.is_file(), f"{path} missing; run `make schemas`"
            assert path.read_text() == render_schema(contract), f"{path} is stale; run `make schemas`"

    def test_schemas_are_valid_draft_2020_12(self):
        for contract in ARTIFACT_CONTRACTS:
            Draft202012Validator.check_schema(json.loads((SCHEMAS / contract.schema_file).read_text()))

    def test_run_artifacts_validate_with_a_third_party_tool(self, workspace):
        run(workspace, OfflineModel())
        for contract in ARTIFACT_CONTRACTS:
            validator = Draft202012Validator(json.loads((SCHEMAS / contract.schema_file).read_text()))
            text = (workspace / contract.artifact).read_text()
            docs = [json.loads(l) for l in text.splitlines() if l.strip()] if contract.jsonl else [json.loads(text)]
            for doc in docs:
                errors = [e.message for e in validator.iter_errors(doc)]
                assert not errors, f"{contract.artifact}: {errors[:3]}"

    @pytest.mark.parametrize(("artifact", "mutate"), [
        ("normalized_alerts.json", lambda d: d[0].pop("delta_from_threshold")),
        ("correlated_incidents.json", lambda d: d[0].update(correlation_basis=[])),
        ("incident_analysis.json", lambda d: next(iter(d["stage1"].values())).update(confidence="certain")),
        ("blast_radius_scores.json", lambda d: d["scores"][0].update(blast_radius_score=101)),
    ])
    def test_nonconforming_artifact_fails_both_validators(self, workspace, artifact, mutate):
        run(workspace, OfflineModel())
        doc = json.loads((workspace / artifact).read_text())
        mutate(doc)
        (workspace / artifact).write_text(json.dumps(doc))
        store = FileSystemArtifactStore(workspace)
        schema_check = check(RunValidator(store, store).run(), SCHEMA_CHECK)
        assert not schema_check.ok and artifact in schema_check.detail
        contract = next(c for c in ARTIFACT_CONTRACTS if c.artifact == artifact)
        validator = Draft202012Validator(json.loads((SCHEMAS / contract.schema_file).read_text()))
        assert list(validator.iter_errors(doc))

    def test_bad_llm_calls_line_is_reported_by_line(self, workspace):
        run(workspace, OfflineModel())
        log = workspace / "llm_calls.jsonl"
        lines = log.read_text().splitlines()
        record = json.loads(lines[1])
        record["timestamp"] = "yesterday"
        lines[1] = json.dumps(record)
        log.write_text("\n".join(lines) + "\n")
        store = FileSystemArtifactStore(workspace)
        detail = check(RunValidator(store, store).run(), SCHEMA_CHECK).detail
        assert "llm_calls.jsonl line 2: timestamp" in detail

    def test_cli_export_and_check(self, tmp_path, capsys):
        target = tmp_path / "schemas"
        assert cli.main(["schemas", "--out-dir", str(target), "--check"]) == 1
        assert cli.main(["schemas", "--out-dir", str(target)]) == 0
        assert sorted(p.name for p in target.iterdir()) == sorted(c.schema_file for c in ARTIFACT_CONTRACTS)
        assert cli.main(["schemas", "--out-dir", str(target), "--check"]) == 0
        assert "up to date" in capsys.readouterr().out
