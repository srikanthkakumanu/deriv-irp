"""Artifact document models (the persisted JSON shapes) and presenter view models."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..domain.alerts import Alert
from ..domain.blast_radius import BlastRadius
from ..domain.incident import Incident
from ..domain.remediation import Classification, RemediationPlan
from ..domain.review import Decision, IncidentFinalState


class BlastRadiusDocument(BaseModel):
    formula: str
    computed_by: str = "deterministic_code"
    scores: list[BlastRadius]


class IncidentAnalysisDocument(BaseModel):
    stage1: dict[str, Classification] = Field(default_factory=dict)
    stage2: dict[str, RemediationPlan] = Field(default_factory=dict)


class CommandLogEntry(BaseModel):
    seq: int
    ts: str
    line: str
    status: str
    error: str | None = None


class OperatorDecisionsDocument(BaseModel):
    operator: str
    input_mode: str
    review_started_at: str
    review_completed_at: str
    prompt_shown: str
    raw_commands: list[CommandLogEntry]
    decisions: list[Decision]
    final_state: dict[str, IncidentFinalState]
    stage3_input_digest: str | None = None
    note: str = "Operator decisions gate AI recommendations. No infrastructure action is executed by this system."


class ReviewView(BaseModel):
    incidents: list[Incident]
    classifications: dict[str, Classification]
    plans: dict[str, RemediationPlan]
    blast: dict[str, BlastRadius]
    prompt: str


class PostmortemIncidentView(BaseModel):
    incident: Incident
    alerts: list[Alert]
    classification: Classification
    final_state: IncidentFinalState
    inference: dict[str, Any]
    evidence_gaps: list[str]


class PostmortemView(BaseModel):
    incidents: list[PostmortemIncidentView]
