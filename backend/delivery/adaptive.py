"""M6 Adaptive question selection using concept graph topology.

The adaptive selector picks the next question based on:
1. Which concepts are already covered in the session
2. Prerequisite ordering from the concept graph
3. Student performance so far (if evaluations are available)
4. Coverage maximization — prefer questions that cover uncovered concepts
"""
from __future__ import annotations
import logging
import random
from typing import List, Dict, Set, Optional, Any

logger = logging.getLogger(__name__)


class AdaptiveSelector:
    """Selects the next question from a pool based on concept graph constraints.

    Ensures prerequisite ordering is respected: a concept cannot be assessed
    until its prerequisites have been covered. Within eligible questions,
    prioritizes those covering the most uncovered concepts.
    """

    def __init__(self, graph_store=None):
        """Initialize with an optional concept graph store.

        Args:
            graph_store: KuzuSchemaManager or compatible graph query interface.
                        If None, falls back to round-robin selection.
        """
        self.graph_store = graph_store

    def select_next(
        self,
        available_questions: List[Dict[str, Any]],
        covered_concepts: Set[str],
        session_history: Optional[List[Dict[str, Any]]] = None,
        max_questions: int = 10,
    ) -> Optional[Dict[str, Any]]:
        """Pick the best next question from the available pool.

        Args:
            available_questions: List of question dicts with 'question_id'
                                and 'concept_ids' fields.
            covered_concepts: Set of concept IDs already assessed in this session.
            session_history: Optional list of prior turns with performance data.
            max_questions: Maximum questions to deliver in the session.

        Returns:
            The selected question dict, or None if no eligible questions remain.
        """
        if not available_questions:
            return None

        # Filter out already-delivered questions
        delivered_ids = set()
        if session_history:
            delivered_ids = {t.get("question_id", "") for t in session_history}

        remaining = [
            q for q in available_questions
            if q.get("question_id") not in delivered_ids
        ]

        if not remaining:
            return None

        # If no graph store, use coverage-based heuristic only
        if not self.graph_store:
            return self._select_by_coverage(remaining, covered_concepts)

        # Get eligible questions (those whose prereqs are satisfied)
        eligible = self._filter_by_prerequisites(remaining, covered_concepts)

        if not eligible:
            # Fallback: if nothing is eligible due to strict prereq enforcement,
            # relax and pick from remaining
            logger.debug("No prereq-eligible questions; relaxing constraint")
            eligible = remaining

        # Score eligible questions and pick the best one
        return self._score_and_select(eligible, covered_concepts, session_history)

    def _filter_by_prerequisites(
        self,
        questions: List[Dict[str, Any]],
        covered_concepts: Set[str],
    ) -> List[Dict[str, Any]]:
        """Filter to questions whose target concepts have satisfied prereqs."""
        eligible = []

        for q in questions:
            concept_ids = q.get("concept_ids", [])
            if not concept_ids:
                # Questions without concept mappings are always eligible
                eligible.append(q)
                continue

            # Check if all prerequisites of target concepts are covered
            all_prereqs_met = True
            for cid in concept_ids:
                prereqs = self._get_direct_prereqs(cid)
                if prereqs and not prereqs.issubset(covered_concepts):
                    all_prereqs_met = False
                    break

            if all_prereqs_met:
                eligible.append(q)

        return eligible

    def _get_direct_prereqs(self, concept_id: str) -> Set[str]:
        """Get the immediate prerequisite concept IDs for a concept."""
        if not self.graph_store:
            return set()

        try:
            prereqs = self.graph_store.get_prerequisites(concept_id, max_depth=1)
            return {p.get("node_id", "") for p in prereqs} if prereqs else set()
        except Exception as e:
            logger.debug("Failed to get prereqs for %s: %s", concept_id, e)
            return set()

    def _select_by_coverage(
        self,
        questions: List[Dict[str, Any]],
        covered_concepts: Set[str],
    ) -> Dict[str, Any]:
        """Simple coverage-maximizing selection without graph constraints."""
        scored = []
        for q in questions:
            concept_ids = set(q.get("concept_ids", []))
            new_concepts = concept_ids - covered_concepts
            scored.append((len(new_concepts), q))

        # Sort by number of new concepts covered (desc), break ties randomly
        scored.sort(key=lambda x: x[0], reverse=True)
        max_score = scored[0][0]
        top_tier = [q for s, q in scored if s == max_score]
        return random.choice(top_tier)

    def _score_and_select(
        self,
        questions: List[Dict[str, Any]],
        covered_concepts: Set[str],
        session_history: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Score questions by coverage, difficulty progression, and novelty."""
        scored = []

        # Determine current difficulty trajectory from session history
        target_difficulty = self._compute_target_difficulty(session_history)

        for q in questions:
            score = 0.0
            concept_ids = set(q.get("concept_ids", []))

            # Coverage score: reward questions covering new concepts
            new_concepts = concept_ids - covered_concepts
            coverage_score = len(new_concepts) / max(len(concept_ids), 1)
            score += coverage_score * 0.5

            # Difficulty match: prefer questions near the target difficulty
            q_difficulty = q.get("difficulty", {})
            if isinstance(q_difficulty, dict):
                eds = q_difficulty.get("eds_score", 0.5)
            else:
                eds = 0.5
            difficulty_match = 1.0 - abs(eds - target_difficulty)
            score += difficulty_match * 0.3

            # Graph depth bonus: deeper concepts are more interesting to assess
            if self.graph_store and concept_ids:
                depth_score = self._compute_depth_score(concept_ids)
                score += depth_score * 0.2

            scored.append((score, q))

        scored.sort(key=lambda x: x[0], reverse=True)

        # Add slight randomness among top candidates to avoid predictability
        top_n = min(3, len(scored))
        top_tier = [q for _, q in scored[:top_n]]
        return random.choice(top_tier)

    def _compute_target_difficulty(
        self, session_history: Optional[List[Dict[str, Any]]]
    ) -> float:
        """Compute the target difficulty based on session performance so far.

        Starts at medium (0.5), adjusts up if student is performing well,
        down if struggling.
        """
        if not session_history:
            return 0.5

        # Look at evaluations from prior turns
        scores = []
        for turn in session_history:
            eval_score = turn.get("eds_score")
            if eval_score is not None:
                scores.append(eval_score)

        if not scores:
            return 0.5

        avg_performance = sum(scores) / len(scores)

        # If performing well (>0.6), increase difficulty; if struggling (<0.4), decrease
        if avg_performance > 0.6:
            return min(0.8, 0.5 + (avg_performance - 0.6) * 0.5)
        elif avg_performance < 0.4:
            return max(0.3, 0.5 - (0.4 - avg_performance) * 0.5)
        return 0.5

    def _compute_depth_score(self, concept_ids: Set[str]) -> float:
        """Average normalized depth of concepts in the graph."""
        if not self.graph_store:
            return 0.5

        depths = []
        for cid in concept_ids:
            try:
                prereqs = self.graph_store.get_prerequisites(cid, max_depth=5)
                if prereqs:
                    max_hop = max(p.get("hop_count", 1) for p in prereqs)
                    depths.append(min(max_hop / 5.0, 1.0))
                else:
                    depths.append(0.1)
            except Exception:
                depths.append(0.5)

        return sum(depths) / len(depths) if depths else 0.5
