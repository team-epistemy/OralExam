"""M5 async worker handler for batch question generation jobs."""
from __future__ import annotations
import json
import logging
import signal
from collections import deque
from typing import Protocol, Optional

from epistemy_m3.questions.models import (
    GenerationJob, GenerationJobStatus, QuestionType,
)
from epistemy_m3.questions.generator import QuestionGenerator

logger = logging.getLogger(__name__)


# ── Queue protocol and implementations ──────────────────────────────────────

class GenerationMessage:
    """SQS payload for a batch generation job."""

    def __init__(self, job_id: str, org_id: str, course_id: str,
                 concept_ids: list, count: int = 5,
                 question_type: str = "free_text", created_by: str = "system"):
        self.job_id = job_id
        self.org_id = org_id
        self.course_id = course_id
        self.concept_ids = concept_ids
        self.count = count
        self.question_type = question_type
        self.created_by = created_by

    def to_json(self) -> str:
        return json.dumps({
            "job_id": self.job_id,
            "org_id": self.org_id,
            "course_id": self.course_id,
            "concept_ids": self.concept_ids,
            "count": self.count,
            "question_type": self.question_type,
            "created_by": self.created_by,
        })

    @classmethod
    def from_json(cls, data: str) -> "GenerationMessage":
        d = json.loads(data)
        return cls(**d)


class GenerationQueue(Protocol):
    """Minimal queue surface for generation jobs."""

    def send(self, message: GenerationMessage) -> None: ...
    def receive(self) -> Optional[GenerationMessage]: ...
    def ack(self, message: GenerationMessage) -> None: ...


class InMemoryGenerationQueue:
    """FIFO queue for tests; ack is a no-op."""

    def __init__(self) -> None:
        self._items: deque = deque()

    def send(self, message: GenerationMessage) -> None:
        self._items.append(message)

    def receive(self) -> Optional[GenerationMessage]:
        return self._items.popleft() if self._items else None

    def ack(self, message: GenerationMessage) -> None:
        return None


class SqsGenerationQueue:
    """Real SQS-backed queue for generation jobs."""

    def __init__(self, client, queue_url: str):
        self.client = client
        self.queue_url = queue_url
        self._receipts: dict = {}

    def send(self, message: GenerationMessage) -> None:
        self.client.send_message(
            QueueUrl=self.queue_url, MessageBody=message.to_json()
        )

    def receive(self) -> Optional[GenerationMessage]:
        resp = self.client.receive_message(
            QueueUrl=self.queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=20
        )
        records = resp.get("Messages", [])
        if not records:
            return None
        record = records[0]
        msg = GenerationMessage.from_json(record["Body"])
        self._receipts[msg.job_id] = record["ReceiptHandle"]
        return msg

    def ack(self, message: GenerationMessage) -> None:
        handle = self._receipts.pop(message.job_id, None)
        if handle:
            self.client.delete_message(
                QueueUrl=self.queue_url, ReceiptHandle=handle
            )


# ── Worker ──────────────────────────────────────────────────────────────────

class GenerationWorker:
    """Consumes generation messages and runs the question generator."""

    def __init__(self, repo, queue: GenerationQueue, generator: QuestionGenerator):
        self.repo = repo
        self.queue = queue
        self.generator = generator
        self._running = True

    def install_signals(self) -> None:
        """SIGTERM lets the current message finish, then the loop exits."""
        signal.signal(signal.SIGTERM, self._stop)

    def _stop(self, *_args) -> None:
        self._running = False

    def run_forever(self) -> None:
        """Drain the queue until stopped."""
        while self._running:
            msg = self.queue.receive()
            if msg:
                self.handle(msg)

    def handle(self, msg: GenerationMessage) -> None:
        """Run the generation pipeline; record failure and always ack."""
        try:
            self._process(msg)
        except Exception as exc:
            self._mark_failed(msg, exc)
        finally:
            self.queue.ack(msg)

    def _process(self, msg: GenerationMessage) -> None:
        """Execute question generation and persist results."""
        self.repo.set_tenant(msg.org_id)

        # Update job status to running
        self.repo.update_generation_job(
            msg.job_id, status=GenerationJobStatus.RUNNING
        )

        # Generate questions
        question_type = QuestionType(msg.question_type)
        candidates = self.generator.generate(
            course_id=msg.course_id,
            org_id=msg.org_id,
            concept_ids=msg.concept_ids or None,
            count=msg.count,
            question_type=question_type,
            created_by=msg.created_by,
        )

        # Persist generated questions
        question_ids = []
        for q in candidates:
            q.generation_job_id = msg.job_id
            self.repo.create_question(q)
            question_ids.append(q.question_id)

        # Mark job succeeded
        self.repo.update_generation_job(
            msg.job_id,
            status=GenerationJobStatus.SUCCEEDED,
            generated_count=len(candidates),
            question_ids=question_ids,
        )

        logger.info(
            "Generation job %s completed: %d questions",
            msg.job_id, len(candidates),
        )

    def _mark_failed(self, msg: GenerationMessage, exc: Exception) -> None:
        """Write the error to the generation_job record."""
        self.repo.set_tenant(msg.org_id)
        err = {"type": type(exc).__name__, "message": str(exc)}
        self.repo.update_generation_job(
            msg.job_id, status=GenerationJobStatus.FAILED, error=err
        )
        logger.error("Generation job %s failed: %s", msg.job_id, exc)
