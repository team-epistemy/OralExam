"""T4-lite + T7: end-to-end markdown slice and mid-pipeline failure safety."""
import pytest

from epistemy_m3.models import (
    Caller, Role, PresignRequest, VersionStatus, JobStatus,
)
from epistemy_m3.api.service import MaterialsApi
from epistemy_m3.async_jobs.pipeline import IngestPipeline
from epistemy_m3.async_jobs.worker import IngestWorker
from epistemy_m3.embedding.fake import FakeEmbedder
from epistemy_m3.db.memory import InMemoryRepository
from epistemy_m3.async_jobs.queue import InMemoryQueue
from epistemy_m3.testing.fakes import FakeS3

MD = b"# ML\n\nintro\n\n## Gradient Descent\n\nfollows the negative gradient.\n"


def _wire():
    """Build a fully in-memory API + worker sharing one repo and queue."""
    repo, storage, queue = InMemoryRepository(), FakeS3(), InMemoryQueue()
    api = MaterialsApi(repo, storage, queue, lambda c, cid: True)
    pipeline = IngestPipeline(repo, storage, FakeEmbedder(dims=1024))
    worker = IngestWorker(repo, queue, pipeline)
    return repo, storage, queue, api, worker


def _prof():
    return Caller(user_id="p1", org_id="org_a", role=Role.PROFESSOR)


def test_markdown_reaches_ready_and_flips_current(monkeypatch):
    repo, storage, queue, api, worker = _wire()
    req = PresignRequest(file_name="l1.md", mime_type="text/markdown", bytes=len(MD))
    resp = api.presign(_prof(), "course_cs101", req)
    storage.put(resp.s3_key, MD)
    api.register(_prof(), resp.material_version_id)
    worker.handle(queue.receive())
    version = repo.get_version(resp.material_version_id)
    material = repo.get_material(resp.material_id)
    assert version.status == VersionStatus.READY
    assert material.current_version_id == version.material_version_id
    assert repo.count_chunks(version.material_version_id) > 0


def test_chunks_carry_heading_path():
    repo, storage, queue, api, worker = _wire()
    req = PresignRequest(file_name="l1.md", mime_type="text/markdown", bytes=len(MD))
    resp = api.presign(_prof(), "course_cs101", req)
    storage.put(resp.s3_key, MD)
    api.register(_prof(), resp.material_version_id)
    worker.handle(queue.receive())
    repo.set_tenant("org_a")
    chunks = [c for c in repo._chunks.values()]
    assert any(c.position.heading_path for c in chunks)


def test_midpipeline_failure_leaves_previous_version_intact():
    repo, storage, queue, api, worker = _wire()
    req = PresignRequest(file_name="l1.md", mime_type="text/markdown", bytes=len(MD))
    resp = api.presign(_prof(), "course_cs101", req)
    storage.put(resp.s3_key, MD)
    api.register(_prof(), resp.material_version_id)
    msg = queue.receive()
    # Object vanishes after register → pipeline download fails mid-run.
    storage.objects.pop(resp.s3_key, None)
    worker.handle(msg)
    version = repo.get_version(resp.material_version_id)
    material = repo.get_material(resp.material_id)
    assert version.status == VersionStatus.FAILED
    assert material.current_version_id is None
    assert repo.get_job(msg.job_id).status == JobStatus.FAILED
