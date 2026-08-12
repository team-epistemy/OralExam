"""
Concept graph ingestion pipeline — tenant-isolated, Bedrock-backed.

Corpus text -> TextChunker -> ConceptExtractor (Bedrock Claude)
-> ConceptDeduplicator -> Kuzu upsert -> Qdrant embed -> topological depths.
"""
from __future__ import annotations

import re
import hashlib
import json
import uuid
import logging
from typing import List, Tuple, Dict, Optional, Any

from epistemy_m3.constants import LLM_MODEL_ID
from epistemy_m3.graph.models import (
    ConceptNode, ConceptEdge, TextChunk,
    ExtractedConcept, ExtractedRelation,
    EdgeRelation, NodeStatus, GraphType,
)
from epistemy_m3.graph.kuzu_store import KuzuSchemaManager
from epistemy_m3.graph.vector_store import (
    QdrantVectorStore, ConceptNodePayload, ConceptSubgraphPayload,
)

logger = logging.getLogger(__name__)


# -- Text Chunker -------------------------------------------------------------

class TextChunker:
    """
    Sentence-aware sliding window chunker.
    Splits text into overlapping chunks for concept extraction.
    """

    def __init__(
        self,
        chunk_size: int = 512,   # approx tokens (chars/4)
        overlap: int = 64,
        min_chunk: int = 64,
    ):
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.min_chunk = min_chunk

    def chunk(
        self, text: str, org_id: str, course_id: str, corpus_id: str
    ) -> List[TextChunk]:
        """Split text into overlapping TextChunks with tenant context."""
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        if not sentences:
            return []

        chunks: List[TextChunk] = []
        buf: List[str] = []
        buf_len = 0
        char_limit = self.chunk_size * 4  # approx char budget

        for sent in sentences:
            sent_len = len(sent)
            if buf_len + sent_len > char_limit and buf:
                chunk_text = " ".join(buf)
                content_hash = hashlib.sha256(chunk_text.encode()).hexdigest()
                chunks.append(TextChunk(
                    org_id=org_id,
                    course_id=course_id,
                    corpus_id=corpus_id,
                    chunk_index=len(chunks),
                    text=chunk_text,
                    content_hash=content_hash,
                ))
                # Keep overlap
                overlap_chars = self.overlap * 4
                overlap_buf: List[str] = []
                overlap_len = 0
                for s in reversed(buf):
                    if overlap_len + len(s) > overlap_chars:
                        break
                    overlap_buf.insert(0, s)
                    overlap_len += len(s)
                buf = overlap_buf
                buf_len = overlap_len

            buf.append(sent)
            buf_len += sent_len

        # Flush remainder
        if buf:
            chunk_text = " ".join(buf)
            if len(chunk_text) >= self.min_chunk:
                content_hash = hashlib.sha256(chunk_text.encode()).hexdigest()
                chunks.append(TextChunk(
                    org_id=org_id,
                    course_id=course_id,
                    corpus_id=corpus_id,
                    chunk_index=len(chunks),
                    text=chunk_text,
                    content_hash=content_hash,
                ))

        return chunks


# -- Concept Extractor (Bedrock Claude) ---------------------------------------

SYSTEM_PROMPT = """You are an expert knowledge graph builder for educational content.

Given a text chunk, extract:
1. Key concepts (technical terms, principles, methods, frameworks)
2. Directed prerequisite/dependency relations between them

Return ONLY valid JSON in this exact structure:
{
  "concepts": [
    {
      "label": "gradient descent",
      "definition": "Iterative optimization algorithm that minimizes a loss function by following the negative gradient.",
      "aliases": ["GD", "steepest descent"],
      "abstraction_level": 0.5
    }
  ],
  "relations": [
    {
      "src": "learning rate",
      "dst": "gradient descent",
      "edge_type": "CO_REQUIRED_WITH",
      "confidence": 0.90
    }
  ]
}

Edge types: PREREQUISITE_FOR, ENABLES, IS_A, PART_OF, APPLIED_IN, CO_REQUIRED_WITH, CONTRASTS_WITH, INSTANTIATES
abstraction_level: float 0.0 (concrete/operational) to 1.0 (abstract/theoretical)
Extract 3-8 concepts per chunk. Only return JSON."""


class ConceptExtractor:
    """
    Extracts concepts and relations from text chunks using Bedrock Claude.

    Uses boto3 bedrock-runtime for LLM calls (not the Anthropic client)
    to align with the existing M3 infrastructure pattern.
    """

    def __init__(
        self,
        bedrock_client=None,
        model_id: str = LLM_MODEL_ID,
        mock_mode: bool = False,
    ):
        self.bedrock_client = bedrock_client
        self.model_id = model_id
        self.mock_mode = mock_mode or (bedrock_client is None)
        self._mock_idx = 0

    def extract(
        self, chunk_text: str, domain: str
    ) -> Tuple[List[ExtractedConcept], List[ExtractedRelation]]:
        """Extract concepts and relations from a text chunk."""
        if self.mock_mode:
            return self._mock_extract()
        return self._bedrock_extract(chunk_text, domain)

    def _bedrock_extract(
        self, text: str, domain: str
    ) -> Tuple[List[ExtractedConcept], List[ExtractedRelation]]:
        """Production LLM extraction via Bedrock converse API."""
        try:
            response = self.bedrock_client.converse(
                modelId=self.model_id,
                system=[{"text": SYSTEM_PROMPT}],
                messages=[{
                    "role": "user",
                    "content": [{"text": f"Domain: {domain}\n\n{text}"}],
                }],
                inferenceConfig={"maxTokens": 2000, "temperature": 0.1},
            )

            raw = response["output"]["message"]["content"][0]["text"].strip()
            # Strip markdown fences if present
            raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`")
            data = json.loads(raw)

            concepts = [ExtractedConcept(**c) for c in data.get("concepts", [])]
            relations = [ExtractedRelation(**r) for r in data.get("relations", [])]
            return concepts, relations

        except Exception as e:
            logger.warning("Bedrock concept extraction failed: %s — returning empty", e)
            return [], []

    def _mock_extract(
        self,
    ) -> Tuple[List[ExtractedConcept], List[ExtractedRelation]]:
        """Deterministic mock for testing without LLM calls."""
        mock_data = [
            {
                "concepts": [
                    {"label": "gradient descent", "definition": "Iterative optimization via steepest descent", "aliases": ["GD"], "abstraction_level": 0.5},
                    {"label": "loss function", "definition": "Measures discrepancy between predictions and targets", "aliases": ["objective function"], "abstraction_level": 0.4},
                    {"label": "learning rate", "definition": "Scalar controlling gradient step size", "aliases": ["step size"], "abstraction_level": 0.3},
                ],
                "relations": [
                    {"src": "gradient descent", "dst": "loss function", "edge_type": "PREREQUISITE_FOR", "confidence": 0.95},
                    {"src": "learning rate", "dst": "gradient descent", "edge_type": "CO_REQUIRED_WITH", "confidence": 0.90},
                ]
            },
            {
                "concepts": [
                    {"label": "backpropagation", "definition": "Computes gradients through neural networks via chain rule", "aliases": ["backprop"], "abstraction_level": 0.6},
                    {"label": "chain rule", "definition": "Calculus rule for differentiating composite functions", "aliases": [], "abstraction_level": 0.5},
                    {"label": "partial derivative", "definition": "Derivative with respect to one variable", "aliases": [], "abstraction_level": 0.5},
                ],
                "relations": [
                    {"src": "chain rule", "dst": "backpropagation", "edge_type": "PREREQUISITE_FOR", "confidence": 0.98},
                    {"src": "partial derivative", "dst": "chain rule", "edge_type": "PREREQUISITE_FOR", "confidence": 0.92},
                ]
            },
        ]
        data = mock_data[self._mock_idx % len(mock_data)]
        self._mock_idx += 1
        concepts = [ExtractedConcept(**c) for c in data["concepts"]]
        relations = [ExtractedRelation(**r) for r in data["relations"]]
        return concepts, relations


# -- Concept Deduplicator -----------------------------------------------------

class ConceptDeduplicator:
    """
    Prevents duplicate concept nodes using vector similarity.
    Returns the existing node_id if a near-duplicate exists, else uses fallback_id.
    """

    def __init__(self, vector_store: QdrantVectorStore, threshold: float = 0.92):
        self.vs = vector_store
        self.threshold = threshold
        self._label_to_id: Dict[str, str] = {}  # session-local cache

    def get_or_create_id(
        self, label: str, domain: str, org_id: str, course_id: str, fallback_id: str
    ) -> str:
        """Resolve a concept label to a node_id, deduplicating via embeddings."""
        key = f"{org_id}::{course_id}::{domain}::{label.lower()}"
        if key in self._label_to_id:
            return self._label_to_id[key]

        matches = self.vs.search_similar_concepts(
            label, domain, org_id, course_id, threshold=self.threshold
        )
        if matches:
            node_id = matches[0]["node_id"]
        else:
            node_id = fallback_id

        self._label_to_id[key] = node_id
        return node_id


# -- Ingestion Pipeline -------------------------------------------------------

class IngestionPipeline:
    """
    Full corpus -> graph pipeline with tenant isolation.

    Steps:
      1. Chunk text
      2. Extract concepts + relations per chunk (Bedrock Claude)
      3. Deduplicate concepts via vector similarity
      4. Upsert ConceptNodes and ConceptEdges into Kuzu
      5. Embed concept nodes into Qdrant (concept_nodes collection)
      6. Build k-hop subgraph embeddings (concept_subgraphs collection)
      7. Compute topological depths
    """

    def __init__(
        self,
        kuzu_mgr: KuzuSchemaManager,
        vector_store: QdrantVectorStore,
        bedrock_client=None,
        model_id: str = LLM_MODEL_ID,
        mock_mode: bool = False,
    ):
        self.kg = kuzu_mgr
        self.vs = vector_store
        self.chunker = TextChunker()
        self.extractor = ConceptExtractor(
            bedrock_client=bedrock_client, model_id=model_id, mock_mode=mock_mode
        )
        self.deduplicator = ConceptDeduplicator(vector_store)

    def ingest(
        self,
        text: str,
        domain: str,
        org_id: str,
        course_id: str,
        corpus_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Ingest a text corpus into the concept graph.

        Args:
            text: Full document text to ingest.
            domain: Knowledge domain (e.g. "machine_learning").
            org_id: Tenant organization ID.
            course_id: Course ID within the org.
            corpus_id: Optional corpus tracking ID. Auto-generated if not provided.

        Returns:
            Dict with ingestion stats: nodes/edges upserted, chunks processed, etc.
        """
        if corpus_id is None:
            corpus_id = str(uuid.uuid4())

        logger.info(
            "Ingesting corpus=%s for org=%s course=%s domain=%s",
            corpus_id[:8], org_id[:8], course_id[:8], domain,
        )

        # 1. Chunk
        chunks = self.chunker.chunk(text, org_id, course_id, corpus_id)
        logger.info("Chunked into %d pieces", len(chunks))

        # 2-4. Extract -> dedup -> collect
        all_nodes: Dict[str, ConceptNode] = {}
        all_edges: List[ConceptEdge] = []
        label_to_node: Dict[str, ConceptNode] = {}

        for chunk in chunks:
            concepts, relations = self.extractor.extract(chunk.text, domain)

            for ec in concepts:
                label_key = ec.label.lower()
                if label_key not in label_to_node:
                    temp_id = str(uuid.uuid4())
                    resolved_id = self.deduplicator.get_or_create_id(
                        ec.label, domain, org_id, course_id, temp_id
                    )
                    node = ConceptNode(
                        node_id=resolved_id,
                        org_id=org_id,
                        course_id=course_id,
                        label=ec.label,
                        definition=ec.definition,
                        domain=domain,
                        aliases=ec.aliases,
                        abstraction_level=ec.abstraction_level,
                        corpus_id=corpus_id,
                    )
                    label_to_node[label_key] = node
                    all_nodes[resolved_id] = node

            for er in relations:
                src_node = label_to_node.get(er.src.lower())
                dst_node = label_to_node.get(er.dst.lower())
                if src_node and dst_node:
                    try:
                        edge_type = EdgeRelation(er.edge_type)
                    except ValueError:
                        edge_type = EdgeRelation.ENABLES
                    edge = ConceptEdge(
                        org_id=org_id,
                        course_id=course_id,
                        src_id=src_node.node_id,
                        dst_id=dst_node.node_id,
                        edge_type=edge_type,
                        confidence=er.confidence,
                        corpus_id=corpus_id,
                    )
                    all_edges.append(edge)

        # 5. Upsert to Kuzu
        logger.info("Upserting %d nodes, %d edges to Kuzu", len(all_nodes), len(all_edges))
        for node in all_nodes.values():
            self.kg.upsert_concept(node)
        for edge in all_edges:
            self.kg.upsert_concept_edge(edge)

        # 6. Embed into Qdrant
        logger.info("Embedding concept nodes into Qdrant")
        for node in all_nodes.values():
            payload = ConceptNodePayload(
                node_id=node.node_id,
                org_id=node.org_id,
                course_id=node.course_id,
                label=node.label,
                domain=node.domain,
                graph_type=node.graph_type.value,
                status=node.status.value,
                abstraction_level=node.abstraction_level,
                corpus_id=node.corpus_id,
            )
            self.vs.upsert_concept_node(payload)

        # 7. Subgraph embeddings (k=2)
        logger.info("Building subgraph embeddings")
        for node in all_nodes.values():
            neighbors = self.kg.get_k_hop_neighborhood(
                node.node_id, org_id, course_id, k=2
            )
            neighbor_labels = [n["label"] for n in neighbors]
            sg_payload = ConceptSubgraphPayload(
                node_id=node.node_id,
                org_id=org_id,
                course_id=course_id,
                domain=node.domain,
                graph_type=node.graph_type.value,
                neighbor_count=len(neighbors),
                hop_radius=2,
                corpus_id=node.corpus_id,
            )
            self.vs.upsert_subgraph_embedding(sg_payload, neighbor_labels)

        # 8. Topological depths
        logger.info("Computing topological depths")
        depths = self.kg.compute_topological_depths(domain, org_id, course_id)

        stats = self.kg.get_stats(org_id, course_id)
        logger.info(
            "Done. Graph: %d nodes, %d edges",
            stats["concept_nodes"], stats["concept_edges"],
        )

        return {
            "corpus_id": corpus_id,
            "org_id": org_id,
            "course_id": course_id,
            "chunks_processed": len(chunks),
            "nodes_upserted": len(all_nodes),
            "edges_upserted": len(all_edges),
            "topological_depths": len(depths),
            **stats,
        }
