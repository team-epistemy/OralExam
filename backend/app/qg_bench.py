"""Question-Generation Quality Bench (QG-01 .. QG-06) — pure, web-free scoring.

Stdlib-only on purpose (like ``exam_questions.py``) so the graded checks can be
unit-tested without FastAPI, a DB, or the LLM/embedding clients. The admin
"Testing" bench (``_register_admin_testing`` in ``http_app.py``) sources a real
subject's concept graph and live-generates a question batch, then hands both to
``run_qg_checks`` here to produce the same style of pass/fail report as the
offline framework and the ``qgqa_console.html`` prototype.

Unlike the prototype (which regex-classifies free text into L1/L2/L3), we grade
against REAL data: the course concept graph (nodes + causal edges) and each
generated question's structured ``expected_path`` (nodes/edges/extensions).
Difficulty tiers are the platform's own — ``recall`` / ``balanced`` / ``deep`` —
so QG-01..03 are re-anchored onto those tiers.

QG-07 (human-rater answerability) and QG-08 (item discrimination) need external
rater / pilot-cohort data and are not computed here — they are reported as
``skip`` with a note, exactly as the prototype does.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

# ── Benchmark: the QG criteria + default thresholds captured in the prototype ──
# These are the DEFAULTS. M2 will let each subject override them; keeping them in
# one dict here means the override just needs to merge-on-top.
DEFAULT_THRESHOLDS: Dict[str, float] = {
    "qg04_min_agreement": 0.95,     # declared vs. classified difficulty
    "qg05_max_naive_acc": 0.40,     # naive-baseline accuracy on deep questions
    "qg06_max_similarity": 0.85,    # max pairwise similarity in the batch
}

CRITERIA_META: Dict[str, str] = {
    "QG-01": "Every recall question grounds to a listed concept",
    "QG-02": "Every balanced question asserts a causal edge that exists in the graph",
    "QG-03": "Every deep question carries a multi-hop expected reasoning path",
    "QG-04": "Declared difficulty matches the structure-classified difficulty (≥95% target)",
    "QG-05": "Naive baseline accuracy on deep questions (≤40% target)",
    "QG-06": "No near-duplicate questions in the batch (<0.85 similarity)",
}

# Surface-pattern "leaks" a naive guesser could exploit (ported from the prototype).
_LEAK_RE1 = re.compile(r"\bnot\b.*\?\s*$", re.IGNORECASE)
_LEAK_RE2 = re.compile(r"\bjust\b", re.IGNORECASE)
# A weak factual-anchor signal for deep questions (ported from the prototype).
_ANCHOR_RE = re.compile(r"\b(given that|given the|since the|after the|because)\b", re.IGNORECASE)


# ── normalization helpers ─────────────────────────────────────────────────────
def normalize(s: Any) -> str:
    """Lowercase, collapse separators/space — so 'debt-to-equity' == 'debt to equity'."""
    return re.sub(r"[._\-]+", " ", str(s or "")).strip().lower()


def build_graph_index(graph: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a course graph snapshot to normalized lookup sets.

    Accepts the shape returned by ``_query_graph_version``:
      {"concepts": [{id,label,...}], "relations"/"edges": [{src,dst,...}]}
    Returns {"nodes": set[str], "edges": set[(src,dst)], "node_count", "edge_count"}.
    """
    nodes = set()
    for c in graph.get("concepts") or []:
        label = normalize(c.get("label") or c.get("id") or "")
        if label:
            nodes.add(label)
    edges = set()
    for r in graph.get("relations") or graph.get("edges") or []:
        src = normalize(r.get("src") or r.get("from") or "")
        dst = normalize(r.get("dst") or r.get("to") or "")
        if src and dst:
            edges.add((src, dst))
            nodes.add(src)
            nodes.add(dst)
    return {"nodes": nodes, "edges": edges,
            "node_count": len(nodes), "edge_count": len(edges)}


def _labels(q: Dict[str, Any]) -> List[str]:
    """All normalized concept labels a question claims to touch — its concept_ids
    plus every node named in its expected_path."""
    out = set()
    for cid in q.get("concept_ids") or []:
        n = normalize(cid)
        if n:
            out.add(n)
    ep = q.get("expected_path") or {}
    for node in ep.get("nodes") or []:
        n = normalize(node.get("label") if isinstance(node, dict) else node)
        if n:
            out.add(n)
    return sorted(out)


def _expected_edges(q: Dict[str, Any]) -> List[Tuple[str, str]]:
    """Normalized (src, dst) causal edges asserted by a question's expected_path."""
    ep = q.get("expected_path") or {}
    out = []
    for e in ep.get("edges") or []:
        if not isinstance(e, dict):
            continue
        src, dst = normalize(e.get("src")), normalize(e.get("dst"))
        if src and dst:
            out.append((src, dst))
    return out


def classify_difficulty(q: Dict[str, Any]) -> str:
    """Structure-based difficulty, independent of the declared label — the ground
    truth QG-04 checks the generator's self-declared difficulty against.

    Heuristic on the expected reasoning path:
      * >=2 causal edges  -> deep      (multi-hop mechanism)
      * exactly 1 edge    -> balanced  (single intervention/relationship)
      * 0 edges           -> recall    (definitional / single node)
    """
    n_edges = len(_expected_edges(q))
    if n_edges >= 2:
        return "deep"
    if n_edges == 1:
        return "balanced"
    return "recall"


# ── cosine over embeddings (real) with a bag-of-words fallback ─────────────────
def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    sim = dot / (na * nb)
    # A single non-finite component (inf/nan) would otherwise NaN-poison the
    # comparison — `nan > worst` is always False, silently passing QG-06.
    return sim if math.isfinite(sim) else 0.0


def _all_finite(vectors: List[List[float]]) -> bool:
    """True only if every component of every vector is a finite number."""
    return all(all(isinstance(x, (int, float)) and math.isfinite(x) for x in v)
               for v in vectors)


def _bow(text: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        counts[w] = counts.get(w, 0) + 1
    return counts


def _bow_cosine(a: Dict[str, int], b: Dict[str, int]) -> float:
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ── the checks ─────────────────────────────────────────────────────────────────
def run_qg_checks(
    subject: str,
    graph: Dict[str, Any],
    questions: List[Dict[str, Any]],
    thresholds: Optional[Dict[str, float]] = None,
    embeddings: Optional[List[List[float]]] = None,
) -> Dict[str, Any]:
    """Grade a generated question batch against a subject's concept graph.

    Args:
      subject:     the subject/course label (echoed into the report).
      graph:       ``_query_graph_version``-shaped {concepts, relations/edges}.
      questions:   ``_build_question_dicts``-shaped items — each has
                   {question, difficulty, concept_ids, expected_path}.
      thresholds:  optional override of ``DEFAULT_THRESHOLDS`` (M2 per-subject).
      embeddings:  optional per-question vectors (parallel to ``questions``) for a
                   real semantic QG-06; falls back to bag-of-words when omitted.

    Returns {"subject", "results": [...], "summary": {...}, "graph": {...}}.
    """
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    idx = build_graph_index(graph)
    nodes, edges = idx["nodes"], idx["edges"]

    # Resolve each question's effective difficulty + structural classification once.
    for q in questions:
        q["_declared"] = (q.get("difficulty") or "balanced").lower()
        q["_classified"] = classify_difficulty(q)

    results: List[Dict[str, Any]] = []

    def _row(criterion, qid, q, status, reasoning):
        results.append({"criterion": criterion, "id": qid, "q": q,
                        "status": status, "reasoning": reasoning})

    # QG-01 — recall questions must ground to a listed concept.
    for i, q in enumerate(questions):
        qid = q.get("question_id") or f"Q{i + 1}"
        if q["_declared"] != "recall":
            _row("QG-01", qid, q, "skip", "Not a recall question — skipped.")
            continue
        matched = next((lbl for lbl in _labels(q) if lbl in nodes), None)
        if matched:
            _row("QG-01", qid, q, "pass", f'Grounded to listed concept "{matched}".')
        else:
            _row("QG-01", qid, q, "fail",
                 "No concept_ids / expected_path node resolves to a graph concept.")

    # QG-02 — balanced questions must assert a causal edge present in the graph.
    for i, q in enumerate(questions):
        qid = q.get("question_id") or f"Q{i + 1}"
        if q["_declared"] != "balanced":
            _row("QG-02", qid, q, "skip", "Not a balanced question — skipped.")
            continue
        exp = _expected_edges(q)
        if not exp:
            _row("QG-02", qid, q, "fail",
                 "expected_path declares no causal edge to assert.")
            continue
        hit = next(((s, d) for (s, d) in exp if (s, d) in edges), None)
        if hit:
            _row("QG-02", qid, q, "pass",
                 f'Asserted edge "{hit[0]} → {hit[1]}" exists in the concept graph.')
        else:
            shown = ", ".join(f"{s}→{d}" for s, d in exp[:3])
            _row("QG-02", qid, q, "fail",
                 f"None of the expected edges ({shown}) are in the graph's relation set.")

    # QG-03 — deep questions must carry a multi-hop expected reasoning path.
    for i, q in enumerate(questions):
        qid = q.get("question_id") or f"Q{i + 1}"
        if q["_declared"] != "deep":
            _row("QG-03", qid, q, "skip", "Not a deep question — skipped.")
            continue
        exp = _expected_edges(q)
        has_anchor = bool(_ANCHOR_RE.search(q.get("question") or ""))
        if len(exp) >= 2:
            _row("QG-03", qid, q, "pass",
                 f"Multi-hop expected path ({len(exp)} causal edges)"
                 + (" with a factual anchor in the prompt." if has_anchor else "."))
        else:
            _row("QG-03", qid, q, "fail",
                 f"Deep question needs ≥2 linked causal edges; expected_path has {len(exp)}.")

    # QG-04 (batch) — declared difficulty vs. structure-classified difficulty.
    if not questions:
        _row("QG-04", "BATCH", None, "skip", "No questions to classify.")
    else:
        mism = [q for q in questions if q["_declared"] != q["_classified"]]
        agreement = (len(questions) - len(mism)) / len(questions)
        passed = agreement >= th["qg04_min_agreement"]
        detail = ("no mismatches" if not mism else "; ".join(
            f'{q.get("question_id", "?")[:8]}: declared {q["_declared"]}, classified {q["_classified"]}'
            for q in mism[:6]))
        _row("QG-04", "BATCH", None, "pass" if passed else "fail",
             f"Agreement = {agreement * 100:.1f}% ({len(questions) - len(mism)}/{len(questions)}). "
             f"Threshold ≥{th['qg04_min_agreement'] * 100:.0f}%. {detail}.")

    # QG-05 (batch, deep only) — naive baseline resistance.
    deep = [q for q in questions if q["_declared"] == "deep"]
    if not deep:
        _row("QG-05", "BATCH", None, "skip", "No deep questions in batch — skipped.")
    else:
        leaked = [q for q in deep
                  if _LEAK_RE1.search(q.get("question") or "")
                  or _LEAK_RE2.search(q.get("question") or "")]
        acc = len(leaked) / len(deep)
        passed = acc <= th["qg05_max_naive_acc"]
        _row("QG-05", "BATCH", None, "pass" if passed else "fail",
             f"Naive baseline guessed {len(leaked)}/{len(deep)} deep questions by surface "
             f"pattern ({acc * 100:.1f}%). Threshold ≤{th['qg05_max_naive_acc'] * 100:.0f}%.")

    # QG-06 (batch) — near-duplicate detection (real embeddings when available).
    # Degenerate embeddings (wrong length or non-finite values) fall back to
    # bag-of-words rather than silently mis-scoring.
    use_emb = (embeddings is not None and len(embeddings) == len(questions)
               and _all_finite(embeddings))
    if len(questions) < 2:
        _row("QG-06", "BATCH", None, "skip", "Fewer than 2 questions — nothing to compare.")
    else:
        vecs = embeddings if use_emb else [_bow(q.get("question") or "") for q in questions]
        worst, worst_pair, flagged = 0.0, None, []
        for i in range(len(questions)):
            for j in range(i + 1, len(questions)):
                sim = _cosine(vecs[i], vecs[j]) if use_emb else _bow_cosine(vecs[i], vecs[j])
                if sim > worst:
                    worst = sim
                    worst_pair = (questions[i].get("question_id", f"Q{i+1}")[:8],
                                  questions[j].get("question_id", f"Q{j+1}")[:8])
                if sim >= th["qg06_max_similarity"]:
                    flagged.append((i, j, sim))
        passed = worst < th["qg06_max_similarity"]
        method = "embedding cosine" if use_emb else "bag-of-words cosine (no embeddings)"
        detail = (f"highest pairwise similarity {worst:.2f} between {worst_pair[0]} and {worst_pair[1]}."
                  if worst_pair else "no pairs to compare.")
        _row("QG-06", "BATCH", None, "pass" if passed else "fail",
             f"Threshold <{th['qg06_max_similarity']:.2f} ({method}). {detail}"
             + (f" {len(flagged)} near-duplicate pair(s) flagged." if flagged else ""))

    return {"subject": subject, "results": _strip(results),
            "summary": summarize(results), "graph": idx_public(idx),
            "thresholds": th}


def _strip(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop the heavy embedded question object, keeping only what the report needs
    (question text + resolved levels) so the JSON stays small."""
    out = []
    for r in results:
        q = r.get("q")
        out.append({
            "criterion": r["criterion"],
            "id": r["id"],
            "status": r["status"],
            "reasoning": r["reasoning"],
            "question": (q.get("question") if q else None),
            "declared": (q.get("_declared") if q else None),
            "classified": (q.get("_classified") if q else None),
        })
    return out


def summarize(results: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    summary: Dict[str, Dict[str, int]] = {}
    for cid in CRITERIA_META:
        summary[cid] = {"pass": 0, "fail": 0, "skip": 0}
    for r in results:
        summary.setdefault(r["criterion"], {"pass": 0, "fail": 0, "skip": 0})
        summary[r["criterion"]][r["status"]] += 1
    return summary


def idx_public(idx: Dict[str, Any]) -> Dict[str, int]:
    """Report-safe view of the graph index (counts only, not the raw sets)."""
    return {"node_count": idx["node_count"], "edge_count": idx["edge_count"]}
