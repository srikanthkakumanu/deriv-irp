"""Runbook policy aggregate — the single source of truth for action permissions.

``max_allowed_if`` is interpreted as an allow-list that may contain incident classes
and/or urgency levels: an action is permitted when the incident's class OR its urgency
is listed. Actions in ``allowed_actions`` with no ``action_policies`` entry are
permitted for any class but ALWAYS require human approval (conservative default).
``requires_human_approval`` always comes from policy, never from the LLM.
"""
from __future__ import annotations

from pydantic import Field, model_validator

from .base import ValueObject
from .remediation import (
    Classification,
    PolicyRejection,
    RecommendedAction,
    RemediationPlan,
    RemediationProposal,
)


class ActionPolicy(ValueObject):
    requires_human_approval: bool = True
    max_allowed_if: tuple[str, ...] = ()


class Permission(ValueObject):
    allowed: bool
    reason: str


class ActionPolicyView(ValueObject):
    """Per-incident view of one action's policy (sent to the Stage 2 prompt)."""

    permitted_for_this_incident: bool
    requires_human_approval: bool
    max_allowed_if: tuple[str, ...] | None
    policy_reason: str


class RunbookPolicy(ValueObject):
    incident_classes: tuple[str, ...] = Field(min_length=1)
    urgency_levels: tuple[str, ...] = Field(min_length=1)
    allowed_actions: tuple[str, ...] = Field(min_length=1)
    action_policies: dict[str, ActionPolicy] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _no_duplicates(self) -> "RunbookPolicy":
        for name in ("incident_classes", "urgency_levels", "allowed_actions"):
            values = getattr(self, name)
            if len(set(values)) != len(values):
                raise ValueError(f"{name} contains duplicates")
        return self

    @property
    def fallback_class(self) -> str:
        return "unknown" if "unknown" in self.incident_classes else self.incident_classes[-1]

    # -- queries -------------------------------------------------------------
    def has_policy(self, action: str) -> bool:
        return action in self.action_policies

    def requires_approval(self, action: str) -> bool:
        policy = self.action_policies.get(action)
        return True if policy is None else policy.requires_human_approval

    def permission(self, action: str, incident_class: str, urgency: str) -> Permission:
        if action not in self.allowed_actions:
            return Permission(allowed=False, reason=f"'{action}' is not in policy allowed_actions")
        policy = self.action_policies.get(action)
        if policy is None:
            return Permission(allowed=True, reason="no action_policies entry: permitted, conservatively human-gated")
        if incident_class in policy.max_allowed_if:
            return Permission(allowed=True, reason=f"class '{incident_class}' in max_allowed_if")
        if urgency in policy.max_allowed_if:
            return Permission(allowed=True, reason=f"urgency '{urgency}' in max_allowed_if")
        return Permission(
            allowed=False,
            reason=f"neither class '{incident_class}' nor urgency '{urgency}' is in max_allowed_if {list(policy.max_allowed_if)}",
        )

    def is_permitted(self, action: str, incident_class: str, urgency: str) -> bool:
        return self.permission(action, incident_class, urgency).allowed

    def permitted_actions(self, incident_class: str, urgency: str) -> list[str]:
        return [a for a in self.allowed_actions if self.is_permitted(a, incident_class, urgency)]

    def view_for(self, incident_class: str, urgency: str) -> dict[str, ActionPolicyView]:
        views: dict[str, ActionPolicyView] = {}
        for action in self.allowed_actions:
            perm = self.permission(action, incident_class, urgency)
            ap = self.action_policies.get(action)
            views[action] = ActionPolicyView(
                permitted_for_this_incident=perm.allowed,
                requires_human_approval=self.requires_approval(action),
                max_allowed_if=ap.max_allowed_if if ap else None,
                policy_reason=perm.reason,
            )
        return views

    # -- commands --------------------------------------------------------------
    def with_preapproved(self, action: str) -> "RunbookPolicy":
        """Copy of this policy where ``action`` no longer requires human approval."""
        policies = dict(self.action_policies)
        current = policies.get(action, ActionPolicy(max_allowed_if=self.incident_classes))
        policies[action] = current.model_copy(update={"requires_human_approval": False})
        return self.model_copy(update={"action_policies": policies})

    def enforce(self, proposal: RemediationProposal, classification: Classification) -> RemediationPlan:
        """Filter an AI proposal against policy (disallowed -> ``policy_rejected_actions``)."""
        cls, urg = classification.incident_class, classification.urgency
        kept: list[RecommendedAction] = []
        rejected: list[PolicyRejection] = []
        seen: set[str] = set()
        for item in proposal.recommended_actions:
            if item.action in seen:
                rejected.append(PolicyRejection(action=item.action, reason="duplicate recommendation removed"))
                continue
            perm = self.permission(item.action, cls, urg)
            if not perm.allowed:
                rejected.append(PolicyRejection(action=item.action, reason=f"policy violation: {perm.reason}", llm_reason=item.reason))
                continue
            seen.add(item.action)
            policy_flag = self.requires_approval(item.action)
            kept.append(RecommendedAction(
                action=item.action,
                reason=item.reason,
                requires_human_approval=policy_flag,
                risk_note=item.risk_note,
                policy_basis=perm.reason,
                llm_approval_flag_mismatch=item.requires_human_approval != policy_flag,
            ))
        avoided = tuple(a for a in proposal.explicitly_avoided_actions if a.action not in seen)
        return RemediationPlan(
            incident_id=proposal.incident_id,
            recommended_actions=tuple(kept),
            explicitly_avoided_actions=avoided,
            rollback_consideration=proposal.rollback_consideration,
            verification_checks=proposal.verification_checks,
            policy_rejected_actions=tuple(rejected),
            evaluated_against_class=cls,
            evaluated_against_urgency=urg,
        )
