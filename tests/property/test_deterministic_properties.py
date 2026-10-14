"""Property-based tests (Hypothesis) for the deterministic, no-LLM stages.

Example-based tests pin known fixtures; these generate arbitrary topologies, alert streams
and AI proposals and check the invariants the requirement relies on: normalisation and
correlation are deterministic and order-independent, every alert lands in exactly one
incident, blast radius follows its published formula, and policy enforcement never lets
an impermissible action through.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from irp.domain.alerts import AlertNormalizer, RawAlertRecord
from irp.domain.blast_radius import BlastRadiusCalculator
from irp.domain.incident import IncidentCorrelator
from irp.domain.policy import RunbookPolicy
from irp.domain.remediation import Classification, ProposedAction, RemediationProposal
from irp.domain.topology import ServiceTopology

DATA = Path(__file__).resolve().parents[2] / "data"
POLICY = RunbookPolicy.model_validate(json.loads((DATA / "runbook_policy.json").read_text()))
BASE_TS = datetime(2026, 3, 3, 9, 0, tzinfo=timezone.utc)
WINDOW_S = 120

settings.register_profile("irp", max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("irp")


# -- strategies -------------------------------------------------------------------------
@st.composite
def topologies(draw: st.DrawFn) -> dict[str, Any]:
    """Random service graphs, cycles included, with optional tiers."""
    names = [f"svc-{i}" for i in range(draw(st.integers(1, 6)))]
    tiers = st.sampled_from(["critical_path", "edge", "ai_support", None])
    return {"services": [{"name": n, "depends_on": draw(st.lists(st.sampled_from(names), max_size=3, unique=True)),
                          "tier": draw(tiers)} for n in names]}


@st.composite
def alert_payloads(draw: st.DrawFn, services: list[str]) -> dict[str, Any]:
    deploy = draw(st.booleans())
    value = draw(st.integers(0, 10_000)) / draw(st.sampled_from([1, 10, 100]))
    return {
        "ts": (BASE_TS + timedelta(seconds=draw(st.integers(0, 900)))).isoformat().replace("+00:00", "Z"),
        "service": draw(st.sampled_from([*services, "not-in-topology"])),
        "severity": draw(st.sampled_from(["info", "warning", "critical", "WARNING", " Critical "])),
        "metric": "deploy_event" if deploy else draw(st.sampled_from(["latency_ms", "error_rate_pct", "lag_ms"])),
        "value": value,
        "threshold": draw(st.integers(0, 5_000)) / 10,
        "region": draw(st.sampled_from(["eu-west-1", "us-east-1"])),
        "message": "Canary deployment started" if deploy else draw(st.sampled_from(["latency high", "errors up"])),
    }


@st.composite
def scenarios(draw: st.DrawFn) -> tuple[ServiceTopology, list[dict[str, Any]]]:
    raw = draw(topologies())
    payloads = draw(st.lists(alert_payloads([s["name"] for s in raw["services"]]), min_size=1, max_size=25))
    return ServiceTopology.model_validate(raw), payloads


def records(payloads: list[dict[str, Any]]) -> list[RawAlertRecord]:
    return [RawAlertRecord(line_no=i, payload=p) for i, p in enumerate(payloads, 1)]


def normalize(payloads: list[dict[str, Any]]):
    return AlertNormalizer().normalize(records(payloads)).alerts


# -- normalisation ----------------------------------------------------------------------
@given(scenarios())
def test_normalization_is_deterministic(scenario):
    _, payloads = scenario
    assert normalize(payloads) == normalize(payloads)


@given(scenarios())
def test_normalized_alerts_are_complete_unique_sorted_and_exact(scenario):
    _, payloads = scenario
    alerts = normalize(payloads)
    assert len(alerts) == len(payloads)  # every generated row is valid
    assert len({a.alert_id for a in alerts}) == len(alerts)
    assert list(alerts) == sorted(alerts, key=lambda a: a.sort_key)
    for a in alerts:
        assert a.severity.value in ("info", "warning", "critical")
        assert abs(a.delta_from_threshold - (a.observed_value - a.threshold)) < 1e-6
        assert a.ts.tzinfo is not None and a.ts.utcoffset() == timedelta(0)


@given(scenarios(), st.randoms(use_true_random=False))
def test_alert_ids_ignore_json_key_order(scenario, rnd: random.Random):
    _, payloads = scenario
    reordered = []
    for p in payloads:
        keys = list(p)
        rnd.shuffle(keys)
        reordered.append({k: p[k] for k in keys})
    assert [a.alert_id for a in normalize(payloads)] == [a.alert_id for a in normalize(reordered)]


# -- correlation ------------------------------------------------------------------------
@given(scenarios())
def test_every_alert_belongs_to_exactly_one_incident(scenario):
    topology, payloads = scenario
    alerts = normalize(payloads)
    incidents = IncidentCorrelator(topology, WINDOW_S).correlate(alerts)
    members = [aid for inc in incidents for aid in inc.alert_ids]
    assert sorted(members) == sorted(a.alert_id for a in alerts)
    assert [i.incident_id for i in incidents] == [f"INC-{n:03d}" for n in range(1, len(incidents) + 1)]


@given(scenarios(), st.randoms(use_true_random=False))
def test_correlation_is_deterministic_and_input_order_independent(scenario, rnd: random.Random):
    topology, payloads = scenario
    alerts = list(normalize(payloads))
    correlator = IncidentCorrelator(topology, WINDOW_S)
    first = correlator.correlate(alerts)
    shuffled = alerts[:]
    rnd.shuffle(shuffled)
    assert correlator.correlate(alerts) == first
    assert correlator.correlate(shuffled) == first


@given(scenarios())
def test_incidents_are_single_region_with_consistent_fields(scenario):
    topology, payloads = scenario
    alerts = normalize(payloads)
    by_id = {a.alert_id: a for a in alerts}
    for inc in IncidentCorrelator(topology, WINDOW_S).correlate(alerts):
        members = [by_id[i] for i in inc.alert_ids]
        assert {a.region for a in members} == {inc.region}
        assert inc.contains_deploy_signal == any(a.is_change_signal for a in members)
        assert inc.primary_service in {a.service for a in members}
        assert set(inc.alerting_services) <= set(inc.impacted_services)
        assert inc.correlation_basis


@given(scenarios())
def test_linked_symptoms_always_share_an_incident(scenario):
    """Soundness of the documented link rule: same region, |dt| <= window, causally adjacent."""
    topology, payloads = scenario
    alerts = normalize(payloads)
    incident_of = {aid: inc.incident_id for inc in IncidentCorrelator(topology, WINDOW_S).correlate(alerts)
                   for aid in inc.alert_ids}
    symptoms = [a for a in alerts if not a.is_change_signal]
    for i, a in enumerate(symptoms):
        for b in symptoms[i + 1:]:
            if (a.region == b.region and abs((a.ts - b.ts).total_seconds()) <= WINDOW_S
                    and topology.causally_adjacent(a.service, b.service)):
                assert incident_of[a.alert_id] == incident_of[b.alert_id], (a.alert_id, b.alert_id)


@given(scenarios())
def test_change_signals_never_merge_incidents(scenario):
    """Dropping every change signal leaves the symptom grouping unchanged."""
    topology, payloads = scenario
    alerts = normalize(payloads)
    correlator = IncidentCorrelator(topology, WINDOW_S)

    def symptom_groups(incidents):
        change = {a.alert_id for a in alerts if a.is_change_signal}
        return sorted(g for g in (tuple(sorted(set(i.alert_ids) - change)) for i in incidents) if g)

    without = [a for a in alerts if not a.is_change_signal]
    assert symptom_groups(correlator.correlate(alerts)) == symptom_groups(correlator.correlate(without))


# -- blast radius -----------------------------------------------------------------------
@given(scenarios())
def test_blast_radius_follows_the_published_formula(scenario):
    topology, payloads = scenario
    alerts = normalize(payloads)
    incidents = IncidentCorrelator(topology, WINDOW_S).correlate(alerts)
    calculator = BlastRadiusCalculator(topology)
    scores = calculator.score_all(incidents, alerts)
    assert scores == calculator.score_all(incidents, alerts)
    assert [s.incident_id for s in scores] == [i.incident_id for i in incidents]
    for s in scores:
        assert 0 <= s.blast_radius_score <= 100
        assert s.blast_radius_score == min(100, sum(f.points for f in s.contributing_factors.values()))


# -- policy enforcement -----------------------------------------------------------------
proposed_actions = st.lists(
    st.tuples(st.sampled_from([*POLICY.allowed_actions, "delete_production_db", "restart_everything"]), st.booleans()),
    max_size=12)


@given(st.sampled_from(POLICY.incident_classes), st.sampled_from(POLICY.urgency_levels), proposed_actions)
def test_enforced_plan_only_keeps_permitted_actions_with_policy_flags(incident_class, urgency, actions):
    proposal = RemediationProposal(
        incident_id="INC-001",
        recommended_actions=tuple(ProposedAction(action=a, reason="r", requires_human_approval=flag, risk_note="n")
                                  for a, flag in actions),
        rollback_consideration="rb", verification_checks=("check",))
    classification = Classification(incident_id="INC-001", incident_class=incident_class, urgency=urgency,
                                    customer_impact_summary="i", suspected_root_cause="c", confidence="medium")
    plan = POLICY.enforce(proposal, classification)
    kept = [a.action for a in plan.recommended_actions]
    assert len(kept) == len(set(kept))
    for a in plan.recommended_actions:
        assert POLICY.is_permitted(a.action, incident_class, urgency)
        assert a.requires_human_approval == POLICY.requires_approval(a.action)
    assert len(kept) + len(plan.policy_rejected_actions) == len(actions)
    for r in plan.policy_rejected_actions:
        assert r.action in kept or not POLICY.is_permitted(r.action, incident_class, urgency)
