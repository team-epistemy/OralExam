"""M6 Pydantic schemas: assignments, exam sessions, and session turns."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Enums ───────────────────────────────────────────────────────────────────

class AssignmentStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    CLOSED = "closed"


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


# ── Domain models ───────────────────────────────────────────────────────────

class AssignmentConfig(BaseModel):
    """Configuration for how the assignment is delivered."""

    adaptive: bool = True
    time_window_start: Optional[datetime] = None
    time_window_end: Optional[datetime] = None
    max_questions: int = 10
    time_limit_minutes: Optional[int] = None
    shuffle_questions: bool = False
    allow_resume: bool = True


class Assignment(BaseModel):
    """An exam/assignment definition linking a question set to delivery config."""

    assignment_id: str = Field(default_factory=_uuid)
    course_id: str
    org_id: str
    title: str
    question_set_id: str
    config: AssignmentConfig = Field(default_factory=AssignmentConfig)
    status: AssignmentStatus = AssignmentStatus.DRAFT
    created_by: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ExamSession(BaseModel):
    """A student's active exam session for an assignment."""

    session_id: str = Field(default_factory=_uuid)
    assignment_id: str
    student_id: str
    org_id: str
    course_id: str
    status: SessionStatus = SessionStatus.ACTIVE
    started_at: datetime = Field(default_factory=_now)
    completed_at: Optional[datetime] = None
    current_turn_index: int = 0
    questions_delivered: List[str] = Field(default_factory=list)
    concepts_covered: List[str] = Field(default_factory=list)


class SessionTurn(BaseModel):
    """A single question-answer turn within an exam session."""

    turn_id: str = Field(default_factory=_uuid)
    session_id: str
    turn_index: int
    question_id: str
    student_answer: Optional[str] = None
    answered_at: Optional[datetime] = None
    time_spent_seconds: Optional[int] = None


# ── Tool I/O models ─────────────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    """Input for starting a new exam session."""

    assignment_id: str
    student_id: str


class SubmitAnswerRequest(BaseModel):
    """Input for submitting an answer to the current question."""

    session_id: str
    answer_text: str
    time_spent_seconds: Optional[int] = None


class SessionState(BaseModel):
    """Current state of a session, returned to the client."""

    session_id: str
    assignment_id: str
    status: SessionStatus
    current_turn_index: int
    total_questions: int
    current_question: Optional[Dict[str, Any]] = None
    time_remaining_seconds: Optional[int] = None
