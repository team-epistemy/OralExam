"""Conformance (needs FastAPI): the syllabus is optional and assignment creation
is scoped by TOPICS, never by a class session.

Guarded with importorskip because http_app imports fastapi at module load, which
the lean CI test env doesn't install (same pattern as test_start_preview_gate).
"""
import pytest

pytest.importorskip("fastapi")  # http_app imports fastapi at module load

from backend.app.http_app import (
    _require_syllabus, BuildExamRequest, AssignExamRequest, ExamAssignQuestion,
)


class _ExplodingRepo:
    """Any DB access blows up — proves _require_syllabus is a pure no-op now
    (not a lookup that happens to pass)."""
    def __getattr__(self, name):
        raise AssertionError(f"_require_syllabus must not touch the repo (accessed .{name})")


def test_require_syllabus_is_a_noop_and_never_queries():
    assert _require_syllabus(_ExplodingRepo(), "course-123") is None


def test_build_exam_request_is_topic_scoped_with_no_session_field():
    req = BuildExamRequest(concept_ids=["Pricing", "Elasticity"], q_count=6,
                           exam_len=30, difficulty="balanced")
    assert req.concept_ids == ["Pricing", "Elasticity"]
    assert not hasattr(req, "session_id")   # build has no notion of a session


def test_build_exam_request_allows_whole_course_no_topics():
    assert BuildExamRequest().concept_ids is None   # empty selection = whole course


def test_assign_exam_request_scopes_by_topics_session_defaults_none():
    req = AssignExamRequest(
        title="Midterm",
        questions=[ExamAssignQuestion(concept_id="c1", topic="Pricing", q="Why do prices rise?")],
        scope_concepts=["Pricing"],
    )
    assert req.scope_concepts == ["Pricing"]
    assert req.session_id is None           # new UI never sends it; legacy-tolerant default
