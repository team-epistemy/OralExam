"""Offline tests for the deterministic exam builder (no AWS/LLM)."""
from backend.questions.exam_builder import (
    distribute_questions,
    build_variants,
    assemble_questions,
    topic_bank,
)

CONCEPTS = [
    {"id": "littleslaw", "label": "Little's Law"},
    {"id": "bottleneck", "label": "Bottleneck & Process Capacity"},
    {"id": "utilization", "label": "Utilization"},
    {"id": "flowtime", "label": "Flow Time"},
]


def test_distribution_sums_to_qcount_even():
    dist = distribute_questions(CONCEPTS, 12, "even")
    assert sum(d["count"] for d in dist) == 12
    # even mode → equal split of 12 across 4 concepts
    assert [d["count"] for d in dist] == [3, 3, 3, 3]


def test_distribution_sums_to_qcount_indivisible():
    # 10 across 4 concepts: floor gives 2 each (=8), remainder 2 handed out
    dist = distribute_questions(CONCEPTS, 10, "even")
    assert sum(d["count"] for d in dist) == 10


def test_front_mode_weights_early_concepts_heavier():
    dist = distribute_questions(CONCEPTS, 12, "front")
    counts = {d["id"]: d["count"] for d in dist}
    assert counts["littleslaw"] >= counts["flowtime"]
    assert sum(counts.values()) == 12


def test_back_mode_weights_later_concepts_heavier():
    dist = distribute_questions(CONCEPTS, 12, "back")
    counts = {d["id"]: d["count"] for d in dist}
    assert counts["flowtime"] >= counts["littleslaw"]
    assert sum(counts.values()) == 12


def test_empty_and_zero_are_safe():
    assert distribute_questions([], 12, "even") == []
    assert distribute_questions(CONCEPTS, 0, "even") == []


def test_build_variants_returns_three_angles():
    variants = build_variants(CONCEPTS, 12, "balanced", exam_len=30)
    assert [v["id"] for v in variants] == ["balanced-even", "balanced-core", "balanced-frontier"]
    assert all(v["duration"] == "30 min" for v in variants)
    assert all(sum(d["count"] for d in v["distribution"]) == 12 for v in variants)


def test_deep_difficulty_uses_conceptual_bank():
    variants = build_variants(CONCEPTS, 8, "deep")
    assert variants[0]["bank_key"] == "conceptual"
    assert variants[0]["title"].startswith("Deep ·")


def test_assemble_pulls_from_bank_then_falls_back_to_generic():
    banks = {"littleslaw": ["State Little's Law.", "Why does it hold?"]}
    dist = [
        {"id": "littleslaw", "label": "Little's Law", "count": 3},
        {"id": "utilization", "label": "Utilization", "count": 2},
    ]
    qs = assemble_questions(dist, banks)
    assert len(qs) == 5
    # bank of size 2 cycles on the 3rd question
    ll = [q["q"] for q in qs if q["concept_id"] == "littleslaw"]
    assert ll == ["State Little's Law.", "Why does it hold?", "State Little's Law."]
    # no bank → generic templates keyed on the label
    util = [q["q"] for q in qs if q["concept_id"] == "utilization"]
    assert all("Utilization" in q for q in util)


def test_topic_bank_dedupes_and_extends():
    bank = topic_bank("Little's Law", ["State Little's Law."])
    assert bank[0] == "State Little's Law."
    assert len(bank) == len(set(bank))  # no duplicates
    assert len(bank) > 1  # generic extras appended
