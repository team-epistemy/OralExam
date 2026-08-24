"""Unit tests for the web-free exam-question generation helpers.

Covers the deterministic parse/merge that turns an LLM response into per-concept
question banks, plus the stored-bank fallback. No FastAPI / LLM client imported.
"""
from backend.app.exam_questions import (
    DIFFICULTY_FOCUS,
    stored_concept_banks,
    parse_generated_banks,
    merge_generated_banks,
)

# ── stored_concept_banks ──────────────────────────────────────────────────────

def test_stored_banks_keys_by_id_and_label():
    concepts = [{"id": "c1", "label": "Little's Law", "questions": ["q1", "q2"]}]
    banks = stored_concept_banks(concepts)
    assert banks["c1"] == ["q1", "q2"]
    assert banks["Little's Law"] == ["q1", "q2"]


def test_stored_banks_drops_non_string_questions():
    concepts = [{"id": "c1", "label": "L", "questions": ["ok", None, 3, {"x": 1}]}]
    assert stored_concept_banks(concepts)["c1"] == ["ok"]


def test_stored_banks_missing_questions_is_empty_list():
    concepts = [{"id": "c1", "label": "L"}]
    assert stored_concept_banks(concepts)["c1"] == []


def test_stored_banks_empty_or_none_input():
    assert stored_concept_banks([]) == {}
    assert stored_concept_banks(None) == {}


# ── parse_generated_banks ─────────────────────────────────────────────────────

def test_parse_indexes_by_lowercased_label_and_strips():
    data = {"banks": [{"label": "Cycle Time", "questions": ["  a ", "b\n"]}]}
    assert parse_generated_banks(data) == {"cycle time": ["a", "b"]}


def test_parse_skips_empty_label_or_questions():
    data = {"banks": [
        {"label": "", "questions": ["a"]},
        {"label": "X", "questions": []},
        {"label": "Y", "questions": ["  ", None]},
        {"label": "Z", "questions": ["real"]},
    ]}
    assert parse_generated_banks(data) == {"z": ["real"]}


def test_parse_tolerates_malformed_payloads():
    assert parse_generated_banks(None) == {}
    assert parse_generated_banks("nope") == {}
    assert parse_generated_banks({}) == {}
    assert parse_generated_banks({"banks": None}) == {}
    assert parse_generated_banks({"banks": ["notadict", 5]}) == {}


# ── merge_generated_banks ─────────────────────────────────────────────────────

def _concepts():
    return [
        {"id": "c1", "label": "Little's Law", "questions": ["stored-1"]},
        {"id": "c2", "label": "Cycle Time", "questions": ["stored-2a", "stored-2b"]},
    ]


def test_merge_uses_generated_when_present_keyed_by_id_and_label():
    data = {"banks": [
        {"label": "Little's Law", "questions": ["gen-1a", "gen-1b"]},
        {"label": "Cycle Time", "questions": ["gen-2a"]},
    ]}
    banks = merge_generated_banks(_concepts(), data)
    assert banks["c1"] == ["gen-1a", "gen-1b"]
    assert banks["Little's Law"] == ["gen-1a", "gen-1b"]
    assert banks["c2"] == ["gen-2a"]


def test_merge_is_case_insensitive_on_label():
    data = {"banks": [{"label": "LITTLE'S LAW", "questions": ["gen"]}]}
    banks = merge_generated_banks(_concepts(), data)
    assert banks["c1"] == ["gen"]


def test_merge_falls_back_per_skipped_concept():
    # Only c1 generated; c2 must fall back to its stored bank.
    data = {"banks": [{"label": "Little's Law", "questions": ["gen-1"]}]}
    banks = merge_generated_banks(_concepts(), data)
    assert banks["c1"] == ["gen-1"]
    assert banks["c2"] == ["stored-2a", "stored-2b"]


def test_merge_wholesale_fallback_when_nothing_generated():
    # No usable generation → identical to stored_concept_banks.
    for bad in ({"banks": []}, {}, None, {"banks": [{"label": "Unknown", "questions": ["x"]}]}):
        assert merge_generated_banks(_concepts(), bad) == stored_concept_banks(_concepts())


def test_merge_concept_with_no_stored_and_skipped_is_empty():
    # A concept the generator skips and that has no stored bank yields an empty
    # list, so the caller's generic-template fallback kicks in downstream.
    concepts = [
        {"id": "c1", "label": "Covered", "questions": []},
        {"id": "c2", "label": "Bare", "questions": []},
    ]
    data = {"banks": [{"label": "Covered", "questions": ["gen"]}]}
    banks = merge_generated_banks(concepts, data)
    assert banks["c1"] == ["gen"]
    assert banks["c2"] == []


# ── difficulty presets ────────────────────────────────────────────────────────

def test_difficulty_focus_has_all_levels():
    assert set(DIFFICULTY_FOCUS) == {"recall", "balanced", "deep"}
    assert all(isinstance(v, str) and v for v in DIFFICULTY_FOCUS.values())
