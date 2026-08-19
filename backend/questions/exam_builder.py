"""Deterministic exam assembly — ports the demo's variant builder (no LLM).

Given the curated concept list, a per-concept question bank (authored at
extraction time), and an exam config, this produces three exam variants
(even / core / frontier) whose question counts are apportioned across concepts
with largest-remainder rounding, then assembles concrete questions per variant.
Mirrors EpistemyOralExamDemo.jsx buildVariants/distributeQuestions/assemble.
"""
from __future__ import annotations
from typing import Callable, Dict, List, Optional

# ── Difficulty presets (demo DIFFICULTY_META) ────────────────────────────────
DIFFICULTY_META: Dict[str, Dict[str, str]] = {
    "recall": {
        "name": "Recall", "bank_key": "balanced",
        "badge": "badge-conceptual", "badge_label": "Recall-focused",
        "eds_focus": "Definition retrieval, shallow-hop checks",
        "blurb": "definitional accuracy and formula recall",
    },
    "balanced": {
        "name": "Balanced", "bank_key": "balanced",
        "badge": "badge-balanced", "badge_label": "Balanced",
        "eds_focus": "Mixed recall and causal reasoning",
        "blurb": "a mix of recall and causal reasoning",
    },
    "deep": {
        "name": "Deep", "bank_key": "conceptual",
        "badge": "badge-applied", "badge_label": "Depth-focused",
        "eds_focus": "High-hop traversal, causal chains",
        "blurb": "causal mechanisms and prerequisite chains",
    },
}

# ── Three probing angles applied to the selected difficulty (demo VARIANT_ANGLES) ──
VARIANT_ANGLES: List[Dict[str, str]] = [
    {"key": "even", "suffix": "Even Coverage", "mode": "even"},
    {"key": "core", "suffix": "Core Emphasis", "mode": "front"},
    {"key": "frontier", "suffix": "Frontier Emphasis", "mode": "back"},
]

# ── Fallback question templates (demo GENERIC_Q / GENERIC_EXTRA) ──────────────
GENERIC_Q: List[Callable[[str], str]] = [
    lambda l: f"Explain the core idea behind {l} and why it matters.",
    lambda l: f"Walk me through a key mechanism or trade-off in {l}.",
    lambda l: f"Where does {l} most often go wrong in practice?",
    lambda l: f"How would you apply {l} to a real decision?",
]
GENERIC_EXTRA: List[Callable[[str], str]] = [
    lambda l: f"Give a concrete example that illustrates {l}.",
    lambda l: f"What is a common misconception about {l}?",
    lambda l: f"How would you explain {l} to a non-expert?",
    lambda l: f"Which assumption behind {l} is most often overlooked?",
]


def _angle_description(meta: Dict[str, str], mode: str, n: int) -> str:
    """Human-readable blurb for a variant angle (demo VARIANT_ANGLES.desc)."""
    blurb = meta["blurb"]
    if mode == "front":
        return f"Same {blurb}, weighted toward the foundational concepts so gaps in prerequisites surface first."
    if mode == "back":
        return f"Same {blurb}, weighted toward the advanced concepts to stretch stronger students."
    return f"Probes {blurb} evenly across all {n} selected concepts. Best when every topic should carry equal weight."


def _weights(n: int, mode: str) -> List[float]:
    """Per-concept apportionment weights: front heavier-early, back heavier-late."""
    if mode == "front":
        return [n - i for i in range(n)]
    if mode == "back":
        return [i + 1 for i in range(n)]
    return [1] * n


def distribute_questions(concepts: List[Dict], q_count: int, mode: str) -> List[Dict]:
    """Largest-remainder apportionment of q_count questions across concepts."""
    n = len(concepts)
    if n == 0 or q_count <= 0:
        return []
    w = _weights(n, mode)
    w_sum = sum(w) or 1
    counts = [int((x / w_sum) * q_count) for x in w]  # floor
    _distribute_remainder(counts, w, w_sum, q_count)
    return [
        {"id": c["id"], "label": c["label"], "count": counts[i]}
        for i, c in enumerate(concepts) if counts[i] > 0
    ]


def _distribute_remainder(counts: List[int], w: List[float], w_sum: float, q_count: int) -> None:
    """Hand out leftover questions to the largest fractional remainders, in order."""
    fracs = [((w[i] / w_sum) * q_count - counts[i], i) for i in range(len(w))]
    order = [i for _, i in sorted(fracs, key=lambda p: -p[0])]  # descending remainder
    total, r = sum(counts), 0
    while total < q_count and order:
        counts[order[r % len(order)]] += 1
        total += 1
        r += 1


def build_variants(concepts: List[Dict], q_count: int, difficulty: str, exam_len: int = 30) -> List[Dict]:
    """Build the three exam variants for a difficulty (demo buildVariants)."""
    meta = DIFFICULTY_META.get(difficulty, DIFFICULTY_META["balanced"])
    return [
        {
            "id": f"{difficulty}-{a['key']}",
            "bank_key": meta["bank_key"],
            "title": f"{meta['name']} · {a['suffix']}",
            "badge": meta["badge"],
            "badge_label": meta["badge_label"],
            # The three variants share one difficulty, so surface the angle that
            # actually distinguishes them on the card (avoids 3x "Recall-focused").
            "angle_label": a["suffix"],
            "description": _angle_description(meta, a["mode"], len(concepts)),
            "q_count": q_count,
            "duration": f"{exam_len} min",
            "eds_focus": meta["eds_focus"],
            "distribution": distribute_questions(concepts, q_count, a["mode"]),
        }
        for a in VARIANT_ANGLES
    ]


def topic_bank(label: str, pool: Optional[List[str]]) -> List[str]:
    """A concept's full selectable bank: real questions, then generic extras, deduped."""
    seen, out = set(), []
    for q in list(pool or []) + [t(label) for t in GENERIC_Q + GENERIC_EXTRA]:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _questions_for(concept_id: str, label: str, banks: Dict[str, List[str]]) -> List[str]:
    """Resolve a concept's question pool: extracted bank first, else generic templates."""
    pool = banks.get(concept_id) or banks.get(label)
    return list(pool) if pool else [t(label) for t in GENERIC_Q]


def assemble_questions(distribution: List[Dict], banks: Dict[str, List[str]]) -> List[Dict]:
    """Pull `count` questions per concept from its bank, cycling (demo assembleExamQuestions)."""
    out: List[Dict] = []
    for d in distribution:
        pool = _questions_for(d["id"], d["label"], banks)
        for i in range(d["count"]):
            out.append({"topic": d["label"], "concept_id": d["id"], "q": pool[i % len(pool)]})
    return out
