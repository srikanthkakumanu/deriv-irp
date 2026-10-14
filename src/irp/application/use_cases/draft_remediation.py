"""Use case: Stage 2 — one LLM call PER incident (never batched), then domain policy enforcement."""
from __future__ import annotations

from ...domain.alerts import Alert
from ...domain.blast_radius import BlastRadius
from ...domain.incident import Incident
from ...domain.policy import RunbookPolicy
from ...domain.remediation import Classification, RemediationPlan, RemediationProposal
from ..contracts import remediation_contract
from ..llm_gateway import AuditedLLMGateway, LLMTask
from ..prompts import load_prompt

STAGE = "stage2_remediation"


class DraftRemediationPlan:
    def __init__(self, gateway: AuditedLLMGateway, policy: RunbookPolicy) -> None:
        self._gateway = gateway
        self._policy = policy

    def execute(self, incident: Incident, classification: Classification, alerts: tuple[Alert, ...],
                blast: BlastRadius) -> RemediationPlan:
        iid = incident.incident_id
        members = set(incident.alert_ids)
        views = self._policy.view_for(classification.incident_class, classification.urgency)
        task = LLMTask(
            stage=STAGE, incident_id=iid,
            system_prompt=load_prompt("system"), instructions=load_prompt("stage2_remediation"),
            payload={
                "incident": incident.model_dump(mode="json"),
                "incident_alerts": [a.model_dump(mode="json") for a in alerts if a.alert_id in members],
                "stage1_classification": classification.model_dump(mode="json"),
                "contains_deploy_signal": incident.contains_deploy_signal,
                "allowed_actions": list(self._policy.allowed_actions),
                "action_policies_for_this_incident": {k: v.model_dump(mode="json") for k, v in views.items()},
                "blast_radius": {"score": blast.blast_radius_score, "justification": blast.justification},
            },
            response_model=remediation_contract(iid, self._policy),
            input_artifacts=("correlated_incidents.json", "incident_analysis.json#stage1", "runbook_policy.json",
                             "blast_radius_scores.json"),
            output_artifact=f"incident_analysis.json#stage2.{iid}",
        )
        parsed = self._gateway.run(task)
        proposal = RemediationProposal.model_validate(parsed.model_dump())
        # Policy compliance is enforced by the domain, not trusted from the model:
        # disallowed actions are filtered into `policy_rejected_actions` (visible + auditable).
        return self._policy.enforce(proposal, classification)
