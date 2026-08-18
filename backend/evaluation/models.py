"""M7 Pydantic schemas: evaluations, claims, grades."""
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

class EDSBucket(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GradeStatus(str, Enum):
    PENDING = "pending"
    RELEASED = "released"


class EvaluationJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# ── Domain models ───────────────────────────────────────────────────────────

class Claim(BaseModel):
    """A single claim extracted from a student answer."""

    claim_id: str = Field(default_factory=_uuid)
    text: str
    concept_ids: List[str] = Field(default_factory=list)
    is_causal: bool = False
    causal_chain: Optional[List[str]] = None
    confidence: float = 0.8


class ClaimExtraction(BaseModel):
    """The full extraction result for a student answer."""

    claims: List[Claim] = Field(default_factory=list)
    total_claims: int = 0
    causal_claims: int = 0
    surface_claims: int = 0
    raw_llm_output: Optional[Dict[str, Any]] = None


class ConceptCoverage(BaseModel):
    """Concept coverage analysis for an evaluation."""

    expected_concepts: List[str] = Field(default_factory=list)
    covered_concepts: List[str] = Field(default_factory=list)
    missing_concepts: List[str] = Field(default_factory=list)
    coverage_ratio: float = 0.0
    partial_coverage: Dict[str, float] = Field(default_factory=dict)


class Evaluation(BaseModel):
    """Full evaluation of a single turn (student answer to a question)."""

    evaluation_id: str = Field(default_factory=_uuid)
    turn_id: str
    org_id: str
    course_id: str
    student_id: str
    question_id: str
    claims: ClaimExtraction = Field(default_factory=ClaimExtraction)
    concept_coverage: ConceptCoverage = Field(default_factory=ConceptCoverage)
    eds_score: float = 0.0
    eds_bucket: EDSBucket = EDSBucket.LOW
    raw_llm_output: Optional[Dict[str, Any]] = None
    evaluated_at: datetime = Field(default_factory=_now)
    evaluation_job_id: Optional[str] = None


class ComponentScores(BaseModel):
    """Breakdown of scoring components for a grade."""

    concept_coverage: float = 0.0
    depth_score: float = 0.0
    coherence_score: float = 0.0
    avg_eds: float = 0.0
    turns_completed: int = 0
    turns_total: int = 0


class Grade(BaseModel):
    """Final grade for an exam session, aggregated from individual evaluations."""

    grade_id: str = Field(default_factory=_uuid)
    session_id: str
    student_id: str
    assignment_id: str
    org_id: str
    course_id: str
    final_score: float = 0.0
    component_scores: ComponentScores = Field(default_factory=ComponentScores)
    override_by: Optional[str] = None
    override_reason: Optional[str] = None
    status: GradeStatus = GradeStatus.PENDING
    released_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ── Tool I/O models ─────────────────────────────────────────────────────────

class EvaluateRequest(BaseModel):
    """Input for the evaluate_answer tool."""

    turn_id: str


class EvaluationResult(BaseModel):
    """Output from evaluate_answer: scores and extracted claims."""

    evaluation_id: str
    eds_score: float
    eds_bucket: EDSBucket
    concept_coverage: ConceptCoverage
    claims_count: int
    causal_claims_count: int
