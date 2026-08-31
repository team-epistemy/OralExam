"""Presign + register handlers (T3). The backend owns org_id, key, version_no."""
from __future__ import annotations
import mimetypes
from typing import Callable

from backend.models import (
    Material, MaterialVersion, AsyncJob, IngestMessage, Caller, Role,
    PresignRequest, PresignResponse, IngestRequest, SourceType,
    VersionStatus, JobStatus,
)
from backend.constants import MAX_UPLOAD_BYTES
from backend.db.repository import Repository
from backend.storage import build_s3_key
from backend.storage.s3_client import S3Storage
from backend.async_jobs.queue import Queue


class AuthorizationError(Exception):
    """Raised when the caller lacks the required role/course access."""


def detect_source_type(file_name: str) -> SourceType:
    """Map a file extension to a supported source type."""
    lower = file_name.lower()
    if lower.endswith((".md", ".markdown")):
        return SourceType.MARKDOWN
    if lower.endswith(".pptx"):
        return SourceType.PPTX
    if lower.endswith(".docx"):
        return SourceType.DOCX
    if lower.endswith(".pdf"):
        return SourceType.PDF
    if lower.endswith(".txt"):
        return SourceType.TEXT
    raise AuthorizationError(f"unsupported file type: {file_name}")


class MaterialsApi:
    """Owns presign + register; professor role enforced on the course."""

    def __init__(self, repo: Repository, storage: S3Storage, queue: Queue,
                 authorize: Callable[[Caller, str], bool]):
        self.repo = repo
        self.storage = storage
        self.queue = queue
        self.authorize = authorize

    def presign(self, caller: Caller, course_id: str,
                req: PresignRequest) -> PresignResponse:
        """Authorize, mint/resolve material, create a pending version + URL."""
        self._require_professor(caller, course_id)
        # Reject oversized uploads up front with a stated limit — a huge file
        # would otherwise upload and then hang/time out the ingest pipeline.
        if req.bytes and req.bytes > MAX_UPLOAD_BYTES:
            got_mb = req.bytes // (1024 * 1024)
            lim_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
            raise ValueError(
                f'"{req.file_name}" is {got_mb} MB, over the {lim_mb} MB per-file limit. '
                'Please split it into smaller files.')
        self.repo.set_tenant(caller.org_id)
        material = self._resolve_material(caller, course_id, req)
        version = self._new_version(caller, course_id, material, req)
        url = self.storage.presign_put(version.s3_key, req.mime_type, req.bytes)
        return self._presign_response(material, version, url)

    def presign_by_name(self, user_id: str, role: str, org_id: str,
                        req: IngestRequest) -> PresignResponse:
        """Presign within the caller's verified org; course resolved by name.

        Tenant is the authenticated org_id, never req.org_name — the request body
        can't redirect an upload into another tenant.
        """
        self.repo.set_tenant(org_id)  # RLS is FORCEd: bind tenant before course create
        # user_id owns the course on first creation (existing courses keep their owner).
        course = self.repo.get_or_create_course(org_id, req.course_name, user_id)
        # A material is normally mapped to a class session — reuse the chosen one,
        # or instantiate one now. The syllabus is course-level, not a class, so it
        # skips this (otherwise it would leave a stray empty session behind).
        session_id = None if req.is_syllabus else self.repo.get_or_create_session(
            org_id, course.course_id, req.session_id, req.session_date, user_id)
        caller = Caller(user_id=user_id, org_id=org_id, role=Role(role))
        inner = PresignRequest(file_name=req.file_name, mime_type=req.mime_type,
                               bytes=req.bytes, material_id=req.material_id,
                               display_name=req.display_name, session_id=session_id)
        return self.presign(caller, course.course_id, inner)

    def _require_professor(self, caller: Caller, course_id: str) -> None:
        """Reject non-professors and professors of other courses."""
        if caller.role != Role.PROFESSOR or not self.authorize(caller, course_id):
            raise AuthorizationError("professor role on this course required")

    def _resolve_material(self, caller, course_id, req) -> Material:
        """Reuse an existing material or create a new one for v1."""
        if req.material_id:
            return self._existing_material(caller, course_id, req.material_id)
        material = Material(course_id=course_id, org_id=caller.org_id,
                            created_by=caller.user_id,
                            display_name=(req.display_name or "").strip() or req.file_name,
                            session_id=getattr(req, "session_id", None))
        return self.repo.create_material(material)

    def _existing_material(self, caller, course_id, material_id) -> Material:
        """Fetch a material and confirm it belongs to this course."""
        material = self.repo.get_material(material_id)
        if not material or material.course_id != course_id:
            raise AuthorizationError("material not found in this course")
        return material

    def _new_version(self, caller, course_id, material, req) -> MaterialVersion:
        """Create the next pending material_version with a server-built key."""
        version_no = self.repo.max_version_no(material.material_id) + 1
        key = build_s3_key(caller.org_id, course_id, material.material_id,
                           version_no, req.file_name)
        version = MaterialVersion(
            material_id=material.material_id, course_id=course_id,
            org_id=caller.org_id, version_no=version_no, uploaded_by=caller.user_id,
            source_type=detect_source_type(req.file_name), mime_type=req.mime_type,
            file_name=req.file_name, s3_key=key, bytes=req.bytes)
        return self.repo.create_version(version)

    def _presign_response(self, material, version, url) -> PresignResponse:
        return PresignResponse(
            material_id=material.material_id,
            material_version_id=version.material_version_id,
            version_no=version.version_no, s3_key=version.s3_key, upload_url=url,
            course_id=str(material.course_id or ""),
            session_id=str(material.session_id or ""))

    def register(self, caller: Caller, version_id: str) -> AsyncJob:
        """Confirm the object exists, flip to uploaded, enqueue an ingest job."""
        self.repo.set_tenant(caller.org_id)
        version = self._authorized_version(caller, version_id)
        self._require_uploaded_object(version)
        job = self._enqueue_ingest(caller, version)
        return job

    def register_by_name(self, user_id: str, role: str, org_id: str,
                        version_id: str) -> AsyncJob:
        """Register within the caller's verified org."""
        caller = Caller(user_id=user_id, org_id=org_id, role=Role(role))
        return self.register(caller, version_id)

    def caller_for_org(self, user_id: str, role: str, org_id: str) -> Caller:
        """Build a Caller from the already-resolved tenant id (chokepoint output)."""
        return Caller(user_id=user_id, org_id=org_id, role=Role(role))

    def resolve_course_id(self, org_id: str, course_name: str) -> str:
        """Return the course UUID for a course name within the caller's org."""
        self.repo.set_tenant(org_id)  # RLS is FORCEd: bind tenant before course create
        course = self.repo.get_or_create_course(org_id, course_name)
        return course.course_id

    def _authorized_version(self, caller, version_id) -> MaterialVersion:
        """Load the version and enforce professor access to its course."""
        version = self.repo.get_version(version_id)
        if not version:
            raise AuthorizationError("version not found")
        self._require_professor(caller, version.course_id)
        return version

    def _require_uploaded_object(self, version: MaterialVersion) -> None:
        """Fail register if the client never uploaded to the bound key."""
        if not self.storage.object_exists(version.s3_key):
            raise AuthorizationError("object not found at expected key")

    def _enqueue_ingest(self, caller, version) -> AsyncJob:
        """Create the async_job, mark uploaded, and send the SQS message."""
        job = self.repo.create_job(AsyncJob(
            org_id=version.org_id, course_id=version.course_id,
            type="ingest", created_by=caller.user_id))
        self.repo.update_version_status(version.material_version_id,
                                        VersionStatus.UPLOADED)
        self.queue.send(self._build_message(job, version))
        return job

    def _build_message(self, job, version) -> IngestMessage:
        return IngestMessage(
            job_id=job.job_id, material_version_id=version.material_version_id,
            org_id=version.org_id, course_id=version.course_id,
            source_type=version.source_type, s3_key=version.s3_key)
