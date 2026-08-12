"""In-memory Repository: enables the whole pipeline to run without AWS/DB."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict, Optional, List

from epistemy_m3.models import (
    Material, MaterialVersion, Chunk, AsyncJob, Org, Course, VersionStatus,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TenantViolation(Exception):
    """Raised when a row is accessed outside its owning org."""


class InMemoryRepository:
    """Dict-backed store that emulates org-scoped RLS in application code."""

    def __init__(self) -> None:
        self._orgs: Dict[str, Org] = {}
        self._courses: Dict[str, Course] = {}
        self._materials: Dict[str, Material] = {}
        self._versions: Dict[str, MaterialVersion] = {}
        self._chunks: Dict[str, Chunk] = {}
        self._jobs: Dict[str, AsyncJob] = {}
        self._org: Optional[str] = None

    def set_tenant(self, org_id: str) -> None:
        self._org = org_id

    def rollback(self) -> None:
        """No-op: in-memory store has no transaction to clear."""
        return None

    # ── org / course registry ──────────────────────────────────────────────────

    def get_or_create_org(self, org_name: str) -> Org:
        for org in self._orgs.values():
            if org.org_name == org_name:
                return org
        org = Org(org_name=org_name)
        self._orgs[org.org_id] = org
        return org

    def get_or_create_course(self, org_id: str, course_name: str) -> Course:
        for c in self._courses.values():
            if c.org_id == org_id and c.course_name == course_name:
                return c
        course = Course(org_id=org_id, course_name=course_name)
        self._courses[course.course_id] = course
        return course

    def get_course(self, course_id: str) -> Optional[Course]:
        return self._courses.get(course_id)

    def _guard(self, org_id: str) -> None:
        """Emulate RLS: refuse cross-org access when a tenant is set."""
        if self._org is not None and org_id != self._org:
            raise TenantViolation(f"org {self._org} cannot access org {org_id}")

    # ── material ──────────────────────────────────────────────────────────────

    def create_material(self, material: Material) -> Material:
        self._guard(material.org_id)
        self._materials[material.material_id] = material
        return material

    def get_material(self, material_id: str) -> Optional[Material]:
        mat = self._materials.get(material_id)
        if mat:
            self._guard(mat.org_id)
        return mat

    def list_materials(self, course_id: str) -> List[Material]:
        return [m for m in self._materials.values()
                if m.course_id == course_id and self._visible(m.org_id)]

    def _visible(self, org_id: str) -> bool:
        return self._org is None or org_id == self._org

    def set_current_version(self, material_id: str, version_id: str) -> None:
        mat = self._materials[material_id]
        self._guard(mat.org_id)
        mat.current_version_id = version_id
        mat.updated_at = _now()

    # ── material_version ────────────────────────────────────────────────────

    def create_version(self, version: MaterialVersion) -> MaterialVersion:
        self._guard(version.org_id)
        self._versions[version.material_version_id] = version
        return version

    def get_version(self, version_id: str) -> Optional[MaterialVersion]:
        ver = self._versions.get(version_id)
        if ver:
            self._guard(ver.org_id)
        return ver

    def list_versions(self, material_id: str) -> List[MaterialVersion]:
        rows = [v for v in self._versions.values()
                if v.material_id == material_id and self._visible(v.org_id)]
        return sorted(rows, key=lambda v: v.version_no)

    def max_version_no(self, material_id: str) -> int:
        rows = [v.version_no for v in self._versions.values()
                if v.material_id == material_id]
        return max(rows) if rows else 0

    def update_version_status(self, version_id, status, error=None) -> None:
        ver = self._versions[version_id]
        self._guard(ver.org_id)
        ver.status = status
        ver.error = error
        ver.updated_at = _now()

    # ── chunk ─────────────────────────────────────────────────────────────────

    def upsert_chunks(self, chunks: List[Chunk]) -> int:
        """Idempotent on (material_version_id, chunk_index)."""
        for ch in chunks:
            self._guard(ch.org_id)
            self._chunks[self._chunk_key(ch)] = ch
        return len(chunks)

    def _chunk_key(self, ch: Chunk) -> str:
        return f"{ch.material_version_id}:{ch.chunk_index}"

    def count_chunks(self, version_id: str) -> int:
        return sum(1 for c in self._chunks.values()
                   if c.material_version_id == version_id)

    # ── async_job ─────────────────────────────────────────────────────────────

    def create_job(self, job: AsyncJob) -> AsyncJob:
        self._jobs[job.job_id] = job
        return job

    def update_job(self, job_id, status=None, step_name=None,
                   progress_pct=None, error=None) -> None:
        job = self._jobs[job_id]
        self._apply_job_fields(job, status, step_name, progress_pct, error)
        job.updated_at = _now()

    def _apply_job_fields(self, job, status, step_name, progress_pct, error):
        """Patch only the provided job fields."""
        job.status = status or job.status
        job.step_name = step_name or job.step_name
        job.progress_pct = progress_pct if progress_pct is not None else job.progress_pct
        job.error = error if error is not None else job.error

    def get_job(self, job_id: str) -> Optional[AsyncJob]:
        return self._jobs.get(job_id)
