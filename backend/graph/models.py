"""Concept graph Pydantic schemas — tenant-isolated with org_id + course_id on all models."""

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, Field


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# -- Enums -------------------------------------------------------------------

class EdgeRelation(str, Enum):
    PREREQUISITE_FOR = "PREREQUISITE_FOR"
    ENABLES = "ENABLES"
    IS_A = "IS_A"
    PART_OF = "PART_OF"
    APPLIED_IN = "APPLIED_IN"
    CO_REQUIRED_WITH = "CO_REQUIRED_WITH"
    CONTRASTS_WITH = "CONTRASTS_WITH"
    INSTANTIATES = "INSTANTIATES"


class NodeStatus(str, Enum):
    ACTIVE = "active"
    DRAFT = "draft"
    DEPRECATED = "deprecated"


class GraphType(str, Enum):
    KG = "kg"  # Knowledge Graph — universal domain concepts
    CG = "cg"  # Context Graph  — course/corpus-scoped subgraph


class DepthLevel(int, Enum):
    SURFACE = 1
    APPLIED = 2
    CAUSAL = 3
    AMBIGUOUS = 4


class GraphBuildStatus(str, Enum):
    QUEUED = "queued"
    EXTRACTING = "extracting"
    BUILDING = "building"
    EMBEDDING = "embedding"
    UPLOADING = "uploading"
    VALIDATING = "validating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# -- Core Node / Edge models --------------------------------------------------

class ConceptNode(BaseModel):
    """A concept in the knowledge graph, scoped to org + course."""

    node_id: str = Field(default_factory=_uuid)
    org_id: str
    course_id: str
    label: str
    definition: str
    domain: str
    graph_type: GraphType = GraphType.KG
    aliases: List[str] = Field(default_factory=list)
    abstraction_level: float = 0.5  # 0 = concrete, 1 = abstract
    depth_level: Optional[int] = None  # topological depth from roots
    status: NodeStatus = NodeStatus.ACTIVE
    corpus_id: Optional[str] = None
    version: int = 1


class ConceptEdge(BaseModel):
    """A directed relationship between two concepts."""

    edge_id: str = Field(default_factory=_uuid)
    org_id: str
    course_id: str
    src_id: str
    dst_id: str
    edge_type: EdgeRelation
    confidence: float = 1.0
    corpus_id: Optional[str] = None
    version: int = 1


class AssessmentNode(BaseModel):
    """An assessment item linked to required concepts."""

    node_id: str = Field(default_factory=_uuid)
    org_id: str
    course_id: str
    content: str
    professor_id: str
    corpus_id: str
    assessment_type: str = "explanatory"
    depth_level: DepthLevel = DepthLevel.SURFACE
    required_concept_ids: List[str] = Field(default_factory=list)
    version: int = 1


# -- Ingestion models ---------------------------------------------------------

class TextChunk(BaseModel):
    """A text chunk produced by the sentence-aware chunker."""

    chunk_id: str = Field(default_factory=_uuid)
    org_id: str
    course_id: str
    corpus_id: str
    chunk_index: int
    text: str
    content_hash: str


class ExtractedConcept(BaseModel):
    """Raw concept output from the LLM concept extractor."""

    label: str
    definition: str
    aliases: List[str] = Field(default_factory=list)
    abstraction_level: float = 0.5


class ExtractedRelation(BaseModel):
    """Raw relation output from the LLM concept extractor."""

    src: str  # concept label
    dst: str  # concept label
    edge_type: str
    confidence: float = 0.9


# -- EDS Scoring models -------------------------------------------------------

class EDSComponents(BaseModel):
    """Epistemic Depth Score components and final score."""

    assessment_id: str
    org_id: str
    course_id: str

    # Graph-structural signals
    required_concept_ids: List[str] = Field(default_factory=list)
    min_hop_depth: int = 0
    max_hop_depth: int = 0
    avg_hop_depth: float = 0.0
    unique_chain_count: int = 0

    # Semantic signals
    avg_abstraction_level: float = 0.5
    domain_breadth: int = 1

    # LLM resistance probe
    llm_direct_score: Optional[float] = None
    llm_paraphrase_score: Optional[float] = None
    probe_model: Optional[str] = None

    # Final score
    eds_score: float = 0.0

    @property
    def eds_score_bucket(self) -> str:
        if self.eds_score >= 0.7:
            return "high"
        elif self.eds_score >= 0.4:
            return "medium"
        return "low"


# -- Graph version management -------------------------------------------------

class GraphVersion(BaseModel):
    """Tracks a built graph version in Aurora — one active version per course."""

    version_id: str = Field(default_factory=_uuid)
    org_id: str
    course_id: str
    graph_version: int = 1
    s3_key: str = ""  # e.g. {org_id}/{course_id}/graph/{version}.kuzu
    node_count: int = 0
    edge_count: int = 0
    validation_score: Optional[float] = None
    job_id: Optional[str] = None
    is_active: bool = False
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# -- SQS message for graph build jobs ----------------------------------------

class GraphBuildMessage(BaseModel):
    """SQS payload for triggering an async graph build job."""

    job_id: str = Field(default_factory=_uuid)
    org_id: str
    course_id: str
    domain: str
    rebuild: bool = False  # True = full rebuild, False = incremental
    triggered_by: Optional[str] = None
