"""Concept provenance: per-document concept lists → course-cumulative graph.

`document_concept` / `document_concept_edge` capture what each document
contributed. `course_concept` / `course_concept_edge` are RECOMPUTED from only a
course's own document rows — so the course graph is a pure function of that
course's materials and off-subject concepts can never accumulate (the fix for
cross-course leaks). `graph_version` is snapshotted from the recomputed graph.

The dedup/merge is a pure function (`merge_document_concepts`) so it's unit-
testable without a DB; the DB wrappers just read rows and persist the result.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List

from backend.app.exam_questions import sanitize_bank, DEPTH_TIERS


def slug(label: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")
    return s[:48] or "concept"


def _merge_banks(a: dict, b: dict) -> dict:
    """Union two depth-tagged banks, ≤3 per tier, case-insensitive dedup."""
    a, b = sanitize_bank(a), sanitize_bank(b)
    out: dict = {}
    for tier in DEPTH_TIERS:
        seen, merged = set(), []
        for x in list(a.get(tier) or []) + list(b.get(tier) or []):
            k = x.strip().lower()
            if x.strip() and k not in seen:
                seen.add(k)
                merged.append(x.strip())
        out[tier] = merged[:3]
    return out


def merge_document_concepts(concept_rows: List[dict], edge_rows: List[dict]) -> dict:
    """Pure: dedup a course's per-document concepts into one graph.

    concept_rows: [{material_version_id, label, definition, abstraction_level, questions}]
    edge_rows:    [{src_label, dst_label, edge_type, confidence}]
    Returns {"concepts": [{id,label,definition,abstraction_level,questions,sources}],
             "relations": [{src,dst,edge_type,confidence}]} — edges kept only when
    BOTH endpoints survive as concepts.
    """
    by_label: Dict[str, dict] = {}
    for r in concept_rows or []:
        label = (r.get("label") or "").strip()
        if not label:
            continue
        key = label.lower()
        q = r.get("questions")
        q = q if isinstance(q, dict) else (json.loads(q) if q else {})
        if key not in by_label:
            by_label[key] = {"label": label, "definition": r.get("definition"),
                             "abstraction_level": r.get("abstraction_level"),
                             "questions": sanitize_bank(q), "sources": set()}
        else:
            e = by_label[key]
            e["questions"] = _merge_banks(e["questions"], q)
            if not e.get("definition") and r.get("definition"):
                e["definition"] = r.get("definition")
            if e.get("abstraction_level") is None and r.get("abstraction_level") is not None:
                e["abstraction_level"] = r.get("abstraction_level")
        mv = r.get("material_version_id")
        if mv:
            by_label[key]["sources"].add(str(mv))

    concepts = []
    for e in by_label.values():
        concepts.append({
            "id": slug(e["label"]), "label": e["label"],
            "definition": e.get("definition") or "",
            "abstraction_level": e["abstraction_level"] if e.get("abstraction_level") is not None else 0.5,
            "questions": e["questions"],
            "sources": sorted(e["sources"]),
        })

    relations, seen = [], set()
    for r in edge_rows or []:
        sk = (r.get("src_label") or "").strip().lower()
        dk = (r.get("dst_label") or "").strip().lower()
        if sk in by_label and dk in by_label and sk != dk:
            et = r.get("edge_type") or "PREREQUISITE_FOR"
            ek = (sk, dk, et)
            if ek in seen:
                continue
            seen.add(ek)
            relations.append({"src": by_label[sk]["label"], "dst": by_label[dk]["label"],
                              "edge_type": et, "confidence": float(r.get("confidence") or 0.8)})
    return {"concepts": concepts, "relations": relations}


# ── DB wrappers (take a psycopg2 cursor with app.org_id already bound) ─────────

def syllabus_version_ids(cur, org_id, course_id) -> List[str]:
    """material_version_ids marked as this course's syllabus (usually 0 or 1).

    The syllabus is an administrative scaffold (schedule/policies/topic outline),
    not learning content, so it is EXCLUDED from the concept graph — the graph is
    built from course materials only. Guarded by to_regclass so it's safe in
    environments where the syllabus pointer table doesn't exist yet."""
    cur.execute("SELECT to_regclass('public.course_syllabus')")
    if cur.fetchone()[0] is None:
        return []
    cur.execute(
        "SELECT material_version_id FROM course_syllabus "
        "WHERE course_id = %s::uuid AND org_id = %s::uuid AND material_version_id IS NOT NULL",
        (course_id, org_id))
    return [str(r[0]) for r in cur.fetchall()]


def write_document_concepts(cur, org_id, course_id, material_version_id, concepts, relations) -> None:
    """Replace one document's concept list + edges (mapping 1)."""
    cur.execute("DELETE FROM document_concept WHERE material_version_id = %s::uuid AND org_id = %s::uuid",
                (material_version_id, org_id))
    cur.execute("DELETE FROM document_concept_edge WHERE material_version_id = %s::uuid AND org_id = %s::uuid",
                (material_version_id, org_id))
    for c in concepts or []:
        label = (c.get("label") or "").strip()
        if not label:
            continue
        cur.execute(
            """INSERT INTO document_concept
               (org_id, course_id, material_version_id, label, definition, abstraction_level, questions)
               VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s::jsonb)""",
            (org_id, course_id, material_version_id, label, c.get("definition"),
             c.get("abstraction_level"), json.dumps(sanitize_bank(c.get("questions")))))
    for r in relations or []:
        src, dst = (r.get("src") or "").strip(), (r.get("dst") or "").strip()
        if not src or not dst:
            continue
        cur.execute(
            """INSERT INTO document_concept_edge
               (org_id, course_id, material_version_id, src_label, dst_label, edge_type, confidence)
               VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s)""",
            (org_id, course_id, material_version_id, src, dst,
             r.get("edge_type") or r.get("link_type") or "PREREQUISITE_FOR",
             float(r.get("confidence") or 0.8)))


def document_graph(cur, org_id, material_version_id) -> dict:
    """The concept graph for a SINGLE document (mapping 1): its own concepts +
    edges from document_concept / document_concept_edge. Returns the same
    {concepts, relations} shape as the course graph so the UI renders it the same."""
    cur.execute("""SELECT material_version_id, label, definition, abstraction_level, questions
                   FROM document_concept WHERE material_version_id = %s::uuid AND org_id = %s::uuid""",
                (material_version_id, org_id))
    concept_rows = [{"material_version_id": r[0], "label": r[1], "definition": r[2],
                     "abstraction_level": r[3], "questions": r[4]} for r in cur.fetchall()]
    cur.execute("""SELECT src_label, dst_label, edge_type, confidence
                   FROM document_concept_edge WHERE material_version_id = %s::uuid AND org_id = %s::uuid""",
                (material_version_id, org_id))
    edge_rows = [{"src_label": r[0], "dst_label": r[1], "edge_type": r[2], "confidence": r[3]}
                 for r in cur.fetchall()]
    graph = merge_document_concepts(concept_rows, edge_rows)
    for c in graph["concepts"]:
        c.pop("sources", None)
    return graph


def recompute_course_graph(cur, org_id, course_id) -> dict:
    """Rebuild course_concept + course_concept_edge from ONLY this course's
    document_concept rows; return the {concepts, relations} snapshot.

    First purges orphaned provenance — rows whose source document no longer has
    chunks (the material was deleted or re-ingested). Without this, a removed
    document's concepts would keep resurrecting on every recompute (the observed
    cross-subject 'leak')."""
    cur.execute("""DELETE FROM document_concept dc
                   WHERE dc.course_id = %s::uuid AND dc.org_id = %s::uuid
                     AND NOT EXISTS (SELECT 1 FROM chunk c WHERE c.material_version_id = dc.material_version_id)""",
                (course_id, org_id))
    cur.execute("""DELETE FROM document_concept_edge de
                   WHERE de.course_id = %s::uuid AND de.org_id = %s::uuid
                     AND NOT EXISTS (SELECT 1 FROM chunk c WHERE c.material_version_id = de.material_version_id)""",
                (course_id, org_id))
    # Build the course graph from MATERIALS only — the syllabus is excluded even
    # if it was ingested and wrote provenance rows (it may have been extracted
    # before being marked as the syllabus). `<> ALL('{}')` excludes nothing when
    # there is no syllabus, so the empty case is a no-op.
    excluded = syllabus_version_ids(cur, org_id, course_id)
    cur.execute("""SELECT material_version_id, label, definition, abstraction_level, questions
                   FROM document_concept
                   WHERE course_id = %s::uuid AND org_id = %s::uuid
                     AND material_version_id <> ALL(%s::uuid[])""",
                (course_id, org_id, excluded))
    concept_rows = [{"material_version_id": r[0], "label": r[1], "definition": r[2],
                     "abstraction_level": r[3], "questions": r[4]} for r in cur.fetchall()]
    cur.execute("""SELECT src_label, dst_label, edge_type, confidence
                   FROM document_concept_edge
                   WHERE course_id = %s::uuid AND org_id = %s::uuid
                     AND material_version_id <> ALL(%s::uuid[])""",
                (course_id, org_id, excluded))
    edge_rows = [{"src_label": r[0], "dst_label": r[1], "edge_type": r[2], "confidence": r[3]}
                 for r in cur.fetchall()]

    graph = merge_document_concepts(concept_rows, edge_rows)

    cur.execute("DELETE FROM course_concept WHERE course_id = %s::uuid AND org_id = %s::uuid", (course_id, org_id))
    cur.execute("DELETE FROM course_concept_edge WHERE course_id = %s::uuid AND org_id = %s::uuid", (course_id, org_id))
    for c in graph["concepts"]:
        cur.execute(
            """INSERT INTO course_concept
               (org_id, course_id, label, definition, abstraction_level, questions, source_material_version_ids)
               VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s::jsonb, %s::jsonb)""",
            (org_id, course_id, c["label"], c["definition"], c["abstraction_level"],
             json.dumps(c["questions"]), json.dumps(c["sources"])))
    for e in graph["relations"]:
        cur.execute(
            """INSERT INTO course_concept_edge (org_id, course_id, src_label, dst_label, edge_type, confidence)
               VALUES (%s::uuid, %s::uuid, %s, %s, %s, %s)
               ON CONFLICT (course_id, src_label, dst_label, edge_type) DO NOTHING""",
            (org_id, course_id, e["src"], e["dst"], e["edge_type"], e["confidence"]))
    # graph_version snapshot doesn't carry the internal `sources` field.
    for c in graph["concepts"]:
        c.pop("sources", None)
    return graph


def snapshot_course_graph(cur, org_id, course_id) -> dict:
    """Recompute the course graph (materials only) and publish it as the active
    `graph_version` snapshot. Shared by every code path that (re)builds a graph —
    the ingest pipeline, the manual rebuild, and marking a material as syllabus —
    so they stay consistent. Returns the {concepts, relations} snapshot."""
    import uuid as _uuid
    snapshot = recompute_course_graph(cur, org_id, course_id)
    cur.execute("UPDATE graph_version SET is_active = false WHERE org_id = %s AND course_id = %s",
                (org_id, course_id))
    cur.execute(
        """INSERT INTO graph_version
           (version_id, org_id, course_id, graph_version, node_count,
            edge_count, validation_score, is_active, s3_key)
           VALUES (%s::uuid, %s::uuid, %s::uuid, 1, %s, %s, %s, true, %s)""",
        (str(_uuid.uuid4()), org_id, course_id, len(snapshot["concepts"]),
         len(snapshot["relations"]), 0.8, json.dumps(snapshot)))
    return snapshot
