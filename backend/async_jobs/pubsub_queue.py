"""Pub/Sub-backed ingest queue mirroring the SqsQueue surface (send/receive/ack).

The HTTP app only sends (enqueue on upload); the worker pulls + acks. Message
body is the same IngestMessage JSON as the SQS path, so the pipeline is unchanged.
"""
from __future__ import annotations

import json
from typing import Optional

from backend.models import IngestMessage


class PubSubQueue:
    """Real Pub/Sub-backed queue. Pull-based receive with per-job ack ids."""

    def __init__(self, project: str, topic: str, subscription: str):
        from google.cloud import pubsub_v1
        self._publisher = pubsub_v1.PublisherClient()
        self._subscriber = pubsub_v1.SubscriberClient()
        self._topic_path = self._publisher.topic_path(project, topic)
        self._sub_path = self._subscriber.subscription_path(project, subscription)
        self._ack_ids: dict = {}

    def send(self, message: IngestMessage) -> None:
        future = self._publisher.publish(self._topic_path, message.model_dump_json().encode())
        future.result(timeout=30)

    def receive(self) -> Optional[IngestMessage]:
        """Pull one message (20s wait); cache its ack id for ack()."""
        resp = self._subscriber.pull(subscription=self._sub_path, max_messages=1, timeout=20)
        if not resp.received_messages:
            return None
        rec = resp.received_messages[0]
        msg = IngestMessage(**json.loads(rec.message.data.decode()))
        self._ack_ids[msg.job_id] = rec.ack_id
        return msg

    def ack(self, message: IngestMessage) -> None:
        ack_id = self._ack_ids.pop(message.job_id, None)
        if ack_id:
            self._subscriber.acknowledge(subscription=self._sub_path, ack_ids=[ack_id])
