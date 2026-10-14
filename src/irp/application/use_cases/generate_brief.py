"""Use case: Stage 3 — ONE LLM call producing the incident brief from the POST-REVIEW state only."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from ...domain.blast_radius import BlastRadius
from ...domain.incident import Incident
from ...domain.pipeline_stage import Stage, StageMachine
from ...domain.remediation import Classification, RemediationPlan
from ...domain.review import IncidentFinalState
from ...shared.hashing import digest
from ..contracts import BRIEF_SECTIONS, BriefResponse, split_sections
from ..documents import OperatorDecisionsDocument
from ..llm_gateway import AuditedLLMGateway, LLMTask
from ..ports import Clock, ReportPresenter
from ..prompts import load_prompt

STAGE = "stage3_brief"


def build_brief_payload(incidents: list[Incident], classifications: dict[str, Classification],
                        plans: dict[str, RemediationPlan], decisions: OperatorDecisionsDocument,
                        blast: list[BlastRadius]) -> dict[str, Any]:
    final = decisions.final_state
    return {
        "correlated_incidents": [i.model_dump(mode="json") for i in incidents],
        "stage1_classifications": {k: v.model_dump(mode="json") for k, v in classifications.items()},
        "stage2_remediation_plans": {k: v.model_dump(mode="json") for k, v in plans.items()},
        "operator_decisions": {
            "operator": decisions.operator,
            # Wall-clock timestamps are excluded so identical decisions yield an identical,
            # replayable prompt; they remain in operator_decisions.json for audit.
            "decisions": [d.model_dump(mode="json", exclude={"ts"}) for d in decisions.decisions],
            "rejected_commands": [c.model_dump(mode="json", exclude={"ts", "seq"}) for c in decisions.raw_commands
                                  if c.status == "error"],
        },
        "final_state": {k: v.model_dump(mode="json") for k, v in final.items()},
        "final_urgencies": {k: v.final_urgency for k, v in final.items()},
        "final_approved_action_sets": {k: list(v.final_approved_action_set) for k, v in final.items()},
        "blast_radius_scores": {b.incident_id: b.model_dump(mode="json") for b in blast},
    }


def brief_consistency_problems(markdown: str, final_states: dict[str, IncidentFinalState]) -> list[str]:
    """Checks the brief reflects post-review urgencies and action statuses (used by Stage 3 and validation)."""
    sections = split_sections(markdown)
    missing = [s for s in BRIEF_SECTIONS if s not in sections]
    if missing:
        return [f"missing sections: {missing}"]
    problems: list[str] = []
    order = [h for h in sections if h in BRIEF_SECTIONS]
    if order != list(BRIEF_SECTIONS):
        problems.append(f"sections out of order: {order}")
    approved_sec = sections["Immediate Approved Actions"]
    pending_sec = sections["Actions Pending Human Approval or Escalation"]
    rejected_sec = sections["Actions Explicitly Rejected"]
    for iid, st in final_states.items():
        if iid not in markdown:
            problems.append(f"{iid} not mentioned")
        if st.final_urgency not in markdown:
            problems.append(f"{iid}: final urgency {st.final_urgency} not stated")
        problems += [f"{iid}: approved/cleared action {a} missing from Immediate Approved Actions"
                     for a in st.final_approved_action_set if a not in approved_sec]
        problems += [f"{iid}: pending action {a} missing from Pending section" for a in st.pending_actions if a not in pending_sec]
        problems += [f"{iid}: rejected action {a} missing from Actions Explicitly Rejected"
                     for a in st.rejected_actions + st.invalidated_actions if a not in rejected_sec]
    return problems


class GenerateIncidentBrief:
    def __init__(self, gateway: AuditedLLMGateway, presenter: ReportPresenter, stages: StageMachine, clock: Clock) -> None:
        self._gateway = gateway
        self._presenter = presenter
        self._stages = stages
        self._clock = clock

    @staticmethod
    def input_digest(payload: dict[str, Any]) -> str:
        return digest(payload)

    def execute(self, payload: dict[str, Any], final_states: dict[str, IncidentFinalState]) -> str:
        self._stages.require_exactly(Stage.OPERATOR_REVIEW_COMPLETE, "Incident brief generation")

        def consistent(parsed: BaseModel) -> list[str]:
            return brief_consistency_problems(parsed.brief_markdown, final_states)  # type: ignore[attr-defined]

        parsed = self._gateway.run(LLMTask(
            stage=STAGE, incident_id=None,
            system_prompt=load_prompt("system"), instructions=load_prompt("stage3_brief"),
            payload=payload, response_model=BriefResponse,
            input_artifacts=("correlated_incidents.json", "incident_analysis.json", "operator_decisions.json",
                             "blast_radius_scores.json"),
            output_artifact="incident_brief.md", semantic_check=consistent,
        ))
        body = re.sub(r"^#\s+.*\n+", "", parsed.brief_markdown.strip())  # type: ignore[attr-defined]
        return self._presenter.incident_brief(body, final_states, self._gateway.provider_label, self._clock.now())
