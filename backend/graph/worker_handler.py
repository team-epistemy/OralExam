"""
Worker handler for async graph build jobs.

Integrates with the existing SQS queue pattern (same as ingest jobs).
The handler:
  1. Downloads relevant chunks from Aurora for the course
  2. Runs concept extraction (LLM via Bedrock)
  3. Builds the graph in Kuzu (local temp directory)
  4. Uploads .kuzu file to S3
  5. Flips is_active atomically in graph_version
"""
from __future__ import annotations

import io
import os
import shutil
import logging
import tempfile
from typing import Optional, Dict, Any

import boto3

from backend.config import Settings
from backend.constants import LLM_MODEL_ID
from backend.graph.models import GraphBuildMessage, GraphBuildStatus, GraphVersion
from backend.graph.kuzu_store import KuzuSchemaManager
from backend.graph.vector_store import QdrantVectorStore
from backend.graph.metadata import GraphMetadataStore
from backend.graph.ingestion import IngestionPipeline
from backend.db.repository import Repository

logger = logging.getLogger(__name__)


class GraphBuildHandler:
    """
    Handles graph build messages from the SQS queue.

    Follows the same worker pattern as IngestWorker in async_jobs/worker.py:
    the worker loop calls handle(msg) and we do the heavy lifting.
    """

    def __init__(
        self,
        repo: Repository,
        metadata_store: GraphMetadataStore,
        vector_store: QdrantVectorStore,
        settings: Settings,
        bedrock_client=None,
        s3_client=None,
    ):
        self.repo = repo
        self.meta = metadata_store
        self.vs = vector_store
        self.settings = settings

        # Bedrock client for concept extraction
        self.bedrock_client = bedrock_client or boto3.client(
            "bedrock-runtime", region_name=settings.bedrock_region
        )
        # S3 client for uploading .kuzu files
        self.s3_client = s3_client or boto3.client(
            "s3", region_name=settings.region
        )

    def handle(self, msg: GraphBuildMessage) -> Dict[str, Any]:
        """
        Execute a full graph build job.

        Args:
            msg: The GraphBuildMessage from the queue.

        Returns:
            Dict with build results (node/edge counts, version_id, s3_key).

        Raises:
            Exception: Propagated to the worker loop for failure recording.
        """
        org_id = msg.org_id
        course_id = msg.course_id
        job_id = msg.job_id

        logger.info(
            "Starting graph build: job=%s org=%s course=%s domain=%s rebuild=%s",
            job_id[:8], org_id[:8], course_id[:8], msg.domain, msg.rebuild,
        )

        # 1. Create a new graph version record
        version_number = self.meta.next_version_number(org_id, course_id)
        s3_key = f"{org_id}/{course_id}/graph/{version_number}.kuzu"

        version = GraphVersion(
            org_id=org_id,
            course_id=course_id,
            graph_version=version_number,
            s3_key=s3_key,
            job_id=job_id,
            is_active=False,
        )
        self.meta.create_version(version)

        # 2. Download relevant chunks from Aurora for this course
        logger.info("Fetching chunks for course %s", course_id[:8])
        chunks_text = self._fetch_course_chunks(org_id, course_id)

        if not chunks_text:
            logger.warning("No chunks found for course %s — building empty graph", course_id[:8])
            self.meta.update_version(
                version.version_id,
                node_count=0,
                edge_count=0,
                validation_score=0.0,
            )
            self.meta.activate_version(version.version_id, org_id, course_id)
            return {
                "job_id": job_id,
                "version_id": version.version_id,
                "status": "succeeded_empty",
                "node_count": 0,
                "edge_count": 0,
            }

        # 3. Run concept extraction + graph build in a temp Kuzu directory
        tmp_dir = tempfile.mkdtemp(prefix="epistemy_graph_")
        try:
            kuzu_path = os.path.join(tmp_dir, "graph.db")
            kuzu_mgr = KuzuSchemaManager(db_path=kuzu_path)

            pipeline = IngestionPipeline(
                kuzu_mgr=kuzu_mgr,
                vector_store=self.vs,
                bedrock_client=self.bedrock_client,
                model_id=self._resolve_model_id(),
                mock_mode=False,
            )

            # Ingest all course content as a single corpus
            result = pipeline.ingest(
                text=chunks_text,
                domain=msg.domain,
                org_id=org_id,
                course_id=course_id,
            )

            node_count = result.get("concept_nodes", 0)
            edge_count = result.get("concept_edges", 0)

            # 4. Compute a validation score (ratio of connected components)
            validation_score = self._compute_validation_score(kuzu_mgr, org_id, course_id)

            # 5. Upload .kuzu database directory to S3 as a tar archive
            logger.info("Uploading graph to S3: %s", s3_key)
            self._upload_graph_to_s3(kuzu_path, s3_key)

            # 6. Update version metadata and flip is_active atomically
            self.meta.update_version(
                version.version_id,
                node_count=node_count,
                edge_count=edge_count,
                validation_score=validation_score,
                s3_key=s3_key,
            )
            self.meta.activate_version(version.version_id, org_id, course_id)

            logger.info(
                "Graph build complete: job=%s version=%d nodes=%d edges=%d score=%.3f",
                job_id[:8], version_number, node_count, edge_count, validation_score,
            )

            return {
                "job_id": job_id,
                "version_id": version.version_id,
                "graph_version": version_number,
                "s3_key": s3_key,
                "node_count": node_count,
                "edge_count": edge_count,
                "validation_score": validation_score,
                "status": "succeeded",
            }

        finally:
            # Clean up temp directory
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _fetch_course_chunks(self, org_id: str, course_id: str) -> str:
        """
        Fetch all text chunks for a course from the repository (Aurora).
        Concatenates them into a single text block for ingestion.
        """
        self.repo.set_tenant(org_id)

        # Get all materials for the course
        materials = self.repo.list_materials(course_id)
        if not materials:
            return ""

        all_text_parts = []
        for material in materials:
            if not material.current_version_id:
                continue
            # Fetch chunks for the current version
            try:
                # Use the repository's chunk access (same pattern as ingest pipeline)
                chunk_count = self.repo.count_chunks(material.current_version_id)
                if chunk_count > 0:
                    # The repository stores chunks; we pull them via a query
                    chunks = self._get_chunks_for_version(
                        org_id, material.current_version_id
                    )
                    all_text_parts.extend(chunks)
            except Exception as e:
                logger.warning(
                    "Failed to fetch chunks for version %s: %s",
                    material.current_version_id[:8], e,
                )

        return "\n\n".join(all_text_parts)

    def _get_chunks_for_version(self, org_id: str, version_id: str) -> list:
        """Retrieve chunk texts for a material version from the DB."""
        # The repository protocol does not expose raw chunk text retrieval,
        # so we query directly. This matches the existing pattern in the
        # ingest pipeline where chunks are accessed through the DB layer.
        try:
            # Access via the underlying store's query mechanism
            # This works for both MemoryStore and PostgresStore
            if hasattr(self.repo, "_chunks"):
                # In-memory store
                return [
                    c.text for c in self.repo._chunks.values()
                    if c.material_version_id == version_id
                ]
            elif hasattr(self.repo, "conn"):
                # Postgres store
                import psycopg2.extras
                with self.repo.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT text FROM chunk WHERE material_version_id = %s "
                        "AND org_id = %s ORDER BY chunk_index",
                        (version_id, org_id),
                    )
                    return [row["text"] for row in cur.fetchall()]
            else:
                return []
        except Exception as e:
            logger.warning("Chunk retrieval failed for version %s: %s", version_id[:8], e)
            return []

    def _compute_validation_score(
        self, kuzu_mgr: KuzuSchemaManager, org_id: str, course_id: str
    ) -> float:
        """
        Compute a validation score for the built graph.

        Score = (connected_nodes / total_nodes) * (1 - isolated_ratio)
        A higher score means a more interconnected, higher-quality graph.
        """
        stats = kuzu_mgr.get_stats(org_id, course_id)
        total_nodes = stats.get("concept_nodes", 0)
        total_edges = stats.get("concept_edges", 0)

        if total_nodes == 0:
            return 0.0

        # Simple validation: edge-to-node ratio normalized
        # A well-connected graph has roughly 1.5-3x edges per node
        edge_ratio = min(1.0, total_edges / (total_nodes * 2.0))

        # Check for isolated nodes
        try:
            res = kuzu_mgr.conn.execute("""
                MATCH (n:Concept)
                WHERE n.org_id = $org_id AND n.course_id = $course_id
                  AND NOT EXISTS {
                    MATCH (n)-[:ConceptEdge]-()
                  }
                RETURN count(n)
            """, {"org_id": org_id, "course_id": course_id})
            isolated_count = res.get_next()[0]
        except Exception:
            isolated_count = 0

        connected_ratio = 1.0 - (isolated_count / total_nodes) if total_nodes > 0 else 0.0

        return round((edge_ratio * 0.5 + connected_ratio * 0.5), 4)

    def _upload_graph_to_s3(self, kuzu_path: str, s3_key: str) -> None:
        """
        Upload the Kuzu database directory to S3 as a tar.gz archive.
        The .kuzu extension in the key is a logical name; actual upload is .tar.gz.
        """
        import tarfile

        archive_key = s3_key.replace(".kuzu", ".tar.gz")
        buf = io.BytesIO()

        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(kuzu_path, arcname="graph.db")

        buf.seek(0)
        self.s3_client.put_object(
            Bucket=self.settings.bucket,
            Key=archive_key,
            Body=buf.getvalue(),
            ContentType="application/gzip",
            ServerSideEncryption="aws:kms",
        )
        logger.info("Uploaded graph archive to s3://%s/%s", self.settings.bucket, archive_key)

    def _resolve_model_id(self) -> str:
        """Resolve the Bedrock model ID from settings or default."""
        return getattr(self.settings, "llm_model", LLM_MODEL_ID)


# -- Queue adapter for GraphBuildMessage --------------------------------------

class GraphBuildQueue:
    """
    SQS queue adapter that speaks GraphBuildMessage instead of IngestMessage.
    Wraps the same SQS client pattern from async_jobs/queue.py.
    """

    def __init__(self, client, queue_url: str):
        self.client = client
        self.queue_url = queue_url
        self._receipts: Dict[str, str] = {}

    def send(self, message: GraphBuildMessage) -> None:
        """Send a graph build message to the queue."""
        self.client.send_message(
            QueueUrl=self.queue_url,
            MessageBody=message.model_dump_json(),
            MessageGroupId=f"{message.org_id}:{message.course_id}",
        )

    def receive(self) -> Optional[GraphBuildMessage]:
        """Long-poll for one message; cache its receipt for ack."""
        resp = self.client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=20,
        )
        records = resp.get("Messages", [])
        if not records:
            return None
        return self._decode(records[0])

    def _decode(self, record: dict) -> GraphBuildMessage:
        """Parse the body and remember the receipt handle for ack."""
        import json
        msg = GraphBuildMessage(**json.loads(record["Body"]))
        self._receipts[msg.job_id] = record["ReceiptHandle"]
        return msg

    def ack(self, message: GraphBuildMessage) -> None:
        """Delete the message so SQS does not redeliver it."""
        handle = self._receipts.pop(message.job_id, None)
        if handle:
            self.client.delete_message(
                QueueUrl=self.queue_url, ReceiptHandle=handle
            )


class InMemoryGraphBuildQueue:
    """FIFO queue for tests; ack is a no-op."""

    def __init__(self):
        from collections import deque
        self._items: deque = deque()

    def send(self, message: GraphBuildMessage) -> None:
        self._items.append(message)

    def receive(self) -> Optional[GraphBuildMessage]:
        return self._items.popleft() if self._items else None

    def ack(self, message: GraphBuildMessage) -> None:
        return None


# -- Worker loop (same pattern as IngestWorker) --------------------------------

class GraphBuildWorker:
    """
    Consumes graph build messages and runs the handler with failure handling.
    Same pattern as async_jobs/worker.py IngestWorker.
    """

    def __init__(self, handler: GraphBuildHandler, queue):
        self.handler = handler
        self.queue = queue
        self._running = True

    def install_signals(self) -> None:
        """SIGTERM lets the current message finish, then the loop exits."""
        import signal
        signal.signal(signal.SIGTERM, self._stop)

    def _stop(self, *_args) -> None:
        self._running = False

    def run_forever(self) -> None:
        """Drain the queue until stopped, handling one message at a time."""
        while self._running:
            msg = self.queue.receive()
            if msg:
                self._handle_message(msg)

    def _handle_message(self, msg: GraphBuildMessage) -> None:
        """Run the handler; record failure and always ack the message."""
        try:
            self.handler.handle(msg)
        except Exception as exc:
            logger.error(
                "Graph build failed: job=%s error=%s", msg.job_id[:8], exc,
                exc_info=True,
            )
            # Update the version record to reflect failure
            try:
                versions = self.handler.meta.list_versions(msg.org_id, msg.course_id)
                for v in versions:
                    if v.job_id == msg.job_id:
                        self.handler.meta.update_version(
                            v.version_id, validation_score=-1.0
                        )
                        break
            except Exception:
                pass
        finally:
            self.queue.ack(msg)
