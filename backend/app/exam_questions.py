"""Web-free helpers for per-assignment exam-question generation.

Import-light (stdlib only) on purpose — like emails.py — so the deterministic
parse/merge logic can be unit-tested without pulling in FastAPI or the LLM
client. The actual LLM call lives in http_app; everything reproducible lives
here.
"""
from __future__ import annotations

# Difficulty → what the generated questions should emphasise. Mirrors the demo's
# difficulty presets but framed as an instruction to the generator.
DIFFICULTY_FOCUS = {
    "recall": "definitional recall — precise definitions, formulas, and key facts",
    "balanced": "a balance of definitional recall and causal reasoning",
    "deep": "deep causal reasoning — mechanisms, prerequisite chains, and multi-step 'why' questions",
}

# A concept's authored bank is tagged by depth. Difficulty Focus selects which
# tiers to draw from (target tier first so small counts favor it).
DEPTH_TIERS = ("recall", "application", "in_depth", "case")
_DIFFICULTY_TIERS = {
    "recall": ("recall", "application"),
    "balanced": ("recall", "application", "in_depth", "case"),
    "deep": ("in_depth", "case", "application"),
}


def sanitize_bank(q) -> dict:
    """Normalize an authored `questions` field to the depth-tagged shape
    {recall, application, in_depth, case} with ≤3 clean strings each. A legacy
    flat list is treated as the recall tier so old graphs still work."""
    if isinstance(q, dict):
        return {t: [x.strip() for x in (q.get(t) or []) if isinstance(x, str) and x.strip()][:3]
                for t in DEPTH_TIERS}
    if isinstance(q, list):
        return {"recall": [x.strip() for x in q if isinstance(x, str) and x.strip()][:4],
                "application": [], "in_depth": [], "case": []}
    return {t: [] for t in DEPTH_TIERS}


def concept_bank(c, difficulty: str = "balanced") -> list:
    """A concept's question pool for a difficulty, drawn from its depth tiers.
    Handles both the new depth-tagged dict and a legacy flat list (used as-is)."""
    q = c.get("questions")
    if isinstance(q, list):                     # legacy flat bank — no depth info
        return [x for x in q if isinstance(x, str) and x.strip()]
    if not isinstance(q, dict):
        return []
    out, seen = [], set()
    for tier in _DIFFICULTY_TIERS.get(difficulty, _DIFFICULTY_TIERS["balanced"]):
        for x in (q.get(tier) or []):
            if isinstance(x, str) and x.strip() and x not in seen:
                seen.add(x)
                out.append(x)
    return out


def stored_concept_banks(concepts, difficulty: str = "balanced") -> dict:
    """Map each concept's id AND label to its depth-filtered question pool.

    Reads the `questions` authored on each concept at graph-build time and picks
    the tiers matching the requested difficulty (recall / balanced / deep).
    """
    banks: dict = {}
    for c in concepts or []:
        qs = concept_bank(c, difficulty)
        if c.get("id"):
            banks[c["id"]] = qs
        if c.get("label"):
            banks.setdefault(c["label"], qs)
    return banks


def parse_generated_banks(data) -> dict:
    """Index an LLM response of the form
    ``{"banks": [{"label": "...", "questions": ["...", ...]}]}`` by lowercased,
    stripped label, keeping only non-empty string questions. Tolerant of a
    non-dict payload or missing/None keys so a malformed generation degrades to
    "nothing generated" rather than raising.
    """
    by_label: dict = {}
    if not isinstance(data, dict):
        return by_label
    for b in (data.get("banks") or []):
        if not isinstance(b, dict):
            continue
        lbl = (b.get("label") or "").strip().lower()
        qs = [q.strip() for q in (b.get("questions") or [])
              if isinstance(q, str) and q.strip()]
        if lbl and qs:
            by_label[lbl] = qs
    return by_label


def merge_generated_banks(concepts, data, difficulty: str = "balanced") -> dict:
    """Merge LLM-generated per-concept questions (parsed from ``data``) with each
    concept's stored bank as a per-concept fallback, keyed by concept id AND
    label — the shape ``assemble_questions`` expects.

    A concept the generator covered uses the fresh questions; a concept it
    skipped falls back to that concept's stored (depth-filtered) bank. If the
    generator produced nothing usable for ANY concept, fall back wholesale to the
    stored banks (so the caller's generic-template fallback still applies).
    """
    by_label = parse_generated_banks(data)
    banks: dict = {}
    any_generated = False
    for c in concepts or []:
        label = c.get("label", "")
        cid = c.get("id")
        qs = by_label.get(label.strip().lower()) if label else None
        if qs:
            any_generated = True
        else:  # generator skipped this concept — fall back to its stored bank
            qs = concept_bank(c, difficulty)
        if cid:
            banks[cid] = qs
        if label:
            banks.setdefault(label, qs)
    return banks if any_generated else stored_concept_banks(concepts, difficulty)
