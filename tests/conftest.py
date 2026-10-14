"""Shared fixtures and builders for the test suite."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from irp.domain.alerts import Alert, AlertNormalizer, RawAlertRecord
from irp.domain.policy import RunbookPolicy
from irp.domain.remediation import Classification, ProposedAction, RemediationProposal
from irp.domain.topology import ServiceTopology

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


@pytest.fixture
def raw_topology() -> dict[str, Any]:
    return json.loads((DATA / "service_topology.json").read_text())


@pytest.fixture
def raw_policy() -> dict[str, Any]:
    return json.loads((DATA / "runbook_policy.json").read_text())


@pytest.fixture
def topology(raw_topology) -> ServiceTopology:
    return ServiceTopology.model_validate(raw_topology)


@pytest.fixture
def policy(raw_policy) -> RunbookPolicy:
    return RunbookPolicy.model_validate(raw_policy)


@pytest.fixture
def sample_records() -> list[RawAlertRecord]:
    lines = (DATA / "alerts.jsonl").read_text().splitlines()
    return [RawAlertRecord(line_no=i, payload=json.loads(l)) for i, l in enumerate(lines, 1) if l.strip()]


@pytest.fixture
def sample_alerts(sample_records) -> tuple[Alert, ...]:
    return AlertNormalizer().normalize(sample_records).alerts


def raw_alert(**overrides: Any) -> dict[str, Any]:
    base = {"ts": "2026-03-03T09:00:00Z", "service": "svc-a", "severity": "warning", "metric": "latency_ms",
            "value": 200, "threshold": 100, "region": "r1", "message": "latency high"}
    base.update(overrides)
    return base


def records(*payloads: dict[str, Any]) -> list[RawAlertRecord]:
    return [RawAlertRecord(line_no=i, payload=p) for i, p in enumerate(payloads, 1)]


def alerts_from(*payloads: dict[str, Any]) -> tuple[Alert, ...]:
    return AlertNormalizer().normalize(records(*payloads)).alerts


def classification(incident_id: str = "INC-001", incident_class: str = "market_data_degradation",
                   urgency: str = "sev1") -> Classification:
    return Classification(incident_id=incident_id, incident_class=incident_class, urgency=urgency,
                          customer_impact_summary="impact", suspected_root_cause="cause", confidence="high")


def proposal(incident_id: str = "INC-001", actions: tuple[tuple[str, bool], ...] = ()) -> RemediationProposal:
    return RemediationProposal(
        incident_id=incident_id,
        recommended_actions=tuple(ProposedAction(action=a, reason="r", requires_human_approval=g, risk_note="n") for a, g in actions),
        rollback_consideration="rb", verification_checks=("check",),
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A temp dir holding copies of the three input fixtures."""
    for name in ("alerts.jsonl", "service_topology.json", "runbook_policy.json"):
        shutil.copy(DATA / name, tmp_path / name)
    return tmp_path
