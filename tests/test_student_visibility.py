"""A student sees only courses/assignments they are enrolled in.

Regression: an empty roster used to mean "open to the whole org", so every
student saw every course any professor created. These tests pin the SQL gate
(the queries are raw SQL, so a recording cursor stands in for Postgres)."""
from backend.app.http_app import (
    _enrolled_sql, _query_student_courses, _query_student_assignments,
    _query_exam_results, _withhold_unreleased,
)


class _Cursor:
    """Records executed SQL/params; replays canned rows in order."""

    def __init__(self, rows):
        self._rows = list(rows)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        return self._rows.pop(0) if self._rows else []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Repo:
    def __init__(self, rows):
        self.cursor_obj = _Cursor(rows)
        self.conn = self

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass


def test_enrolled_sql_requires_enrolment_from_either_table():
    sql = _enrolled_sql("c.course_id")
    assert "NOT EXISTS" not in sql                    # no open-to-everyone escape
    assert "FROM enrollment e" in sql                 # email-keyed roster mirror
    assert "FROM auth.enrollment ae" in sql           # authoritative auth-side
    assert sql.count("%s") == 2                       # one email per branch


def test_student_courses_gated_and_email_lowercased():
    # rows: to_regclass probe (table present), then the course rows
    repo = _Repo([("enrollment",), [("c1", "CS101")]])
    out = _query_student_courses(repo, "org1", "  Uthira2@Gmail.com ")
    assert out == [{"course_id": "c1", "course_name": "CS101"}]
    sql, params = repo.cursor_obj.calls[-1]
    assert "NOT EXISTS" not in sql
    assert params == ("org1", "uthira2@gmail.com", "uthira2@gmail.com")


def test_student_assignments_gated_by_enrolment():
    repo = _Repo([("enrollment",), None, []])  # regclass, assignment_type probe, rows
    assert _query_student_assignments(repo, "org1", "uthira2@gmail.com") == []
    sql, params = repo.cursor_obj.calls[-1]
    assert "NOT EXISTS" not in sql
    # (completed email, grade email, org, roster email, auth email)
    assert params == ("uthira2@gmail.com",) * 2 + ("org1",) + ("uthira2@gmail.com",) * 2


def test_student_assignments_report_grade_status_not_score():
    """The card needs Completed / Awaiting grade / Graded — never the number."""
    row = ("a1", "Midterm", "active", {"max_questions": 5}, None, "CS101", "c1",
           "exam", True, "released")
    repo = _Repo([("enrollment",), None, [row]])
    out = _query_student_assignments(repo, "org1", "uthira2@gmail.com")
    assert out[0]["completed"] is True and out[0]["grade_status"] == "released"
    assert "final_score" not in out[0] and "score" not in out[0]
    sql, _ = repo.cursor_obj.calls[-1]
    assert "g.status FROM grade g" in sql and "final_score" not in sql


def test_unknown_student_email_matches_nothing():
    repo = _Repo([("enrollment",), []])
    assert _query_student_courses(repo, "org1", "") == []


# ── Results: the draft EDS is not a mark ────────────────────────────────────

def _results_rows(grade_row, assignment_type):
    """Row queue for _query_exam_results: session, turns, grade, type probes."""
    turn = ("q1", "Explain X", "because Y", 0.8, {"feedback": "good"},
            {"node_score": 0.8, "edge_score": 0.0, "r_gate": 1.0, "gen_score": 0.5})
    return [("s1", "completed", None), [turn], grade_row, ("1",), (assignment_type,)]


def test_student_sees_no_score_until_the_professor_releases_it():
    repo = _Repo(_results_rows(None, "exam"))           # taken, never graded
    out = _query_exam_results(repo, "a1", "stud@x.edu", for_student=True)
    assert out["grade_released"] is False
    assert out["score"] is None and out["components"] is None
    assert "score" not in out["question_results"][0]    # no per-question EDS either
    assert "release" in out["feedback"].lower()


def test_pending_grade_is_still_withheld_from_the_student():
    repo = _Repo(_results_rows((0.84, {}, "pending"), "assignment"))
    assert _query_exam_results(repo, "a1", "stud@x.edu", for_student=True)["score"] is None


def test_released_grade_shows_the_professors_score():
    repo = _Repo(_results_rows((0.84, {"overall_comment": "Nice"}, "released"), "exam"))
    out = _query_exam_results(repo, "a1", "stud@x.edu", for_student=True)
    assert out["grade_released"] is True and out["score"] == 84
    assert out["feedback"] == "Nice"


def test_practice_results_are_exempt_eds_is_all_they_have():
    repo = _Repo(_results_rows(None, "practice"))
    out = _query_exam_results(repo, "a1", "stud@x.edu", for_student=True)
    assert out["grade_released"] is True and out["score"] == 80


def test_professor_still_sees_the_draft_eds():
    repo = _Repo(_results_rows(None, "exam"))
    out = _query_exam_results(repo, "a1", "stud@x.edu", for_student=False)
    assert out["score"] == 80 and out["question_results"][0]["score"] == 80


def test_withhold_keeps_the_students_own_answers():
    out = _withhold_unreleased({
        "score": 80, "components": {"node_score": 1.0}, "feedback": "EDS 80/100",
        "question_results": [{"question_id": "q1", "question_text": "Explain X",
                              "answer": "because Y", "score": 80, "feedback": "good"}],
    })
    q = out["question_results"][0]
    assert q["answer"] == "because Y" and q["question_text"] == "Explain X"
    assert "score" not in q and "feedback" not in q
