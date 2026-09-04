"""Ingest pipeline (T7): extract -> chunk -> embed -> persist -> ready/flip -> graph build."""
from __future__ import annotations
import logging
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
from backend.app.exam_questions import sanitize_bank
from backend.app.concept_graph import (
    write_document_concepts, snapshot_course_graph, syllabus_version_ids,
)
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

        # Tabular/data uploads (CSV, XLSX) are ingested and searchable but never
        # contribute to the concept graph — a spreadsheet has no conceptual prose.
        from backend.models import NON_GRAPH_SOURCE_TYPES
        if msg.source_type in NON_GRAPH_SOURCE_TYPES:
            logger.info("Material %s is a %s data file — skipping concept-graph build",
                        msg.material_version_id[:8], getattr(msg.source_type, "value", msg.source_type))
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

                # The syllabus is a scaffold, not learning content, so it never
                # contributes concepts. Skip extraction, but still recompute so the
                # course graph reflects only its materials (and drops the syllabus
                # if it had been ingested before being marked as the syllabus).
                if str(msg.material_version_id) in syllabus_version_ids(cur, msg.org_id, msg.course_id):
                    snapshot = snapshot_course_graph(cur, msg.org_id, msg.course_id)
                    conn.commit()
                    logger.info("Material %s is the course syllabus — excluded from the graph; "
                                "course graph now %d concepts from materials only",
                                msg.material_version_id[:8], len(snapshot["concepts"]))
                    return

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

            # Concepts already known for THIS course — tell the model so it reuses
            # exact labels (dedup keys on label) instead of coining variants.
            with conn.cursor() as cur:
                cur.execute("SELECT label FROM course_concept WHERE course_id = %s::uuid AND org_id = %s::uuid",
                            (msg.course_id, msg.org_id))
                known_labels = [r[0] for r in cur.fetchall()]

            combined = "\n\n".join(new_chunks[:MAX_CHUNKS_FOR_GRAPH])

            existing_context = ""
            if known_labels:
                existing_context = (
                    f"\n\nConcepts already in this course: {', '.join(known_labels)}. "
                    "Reuse the EXACT same label when this material covers one of them; "
                    "otherwise add new concepts found in this material."
                )

            system_prompt = (
                "You are an expert knowledge graph builder for educational content. "
                "Given NEW course material, extract key concepts and their relationships. "
                "For EACH concept also author a DEPTH-TAGGED oral-exam question bank grounded "
                "strictly in the material: 'recall' = 2 questions on precise definitions/facts/"
                "formulas; 'application' = 2 questions applying the concept to a straightforward "
                "situation; 'in_depth' = 2 higher-order 'why/how' questions on mechanisms, "
                "prerequisite chains, or multi-step reasoning; 'case' = 1 question opening with a "
                "brief 1-2 sentence mini-case (a realistic scenario) that asks the student to APPLY "
                "the concept. "
                "Return ONLY valid JSON: "
                '{"concepts": [{"label": "...", "definition": "...", "abstraction_level": 0.5, '
                '"questions": {"recall": ["...", "..."], "application": ["...", "..."], '
                '"in_depth": ["...", "..."], "case": ["Mini-case: <scenario>. <apply the concept>"]}}], '
                '"relations": [{"src": "...", "dst": "...", "edge_type": "PREREQUISITE_FOR", "confidence": 0.9}]} '
                "Edge types: PREREQUISITE_FOR, ENABLES, IS_A, PART_OF, APPLIED_IN, CO_REQUIRED_WITH. "
                "Extract 5-20 NEW concepts from this material. Only return JSON."
                + existing_context
            )
            data = call_bedrock(
                self.settings, system_prompt,
                f"Domain: general\n\n{combined}",
                max_tokens=8000, temperature=0.1,
            )
            new_concepts = data.get("concepts", [])
            new_relations = data.get("relations", [])

            # Record THIS document's concepts (mapping 1), then recompute the course
            # graph from ONLY this course's documents (mapping 2) and snapshot it.
            # The graph can no longer accumulate off-subject concepts across courses.
            with conn.cursor() as cur:
                write_document_concepts(cur, msg.org_id, msg.course_id,
                                        msg.material_version_id, new_concepts, new_relations)
                snapshot = snapshot_course_graph(cur, msg.org_id, msg.course_id)
            conn.commit()

            logger.info("Graph build for course %s: document contributed %d concepts; "
                        "course graph now %d concepts, %d relations",
                        msg.course_id[:8], len(new_concepts),
                        len(snapshot["concepts"]), len(snapshot["relations"]))
        finally:
            conn.close()

    def _step(self, msg: IngestMessage, status: VersionStatus,
              name: str, pct: int) -> None:
        """Advance version status and async_job progress together."""
        self.repo.update_version_status(msg.material_version_id, status)
        self.repo.update_job(msg.job_id, status=JobStatus.RUNNING,
                             step_name=name, progress_pct=pct)
