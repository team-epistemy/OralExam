"""Cosine-similarity search over course chunks using pgvector (T15).

Retrieval primitive consumed by M5 (question generation) and M7 (evaluation).
Targets p95 < 300ms for k=10 by leveraging the IVFFlat index on chunk.embedding.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

from epistemy_m3.embedding.embedder import Embedder


@dataclass(frozen=True)
class SearchResult:
    """Single retrieval hit returned by search_corpus."""

    text: str
    position: dict
    material_version_id: str
    score: float
    chunk_index: int


class CorpusSearcher:
    """Vector search over course material chunks, respecting RLS.

    Uses the same Bedrock Titan v2 embedder that ingestion writes with,
    then performs cosine distance search via pgvector's <=> operator.
    """

    def __init__(self, conn, embedder: Embedder):
        """
        Parameters
        ----------
        conn : psycopg2 connection
            Database connection. RLS is enforced via app.org_id session var.
        embedder : Embedder
            Same embedding model used during ingestion (Titan v2, 1024-dim).
        """
        self.conn = conn
        self.embedder = embedder

    def search(
        self,
        org_id: str,
        course_id: str,
        query: str,
        k: int = 10,
        material_version_ids: Optional[List[str]] = None,
    ) -> List[SearchResult]:
        """Embed the query and retrieve the top-k chunks by cosine similarity.

        Parameters
        ----------
        org_id : str
            Tenant UUID; sets the RLS session variable.
        course_id : str
            Course to search within.
        query : str
            Natural-language search query.
        k : int
            Number of results to return (default 10).
        material_version_ids : list[str] | None
            If supplied, restrict to exactly those versions (for M7
            reproducibility). Otherwise, search only chunks belonging
            to each material's current version.

        Returns
        -------
        list[SearchResult]
            Ordered by descending similarity (highest score first).
        """
        self._set_tenant(org_id)
        query_vec = self._embed_query(query)
        return self._vector_search(course_id, query_vec, k, material_version_ids)

    def _set_tenant(self, org_id: str) -> None:
        """Bind app.org_id so Postgres RLS scopes all subsequent reads."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id', %s, false)", (org_id,))
        self.conn.commit()

    def _embed_query(self, query: str) -> List[float]:
        """Embed a single query string using the shared embedder."""
        vectors = self.embedder.embed([query])
        return vectors[0]

    def _vector_search(
        self,
        course_id: str,
        query_vec: List[float],
        k: int,
        material_version_ids: Optional[List[str]],
    ) -> List[SearchResult]:
        """Run the pgvector cosine search with course/version filtering."""
        vec_literal = "[" + ",".join(str(x) for x in query_vec) + "]"

        if material_version_ids:
            # M7 reproducibility mode: search exact version set
            sql = _SQL_SEARCH_BY_VERSIONS
            params = {
                "course_id": course_id,
                "version_ids": tuple(material_version_ids),
                "vec": vec_literal,
                "k": k,
            }
        else:
            # Default: current versions only (join through material)
            sql = _SQL_SEARCH_CURRENT
            params = {
                "course_id": course_id,
                "vec": vec_literal,
                "k": k,
            }

        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

        return [self._row_to_result(row) for row in rows]

    def _row_to_result(self, row) -> SearchResult:
        """Convert a DB row to a SearchResult, parsing position JSON."""
        text, position_raw, material_version_id, distance, chunk_index = row
        # pgvector <=> returns cosine distance (0 = identical, 2 = opposite).
        # Convert to similarity score in [0, 1].
        score = max(0.0, 1.0 - distance)
        position = (
            position_raw if isinstance(position_raw, dict)
            else json.loads(position_raw or "{}")
        )
        return SearchResult(
            text=text,
            position=position,
            material_version_id=str(material_version_id),
            score=round(score, 6),
            chunk_index=chunk_index,
        )


# ── SQL Templates ────────────────────────────────────────────────────────────
# Both queries use the IVFFlat cosine index via the <=> operator.
# RLS (app.org_id) is enforced at the session level and applies automatically.

_SQL_SEARCH_CURRENT = """
SELECT c.text,
       c.position,
       c.material_version_id,
       c.embedding <=> %(vec)s::vector AS distance,
       c.chunk_index
FROM chunk c
JOIN material m ON m.current_version_id = c.material_version_id
WHERE c.course_id = %(course_id)s
ORDER BY distance
LIMIT %(k)s
"""

_SQL_SEARCH_BY_VERSIONS = """
SELECT c.text,
       c.position,
       c.material_version_id,
       c.embedding <=> %(vec)s::vector AS distance,
       c.chunk_index
FROM chunk c
WHERE c.course_id = %(course_id)s
  AND c.material_version_id IN %(version_ids)s
ORDER BY distance
LIMIT %(k)s
"""
