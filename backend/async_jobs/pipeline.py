"""Ingest pipeline (T7): extract -> chunk -> embed -> persist -> ready/flip -> graph build."""
from __future__ import annotations
import json
import logging
import uuid
from typing import List

from backend.models import (
    IngestMessage, MaterialVersion, Chunk, VersionStatus, JobStatus,
)
from backend.db.repository import Repository
from backend.storage.s3_client import S3Storage
from backend.embedding.embedder import Embedder
from backend.embedding.persist import stamp_tenant, embed_chunks, persist_chunks
from backend.extract.base import get_extractor
from backend.chunking import Chunker
from backend.config import Settings
from backend.constants import MAX_CHUNKS_FOR_GRAPH, LLM_MAX_TOKENS_GENERATION
from backend.bedrock_helper import call_bedrock

logger = logging.getLogger(__name__)


class IngestError(Exception):
    """A user-actionable ingestion failure (bad/empty/oversized file).

    The worker records the message on the material_version so the professor
    sees exactly which file failed and why, instead of a silent partial ingest.
    """


class IngestPipeline:
    """Runs one material version through every ingestion step."""

    def __init__(self, repo: Repository, storage: S3Storage,
                 embedder: Embedder, chunker: Chunker = None,
                 settings: Settings = None):
        self.repo = repo
        self.storage = storage
        self.embedder = embedder
        self.chunker = chunker or Chunker()
        self.settings = settings

    def run(self, msg: IngestMessage) -> MaterialVersion:
        """Drive the version to ready, flipping current_version_id at the end."""
        # RLS: all subsequent queries are scoped to this tenant
        self.repo.set_tenant(msg.org_id)
        version = self.repo.get_version(msg.material_version_id)
        chunks = self._extract_and_chunk(msg, version)
        self._embed_and_persist(msg, version, chunks)
        self._finalize(msg, version)
        return version

    def _extract_and_chunk(self, msg, version) -> List[Chunk]:
        """Download bytes, extract structural units, build tenant-stamped chunks."""
        self._step(msg, VersionStatus.EXTRACTING, "extracting", 20)
        data = self.storage.get_bytes(msg.s3_key)
        name = version.file_name if version else msg.s3_key
        try:
            units = get_extractor(msg.source_type).extract(data)
        except IngestError:
            raise
        except Exception as exc:
            # Name the file so the failure is actionable (e.g. over the page limit).
            raise IngestError(f'Could not read "{name}": {exc}') from exc
        # No extractable text = silent-partial-ingest territory. Fail loudly with a
        # reason instead of marking an empty document "ready" (issue P-S-1.3).
        if not units:
            raise IngestError(
                f'No readable text found in "{name}". It looks like a scanned or '
                'image-only PDF, and OCR is not enabled. Please upload a text-based '
                'PDF or DOCX, or paste the text directly.')
        self._step(msg, VersionStatus.CHUNKING, "chunking", 45)
        return stamp_tenant(self.chunker.chunk(units), version)

    def _embed_and_persist(self, msg, version, chunks: List[Chunk]) -> None:
        """Embed chunks then idempotently persist them."""
        self._step(msg, VersionStatus.EMBEDDING, "embedding", 70)
        embed_chunks(self.embedder, chunks)
        persist_chunks(self.repo, version, chunks)

    def _finalize(self, msg, version) -> None:
        """Mark ready, flip current version pointer, then trigger graph build."""
        self.repo.update_version_status(version.material_version_id,
                                        VersionStatus.READY)
        self.repo.set_current_version(version.material_id,
                                      version.material_version_id)
        self.repo.update_job(msg.job_id, status=JobStatus.SUCCEEDED,
                             step_name="ready", progress_pct=100)
        self._trigger_graph_build(msg)

    def _trigger_graph_build(self, msg: IngestMessage) -> None:
        """After ingest completes, build the concept graph inline.

        Uses a fresh DB connection to avoid conflicts with the pipeline's
        connection. Calls Bedrock Qwen3 directly (same approach as the HTTP
        rebuild endpoint). Non-fatal: if anything fails, we log a warning and
        the material remains "ready".
        """
        if not self.settings:
            logger.info("No settings provided — skipping graph build")
            return

        try:
            logger.info("Graph auto-trigger starting for course %s", msg.course_id[:8])
            self._do_graph_build(msg)
        except Exception as exc:
            logger.warning("Graph build failed (non-fatal): %s", exc, exc_info=True)

    def _do_graph_build(self, msg: IngestMessage) -> None:
        """Incremental graph build: extract topics from new material only, merge into existing graph."""
        from backend.app.factory import db_connection

        conn = db_connection(self.settings)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.org_id', %s, false)", (msg.org_id,)
                )
                conn.commit()

                # Only fetch chunks from the newly ingested material
                cur.execute(
                    "SELECT text FROM chunk WHERE material_version_id = %s ORDER BY chunk_index",
                    (msg.material_version_id,),
                )
                new_chunks = [row[0] for row in cur.fetchall()]

            if not new_chunks:
                logger.info("No chunks for material %s — skipping graph build", msg.material_version_id[:8])
                return

            logger.info("Extracting topics from %d new chunks (material %s)",
                        len(new_chunks), msg.material_version_id[:8])

            # Load existing graph to merge into
            existing_concepts = []
            existing_relations = []
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT s3_key FROM graph_version WHERE course_id = %s AND is_active = true",
                    (msg.course_id,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    try:
                        existing = json.loads(row[0])
                        existing_concepts = existing.get("concepts", [])
                        existing_relations = existing.get("relations", [])
                    except (json.JSONDecodeError, TypeError):
                        pass

            combined = "\n\n".join(new_chunks[:MAX_CHUNKS_FOR_GRAPH])

            # If existing graph has concepts, tell the model about them to avoid duplicates
            existing_context = ""
            if existing_concepts:
                existing_labels = [c.get("label", "") for c in existing_concepts]
                existing_context = (
                    f"\n\nExisting concepts already in the graph: {', '.join(existing_labels)}. "
                    "Do NOT re-extract these unless the new material adds significant depth. "
                    "Focus on NEW concepts from this material and relationships to existing ones."
                )

            system_prompt = (
                "You are an expert knowledge graph builder for educational content. "
                "Given NEW course material, extract key concepts and their relationships. "
                "Return ONLY valid JSON: "
                '{"concepts": [{"label": "...", "definition": "...", "abstraction_level": 0.5}], '
                '"relations": [{"src": "...", "dst": "...", "edge_type": "PREREQUISITE_FOR", "confidence": 0.9}]} '
                "Edge types: PREREQUISITE_FOR, ENABLES, IS_A, PART_OF, APPLIED_IN, CO_REQUIRED_WITH. "
                "Extract 5-20 NEW concepts from this material. Only return JSON."
                + existing_context
            )
            data = call_bedrock(
                self.settings, system_prompt,
                f"Domain: general\n\n{combined}",
                max_tokens=LLM_MAX_TOKENS_GENERATION, temperature=0.1,
            )
            new_concepts = data.get("concepts", [])
            new_relations = data.get("relations", [])

            # Merge: add new concepts (deduplicate by label)
            existing_labels = {c.get("label", "").lower() for c in existing_concepts}
            merged_concepts = list(existing_concepts)
            for c in new_concepts:
                if c.get("label", "").lower() not in existing_labels:
                    merged_concepts.append(c)
                    existing_labels.add(c.get("label", "").lower())

            # Merge relations (deduplicate by src+dst+edge_type)
            existing_rel_keys = {
                (r.get("src", ""), r.get("dst", ""), r.get("edge_type", ""))
                for r in existing_relations
            }
            merged_relations = list(existing_relations)
            for r in new_relations:
                key = (r.get("src", ""), r.get("dst", ""), r.get("edge_type", ""))
                if key not in existing_rel_keys:
                    merged_relations.append(r)
                    existing_rel_keys.add(key)

            version_id = str(uuid.uuid4())
            graph_json = json.dumps({"concepts": merged_concepts, "relations": merged_relations})
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE graph_version SET is_active = false "
                    "WHERE org_id = %s AND course_id = %s",
                    (msg.org_id, msg.course_id),
                )
                cur.execute(
                    """INSERT INTO graph_version
                       (version_id, org_id, course_id, graph_version, node_count,
                        edge_count, validation_score, is_active, s3_key)
                       VALUES (%s::uuid, %s::uuid, %s::uuid, 1, %s, %s, %s, true, %s)""",
                    (version_id, msg.org_id, msg.course_id, len(merged_concepts),
                     len(merged_relations), 0.8, graph_json),
                )
            conn.commit()

            logger.info("Incremental graph build for course %s: %d new concepts merged, total %d concepts, %d relations",
                        msg.course_id[:8], len(new_concepts), len(merged_concepts), len(merged_relations))
        finally:
            conn.close()

    def _step(self, msg: IngestMessage, status: VersionStatus,
              name: str, pct: int) -> None:
        """Advance version status and async_job progress together."""
        self.repo.update_version_status(msg.material_version_id, status)
        self.repo.update_job(msg.job_id, status=JobStatus.RUNNING,
                             step_name=name, progress_pct=pct)
