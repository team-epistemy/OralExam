"""Intra-org isolation: a course is owned by the professor who created it, and
another professor in the same org can neither open nor upload to it."""
import pytest

from backend.app import factory
from backend.db.memory import InMemoryRepository
from backend.testing.fakes import FakeS3
from backend.async_jobs.queue import InMemoryQueue
from backend.models import Caller, Role, PresignRequest
from backend.api.service import AuthorizationError


def _env():
    repo = InMemoryRepository()
    api = factory.build_api(None, repo, FakeS3(), InMemoryQueue())
    return repo, api


PROF_A = Caller(user_id="prof_a@x.edu", org_id="org1", role=Role.PROFESSOR)
PROF_B = Caller(user_id="prof_b@x.edu", org_id="org1", role=Role.PROFESSOR)
REQ = PresignRequest(file_name="l.md", mime_type="text/markdown", bytes=10)


def test_creation_records_owner():
    repo, _ = _env()
    repo.set_tenant("org1")
    c = repo.get_or_create_course("org1", "CS101", PROF_A.user_id)
    assert c.created_by == PROF_A.user_id
    # resolving the same name returns the existing course, owner unchanged
    again = repo.get_or_create_course("org1", "CS101", PROF_B.user_id)
    assert again.course_id == c.course_id and again.created_by == PROF_A.user_id


def test_owner_can_upload_other_professor_cannot():
    repo, api = _env()
    repo.set_tenant("org1")
    c = repo.get_or_create_course("org1", "CS101", PROF_A.user_id)
    api.presign(PROF_A, c.course_id, REQ)  # owner: allowed
    with pytest.raises(AuthorizationError):
        api.presign(PROF_B, c.course_id, REQ)  # same org, not owner: denied
