"""Offline unit tests for the agent-cohort simulation engine.

The two LLM touchpoints are faked, so these exercise the competence curve, the
EDS scoring (must match the live grader's formula), and the orchestration /
report — deterministically and without spending tokens.
"""
from backend.app import agent_sim as S
from backend.constants import EDS_ALPHA, EDS_BETA, EDS_GAMMA


def test_skill_curve_linear_bounds_and_order():
    for n in range(1, 11):
        skills = S.skill_curve(n, "linear")
        assert len(skills) == n
        assert skills == sorted(skills)                    # ascending
        assert all(S.SKILL_LO <= s <= S.SKILL_HI for s in skills)
    # spread endpoints for n>1
    s = S.skill_curve(5, "linear")
    assert s[0] == S.SKILL_LO and s[-1] == S.SKILL_HI


def test_skill_curve_bell_is_centre_dense():
    s = S.skill_curve(9, "bell")
    assert len(s) == 9 and s == sorted(s)
    # middle gap (around 0.5) is smaller than the edge gap for a bell
    mid_gap = s[5] - s[4]
    edge_gap = s[1] - s[0]
    assert mid_gap < edge_gap


def test_skill_curve_clamps_to_1_to_10():
    assert len(S.skill_curve(0, "linear")) == 1
    assert len(S.skill_curve(99, "linear")) == 10


def test_eds_formula_matches_hand_computation():
    expected = {"nodes": ["a", "b"], "edges": [{}, {}], "extensions": ["x"]}
    # one sub-turn: 1 of 2 nodes, 1 of 2 edges, no extensions, recitation 0.0
    eds = S.eds_from_components(expected, [["a"]], [[0]], [0.0], [[]])
    R = 1.0
    node_score, edge_score = 0.5, 0.5
    coverage = 0.5
    gen = 0.0
    want = R * (EDS_ALPHA * node_score + EDS_BETA * edge_score) + \
        EDS_GAMMA * (1 - R * coverage) * gen
    assert abs(eds - round(min(1, max(0, want)), 4)) < 1e-6


def test_eds_unions_across_subturns():
    expected = {"nodes": ["a", "b"], "edges": [{}, {}], "extensions": []}
    # two sub-turns each covering a different node/edge → full coverage
    eds = S.eds_from_components(expected, [["a"], ["b"]], [[0], [1]], [0.2, 0.0], [[], []])
    # R uses the BEST (lowest) recitation = 0.0 → R=1, full node+edge coverage
    assert eds == round(EDS_ALPHA * 1.0 + EDS_BETA * 1.0, 4)


# ── Fakes: answer carries the persona band; eval maps band → components ────────
def _fake_answer(directive, question, probe, prior):
    return directive  # the band words (EXCELLENT/GOOD/AVERAGE/STRUGGLING) ride along


def _fake_eval(qtext, expected, answer_text, prior):
    band = answer_text
    if "EXCELLENT" in band:
        return {"answered": True, "adequate": True, "probe": "",
                "nodes_demonstrated": ["a", "b"], "edges_demonstrated": [0, 1],
                "recitation_score": 0.0, "novel_extensions": ["x"]}
    if "GOOD" in band:
        return {"answered": True, "adequate": True, "probe": "",
                "nodes_demonstrated": ["a"], "edges_demonstrated": [0],
                "recitation_score": 0.1, "novel_extensions": []}
    if "AVERAGE" in band:
        return {"answered": True, "adequate": False, "probe": "explain the link?",
                "nodes_demonstrated": ["a"], "edges_demonstrated": [],
                "recitation_score": 0.4, "novel_extensions": []}
    return {"answered": False, "adequate": False, "probe": "what do you mean?",
            "nodes_demonstrated": [], "edges_demonstrated": [],
            "recitation_score": 0.8, "novel_extensions": []}


def _questions(n=3):
    exp = {"nodes": ["a", "b"], "edges": [{}, {}], "extensions": ["x"]}
    return [{"text": f"Q{i}", "topic": f"t{i}", "expected_path": exp} for i in range(n)]


def test_run_simulation_report_shape_and_discrimination():
    report = S.run_simulation(_questions(3), num_agents=4, curve="linear",
                              answer_fn=_fake_answer, eval_fn=_fake_eval,
                              max_followups=1)
    assert report["num_agents"] == 4
    assert len(report["agents"]) == 4
    assert len(report["per_question"]) == 3
    agg = report["aggregate"]
    assert set(agg) == {"mean", "min", "max", "stdev", "distribution"}
    # agents are ordered by ascending skill; scores must be non-decreasing
    scores = [a["score"] for a in report["agents"]]
    assert scores == sorted(scores)
    assert scores[-1] > scores[0]           # strong strictly beats weak
    # per-question difficulty averages are within range
    assert all(0 <= q["avg_score"] <= 100 for q in report["per_question"])


def test_followups_stop_at_adequate():
    # A GOOD agent is adequate on the first turn → exactly one turn taken.
    q = _questions(1)
    out = S.take_exam(q, S.persona_directive(0.7), _fake_answer, _fake_eval, max_followups=3)
    assert out["per_q"][0]["turns"] == 1
    # A STRUGGLING agent never reaches adequate → uses all rounds (1 + max_followups)
    out2 = S.take_exam(q, S.persona_directive(0.1), _fake_answer, _fake_eval, max_followups=2)
    assert out2["per_q"][0]["turns"] == 3
