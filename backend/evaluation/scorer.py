"""M7 GraphScorer: maps claims to concept graph, computes EDS-based evaluation.

Adapted from concept-graph-uthiraingest/pipeline/eds_scorer.py for production
use within the M3 platform. Integrates with the claim extraction pipeline to
compute three scoring dimensions:

1. concept_coverage: fraction of expected concepts actually demonstrated
2. depth_score: EDS-style graph depth (hop depth, abstraction level)
3. coherence_score: whether claims form valid causal chains in the graph

Scoring formula (production v1):
    concept_coverage_score  = coverage_ratio                    * 0.35
    depth_score             = sigmoid(avg_hop_depth / 3.0)      * 0.35
    coherence_score         = valid_chains / total_chains       * 0.30

EDS buckets: LOW < 0.4 <= MEDIUM < 0.7 <= HIGH
"""
from __future__ import annotations
import math
import logging
from typing import List, Dict, Set, Optional, Any, Tuple

from backend.evaluation.models import (
    Claim, ClaimExtraction, ConceptCoverage, EDSBucket, ComponentScores,
)

logger = logging.getLogger(__name__)


def _sigmoid(x: float) -> float:
    """Standard sigmoid, clamped to avoid overflow."""
    x = max(-10.0, min(10.0, x))
    return 1.0 / (1.0 + math.exp(-x))


def _bucket(score: float) -> EDSBucket:
    """Map a 0-1 score to an EDS bucket."""
    if score < 0.4:
        return EDSBucket.LOW
    elif score < 0.7:
        return EDSBucket.MEDIUM
    return EDSBucket.HIGH


class GraphScorer:
    """Production EDS scorer: maps claims to the concept graph and scores.

    Computes:
    - concept_coverage: what fraction of expected concepts were hit
    - depth_score: EDS-style depth based on graph topology
    - coherence_score: do the claims form valid causal chains in the graph

    The final eds_score is a weighted combination of these three components.
    """

    WEIGHTS = {
        "concept_coverage": 0.35,
        "depth_score": 0.35,
        "coherence_score": 0.30,
    }

    def __init__(self, graph_store=None, vector_store=None):
        """Initialize the scorer.

        Args:
            graph_store: KuzuSchemaManager or compatible for graph queries.
            vector_store: QdrantVectorStore for semantic similarity fallback.
        """
        self.graph_store = graph_store
        self.vector_store = vector_store

    def score(
        self,
        claims: ClaimExtraction,
        expected_concept_ids: List[str],
        question_text: Optional[str] = None,
    ) -> Tuple[float, EDSBucket, ConceptCoverage, ComponentScores]:
        """Score a claim extraction against expected concepts.

        Args:
            claims: The extracted claims from the student answer.
            expected_concept_ids: Concept IDs the question targets.
            question_text: Original question (for context in scoring).

        Returns:
            Tuple of (eds_score, eds_bucket, concept_coverage, component_scores)
        """
        # 1. Compute concept coverage
        coverage = self._compute_coverage(claims, expected_concept_ids)

        # 2. Compute depth score from graph topology
        depth_score = self._compute_depth_score(claims, expected_concept_ids)

        # 3. Compute coherence score from causal chain validation
        coherence_score = self._compute_coherence(claims, expected_concept_ids)

        # 4. Weighted combination
        w = self.WEIGHTS
        eds_score = (
            coverage.coverage_ratio * w["concept_coverage"]
            + depth_score * w["depth_score"]
            + coherence_score * w["coherence_score"]
        )
        eds_score = round(min(1.0, max(0.0, eds_score)), 4)
        bucket = _bucket(eds_score)

        component_scores = ComponentScores(
            concept_coverage=round(coverage.coverage_ratio, 4),
            depth_score=round(depth_score, 4),
            coherence_score=round(coherence_score, 4),
            avg_eds=eds_score,
            turns_completed=1,
            turns_total=1,
        )

        return eds_score, bucket, coverage, component_scores

    def score_session(
        self,
        evaluations: List[Dict[str, Any]],
    ) -> Tuple[float, ComponentScores]:
        """Aggregate per-turn evaluations into a session-level grade.

        Args:
            evaluations: List of evaluation dicts with eds_score, concept_coverage, etc.

        Returns:
            Tuple of (final_score, component_scores)
        """
        if not evaluations:
            return 0.0, ComponentScores()

        total_coverage = 0.0
        total_depth = 0.0
        total_coherence = 0.0
        count = len(evaluations)

        for ev in evaluations:
            total_coverage += ev.get("concept_coverage", {}).get("coverage_ratio", 0.0)
            total_depth += ev.get("component_scores", {}).get("depth_score", 0.0)
            total_coherence += ev.get("component_scores", {}).get("coherence_score", 0.0)

        avg_coverage = total_coverage / count
        avg_depth = total_depth / count
        avg_coherence = total_coherence / count

        w = self.WEIGHTS
        final_score = (
            avg_coverage * w["concept_coverage"]
            + avg_depth * w["depth_score"]
            + avg_coherence * w["coherence_score"]
        )
        final_score = round(min(1.0, max(0.0, final_score)), 4)

        component_scores = ComponentScores(
            concept_coverage=round(avg_coverage, 4),
            depth_score=round(avg_depth, 4),
            coherence_score=round(avg_coherence, 4),
            avg_eds=final_score,
            turns_completed=count,
            turns_total=count,
        )

        return final_score, component_scores

    def _compute_coverage(
        self,
        claims: ClaimExtraction,
        expected_concept_ids: List[str],
    ) -> ConceptCoverage:
        """Determine which expected concepts were covered by the claims."""
        if not expected_concept_ids:
            return ConceptCoverage(coverage_ratio=1.0)

        expected = set(expected_concept_ids)
        covered: Set[str] = set()
        partial: Dict[str, float] = {}

        for claim in claims.claims:
            for cid in claim.concept_ids:
                if cid in expected:
                    # Weight by confidence and causal depth
                    weight = claim.confidence
                    if claim.is_causal:
                        weight = min(1.0, weight * 1.2)  # Bonus for causal
                    current = partial.get(cid, 0.0)
                    partial[cid] = min(1.0, current + weight)
                    if partial[cid] >= 0.5:
                        covered.add(cid)

        # Also try semantic matching if graph store is available
        if self.graph_store and len(covered) < len(expected):
            additional = self._semantic_concept_match(claims, expected - covered)
            covered.update(additional)
            for cid in additional:
                partial[cid] = partial.get(cid, 0.0) + 0.5

        missing = list(expected - covered)
        coverage_ratio = len(covered) / len(expected) if expected else 1.0

        return ConceptCoverage(
            expected_concepts=list(expected),
            covered_concepts=list(covered),
            missing_concepts=missing,
            coverage_ratio=round(coverage_ratio, 4),
            partial_coverage=partial,
        )

    def _compute_depth_score(
        self,
        claims: ClaimExtraction,
        concept_ids: List[str],
    ) -> float:
        """Compute EDS-style depth score from graph topology.

        Uses the prerequisite chain depth of covered concepts to determine
        how deeply the student engaged with the material hierarchy.
        """
        if not self.graph_store or not concept_ids:
            # Fallback: use causal ratio as a proxy for depth
            if claims.total_claims == 0:
                return 0.0
            return claims.causal_claims / claims.total_claims

        hop_depths: List[float] = []
        abstraction_levels: List[float] = []

        for cid in concept_ids:
            try:
                prereqs = self.graph_store.get_prerequisites(cid, max_depth=5)
                if prereqs:
                    depths = [float(p.get("hop_count", 1)) for p in prereqs]
                    hop_depths.extend(depths)

                node = self.graph_store.get_concept_by_id(cid)
                if node:
                    al = float(node.get("abstraction_level",
                                       node.get("n.abstraction_level", 0.5)))
                    abstraction_levels.append(al)
            except Exception as e:
                logger.debug("Graph query failed for concept %s: %s", cid, e)

        if not hop_depths:
            # Use causal ratio as fallback
            if claims.total_claims == 0:
                return 0.0
            return claims.causal_claims / claims.total_claims

        # EDS formula: sigmoid(avg_hop_depth / 3.0) weighted with abstraction
        avg_depth = sum(hop_depths) / len(hop_depths)
        avg_abstraction = (sum(abstraction_levels) / len(abstraction_levels)
                          if abstraction_levels else 0.5)

        depth_component = _sigmoid(avg_depth / 3.0)
        # Blend graph depth with abstraction level
        score = depth_component * 0.7 + avg_abstraction * 0.3

        return min(1.0, score)

    def _compute_coherence(
        self,
        claims: ClaimExtraction,
        concept_ids: List[str],
    ) -> float:
        """Score whether claims form valid causal chains in the concept graph.

        Validates that causal claims follow actual edges in the knowledge graph.
        A claim like "A causes B" is valid if there's a path A->B in the graph.
        """
        causal_claims = [c for c in claims.claims if c.is_causal]
        if not causal_claims:
            # No causal claims: give partial credit for surface coverage
            if claims.total_claims > 0:
                return 0.3  # Some content but no causal reasoning
            return 0.0

        if not self.graph_store:
            # Without graph, use ratio of causal claims as proxy
            return len(causal_claims) / claims.total_claims

        valid_chains = 0
        total_chains = len(causal_claims)

        for claim in causal_claims:
            if self._validate_causal_chain(claim, concept_ids):
                valid_chains += 1

        # Score: ratio of valid chains with a floor for attempting causal reasoning
        raw_ratio = valid_chains / total_chains if total_chains > 0 else 0.0
        # Give credit for attempting causal reasoning even if chains aren't perfect
        attempt_bonus = min(0.2, total_chains * 0.05)

        return min(1.0, raw_ratio + attempt_bonus)

    def _validate_causal_chain(
        self, claim: Claim, concept_ids: List[str]
    ) -> bool:
        """Check if a causal claim follows valid graph edges.

        A causal chain is valid if the concepts mentioned in the claim
        are connected in the knowledge graph (there exists a directed path).
        """
        if not claim.concept_ids or len(claim.concept_ids) < 2:
            # Single-concept causal claims are assumed valid (self-explanation)
            return True

        if not self.graph_store:
            return True  # Can't validate without graph

        # Check if there's a path between the claimed concepts
        try:
            for i in range(len(claim.concept_ids) - 1):
                source = claim.concept_ids[i]
                target = claim.concept_ids[i + 1]
                # Check if target is reachable from source (within 3 hops)
                prereqs = self.graph_store.get_prerequisites(target, max_depth=3)
                if prereqs:
                    reachable = {p.get("node_id", "") for p in prereqs}
                    if source in reachable:
                        return True
                # Also check reverse direction
                prereqs_rev = self.graph_store.get_prerequisites(source, max_depth=3)
                if prereqs_rev:
                    reachable_rev = {p.get("node_id", "") for p in prereqs_rev}
                    if target in reachable_rev:
                        return True
        except Exception as e:
            logger.debug("Chain validation failed: %s", e)
            return True  # Benefit of the doubt on graph errors

        return False

    def _semantic_concept_match(
        self, claims: ClaimExtraction, unmatched_concepts: Set[str]
    ) -> Set[str]:
        """Try to match claims to concepts via semantic similarity.

        Used when explicit concept_id mapping misses coverage that
        the student actually demonstrated through paraphrasing.
        """
        if not self.vector_store or not unmatched_concepts:
            return set()

        matched: Set[str] = set()
        claim_texts = [c.text for c in claims.claims]
        combined_text = " ".join(claim_texts)[:500]

        for cid in unmatched_concepts:
            try:
                node = self.graph_store.get_concept_by_id(cid)
                if not node:
                    continue
                label = node.get("label", node.get("n.label", ""))
                # Check if concept label appears semantically in claims
                if label.lower() in combined_text.lower():
                    matched.add(cid)
            except Exception:
                continue

        return matched
