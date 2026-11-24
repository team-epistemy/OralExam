"""Web-free aggregation for the anonymized class practice-performance dashboard.

Import-light (stdlib only), like emails.py / exam_questions.py, so the percentage
math is unit-testable without a database. The HTTP query fetches the rows and the
topic label map, then delegates here.
"""
from __future__ import annotations
import json
from collections import defaultdict

from backend.constants import compute_eds

# (key, label, description, EDS-component field). The aspect→component mapping:
# Recall=Concepts, Application=Causal Links. Surfaced in the UI so the
# correctness-only EDS rubric is transparent. (Novel Insight/gen was retired.)
ASPECTS = [
    ("recall", "Recall", "Named and defined the right concepts", "node_score"),
    ("application", "Application", "Connected and applied concepts (incl. case scenarios)", "edge_score"),
]


def aggregate_performance(rows, label_of=None, bar: float = 0.5) -> dict:
    """Aggregate practice-session answers into anonymized class figures.

    rows: iterable of ``(student_id, concept_ids, eds_components)`` — one per
    answered turn. concept_ids may be a list or a JSON string. Returns per-aspect
    (% of students at/above ``bar`` and class average) and per-topic stats. No
    per-student data appears in the output — only aggregate counts.

    Each topic reports BOTH signals so coverage isn't mistaken for mastery:
      - NAMED:    student recalled the topic's concepts (node_score >= bar).
      - MASTERED: student demonstrated it on the full EDS rubric
                  (compute_eds(node, edge) >= bar) — the same number as the
                  headline score. ``pct_students``/``avg_score`` stay = NAMED for
                  back-compat; ``pct_mastered``/``avg_mastered`` are the new,
                  stricter figures the UI leads with.
    """
    label_of = label_of or {}
    per_aspect = defaultdict(lambda: {k: [] for k, *_ in ASPECTS})
    per_topic = defaultdict(lambda: defaultdict(list))          # node_score (named)
    per_topic_eds = defaultdict(lambda: defaultdict(list))      # full EDS (mastered)
    for student_id, concept_ids, comp in rows:
        if not isinstance(comp, dict):
            continue
        node = float(comp.get("node_score") or 0)
        edge = float(comp.get("edge_score") or 0)
        eds = compute_eds(node, edge)
        for key, _label, _desc, field in ASPECTS:
            per_aspect[student_id][key].append(float(comp.get(field) or 0))
        topics = concept_ids if isinstance(concept_ids, list) else (json.loads(concept_ids) if concept_ids else [])
        for t in topics:
            per_topic[student_id][str(t)].append(node)
            per_topic_eds[student_id][str(t)].append(eds)

    students = list(per_aspect.keys())
    n = len(students)

    def avg(xs):
        return sum(xs) / len(xs) if xs else 0.0

    aspects = []
    for key, label, desc, _field in ASPECTS:
        per_student = [avg(per_aspect[s][key]) for s in students]
        pct = (sum(1 for a in per_student if a >= bar) / n) if n else 0.0
        aspects.append({"key": key, "label": label, "description": desc,
                        "pct_students": round(pct, 3), "avg_score": round(avg(per_student), 3)})

    all_topics = set()
    for s in students:
        all_topics.update(per_topic[s].keys())
    topics = []
    for t in all_topics:
        named_scores, named_demoed = [], 0
        mastered_scores, mastered_demoed = [], 0
        for s in students:
            ns = per_topic[s].get(t)
            if ns:
                a = avg(ns)
                named_scores.append(a)
                if a >= bar:
                    named_demoed += 1
            es = per_topic_eds[s].get(t)
            if es:
                m = avg(es)
                mastered_scores.append(m)
                if m >= bar:
                    mastered_demoed += 1
        topics.append({
            "label": label_of.get(t, t),
            # back-compat: pct_students / avg_score == NAMED (concept recall)
            "pct_students": round((named_demoed / n), 3) if n else 0.0,
            "avg_score": round(avg(named_scores), 3),
            "pct_named": round((named_demoed / n), 3) if n else 0.0,
            "avg_named": round(avg(named_scores), 3),
            # new, stricter: MASTERED (full EDS rubric)
            "pct_mastered": round((mastered_demoed / n), 3) if n else 0.0,
            "avg_mastered": round(avg(mastered_scores), 3),
            "n_attempted": len(named_scores),
        })
    # Order by mastery (the figure the UI leads with), then name.
    topics.sort(key=lambda x: (-x["pct_mastered"], x["label"]))

    return {"practice_takers": n, "bar": bar, "aspects": aspects, "topics": topics}
