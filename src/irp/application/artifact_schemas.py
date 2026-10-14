"""Published contracts for the generated artifacts (REQUIREMENT.md shapes).

One definition serves three purposes:

* ``RunValidator`` checks every run's artifacts against these models;
* ``irp schemas`` exports them as JSON Schema (draft 2020-12) to ``schemas/`` so an
  evaluator can check the artifacts with any JSON Schema tool;
* a test keeps the committed ``schemas/`` files identical to this module.

The contracts fix the fields the requirement names and allow extra fields, because the
artifacts carry more than the minimum (e.g. ``source_line``, ``policy_rejected_actions``).
Policy-dependent values (incident classes, urgency levels, action names) are plain strings
here: they change with the fixture, so membership is checked by ``RunValidator`` against
the policy file the run used, not by the schema.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, ValidationError

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
ISO_8601_UTC = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"
ACTION_STATUSES = Literal["approved", "auto_cleared_by_policy", "pending_approval", "rejected", "invalidated_by_override"]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="allow")


# -- normalized_alerts.json -------------------------------------------------------------
class NormalizedAlert(_Contract):
    alert_id: str = Field(min_length=1)
    ts: str = Field(pattern=ISO_8601_UTC, description="UTC, ISO-8601 with Z suffix")
    service: str
    severity: Literal["info", "warning", "critical"]
    metric: str
    observed_value: float
    threshold: float
    delta_from_threshold: float = Field(description="observed_value - threshold")
    region: str
    message: str


class NormalizedAlerts(RootModel[list[NormalizedAlert]]):
    root: list[NormalizedAlert] = Field(min_length=1)


# -- correlated_incidents.json ----------------------------------------------------------
class CorrelatedIncident(_Contract):
    incident_id: str = Field(min_length=1)
    alert_ids: list[str] = Field(min_length=1)
    primary_service: str
    impacted_services: list[str] = Field(min_length=1)
    region: str
    correlation_basis: list[str] = Field(min_length=1)
    contains_deploy_signal: bool


class CorrelatedIncidents(RootModel[list[CorrelatedIncident]]):
    root: list[CorrelatedIncident] = Field(min_length=1)


# -- incident_analysis.json -------------------------------------------------------------
class Stage1Classification(_Contract):
    incident_id: str
    incident_class: str = Field(description="one of runbook_policy.incident_classes")
    urgency: str = Field(description="one of runbook_policy.urgency_levels")
    customer_impact_summary: str
    suspected_root_cause: str
    confidence: Literal["low", "medium", "high"]


class RecommendedActionContract(_Contract):
    action: str = Field(description="one of runbook_policy.allowed_actions")
    reason: str
    requires_human_approval: bool = Field(description="taken from runbook_policy.action_policies")
    risk_note: str


class AvoidedActionContract(_Contract):
    action: str
    reason: str


class Stage2Plan(_Contract):
    incident_id: str
    recommended_actions: list[RecommendedActionContract]
    explicitly_avoided_actions: list[AvoidedActionContract]
    rollback_consideration: str
    verification_checks: list[str]


class IncidentAnalysis(_Contract):
    stage1: dict[str, Stage1Classification] = Field(description="keyed by incident_id")
    stage2: dict[str, Stage2Plan] = Field(description="keyed by incident_id; one Stage 2 call each")


# -- operator_decisions.json ------------------------------------------------------------
class OperatorDecision(_Contract):
    type: Literal["approve", "reject", "urgency"]
    incident_id: str
    action: str | None = None
    to_urgency: str | None = None


class FinalAction(_Contract):
    action: str
    status: ACTION_STATUSES
    requires_human_approval: bool


class FinalIncidentState(_Contract):
    incident_class: str
    original_urgency: str
    final_urgency: str
    actions: list[FinalAction]
    final_approved_action_set: list[str]
    pending_actions: list[str]
    rejected_actions: list[str]


class OperatorDecisions(_Contract):
    operator: str
    prompt_shown: str
    decisions: list[OperatorDecision]
    final_state: dict[str, FinalIncidentState] = Field(description="post-review state per incident_id; Stage 3 input")
    stage3_input_digest: str = Field(min_length=1)


# -- llm_calls.jsonl (one record per line) ----------------------------------------------
class LLMCallLogRecord(_Contract):
    stage: str
    incident_id: str | None
    timestamp: str = Field(pattern=ISO_8601_UTC)
    provider: str
    model: str
    prompt_hash: str = Field(min_length=1)
    input_artifacts: list[str] = Field(min_length=1, description="real file paths")
    output_artifact: str = Field(min_length=1, description="real file path")


# -- blast_radius_scores.json -----------------------------------------------------------
class BlastFactor(_Contract):
    points: int = Field(ge=0)


class BlastScore(_Contract):
    incident_id: str
    blast_radius_score: int = Field(ge=0, le=100)
    contributing_factors: dict[str, BlastFactor]
    justification: str = Field(min_length=1)


class BlastRadiusScores(_Contract):
    formula: str = Field(min_length=1)
    computed_by: Literal["deterministic_code"]
    scores: list[BlastScore]


# -- policy_simulation.json -------------------------------------------------------------
class ActionSets(_Contract):
    recommended: list[str]
    pending: list[str]


class SimulationScenario(_Contract):
    scenario: str
    before: dict[str, ActionSets]
    after: dict[str, ActionSets]
    diff: dict[str, Any]


class PolicySimulation(_Contract):
    computed_by: Literal["deterministic_code"]
    llm_calls: Literal[0]
    scenarios: list[SimulationScenario] = Field(min_length=2)


# -- registry ---------------------------------------------------------------------------
class ArtifactContract(BaseModel):
    artifact: str
    schema_file: str
    model: type[BaseModel]
    jsonl: bool = False  # True: the schema applies to each line
    title: str


ARTIFACT_CONTRACTS: tuple[ArtifactContract, ...] = (
    ArtifactContract(artifact="normalized_alerts.json", schema_file="normalized_alerts.schema.json",
                     model=NormalizedAlerts, title="Normalized alerts (REQUIREMENT step 1)"),
    ArtifactContract(artifact="correlated_incidents.json", schema_file="correlated_incidents.schema.json",
                     model=CorrelatedIncidents, title="Correlated incidents (REQUIREMENT step 2)"),
    ArtifactContract(artifact="incident_analysis.json", schema_file="incident_analysis.schema.json",
                     model=IncidentAnalysis, title="Stage 1 classifications and Stage 2 plans (REQUIREMENT steps 3-4)"),
    ArtifactContract(artifact="operator_decisions.json", schema_file="operator_decisions.schema.json",
                     model=OperatorDecisions, title="Operator decisions and post-review state (REQUIREMENT step 5)"),
    ArtifactContract(artifact="llm_calls.jsonl", schema_file="llm_calls.record.schema.json",
                     model=LLMCallLogRecord, jsonl=True, title="One llm_calls.jsonl record"),
    ArtifactContract(artifact="blast_radius_scores.json", schema_file="blast_radius_scores.schema.json",
                     model=BlastRadiusScores, title="Blast radius scores (REQUIREMENT step 7)"),
    ArtifactContract(artifact="policy_simulation.json", schema_file="policy_simulation.schema.json",
                     model=PolicySimulation, title="What-if policy simulation (REQUIREMENT step 10)"),
)


def json_schema(contract: ArtifactContract) -> dict[str, Any]:
    """The JSON Schema document published as ``schemas/<schema_file>``."""
    body = contract.model.model_json_schema()
    body.pop("title", None)
    return {"$schema": SCHEMA_DIALECT, "$id": contract.schema_file, "title": contract.title,
            "description": f"Contract for {contract.artifact}" + (" (applies to each line)" if contract.jsonl else ""),
            **body}


def render_schema(contract: ArtifactContract) -> str:
    return json.dumps(json_schema(contract), indent=2, ensure_ascii=False) + "\n"


def conformance_problems(contract: ArtifactContract, text: str) -> list[str]:
    """Problems found validating an artifact's text against its contract (empty when it conforms)."""
    documents: list[tuple[str, Any]]
    if contract.jsonl:
        documents = [(f"line {n}", json.loads(line)) for n, line in enumerate(text.splitlines(), 1) if line.strip()]
    else:
        documents = [("", json.loads(text))]
    problems: list[str] = []
    for where, doc in documents:
        try:
            contract.model.model_validate(doc)
        except ValidationError as exc:
            for e in exc.errors()[:3]:
                loc = ".".join(map(str, e["loc"])) or "<root>"
                problems.append(f"{contract.artifact}{' ' + where if where else ''}: {loc}: {e['msg']}")
    return problems
