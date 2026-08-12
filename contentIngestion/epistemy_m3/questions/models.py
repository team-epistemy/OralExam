"""M5 Pydantic schemas: question candidates, sets, and generation jobs."""
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

class QuestionType(str, Enum):
    FREE_TEXT = "free_text"
    ORAL = "oral"


class QuestionStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"


class DifficultyLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GenerationJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# ── Domain models ───────────────────────────────────────────────────────────

class QuestionDifficulty(BaseModel):
    """EDS-derived difficulty metadata attached to a question."""

    level: DifficultyLevel = DifficultyLevel.MEDIUM
    eds_score: Optional[float] = None
    avg_hop_depth: Optional[float] = None
    concept_count: int = 1
    reasoning: Optional[str] = None


class QuestionCandidate(BaseModel):
    """A generated question, initially in draft status pending professor review."""

    question_id: str = Field(default_factory=_uuid)
    course_id: str
    org_id: str
    concept_ids: List[str] = Field(default_factory=list)
    text: str
    question_type: QuestionType = QuestionType.FREE_TEXT
    difficulty: QuestionDifficulty = Field(default_factory=QuestionDifficulty)
    status: QuestionStatus = QuestionStatus.DRAFT
    created_by: str = "system"
    source_chunks: List[str] = Field(default_factory=list)
    generation_job_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class QuestionSet(BaseModel):
    """A named grouping of questions for use in an assignment."""

    question_set_id: str = Field(default_factory=_uuid)
    course_id: str
    org_id: str
    title: str
    question_ids: List[str] = Field(default_factory=list)
    created_by: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class GenerationJob(BaseModel):
    """Tracks a batch question generation request."""

    job_id: str = Field(default_factory=_uuid)
    org_id: str
    course_id: str
    concept_ids: List[str] = Field(default_factory=list)
    requested_count: int = 5
    status: GenerationJobStatus = GenerationJobStatus.QUEUED
    generated_count: int = 0
    question_ids: List[str] = Field(default_factory=list)
    error: Optional[Dict[str, Any]] = None
    created_by: str = "system"
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ── Tool I/O models ─────────────────────────────────────────────────────────

class GenerationRequest(BaseModel):
    """Input for the generate_questions tool."""

    course_id: str
    concept_ids: Optional[List[str]] = None
    count: int = 5
    question_type: QuestionType = QuestionType.FREE_TEXT


class GenerationResult(BaseModel):
    """Output from generate_questions: the job id and any immediate candidates."""

    job_id: str
    status: GenerationJobStatus
    generated_count: int = 0
    questions: List[QuestionCandidate] = Field(default_factory=list)
