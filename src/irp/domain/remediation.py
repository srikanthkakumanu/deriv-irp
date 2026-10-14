"""AI analysis value objects: classification, remediation proposals and enforced plans."""
from __future__ import annotations

from enum import Enum

from .base import ValueObject


class Confidence(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Classification(ValueObject):
    """Stage 1 result for one incident (AI-generated, policy-validated)."""

    incident_id: str
    incident_class: str
    urgency: str
    customer_impact_summary: str
    suspected_root_cause: str
    confidence: Confidence
    source: str = "ai_generated"


class ProposedAction(ValueObject):
    action: str
    reason: str
    requires_human_approval: bool
    risk_note: str


class AvoidedAction(ValueObject):
    action: str
    reason: str


class RemediationProposal(ValueObject):
    """Raw Stage 2 AI output for one incident, before policy enforcement."""

    incident_id: str
    recommended_actions: tuple[ProposedAction, ...]
    explicitly_avoided_actions: tuple[AvoidedAction, ...] = ()
    rollback_consideration: str
    verification_checks: tuple[str, ...] = ()


class RecommendedAction(ValueObject):
    action: str
    reason: str
    requires_human_approval: bool
    risk_note: str
    origin: str = "ai_recommendation"
    policy_basis: str
    llm_approval_flag_mismatch: bool = False


class PolicyRejection(ValueObject):
    action: str
    reason: str
    llm_reason: str | None = None


class RemediationPlan(ValueObject):
    """Policy-enforced Stage 2 plan: only permitted actions, approval flags from policy."""

    incident_id: str
    recommended_actions: tuple[RecommendedAction, ...]
    explicitly_avoided_actions: tuple[AvoidedAction, ...]
    rollback_consideration: str
    verification_checks: tuple[str, ...]
    policy_rejected_actions: tuple[PolicyRejection, ...]
    evaluated_against_class: str
    evaluated_against_urgency: str
    source: str = "ai_generated_policy_filtered"

    @property
    def action_names(self) -> list[str]:
        return [a.action for a in self.recommended_actions]

    def without(self, actions: set[str]) -> "RemediationProposal":
        """Re-express as a proposal minus some actions (used by what-if simulation)."""
        return RemediationProposal(
            incident_id=self.incident_id,
            recommended_actions=tuple(
                ProposedAction(action=a.action, reason=a.reason, requires_human_approval=a.requires_human_approval, risk_note=a.risk_note)
                for a in self.recommended_actions if a.action not in actions
            ),
            explicitly_avoided_actions=self.explicitly_avoided_actions,
            rollback_consideration=self.rollback_consideration,
            verification_checks=self.verification_checks,
        )
