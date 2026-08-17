"""Worker loop (T4-lite): long-poll SQS, dispatch, fail-safe on exceptions."""
from __future__ import annotations
import signal
from typing import Optional

from backend.models import IngestMessage, VersionStatus, JobStatus
from backend.db.repository import Repository
from backend.async_jobs.queue import Queue
from backend.async_jobs.pipeline import IngestPipeline


class IngestWorker:
    """Consumes ingest messages and runs the pipeline with failure handling."""

    def __init__(self, repo: Repository, queue: Queue, pipeline: IngestPipeline):
        self.repo = repo
        self.queue = queue
        self.pipeline = pipeline
        self._running = True

    def install_signals(self) -> None:
        """SIGTERM lets the current message finish, then the loop exits."""
        signal.signal(signal.SIGTERM, self._stop)

    def _stop(self, *_args) -> None:
        self._running = False

    def run_forever(self) -> None:
        """Drain the queue until stopped, handling one message at a time."""
        while self._running:
            msg = self.queue.receive()
            if msg:
                self.handle(msg)

    def handle(self, msg: IngestMessage) -> None:
        """Run the pipeline; record failure and always ack the message."""
        try:
            self.pipeline.run(msg)
        except Exception as exc:
            self._mark_failed(msg, exc)
        finally:
            self.queue.ack(msg)

    def _mark_failed(self, msg: IngestMessage, exc: Exception) -> None:
        """Write the error to async_job and material_version, mark failed."""
        self.repo.set_tenant(msg.org_id)
        err = {"type": type(exc).__name__, "message": str(exc)}
        self.repo.update_version_status(msg.material_version_id,
                                        VersionStatus.FAILED, error=err)
        self.repo.update_job(msg.job_id, status=JobStatus.FAILED, error=err)
