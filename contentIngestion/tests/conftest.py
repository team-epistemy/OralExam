"""Shared fixtures: callers, course membership, and a wired MaterialsApi."""
import pytest

from epistemy_m3.models import Caller, Role, PresignRequest
from epistemy_m3.api.service import MaterialsApi
from epistemy_m3.async_jobs.queue import InMemoryQueue
from epistemy_m3.db.memory import InMemoryRepository
from epistemy_m3.testing.fakes import FakeS3


@pytest.fixture
def professor():
    return Caller(user_id="prof_1", org_id="org_a", role=Role.PROFESSOR)


@pytest.fixture
def student():
    return Caller(user_id="stud_1", org_id="org_a", role=Role.STUDENT)


@pytest.fixture
def env():
    """Repo, storage, queue, and an API authorizing course_cs101 only."""
    repo = InMemoryRepository()
    storage = FakeS3()
    queue = InMemoryQueue()
    authorize = lambda caller, course_id: course_id == "course_cs101"
    api = MaterialsApi(repo, storage, queue, authorize)
    return repo, storage, queue, api


@pytest.fixture
def presign_md():
    return PresignRequest(file_name="lecture-1.md", mime_type="text/markdown",
                          bytes=2048)
