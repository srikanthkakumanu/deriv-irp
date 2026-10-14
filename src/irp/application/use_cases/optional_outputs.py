"""Use cases for SHOULD/STRETCH outputs: safe automation plan, postmortem skeleton, policy simulation."""
from __future__ import annotations

from typing import Any

from ...domain.alerts import Alert, Severity
from ...domain.automation import AutomationTiering
from ...domain.blast_radius import BlastRadius
from ...domain.incident import Incident
from ...domain.policy import RunbookPolicy
from ...domain.remediation import Classification, RemediationPlan
from ...domain.review import Decision, IncidentFinalState
from ...domain.simulation import PolicySimulator, SimulationReport
from ..contracts import AutomationNarrativeResponse, PostmortemResponse
from ..documents import PostmortemIncidentView, PostmortemView
from ..llm_gateway import AuditedLLMGateway, LLMTask
from ..ports import ReportPresenter
from ..prompts import load_prompt

AUTOMATION_SYSTEM = "You advise on safe automation for a trading-platform control plane. Be concise and conservative."
POSTMORTEM_SYSTEM = "You draft postmortem hypotheses for a trading-platform incident. Present nothing as established fact."


class GenerateSafeAutomationPlan:
    def __init__(self, gateway: AuditedLLMGateway, presenter: ReportPresenter, policy: RunbookPolicy,
                 raw_policy: dict[str, Any]) -> None:
        self._gateway = gateway
        self._presenter = presenter
        self._policy = policy
        self._raw_policy = raw_policy

    def execute(self, final_states: dict[str, IncidentFinalState]) -> str:
        tiers = AutomationTiering(self._policy).classify(final_states)
        narrative = self._gateway.run(LLMTask(
            stage="optional_automation_plan", incident_id=None,
            system_prompt=AUTOMATION_SYSTEM, instructions=load_prompt("optional_automation"),
            payload={"tiers": [t.model_dump(mode="json") for t in tiers],
                     "final_state": {k: v.model_dump(mode="json") for k, v in final_states.items()},
                     "policy": self._raw_policy},
            response_model=AutomationNarrativeResponse,
            input_artifacts=("runbook_policy.json", "operator_decisions.json", "incident_analysis.json"),
            output_artifact="safe_automation_plan.md",
        ))
        return self._presenter.automation_plan(tiers, narrative.narrative, self._gateway.provider_label)  # type: ignore[attr-defined]


def evidence_gaps(incident: Incident, alerts: list[Alert], state: IncidentFinalState) -> list[str]:
    gaps = ["Upstream provider/network telemetry for the primary service during the incident window."]
    if incident.contains_deploy_signal:
        gaps.append("Change diff, canary scope and rollout timeline for the deploy/change signal; confirm or exclude it as a cause.")
    if any(a.severity is Severity.CRITICAL for a in alerts):
        gaps.append("Exact client impact: affected orders/quotes, rejected order reasons, P&L exposure.")
    if state.pending_actions:
        gaps.append(f"Outcome of pending decisions: {', '.join(state.pending_actions)}.")
    gaps.append("Confirmation that alert thresholds were correct (no threshold drift or flapping).")
    return gaps


class GeneratePostmortemSkeleton:
    def __init__(self, gateway: AuditedLLMGateway, presenter: ReportPresenter) -> None:
        self._gateway = gateway
        self._presenter = presenter

    def execute(self, incidents: list[Incident], alerts: tuple[Alert, ...], classifications: dict[str, Classification],
                plans: dict[str, RemediationPlan], final_states: dict[str, IncidentFinalState]) -> str:
        views: list[PostmortemIncidentView] = []
        for inc in incidents:
            iid = inc.incident_id
            members = [a for a in alerts if a.alert_id in set(inc.alert_ids)]
            inference = self._gateway.run(LLMTask(
                stage="optional_postmortem", incident_id=iid,
                system_prompt=POSTMORTEM_SYSTEM, instructions=load_prompt("optional_postmortem"),
                payload={"incident": inc.model_dump(mode="json"),
                         "incident_alerts": [a.model_dump(mode="json") for a in members],
                         "stage1_classification": classifications[iid].model_dump(mode="json"),
                         "stage2_plan": plans[iid].model_dump(mode="json"),
                         "final_state": final_states[iid].model_dump(mode="json")},
                response_model=PostmortemResponse,
                input_artifacts=("correlated_incidents.json", "incident_analysis.json", "operator_decisions.json"),
                output_artifact="postmortem_template.md",
            ))
            views.append(PostmortemIncidentView(
                incident=inc, alerts=members, classification=classifications[iid], final_state=final_states[iid],
                inference=inference.model_dump(), evidence_gaps=evidence_gaps(inc, members, final_states[iid]),
            ))
        return self._presenter.postmortem(PostmortemView(incidents=views), self._gateway.provider_label)


class SimulatePolicyWhatIfs:
    def __init__(self, simulator: PolicySimulator) -> None:
        self._simulator = simulator

    def execute(self, alerts: tuple[Alert, ...], incidents: list[Incident], classifications: dict[str, Classification],
                plans: dict[str, RemediationPlan], decisions: list[Decision], final_states: dict[str, IncidentFinalState],
                blast: list[BlastRadius]) -> SimulationReport:
        return self._simulator.run(alerts, incidents, classifications, plans, decisions, final_states,
                                   {b.incident_id: b.blast_radius_score for b in blast})
