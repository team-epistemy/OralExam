"""
Epistemic Depth Score (EDS) scorer with CausalProbe — tenant-isolated, Bedrock-backed.

Scoring formula (v1.0-alpha):
    graph_depth_score  = sigmoid(avg_hop_depth / 3.0)      * 0.40
    chain_count_score  = sigmoid(unique_chain_count / 5.0)  * 0.25
    abstraction_score  = avg_abstraction_level              * 0.20
    llm_resistance     = 1 - avg(llm_probe_scores)         * 0.15

Score buckets: LOW < 0.4 <= MEDIUM < 0.7 <= HIGH
"""
from __future__ import annotations

import math
import logging
from typing import Optional, List, Dict, Any

from backend.graph.models import AssessmentNode, EDSComponents
from backend.graph.kuzu_store import KuzuSchemaManager
from backend.graph.vector_store import QdrantVectorStore
from backend.graph.metadata import GraphMetadataStore

logger = logging.getLogger(__name__)


# -- CausalProbe --------------------------------------------------------------

class CausalProbe:
    """
    LLM-resistance probe using Bedrock Claude.

    Scores a student response on a scale [0, 1]:
      0.0 = deep causal reasoning, mechanistic understanding
      0.5 = mixed surface + causal
      1.0 = pure surface: definitions, memorised phrases, no causal chain
    """

    def __init__(
        self,
        bedrock_client=None,
        model_id: str = "anthropic.claude-sonnet-4-20250514-v1:0",
    ):
        self.bedrock_client = bedrock_client
        self.model_id = model_id

    def score(self, question: str, response: str) -> float:
        """Score a student response for surface-level vs causal reasoning."""
        if not self.bedrock_client:
            return self._heuristic_score(response)
        return self._bedrock_score(question, response)

    def _heuristic_score(self, response: str) -> float:
        """Fallback heuristic: count causal connectors as a proxy."""
        causal_markers = [
            "because", "therefore", "thus", "hence", "causes", "leads to",
            "results in", "due to", "as a result", "consequently", "implies",
            "mechanism", "underlying", "fundamentally",
        ]
        text = response.lower()
        count = sum(text.count(m) for m in causal_markers)
        # More causal connectors -> lower surface score
        surface_score = max(0.0, 1.0 - (count / 10.0))
        return surface_score

    def _bedrock_score(self, question: str, response: str) -> float:
        """Score via Bedrock Claude converse API."""
        prompt = f"""You are evaluating whether a student response demonstrates
genuine causal understanding or surface-level pattern matching.

Question: {question}
Student response: {response}

Rate on a scale 0.0 to 1.0 where:
  0.0 = Deep causal reasoning, novel synthesis, clear mechanistic understanding
  0.5 = Mixed: some causal reasoning, some surface retrieval
  1.0 = Pure surface: definitions, memorised phrases, no causal chain

Respond with ONLY a single float (e.g. 0.3). No explanation."""

        try:
            resp = self.bedrock_client.converse(
                modelId=self.model_id,
                messages=[{
                    "role": "user",
                    "content": [{"text": prompt}],
                }],
                inferenceConfig={"maxTokens": 10, "temperature": 0.0},
            )
            raw = resp["output"]["message"]["content"][0]["text"].strip()
            return float(raw)
        except Exception as e:
            logger.warning("Bedrock CausalProbe failed: %s — returning 0.5", e)
            return 0.5


# -- EDS Scorer ---------------------------------------------------------------

def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class EDSScorer:
    """
    Computes Epistemic Depth Score for a student response or assessment node.

    Weighted formula (v1.0-alpha):
        graph_depth  = sigmoid(avg_hop_depth / 3.0)     * 0.40
        chain_count  = sigmoid(chain_count / 5.0)        * 0.25
        abstraction  = avg_abstraction_level             * 0.20
        llm_resist   = 1 - avg(llm_probe_scores)        * 0.15

    All operations are tenant-scoped by org_id + course_id.
    """

    WEIGHTS = {
        "graph_depth": 0.40,
        "chain_count": 0.25,
        "abstraction": 0.20,
        "llm_resistance": 0.15,
    }

    def __init__(
        self,
        kuzu_mgr: KuzuSchemaManager,
        vector_store: QdrantVectorStore,
        metadata_store: Optional[GraphMetadataStore] = None,
        bedrock_client=None,
        model_id: str = "anthropic.claude-sonnet-4-20250514-v1:0",
    ):
        self.kg = kuzu_mgr
        self.vs = vector_store
        self.meta = metadata_store
        self.probe = CausalProbe(bedrock_client=bedrock_client, model_id=model_id)

    def score_assessment(
        self,
        assessment: AssessmentNode,
        domain: str,
        run_llm_probe: bool = False,
        probe_model: str = "heuristic",
        student_response: Optional[str] = None,
    ) -> EDSComponents:
        """
        Main scoring entry point.

        Args:
            assessment: The assessment node to score (carries org_id + course_id).
            domain: Domain for concept lookup.
            run_llm_probe: Whether to run the CausalProbe scorer.
            probe_model: Label for the probe (stored in metadata).
            student_response: Used by CausalProbe (defaults to assessment content).

        Returns:
            Fully populated EDSComponents with eds_score set.
        """
        org_id = assessment.org_id
        course_id = assessment.course_id

        c = EDSComponents(
            assessment_id=assessment.node_id,
            org_id=org_id,
            course_id=course_id,
        )

        # 1. Map assessment -> required concepts via vector similarity
        required = self._map_to_concepts(assessment.content, domain, org_id, course_id)
        c.required_concept_ids = [r["node_id"] for r in required]

        if not c.required_concept_ids:
            logger.info("No concepts mapped for assessment %s", assessment.node_id[:8])
            c.eds_score = self._compute_score(c)
            return c

        # 2. Graph-structural signals
        hop_depths: List[float] = []
        abstraction_levels: List[float] = []
        chain_count = 0

        for cid in c.required_concept_ids:
            prereqs = self.kg.get_prerequisites(cid, org_id, course_id, max_depth=5)
            if prereqs:
                depths = [float(p.get("hop_count", 1)) for p in prereqs]
                hop_depths.extend(depths)
                chain_count += len(depths)

            node = self.kg.get_concept_by_id(cid)
            if node:
                al = node.get("abstraction_level") or 0.5
                abstraction_levels.append(float(al))

        if hop_depths:
            c.min_hop_depth = int(min(hop_depths))
            c.max_hop_depth = int(max(hop_depths))
            c.avg_hop_depth = sum(hop_depths) / len(hop_depths)
        c.unique_chain_count = chain_count
        c.avg_abstraction_level = (
            sum(abstraction_levels) / len(abstraction_levels)
            if abstraction_levels else 0.5
        )

        # 3. LLM resistance probe
        if run_llm_probe:
            text_to_probe = student_response or assessment.content
            surface_score = self.probe.score(assessment.content, text_to_probe)
            c.llm_direct_score = surface_score
            c.probe_model = probe_model

        # 4. Compute weighted EDS
        c.eds_score = self._compute_score(c)

        # 5. Persist to metadata store
        if self.meta:
            self.meta.record_eds(
                assessment_id=assessment.node_id,
                student_id="anonymous",
                org_id=org_id,
                course_id=course_id,
                eds_score=c.eds_score,
                components={
                    "avg_hop_depth": c.avg_hop_depth,
                    "unique_chain_count": c.unique_chain_count,
                    "avg_abstraction_level": c.avg_abstraction_level,
                    "llm_direct_score": c.llm_direct_score,
                },
            )

        return c

    def _compute_score(self, c: EDSComponents) -> float:
        """Apply the weighted EDS formula."""
        w = self.WEIGHTS
        graph_depth = _sigmoid(c.avg_hop_depth / 3.0) * w["graph_depth"]
        chain = _sigmoid(c.unique_chain_count / 5.0) * w["chain_count"]
        abstraction = c.avg_abstraction_level * w["abstraction"]

        if c.llm_direct_score is not None:
            llm_resist = (1.0 - c.llm_direct_score) * w["llm_resistance"]
        else:
            # Redistribute weight to graph signals proportionally
            total_other = w["graph_depth"] + w["chain_count"] + w["abstraction"]
            scale = 1.0 / total_other
            graph_depth *= scale
            chain *= scale
            abstraction *= scale
            llm_resist = 0.0

        raw = graph_depth + chain + abstraction + llm_resist
        return round(min(1.0, max(0.0, raw)), 4)

    def _map_to_concepts(
        self, content: str, domain: str, org_id: str, course_id: str
    ) -> List[Dict]:
        """
        Find relevant concept nodes for an assessment via vector search.
        Falls back to Kuzu direct lookup if Qdrant returns nothing.
        """
        results = self.vs.search_similar_concepts(
            label=content[:200],
            domain=domain,
            org_id=org_id,
            course_id=course_id,
            threshold=0.3,  # low threshold for assessment mapping
            top_k=10,
        )
        if not results:
            # Fallback: pull concepts from Kuzu directly
            try:
                res = self.kg.conn.execute("""
                    MATCH (n:Concept)
                    WHERE n.domain = $domain AND n.org_id = $org_id AND n.course_id = $course_id
                    RETURN n.node_id, n.label, n.abstraction_level
                    LIMIT 10
                """, {"domain": domain, "org_id": org_id, "course_id": course_id})
                fallback = []
                while res.has_next():
                    row = res.get_next()
                    fallback.append({"node_id": row[0], "label": row[1], "score": 0.5})
                return fallback
            except Exception:
                return []
        return results

    def score_student_response(
        self,
        question: str,
        student_response: str,
        domain: str,
        org_id: str,
        course_id: str,
        professor_id: str = "anon",
        corpus_id: str = "default",
    ) -> EDSComponents:
        """
        Convenience method: wraps a raw student response in an AssessmentNode
        and scores it end-to-end.
        """
        assessment = AssessmentNode(
            org_id=org_id,
            course_id=course_id,
            content=question,
            professor_id=professor_id,
            corpus_id=corpus_id,
        )
        return self.score_assessment(
            assessment=assessment,
            domain=domain,
            run_llm_probe=True,
            student_response=student_response,
        )
