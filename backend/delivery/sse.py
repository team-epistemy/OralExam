"""M6 SSE (Server-Sent Events) helpers for real-time exam delivery.

Provides utilities for pushing questions and session updates to the
browser via SSE streams. Used by the HTTP layer to maintain a live
connection during exam sessions.
"""
from __future__ import annotations
import json
import asyncio
import logging
from typing import AsyncGenerator, Optional, Dict, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class SSEEvent:
    """A single Server-Sent Event."""

    def __init__(
        self,
        event: str,
        data: Dict[str, Any],
        event_id: Optional[str] = None,
        retry: Optional[int] = None,
    ):
        self.event = event
        self.data = data
        self.event_id = event_id
        self.retry = retry

    def encode(self) -> str:
        """Encode the event as an SSE-formatted string."""
        lines = []
        if self.event_id:
            lines.append(f"id: {self.event_id}")
        if self.retry is not None:
            lines.append(f"retry: {self.retry}")
        lines.append(f"event: {self.event}")
        lines.append(f"data: {json.dumps(self.data)}")
        lines.append("")  # Trailing newline to terminate the event
        return "\n".join(lines) + "\n"


# ── Event types ─────────────────────────────────────────────────────────────

def question_delivered_event(
    question_id: str,
    text: str,
    question_type: str,
    turn_index: int,
    total_questions: int,
    time_remaining_seconds: Optional[int] = None,
) -> SSEEvent:
    """Create an SSE event for delivering a question to the student."""
    return SSEEvent(
        event="question",
        data={
            "question_id": question_id,
            "text": text,
            "question_type": question_type,
            "turn_index": turn_index,
            "total_questions": total_questions,
            "time_remaining_seconds": time_remaining_seconds,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


def answer_acknowledged_event(
    turn_id: str,
    turn_index: int,
    next_available: bool,
) -> SSEEvent:
    """Create an SSE event acknowledging answer submission."""
    return SSEEvent(
        event="answer_ack",
        data={
            "turn_id": turn_id,
            "turn_index": turn_index,
            "next_available": next_available,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


def session_completed_event(
    session_id: str,
    total_turns: int,
) -> SSEEvent:
    """Create an SSE event when the exam session is completed."""
    return SSEEvent(
        event="session_complete",
        data={
            "session_id": session_id,
            "total_turns": total_turns,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


def session_error_event(message: str) -> SSEEvent:
    """Create an SSE event for session errors."""
    return SSEEvent(
        event="error",
        data={
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


def heartbeat_event() -> SSEEvent:
    """Create a keepalive heartbeat event."""
    return SSEEvent(
        event="heartbeat",
        data={"timestamp": datetime.now(timezone.utc).isoformat()},
    )


# ── Stream generator ────────────────────────────────────────────────────────

class SessionEventStream:
    """Manages an SSE event stream for a single exam session.

    Use as an async generator in a FastAPI/Starlette StreamingResponse:

        stream = SessionEventStream(session_manager, session_id)
        return StreamingResponse(
            stream.generate(),
            media_type="text/event-stream",
        )
    """

    def __init__(
        self,
        session_manager,
        session_id: str,
        heartbeat_interval: int = 30,
    ):
        self.session_manager = session_manager
        self.session_id = session_id
        self.heartbeat_interval = heartbeat_interval
        self._closed = False
        self._event_queue: asyncio.Queue = asyncio.Queue()

    async def push_event(self, event: SSEEvent) -> None:
        """Push an event to the stream."""
        if not self._closed:
            await self._event_queue.put(event)

    async def close(self) -> None:
        """Signal the stream to close."""
        self._closed = True
        await self._event_queue.put(None)  # Sentinel to unblock generate()

    async def generate(self) -> AsyncGenerator[str, None]:
        """Async generator yielding SSE-formatted strings.

        Sends heartbeats at the configured interval to keep the
        connection alive through proxies and load balancers.
        """
        # Send initial retry directive
        yield f"retry: 5000\n\n"

        while not self._closed:
            try:
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=self.heartbeat_interval,
                )
                if event is None:
                    break
                yield event.encode()
            except asyncio.TimeoutError:
                # Send heartbeat to keep connection alive
                yield heartbeat_event().encode()

        # Final event
        yield session_completed_event(self.session_id, 0).encode()
