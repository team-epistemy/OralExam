"""Conformance (web-free): uploading a material creates NO class session.

Part of the simplified model — Professor -> Course -> Material (identified by
topics) -> Assignment (by topics). Uses only web-free modules so it runs in the
lean CI env (no FastAPI). The syllabus-gate + exam-request-shape conformance
lives in test_no_session_http_conformance.py (guarded on fastapi).
"""
from backend.api.service import MaterialsApi
from backend.db.memory import InMemoryRepository
from backend.async_jobs.queue import InMemoryQueue
from backend.testing.fakes import FakeS3
from backend.models import IngestRequest


def _api():
    repo = InMemoryRepository()
    repo.set_tenant("org_a")
    return repo, MaterialsApi(repo, FakeS3(), InMemoryQueue(), lambda caller, cid: True)


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
