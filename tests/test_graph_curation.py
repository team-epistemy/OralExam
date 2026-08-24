"""Unit tests for apply_curation — persisting a professor's curated concept set
onto a stored graph (keep full data, stub additions, prune dangling relations).
"""
from backend.app.graph_curation import apply_curation


def _stored():
    concepts = [
        {"id": "littles-law", "label": "Little's Law", "definition": "L=λW", "questions": ["q1", "q2"]},
        {"id": "cycle-time", "label": "Cycle Time", "definition": "time per unit", "questions": ["q3"]},
        {"id": "wip", "label": "WIP", "definition": "work in process", "questions": ["q4"]},
    ]
    relations = [
        {"src": "Little's Law", "dst": "Cycle Time", "edge_type": "PREREQUISITE_FOR"},
        {"src": "WIP", "dst": "Cycle Time", "edge_type": "ENABLES"},
    ]
    return concepts, relations


def test_removal_filters_and_preserves_full_objects():
    concepts, relations = _stored()
    kept = [{"id": "littles-law", "label": "Little's Law"}, {"id": "cycle-time", "label": "Cycle Time"}]
    new_c, new_r = apply_curation(concepts, relations, kept)
    assert [c["label"] for c in new_c] == ["Little's Law", "Cycle Time"]
    # full data preserved (definition + questions), not just id/label
    assert new_c[0]["definition"] == "L=λW"
    assert new_c[0]["questions"] == ["q1", "q2"]


def test_relation_pruned_when_endpoint_removed():
    concepts, relations = _stored()
    # Drop WIP → the WIP→Cycle Time relation must go; the other stays.
    kept = [{"id": "littles-law", "label": "Little's Law"}, {"id": "cycle-time", "label": "Cycle Time"}]
    _new_c, new_r = apply_curation(concepts, relations, kept)
    assert new_r == [{"src": "Little's Law", "dst": "Cycle Time", "edge_type": "PREREQUISITE_FOR"}]


def test_match_by_label_case_insensitive_when_id_absent():
    concepts, relations = _stored()
    kept = [{"label": "little's law"}]  # no id, different case
    new_c, _new_r = apply_curation(concepts, relations, kept)
    assert new_c[0]["id"] == "littles-law"
    assert new_c[0]["questions"] == ["q1", "q2"]


def test_added_concept_becomes_stub():
    concepts, relations = _stored()
    kept = [{"id": "littles-law", "label": "Little's Law"}, {"id": "custom-1", "label": "Throughput Rate"}]
    new_c, _new_r = apply_curation(concepts, relations, kept)
    stub = new_c[1]
    assert stub["label"] == "Throughput Rate"
    assert stub["id"] == "throughput-rate"      # slugified from the label
    assert stub["definition"] == "" and stub["questions"] == []


def test_order_follows_kept():
    concepts, relations = _stored()
    kept = [{"id": "wip", "label": "WIP"}, {"id": "littles-law", "label": "Little's Law"}]
    new_c, _ = apply_curation(concepts, relations, kept)
    assert [c["label"] for c in new_c] == ["WIP", "Little's Law"]


def test_unique_ids_for_clashing_added_labels():
    # Two brand-new concepts that slugify identically must not collide.
    new_c, _ = apply_curation([], [], [{"label": "Net Flow"}, {"label": "net  flow"}])
    ids = [c["id"] for c in new_c]
    assert ids == ["net-flow", "net-flow-2"]


def test_empty_kept_yields_empty_graph():
    concepts, relations = _stored()
    assert apply_curation(concepts, relations, []) == ([], [])


def test_blank_labels_are_skipped():
    concepts, relations = _stored()
    kept = [{"id": "wip", "label": "WIP"}, {"label": "   "}, {"label": ""}]
    new_c, _ = apply_curation(concepts, relations, kept)
    assert [c["label"] for c in new_c] == ["WIP"]
