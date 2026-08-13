"""org_name/course_name resolution: server-minted UUIDs, per-org uniqueness."""
import pytest

from backend.models import IngestRequest
from backend.api.service import MaterialsApi
from backend.db.memory import InMemoryRepository
from backend.async_jobs.queue import InMemoryQueue
from backend.testing.fakes import FakeS3


def _api():
    repo, storage, queue = InMemoryRepository(), FakeS3(), InMemoryQueue()
    return repo, MaterialsApi(repo, storage, queue, lambda c, cid: True)


def _req(org, course):
    return IngestRequest(org_name=org, course_name=course, file_name="l.md",
                         mime_type="text/markdown", bytes=20)


def test_name_resolves_to_minted_uuid():
    repo, api = _api()
    resp = api.presign_by_name("op", "professor", _req("berkeley", "data101"))
    org = repo.get_or_create_org("berkeley")
    course = repo.get_or_create_course(org.org_id, "data101")
    assert resp.s3_key.startswith(f"{org.org_id}/{course.course_id}/materials/")


def test_same_course_name_different_orgs_are_distinct():
    repo, api = _api()
    api.presign_by_name("op", "professor", _req("berkeley", "data101"))
    api.presign_by_name("op", "professor", _req("stanford", "data101"))
    berkeley = repo.get_or_create_org("berkeley")
    stanford = repo.get_or_create_org("stanford")
    c_b = repo.get_or_create_course(berkeley.org_id, "data101")
    c_s = repo.get_or_create_course(stanford.org_id, "data101")
    assert berkeley.org_id != stanford.org_id
    assert c_b.course_id != c_s.course_id


def test_same_name_resolves_to_same_uuid_on_reuse():
    repo, _ = _api()
    first = repo.get_or_create_org("berkeley")
    again = repo.get_or_create_org("berkeley")
    assert first.org_id == again.org_id
