import pytest

from irp.domain.incident import IncidentCorrelator
from irp.domain.topology import ServiceTopology
from tests.conftest import alerts_from, raw_alert

TOPO = ServiceTopology.model_validate({"services": [
    {"name": "feed", "depends_on": [], "tier": "critical_path"},
    {"name": "pricer", "depends_on": ["feed"], "tier": "critical_path"},
    {"name": "router", "depends_on": ["pricer", "edge"], "tier": "critical_path"},
    {"name": "edge", "depends_on": [], "tier": "edge"},
    {"name": "ml", "depends_on": [], "tier": "ai_support"},
]})


def correlate(*payloads, window=120):
    return IncidentCorrelator(TOPO, window).correlate(alerts_from(*payloads))


class TestSampleFixture:
    def test_two_incidents(self, topology, sample_alerts):
        incidents = IncidentCorrelator(topology).correlate(sample_alerts)
        assert [i.incident_id for i in incidents] == ["INC-001", "INC-002"]
        inc1, inc2 = incidents
        assert inc1.primary_service == "market-data-gateway"
        assert inc1.contains_deploy_signal and len(inc1.deploy_alert_ids) == 1
        assert set(inc2.impacted_services) == {"feature-store", "model-serving"}
        assert not inc2.contains_deploy_signal

    def test_every_alert_in_exactly_one_incident(self, topology, sample_alerts):
        incidents = IncidentCorrelator(topology).correlate(sample_alerts)
        ids = [a for i in incidents for a in i.alert_ids]
        assert sorted(ids) == sorted(a.alert_id for a in sample_alerts)


class TestCorrelationRules:
    def test_dependency_chain_links(self):
        incs = correlate(raw_alert(service="feed"), raw_alert(service="router", ts="2026-03-03T09:00:30Z"))
        assert len(incs) == 1 and incs[0].primary_service == "feed"
        assert "topology:router->depends_on->pricer" not in incs[0].correlation_basis  # pricer not alerting
        assert "pricer" in incs[0].impacted_services  # downstream blast includes non-alerting dependents

    def test_different_region_splits(self):
        assert len(correlate(raw_alert(service="feed"), raw_alert(service="pricer", region="r2"))) == 2

    def test_outside_time_window_splits(self):
        assert len(correlate(raw_alert(service="feed"), raw_alert(service="pricer", ts="2026-03-03T09:05:00Z"))) == 2

    def test_window_boundary_is_inclusive(self):
        assert len(correlate(raw_alert(service="feed"), raw_alert(service="pricer", ts="2026-03-03T09:02:00Z"))) == 1

    def test_chaining_links_beyond_single_window(self):
        incs = correlate(raw_alert(service="feed"), raw_alert(service="feed", ts="2026-03-03T09:01:50Z"),
                         raw_alert(service="feed", ts="2026-03-03T09:03:40Z"))
        assert len(incs) == 1

    def test_unrelated_services_split(self):
        assert len(correlate(raw_alert(service="feed"), raw_alert(service="ml"))) == 2

    def test_siblings_join_only_through_common_dependent(self):
        assert len(correlate(raw_alert(service="feed"), raw_alert(service="edge"))) == 2
        assert len(correlate(raw_alert(service="feed"), raw_alert(service="edge"), raw_alert(service="router"))) == 1

    def test_unknown_service_is_singleton(self):
        incs = correlate(raw_alert(service="ghost"), raw_alert(service="feed"))
        ghost = next(i for i in incs if i.primary_service == "ghost")
        assert "service_not_in_topology:ghost" in ghost.correlation_basis
        assert "singleton:no_correlated_alerts" in ghost.correlation_basis

    def test_primary_tie_broken_by_severity(self):
        incs = correlate(raw_alert(service="edge", severity="critical"), raw_alert(service="feed", severity="warning"),
                         raw_alert(service="router"))
        assert incs[0].primary_service == "edge"


class TestChangeSignals:
    def test_deploy_attaches_to_adjacent_incident(self):
        incs = correlate(raw_alert(service="edge"), raw_alert(service="edge", metric="deploy_event", severity="info",
                                                              message="Canary deploy", ts="2026-03-03T09:00:20Z"))
        assert len(incs) == 1 and incs[0].contains_deploy_signal

    def test_orphan_deploy_becomes_own_incident(self):
        incs = correlate(raw_alert(service="feed"), raw_alert(service="ml", metric="deploy_event", severity="info", message="deploy"))
        assert len(incs) == 2
        orphan = next(i for i in incs if i.contains_deploy_signal)
        assert orphan.alerting_services == ("ml",)

    def test_deploy_outside_window_is_not_attached(self):
        incs = correlate(raw_alert(service="edge"), raw_alert(service="edge", metric="deploy_event", message="deploy",
                                                              ts="2026-03-03T10:00:00Z"))
        assert len(incs) == 2

    def test_deploy_prefers_same_service_incident(self):
        incs = correlate(raw_alert(service="feed"), raw_alert(service="edge", ts="2026-03-03T09:00:01Z"),
                         raw_alert(service="edge", metric="deploy_event", message="deploy", ts="2026-03-03T09:00:02Z"))
        edge_inc = next(i for i in incs if "edge" in i.alerting_services)
        assert edge_inc.contains_deploy_signal

    def test_deploy_signals_do_not_merge_incidents(self):
        # feed and edge are unrelated siblings; a deploy on edge must not glue them together.
        incs = correlate(raw_alert(service="feed"), raw_alert(service="edge"),
                         raw_alert(service="edge", metric="deploy_event", message="deploy"))
        assert len(incs) == 2


def test_ids_are_numbered_by_first_alert():
    incs = correlate(raw_alert(service="ml", ts="2026-03-03T08:00:00Z"), raw_alert(service="feed"))
    assert incs[0].primary_service == "ml" and incs[0].incident_id == "INC-001"


@pytest.mark.parametrize("window", [0, 1])
def test_zero_window_only_links_simultaneous_alerts(window):
    incs = correlate(raw_alert(service="feed"), raw_alert(service="pricer", ts="2026-03-03T09:00:05Z"), window=window)
    assert len(incs) == 2
