"""The communication turn handler, driven through a whole five-turn exam.

The ladder maths is covered in test_communication_ladder.py; this covers the
handler's contract with everything around it: the response shape the exam page
reads, the one evaluation row per turn, and the eds_score pattern that makes the
released grade come out as the overall CEFR score over six.

The DB is a dict-backed stand-in that answers the handler's queries by SQL shape,
so these run with no Postgres and no LLM.
"""
import json

import pytest

from backend.communication import content as C
from backend.communication import turn as T


# ── Fake DB ──────────────────────────────────────────────────────────────────

class _Cursor:
    def __init__(self, db):
        self.db = db
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        params = params or ()
        if sql.startswith("SELECT q.question_id, q.text"):
            self._result = (self.db["question_id"], self.db["question_text"])
        elif sql.startswith("SELECT COUNT(*) FROM session_turn"):
            self._result = (len(self.db["turns"]),)
        elif sql.startswith("INSERT INTO session_turn"):
            turn_id = f"turn-{len(self.db['turns'])}"
            self.db["turns"].append({"turn_id": turn_id,
                                     "answer": params[6]})
            self._result = (turn_id,)
        elif sql.startswith("UPDATE exam_session"):
            self._result = None
        elif sql.startswith("SELECT st.student_answer, e.raw_llm_output"):
            self._result = [(t["answer"], self.db["evals"][t["turn_id"]])
                            for t in self.db["turns"]
                            if t["turn_id"] in self.db["evals"]]
        elif sql.startswith("INSERT INTO evaluation"):
            # (eval_id, turn_id, org, course, student, question, score, bucket, raw)
            self.db["evals"][params[1]] = json.loads(params[8])
            self.db["eval_scores"][params[1]] = params[6]
            self.db["eval_buckets"][params[1]] = params[7]
            self._result = None
        else:
            raise AssertionError(f"unexpected SQL: {sql[:80]}")

    def fetchone(self):
        return self._result

    def fetchall(self):
        return self._result


class _Conn:
    def __init__(self, db):
        self.db = db
        self.commits = 0

    def cursor(self):
        return _Cursor(self.db)

    def commit(self):
        self.commits += 1


class _Repo:
    def __init__(self):
        self.db = {
            "question_id": "11111111-1111-1111-1111-111111111111",
            "question_text": C.PART2_QUESTION,
            "turns": [], "evals": {}, "eval_scores": {}, "eval_buckets": {},
        }
        self.conn = _Conn(self.db)


class _Req:
    def __init__(self, answer_text, question_index=0):
        self.question_index = question_index
        self.answer_text = answer_text


_SROW = ("ssss", "aaaa", "stud_1", "cccc", "active", 0,
         "oooo")   # session, assignment, student, course, status, turn, org


def _is_grader(system: str) -> bool:
    """Grading and examining are separate calls with distinguishable prompts."""
    return "CEFR GRID" in system


def _stub_llm(monkeypatch, bands, repeat_on=(), capture=None):
    """Stub both LLM calls: the grader returns `bands[i]`, the examiner a question.

    `capture` collects (role, system) per call so tests can assert on the prompts.
    """
    turn = {"n": 0}

    def fake_call(settings, system, user, **kw):
        if _is_grader(system):
            i = turn["n"]
            turn["n"] += 1
            if capture is not None:
                capture.append(("grade", system))
            band = bands[min(i, len(bands) - 1)]
            out = {c: band for c in C.CORE_CRITERIA}
            out[C.INTERACTION] = band
            out["note"] = f"turn {i + 1}"
            out["repeat_request"] = (i + 1) in repeat_on
            return out
        if capture is not None:
            capture.append(("examine", system))
        return {"follow_up": f"follow-up written after turn {turn['n']}"}

    monkeypatch.setattr(T, "call_bedrock", fake_call)


def _run_exam(monkeypatch, bands, n_turns=C.TOTAL_TURNS, repeat_on=(),
              capture=None):
    """Run n_turns against the stubbed LLM. Returns (repo, [response per turn])."""
    _stub_llm(monkeypatch, bands, repeat_on, capture)
    repo = _Repo()
    responses = []
    for i in range(n_turns):
        responses.append(T.handle_turn(repo, object(), "ssss", _SROW,
                                       _Req(f"answer {i + 1}")))
    return repo, responses


def _cefr(repo):
    """The cefr block of each evaluation row, in turn order."""
    return [repo.db["evals"][t["turn_id"]]["cefr"] for t in repo.db["turns"]]


# ── A whole exam ─────────────────────────────────────────────────────────────

def test_five_turns_write_five_evaluation_rows_with_band_data(monkeypatch):
    repo, _ = _run_exam(monkeypatch, [3, 3, 3, 3, 3])
    assert len(repo.db["turns"]) == 5
    assert len(repo.db["evals"]) == 5
    for i, cefr in enumerate(_cefr(repo), start=1):
        assert cefr["turn_number"] == i
        assert cefr["turn_band"] == 3
        assert cefr["turn_band_name"] == "B1"
        assert set(cefr["scores"]) == set(C.ALL_CRITERIA)


def test_part_2_is_turn_1_and_part_3_is_turns_2_to_5(monkeypatch):
    repo, _ = _run_exam(monkeypatch, [3] * 5)
    assert [c["part"] for c in _cefr(repo)] == ["part2"] + ["part3"] * 4


def test_interaction_is_scored_on_part_3_turns_only(monkeypatch):
    repo, _ = _run_exam(monkeypatch, [3] * 5)
    cefr = _cefr(repo)
    assert cefr[0]["scores"][C.INTERACTION] is None
    assert all(c["scores"][C.INTERACTION] == 3.0 for c in cefr[1:])


def test_the_ladder_drives_the_targets_across_the_exam(monkeypatch):
    # B1 throughout: probe up to B2, settle back to B1, probe B2 again...
    repo, _ = _run_exam(monkeypatch, [3] * 5)
    cefr = _cefr(repo)
    assert [c["next_target_band_name"] for c in cefr] == ["B2", "B1", "B2", "B1", "B2"]
    assert [c["direction"] for c in cefr] == ["up", "down", "up", "down", "up"]
    # Each turn's target is the previous turn's next target.
    assert [c["target_band"] for c in cefr] == [None, 4, 3, 4, 3]


def test_the_examiner_is_told_the_target_band_and_question_type(monkeypatch):
    """The examiner must be told which question to ask, not left to infer it."""
    capture = []
    _run_exam(monkeypatch, [3], n_turns=1, capture=capture)
    examiner = [s for role, s in capture if role == "examine"][0]
    # Part 2 graded B1, so the ladder probes B2 and picks a B2 type.
    assert "Target level: B2" in examiner
    assert "Question type:" in examiner
    assert "Question form" in examiner


def test_the_grader_is_not_asked_to_write_a_question(monkeypatch):
    """Splitting the calls is the point: the grader only grades."""
    capture = []
    _run_exam(monkeypatch, [3], n_turns=1, capture=capture)
    grader = [s for role, s in capture if role == "grade"][0]
    assert "follow_up" not in grader
    assert "Target level" not in grader


def test_the_final_turn_skips_the_examiner_call_entirely(monkeypatch):
    capture = []
    _run_exam(monkeypatch, [3] * 5, capture=capture)
    roles = [role for role, _ in capture]
    # Four follow-ups for five turns: the last turn has nothing left to ask.
    assert roles.count("grade") == 5
    assert roles.count("examine") == 4


def test_the_final_turn_asks_for_no_follow_up(monkeypatch):
    repo, responses = _run_exam(monkeypatch, [3] * 5)
    assert responses[-1]["probe"] == ""
    assert _cefr(repo)[-1]["final"] is True
    # ...and every earlier turn does have one to speak.
    assert all(r["probe"] for r in responses[:-1])


# ── Grade plumbing ───────────────────────────────────────────────────────────

def test_eds_score_is_zero_until_the_last_turn(monkeypatch):
    repo, _ = _run_exam(monkeypatch, [3] * 5)
    scores = [repo.db["eval_scores"][t["turn_id"]] for t in repo.db["turns"]]
    assert scores[:4] == [0.0, 0.0, 0.0, 0.0]
    assert scores[4] == pytest.approx(3.0 / 6)


def test_released_grade_equals_the_overall_cefr_score_over_six(monkeypatch):
    # Grade release sums eds_score over the session and divides by max_questions
    # (1 for this exam type), so the single non-zero row IS the released grade.
    repo, responses = _run_exam(monkeypatch, [4] * 5)
    total = sum(repo.db["eval_scores"].values())
    assert total == pytest.approx(round(4.0 / 6, 4))
    assert responses[-1]["eds_question"] == pytest.approx(round(4.0 / 6, 4))
    assert _cefr(repo)[-1]["overall_band"] == "B2"


def test_eds_question_tracks_the_running_score_every_turn(monkeypatch):
    # The live gauge on the exam page reads eds_question, so it must move.
    _, responses = _run_exam(monkeypatch, [2, 3, 4, 5, 6])
    values = [r["eds_question"] for r in responses]
    assert values == sorted(values)
    assert values[0] == pytest.approx(round(2.0 / 6, 4))


def test_response_matches_the_shape_the_exam_page_reads(monkeypatch):
    _, responses = _run_exam(monkeypatch, [3] * 5)
    r = responses[0]
    assert set(r) == {"answered", "adequate", "feedback", "probe",
                      "eds_delta", "eds_question", "eds_components", "cefr"}
    # adequate must stay false: the page advances the question on adequate, and
    # this exam type always runs all five turns.
    assert all(resp["adequate"] is False for resp in responses)
    assert r["answered"] is True
    # The CEFR grid is surfaced to the client for the dedicated speaking UI.
    assert set(r["cefr"]["scores"]) <= set(C.ALL_CRITERIA)
    assert r["cefr"]["overall_band"] in C.BANDS
    assert "direction" in r["cefr"] and "next_target_band" in r["cefr"]


# ── Repeat requests and degradation ──────────────────────────────────────────

def test_a_repeat_request_holds_the_ladder_and_is_left_out_of_the_score(monkeypatch):
    # Turn 2 is "can you repeat that?"; every other turn is a solid B2.
    repo, responses = _run_exam(monkeypatch, [4, 1, 4, 4, 4], repeat_on=(2,))

    cefr = _cefr(repo)
    assert cefr[1]["repeat_request"] is True
    assert responses[1]["answered"] is False
    # The turn still burns a turn but the target does not move...
    assert cefr[1]["target_band"] == cefr[1]["next_target_band"] == 5
    assert cefr[1]["direction"] == "same"
    # ...and its scores do not drag the final result down to A1.
    assert cefr[-1]["overall_band"] == "B2"


def test_a_repeat_request_re_asks_the_same_question_type(monkeypatch):
    """Nothing was demonstrated, so the bank must not advance past that type."""
    repo, _ = _run_exam(monkeypatch, [4, 1, 4, 4, 4], repeat_on=(2,))
    cefr = _cefr(repo)
    asked_on_turn_2 = cefr[0]["next_type_id"]
    assert cefr[1]["asked_type_id"] == asked_on_turn_2
    assert cefr[1]["next_type_id"] == asked_on_turn_2


def test_the_examiner_is_told_to_re_ask_on_a_repeat_request(monkeypatch):
    capture = []
    _run_exam(monkeypatch, [4, 1], n_turns=2, repeat_on=(2,), capture=capture)
    examiner = [s for role, s in capture if role == "examine"][-1]
    assert "asked you to repeat or rephrase" in examiner
    assert "do not change the question type" in examiner


def test_a_failed_grade_still_finishes_the_turn(monkeypatch):
    def boom(settings, system, user, **kw):
        if _is_grader(system):
            raise RuntimeError("bedrock down")
        return {"follow_up": "and what else?"}

    monkeypatch.setattr(T, "call_bedrock", boom)
    repo = _Repo()
    resp = T.handle_turn(repo, object(), "ssss", _SROW, _Req("an answer"))

    assert resp["probe"]                        # something to speak
    assert len(repo.db["evals"]) == 1           # the turn is still on record
    cefr = _cefr(repo)[0]
    assert cefr["turn_band"] == 3               # scored at the default band
    assert cefr["degraded"] is True             # ...and flagged as not a real judgement
    assert "grading unavailable" in repo.db["evals"]["turn-0"]["feedback"]


def test_a_normal_turn_is_not_flagged_as_degraded(monkeypatch):
    repo, _ = _run_exam(monkeypatch, [3] * 5)
    assert all(c["degraded"] is False for c in _cefr(repo))


def test_an_examiner_that_returns_nothing_still_yields_a_question(monkeypatch):
    def thin(settings, system, user, **kw):
        if _is_grader(system):
            return {c: 3 for c in C.CORE_CRITERIA}
        return {}                                # examiner produced no question

    monkeypatch.setattr(T, "call_bedrock", thin)
    repo = _Repo()
    resp = T.handle_turn(repo, object(), "ssss", _SROW, _Req("an answer"))
    assert resp["probe"]


# ── Storage contract ─────────────────────────────────────────────────────────

def test_a_failed_examiner_call_still_yields_a_question(monkeypatch):
    def boom(settings, system, user, **kw):
        if _is_grader(system):
            return {c: 3 for c in C.CORE_CRITERIA}
        raise RuntimeError("examiner down")

    monkeypatch.setattr(T, "call_bedrock", boom)
    repo = _Repo()
    resp = T.handle_turn(repo, object(), "ssss", _SROW, _Req("an answer"))
    assert resp["probe"]
    # The grade itself is real, so the turn is not flagged degraded.
    assert _cefr(repo)[0]["degraded"] is False


def test_the_evaluation_row_carries_the_criterion_detail_and_exam_kind(monkeypatch):
    repo, _ = _run_exam(monkeypatch, [3] * 5)
    row = repo.db["evals"]["turn-4"]
    assert row["exam_kind"] == "communication"
    assert row["cefr"]["criterion_averages"].keys() == set(C.ALL_CRITERIA)
    assert row["cefr"]["overall_score"] == 3.0


def test_eds_bucket_stays_within_the_check_constraint(monkeypatch):
    repo, _ = _run_exam(monkeypatch, [1, 1, 6, 6, 6])
    assert set(repo.db["eval_buckets"].values()) <= {"low", "medium", "high"}
