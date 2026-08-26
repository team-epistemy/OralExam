"""Regression test for the Postgres get_course mapping.

get_course must SELECT and return created_by: the per-professor ownership check
(_owns_course) compares course.created_by to the caller, so dropping the column
made every upload 403 with "professor role on this course required" — even for
the course owner. The in-memory repo returns the full object, so the existing
ownership tests couldn't catch the Postgres-path divergence.

No live DB needed: we stub _one (the only thing get_course touches) and assert
both that the SQL selects created_by and that it's populated on the Course.
"""
from backend.db.postgres import PostgresRepository


class _RecordingOne:
    """Stand-in for repo._one — records the SQL it was called with, returns a row."""
    def __init__(self, row):
        self.row = row
        self.sql = None
        self.params = None

    def __call__(self, sql, params):
        self.sql = sql
        self.params = params
        return self.row


def _repo_without_db() -> PostgresRepository:
    # Skip __init__ (it wants a real connection); get_course only calls self._one.
    return PostgresRepository.__new__(PostgresRepository)


def test_get_course_selects_and_returns_created_by():
    repo = _repo_without_db()
    one = _RecordingOne(("cid-1", "org-1", "Operations Management", "prof@x.edu"))
    repo._one = one  # type: ignore[method-assign]

    course = repo.get_course("cid-1")

    # The SQL must select created_by — omitting it is exactly what regressed uploads.
    assert "created_by" in (one.sql or "").lower()
    assert one.params == ("cid-1",)
    # ...and it must land on the Course, since _owns_course reads it.
    assert course is not None
    assert course.created_by == "prof@x.edu"
    assert course.course_id == "cid-1"
    assert course.org_id == "org-1"
    assert course.course_name == "Operations Management"


def test_get_course_returns_none_when_missing():
    repo = _repo_without_db()
    repo._one = lambda sql, params: None  # type: ignore[method-assign]
    assert repo.get_course("does-not-exist") is None
