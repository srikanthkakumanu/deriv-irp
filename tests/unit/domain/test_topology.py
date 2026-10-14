from irp.domain.topology import ServiceTopology


def test_upstream_and_downstream_are_transitive(topology):
    assert topology.upstream("order-router") == {"pricing-engine", "api-edge", "market-data-gateway"}
    assert topology.downstream("market-data-gateway") == {"pricing-engine", "order-router"}


def test_causal_adjacency(topology):
    assert topology.causally_adjacent("order-router", "market-data-gateway")
    assert topology.causally_adjacent("market-data-gateway", "order-router")
    assert not topology.causally_adjacent("api-edge", "market-data-gateway")  # siblings only
    assert not topology.causally_adjacent("model-serving", "pricing-engine")


def test_unknown_service_is_only_adjacent_to_itself(topology):
    assert topology.causally_adjacent("ghost", "ghost")
    assert not topology.causally_adjacent("ghost", "order-router")
    assert topology.tier("ghost") is None and not topology.known("ghost")


def test_cycles_do_not_loop_forever():
    topo = ServiceTopology.model_validate({"services": [
        {"name": "a", "depends_on": ["b"]}, {"name": "b", "depends_on": ["a"]}]})
    assert topo.upstream("a") == {"b"}
    assert topo.downstream("a") == {"b"}


def test_empty_topology():
    topo = ServiceTopology.model_validate({"services": []})
    assert topo.upstream("x") == frozenset()
