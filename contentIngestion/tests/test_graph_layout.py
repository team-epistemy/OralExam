"""Offline tests for pure-Python concept-graph layout (no AWS/LLM)."""
from epistemy_m3.graph.layout import (
    slugify,
    build_node_ids,
    compute_layout,
    neighbors,
)

CONCEPTS = [
    {"label": "Activity Time"},
    {"label": "Resource Capacity"},
    {"label": "Bottleneck"},
    {"label": "Process Capacity"},
]
# Activity Time -> Resource Capacity -> Bottleneck -> Process Capacity (a chain)
EDGES = [
    {"src": "Activity Time", "dst": "Resource Capacity", "edge_type": "PREREQUISITE_FOR"},
    {"src": "Resource Capacity", "dst": "Bottleneck", "edge_type": "PREREQUISITE_FOR"},
    {"src": "Bottleneck", "dst": "Process Capacity", "edge_type": "ENABLES"},
]


def test_slugify_matches_demo_rules():
    assert slugify("Little's Law") == "littleslaw"
    assert slugify("Bottleneck & Process Capacity") == "bottleneckpr"  # first 12 chars
    assert slugify("") == "t0"
    assert slugify("!!!", 3) == "t3"


def test_build_node_ids_disambiguates_collisions():
    ids = build_node_ids([{"label": "Cash Flow"}, {"label": "Cash Flow!!"}])
    assert len(set(ids.values())) == 2  # unique despite same slug base


def test_layout_orders_chain_left_to_right():
    layout = compute_layout(CONCEPTS, EDGES)
    xs = {n["label"]: n["x"] for n in layout["nodes"]}
    assert xs["Activity Time"] < xs["Resource Capacity"] < xs["Bottleneck"] < xs["Process Capacity"]
    assert len(layout["edges"]) == 3


def test_layout_edges_reference_node_ids():
    layout = compute_layout(CONCEPTS, EDGES)
    node_ids = {n["id"] for n in layout["nodes"]}
    for src, dst in layout["edges"]:
        assert src in node_ids and dst in node_ids


def test_layout_is_cycle_safe():
    cyc = [{"label": "A"}, {"label": "B"}]
    edges = [{"src": "A", "dst": "B"}, {"src": "B", "dst": "A"}]
    layout = compute_layout(cyc, edges)  # must not hang or crash
    assert len(layout["nodes"]) == 2


def test_neighbors_reports_direction():
    ids = build_node_ids(CONCEPTS)
    bn = ids["Bottleneck"]
    nbrs = neighbors(EDGES, bn, CONCEPTS)
    dirs = {n["label"]: n["direction"] for n in nbrs}
    assert dirs["Resource Capacity"] == "inbound"
    assert dirs["Process Capacity"] == "outbound"
