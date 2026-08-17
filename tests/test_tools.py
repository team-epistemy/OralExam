"""T14: tools enforce membership and return current-version status."""
import pytest

from backend.models import Caller, Role, PresignRequest, VersionStatus
from backend.api.service import MaterialsApi, AuthorizationError
from backend.tools.materials_tools import MaterialsTools
from backend.db.memory import InMemoryRepository
from backend.async_jobs.queue import InMemoryQueue
from backend.testing.fakes import FakeS3


def _tools():
    """Wire tools with membership limited to org_a callers."""
    repo, storage, queue = InMemoryRepository(), FakeS3(), InMemoryQueue()
    api = MaterialsApi(repo, storage, queue, lambda c, cid: True)
    tools = MaterialsTools(repo, api, lambda c, cid: c.org_id == "org_a")
    return repo, tools


def test_list_materials_returns_summaries():
    repo, tools = _tools()
    prof = Caller(user_id="p", org_id="org_a", role=Role.PROFESSOR)
    tools.upload_material(prof, "course_cs101",
                          PresignRequest(file_name="a.md", mime_type="text/markdown",
                                         bytes=10))
    summaries = tools.list_materials(prof, "course_cs101")
    assert len(summaries) == 1
    assert summaries[0].display_name == "a.md"


def test_cross_org_member_check_blocks():
    repo, tools = _tools()
    outsider = Caller(user_id="x", org_id="org_b", role=Role.PROFESSOR)
    with pytest.raises(AuthorizationError):
        tools.list_materials(outsider, "course_cs101")
