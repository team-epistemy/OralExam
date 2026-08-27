"""Web-free aggregation for the anonymized class practice-performance dashboard.

Import-light (stdlib only), like emails.py / exam_questions.py, so the percentage
math is unit-testable without a database. The HTTP query fetches the rows and the
topic label map, then delegates here.
"""
from __future__ import annotations
import json
from collections import defaultdict

# (key, label, description, EDS-component field). The aspect→component mapping:
# Recall=Concepts, Application=Causal Links, In-depth=Novel Insight, plus an
# Authenticity signal. Surfaced in the UI so it's transparent.
ASPECTS = [
    ("recall", "Recall", "Named and defined the right concepts", "node_score"),
    ("application", "Application", "Connected and applied concepts (incl. case scenarios)", "edge_score"),
    ("depth", "In-depth Understanding", "Went beyond the basics with novel insight", "gen_score"),
    ("authenticity", "Authenticity", "Reasoning was genuine, not guessed", "r_gate"),
]


def aggregate_performance(rows, label_of=None, bar: float = 0.5) -> dict:
    """Aggregate practice-session answers into anonymized class figures.

    rows: iterable of ``(student_id, concept_ids, eds_components)`` — one per
    answered turn. concept_ids may be a list or a JSON string. Returns per-aspect
    (% of students at/above ``bar`` and class average) and per-topic (% of
    students who demonstrated it) stats. No per-student data appears in the
    output — only aggregate counts.
    """
    label_of = label_of or {}
    per_aspect = defaultdict(lambda: {k: [] for k, *_ in ASPECTS})
    per_topic = defaultdict(lambda: defaultdict(list))
    for student_id, concept_ids, comp in rows:
        if not isinstance(comp, dict):
            continue
        node = float(comp.get("node_score") or 0)
        for key, _label, _desc, field in ASPECTS:
            per_aspect[student_id][key].append(float(comp.get(field) or 0))
        topics = concept_ids if isinstance(concept_ids, list) else (json.loads(concept_ids) if concept_ids else [])
        for t in topics:
            per_topic[student_id][str(t)].append(node)

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
        scores, demoed = [], 0
        for s in students:
            ts = per_topic[s].get(t)
            if ts:
                a = avg(ts)
                scores.append(a)
                if a >= bar:
                    demoed += 1
        topics.append({"label": label_of.get(t, t),
                       "pct_students": round((demoed / n), 3) if n else 0.0,
                       "avg_score": round(avg(scores), 3), "n_attempted": len(scores)})
    topics.sort(key=lambda x: (-x["pct_students"], x["label"]))

    return {"practice_takers": n, "bar": bar, "aspects": aspects, "topics": topics}
