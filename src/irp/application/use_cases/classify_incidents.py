"""Use case: Stage 1 — ONE LLM call classifying all correlated incidents within policy."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ...domain.alerts import Alert
from ...domain.blast_radius import BlastRadius
from ...domain.incident import Incident
from ...domain.policy import RunbookPolicy
from ...domain.remediation import Classification
from ..contracts import classification_contract
from ..llm_gateway import AuditedLLMGateway, LLMTask
from ..prompts import load_prompt

STAGE = "stage1_classification"


class ClassifyIncidents:
    def __init__(self, gateway: AuditedLLMGateway, policy: RunbookPolicy) -> None:
        self._gateway = gateway
        self._policy = policy

    def execute(self, incidents: list[Incident], alerts: tuple[Alert, ...], raw_topology: dict[str, Any],
                raw_policy: dict[str, Any], blast: list[BlastRadius]) -> dict[str, Classification]:
        ids = [i.incident_id for i in incidents]

        def exactly_once(parsed: BaseModel) -> list[str]:
            got = [item.incident_id for item in parsed.incidents]  # type: ignore[attr-defined]
            return [] if sorted(got) == sorted(ids) else [f"each incident_id must appear exactly once: expected {ids}, got {got}"]

        task = LLMTask(
            stage=STAGE, incident_id=None,
            system_prompt=load_prompt("system"), instructions=load_prompt("stage1_classification"),
            payload={
                "correlated_incidents": [i.model_dump(mode="json") for i in incidents],
                "alerts": [a.model_dump(mode="json") for a in alerts],
                "service_topology": raw_topology,
                "policy": raw_policy,
                "allowed_incident_classes": list(self._policy.incident_classes),
                "allowed_urgency_levels": list(self._policy.urgency_levels),
                "blast_radius_scores": {b.incident_id: b.blast_radius_score for b in blast},
            },
            response_model=classification_contract(ids, self._policy),
            input_artifacts=("correlated_incidents.json", "normalized_alerts.json", "service_topology.json",
                             "runbook_policy.json", "blast_radius_scores.json"),
            output_artifact="incident_analysis.json#stage1",
            semantic_check=exactly_once,
        )
        parsed = self._gateway.run(task)
        return {
            item.incident_id: Classification.model_validate(item.model_dump())
            for item in sorted(parsed.incidents, key=lambda x: x.incident_id)  # type: ignore[attr-defined]
        }
