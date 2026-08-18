"""Graph version metadata management in Aurora (per HLD design).

Tracks active graph version per course with validation_score, job_id, and
is_active flag. The .kuzu file lives on S3 at:
    s3://bucket/{org_id}/{course_id}/graph/{version}.kuzu

Uses the same psycopg2 / in-memory fallback pattern as the prototype.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from backend.graph.models import GraphVersion

logger = logging.getLogger(__name__)


# -- In-memory stub for local dev / testing -----------------------------------

class _InMemoryGraphMetaStore:
    """Drop-in stub when Postgres is unavailable."""

    def __init__(self):
        self._versions: Dict[str, List[GraphVersion]] = {}
        self._eds: List[Dict] = []

    def create_version(self, version: GraphVersion) -> GraphVersion:
        key = f"{version.org_id}:{version.course_id}"
        self._versions.setdefault(key, []).append(version)
        return version

    def get_active_version(self, org_id: str, course_id: str) -> Optional[GraphVersion]:
        key = f"{org_id}:{course_id}"
        for v in reversed(self._versions.get(key, [])):
            if v.is_active:
                return v
        return None

    def get_version(self, version_id: str) -> Optional[GraphVersion]:
        for versions in self._versions.values():
            for v in versions:
                if v.version_id == version_id:
                    return v
        return None

    def list_versions(self, org_id: str, course_id: str) -> List[GraphVersion]:
        key = f"{org_id}:{course_id}"
        return self._versions.get(key, [])

    def activate_version(self, version_id: str, org_id: str, course_id: str) -> bool:
        key = f"{org_id}:{course_id}"
        # Deactivate all others
        for v in self._versions.get(key, []):
            v.is_active = False
        # Activate the target
        for v in self._versions.get(key, []):
            if v.version_id == version_id:
                v.is_active = True
                v.updated_at = datetime.now(timezone.utc)
                return True
        return False

    def update_version(
        self,
        version_id: str,
        *,
        node_count: Optional[int] = None,
        edge_count: Optional[int] = None,
        validation_score: Optional[float] = None,
        s3_key: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> None:
        for versions in self._versions.values():
            for v in versions:
                if v.version_id == version_id:
                    if node_count is not None:
                        v.node_count = node_count
                    if edge_count is not None:
                        v.edge_count = edge_count
                    if validation_score is not None:
                        v.validation_score = validation_score
                    if s3_key is not None:
                        v.s3_key = s3_key
                    if job_id is not None:
                        v.job_id = job_id
                    v.updated_at = datetime.now(timezone.utc)
                    return

    def next_version_number(self, org_id: str, course_id: str) -> int:
        key = f"{org_id}:{course_id}"
        versions = self._versions.get(key, [])
        if not versions:
            return 1
        return max(v.graph_version for v in versions) + 1

    def record_eds(
        self,
        assessment_id: str,
        student_id: str,
        org_id: str,
        course_id: str,
        eds_score: float,
        components: Dict,
    ) -> None:
        self._eds.append({
            "assessment_id": assessment_id,
            "student_id": student_id,
            "org_id": org_id,
            "course_id": course_id,
            "eds_score": eds_score,
            "components": components,
        })

    def close(self) -> None:
        pass


# -- Postgres-backed graph metadata store -------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS graph_version (
    version_id       TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL,
    course_id        TEXT NOT NULL,
    graph_version    INT NOT NULL DEFAULT 1,
    s3_key           TEXT NOT NULL DEFAULT '',
    node_count       INT NOT NULL DEFAULT 0,
    edge_count       INT NOT NULL DEFAULT 0,
    validation_score DOUBLE PRECISION,
    job_id           TEXT,
    is_active        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_graph_version_tenant
    ON graph_version(org_id, course_id);
CREATE INDEX IF NOT EXISTS idx_graph_version_active
    ON graph_version(org_id, course_id, is_active) WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS graph_eds_results (
    run_id         SERIAL PRIMARY KEY,
    assessment_id  TEXT NOT NULL,
    student_id     TEXT NOT NULL,
    org_id         TEXT NOT NULL,
    course_id      TEXT NOT NULL,
    eds_score      DOUBLE PRECISION NOT NULL,
    components     JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_graph_eds_tenant
    ON graph_eds_results(org_id, course_id, assessment_id);
"""


class GraphMetadataStore:
    """
    Aurora Postgres-backed store for graph_version rows and EDS results.

    Falls back to in-memory stub if psycopg2 or connection is unavailable.
    Thread-safety: each instance holds its own connection; create one per thread.
    """

    def __init__(self, dsn: Optional[str] = None, conn=None):
        self._stub: Optional[_InMemoryGraphMetaStore] = None
        if conn is not None:
            self.conn = conn
            self._apply_ddl()
            return
        if not dsn:
            logger.info("No Postgres DSN for graph metadata — using in-memory stub")
            self._stub = _InMemoryGraphMetaStore()
            return
        try:
            import psycopg2
            self.conn = psycopg2.connect(dsn)
            self._apply_ddl()
        except Exception as e:
            logger.warning("Postgres unavailable for graph metadata (%s) — using in-memory stub", e)
            self._stub = _InMemoryGraphMetaStore()

    def _apply_ddl(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(DDL)
        self.conn.commit()

    def _use_stub(self) -> bool:
        return self._stub is not None

    # -- Version lifecycle -----------------------------------------------------

    def create_version(self, version: GraphVersion) -> GraphVersion:
        """Insert a new graph_version row. Caller sets version_id upfront."""
        if self._use_stub():
            return self._stub.create_version(version)

        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO graph_version
                    (version_id, org_id, course_id, graph_version, s3_key,
                     node_count, edge_count, validation_score, job_id, is_active,
                     created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                version.version_id, version.org_id, version.course_id,
                version.graph_version, version.s3_key,
                version.node_count, version.edge_count,
                version.validation_score, version.job_id, version.is_active,
                version.created_at, version.updated_at,
            ))
        self.conn.commit()
        return version

    def get_active_version(self, org_id: str, course_id: str) -> Optional[GraphVersion]:
        """Return the currently active graph version for a course, or None."""
        if self._use_stub():
            return self._stub.get_active_version(org_id, course_id)

        import psycopg2.extras
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM graph_version
                WHERE org_id = %s AND course_id = %s AND is_active = TRUE
                LIMIT 1
            """, (org_id, course_id))
            row = cur.fetchone()
            if not row:
                return None
            return GraphVersion(**dict(row))

    def get_version(self, version_id: str) -> Optional[GraphVersion]:
        """Retrieve a specific graph version by ID."""
        if self._use_stub():
            return self._stub.get_version(version_id)

        import psycopg2.extras
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM graph_version WHERE version_id = %s", (version_id,))
            row = cur.fetchone()
            if not row:
                return None
            return GraphVersion(**dict(row))

    def list_versions(self, org_id: str, course_id: str) -> List[GraphVersion]:
        """List all graph versions for a course, newest first."""
        if self._use_stub():
            return self._stub.list_versions(org_id, course_id)

        import psycopg2.extras
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM graph_version
                WHERE org_id = %s AND course_id = %s
                ORDER BY graph_version DESC
            """, (org_id, course_id))
            return [GraphVersion(**dict(row)) for row in cur.fetchall()]

    def activate_version(self, version_id: str, org_id: str, course_id: str) -> bool:
        """
        Atomically flip is_active to the given version, deactivating all others.
        Returns True if the version was found and activated.
        """
        if self._use_stub():
            return self._stub.activate_version(version_id, org_id, course_id)

        with self.conn.cursor() as cur:
            # Deactivate all for this tenant
            cur.execute("""
                UPDATE graph_version SET is_active = FALSE, updated_at = NOW()
                WHERE org_id = %s AND course_id = %s AND is_active = TRUE
            """, (org_id, course_id))
            # Activate target
            cur.execute("""
                UPDATE graph_version SET is_active = TRUE, updated_at = NOW()
                WHERE version_id = %s AND org_id = %s AND course_id = %s
                RETURNING version_id
            """, (version_id, org_id, course_id))
            activated = cur.fetchone() is not None
        self.conn.commit()
        return activated

    def update_version(
        self,
        version_id: str,
        *,
        node_count: Optional[int] = None,
        edge_count: Optional[int] = None,
        validation_score: Optional[float] = None,
        s3_key: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> None:
        """Update mutable fields on a graph version row."""
        if self._use_stub():
            self._stub.update_version(
                version_id,
                node_count=node_count,
                edge_count=edge_count,
                validation_score=validation_score,
                s3_key=s3_key,
                job_id=job_id,
            )
            return

        sets: List[str] = ["updated_at = NOW()"]
        params: List[Any] = []
        if node_count is not None:
            sets.append("node_count = %s")
            params.append(node_count)
        if edge_count is not None:
            sets.append("edge_count = %s")
            params.append(edge_count)
        if validation_score is not None:
            sets.append("validation_score = %s")
            params.append(validation_score)
        if s3_key is not None:
            sets.append("s3_key = %s")
            params.append(s3_key)
        if job_id is not None:
            sets.append("job_id = %s")
            params.append(job_id)

        params.append(version_id)
        with self.conn.cursor() as cur:
            cur.execute(
                f"UPDATE graph_version SET {', '.join(sets)} WHERE version_id = %s",
                params,
            )
        self.conn.commit()

    def next_version_number(self, org_id: str, course_id: str) -> int:
        """Return the next graph_version number for a course."""
        if self._use_stub():
            return self._stub.next_version_number(org_id, course_id)

        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT COALESCE(MAX(graph_version), 0) + 1
                FROM graph_version
                WHERE org_id = %s AND course_id = %s
            """, (org_id, course_id))
            return cur.fetchone()[0]

    # -- EDS results -----------------------------------------------------------

    def record_eds(
        self,
        assessment_id: str,
        student_id: str,
        org_id: str,
        course_id: str,
        eds_score: float,
        components: Dict,
    ) -> None:
        """Persist an EDS scoring result."""
        if self._use_stub():
            self._stub.record_eds(
                assessment_id, student_id, org_id, course_id, eds_score, components
            )
            return

        with self.conn.cursor() as cur:
            cur.execute("""
                INSERT INTO graph_eds_results
                    (assessment_id, student_id, org_id, course_id, eds_score, components)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (assessment_id, student_id, org_id, course_id,
                  eds_score, json.dumps(components)))
        self.conn.commit()

    # -- Cleanup ---------------------------------------------------------------

    def close(self) -> None:
        if self._use_stub():
            self._stub.close()
            return
        self.conn.close()
