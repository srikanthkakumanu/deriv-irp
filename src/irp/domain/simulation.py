"""Deterministic what-if policy simulation (no LLM; reuses Stage 1/2 results).

Scenario A: the AI-assist kill switch is pre-approved (``requires_human_approval=False``
on a policy copy); the same operator decisions are replayed.
Scenario B: deployment/change signals are ignored. Change alerts are dropped,
correlation and blast radius are recomputed, and deploy-dependent recommendations are
removed (rollback-type always; freeze-type when the class is deployment-related).
Incidents classed as deployment-related are flagged ``requires_reclassification``,
because re-deriving the class would need a new (non-deterministic) Stage 1 call.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .alerts import Alert
from .base import ValueObject
from .blast_radius import BlastRadiusCalculator
from .errors import InvalidDecisionError
from .incident import Incident, IncidentCorrelator
from .policy import RunbookPolicy
from .remediation import Classification, RemediationPlan
from .review import Decision, IncidentFinalState, ReviewSession
from .topology import ServiceTopology

PREAPPROVE_ACTION = "disable_ai_assist_path"
DEPLOY_CLASS_RE = re.compile(r"deploy|change|release", re.I)
ROLLBACK_RE = re.compile(r"rollback", re.I)
FREEZE_RE = re.compile(r"freeze", re.I)


class ActionSnapshot(ValueObject):
    recommended: tuple[str, ...]
    pending: tuple[str, ...]
    final_approved_action_set: tuple[str, ...]
    rejected: tuple[str, ...]


class SetDiff(ValueObject):
    added: tuple[str, ...]
    removed: tuple[str, ...]


class BlastRadiusDelta(ValueObject):
    before: int | None
    after: int | None
    delta: int | None


class IncidentDiff(ValueObject):
    recommended: SetDiff
    pending: SetDiff
    approved_or_cleared: SetDiff
    moved_pending_to_cleared: tuple[str, ...]
    changed: bool
    blast_radius: BlastRadiusDelta | None = None
    deploy_dependent_actions_removed: tuple[str, ...] = ()
    requires_reclassification: bool = False
    maps_to_incident_without_deploy_signals: str | None = None


class RecorrelatedIncident(ValueObject):
    incident_id: str
    alert_ids: tuple[str, ...]
    contains_deploy_signal: bool


class ScenarioResult(ValueObject):
    scenario: str
    description: str
    before: dict[str, ActionSnapshot]
    after: dict[str, ActionSnapshot]
    diff: dict[str, IncidentDiff]
    no_change: bool
    pre_review_diff: dict[str, IncidentDiff] = {}
    dropped_alert_ids: tuple[str, ...] = ()
    incidents_after_recorrelation: tuple[RecorrelatedIncident, ...] = ()


class SimulationReport(ValueObject):
    computed_by: str = "deterministic_code"
    llm_calls: int = 0
    scenarios: tuple[ScenarioResult, ...]


def _snapshot(states: Mapping[str, IncidentFinalState]) -> dict[str, ActionSnapshot]:
    return {iid: ActionSnapshot(
        recommended=tuple(sorted(o.action for o in s.actions)),
        pending=tuple(sorted(s.pending_actions)),
        final_approved_action_set=tuple(sorted(s.final_approved_action_set)),
        rejected=tuple(sorted(s.rejected_actions + s.invalidated_actions)),
    ) for iid, s in states.items()}


def _set_diff(before: Sequence[str], after: Sequence[str]) -> SetDiff:
    return SetDiff(added=tuple(sorted(set(after) - set(before))), removed=tuple(sorted(set(before) - set(after))))


def _diff(before: dict[str, ActionSnapshot], after: dict[str, ActionSnapshot]) -> dict[str, IncidentDiff]:
    out: dict[str, IncidentDiff] = {}
    for iid in sorted(before):
        b, a = before[iid], after[iid]
        rec, pen = _set_diff(b.recommended, a.recommended), _set_diff(b.pending, a.pending)
        app = _set_diff(b.final_approved_action_set, a.final_approved_action_set)
        moved = tuple(sorted(set(b.pending) & set(a.final_approved_action_set)))
        changed = any(d.added or d.removed for d in (rec, pen, app)) or bool(moved)
        out[iid] = IncidentDiff(recommended=rec, pending=pen, approved_or_cleared=app, moved_pending_to_cleared=moved, changed=changed)
    return out


class PolicySimulator:
    def __init__(self, policy: RunbookPolicy, topology: ServiceTopology, correlator: IncidentCorrelator,
                 calculator: BlastRadiusCalculator) -> None:
        self._policy = policy
        self._topology = topology
        self._correlator = correlator
        self._calculator = calculator

    def run(self, alerts: Sequence[Alert], incidents: Sequence[Incident], classifications: Mapping[str, Classification],
            plans: Mapping[str, RemediationPlan], decisions: Sequence[Decision],
            final_states: Mapping[str, IncidentFinalState], blast_scores: Mapping[str, int]) -> SimulationReport:
        """Each scenario is evaluated twice: with the operator's decisions replayed (``diff``) and
        on the raw AI plans before any review (``pre_review_diff``), so the policy effect stays
        visible even when an operator decision already masks it."""
        before = _snapshot(final_states)
        before_raw = _snapshot(self._replay(self._policy, classifications, plans, []))
        a = self._preapprove_ai_kill_switch(before, classifications, plans, decisions)
        a_raw = self._preapprove_ai_kill_switch(before_raw, classifications, plans, [])
        b = self._ignore_deploy_signals(before, alerts, incidents, classifications, plans, decisions, blast_scores)
        b_raw = self._ignore_deploy_signals(before_raw, alerts, incidents, classifications, plans, [], blast_scores)
        return SimulationReport(scenarios=(
            a.model_copy(update={"pre_review_diff": a_raw.diff}),
            b.model_copy(update={"pre_review_diff": b_raw.diff}),
        ))

    # -- scenario A --------------------------------------------------------------
    def _preapprove_ai_kill_switch(self, before, classifications, plans, decisions) -> ScenarioResult:
        name = "A_disable_ai_assist_path_preapproved"
        if PREAPPROVE_ACTION not in self._policy.allowed_actions:
            diff = _diff(before, before)
            return ScenarioResult(scenario=name, description=f"'{PREAPPROVE_ACTION}' is not in allowed_actions; no change possible.",
                                  before=before, after=before, diff=diff, no_change=True)
        policy = self._policy.with_preapproved(PREAPPROVE_ACTION)
        replanned = {iid: policy.enforce(plan.without(set()), classifications[iid]) for iid, plan in plans.items()}
        after = _snapshot(self._replay(policy, classifications, replanned, decisions))
        diff = _diff(before, after)
        return ScenarioResult(scenario=name, description="Policy copy sets requires_human_approval=false; the same operator decisions are replayed.",
                              before=before, after=after, diff=diff, no_change=not any(d.changed for d in diff.values()))

    # -- scenario B --------------------------------------------------------------
    def _ignore_deploy_signals(self, before, alerts, incidents, classifications, plans, decisions, blast_scores) -> ScenarioResult:
        kept = [a for a in alerts if not a.is_change_signal]
        dropped = tuple(a.alert_id for a in alerts if a.is_change_signal)
        new_incidents = self._correlator.correlate(kept)
        new_scores = {b.incident_id: b.blast_radius_score for b in self._calculator.score_all(new_incidents, kept)}
        mapping: dict[str, str | None] = {}
        for old in incidents:
            symptoms = set(old.alert_ids) - set(dropped)
            mapping[old.incident_id] = next((n.incident_id for n in new_incidents if symptoms & set(n.alert_ids)), None)

        removed: dict[str, tuple[str, ...]] = {}
        replanned: dict[str, RemediationPlan] = {}
        for iid, plan in plans.items():
            deploy_class = bool(DEPLOY_CLASS_RE.search(classifications[iid].incident_class))
            drop = tuple(a for a in plan.action_names if ROLLBACK_RE.search(a) or (deploy_class and FREEZE_RE.search(a)))
            removed[iid] = drop
            replanned[iid] = self._policy.enforce(plan.without(set(drop)), classifications[iid])
        skip = {(iid, a) for iid, acts in removed.items() for a in acts}
        after = _snapshot(self._replay(self._policy, classifications, replanned, decisions, skip))
        diff = _diff(before, after)
        enriched: dict[str, IncidentDiff] = {}
        for iid, d in diff.items():
            target = mapping.get(iid)
            new_score = new_scores.get(target) if target else None
            old_score = blast_scores.get(iid)
            delta = new_score - old_score if new_score is not None and old_score is not None else None
            enriched[iid] = d.model_copy(update={
                "blast_radius": BlastRadiusDelta(before=old_score, after=new_score, delta=delta),
                "deploy_dependent_actions_removed": removed[iid],
                "requires_reclassification": bool(DEPLOY_CLASS_RE.search(classifications[iid].incident_class)),
                "maps_to_incident_without_deploy_signals": target,
                "changed": d.changed or bool(removed[iid]) or (delta not in (0, None)),
            })
        return ScenarioResult(
            scenario="B_ignore_deployment_signals",
            description="Change-signal alerts are dropped; correlation and blast radius are recomputed; deploy-dependent actions are removed. Stage 1 classes are NOT re-derived (that would need an LLM).",
            before=before, after=after, diff=enriched, no_change=not any(d.changed for d in enriched.values()),
            dropped_alert_ids=dropped,
            incidents_after_recorrelation=tuple(RecorrelatedIncident(incident_id=n.incident_id, alert_ids=n.alert_ids,
                                                                     contains_deploy_signal=n.contains_deploy_signal) for n in new_incidents),
        )

    @staticmethod
    def _replay(policy: RunbookPolicy, classifications, plans, decisions: Sequence[Decision],
                skip: set[tuple[str, str]] | None = None) -> dict[str, IncidentFinalState]:
        session = ReviewSession(policy, classifications, plans)
        for d in decisions:
            try:
                if d.type == "urgency" and d.to_urgency:
                    session.override_urgency(d.incident_id, d.to_urgency, d.ts)
                elif d.action and (d.incident_id, d.action) not in (skip or set()):
                    (session.approve if d.type == "approve" else session.reject)(d.incident_id, d.action, d.ts)
            except InvalidDecisionError:
                continue  # decision no longer applicable in this scenario
        return session.final_states()
