"""Unit tests for the QG quality bench (QG-01 .. QG-06) scoring.

Pure logic — no FastAPI / DB / LLM / embedding client imported. Mirrors how the
admin Testing bench grades a live-generated batch against a course concept graph.
"""
from backend.app.qg_bench import (
    DEFAULT_THRESHOLDS,
    build_graph_index,
    classify_difficulty,
    normalize,
    run_qg_checks,
)

GRAPH = {
    "concepts": [
        {"id": "wip", "label": "WIP"},
        {"label": "throughput"},
        {"label": "batch size"},
        {"label": "changeover cost"},
        {"label": "WACC"},
        {"label": "NPV"},
        {"label": "project approval"},
    ],
    "relations": [
        {"src": "batch size", "dst": "changeover cost"},
        {"src": "WACC", "dst": "NPV"},
        {"src": "NPV", "dst": "project approval"},
    ],
}


def _status(report, criterion, qid=None):
    for r in report["results"]:
        if r["criterion"] == criterion and (qid is None or r["id"].startswith(qid)):
            return r["status"]
    return None


# ── normalization / index ─────────────────────────────────────────────────────
def test_normalize_collapses_separators():
    assert normalize("Debt-to_Equity") == "debt to equity"


def test_build_graph_index_counts_nodes_and_edges():
    idx = build_graph_index(GRAPH)
    assert "wip" in idx["nodes"] and "npv" in idx["nodes"]
    assert ("wacc", "npv") in idx["edges"]
    assert idx["edge_count"] == 3


# ── classifier (structure-based ground truth for QG-04) ───────────────────────
def test_classify_by_edge_count():
    assert classify_difficulty({"expected_path": {"edges": []}}) == "recall"
    assert classify_difficulty({"expected_path": {"edges": [{"src": "a", "dst": "b"}]}}) == "balanced"
    assert classify_difficulty(
        {"expected_path": {"edges": [{"src": "a", "dst": "b"}, {"src": "b", "dst": "c"}]}}
    ) == "deep"


# ── QG-01: recall grounding ───────────────────────────────────────────────────
def test_qg01_pass_when_concept_in_graph():
    q = {"question_id": "q1", "question": "What is WIP?", "difficulty": "recall",
         "concept_ids": ["WIP"], "expected_path": {"nodes": [{"label": "WIP"}]}}
    rep = run_qg_checks("Ops", GRAPH, [q])
    assert _status(rep, "QG-01", "q1") == "pass"


def test_qg01_fail_when_concept_absent():
    q = {"question_id": "q1", "question": "What is inventory?", "difficulty": "recall",
         "concept_ids": ["inventory"], "expected_path": {"nodes": [{"label": "inventory"}]}}
    rep = run_qg_checks("Ops", GRAPH, [q])
    assert _status(rep, "QG-01", "q1") == "fail"


# ── QG-02: balanced edge validity ─────────────────────────────────────────────
def test_qg02_pass_on_real_edge():
    q = {"question_id": "q1", "question": "If batch size is cut, changeover cost?",
         "difficulty": "balanced", "concept_ids": ["batch size", "changeover cost"],
         "expected_path": {"edges": [{"src": "batch size", "dst": "changeover cost"}]}}
    rep = run_qg_checks("Ops", GRAPH, [q])
    assert _status(rep, "QG-02", "q1") == "pass"


def test_qg02_fail_on_hallucinated_edge():
    q = {"question_id": "q1", "question": "How does WIP cause NPV?",
         "difficulty": "balanced", "concept_ids": ["WIP", "NPV"],
         "expected_path": {"edges": [{"src": "WIP", "dst": "NPV"}]}}
    rep = run_qg_checks("Ops", GRAPH, [q])
    assert _status(rep, "QG-02", "q1") == "fail"


# ── QG-03: deep multi-hop path ────────────────────────────────────────────────
def test_qg03_pass_on_multihop():
    q = {"question_id": "q1",
         "question": "Given that NPV fell, trace how WACC drove approval down.",
         "difficulty": "deep", "concept_ids": ["WACC", "NPV"],
         "expected_path": {"edges": [{"src": "WACC", "dst": "NPV"},
                                     {"src": "NPV", "dst": "project approval"}]}}
    rep = run_qg_checks("Ops", GRAPH, [q])
    assert _status(rep, "QG-03", "q1") == "pass"


def test_qg03_fail_on_single_hop():
    q = {"question_id": "q1", "question": "Why does WACC matter for NPV?",
         "difficulty": "deep", "concept_ids": ["WACC", "NPV"],
         "expected_path": {"edges": [{"src": "WACC", "dst": "NPV"}]}}
    rep = run_qg_checks("Ops", GRAPH, [q])
    assert _status(rep, "QG-03", "q1") == "fail"


# ── QG-04: declared vs classified agreement ───────────────────────────────────
def test_qg04_flags_mislabeled_difficulty():
    # Declared 'recall' but the expected_path has a causal edge -> classifies 'balanced'.
    q = {"question_id": "q1", "question": "What is WACC?", "difficulty": "recall",
         "concept_ids": ["WACC"], "expected_path": {"edges": [{"src": "WACC", "dst": "NPV"}]}}
    rep = run_qg_checks("Ops", GRAPH, [q])
    assert _status(rep, "QG-04") == "fail"


# ── QG-06: near-duplicate detection ───────────────────────────────────────────
def test_qg06_flags_duplicates_via_bow():
    q1 = {"question_id": "q1", "question": "What happens to changeover cost when batch size drops?",
          "difficulty": "balanced", "concept_ids": ["batch size"], "expected_path": {}}
    q2 = {"question_id": "q2", "question": "What happens to changeover cost when batch size drops?",
          "difficulty": "balanced", "concept_ids": ["batch size"], "expected_path": {}}
    rep = run_qg_checks("Ops", GRAPH, [q1, q2])
    assert _status(rep, "QG-06") == "fail"


def test_qg06_uses_real_embeddings_when_provided():
    q1 = {"question_id": "q1", "question": "alpha", "difficulty": "recall", "expected_path": {}}
    q2 = {"question_id": "q2", "question": "beta", "difficulty": "recall", "expected_path": {}}
    # Orthogonal vectors -> similarity 0 -> pass, proving the embedding path is used.
    rep = run_qg_checks("Ops", GRAPH, [q1, q2], embeddings=[[1.0, 0.0], [0.0, 1.0]])
    assert _status(rep, "QG-06") == "pass"


def test_qg06_falls_back_to_bow_on_nonfinite_embeddings():
    # Non-finite embedding components (inf/nan) must not silently pass QG-06 —
    # it falls back to bag-of-words, which still catches the identical texts.
    dup = "If batch size is cut in half, what happens to changeover cost?"
    q1 = {"question_id": "q1", "question": dup, "difficulty": "balanced", "expected_path": {}}
    q2 = {"question_id": "q2", "question": dup, "difficulty": "balanced", "expected_path": {}}
    bad = [[float("nan")] * 4, [float("inf")] * 4]
    rep = run_qg_checks("Ops", GRAPH, [q1, q2], embeddings=bad)
    assert _status(rep, "QG-06") == "fail"


def test_default_thresholds_match_prototype():
    assert DEFAULT_THRESHOLDS["qg04_min_agreement"] == 0.95
    assert DEFAULT_THRESHOLDS["qg05_max_naive_acc"] == 0.40
    assert DEFAULT_THRESHOLDS["qg06_max_similarity"] == 0.85
