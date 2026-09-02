"""M3 Pydantic schemas: enums, domain rows, and tool I/O models."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone, date
from enum import Enum
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Enums ───────────────────────────────────────────────────────────────────

class SourceType(str, Enum):
    MARKDOWN = "markdown"
    TEXT = "text"
    PPTX = "pptx"
    PDF = "pdf"
    DOCX = "docx"
    DOC = "doc"
    RTF = "rtf"


class VersionStatus(str, Enum):
    PENDING = "pending"
    UPLOADED = "uploaded"
    EXTRACTING = "extracting"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Role(str, Enum):
    PLATFORM_ADMIN = "platform_admin"
    PROFESSOR = "professor"
    STUDENT = "student"


# ── Domain rows ──────────────────────────────────────────────────────────────

class Org(BaseModel):
    """A tenant (university). org_name is the human handle; org_id is the key."""

    org_id: str = Field(default_factory=_uuid)
    org_name: str
    title: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class Course(BaseModel):
    """A course. course_name is unique within its org; course_id is the key."""

    course_id: str = Field(default_factory=_uuid)
    org_id: str
    course_name: str
    title: Optional[str] = None
    # Owning professor's email (intra-org isolation); NULL for legacy/orphan courses.
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class ClassSession(BaseModel):
    """A class session belonging to a course (a course maps to N sessions)."""

    session_id: str = Field(default_factory=_uuid)
    course_id: str
    org_id: str
    session_date: Optional[date] = None       # optional
    session_document: Optional[str] = None     # the session's document (content or reference)
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class Material(BaseModel):
    """Logical material; current_version_id flips when a version reaches ready."""

    material_id: str = Field(default_factory=_uuid)
    course_id: str
    org_id: str
    created_by: str
    display_name: str
    # Every material is mapped to a class session (created on upload if absent).
    session_id: Optional[str] = None
    current_version_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class MaterialVersion(BaseModel):
    """One row per upload of a material."""

    material_version_id: str = Field(default_factory=_uuid)
    material_id: str
    course_id: str
    org_id: str
    version_no: int
    uploaded_by: str
    source_type: SourceType
    mime_type: str
    file_name: str
    s3_key: str
    bytes: int = 0
    checksum: Optional[str] = None
    status: VersionStatus = VersionStatus.PENDING
    error: Optional[Dict[str, Any]] = None
    ingest_job_id: Optional[str] = None
    superseded_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ChunkPosition(BaseModel):
    """Structure markers; only the relevant fields are populated per format."""

    slide_no: Optional[int] = None
    page_no: Optional[int] = None
    heading_path: Optional[List[str]] = None
    time_start_ms: Optional[int] = None
    time_end_ms: Optional[int] = None


class Chunk(BaseModel):
    """Unit of retrieval; belongs to a specific material version."""

    chunk_id: str = Field(default_factory=_uuid)
    material_version_id: str
    course_id: str
    org_id: str
    chunk_index: int
    text: str
    token_count: int
    position: ChunkPosition = Field(default_factory=ChunkPosition)
    embedding: Optional[List[float]] = None


class AsyncJob(BaseModel):
    """Cross-cutting async pipeline record (S2-owned shape)."""

    job_id: str = Field(default_factory=_uuid)
    org_id: str
    course_id: Optional[str] = None
    type: str = "ingest"
    status: JobStatus = JobStatus.QUEUED
    progress_pct: int = 0
    step_name: Optional[str] = None
    created_by: Optional[str] = None
    error: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ── Tool I/O models (T14) ────────────────────────────────────────────────────

class Caller(BaseModel):
    """Resolved identity shared by REST and MCP surfaces."""

    user_id: str
    org_id: str
    role: Role


class PresignRequest(BaseModel):
    file_name: str
    mime_type: str
    bytes: int
    material_id: Optional[str] = None
    # Optional human label (e.g. topic / class session); falls back to file_name.
    display_name: Optional[str] = None
    # Class session this material belongs to (resolved/created before presign).
    session_id: Optional[str] = None


class IngestRequest(BaseModel):
    """Name-based upload request: the UI sends human handles, not UUIDs."""

    org_name: str
    course_name: str
    file_name: str
    mime_type: str
    bytes: int
    material_id: Optional[str] = None
    # Optional override for the material's display name (defaults to the file name).
    display_name: Optional[str] = None
    # The class session to attach the material to. If omitted (or not found), a
    # new session is created — a material is always mapped to a session.
    session_id: Optional[str] = None
    session_date: Optional[date] = None
    # Topic that titles a NEWLY created session (the group heading in the UI).
    # Ignored when session_id points at an existing (already titled) session.
    session_title: Optional[str] = None
    # The course syllabus is a course-level document, not a class session, so it
    # skips session creation/attachment (avoids a stray empty session).
    is_syllabus: bool = False


class PresignResponse(BaseModel):
    material_id: str
    material_version_id: str
    version_no: int
    s3_key: str
    upload_url: str
    course_id: str = ""
    session_id: str = ""
    fields: Dict[str, Any] = Field(default_factory=dict)


class MaterialSummary(BaseModel):
    material_id: str
    display_name: str
    current_version_id: Optional[str]
    status: Optional[VersionStatus] = None


class IngestMessage(BaseModel):
    """SQS payload handed from the register endpoint to the worker."""

    job_id: str
    material_version_id: str
    org_id: str
    course_id: str
    source_type: SourceType
    s3_key: str
