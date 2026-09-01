"""Pure dedup that recomputes a course graph from its documents' concepts."""
from backend.app.concept_graph import merge_document_concepts, slug


def test_dedup_merges_same_label_across_documents_with_provenance():
    rows = [
        {"material_version_id": "m1", "label": "Monopoly", "definition": "one seller",
         "abstraction_level": 0.6, "questions": {"recall": ["R1"], "in_depth": ["D1"]}},
        {"material_version_id": "m2", "label": "monopoly", "definition": "",
         "abstraction_level": None, "questions": {"recall": ["R2"], "case": ["C1"]}},
    ]
    g = merge_document_concepts(rows, [])
    assert len(g["concepts"]) == 1
    c = g["concepts"][0]
    assert c["label"] == "Monopoly" and c["id"] == "monopoly"
    assert c["definition"] == "one seller"           # first non-empty kept
    assert set(c["sources"]) == {"m1", "m2"}          # provenance from both docs
    assert "R1" in c["questions"]["recall"] and "R2" in c["questions"]["recall"]
    assert c["questions"]["case"] == ["C1"]


def test_edges_kept_only_when_both_endpoints_survive():
    rows = [
        {"material_version_id": "m1", "label": "A", "questions": {}},
        {"material_version_id": "m1", "label": "B", "questions": {}},
    ]
    edges = [
        {"src_label": "A", "dst_label": "B", "edge_type": "PREREQUISITE_FOR", "confidence": 0.9},
        {"src_label": "A", "dst_label": "Ghost", "edge_type": "ENABLES", "confidence": 0.5},  # dropped
    ]
    g = merge_document_concepts(rows, edges)
    assert len(g["relations"]) == 1
    assert g["relations"][0]["src"] == "A" and g["relations"][0]["dst"] == "B"


def test_empty_documents_yield_empty_graph():
    # A course whose documents contributed nothing => empty graph (no leaked concepts).
    assert merge_document_concepts([], []) == {"concepts": [], "relations": []}


def test_slug_is_stable_and_safe():
    assert slug("Customer Perceived Value (CPV)") == "customer-perceived-value-cpv"
    assert slug("   ") == "concept"
