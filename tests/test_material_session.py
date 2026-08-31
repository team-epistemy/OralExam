"""Tests: a material is always mapped to a class session (created if absent)."""
from backend.db.memory import InMemoryRepository
from backend.api.service import MaterialsApi
from backend.async_jobs.queue import InMemoryQueue
from backend.testing.fakes import FakeS3
from backend.models import IngestRequest


def _repo():
    r = InMemoryRepository()
    r.set_tenant("org_a")
    return r


def _api(repo):
    return MaterialsApi(repo, FakeS3(), InMemoryQueue(), lambda caller, course_id: True)


# ── repo-level: create-if-absent / reuse ──────────────────────────────────────

def test_get_or_create_session_creates_when_absent():
    repo = _repo()
    sid = repo.get_or_create_session("org_a", "course_1", None, None, "prof_1")
    assert sid and sid in repo._sessions
    assert repo._sessions[sid]["course_id"] == "course_1"


def test_get_or_create_session_reuses_existing():
    repo = _repo()
    first = repo.get_or_create_session("org_a", "course_1", None, "2026-08-27", "prof_1")
    again = repo.get_or_create_session("org_a", "course_1", first, None, "prof_1")
    assert again == first
    assert len(repo._sessions) == 1


def test_get_or_create_session_ignores_a_foreign_courses_session():
    repo = _repo()
    other = repo.get_or_create_session("org_a", "course_OTHER", None, None, "prof_1")
    sid = repo.get_or_create_session("org_a", "course_1", other, None, "prof_1")
    assert sid != other
    assert repo._sessions[sid]["course_id"] == "course_1"


# ── end-to-end through the upload service ─────────────────────────────────────

def test_upload_creates_a_session_and_maps_the_material_to_it():
    repo = _repo()
    req = IngestRequest(org_name="org_a", course_name="Ops", file_name="l1.md",
                        mime_type="text/markdown", bytes=1024)
    resp = _api(repo).presign_by_name("prof_1", "professor", "org_a", req)
    assert resp.session_id                    # a session was instantiated
    assert resp.session_id in repo._sessions
    mat = repo.get_material(resp.material_id)
    assert mat.session_id == resp.session_id  # the material is mapped to it


def test_syllabus_upload_does_not_create_a_session():
    repo = _repo()
    req = IngestRequest(org_name="org_a", course_name="Ops", file_name="syllabus.pdf",
                        mime_type="application/pdf", bytes=2048, is_syllabus=True)
    resp = _api(repo).presign_by_name("prof_1", "professor", "org_a", req)
    assert not resp.session_id                # the syllabus is course-level (no session)
    assert len(repo._sessions) == 0           # no stray class session
    mat = repo.get_material(resp.material_id)
    assert not mat.session_id


def test_upload_reuses_a_given_session_without_creating_another():
    repo = _repo()
    course = repo.get_or_create_course("org_a", "Ops", "prof_1")
    sid = repo.get_or_create_session("org_a", course.course_id, None, None, "prof_1")
    req = IngestRequest(org_name="org_a", course_name="Ops", file_name="l1.md",
                        mime_type="text/markdown", bytes=1024, session_id=sid)
    resp = _api(repo).presign_by_name("prof_1", "professor", "org_a", req)
    assert resp.session_id == sid
    assert len(repo._sessions) == 1
