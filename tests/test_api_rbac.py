"""T3: RBAC, server-owned keys, version increment, register enqueues a job."""
import pytest

from backend.models import PresignRequest, VersionStatus
from backend.api.service import AuthorizationError


def test_student_presign_denied(env, student, presign_md):
    _, _, _, api = env
    with pytest.raises(AuthorizationError):
        api.presign(student, "course_cs101", presign_md)


def test_professor_other_course_denied(env, professor, presign_md):
    _, _, _, api = env
    with pytest.raises(AuthorizationError):
        api.presign(professor, "course_other", presign_md)


def test_presign_builds_server_owned_key(env, professor, presign_md):
    _, _, _, api = env
    resp = api.presign(professor, "course_cs101", presign_md)
    assert resp.s3_key.startswith("org_a/course_cs101/materials/")
    assert resp.s3_key.endswith("/v1/lecture-1.md")


def test_replacement_upload_increments_version(env, professor, presign_md):
    _, _, _, api = env
    first = api.presign(professor, "course_cs101", presign_md)
    again = PresignRequest(file_name="lecture-1.md", mime_type="text/markdown",
                           bytes=2048, material_id=first.material_id)
    second = api.presign(professor, "course_cs101", again)
    assert second.version_no == 2
    assert "/v2/" in second.s3_key


def test_register_requires_uploaded_object(env, professor, presign_md):
    repo, storage, _, api = env
    resp = api.presign(professor, "course_cs101", presign_md)
    with pytest.raises(AuthorizationError):
        api.register(professor, resp.material_version_id)
    storage.put(resp.s3_key, b"# T\n\nbody\n")
    job = api.register(professor, resp.material_version_id)
    version = repo.get_version(resp.material_version_id)
    assert version.status == VersionStatus.UPLOADED
    assert job.course_id == "course_cs101"
