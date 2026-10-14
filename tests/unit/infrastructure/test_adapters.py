import json

import pytest

from irp.application.ports import LLMRequest
from irp.infrastructure.clock import FixedStepClock
from irp.infrastructure.filesystem import FileSystemArtifactStore, FileSystemInputSource, InputFileError
from irp.infrastructure.offline_model import OfflineModel


class TestFileSystemInputSource:
    def test_loads_and_records_provenance(self, workspace):
        inputs = FileSystemInputSource(workspace, FixedStepClock()).load()
        assert len(inputs.alert_records) == 10
        assert set(inputs.provenance) == {"alerts", "topology", "policy"}
        assert len(inputs.provenance["alerts"].sha256) == 64

    def test_missing_file(self, workspace):
        (workspace / "runbook_policy.json").unlink()
        with pytest.raises(InputFileError, match="missing"):
            FileSystemInputSource(workspace, FixedStepClock()).load()

    def test_invalid_json_policy(self, workspace):
        (workspace / "runbook_policy.json").write_text("{nope")
        with pytest.raises(InputFileError, match="invalid JSON"):
            FileSystemInputSource(workspace, FixedStepClock()).load()

    def test_policy_schema_error(self, workspace):
        (workspace / "runbook_policy.json").write_text(json.dumps({"incident_classes": []}))
        with pytest.raises(InputFileError, match="schema"):
            FileSystemInputSource(workspace, FixedStepClock()).load()

    def test_malformed_and_blank_alert_lines(self, workspace):
        (workspace / "alerts.jsonl").write_text('{"a":1}\n\nnot json\n[1,2]\n')
        records = FileSystemInputSource(workspace, FixedStepClock()).load().alert_records
        assert [r.line_no for r in records] == [1, 3, 4]
        assert records[1].parse_error and records[2].parse_error == "alert must be a JSON object"


class TestArtifactStore:
    def test_roundtrip_and_remove(self, tmp_path):
        store = FileSystemArtifactStore(tmp_path)
        store.save_json("x/y.json", {"a": 1})
        store.append_jsonl("log.jsonl", {"n": 1})
        store.append_jsonl("log.jsonl", {"n": 2})
        assert store.read_json("x/y.json") == {"a": 1}
        assert len(store.read_text("log.jsonl").splitlines()) == 2
        store.remove("x")
        assert not store.exists("x/y.json")

    def test_path_traversal_blocked(self, tmp_path):
        with pytest.raises(ValueError):
            FileSystemArtifactStore(tmp_path / "out").save_text("../escape.txt", "x")


class TestOfflineModel:
    def _req(self, stage, payload):
        return LLMRequest(stage=stage, messages=(), response_schema={}, schema_name=stage, payload=payload)

    def test_stage1_uses_only_policy_labels(self, raw_policy, raw_topology, sample_alerts, topology):
        from irp.domain.incident import IncidentCorrelator
        incidents = IncidentCorrelator(topology).correlate(sample_alerts)
        raw_policy["incident_classes"] = ["something_else", "unknown"]
        out = OfflineModel().complete(self._req("stage1_classification", {
            "correlated_incidents": [i.model_dump(mode="json") for i in incidents],
            "alerts": [a.model_dump(mode="json") for a in sample_alerts],
            "service_topology": raw_topology, "allowed_incident_classes": raw_policy["incident_classes"],
            "allowed_urgency_levels": ["P1", "P2"]}))
        for item in out["incidents"]:
            assert item["incident_class"] in raw_policy["incident_classes"]
            assert item["urgency"] in ("P1", "P2")

    def test_unsupported_stage(self):
        with pytest.raises(ValueError):
            OfflineModel().complete(self._req("stage9", {}))
