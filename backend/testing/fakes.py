"""In-memory fakes that let the full pipeline run with zero AWS calls."""
from __future__ import annotations
from typing import Dict

from backend.db.memory import InMemoryRepository
from backend.async_jobs.queue import InMemoryQueue
from backend.async_jobs.pipeline import IngestPipeline
from backend.async_jobs.worker import IngestWorker
from backend.embedding.fake import FakeEmbedder


class FakeS3:
    """Dict-backed object store with presign returning a sentinel URL."""

    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}

    def presign_put(self, key: str, mime_type: str, max_bytes: int) -> str:
        return f"https://fake-s3.local/{key}"

    def put(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def object_exists(self, key: str) -> bool:
        return key in self.objects

    def get_bytes(self, key: str) -> bytes:
        return self.objects[key]


def build_offline_worker():
    """Assemble repo, queue, fake S3, fake embedder, pipeline, and worker."""
    repo = InMemoryRepository()
    queue = InMemoryQueue()
    storage = FakeS3()
    pipeline = IngestPipeline(repo, storage, FakeEmbedder(dims=1024))
    worker = IngestWorker(repo, queue, pipeline)
    return repo, queue, storage, worker
