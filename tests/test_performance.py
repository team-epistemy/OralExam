"""Unit tests for the anonymized practice-performance aggregation."""
from backend.app.performance import aggregate_performance


def _aspect(out, key):
    return next(a for a in out["aspects"] if a["key"] == key)


def _topic(out, label):
    return next(t for t in out["topics"] if t["label"] == label)


ROWS = [
    ("s1", ["littles-law"], {"node_score": 0.9, "edge_score": 0.8, "gen_score": 0.3, "r_gate": 1.0}),
    ("s1", ["cycle-time"],  {"node_score": 0.6, "edge_score": 0.2, "gen_score": 0.0, "r_gate": 0.9}),
    ("s2", ["littles-law"], {"node_score": 0.4, "edge_score": 0.1, "gen_score": 0.0, "r_gate": 0.5}),
]
LABELS = {"littles-law": "Little's Law", "cycle-time": "Cycle Time"}


def test_aspect_percentages_and_averages():
    out = aggregate_performance(ROWS, LABELS, bar=0.5)
    assert out["practice_takers"] == 2
    assert out["bar"] == 0.5
    # recall = node: s1 avg (0.9+0.6)/2=0.75 (>=bar), s2 avg 0.4 (<bar) → 1/2 students; class avg 0.575
    assert _aspect(out, "recall")["pct_students"] == 0.5
    assert _aspect(out, "recall")["avg_score"] == 0.575
    # application = edge: s1 avg 0.5 (>=bar), s2 0.1 → 0.5; class avg 0.3
    assert _aspect(out, "application") == {"key": "application", "label": "Application",
        "description": "Connected and applied concepts (incl. case scenarios)",
        "pct_students": 0.5, "avg_score": 0.3}
    # in-depth = gen: nobody at/above bar → 0.0; class avg 0.075
    assert _aspect(out, "depth")["pct_students"] == 0.0
    assert _aspect(out, "depth")["avg_score"] == 0.075
    # authenticity = r_gate: both at/above bar → 1.0; class avg 0.725
    assert _aspect(out, "authenticity")["pct_students"] == 1.0
    assert _aspect(out, "authenticity")["avg_score"] == 0.725


def test_topic_coverage_and_labels():
    out = aggregate_performance(ROWS, LABELS, bar=0.5)
    ll = _topic(out, "Little's Law")
    assert ll["pct_students"] == 0.5      # s1 demonstrated (0.9), s2 didn't (0.4) → 1/2
    assert ll["n_attempted"] == 2
    assert ll["avg_score"] == 0.65
    ct = _topic(out, "Cycle Time")
    assert ct["pct_students"] == 0.5      # 1 of 2 takers demonstrated; only s1 attempted
    assert ct["n_attempted"] == 1
    assert ct["avg_score"] == 0.6


def test_empty_rows_yield_zeroed_stats():
    out = aggregate_performance([], LABELS)
    assert out["practice_takers"] == 0
    assert out["topics"] == []
    assert all(a["pct_students"] == 0.0 and a["avg_score"] == 0.0 for a in out["aspects"])
    assert [a["key"] for a in out["aspects"]] == ["recall", "application", "depth", "authenticity"]


def test_concept_ids_as_json_string_is_parsed():
    out = aggregate_performance(
        [("s1", '["littles-law"]', {"node_score": 0.8})], LABELS, bar=0.5)
    assert _topic(out, "Little's Law")["pct_students"] == 1.0


def test_unmapped_topic_falls_back_to_raw_key():
    out = aggregate_performance(
        [("s1", ["mystery-concept"], {"node_score": 0.9})], {}, bar=0.5)
    assert _topic(out, "mystery-concept")["pct_students"] == 1.0


def test_non_dict_components_are_skipped():
    out = aggregate_performance(
        [("s1", ["x"], None), ("s2", ["x"], {"node_score": 0.9})], {}, bar=0.5)
    assert out["practice_takers"] == 1  # s1's None row contributed no student
