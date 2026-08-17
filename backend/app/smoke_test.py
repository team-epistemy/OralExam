"""In-VPC end-to-end smoke test against real S3, SQS, Aurora, and the worker."""
from __future__ import annotations
import time
import uuid

from backend.config import Settings
from backend.models import (
    Material, MaterialVersion, AsyncJob, IngestMessage, SourceType, VersionStatus,
)
from backend.app import factory

_MD = b"# Smoke\n\nintro\n\n## Topic\n\nbody about a topic.\n"
_ORG_NAME = "smoke-org"
_COURSE_NAME = "smoke-course"


def run(settings: Settings) -> None:
    """Seed a version, upload to S3, enqueue, then poll Aurora to ready."""
    repo = factory.build_repo(settings)
    storage = factory.build_storage(settings)
    queue = factory.build_queue(settings)
    version = _seed(repo, storage, settings)
    _enqueue(repo, queue, version)
    _await_ready(repo, version)


def _seed(repo, storage, settings: Settings) -> MaterialVersion:
    """Resolve org/course names, create material + version, upload to S3."""
    org = repo.get_or_create_org(_ORG_NAME)
    course = repo.get_or_create_course(org.org_id, _COURSE_NAME)
    repo.set_tenant(org.org_id)
    material = repo.create_material(Material(
        course_id=course.course_id, org_id=org.org_id,
        created_by=org.org_id, display_name="smoke.md"))
    version = _make_version(repo, material, settings)
    _upload(storage, settings, version)
    print(f"seeded version {version.material_version_id} key={version.s3_key}")
    return version


def _make_version(repo, material, settings: Settings) -> MaterialVersion:
    """Insert a pending material_version with a tenant-scoped S3 key."""
    vid = str(uuid.uuid4())
    key = (f"{material.org_id}/{material.course_id}/materials/"
           f"{material.material_id}/v1/smoke.md")
    return repo.create_version(MaterialVersion(
        material_version_id=vid, material_id=material.material_id,
        course_id=material.course_id, org_id=material.org_id, version_no=1,
        uploaded_by=material.org_id, source_type=SourceType.MARKDOWN,
        mime_type="text/markdown", file_name="smoke.md", s3_key=key,
        bytes=len(_MD)))


def _upload(storage, settings: Settings, version: MaterialVersion) -> None:
    """Put the test bytes directly via the boto3 S3 client (SSE-KMS)."""
    storage.client.put_object(
        Bucket=settings.bucket, Key=version.s3_key, Body=_MD,
        ServerSideEncryption="aws:kms", SSEKMSKeyId=settings.kms_alias)


def _enqueue(repo, queue, version: MaterialVersion) -> None:
    """Create the async_job and send the ingest message to SQS."""
    repo.set_tenant(version.org_id)
    job = repo.create_job(AsyncJob(org_id=version.org_id,
                                   course_id=version.course_id,
                                   created_by=version.org_id))
    repo.update_version_status(version.material_version_id, VersionStatus.UPLOADED)
    queue.send(IngestMessage(
        job_id=job.job_id, material_version_id=version.material_version_id,
        org_id=version.org_id, course_id=version.course_id,
        source_type=SourceType.MARKDOWN, s3_key=version.s3_key))
    print(f"enqueued job {job.job_id}")


def _await_ready(repo, version: MaterialVersion) -> None:
    """Poll the version status, printing transitions until terminal."""
    for _ in range(30):
        repo.set_tenant(version.org_id)
        current = repo.get_version(version.material_version_id)
        print(f"  status={current.status.value} "
              f"chunks={repo.count_chunks(version.material_version_id)}")
        if current.status in (VersionStatus.READY, VersionStatus.FAILED):
            return _report(repo, current)
        time.sleep(5)
    raise RuntimeError("smoke test timed out waiting for worker")


def _report(repo, version) -> None:
    """Print the final outcome and the material's current-version flip."""
    material = repo.get_material(version.material_id)
    flipped = material.current_version_id == version.material_version_id
    print(f"\nSMOKE RESULT: {version.status.value} "
          f"current_version_flipped={flipped} "
          f"chunks={repo.count_chunks(version.material_version_id)}")
