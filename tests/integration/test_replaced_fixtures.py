"""The evaluator may swap fixtures: nothing may depend on sample names, IDs, regions or labels."""
from __future__ import annotations

import json
from pathlib import Path

from irp.bootstrap import RunConfig, build_pipeline
from irp.infrastructure.offline_model import OfflineModel
from irp.interface.operator_console import ScriptedOperatorConsole

TOPOLOGY = {"services": [
    {"name": "fx-feed", "depends_on": [], "tier": "critical_path"},
    {"name": "fx-quoter", "depends_on": ["fx-feed"], "tier": "critical_path"},
    {"name": "execution-svc", "depends_on": ["fx-quoter"], "tier": "critical_path"},
    {"name": "risk-ml", "depends_on": [], "tier": "ai_support"},
]}
POLICY = {
    "incident_classes": ["market_data_degradation", "pricing_staleness", "ai_inference_degradation", "other"],
    "urgency_levels": ["P1", "P2", "P3"],
    "allowed_actions": ["page_primary_oncall", "collect_additional_diagnostics", "disable_ai_assist_path",
                        "restart_consumer", "page_platform_lead"],
    "action_policies": {
        "page_primary_oncall": {"requires_human_approval": False, "max_allowed_if": ["market_data_degradation", "pricing_staleness", "ai_inference_degradation", "other"]},
        "collect_additional_diagnostics": {"requires_human_approval": False, "max_allowed_if": ["market_data_degradation", "pricing_staleness", "ai_inference_degradation", "other"]},
        "disable_ai_assist_path": {"requires_human_approval": True, "max_allowed_if": ["ai_inference_degradation"]},
        "page_platform_lead": {"requires_human_approval": False, "max_allowed_if": ["P1"]},
    },
}
ALERTS = [
    {"ts": "2027-01-10T14:00:00Z", "service": "fx-feed", "severity": "critical", "metric": "tick_gap_ms", "value": 900, "threshold": 100, "region": "ap-south-1", "message": "feed gap"},
    {"ts": "2027-01-10T14:00:20Z", "service": "fx-quoter", "severity": "critical", "metric": "quote_age_ms", "value": 800, "threshold": 200, "region": "ap-south-1", "message": "stale quotes"},
    {"ts": "2027-01-10T14:00:40Z", "service": "execution-svc", "severity": "warning", "metric": "order_reject_rate_pct", "value": 3, "threshold": 1, "region": "ap-south-1", "message": "rejects"},
    {"ts": "2027-01-10T14:30:00Z", "service": "risk-ml", "severity": "warning", "metric": "inference_latency_ms", "value": 500, "threshold": 200, "region": "us-east-2", "message": "slow model"},
    {"ts": "2027-01-10T14:31:00Z", "service": "unknown-svc", "severity": "info", "metric": "release_marker", "value": 1, "threshold": 0, "region": "us-east-2", "message": "release v2"},
]


def write_fixtures(path: Path) -> None:
    (path / "service_topology.json").write_text(json.dumps(TOPOLOGY))
    (path / "runbook_policy.json").write_text(json.dumps(POLICY))
    (path / "alerts.jsonl").write_text("\n".join(json.dumps(a) for a in ALERTS) + "\n")


def test_pipeline_runs_and_validates_on_replaced_fixtures(tmp_path):
    write_fixtures(tmp_path)
    console = ScriptedOperatorConsole(["urgency INC-001 P1", "approve INC-002 disable_ai_assist_path", ""], "t", output=lambda _: None)
    result = build_pipeline(RunConfig(input_dir=tmp_path, out_dir=tmp_path, provider="offline"),
                            console=console, model=OfflineModel()).run()
    assert result.status == "ok", [r.model_dump() for r in result.validation if not r.ok]
    incidents = json.loads((tmp_path / "correlated_incidents.json").read_text())
    assert len(incidents) == 3  # fx chain, risk-ml, orphan release marker in an unknown service
    analysis = json.loads((tmp_path / "incident_analysis.json").read_text())
    for c in analysis["stage1"].values():
        assert c["incident_class"] in POLICY["incident_classes"]
        assert c["urgency"] in POLICY["urgency_levels"]
    decisions = json.loads((tmp_path / "operator_decisions.json").read_text())
    assert decisions["final_state"]["INC-001"]["final_urgency"] == "P1"
    brief = (tmp_path / "incident_brief.md").read_text()
    assert "final urgency **P1**" in brief
    scores = json.loads((tmp_path / "blast_radius_scores.json").read_text())["scores"]
    assert scores[0]["contributing_factors"]["O_order_routing"]["points"] == 15  # "execution-svc" matches pattern
