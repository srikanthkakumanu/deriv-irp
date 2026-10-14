"""Safe-automation tiering domain service (deterministic, policy- and keyword-driven).

* fully_automatable          policy needs no approval AND the action is read-only or communicative
* conditionally_automatable  state-changing but reversible and isolated from pricing/order flow
* never_automate             moves order flow / alters quoting, or has no policy entry
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from enum import Enum

from .base import ValueObject
from .policy import RunbookPolicy
from .review import IncidentFinalState

READ_ONLY_TOKENS = frozenset({"collect", "diagnostics", "page", "status", "draft"})
REVERSIBLE_TOKENS = frozenset({"throttle", "disable", "rollback", "freeze"})
MARKET_RISK_TOKENS = frozenset({"traffic", "region", "shift", "quotes", "cached", "price", "pricing", "order"})
GUARDRAILS: dict[str, str] = {
    "freeze": "Auto-apply only while a sev0–sev2 incident is open on a critical-path service; auto-lift on incident close; break-glass override for hotfixes.",
    "throttle": "Only non-critical request classes; cap at a fixed percentage; auto-revert after N minutes unless re-confirmed; never touches order submission.",
    "disable": "Only when the AI path is the incident's primary service and a deterministic fallback is healthy; re-enabling stays human-gated.",
    "rollback": "Only when a deploy/change signal is correlated with the incident, the change is a canary or under 30 minutes old, and the rollback artifact is verified.",
}


class AutomationTier(str, Enum):
    FULL = "fully_automatable"
    CONDITIONAL = "conditionally_automatable"
    NEVER = "never_automate"


class ActionTiering(ValueObject):
    action: str
    tier: AutomationTier
    policy_requires_human_approval: bool
    max_allowed_if: tuple[str, ...] | None
    rationale: str
    guardrails: str | None
    observed_in_incidents: tuple[str, ...]
    operator_rejected_in: tuple[str, ...]


def _tokens(action: str) -> frozenset[str]:
    return frozenset(t for t in re.split(r"[^a-z0-9]+", action.lower()) if t)


class AutomationTiering:
    def __init__(self, policy: RunbookPolicy) -> None:
        self._policy = policy

    def classify(self, final_states: Mapping[str, IncidentFinalState]) -> list[ActionTiering]:
        return [self._tier(action, final_states) for action in self._policy.allowed_actions]

    def _tier(self, action: str, final_states: Mapping[str, IncidentFinalState]) -> ActionTiering:
        tokens = _tokens(action)
        gated = self._policy.requires_approval(action)
        if not self._policy.has_policy(action):
            tier, why = AutomationTier.NEVER, "No action_policies entry; conservative default is human-only."
        elif tokens & MARKET_RISK_TOKENS:
            tier, why = AutomationTier.NEVER, "Moves order flow or alters quoting: direct market risk; a wrong automated call is costly and hard to unwind."
        elif not gated and tokens & READ_ONLY_TOKENS:
            tier, why = AutomationTier.FULL, "Policy requires no approval and the action is read-only or communicative."
        elif tokens & REVERSIBLE_TOKENS:
            tier = AutomationTier.CONDITIONAL
            why = ("Policy requires no approval but the action changes production state." if not gated
                   else "Policy requires approval today; reversible and isolated from pricing/order flow, so a candidate for guarded automation.")
        else:
            tier, why = AutomationTier.NEVER, "Unclassified action; default to human-gated."
        guard = next((g for key, g in GUARDRAILS.items() if key in tokens), None)
        ap = self._policy.action_policies.get(action)
        return ActionTiering(
            action=action, tier=tier, policy_requires_human_approval=gated,
            max_allowed_if=ap.max_allowed_if if ap else None, rationale=why,
            guardrails=guard if tier is AutomationTier.CONDITIONAL else None,
            observed_in_incidents=tuple(i for i, s in final_states.items() if any(o.action == action for o in s.actions)),
            operator_rejected_in=tuple(i for i, s in final_states.items() if action in s.rejected_actions),
        )
