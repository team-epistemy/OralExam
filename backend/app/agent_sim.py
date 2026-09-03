"""Agent-cohort exam simulation (self-contained).

Spawns N synthetic "student agents" whose competence is spread on a curve, has
each one take a real assignment's questions — answering in free-form prose and
following the examiner's Socratic probes turn by turn — and scores every answer
with the SAME EDS formula the live grader uses, so the resulting performance
report is comparable to real student results.

Self-contained: this module never creates student accounts or exam_session rows.
It reads an assignment's questions + expected reasoning paths, runs the
answer→evaluate loop in-process, and returns a JSON report. The HTTP layer
persists that report on an `agent_simulation` row.

Design for testability: the two LLM touchpoints (answer generation, answer
evaluation) are injected as callables, so the orchestration, competence curve,
and EDS math are all unit-testable with fakes and never spend tokens.
"""
from __future__ import annotations

import math
import statistics
from typing import Callable, List

from backend.constants import EDS_ALPHA, EDS_BETA, EDS_GAMMA

# Competence is a skill in [SKILL_LO, SKILL_HI]; 0 ≈ struggling, 1 ≈ mastery.
SKILL_LO, SKILL_HI = 0.05, 0.95
DEFAULT_MAX_FOLLOWUPS = 2   # Socratic probe rounds per question in a sim


# ── Competence curve ──────────────────────────────────────────────────────────
def skill_curve(n: int, curve: str = "linear") -> List[float]:
    """Return n competence skills in [SKILL_LO, SKILL_HI], ascending.

    - "linear": evenly spread from low to high (uniform spectrum of ability).
    - "bell":   clustered around the middle (most agents average, few extremes),
                via the logistic quantile function.
    """
    n = max(1, min(10, int(n)))
    if n == 1:
        return [round((SKILL_LO + SKILL_HI) / 2, 4)]
    if curve == "bell":
        out = []
        for i in range(n):
            u = (i + 0.5) / n                     # (0,1), symmetric
            z = math.log(u / (1 - u)) / 6.0       # logistic quantile, denser mid
            out.append(0.5 + z)
    else:  # linear
        step = (SKILL_HI - SKILL_LO) / (n - 1)
        out = [SKILL_LO + i * step for i in range(n)]
    return [round(min(SKILL_HI, max(SKILL_LO, s)), 4) for s in out]


def persona_directive(skill: float) -> str:
    """Answer-style instruction for a competence skill (fed to the answer LLM)."""
    if skill >= 0.8:
        band = ("an EXCELLENT student who has mastered the material: explain the "
                "MECHANISM step by step, make causal links explicit, use correct "
                "terminology, 4-7 precise sentences")
    elif skill >= 0.55:
        band = ("a GOOD student: mostly correct with real reasoning, but you miss "
                "a step or a nuance here and there, 3-5 sentences")
    elif skill >= 0.3:
        band = ("an AVERAGE student: you recall the main idea and some facts but "
                "your causal reasoning is partial and vague on the mechanism, 2-4 "
                "sentences")
    else:
        band = ("a STRUGGLING student: vague, surface-level answers that restate "
                "the question or name terms without explaining any mechanism; "
                "sometimes unsure, 1-2 short sentences")
    return (f"You are {band}. Your competence level is {skill:.2f} on a 0-1 scale — "
            "answer with exactly that level of depth and accuracy, no more.")


# ── EDS scoring (mirrors the live grader) ─────────────────────────────────────
def eds_from_components(expected_path: dict, nodes_sets: List[list],
                        edge_idx_sets: List[list], recitation_scores: List[float],
                        extension_sets: List[list]) -> float:
    """Per-question EDS in [0,1], accumulated across a question's sub-turns.

    Same formula as the live answer endpoint: union node/edge/extension coverage
    across sub-turns, take the least recitation (best authenticity), then
    R·(α·node + β·edge) + γ·(1 − R·coverage)·gen."""
    exp_nodes = expected_path.get("nodes", []) or []
    exp_edges = expected_path.get("edges", []) or []
    exp_ext = expected_path.get("extensions", []) or []

    all_nodes, all_edges, all_ext = set(), set(), set()
    for s in nodes_sets:
        all_nodes.update(s or [])
    for s in edge_idx_sets:
        all_edges.update(s or [])
    for s in extension_sets:
        all_ext.update(s or [])
    min_recit = min(recitation_scores) if recitation_scores else 0.5

    R = 1.0 - min_recit
    node_score = len(all_nodes) / max(len(exp_nodes), 1)
    edge_score = len(all_edges) / max(len(exp_edges), 1)
    gen = min(1.0, len(all_ext) / max(len(exp_ext), 3))
    coverage = (node_score + edge_score) / 2.0
    eds = R * (EDS_ALPHA * node_score + EDS_BETA * edge_score) + \
        EDS_GAMMA * (1.0 - R * coverage) * gen
    return round(min(1.0, max(0.0, eds)), 4)


def _legacy_score(answered: bool, adequate: bool) -> float:
    """0/0.4/1.0 fallback when a question has no expected reasoning path."""
    if not answered:
        return 0.0
    return 1.0 if adequate else 0.4


# ── Orchestration ─────────────────────────────────────────────────────────────
def take_question(question: dict, directive: str, answer_fn: Callable,
                  eval_fn: Callable, max_followups: int) -> dict:
    """Answer one question, following probes until adequate or the cap. Returns
    {topic, turns, adequate, answered, score(0-100)}."""
    qtext = question.get("text", "")
    expected = question.get("expected_path") or {}
    has_eds = bool(expected.get("nodes"))

    prior, node_sets, edge_sets, recit, ext_sets = [], [], [], [], []
    probe = None
    answered = adequate = False
    turns = 0
    for _round in range(max_followups + 1):
        ans = answer_fn(directive, qtext, probe, list(prior))
        ev = eval_fn(qtext, expected, ans, list(prior)) or {}
        turns += 1
        prior.append(ans)
        answered = bool(ev.get("answered", False)) or answered
        adequate = bool(ev.get("adequate", False))
        if has_eds:
            node_sets.append(ev.get("nodes_demonstrated", []) or [])
            edge_sets.append(ev.get("edges_demonstrated", []) or [])
            ext_sets.append(ev.get("novel_extensions", []) or [])
            recit.append(float(ev.get("recitation_score", 0.5)))
        probe = (ev.get("probe") or "").strip()
        if adequate or not probe:
            break

    if has_eds:
        score = eds_from_components(expected, node_sets, edge_sets, recit, ext_sets)
    else:
        score = _legacy_score(answered, adequate)
    return {
        "topic": question.get("topic", "general"),
        "turns": turns,
        "answered": answered,
        "adequate": adequate,
        "score": round(score * 100),
    }


def take_exam(questions: List[dict], directive: str, answer_fn: Callable,
              eval_fn: Callable, max_followups: int) -> dict:
    """One agent takes every question. Returns {score, per_q:[...]}."""
    per_q = [take_question(q, directive, answer_fn, eval_fn, max_followups)
             for q in questions]
    score = round(statistics.mean([p["score"] for p in per_q])) if per_q else 0
    return {"score": score, "answered": sum(1 for p in per_q if p["answered"]),
            "adequate": sum(1 for p in per_q if p["adequate"]),
            "questions": len(per_q), "per_q": per_q}


def run_simulation(questions: List[dict], num_agents: int, curve: str,
                   *, answer_fn: Callable, eval_fn: Callable,
                   max_followups: int = DEFAULT_MAX_FOLLOWUPS,
                   progress_fn: Callable = None) -> dict:
    """Run the whole cohort and build the performance report.

    answer_fn(directive, question_text, probe_or_None, prior_answers) -> str
    eval_fn(question_text, expected_path, answer_text, prior_answers) -> dict with
        answered, adequate, probe, and (when expected_path has nodes)
        nodes_demonstrated, edges_demonstrated, recitation_score, novel_extensions.
    progress_fn(done, total) is called after each agent (optional).
    """
    skills = skill_curve(num_agents, curve)
    agents = []
    for i, skill in enumerate(skills):
        directive = persona_directive(skill)
        res = take_exam(questions, directive, answer_fn, eval_fn, max_followups)
        agents.append({"index": i + 1, "skill": skill, **res})
        if progress_fn:
            progress_fn(i + 1, len(skills))
    return {
        "num_agents": len(skills),
        "curve": curve,
        "questions": len(questions),
        "agents": agents,
        "aggregate": _aggregate(agents),
        "per_question": _per_question(questions, agents),
    }


def _aggregate(agents: List[dict]) -> dict:
    scores = [a["score"] for a in agents]
    if not scores:
        return {"mean": 0, "min": 0, "max": 0, "stdev": 0, "distribution": {}}
    buckets = {"0-19": 0, "20-39": 0, "40-59": 0, "60-79": 0, "80-100": 0}
    for s in scores:
        key = ("0-19" if s < 20 else "20-39" if s < 40 else "40-59" if s < 60
               else "60-79" if s < 80 else "80-100")
        buckets[key] += 1
    return {
        "mean": round(statistics.mean(scores)),
        "min": min(scores),
        "max": max(scores),
        "stdev": round(statistics.pstdev(scores), 1),
        "distribution": buckets,
    }


def _per_question(questions: List[dict], agents: List[dict]) -> list:
    """Average score per question across agents — a difficulty signal."""
    out = []
    for qi, q in enumerate(questions):
        scores = [a["per_q"][qi]["score"] for a in agents if qi < len(a["per_q"])]
        out.append({
            "index": qi + 1,
            "topic": q.get("topic", "general"),
            "text": (q.get("text") or "")[:160],
            "avg_score": round(statistics.mean(scores)) if scores else 0,
        })
    return out


# ── Production LLM callables (Bedrock/Claude via call_bedrock) ─────────────────
# Kept out of the orchestration above so the engine stays unit-testable with
# fakes. Both are bounded with a per-call timeout + single retry, and degrade
# gracefully (a null answer / an "unanswered" verdict) rather than raising, so a
# slow model can't hang a whole cohort run.
_SIM_LLM_TIMEOUT_S = 40

# NOTE: this evaluation prompt mirrors the live answer endpoint's combined
# Socratic + EDS grader (submit_answer in http_app). Kept here so a sim run
# grades on the same rubric; unify into one shared builder when convenient.
def _eval_system_prompt(question_text: str, expected_path: dict) -> str:
    import json as _json
    return (
        "You are an Epistemy Socratic oral examiner performing two tasks.\n"
        "TASK 1: evaluate the student's answer; when adequate=false give a probe.\n"
        "TASK 2: given the expected reasoning path, identify which concepts and "
        "causal links the student DEMONSTRATED WITH UNDERSTANDING (not just named).\n"
        f"The exam question is: \"{question_text}\"\n"
        f"EXPECTED PATH:\n{_json.dumps(expected_path)}\n\n"
        "SCORING RULES:\n"
        "- A node is 'demonstrated' only if the student shows understanding of what it means.\n"
        "- An edge is 'demonstrated' only if the student articulates the CAUSAL MECHANISM.\n"
        "- recitation_score: 0.0=authentic reasoning, 1.0=pure keyword recitation.\n"
        "- adequate=true ONLY with clear mechanistic/causal reasoning.\n"
        "Respond ONLY with minified JSON, no prose, no fences:\n"
        '{"answered": true, "adequate": false, "probe": "follow-up question", '
        '"eds": {"nodes_demonstrated": ["node labels"], "edges_demonstrated": [0,1], '
        '"recitation_score": 0.3, "novel_extensions": ["concepts beyond the path"]}}'
    )


def default_answer_fn(settings):
    """Answer generator: persona-conditioned free-text via the LLM. Returns a str."""
    from backend.bedrock_helper import call_bedrock

    def fn(directive, question_text, probe, prior):
        system = (
            directive + "\n\nYou are taking an oral exam. You do NOT have the answer "
            "key — reason from your own understanding and stay in character for your "
            'competence level. Return ONLY minified JSON: {"answer": "<spoken answer>"} '
            "with no preamble, meta-commentary, or markdown."
        )
        if probe:
            user = (f"Original question: {question_text}\n"
                    + (f"Your earlier answer(s): {' | '.join(prior)}\n" if prior else "")
                    + f"Examiner's follow-up probe: {probe}\nRespond to the probe.")
        else:
            user = f"Exam question: {question_text}\nGive your spoken answer."
        try:
            data = call_bedrock(settings, system, user, max_tokens=600,
                                temperature=0.7, retries=1, timeout=_SIM_LLM_TIMEOUT_S)
            return (data.get("answer") or "").strip() or "I'm not sure."
        except Exception:  # noqa: BLE001 - degrade; a blank-ish answer scores low
            return "I'm not sure how to answer that."
    return fn


def default_eval_fn(settings):
    """Answer evaluator: the same combined Socratic + EDS grader the app uses."""
    from backend.bedrock_helper import call_bedrock

    def fn(question_text, expected_path, answer_text, prior):
        system = _eval_system_prompt(question_text, expected_path)
        ctx = f"Exam question: {question_text}\n\n"
        if prior:
            ctx += "Prior exchanges:\n" + "".join(f"Student: {p}\n" for p in prior) + "\n"
        ctx += f"Student's latest answer: {answer_text}"
        try:
            parsed = call_bedrock(settings, system, ctx, max_tokens=1500,
                                  temperature=0.1, retries=1, timeout=_SIM_LLM_TIMEOUT_S)
        except Exception:  # noqa: BLE001 - degrade to "answered but not adequate"
            return {"answered": bool(answer_text.strip()), "adequate": False, "probe": ""}
        eds = parsed.get("eds", {}) if isinstance(parsed, dict) else {}
        return {
            "answered": bool(parsed.get("answered", False)),
            "adequate": bool(parsed.get("adequate", False)),
            "probe": (parsed.get("probe") or "").strip(),
            "nodes_demonstrated": eds.get("nodes_demonstrated", []),
            "edges_demonstrated": eds.get("edges_demonstrated", []),
            "recitation_score": float(eds.get("recitation_score", 0.5)),
            "novel_extensions": eds.get("novel_extensions", []),
        }
    return fn
