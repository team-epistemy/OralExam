"""M7 async worker handler for evaluation jobs (triggered per turn submission)."""
from __future__ import annotations
import json
import logging
import signal
from collections import deque
from typing import Protocol, Optional

from backend.evaluation.models import (
    Evaluation, EvaluationJobStatus, EDSBucket,
)
from backend.evaluation.claim_extractor import ClaimExtractor
from backend.evaluation.scorer import GraphScorer

logger = logging.getLogger(__name__)


# ── Message and queue ───────────────────────────────────────────────────────

class EvaluationMessage:
    """SQS payload for an evaluation job triggered by answer submission."""

    def __init__(
        self,
        turn_id: str,
        session_id: str,
        org_id: str,
        course_id: str,
        student_id: str,
        question_id: str,
        job_id: Optional[str] = None,
    ):
        self.turn_id = turn_id
        self.session_id = session_id
        self.org_id = org_id
        self.course_id = course_id
        self.student_id = student_id
        self.question_id = question_id
        self.job_id = job_id or turn_id  # Use turn_id as job key if not specified

    def to_json(self) -> str:
        return json.dumps({
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "org_id": self.org_id,
            "course_id": self.course_id,
            "student_id": self.student_id,
            "question_id": self.question_id,
            "job_id": self.job_id,
        })

    @classmethod
    def from_json(cls, data: str) -> "EvaluationMessage":
        d = json.loads(data)
        return cls(**d)


class EvaluationQueue(Protocol):
    """Minimal queue surface for evaluation jobs."""

    def send(self, message: EvaluationMessage) -> None: ...
    def receive(self) -> Optional[EvaluationMessage]: ...
    def ack(self, message: EvaluationMessage) -> None: ...


class InMemoryEvaluationQueue:
    """FIFO queue for tests; ack is a no-op."""

    def __init__(self) -> None:
        self._items: deque = deque()

    def send(self, message: EvaluationMessage) -> None:
        self._items.append(message)

    def receive(self) -> Optional[EvaluationMessage]:
        return self._items.popleft() if self._items else None

    def ack(self, message: EvaluationMessage) -> None:
        return None


class SqsEvaluationQueue:
    """Real SQS-backed queue for evaluation jobs."""

    def __init__(self, client, queue_url: str):
        self.client = client
        self.queue_url = queue_url
        self._receipts: dict = {}

    def send(self, message: EvaluationMessage) -> None:
        self.client.send_message(
            QueueUrl=self.queue_url, MessageBody=message.to_json()
        )

    def receive(self) -> Optional[EvaluationMessage]:
        resp = self.client.receive_message(
            QueueUrl=self.queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=20
        )
        records = resp.get("Messages", [])
        if not records:
            return None
        record = records[0]
        msg = EvaluationMessage.from_json(record["Body"])
        self._receipts[msg.job_id] = record["ReceiptHandle"]
        return msg

    def ack(self, message: EvaluationMessage) -> None:
        handle = self._receipts.pop(message.job_id, None)
        if handle:
            self.client.delete_message(
                QueueUrl=self.queue_url, ReceiptHandle=handle
            )


# ── Worker ──────────────────────────────────────────────────────────────────

class EvaluationWorker:
    """Consumes evaluation messages and runs claim extraction + scoring.

    Triggered per turn submission: when a student submits an answer,
    an evaluation message is enqueued. This worker picks it up,
    extracts claims, scores against the concept graph, and persists
    the evaluation result.
    """

    def __init__(
        self,
        repo,
        queue: EvaluationQueue,
        extractor: ClaimExtractor,
        scorer: GraphScorer,
    ):
        self.repo = repo
        self.queue = queue
        self.extractor = extractor
        self.scorer = scorer
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

    def handle(self, msg: EvaluationMessage) -> None:
        """Run evaluation; record failure and always ack."""
        try:
            self._process(msg)
        except Exception as exc:
            self._mark_failed(msg, exc)
        finally:
            self.queue.ack(msg)

    def _process(self, msg: EvaluationMessage) -> None:
        """Execute the full evaluation pipeline for a single turn."""
        self.repo.set_tenant(msg.org_id)

        # Check if already evaluated (idempotency)
        existing = self.repo.get_evaluation_by_turn(msg.turn_id)
        if existing:
            logger.info("Turn %s already evaluated, skipping", msg.turn_id)
            return

        # Load the turn and question
        turn = self.repo.get_turn_by_id(msg.turn_id)
        if not turn:
            raise EvaluationError(f"Turn {msg.turn_id} not found")
        if not turn.student_answer:
            logger.warning("Turn %s has no answer, skipping evaluation", msg.turn_id)
            return

        question = self.repo.get_question(msg.question_id)
        if not question:
            raise EvaluationError(f"Question {msg.question_id} not found")

        # Step 1: Extract claims
        logger.info("Extracting claims for turn %s", msg.turn_id)
        claims = self.extractor.extract(
            question_text=question.text,
            student_answer=turn.student_answer,
            concept_ids=question.concept_ids,
        )

        # Step 2: Score against concept graph
        logger.info(
            "Scoring turn %s: %d claims (%d causal)",
            msg.turn_id, claims.total_claims, claims.causal_claims,
        )
        eds_score, eds_bucket, coverage, component_scores = self.scorer.score(
            claims=claims,
            expected_concept_ids=question.concept_ids,
            question_text=question.text,
        )

        # Step 3: Persist evaluation
        evaluation = Evaluation(
            turn_id=msg.turn_id,
            org_id=msg.org_id,
            course_id=msg.course_id,
            student_id=msg.student_id,
            question_id=msg.question_id,
            claims=claims,
            concept_coverage=coverage,
            eds_score=eds_score,
            eds_bucket=eds_bucket,
            raw_llm_output=claims.raw_llm_output,
            evaluation_job_id=msg.job_id,
        )
        self.repo.create_evaluation(evaluation)

        logger.info(
            "Evaluation complete for turn %s: EDS=%.4f (%s)",
            msg.turn_id, eds_score, eds_bucket.value,
        )

    def _mark_failed(self, msg: EvaluationMessage, exc: Exception) -> None:
        """Log the evaluation failure. Job tracking is via async_job table."""
        err = {"type": type(exc).__name__, "message": str(exc)}
        logger.error(
            "Evaluation failed for turn %s: %s - %s",
            msg.turn_id, type(exc).__name__, str(exc),
        )
        # Update async_job if we have a job_id reference
        try:
            self.repo.set_tenant(msg.org_id)
            self.repo.update_job(
                msg.job_id,
                status="failed",
                error=err,
            )
        except Exception as update_err:
            logger.error("Failed to update job status: %s", update_err)


class EvaluationError(Exception):
    """Raised when evaluation cannot proceed due to missing data."""
    pass
