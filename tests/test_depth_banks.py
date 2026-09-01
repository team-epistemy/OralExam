"""Depth-tagged question banks: authoring shape + difficulty-based selection."""
from backend.app.exam_questions import (
    concept_bank, sanitize_bank, stored_concept_banks, merge_generated_banks,
)

_C = {
    "id": "c1", "label": "Monopoly",
    "questions": {
        "recall": ["Define monopoly", "State the monopoly condition"],
        "application": ["Given one seller, what happens to price?"],
        "in_depth": ["Why does deadweight loss arise?", "How does elasticity shape markup?"],
        "case": ["Mini-case: a lone water utility. How should it price?"],
    },
}


def test_recall_pulls_recall_then_application():
    assert concept_bank(_C, "recall") == [
        "Define monopoly", "State the monopoly condition",
        "Given one seller, what happens to price?",
    ]


def test_deep_pulls_in_depth_and_case_first():
    got = concept_bank(_C, "deep")
    assert got[0] == "Why does deadweight loss arise?"
    assert "Mini-case: a lone water utility. How should it price?" in got
    assert "Define monopoly" not in got            # recall tier excluded from deep


def test_balanced_pulls_all_tiers():
    got = concept_bank(_C, "balanced")
    assert len(got) == 6 and "Define monopoly" in got and "Why does deadweight loss arise?" in got


def test_legacy_flat_list_used_as_is():
    assert concept_bank({"questions": ["q1", "q2"]}, "deep") == ["q1", "q2"]


def test_sanitize_bank_normalizes_shapes():
    # dict is trimmed to the four tiers, ≤3 each
    s = sanitize_bank({"recall": ["a", "b", "c", "d"], "in_depth": ["x"]})
    assert s["recall"] == ["a", "b", "c"] and s["application"] == [] and s["in_depth"] == ["x"] and s["case"] == []
    # a legacy flat list lands in the recall tier
    assert sanitize_bank(["p", "q"]) == {"recall": ["p", "q"], "application": [], "in_depth": [], "case": []}
    # junk -> empty tiers
    assert sanitize_bank(None) == {"recall": [], "application": [], "in_depth": [], "case": []}


def test_stored_banks_key_by_id_and_label_and_respect_difficulty():
    banks = stored_concept_banks([_C], "recall")
    assert banks["c1"] == banks["Monopoly"]
    assert "Why does deadweight loss arise?" not in banks["c1"]   # deep tier excluded for recall


def test_merge_falls_back_to_depth_bank_for_skipped_concepts():
    # generator returned nothing for this concept -> stored depth bank (deep) used
    banks = merge_generated_banks([_C], {"banks": []}, "deep")
    assert banks["c1"][0] == "Why does deadweight loss arise?"
