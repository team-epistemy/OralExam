"""M7 ClaimExtractor: student answer -> LLM -> structured claims.

Extracts what concepts the student invoked and what causal chains
they articulated. Distinguishes surface-level recall from deep
mechanistic reasoning.
"""
from __future__ import annotations
import json
import logging
from typing import List, Optional, Dict, Any

import boto3

from backend.config import Settings
from backend.evaluation.models import Claim, ClaimExtraction

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """You are an expert educational evaluator analyzing a student's answer.

QUESTION:
{question_text}

STUDENT ANSWER:
{student_answer}

TARGET CONCEPTS (from the knowledge graph):
{concept_descriptions}

Your task is to extract the specific CLAIMS the student makes. For each claim:
1. Identify the core assertion
2. Determine which concept(s) from the knowledge graph it relates to
3. Determine if it represents causal/mechanistic reasoning or surface recall
4. If causal, identify the causal chain (A causes B because C)

A "causal claim" demonstrates understanding of WHY or HOW something works,
not just WHAT it is. Look for:
- Mechanism explanations ("X happens because Y triggers Z")
- Causal chains ("A leads to B which results in C")
- Conditional reasoning ("If X then Y because of Z")
- Process descriptions with cause-effect links

A "surface claim" is:
- Definitions without explanation
- Enumeration/listing without connecting logic
- Restating the question
- Memorized phrases without synthesis

Return a JSON object with:
{{
    "claims": [
        {{
            "text": "the specific claim text",
            "concept_ids": ["id1", "id2"],
            "is_causal": true/false,
            "causal_chain": ["cause", "mechanism", "effect"] or null,
            "confidence": 0.0-1.0
        }}
    ],
    "total_claims": <int>,
    "causal_claims": <int>,
    "surface_claims": <int>
}}

Return ONLY valid JSON. No markdown fencing."""


class ClaimExtractor:
    """Extracts structured claims from student answers using LLM analysis.

    Maps each claim to concept graph nodes to determine what the student
    actually demonstrated understanding of (vs. what they merely mentioned).
    """

    def __init__(
        self,
        settings: Settings,
        graph_store=None,
        bedrock_client=None,
    ):
        self.settings = settings
        self.graph_store = graph_store
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

    def extract(
        self,
        question_text: str,
        student_answer: str,
        concept_ids: List[str],
    ) -> ClaimExtraction:
        """Extract claims from a student answer.

        Args:
            question_text: The question that was asked.
            student_answer: The student's response text.
            concept_ids: Target concept IDs the question was designed to assess.

        Returns:
            ClaimExtraction with parsed claims and summary statistics.
        """
        if not student_answer or not student_answer.strip():
            return ClaimExtraction(
                claims=[], total_claims=0, causal_claims=0, surface_claims=0
            )

        # Resolve concept labels for the prompt
        concept_descriptions = self._resolve_concept_labels(concept_ids)

        # Invoke LLM for extraction
        raw_output = self._invoke_llm(
            question_text, student_answer, concept_descriptions
        )

        if not raw_output:
            # Fallback: heuristic extraction
            return self._heuristic_extraction(student_answer, concept_ids)

        # Parse and validate the LLM output
        return self._parse_extraction(raw_output, concept_ids)

    def _resolve_concept_labels(self, concept_ids: List[str]) -> str:
        """Build concept descriptions for the prompt."""
        if not self.graph_store or not concept_ids:
            return "(No concept graph available)"

        descriptions = []
        for cid in concept_ids:
            try:
                node = self.graph_store.get_concept_by_id(cid)
                if node:
                    label = node.get("label", node.get("n.label", cid[:8]))
                    descriptions.append(f"- [{cid[:8]}] {label}")
                else:
                    descriptions.append(f"- [{cid[:8]}] (unknown concept)")
            except Exception:
                descriptions.append(f"- [{cid[:8]}] (lookup failed)")

        return "\n".join(descriptions) if descriptions else "(No concepts mapped)"

    def _invoke_llm(
        self,
        question_text: str,
        student_answer: str,
        concept_descriptions: str,
    ) -> Optional[Dict[str, Any]]:
        """Invoke Claude via Bedrock to extract claims."""
        prompt = EXTRACTION_PROMPT.format(
            question_text=question_text,
            student_answer=student_answer,
            concept_descriptions=concept_descriptions,
        )

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 2048,
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
            return self._parse_json_response(text)
        except Exception as e:
            logger.error("Bedrock claim extraction failed: %s", e)
            return None

    def _parse_json_response(self, text: str) -> Optional[Dict[str, Any]]:
        """Parse JSON from LLM response, handling common formatting."""
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            cleaned = "\n".join(lines)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse claim extraction JSON: %s", e)
            return None

    def _parse_extraction(
        self, raw: Dict[str, Any], concept_ids: List[str]
    ) -> ClaimExtraction:
        """Validate and convert raw LLM output into ClaimExtraction model."""
        claims = []
        for raw_claim in raw.get("claims", []):
            if not isinstance(raw_claim, dict) or "text" not in raw_claim:
                continue

            # Validate concept_ids against known concepts
            claim_concepts = raw_claim.get("concept_ids", [])
            valid_concepts = [c for c in claim_concepts if c in concept_ids]

            claim = Claim(
                text=raw_claim["text"],
                concept_ids=valid_concepts,
                is_causal=raw_claim.get("is_causal", False),
                causal_chain=raw_claim.get("causal_chain"),
                confidence=min(1.0, max(0.0, raw_claim.get("confidence", 0.8))),
            )
            claims.append(claim)

        total = len(claims)
        causal = sum(1 for c in claims if c.is_causal)
        surface = total - causal

        return ClaimExtraction(
            claims=claims,
            total_claims=total,
            causal_claims=causal,
            surface_claims=surface,
            raw_llm_output=raw,
        )

    def _heuristic_extraction(
        self, student_answer: str, concept_ids: List[str]
    ) -> ClaimExtraction:
        """Fallback heuristic claim extraction when LLM is unavailable.

        Splits the answer into sentences and classifies each as causal
        or surface based on the presence of causal connectors.
        """
        causal_markers = [
            "because", "therefore", "thus", "hence", "causes", "leads to",
            "results in", "due to", "as a result", "consequently", "implies",
            "since", "so that", "in order to", "triggers", "enables",
        ]

        # Split into sentence-like segments
        sentences = [s.strip() for s in student_answer.replace("\n", ". ").split(".")
                     if s.strip() and len(s.strip()) > 10]

        claims = []
        for sentence in sentences:
            lower = sentence.lower()
            is_causal = any(marker in lower for marker in causal_markers)
            claim = Claim(
                text=sentence,
                concept_ids=concept_ids[:2],  # Rough assignment
                is_causal=is_causal,
                confidence=0.5,  # Low confidence for heuristic
            )
            claims.append(claim)

        total = len(claims)
        causal = sum(1 for c in claims if c.is_causal)

        return ClaimExtraction(
            claims=claims,
            total_claims=total,
            causal_claims=causal,
            surface_claims=total - causal,
        )
