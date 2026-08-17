"""Postgres-backed Repository with per-call RLS session vars and pgvector."""
from __future__ import annotations
import json
from typing import Optional, List

from backend.models import (
    Material, MaterialVersion, Chunk, ChunkPosition, AsyncJob, Org, Course,
    SourceType, VersionStatus, JobStatus,
)


class PostgresRepository:
    """Thin psycopg2 store; sets app.org_id so RLS isolates every query."""

    def __init__(self, conn):
        self.conn = conn
        self._org: Optional[str] = None

    def set_tenant(self, org_id: str) -> None:
        """Bind the RLS session variable for subsequent statements."""
        self._org = org_id
        with self.conn.cursor() as cur:
            # RLS policy: Postgres rejects queries for other tenants automatically
            cur.execute("SELECT set_config('app.org_id', %s, false)", (org_id,))
        self.conn.commit()

    def rollback(self) -> None:
        """Clear aborted transaction state so psycopg2 doesn't block next query."""
        try:
            self.conn.rollback()
        except Exception:
            pass

    # ── org / course registry ──────────────────────────────────────────────────

    def get_or_create_org(self, org_name: str) -> Org:
        """Resolve an org by name, creating it on first use."""
        row = self._one("SELECT org_id, org_name FROM org WHERE org_name=%s",
                        (org_name,))
        if row:
            return Org(org_id=str(row[0]), org_name=row[1])
        org = Org(org_name=org_name)
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO org (org_id, org_name) VALUES (%s,%s)",
                        (org.org_id, org.org_name))
        self.conn.commit()
        return org

    def get_or_create_course(self, org_id: str, course_name: str) -> Course:
        """Resolve a course by name within an org, creating it on first use."""
        row = self._one("SELECT course_id, org_id, course_name FROM course"
                        " WHERE org_id=%s AND course_name=%s", (org_id, course_name))
        if row:
            return Course(course_id=str(row[0]), org_id=str(row[1]),
                          course_name=row[2])
        return self._insert_course(org_id, course_name)

    def _insert_course(self, org_id: str, course_name: str) -> Course:
        """Insert a new course row with a server-minted UUID."""
        course = Course(org_id=org_id, course_name=course_name)
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO course (course_id, org_id, course_name)"
                        " VALUES (%s,%s,%s)",
                        (course.course_id, course.org_id, course.course_name))
        self.conn.commit()
        return course

    def get_course(self, course_id: str) -> Optional[Course]:
        """Look up a course by its UUID (for UUID→name display)."""
        row = self._one("SELECT course_id, org_id, course_name FROM course"
                        " WHERE course_id=%s", (course_id,))
        if not row:
            return None
        return Course(course_id=str(row[0]), org_id=str(row[1]), course_name=row[2])

    # ── material ──────────────────────────────────────────────────────────────

    def create_material(self, material: Material) -> Material:
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO material (material_id, course_id, org_id, created_by,"
                " display_name) VALUES (%s,%s,%s,%s,%s)",
                (material.material_id, material.course_id, material.org_id,
                 material.created_by, material.display_name))
        self.conn.commit()
        return material

    def get_material(self, material_id: str) -> Optional[Material]:
        row = self._one(
            "SELECT material_id, course_id, org_id, created_by, display_name,"
            " current_version_id FROM material WHERE material_id=%s", (material_id,))
        return self._material_from_row(row) if row else None

    def _material_from_row(self, row) -> Material:
        return Material(material_id=str(row[0]), course_id=str(row[1]),
                        org_id=str(row[2]), created_by=str(row[3]),
                        display_name=row[4],
                        current_version_id=str(row[5]) if row[5] else None)

    def list_materials(self, course_id: str) -> List[Material]:
        rows = self._all(
            "SELECT material_id, course_id, org_id, created_by, display_name,"
            " current_version_id FROM material WHERE course_id=%s", (course_id,))
        return [self._material_from_row(r) for r in rows]

    def set_current_version(self, material_id: str, version_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute("UPDATE material SET current_version_id=%s, updated_at=NOW()"
                        " WHERE material_id=%s", (version_id, material_id))
        self.conn.commit()

    # ── material_version ────────────────────────────────────────────────────

    def create_version(self, version: MaterialVersion) -> MaterialVersion:
        with self.conn.cursor() as cur:
            cur.execute(self._INSERT_VERSION, self._version_params(version))
        self.conn.commit()
        return version

    _INSERT_VERSION = (
        "INSERT INTO material_version (material_version_id, material_id, course_id,"
        " org_id, version_no, uploaded_by, source_type, mime_type, file_name, s3_key,"
        " bytes, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")

    def _version_params(self, v: MaterialVersion) -> tuple:
        return (v.material_version_id, v.material_id, v.course_id, v.org_id,
                v.version_no, v.uploaded_by, v.source_type.value, v.mime_type,
                v.file_name, v.s3_key, v.bytes, v.status.value)

    def get_version(self, version_id: str) -> Optional[MaterialVersion]:
        row = self._one(self._SELECT_VERSION + " WHERE material_version_id=%s",
                        (version_id,))
        return self._version_from_row(row) if row else None

    _SELECT_VERSION = (
        "SELECT material_version_id, material_id, course_id, org_id, version_no,"
        " uploaded_by, source_type, mime_type, file_name, s3_key, bytes, status"
        " FROM material_version")

    def _version_from_row(self, row) -> MaterialVersion:
        return MaterialVersion(
            material_version_id=str(row[0]), material_id=str(row[1]),
            course_id=str(row[2]), org_id=str(row[3]), version_no=row[4],
            uploaded_by=str(row[5]), source_type=SourceType(row[6]),
            mime_type=row[7], file_name=row[8], s3_key=row[9], bytes=row[10],
            status=VersionStatus(row[11]))

    def list_versions(self, material_id: str) -> List[MaterialVersion]:
        rows = self._all(self._SELECT_VERSION +
                         " WHERE material_id=%s ORDER BY version_no", (material_id,))
        return [self._version_from_row(r) for r in rows]

    def max_version_no(self, material_id: str) -> int:
        row = self._one("SELECT COALESCE(MAX(version_no),0) FROM material_version"
                        " WHERE material_id=%s", (material_id,))
        return row[0] if row else 0

    def update_version_status(self, version_id, status, error=None) -> None:
        err = json.dumps(error) if error is not None else None
        with self.conn.cursor() as cur:
            cur.execute("UPDATE material_version SET status=%s, error=%s,"
                        " updated_at=NOW() WHERE material_version_id=%s",
                        (self._status_value(status), err, version_id))
        self.conn.commit()

    def _status_value(self, status) -> str:
        return status.value if hasattr(status, "value") else status

    # ── chunk ─────────────────────────────────────────────────────────────────

    def upsert_chunks(self, chunks: List[Chunk]) -> int:
        with self.conn.cursor() as cur:
            for ch in chunks:
                cur.execute(self._UPSERT_CHUNK, self._chunk_params(ch))
        self.conn.commit()
        return len(chunks)

    # DO NOTHING on conflict: re-processing the same version is idempotent
    _UPSERT_CHUNK = (
        "INSERT INTO chunk (chunk_id, material_version_id, course_id, org_id,"
        " chunk_index, text, token_count, position, embedding)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (material_version_id, chunk_index) DO NOTHING")

    def _chunk_params(self, ch: Chunk) -> tuple:
        # pgvector expects the literal format [0.1,0.2,...] not a list object
        vec = "[" + ",".join(str(x) for x in (ch.embedding or [])) + "]"
        return (ch.chunk_id, ch.material_version_id, ch.course_id, ch.org_id,
                ch.chunk_index, ch.text, ch.token_count,
                ch.position.model_dump_json(), vec)

    def count_chunks(self, version_id: str) -> int:
        row = self._one("SELECT COUNT(*) FROM chunk WHERE material_version_id=%s",
                        (version_id,))
        return row[0] if row else 0

    def list_chunks(self, version_id: str) -> List[dict]:
        """Return chunk text/position/token_count for a version, in order."""
        rows = self._all(
            "SELECT chunk_index, text, token_count, position FROM chunk"
            " WHERE material_version_id=%s ORDER BY chunk_index", (version_id,))
        return [self._chunk_row(r) for r in rows]

    def _chunk_row(self, row) -> dict:
        """Shape one chunk row for the read API (embedding omitted)."""
        import json
        pos = row[3] if isinstance(row[3], dict) else json.loads(row[3] or "{}")
        return {"chunk_index": row[0], "text": row[1],
                "token_count": row[2], "position": pos}

    # ── async_job ─────────────────────────────────────────────────────────────

    def create_job(self, job: AsyncJob) -> AsyncJob:
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO async_job (job_id, org_id, course_id, type,"
                        " status, created_by) VALUES (%s,%s,%s,%s,%s,%s)",
                        (job.job_id, job.org_id, job.course_id, job.type,
                         job.status.value, job.created_by))
        self.conn.commit()
        return job

    def update_job(self, job_id, status=None, step_name=None,
                   progress_pct=None, error=None) -> None:
        sets, params = self._job_updates(status, step_name, progress_pct, error)
        params.append(job_id)
        with self.conn.cursor() as cur:
            cur.execute(f"UPDATE async_job SET {', '.join(sets)},"
                        f" updated_at=NOW() WHERE job_id=%s", params)
        self.conn.commit()

    def _job_updates(self, status, step_name, progress_pct, error):
        """Build the SET clause for only the provided job fields."""
        sets, params = [], []
        for col, val in self._job_field_values(status, step_name, progress_pct, error):
            sets.append(f"{col}=%s")
            params.append(val)
        return sets or ["status=status"], params

    def _job_field_values(self, status, step_name, progress_pct, error):
        """Yield (column, value) pairs for non-None job fields."""
        if status is not None:
            yield "status", status.value if hasattr(status, "value") else status
        if step_name is not None:
            yield "step_name", step_name
        if progress_pct is not None:
            yield "progress_pct", progress_pct
        if error is not None:
            yield "error", json.dumps(error)

    def get_job(self, job_id: str) -> Optional[AsyncJob]:
        row = self._one("SELECT job_id, org_id, course_id, type, status FROM"
                        " async_job WHERE job_id=%s", (job_id,))
        if not row:
            return None
        return AsyncJob(job_id=str(row[0]), org_id=str(row[1]),
                        course_id=str(row[2]) if row[2] else None,
                        type=row[3], status=JobStatus(row[4]))

    # ── query helpers ──────────────────────────────────────────────────────────

    def _one(self, sql: str, params: tuple):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    def _all(self, sql: str, params: tuple):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
