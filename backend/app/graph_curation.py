"""Web-free helper for persisting professor concept-curation onto a stored graph.

Import-light (stdlib only), like emails.py / exam_questions.py, so the merge/prune
is unit-testable without FastAPI. The HTTP handler loads the active graph, calls
apply_curation, and writes the result back.
"""
from __future__ import annotations
import re


def _slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (label or "").strip().lower()).strip("-")
    return s or "concept"


def apply_curation(stored_concepts, stored_relations, kept):
    """Rewrite a graph's concept set to the professor's curated ``kept`` list.

    ``kept`` is an ordered list of ``{"id"?: str, "label": str}``. A kept entry
    that matches a stored concept — by id, else by case-insensitive label —
    preserves that concept's FULL object (definition, questions); an unmatched
    entry becomes a stub ``{id, label, definition:"", questions:[]}`` that the
    per-assignment generator can still write questions for. Relations with an
    endpoint no longer in the kept set are dropped. Concept order follows
    ``kept``; ids are made unique. Returns ``(concepts, relations)``.
    """
    by_id, by_label = {}, {}
    for c in stored_concepts or []:
        if c.get("id"):
            by_id[c["id"]] = c
        if c.get("label"):
            by_label.setdefault(c["label"].strip().lower(), c)

    new_concepts, used_ids, kept_labels = [], set(), set()
    for entry in kept or []:
        label = (entry.get("label") or "").strip()
        if not label:
            continue
        existing = by_id.get(entry.get("id")) or by_label.get(label.lower())
        concept = dict(existing) if existing else {
            "id": _slug(label), "label": label, "definition": "", "questions": []}
        # Guarantee a unique, stable id even across added stubs / label clashes.
        cid = concept.get("id") or _slug(label)
        base, n = cid, 2
        while cid in used_ids:
            cid = f"{base}-{n}"
            n += 1
        concept["id"] = cid
        used_ids.add(cid)
        new_concepts.append(concept)
        kept_labels.add(label.lower())

    new_relations = [
        r for r in (stored_relations or [])
        if (r.get("src") or "").strip().lower() in kept_labels
        and (r.get("dst") or "").strip().lower() in kept_labels
    ]
    return new_concepts, new_relations
