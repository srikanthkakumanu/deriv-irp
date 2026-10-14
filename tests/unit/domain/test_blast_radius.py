from irp.domain.blast_radius import BlastRadiusCalculator
from irp.domain.incident import IncidentCorrelator
from irp.domain.topology import ServiceTopology
from tests.conftest import alerts_from, raw_alert


def score_for(topology, *payloads, pattern=None):
    alerts = alerts_from(*payloads)
    incidents = IncidentCorrelator(topology).correlate(alerts)
    calc = BlastRadiusCalculator(topology, pattern) if pattern else BlastRadiusCalculator(topology)
    return calc.score_all(incidents, alerts)


def test_sample_scores(topology, sample_alerts):
    incidents = IncidentCorrelator(topology).correlate(sample_alerts)
    scores = BlastRadiusCalculator(topology).score_all(incidents, sample_alerts)
    assert [s.blast_radius_score for s in scores] == [100, 30]
    f = scores[0].contributing_factors
    assert f["S_impacted_services"].points == 30 and f["C_critical_path"].points == 25
    assert f["V_max_severity"].points == 20 and f["O_order_routing"].points == 15 and f["D_deploy_signal"].points == 10


def test_score_equals_sum_of_factors_and_is_capped(topology, sample_alerts):
    for s in BlastRadiusCalculator(topology).score_all(IncidentCorrelator(topology).correlate(sample_alerts), sample_alerts):
        assert s.blast_radius_score == min(100, sum(f.points for f in s.contributing_factors.values()))
        assert 0 <= s.blast_radius_score <= 100


def test_minimum_score_info_alert_unknown_service(topology):
    [s] = score_for(topology, raw_alert(service="ghost", severity="info"))
    assert s.blast_radius_score == 10 and s.band == "low"  # 1 service only
    assert s.contributing_factors["C_critical_path"].note


def test_order_routing_fallback_to_critical_path_sink():
    topo = ServiceTopology.model_validate({"services": [
        {"name": "a", "depends_on": [], "tier": "critical_path"},
        {"name": "b", "depends_on": ["a"], "tier": "critical_path"}]})
    [s] = score_for(topo, raw_alert(service="a"))
    o = s.contributing_factors["O_order_routing"]
    assert o.points == 15 and o.detected_by == "critical_path_sink" and o.value == ("b",)


def test_custom_order_pattern(topology):
    [s] = score_for(topology, raw_alert(service="feature-store"), pattern=r"feature")
    assert s.contributing_factors["O_order_routing"].points == 15


def test_deterministic(topology, sample_alerts):
    incidents = IncidentCorrelator(topology).correlate(sample_alerts)
    calc = BlastRadiusCalculator(topology)
    assert calc.score_all(incidents, sample_alerts) == calc.score_all(incidents, sample_alerts)
