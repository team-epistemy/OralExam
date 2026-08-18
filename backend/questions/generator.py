"""M5 QuestionGenerator: concept graph + corpus chunks -> LLM -> candidate questions."""
from __future__ import annotations
import json
import logging
from typing import List, Optional, Dict, Any

import boto3

from backend.config import Settings
from backend.questions.models import (
    QuestionCandidate, QuestionType, QuestionStatus, QuestionDifficulty,
    DifficultyLevel, GenerationJob, GenerationJobStatus,
)

logger = logging.getLogger(__name__)

# ── Prompt templates ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert assessment designer for university courses.
Your task is to generate questions that test deep epistemic understanding — not
surface recall. Questions should require students to articulate causal chains,
explain mechanisms, and synthesize concepts across prerequisite relationships.

Guidelines:
- Questions should target specific concept clusters from the knowledge graph
- Prefer "explain why" and "trace how" over "define" or "list"
- Questions should be answerable from the provided source material
- Difficulty should match the depth of the concept graph topology
- Each question should be standalone and clearly worded
"""

GENERATION_PROMPT = """Generate {count} assessment questions for this course material.

Target concepts (from the knowledge graph):
{concept_descriptions}

Relevant source material chunks:
{source_chunks}

Concept graph context:
- Prerequisite depth: {prereq_depth}
- Connected concepts: {connected_concepts}
- Abstraction level: {abstraction_level}

Question type: {question_type}

Return a JSON array of objects, each with:
- "text": the question text
- "target_concept_ids": list of concept IDs this question targets
- "reasoning": brief explanation of what depth of understanding this tests
- "difficulty_level": one of "low", "medium", "high"

Return ONLY valid JSON. No markdown fencing."""


class QuestionGenerator:
    """Generates assessment questions using concept graph topology and corpus content.

    Selects concepts based on graph structure (prereqs, depth, abstraction level),
    retrieves relevant chunks via search, then prompts Claude via Bedrock to produce
    candidate questions targeting specific concept clusters.
    """

    def __init__(
        self,
        settings: Settings,
        graph_store=None,
        corpus_searcher=None,
        bedrock_client=None,
    ):
        self.settings = settings
        self.graph_store = graph_store
        self.corpus_searcher = corpus_searcher
        self._bedrock = bedrock_client
        self._model_id = "anthropic.claude-3-5-sonnet-20241022-v2:0"

    @property
    def bedrock(self):
        """Lazy-initialize the Bedrock runtime client."""
        if self._bedrock is None:
            self._bedrock = boto3.client(
                "bedrock-runtime", region_name=self.settings.bedrock_region
            )
        return self._bedrock

    def generate(
        self,
        course_id: str,
        org_id: str,
        concept_ids: Optional[List[str]] = None,
        count: int = 5,
        question_type: QuestionType = QuestionType.FREE_TEXT,
        created_by: str = "system",
    ) -> List[QuestionCandidate]:
        """Generate candidate questions for the given concepts.

        If concept_ids is None, selects high-value concepts from the graph
        based on topology (nodes with deep prereq chains, high abstraction).
        """
        # Step 1: Resolve target concepts
        target_concepts = self._resolve_concepts(course_id, concept_ids)
        if not target_concepts:
            logger.warning("No concepts found for course %s", course_id)
            return []

        # Step 2: Gather graph topology context
        graph_context = self._gather_graph_context(target_concepts)

        # Step 3: Retrieve relevant corpus chunks
        source_chunks = self._retrieve_chunks(course_id, target_concepts)

        # Step 4: Build the prompt and invoke the LLM
        raw_questions = self._invoke_llm(
            target_concepts, graph_context, source_chunks, count, question_type
        )

        # Step 5: Convert LLM output into QuestionCandidate models
        candidates = self._build_candidates(
            raw_questions, course_id, org_id, question_type, created_by,
            source_chunks, graph_context,
        )

        return candidates

    def _resolve_concepts(
        self, course_id: str, concept_ids: Optional[List[str]]
    ) -> List[Dict[str, Any]]:
        """Resolve concept IDs to full concept data, or auto-select from graph."""
        if not self.graph_store:
            # Fallback: return minimal concept stubs if no graph store
            if concept_ids:
                return [{"node_id": cid, "label": f"concept_{cid[:8]}", "abstraction_level": 0.5}
                        for cid in concept_ids]
            return []

        if concept_ids:
            concepts = []
            for cid in concept_ids:
                node = self.graph_store.get_concept_by_id(cid)
                if node:
                    concepts.append(node)
            return concepts

        # Auto-select: pick concepts with deepest prereq chains (most assessable)
        return self._select_high_value_concepts(course_id)

    def _select_high_value_concepts(self, course_id: str, limit: int = 10) -> List[Dict]:
        """Select concepts that are good assessment targets based on graph topology.

        Prioritizes concepts with:
        - Deep prerequisite chains (high hop depth)
        - High abstraction level
        - Multiple incoming edges (synthesis nodes)
        """
        if not self.graph_store:
            return []

        try:
            # Query concepts ordered by prereq depth and abstraction
            all_concepts = self.graph_store.get_concepts_for_course(course_id)
            scored = []
            for concept in all_concepts:
                prereqs = self.graph_store.get_prerequisites(
                    concept.get("node_id", concept.get("n.node_id")), max_depth=5
                )
                depth = len(prereqs) if prereqs else 0
                abstraction = float(concept.get("abstraction_level",
                                               concept.get("n.abstraction_level", 0.5)))
                score = (depth * 0.6) + (abstraction * 0.4)
                scored.append((score, concept))

            scored.sort(key=lambda x: x[0], reverse=True)
            return [c for _, c in scored[:limit]]
        except Exception as e:
            logger.warning("Failed to select high-value concepts: %s", e)
            return []

    def _gather_graph_context(self, concepts: List[Dict]) -> Dict[str, Any]:
        """Extract graph topology signals for the prompt context."""
        hop_depths = []
        connected = set()
        abstraction_levels = []

        for concept in concepts:
            node_id = concept.get("node_id", concept.get("n.node_id", ""))
            abstraction = float(concept.get("abstraction_level",
                                           concept.get("n.abstraction_level", 0.5)))
            abstraction_levels.append(abstraction)

            if self.graph_store:
                prereqs = self.graph_store.get_prerequisites(node_id, max_depth=5)
                if prereqs:
                    for p in prereqs:
                        hop_depths.append(p.get("hop_count", 1))
                        connected.add(p.get("node_id", ""))

        return {
            "avg_prereq_depth": sum(hop_depths) / len(hop_depths) if hop_depths else 0,
            "max_prereq_depth": max(hop_depths) if hop_depths else 0,
            "connected_count": len(connected),
            "avg_abstraction": (sum(abstraction_levels) / len(abstraction_levels)
                               if abstraction_levels else 0.5),
        }

    def _retrieve_chunks(
        self, course_id: str, concepts: List[Dict], max_chunks: int = 10
    ) -> List[Dict[str, Any]]:
        """Retrieve relevant corpus chunks for the target concepts."""
        if not self.corpus_searcher:
            return []

        chunks = []
        # Search for chunks relevant to each concept label
        seen_ids = set()
        for concept in concepts:
            label = concept.get("label", concept.get("n.label", ""))
            if not label:
                continue
            try:
                results = self.corpus_searcher.search(
                    query=label, course_id=course_id, top_k=3
                )
                for r in results:
                    chunk_id = getattr(r, "chunk_id", None) or r.get("chunk_id", "")
                    if chunk_id not in seen_ids:
                        seen_ids.add(chunk_id)
                        chunks.append({
                            "chunk_id": chunk_id,
                            "text": getattr(r, "text", None) or r.get("text", ""),
                        })
            except Exception as e:
                logger.debug("Chunk search failed for concept %s: %s", label, e)

        return chunks[:max_chunks]

    def _invoke_llm(
        self,
        concepts: List[Dict],
        graph_context: Dict[str, Any],
        source_chunks: List[Dict],
        count: int,
        question_type: QuestionType,
    ) -> List[Dict[str, Any]]:
        """Invoke Claude via Bedrock to generate questions."""
        concept_descriptions = "\n".join(
            f"- [{c.get('node_id', c.get('n.node_id', ''))[:8]}] "
            f"{c.get('label', c.get('n.label', 'unknown'))}"
            for c in concepts
        )

        chunk_texts = "\n---\n".join(
            ch.get("text", "")[:500] for ch in source_chunks[:6]
        ) or "(No source chunks available — generate from concept descriptions)"

        prompt = GENERATION_PROMPT.format(
            count=count,
            concept_descriptions=concept_descriptions,
            source_chunks=chunk_texts,
            prereq_depth=f"avg={graph_context['avg_prereq_depth']:.1f}, "
                        f"max={graph_context['max_prereq_depth']}",
            connected_concepts=graph_context["connected_count"],
            abstraction_level=f"{graph_context['avg_abstraction']:.2f}",
            question_type=question_type.value,
        )

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        })

        try:
            response = self.bedrock.invoke_model(
                modelId=self._model_id,
                contentType="application/json",
                accept="application/json",
                body=body,
            )
            result = json.loads(response["body"].read())
            text = result["content"][0]["text"]
            return self._parse_llm_response(text)
        except Exception as e:
            logger.error("Bedrock invocation failed: %s", e)
            return []

    def _parse_llm_response(self, text: str) -> List[Dict[str, Any]]:
        """Parse the LLM JSON response, handling common formatting issues."""
        # Strip markdown fencing if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            # Remove first and last lines (fencing)
            lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            cleaned = "\n".join(lines)

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict) and "questions" in parsed:
                return parsed["questions"]
            return []
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse LLM response as JSON: %s", e)
            return []

    def _build_candidates(
        self,
        raw_questions: List[Dict],
        course_id: str,
        org_id: str,
        question_type: QuestionType,
        created_by: str,
        source_chunks: List[Dict],
        graph_context: Dict[str, Any],
    ) -> List[QuestionCandidate]:
        """Convert raw LLM output into validated QuestionCandidate models."""
        candidates = []
        chunk_ids = [ch.get("chunk_id", "") for ch in source_chunks]

        for raw in raw_questions:
            if not isinstance(raw, dict) or "text" not in raw:
                continue

            # Map difficulty level
            level_str = raw.get("difficulty_level", "medium").lower()
            try:
                level = DifficultyLevel(level_str)
            except ValueError:
                level = DifficultyLevel.MEDIUM

            # Compute EDS-based difficulty from graph context
            difficulty = QuestionDifficulty(
                level=level,
                eds_score=self._estimate_eds(graph_context, level),
                avg_hop_depth=graph_context.get("avg_prereq_depth"),
                concept_count=len(raw.get("target_concept_ids", [])) or 1,
                reasoning=raw.get("reasoning"),
            )

            candidate = QuestionCandidate(
                course_id=course_id,
                org_id=org_id,
                concept_ids=raw.get("target_concept_ids", []),
                text=raw["text"],
                question_type=question_type,
                difficulty=difficulty,
                status=QuestionStatus.DRAFT,
                created_by=created_by,
                source_chunks=chunk_ids,
            )
            candidates.append(candidate)

        return candidates

    def _estimate_eds(self, graph_context: Dict[str, Any], level: DifficultyLevel) -> float:
        """Estimate an EDS score from graph topology and declared difficulty."""
        base = {"low": 0.25, "medium": 0.55, "high": 0.80}[level.value]
        # Adjust based on actual graph depth
        depth_factor = min(graph_context.get("avg_prereq_depth", 0) / 5.0, 1.0)
        return round(base * 0.6 + depth_factor * 0.4, 4)
