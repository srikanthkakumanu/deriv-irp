"""Operator review aggregate: decisions on AI recommendations and the post-review state.

Rules:
* ``reject`` always wins for that action.
* After review the policy is RE-APPLIED at the final urgency. An action that becomes
  impermissible (e.g. an urgency-gated page after a downgrade) is
  ``invalidated_by_override``, even if it was approved.
* Actions newly permitted only because of an urgency override are added as
  ``pending_approval`` (origin ``policy_unlocked``), never auto-cleared.
* Non-gated actions that are not rejected are ``auto_cleared_by_policy``, which is
  distinct from ``approved`` (an explicit operator approval).
* The last decision for a given (incident, action) wins; every decision is kept.
"""
from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Literal

from .base import ValueObject
from .errors import InvalidDecisionError
from .policy import RunbookPolicy
from .remediation import Classification, PolicyRejection, RemediationPlan


class ActionStatus(str, Enum):
    APPROVED = "approved"
    AUTO_CLEARED = "auto_cleared_by_policy"
    PENDING = "pending_approval"
    REJECTED = "rejected"
    INVALIDATED = "invalidated_by_override"


class ActionOrigin(str, Enum):
    AI = "ai_recommendation"
    OPERATOR = "operator_added"
    UNLOCKED = "policy_unlocked"


class Decision(ValueObject):
    seq: int
    ts: str
    type: Literal["approve", "reject", "urgency"]
    incident_id: str
    action: str | None = None
    from_urgency: str | None = None
    to_urgency: str | None = None
    action_in_ai_plan: bool | None = None


class ActionOutcome(ValueObject):
    action: str
    status: ActionStatus
    requires_human_approval: bool
    decided_by: Literal["operator", "policy", "none"]
    origin: ActionOrigin
    note: str = ""


class IncidentFinalState(ValueObject):
    incident_class: str
    original_urgency: str
    final_urgency: str
    urgency_overridden: bool
    actions: tuple[ActionOutcome, ...]
    approved_actions: tuple[str, ...]
    auto_cleared_actions: tuple[str, ...]
    pending_actions: tuple[str, ...]
    rejected_actions: tuple[str, ...]
    invalidated_actions: tuple[str, ...]
    policy_rejected_actions: tuple[PolicyRejection, ...]
    final_approved_action_set: tuple[str, ...]


class ReviewSession:
    """Collects operator decisions and derives each incident's post-review state."""

    def __init__(self, policy: RunbookPolicy, classifications: Mapping[str, Classification],
                 plans: Mapping[str, RemediationPlan]) -> None:
        self._policy = policy
        self._classifications = dict(classifications)
        self._plans = dict(plans)
        self._urgency: dict[str, str] = {iid: c.urgency for iid, c in classifications.items()}
        self._decisions: list[Decision] = []

    @property
    def incident_ids(self) -> list[str]:
        return list(self._classifications)

    @property
    def decisions(self) -> tuple[Decision, ...]:
        return tuple(self._decisions)

    def current_urgency(self, incident_id: str) -> str:
        self._require_incident(incident_id)
        return self._urgency[incident_id]

    # -- commands --------------------------------------------------------------
    def approve(self, incident_id: str, action: str, ts: str) -> Decision:
        self._require_incident(incident_id)
        self._require_known_action(action)
        in_plan = action in self._plans[incident_id].action_names
        cls = self._classifications[incident_id].incident_class
        if not in_plan:
            perm = self._policy.permission(action, cls, self._urgency[incident_id])
            if not perm.allowed:
                raise InvalidDecisionError(f"cannot approve '{action}' for {incident_id}: {perm.reason}")
        return self._record(Decision(seq=self._next_seq(), ts=ts, type="approve", incident_id=incident_id,
                                     action=action, action_in_ai_plan=in_plan))

    def reject(self, incident_id: str, action: str, ts: str) -> Decision:
        self._require_incident(incident_id)
        self._require_known_action(action)
        in_plan = action in self._plans[incident_id].action_names
        return self._record(Decision(seq=self._next_seq(), ts=ts, type="reject", incident_id=incident_id,
                                     action=action, action_in_ai_plan=in_plan))

    def override_urgency(self, incident_id: str, urgency: str, ts: str) -> Decision:
        self._require_incident(incident_id)
        if urgency not in self._policy.urgency_levels:
            raise InvalidDecisionError(f"invalid urgency '{urgency}' (allowed: {'|'.join(self._policy.urgency_levels)})")
        decision = Decision(seq=self._next_seq(), ts=ts, type="urgency", incident_id=incident_id,
                            from_urgency=self._urgency[incident_id], to_urgency=urgency)
        self._urgency[incident_id] = urgency
        return self._record(decision)

    # -- derived state ------------------------------------------------------------
    def final_states(self) -> dict[str, IncidentFinalState]:
        return {iid: self._final_state(iid) for iid in self._classifications}

    def _final_state(self, iid: str) -> IncidentFinalState:
        cls = self._classifications[iid].incident_class
        original, final = self._classifications[iid].urgency, self._urgency[iid]
        plan = self._plans[iid]
        last: dict[str, str] = {}
        for d in self._decisions:
            if d.incident_id == iid and d.action is not None:
                last[d.action] = d.type

        candidates = plan.action_names + [a for a in last if a not in plan.action_names]
        if final != original:
            for action in self._policy.permitted_actions(cls, final):
                if action not in candidates and not self._policy.is_permitted(action, cls, original):
                    candidates.append(action)

        outcomes = [self._outcome(a, cls, original, final, plan, last.get(a)) for a in candidates]

        def bucket(status: ActionStatus) -> tuple[str, ...]:
            return tuple(o.action for o in outcomes if o.status == status)

        approved, cleared = bucket(ActionStatus.APPROVED), bucket(ActionStatus.AUTO_CLEARED)
        return IncidentFinalState(
            incident_class=cls,
            original_urgency=original,
            final_urgency=final,
            urgency_overridden=final != original,
            actions=tuple(outcomes),
            approved_actions=approved,
            auto_cleared_actions=cleared,
            pending_actions=bucket(ActionStatus.PENDING),
            rejected_actions=bucket(ActionStatus.REJECTED),
            invalidated_actions=bucket(ActionStatus.INVALIDATED),
            policy_rejected_actions=plan.policy_rejected_actions,
            final_approved_action_set=approved + cleared,
        )

    def _outcome(self, action: str, cls: str, original: str, final: str, plan: RemediationPlan,
                 decision: str | None) -> ActionOutcome:
        gated = self._policy.requires_approval(action)
        perm = self._policy.permission(action, cls, final)
        if action in plan.action_names:
            origin = ActionOrigin.AI
        elif decision is not None:
            origin = ActionOrigin.OPERATOR
        else:
            origin = ActionOrigin.UNLOCKED

        def outcome(status: ActionStatus, by: Literal["operator", "policy", "none"], note: str = "") -> ActionOutcome:
            return ActionOutcome(action=action, status=status, requires_human_approval=gated, decided_by=by, origin=origin, note=note)

        if decision == "reject":
            return outcome(ActionStatus.REJECTED, "operator")
        if not perm.allowed:
            note = f"impermissible at final urgency {final}: {perm.reason}"
            if decision == "approve":
                note += "; operator approval voided"
            return outcome(ActionStatus.INVALIDATED, "policy", note)
        if decision == "approve":
            return outcome(ActionStatus.APPROVED, "operator")
        if origin is ActionOrigin.UNLOCKED:
            return outcome(ActionStatus.PENDING, "none", f"unlocked by urgency override {original}->{final}; requires explicit operator approval")
        if not gated:
            return outcome(ActionStatus.AUTO_CLEARED, "policy")
        return outcome(ActionStatus.PENDING, "none")

    # -- guards -------------------------------------------------------------------
    def _require_incident(self, incident_id: str) -> None:
        if incident_id not in self._classifications:
            raise InvalidDecisionError(f"unknown incident_id '{incident_id}' (known: {', '.join(self._classifications)})")

    def _require_known_action(self, action: str) -> None:
        if action not in self._policy.allowed_actions:
            raise InvalidDecisionError(f"'{action}' is not an allowed action in the runbook policy")

    def _next_seq(self) -> int:
        return len(self._decisions) + 1

    def _record(self, decision: Decision) -> Decision:
        self._decisions.append(decision)
        return decision
