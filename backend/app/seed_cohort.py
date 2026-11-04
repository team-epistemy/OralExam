"""Seed a cohort of SYNTHETIC students against a real assignment.

Reuses the agent_sim engine (bell-curve ability spread + persona answers + the
same EDS grader the live exam uses) and persists each agent as a REAL completed
exam_session — session_turn + evaluation + question_eds_aggregate rows shaped
exactly like a live student's — so the professor Performance dashboard and
GradeView populate legitimately.

This writes synthetic data for demos/testing: students are labelled
``sim-student-NN@<domain>`` so they are never mistaken for real pilots. Re-running
for the same assignment first clears its prior ``sim-`` sessions (idempotent).

CLI:  seed-cohort <course_name> <assignment_title> [n=10] [curve=bell] [domain]
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone

from backend.config import Settings
from backend.app import factory, agent_sim
from backend.constants import compute_eds


def _resolve_org_ids(cur) -> list:
    """Owner conn: list candidate org_ids. Tenant tables are FORCE-RLS, so try to
    bypass; fall back to the (non-FORCE) auth.app_user if org can't be read."""
    try:
        cur.execute("SET row_security = off")
    except Exception:
        cur.connection.rollback()
    try:
        cur.execute("SELECT org_id, org_name FROM org")
        rows = cur.fetchall()
        if rows:
            return rows
    except Exception:
        cur.connection.rollback()
    cur.execute("SELECT DISTINCT org_id FROM auth.app_user")
    return [(r[0], "?") for r in cur.fetchall()]


def _find_assignment(cur, course_name, assignment_title):
    """Return (org_id, course_id, assignment_id, question_set_id) or None, by
    scanning each org with app.org_id set so RLS lets us read its tenant rows."""
    for oid, _oname in _resolve_org_ids(cur):
        cur.execute("SELECT set_config('app.org_id', %s, false)", (str(oid),))
        cur.execute(
            "SELECT course_id FROM course WHERE course_name = %s OR title = %s",
            (course_name, course_name))
        crow = cur.fetchone()
        if not crow:
            continue
        course_id = str(crow[0])
        cur.execute(
            "SELECT assignment_id, question_set_id FROM assignment "
            "WHERE course_id = %s::uuid AND title = %s",
            (course_id, assignment_title))
        arow = cur.fetchone()
        if arow:
            return str(oid), course_id, str(arow[0]), str(arow[1])
    return None


def _load_questions(cur, settings, question_set_id, course_id, org_id):
    """Ordered questions with a usable expected_path; generate+persist any missing
    one (EDS — and therefore topic coverage — needs the concept graph fragment)."""
    from backend.app import http_app as H
    cur.execute(
        """SELECT q.question_id, q.text, q.concept_ids, q.expected_path, qsm.position
           FROM question_set_membership qsm
           JOIN question q ON q.question_id = qsm.question_id
           WHERE qsm.question_set_id = %s::uuid
           ORDER BY qsm.position""",
        (question_set_id,))
    out = []
    for qid, text, concept_ids, ep, pos in cur.fetchall():
        concept_ids = concept_ids if isinstance(concept_ids, list) else (json.loads(concept_ids) if concept_ids else [])
        ep = ep if isinstance(ep, dict) else (json.loads(ep) if ep else {})
        if not ep.get("nodes"):
            print(f"  generating expected_path for q@{pos} ({str(qid)[:8]})…", flush=True)
            try:
                ep = H._generate_expected_path(settings, text, concept_ids) or {}
            except Exception as exc:
                print(f"    expected_path generation failed: {exc}", flush=True)
                ep = {}
            if ep.get("nodes"):
                cur.execute("UPDATE question SET expected_path = %s::jsonb WHERE question_id = %s::uuid",
                            (json.dumps(ep), qid))
                cur.connection.commit()
        out.append({"question_id": str(qid), "text": text, "concept_ids": concept_ids,
                    "expected_path": ep, "topic": (concept_ids[0] if concept_ids else "general"),
                    "position": pos})
    return out


def _clear_prior(cur, assignment_id, prefix):
    """Delete prior synthetic sessions for this assignment (FK-safe order)."""
    cur.execute("SELECT session_id FROM exam_session WHERE assignment_id = %s::uuid AND student_id LIKE %s",
                (assignment_id, prefix + "%"))
    sids = [str(r[0]) for r in cur.fetchall()]
    if not sids:
        return 0
    cur.execute("""DELETE FROM evaluation WHERE turn_id IN
                   (SELECT turn_id FROM session_turn WHERE session_id = ANY(%s::uuid[]))""", (sids,))
    for tbl in ("question_eds_aggregate", "grade", "session_turn"):
        cur.execute(f"DELETE FROM {tbl} WHERE session_id = ANY(%s::uuid[])", (sids,))
    cur.execute("DELETE FROM exam_session WHERE session_id = ANY(%s::uuid[])", (sids,))
    cur.connection.commit()
    return len(sids)


def _bucket(eds: float) -> str:
    return "high" if eds >= 0.7 else ("medium" if eds >= 0.3 else "low")


def _persist_agent(cur, org_id, course_id, assignment_id, student_id, questions, per_q, now):
    """Write one completed session + its turns/evaluations/aggregates. per_q is the
    list of agent_sim.take_question results, aligned with `questions`."""
    session_id = str(uuid.uuid4())
    qids = [q["question_id"] for q in questions]
    cur.execute(
        """INSERT INTO exam_session
           (session_id, assignment_id, student_id, org_id, course_id, status,
            current_turn_index, questions_delivered, concepts_covered, is_preview,
            started_at, completed_at)
           VALUES (%s::uuid, %s::uuid, %s, %s::uuid, %s::uuid, 'completed',
                   %s, %s::jsonb, '[]'::jsonb, false, %s, %s)""",
        (session_id, assignment_id, student_id, org_id, course_id,
         len(questions), json.dumps(qids), now - timedelta(minutes=12), now))

    q_scores = []
    for q, res in zip(questions, per_q):
        expected = q["expected_path"] or {}
        exp_nodes = [agent_sim._node_label(x) for x in (expected.get("nodes") or [])]
        exp_edges = expected.get("edges") or []
        n_nodes, n_edges = max(len(exp_nodes), 1), max(len(exp_edges), 1)
        union_nodes, union_edges = set(), set()
        last_R = None
        for st, turn in enumerate(res.get("transcript") or []):
            tn = turn.get("nodes_demonstrated") or []
            te = [x for x in (turn.get("edges_demonstrated") or []) if isinstance(x, int)]
            per_node = len(tn) / n_nodes
            per_edge = len(te) / n_edges
            union_nodes.update(tn)
            union_edges.update(te)
            eds_cum = compute_eds(len(union_nodes) / n_nodes, len(union_edges) / n_edges)
            last_R = turn.get("recitation_score")
            turn_id = str(uuid.uuid4())
            cur.execute(
                """INSERT INTO session_turn
                   (turn_id, session_id, org_id, turn_index, sub_turn_index,
                    question_id, student_answer, answered_at)
                   VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s::uuid, %s, %s)""",
                (turn_id, session_id, org_id, q["position"], st, q["question_id"],
                 turn.get("answer", ""), now - timedelta(minutes=11 - st)))
            comp = {"node_score": round(per_node, 4), "edge_score": round(per_edge, 4),
                    "r_gate": last_R, "gen_score_norm": 0.0,
                    "nodes_detected": tn, "edges_demonstrated": te,
                    "novel_extensions": turn.get("novel_extensions") or [],
                    "raw_probe_score": last_R}
            raw = {"answered": turn.get("answered"), "adequate": turn.get("adequate"),
                   "feedback": "", "probe": turn.get("probe", ""), "eds_question": eds_cum}
            cur.execute(
                """INSERT INTO evaluation
                   (evaluation_id, turn_id, org_id, course_id, student_id,
                    question_id, eds_score, eds_bucket, raw_llm_output, eds_components)
                   VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s::uuid, %s, %s, %s::jsonb, %s::jsonb)""",
                (str(uuid.uuid4()), turn_id, org_id, course_id, student_id, q["question_id"],
                 eds_cum, _bucket(eds_cum), json.dumps(raw), json.dumps(comp)))
        agg_node = len(union_nodes) / n_nodes
        agg_edge = len(union_edges) / n_edges
        final_eds = compute_eds(agg_node, agg_edge)
        q_scores.append(final_eds)
        cur.execute(
            """INSERT INTO question_eds_aggregate
               (session_id, question_id, org_id, node_score, edge_score, r_gate,
                gen_score_norm, coverage, final_eds, turn_details)
               VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s::jsonb)
               ON CONFLICT (session_id, question_id) DO UPDATE SET
                 node_score=EXCLUDED.node_score, edge_score=EXCLUDED.edge_score,
                 final_eds=EXCLUDED.final_eds""",
            (session_id, q["question_id"], org_id, round(agg_node, 4), round(agg_edge, 4),
             last_R, 0.0, round((agg_node + agg_edge) / 2, 4), final_eds, json.dumps({})))
    overall = round(100 * (sum(q_scores) / len(q_scores))) if q_scores else 0
    return session_id, overall


def run(course_name: str, assignment_title: str, n: int = 10,
        curve: str = "bell", domain: str = "columbia.demo",
        max_followups: int = 1) -> None:
    settings = Settings()
    conn = factory.db_connection(settings)
    conn.autocommit = False
    cur = conn.cursor()

    found = _find_assignment(cur, course_name, assignment_title)
    if not found:
        print(f"NOT FOUND: course={course_name!r} assignment={assignment_title!r}", flush=True)
        return
    org_id, course_id, assignment_id, qs_id = found
    print(f"org={org_id} course={course_id} assignment={assignment_id}", flush=True)

    questions = _load_questions(cur, settings, qs_id, course_id, org_id)
    print(f"{len(questions)} question(s); expected_path nodes: "
          f"{[len((q['expected_path'] or {}).get('nodes') or []) for q in questions]}", flush=True)

    cleared = _clear_prior(cur, assignment_id, "sim-")
    if cleared:
        print(f"cleared {cleared} prior synthetic session(s)", flush=True)

    answer_fn = agent_sim.default_answer_fn(settings)
    eval_fn = agent_sim.default_eval_fn(settings)
    skills = agent_sim.skill_curve(n, curve)
    now = datetime.now(timezone.utc)
    print(f"seeding {len(skills)} agents (curve={curve}, max_followups={max_followups}); "
          f"skills={skills}", flush=True)

    results = []
    for i, skill in enumerate(skills):
        directive = agent_sim.persona_directive(skill)
        student_id = f"sim-student-{i + 1:02d}@{domain}"
        try:
            per_q = [agent_sim.take_question(q, directive, answer_fn, eval_fn,
                                             max_followups) for q in questions]
            sid, overall = _persist_agent(cur, org_id, course_id, assignment_id,
                                          student_id, questions, per_q, now)
            conn.commit()
            results.append(overall)
            print(f"  [{i + 1}/{len(skills)}] {student_id} skill={skill} overall_EDS={overall}", flush=True)
        except Exception as exc:
            conn.rollback()
            print(f"  [{i + 1}/{len(skills)}] {student_id} FAILED: {exc}", flush=True)

    if results:
        results.sort()
        print(f"DONE: {len(results)} sessions; EDS min/median/max = "
              f"{results[0]}/{results[len(results) // 2]}/{results[-1]}; all={results}", flush=True)
    else:
        print("DONE: no sessions written", flush=True)
