"""SQS queue wrapper and an in-memory queue for offline runs."""
from __future__ import annotations
import json
from collections import deque
from typing import Protocol, Optional

from backend.models import IngestMessage


class Queue(Protocol):
    """Minimal enqueue/receive/ack surface used by the worker."""

    def send(self, message: IngestMessage) -> None: ...
    def receive(self) -> Optional[IngestMessage]: ...
    def ack(self, message: IngestMessage) -> None: ...


class InMemoryQueue:
    """FIFO queue for tests; ack is a no-op since nothing redelivers."""

    def __init__(self) -> None:
        self._items: deque = deque()

    def send(self, message: IngestMessage) -> None:
        self._items.append(message)

    def receive(self) -> Optional[IngestMessage]:
        return self._items.popleft() if self._items else None

    def ack(self, message: IngestMessage) -> None:
        return None


class SqsQueue:
    """Real SQS-backed queue with 20s long polling."""

    def __init__(self, client, queue_url: str):
        self.client = client
        self.queue_url = queue_url
        self._receipts: dict = {}

    def send(self, message: IngestMessage) -> None:
        self.client.send_message(QueueUrl=self.queue_url,
                                 MessageBody=message.model_dump_json())

    def receive(self) -> Optional[IngestMessage]:
        """Long-poll for one message; cache its receipt for ack."""
        resp = self.client.receive_message(
            QueueUrl=self.queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=20)
        records = resp.get("Messages", [])
        if not records:
            return None
        return self._decode(records[0])

    def _decode(self, record: dict) -> IngestMessage:
        """Parse the body and remember the receipt handle for ack."""
        msg = IngestMessage(**json.loads(record["Body"]))
        self._receipts[msg.job_id] = record["ReceiptHandle"]
        return msg

    def ack(self, message: IngestMessage) -> None:
        """Delete the message so SQS does not redeliver it."""
        handle = self._receipts.pop(message.job_id, None)
        if handle:
            self.client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=handle)
