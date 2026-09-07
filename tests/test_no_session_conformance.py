"""Conformance tests for the simplified model:

    Professor -> Course -> Material (identified by topics) -> Assignment (by topics)

Class sessions are no longer part of any professor flow:
  * uploading a material creates NO class session (the material stands alone),
  * no course action is gated behind a syllabus (syllabus is optional),
  * assignment build/assign is scoped by TOPICS (concept ids), never a session.

These pin the requirement so a regression that reintroduces session coupling
fails loudly.
"""
import pytest

from backend.api.service import MaterialsApi
from backend.db.memory import InMemoryRepository
from backend.async_jobs.queue import InMemoryQueue
from backend.testing.fakes import FakeS3
from backend.models import IngestRequest
from backend.app.http_app import (
    _require_syllabus, BuildExamRequest, AssignExamRequest, ExamAssignQuestion,
)


def _api():
    repo = InMemoryRepository()
    repo.set_tenant("org_a")
    return repo, MaterialsApi(repo, FakeS3(), InMemoryQueue(), lambda caller, cid: True)


# ── Syllabus is optional: the gate is a no-op ────────────────────────────────
class _ExplodingRepo:
    """Any DB access would blow up — proves _require_syllabus never touches the
    repo now (it's a pure no-op, not a lookup that happens to pass)."""
    def __getattr__(self, name):
        raise AssertionError(f"_require_syllabus must not touch the repo (accessed .{name})")


def test_require_syllabus_is_a_noop_and_never_queries():
    # Returns None and does not raise, without touching the repo at all.
    assert _require_syllabus(_ExplodingRepo(), "course-123") is None


# ── Uploading a material creates no class session ────────────────────────────
def test_material_upload_creates_no_class_session():
    repo, api = _api()
    req = IngestRequest(org_name="org_a", course_name="Ops", file_name="lecture.pdf",
                        mime_type="application/pdf", bytes=2048)
    resp = api.presign_by_name("prof_1", "professor", "org_a", req)
    assert not resp.session_id                      # no session on the response
    assert len(repo._sessions) == 0                 # none created anywhere
    assert not repo.get_material(resp.material_id).session_id  # material stands alone


def test_multiple_uploads_create_no_sessions():
    repo, api = _api()
    for name in ("a.pdf", "b.pdf", "c.md"):
        api.presign_by_name("prof_1", "professor", "org_a",
                            IngestRequest(org_name="org_a", course_name="Ops",
                                          file_name=name, mime_type="application/pdf", bytes=10))
    assert len(repo._sessions) == 0


# ── Assignment creation is scoped by TOPICS, not sessions ────────────────────
def test_build_exam_request_is_topic_scoped_with_no_session_field():
    req = BuildExamRequest(concept_ids=["Pricing", "Elasticity"], q_count=6,
                           exam_len=30, difficulty="balanced")
    assert req.concept_ids == ["Pricing", "Elasticity"]
    # The build request has no notion of a session at all.
    assert not hasattr(req, "session_id")


def test_build_exam_request_allows_whole_course_no_topics():
    req = BuildExamRequest()  # empty selection = whole course
    assert req.concept_ids is None


def test_assign_exam_request_scopes_by_topics_session_defaults_none():
    req = AssignExamRequest(
        title="Midterm",
        questions=[ExamAssignQuestion(concept_id="c1", topic="Pricing", q="Why do prices rise?")],
        scope_concepts=["Pricing"],
    )
    assert req.scope_concepts == ["Pricing"]
    # session_id is not sent by the new UI; it defaults to None (legacy-tolerant).
    assert req.session_id is None
