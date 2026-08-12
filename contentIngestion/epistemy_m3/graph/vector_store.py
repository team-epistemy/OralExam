"""Qdrant vector store for concept deduplication, subgraph search, and assessment mapping."""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance, VectorParams, PointStruct,
        Filter, FieldCondition, MatchValue,
        UpdateStatus,
    )
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer
    ST_AVAILABLE = True
except ImportError:
    ST_AVAILABLE = False

import logging

logger = logging.getLogger(__name__)


# -- Collection definitions ---------------------------------------------------

COLLECTIONS = {
    "concept_nodes": {
        "description": "Concept-level embeddings for deduplication and clustering",
        "vector_size": 384,
        "distance": "Cosine",
    },
    "concept_subgraphs": {
        "description": "Subgraph neighborhood embeddings for CG hop-distance scoring",
        "vector_size": 384,
        "distance": "Cosine",
    },
    "assessment_items": {
        "description": "Assessment-level embeddings for EDS prediction",
        "vector_size": 384,
        "distance": "Cosine",
    },
}


# -- Payload schemas ----------------------------------------------------------

@dataclass
class ConceptNodePayload:
    node_id: str
    org_id: str
    course_id: str
    label: str
    domain: str
    graph_type: str
    status: str
    depth_level: Optional[int] = None
    abstraction_level: float = 0.5
    corpus_id: Optional[str] = None
    version: int = 1


@dataclass
class ConceptSubgraphPayload:
    node_id: str
    org_id: str
    course_id: str
    domain: str
    graph_type: str
    neighbor_count: int
    hop_radius: int
    corpus_id: Optional[str] = None
    version: int = 1


@dataclass
class AssessmentPayload:
    node_id: str
    org_id: str
    course_id: str
    professor_id: str
    corpus_id: str
    assessment_type: str
    eds_score: Optional[float] = None
    eds_score_bucket: Optional[str] = None
    min_hop_depth: Optional[int] = None
    avg_hop_depth: Optional[float] = None
    version: int = 1


# -- Offline dummy encoder ----------------------------------------------------

class _DummyEncoder:
    """Offline encoder for testing without HuggingFace network access."""

    def encode(self, text, normalize_embeddings=False):
        if isinstance(text, list):
            v = np.random.rand(len(text), 384).astype("float32")
        else:
            v = np.random.rand(384).astype("float32")
        if normalize_embeddings:
            norms = np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9
            v = v / norms
        return v


# -- Vector Store Manager -----------------------------------------------------

class QdrantVectorStore:
    """
    Manages three Qdrant collections for Epistemy concept graph.

    All payloads carry org_id + course_id for tenant-scoped queries.

    Usage:
        vs = QdrantVectorStore()                         # in-memory, dummy encoder
        vs = QdrantVectorStore(host="localhost", port=6333, model_name="all-MiniLM-L6-v2")
    """

    def __init__(
        self,
        host: Optional[str] = None,
        port: int = 6333,
        model_name: str = "all-MiniLM-L6-v2",
        use_dummy_encoder: bool = False,
    ):
        if not QDRANT_AVAILABLE:
            raise ImportError("qdrant-client not installed. Run: pip install qdrant-client")

        # Client
        if host:
            self.client = QdrantClient(host=host, port=port)
        else:
            self.client = QdrantClient(":memory:")

        # Encoder
        if use_dummy_encoder or not ST_AVAILABLE:
            logger.info("Using dummy encoder (no real embeddings)")
            self.encoder = _DummyEncoder()
        else:
            try:
                self.encoder = SentenceTransformer(model_name)
            except Exception:
                logger.warning("SentenceTransformer load failed — using dummy encoder")
                self.encoder = _DummyEncoder()

        self._ensure_collections()

    def _ensure_collections(self) -> None:
        existing = {c.name for c in self.client.get_collections().collections}
        for name, cfg in COLLECTIONS.items():
            if name not in existing:
                distance = Distance.COSINE if cfg["distance"] == "Cosine" else Distance.DOT
                self.client.create_collection(
                    collection_name=name,
                    vectors_config=VectorParams(size=cfg["vector_size"], distance=distance),
                )

    def _embed(self, text: str) -> List[float]:
        vec = self.encoder.encode(text, normalize_embeddings=True)
        if hasattr(vec, "tolist"):
            return vec.tolist()
        return list(vec)

    def _embed_many(self, texts: List[str]) -> np.ndarray:
        vecs = self.encoder.encode(texts, normalize_embeddings=True)
        return np.array(vecs)

    # -- Concept node ops -----------------------------------------------------

    def upsert_concept_node(self, payload: ConceptNodePayload) -> bool:
        """Upsert a concept node embedding into the concept_nodes collection."""
        text = f"{payload.label}: {payload.label}"
        vec = self._embed(text)
        point = PointStruct(
            id=abs(hash(payload.node_id)) % (2**63),
            vector=vec,
            payload={
                "node_id": payload.node_id,
                "org_id": payload.org_id,
                "course_id": payload.course_id,
                "label": payload.label,
                "domain": payload.domain,
                "graph_type": payload.graph_type,
                "status": payload.status,
                "depth_level": payload.depth_level,
                "abstraction_level": payload.abstraction_level,
                "corpus_id": payload.corpus_id,
                "version": payload.version,
            }
        )
        result = self.client.upsert(collection_name="concept_nodes", points=[point])
        return result.status == UpdateStatus.COMPLETED

    def search_similar_concepts(
        self,
        label: str,
        domain: str,
        org_id: str,
        course_id: str,
        threshold: float = 0.92,
        top_k: int = 5,
    ) -> List[Dict]:
        """Find similar concepts for deduplication within tenant scope."""
        vec = self._embed(label)
        result = self.client.query_points(
            collection_name="concept_nodes",
            query=vec,
            query_filter=Filter(must=[
                FieldCondition(key="domain", match=MatchValue(value=domain)),
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="course_id", match=MatchValue(value=course_id)),
            ]),
            limit=top_k,
            with_payload=True,
        )
        return [
            {"node_id": r.payload["node_id"], "label": r.payload["label"], "score": r.score}
            for r in result.points
            if r.score >= threshold
        ]

    # -- Subgraph ops ---------------------------------------------------------

    def upsert_subgraph_embedding(
        self, payload: ConceptSubgraphPayload, neighbor_labels: List[str]
    ) -> bool:
        """Mean-pool embeddings of center + neighbor labels."""
        all_labels = [payload.node_id] + neighbor_labels
        vecs = self._embed_many(all_labels)
        mean_vec = vecs.mean(axis=0)
        norm = np.linalg.norm(mean_vec) + 1e-9
        mean_vec = (mean_vec / norm).tolist()

        point = PointStruct(
            id=abs(hash(f"sg_{payload.node_id}")) % (2**63),
            vector=mean_vec,
            payload={
                "node_id": payload.node_id,
                "org_id": payload.org_id,
                "course_id": payload.course_id,
                "domain": payload.domain,
                "graph_type": payload.graph_type,
                "neighbor_count": payload.neighbor_count,
                "hop_radius": payload.hop_radius,
                "corpus_id": payload.corpus_id,
                "version": payload.version,
            }
        )
        result = self.client.upsert(collection_name="concept_subgraphs", points=[point])
        return result.status == UpdateStatus.COMPLETED

    def search_similar_subgraphs(
        self, center_label: str, domain: str, org_id: str, course_id: str, top_k: int = 5
    ) -> List[Dict]:
        """Search for similar subgraph neighborhoods."""
        vec = self._embed(center_label)
        result = self.client.query_points(
            collection_name="concept_subgraphs",
            query=vec,
            query_filter=Filter(must=[
                FieldCondition(key="domain", match=MatchValue(value=domain)),
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="course_id", match=MatchValue(value=course_id)),
            ]),
            limit=top_k,
            with_payload=True,
        )
        return [
            {"node_id": r.payload["node_id"], "score": r.score, **r.payload}
            for r in result.points
        ]

    # -- Assessment ops -------------------------------------------------------

    def upsert_assessment(self, payload: AssessmentPayload, content: str) -> bool:
        """Upsert an assessment embedding."""
        vec = self._embed(content)
        point = PointStruct(
            id=abs(hash(payload.node_id)) % (2**63),
            vector=vec,
            payload={
                "node_id": payload.node_id,
                "org_id": payload.org_id,
                "course_id": payload.course_id,
                "professor_id": payload.professor_id,
                "corpus_id": payload.corpus_id,
                "assessment_type": payload.assessment_type,
                "eds_score": payload.eds_score,
                "eds_score_bucket": payload.eds_score_bucket,
                "min_hop_depth": payload.min_hop_depth,
                "avg_hop_depth": payload.avg_hop_depth,
                "version": payload.version,
            }
        )
        result = self.client.upsert(collection_name="assessment_items", points=[point])
        return result.status == UpdateStatus.COMPLETED

    def search_similar_assessments(
        self, content: str, org_id: str, course_id: str, top_k: int = 5
    ) -> List[Dict]:
        """Search for similar assessments within tenant scope."""
        vec = self._embed(content)
        result = self.client.query_points(
            collection_name="assessment_items",
            query=vec,
            query_filter=Filter(must=[
                FieldCondition(key="org_id", match=MatchValue(value=org_id)),
                FieldCondition(key="course_id", match=MatchValue(value=course_id)),
            ]),
            limit=top_k,
            with_payload=True,
        )
        return [
            {"node_id": r.payload["node_id"], "score": r.score, **r.payload}
            for r in result.points
        ]

    def collection_stats(self) -> Dict[str, int]:
        """Return point counts per collection."""
        return {
            name: self.client.get_collection(name).points_count
            for name in COLLECTIONS
        }
