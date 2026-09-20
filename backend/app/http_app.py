"""FastAPI HTTP surface: health, presign, register, read tools, search, graph, questions, delivery, evaluation, dashboard.

TODO(prod): Extract route handlers into domain service classes to improve testability and reduce file size.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta, date
import threading
from typing import Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from backend.config import load_settings
from backend.constants import (
    MAX_CHUNKS_FOR_GRAPH, MAX_CHUNKS_FOR_GENERATION,
    MAX_QUESTION_COUNT, MAX_ANSWER_LENGTH, LLM_MAX_TOKENS_GENERATION,
    LLM_MAX_TOKENS_EVALUATION, LLM_MAX_TOKENS_GRAPH,
    EDS_ALPHA, EDS_BETA, EDS_GAMMA,
)
from backend.models import Role, IngestRequest, NON_GRAPH_SOURCE_TYPES
from backend.api.service import AuthorizationError
from backend.tools.materials_tools import MaterialsTools
from backend.tools.search_tools import SearchTools
from backend.search.corpus_search import CorpusSearcher
from backend.bedrock_helper import call_bedrock
from backend.graph.layout import build_node_ids, compute_layout
from backend.graph.layout import neighbors as graph_neighbors
from backend.questions.exam_builder import build_variants, assemble_questions
from backend.app import factory, routes as R
from backend.app.emails import parse_emails as _parse_emails
from backend.app.syllabus_parser import parse_syllabus as _parse_syllabus, normalize_date as _normalize_date
from backend.app.syllabus_mapper import map_pdf_bytes_to_sessions as _map_pdf_to_sessions
from backend.app.exam_questions import (
    stored_concept_banks as _concept_banks,
    merge_generated_banks,
    sanitize_bank as _sanitize_bank,
    DIFFICULTY_FOCUS,
)
from backend.app.concept_graph import (
    write_document_concepts, document_graph,
    snapshot_course_graph, syllabus_version_ids,
)
from backend.app.graph_curation import apply_curation
from backend.app.performance import aggregate_performance
from backend.db.postgres import PostgresRepository

logger = logging.getLogger(__name__)

# ── Authentication (Cognito access tokens) ─────────────────────────────────────
# No shared secret and no user table: the SPA obtains an access token from the
# Cognito Hosted UI (authorization-code + PKCE) and sends it as a Bearer token.
# The middleware validates it and resolves it to a provisioned auth.app_user (see
# backend/auth). Handlers still read x-user-id / x-role / x-org-name; the
# middleware overwrites those with verified values, so no signature changes.

# Reachable without a token: the Cognito params the login page needs, health,
# static assets, and the SPA shell. The SSE stream is authenticated via a
# ?token= query param (EventSource cannot set headers) — see the middleware.
_PUBLIC_PATH_PREFIXES = (
    "/api/auth/config", "/api/auth/invitations/redeem",
    "/api/demo/",  # credential-free demo links; endpoints self-authenticate via token
    "/health", "/config", "/app", "/static",
    "/docs", "/openapi.json", "/redoc", "/favicon",
)


def _bearer(authorization: str | None) -> str | None:
    """Extract a bearer token from an Authorization header, or None."""
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:]
    return None


def _prior_coverage(repo, session_id: str, question_index: int) -> tuple:
    """Nodes and edge indices already demonstrated on this question's earlier sub-turns."""
    nodes, edges = set(), set()
    try:
        with repo.conn.cursor() as cur:
            cur.execute(
                """SELECT e.eds_components FROM evaluation e
                   JOIN session_turn st ON st.turn_id = e.turn_id
                   WHERE st.session_id = %s::uuid AND st.turn_index = %s
                   ORDER BY st.sub_turn_index""",
                (session_id, question_index),
            )
            for (comp,) in cur.fetchall():
                if not comp:
                    continue
                nodes.update(comp.get("nodes_detected") or [])
                edges.update(comp.get("edges_demonstrated") or [])
    except Exception:
        repo.conn.rollback()  # missing column on an un-migrated DB; treat as no coverage
    return nodes, edges


# The production examiner evaluation prompt (EDS variant), as a template. The tokens
# {{QUESTION_TEXT}}, {{EXPECTED_PATH_JSON}} and {{PROBE_DIRECTIVE}} are filled by
# build_examiner_eval_prompt(), so the live answer flow and the admin tone-lab endpoint
# render the SAME bytes. Kept whitespace-identical to the string that shipped inline in
# the answer handler — do not reformat, the tone eval measures exactly what production
# sends. Literal { } are JSON in the output contract, so interpolation is by str.replace
# on the {{...}} tokens, never str.format.
EXAMINER_EVAL_TEMPLATE = (
    "You are an Epistemy Socratic oral examiner performing two tasks:\n\n"
    "TASK 1: SOCRATIC EVALUATION\n"
    "The exam question is: \"{{QUESTION_TEXT}}\"\n"
    "Evaluate the student's answer. When adequate=false, provide a scaffolding probe.\n\n"
    "TASK 2: EDS COMPONENT EXTRACTION\n"
    "Given the expected reasoning path below, identify which concepts and causal links "
    "the student DEMONSTRATED WITH UNDERSTANDING (not just named).\n\n"
    "EXPECTED PATH:\n{{EXPECTED_PATH_JSON}}\n\n"
    "SCORING RULES:\n"
    "- A node is 'demonstrated' only if the student shows understanding of WHAT it means\n"
    "- An edge is 'demonstrated' only if the student articulates the CAUSAL MECHANISM between src and dst\n"
    "- recitation_score: 0.0=fully authentic reasoning, 1.0=pure keyword recitation without understanding\n"
    "- novel_extensions: valid concepts/links beyond the expected path\n\n"
    "CRITICAL: adequate=true ONLY if student shows clear mechanistic/causal reasoning.\n"
    "ALWAYS provide a probe sub-question that is grounded in THIS student's actual "
    "answer: quote or paraphrase the specific thing they said (or the exact step they "
    "skipped) and push on that precise gap or next causal link. Do NOT emit a generic, "
    "reusable phrase like 'tell me more', 'explain the mechanism', or 'why does that "
    "matter' — the probe must only make sense as a reply to what they just said.\n"
    "{{PROBE_DIRECTIVE}}\n"
    "Respond ONLY with minified JSON, no prose, no code fences:\n"
    '{"clarify": false, "answered": true, "adequate": false, '
    '"feedback": "one sentence", "probe": "follow-up question", '
    '"eds": {"nodes_demonstrated": ["list of node labels demonstrated"], '
    '"edges_demonstrated": [0, 1], '
    '"recitation_score": 0.3, '
    '"novel_extensions": ["any valid concepts beyond expected path"]}}'
)


def build_examiner_eval_prompt(repo, org_id, question_text, expected_path,
                               probe_directive=""):
    """Assemble the production examiner evaluation system prompt (EDS variant).

    Single source of truth for the string the answer-flow model receives, so the admin
    tone-lab endpoint can render it byte-identically (same code path). If an approved
    override is active for this org it supplies the template; otherwise the shipped
    default (EXAMINER_EVAL_TEMPLATE) is used.

    Returns (system_prompt, prompt_version). prompt_version is "active:<id8>" when an
    override is live, else "default:<sha8>" of the shipped template — so editing the
    template constant changes the version with no other edit. Pass repo=None to force
    the default without touching the DB.
    """
    import json as _json, hashlib as _hashlib
    template = EXAMINER_EVAL_TEMPLATE
    version = "default:" + _hashlib.sha1(
        EXAMINER_EVAL_TEMPLATE.encode("utf-8")).hexdigest()[:8]
    if repo is not None and org_id is not None:
        try:
            with repo.conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.examiner_prompt_override')")
                if cur.fetchone()[0] is not None:
                    cur.execute(
                        """SELECT override_id, template FROM examiner_prompt_override
                           WHERE status = 'active' AND org_id = %s::uuid
                           ORDER BY activated_at DESC LIMIT 1""",
                        (org_id,))
                    row = cur.fetchone()
                    if row and row[1]:
                        template = row[1]
                        version = "active:" + str(row[0])[:8]
        except Exception:
            # Un-migrated table or a transient read error must never break the answer
            # flow: fall back to the shipped default.
            try:
                repo.conn.rollback()
            except Exception:
                pass
    system = (template
              .replace("{{QUESTION_TEXT}}", question_text or "")
              .replace("{{EXPECTED_PATH_JSON}}", _json.dumps(expected_path or {}))
              .replace("{{PROBE_DIRECTIVE}}", probe_directive or ""))
    return system, version


# ── Hybrid eval path (fast Haiku spoken probe + async Sonnet EDS scoring) ────────
# Selected per-org via examiner_config.eval_mode ('sonnet' default | 'hybrid'). The
# spoken probe is generated by Haiku for latency; EDS scoring stays on Sonnet but runs
# off the request's critical path so the student isn't blocked on it.
HYBRID_PROBE_MODEL = "claude-haiku-4-5-20251001"

# Probe-only prompt (no EDS): the disciplined warm-receipt spoken turn. Tokens
# {{QUESTION_TEXT}}, {{PROBE_DIRECTIVE}}.
EXAMINER_PROBE_TEMPLATE = (
    "You are an Epistemy Socratic oral examiner.\n"
    "The exam question is: \"{{QUESTION_TEXT}}\"\n"
    "Evaluate the student's latest answer. Decide: answered (did they attempt with "
    "relevant content) and adequate (true ONLY for clear mechanistic/causal reasoning). "
    "Treat \"I don't know\", refusals, gibberish, or off-topic replies as not answered. "
    "When adequate is false, produce a scaffolding probe.\n\n"
    "OUTPUT FIELDS:\n"
    "- feedback: a one-sentence internal assessment (for scoring only; NOT spoken to the "
    "student). Never merge it into the probe.\n"
    "- probe: the spoken turn — exactly ONE question, and nothing else.\n\n"
    "SPOKEN RULES for the probe (hard):\n"
    "- The probe is ONE question, at most 25 words, and nothing else: NO acknowledgment, "
    "NO preamble, no praise, no 'good effort'/'nice'/'thanks', no meta-commentary.\n"
    "- Do NOT evaluate, confirm, or deny the correctness of what they said, and do NOT "
    "restate or summarize their answer or point out what they did not say.\n"
    "- Ground it in what they actually said: push on the specific gap or next causal link.\n"
    "- Never name, define, or hint at a concept the student has not already produced.\n"
    "- Never join two questions with \"and\", \"or\", or \"also\". Plain spoken sentence.\n"
    "{{PROBE_DIRECTIVE}}\n"
    "Respond ONLY with minified JSON, no prose, no code fences:\n"
    '{"clarify": false, "answered": true, "adequate": false, '
    '"feedback": "one-sentence internal assessment, always present", '
    '"probe": "ONE grounded question, <=25 words, no acknowledgment"}'
)

# EDS-only prompt (no probe): the scoring extraction, run async on Sonnet. Tokens
# {{QUESTION_TEXT}}, {{EXPECTED_PATH_JSON}}.
EXAMINER_EDS_TEMPLATE = (
    "You are an Epistemy examiner scoring engine. Given the exam question, the expected "
    "reasoning path, and the student's answer(s), identify which concepts and causal links "
    "the student DEMONSTRATED WITH UNDERSTANDING (not just named).\n"
    "The exam question is: \"{{QUESTION_TEXT}}\"\n"
    "EXPECTED PATH:\n{{EXPECTED_PATH_JSON}}\n"
    "SCORING RULES:\n"
    "- A node is 'demonstrated' only if the student shows understanding of WHAT it means\n"
    "- An edge is 'demonstrated' only if the student articulates the CAUSAL MECHANISM between src and dst\n"
    "- recitation_score: 0.0=fully authentic reasoning, 1.0=pure keyword recitation without understanding\n"
    "- novel_extensions: valid concepts/links beyond the expected path\n"
    "Respond ONLY with minified JSON, no prose, no code fences:\n"
    '{"eds": {"nodes_demonstrated": ["node labels"], "edges_demonstrated": [0, 1], '
    '"recitation_score": 0.3, "novel_extensions": ["concepts beyond expected path"]}}'
)


def get_eval_mode(repo, org_id) -> str:
    """Return the org's answer-flow eval mode ('sonnet' default, or 'hybrid').

    Read per-turn (cheap: guarded to_regclass + one PK lookup). Any error or missing
    table falls back to 'sonnet' so the answer flow never breaks on this read.
    """
    if repo is None or org_id is None:
        return "sonnet"
    try:
        with repo.conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.examiner_config')")
            if cur.fetchone()[0] is None:
                return "sonnet"
            cur.execute("SELECT eval_mode FROM examiner_config WHERE org_id = %s::uuid",
                        (org_id,))
            row = cur.fetchone()
            if row and row[0] in ("sonnet", "hybrid"):
                return row[0]
    except Exception:
        try:
            repo.conn.rollback()
        except Exception:
            pass
    return "sonnet"


def get_text_first(repo, org_id) -> bool:
    """Return the org's text-first render flag (default True). True = the student UI
    shows the probe immediately + plays TTS async; False = reveal text with audio.
    Guarded/safe on an un-migrated DB (missing table or column) → True."""
    if repo is None or org_id is None:
        return True
    try:
        with repo.conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.examiner_config')")
            if cur.fetchone()[0] is None:
                return True
            cur.execute("SELECT text_first FROM examiner_config WHERE org_id = %s::uuid",
                        (org_id,))
            row = cur.fetchone()
            if row and row[0] is not None:
                return bool(row[0])
    except Exception:
        try:
            repo.conn.rollback()
        except Exception:
            pass
    return True


def build_examiner_probe_prompt(question_text, probe_directive="") -> str:
    """Assemble the Haiku probe-only system prompt (hybrid fast path)."""
    return (EXAMINER_PROBE_TEMPLATE
            .replace("{{QUESTION_TEXT}}", question_text or "")
            .replace("{{PROBE_DIRECTIVE}}", probe_directive or ""))


def build_examiner_eds_prompt(question_text, expected_path) -> str:
    """Assemble the Sonnet EDS-only system prompt (hybrid async scoring path)."""
    import json as _json
    return (EXAMINER_EDS_TEMPLATE
            .replace("{{QUESTION_TEXT}}", question_text or "")
            .replace("{{EXPECTED_PATH_JSON}}", _json.dumps(expected_path or {})))


def _run_hybrid_eds_bg(settings, org_id, course_id, student_id, session_id,
                       question_index, actual_turn_id, question_id, question_text,
                       expected_path, ctx_text, answered, adequate, feedback, probe):
    """Background: score EDS on Sonnet off the request's critical path, then update the
    turn's evaluation row + question_eds_aggregate. Mirrors the synchronous EDS-formula
    math; best-effort (a failure leaves the prelim 'pending' evaluation in place)."""
    import json as _json, uuid as _uuid
    conn = None
    try:
        parsed = call_bedrock(
            settings, build_examiner_eds_prompt(question_text, expected_path), ctx_text,
            max_tokens=LLM_MAX_TOKENS_EVALUATION, temperature=0.1)
        eds_raw = parsed.get("eds", {}) if isinstance(parsed, dict) else {}

        expected_nodes = expected_path.get("nodes", [])
        expected_edges = expected_path.get("edges", [])
        expected_extensions = expected_path.get("extensions", [])
        nodes_demonstrated = eds_raw.get("nodes_demonstrated", [])
        edges_demonstrated_indices = eds_raw.get("edges_demonstrated", [])
        recitation_score = float(eds_raw.get("recitation_score", 0.5))
        novel_extensions = eds_raw.get("novel_extensions", [])

        R = 1.0 - recitation_score
        node_score = len(nodes_demonstrated) / max(len(expected_nodes), 1)
        edge_score = len(edges_demonstrated_indices) / max(len(expected_edges), 1)
        max_ext = max(len(expected_extensions), 3)
        gen_score_norm = min(1.0, len(novel_extensions) / max_ext)

        conn = factory.db_connection(settings)
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id', %s, false)", (str(org_id),))
            # Union prior sub-turn components for this question
            cur.execute(
                """SELECT e.eds_components FROM evaluation e
                   JOIN session_turn st ON st.turn_id = e.turn_id
                   WHERE st.session_id = %s::uuid AND st.turn_index = %s
                     AND e.turn_id != %s::uuid
                   ORDER BY st.answered_at""",
                (session_id, question_index, actual_turn_id))
            prior_components = [r[0] for r in cur.fetchall() if r[0]]

        all_nodes, all_edge_indices, all_extensions = set(), set(), set()
        min_recitation = recitation_score
        for pc in prior_components:
            if isinstance(pc, str):
                pc = _json.loads(pc)
            all_nodes.update(pc.get("nodes_detected", []))
            all_edge_indices.update(pc.get("edges_demonstrated", []))
            all_extensions.update(pc.get("novel_extensions", []))
            min_recitation = min(min_recitation, pc.get("raw_probe_score", 1.0))
        all_nodes.update(nodes_demonstrated)
        all_edge_indices.update(edges_demonstrated_indices)
        all_extensions.update(novel_extensions)
        min_recitation = min(min_recitation, recitation_score)

        agg_R = 1.0 - min_recitation
        agg_node_score = len(all_nodes) / max(len(expected_nodes), 1)
        agg_edge_score = len(all_edge_indices) / max(len(expected_edges), 1)
        agg_gen = min(1.0, len(all_extensions) / max(len(expected_extensions), 3))
        agg_coverage = (agg_node_score + agg_edge_score) / 2.0
        eds_question = (agg_R * (EDS_ALPHA * agg_node_score + EDS_BETA * agg_edge_score)
                        + EDS_GAMMA * (1.0 - agg_R * agg_coverage) * agg_gen)
        eds_question = round(min(1.0, max(0.0, eds_question)), 4)

        eds_components_data = {
            "node_score": node_score, "edge_score": edge_score, "r_gate": R,
            "gen_score_norm": gen_score_norm,
            "nodes_detected": list(nodes_demonstrated),
            "edges_demonstrated": list(edges_demonstrated_indices),
            "novel_extensions": list(novel_extensions),
            "raw_probe_score": recitation_score,
        }
        eval_data = {"answered": answered, "adequate": adequate, "feedback": feedback,
                     "probe": probe, "eds_delta": int(eds_question * 10),
                     "eds_question": eds_question, "eval_mode": "hybrid"}
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO evaluation
                   (evaluation_id, turn_id, org_id, course_id, student_id,
                    question_id, eds_score, eds_bucket, raw_llm_output, eds_components)
                   VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s,
                           %s::uuid, %s, %s, %s::jsonb, %s::jsonb)
                   ON CONFLICT (turn_id) DO UPDATE
                   SET eds_score = EXCLUDED.eds_score, eds_bucket = EXCLUDED.eds_bucket,
                       raw_llm_output = EXCLUDED.raw_llm_output,
                       eds_components = EXCLUDED.eds_components""",
                (str(_uuid.uuid4()), actual_turn_id, org_id, course_id, student_id,
                 question_id, eds_question,
                 "high" if eds_question >= 0.7 else ("medium" if eds_question >= 0.3 else "low"),
                 _json.dumps(eval_data), _json.dumps(eds_components_data)))
            try:
                cur.execute(
                    """INSERT INTO question_eds_aggregate
                       (session_id, question_id, org_id, node_score, edge_score,
                        r_gate, gen_score_norm, coverage, final_eds, turn_details)
                       VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s::jsonb)
                       ON CONFLICT (session_id, question_id) DO UPDATE SET
                           node_score = EXCLUDED.node_score, edge_score = EXCLUDED.edge_score,
                           r_gate = EXCLUDED.r_gate, gen_score_norm = EXCLUDED.gen_score_norm,
                           coverage = EXCLUDED.coverage, final_eds = EXCLUDED.final_eds,
                           turn_details = EXCLUDED.turn_details, computed_at = NOW()""",
                    (session_id, question_id, org_id, agg_node_score, agg_edge_score,
                     agg_R, agg_gen, agg_coverage, eds_question,
                     _json.dumps(eds_components_data)))
            except Exception as agg_err:
                logger.warning("hybrid EDS aggregate upsert failed: %s", agg_err)
        conn.commit()
        logger.info("hybrid EDS scored turn=%s eds=%.3f", str(actual_turn_id)[:8], eds_question)
    except Exception as exc:  # noqa: BLE001
        logger.warning("hybrid EDS bg failed for turn %s: %s", str(actual_turn_id)[:8], exc)
    finally:
        if conn is not None:
            conn.close()


# LLM warm-start readiness. A fresh task's first eval otherwise pays ~2s of Secrets
# Manager fetch + SDK import + first-TLS cold-start; we pay it at boot instead and keep
# /health at 503 until warm so the ALB doesn't route a cold task (see _warmup_llm).
_LLM_READY = False


def _warmup_llm(settings) -> None:
    """Pre-build the Anthropic client and open a keep-alive connection for both the
    Haiku (hybrid probe) and Sonnet (eval/EDS) models at task start. Best-effort:
    readiness flips in `finally` regardless, so a warmup failure never bricks the task
    (a cold task still works, just pays the first-call penalty)."""
    global _LLM_READY
    from backend.bedrock_helper import _get_anthropic_client
    sonnet = getattr(settings, "anthropic_model", "claude-sonnet-4-6")
    try:
        client = _get_anthropic_client(settings)  # SM fetch + import + construct
        for model in (HYBRID_PROBE_MODEL, sonnet):
            try:
                client.with_options(timeout=15).messages.create(
                    model=model, max_tokens=1,
                    messages=[{"role": "user", "content": "warmup"}])
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM warmup ping failed (%s): %s", model, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM warmup failed: %s", exc)
    finally:
        _LLM_READY = True
        logger.info("LLM warmup complete — task ready")


def _probe_target(expected_path: dict, seen_nodes: set, seen_edges: set) -> str:
    """Name the next uncovered concept or causal link, as a directive for the prompt.

    Without this the model picks a follow-up freely and tends to re-probe ground the
    student already covered, so three turns can circle one idea while other parts of
    the expected path are never examined. Edges are preferred over bare nodes because
    articulating a mechanism is what the EDS edge weight (0.6) actually rewards.
    """
    seen_lower = {n.lower() for n in seen_nodes}

    for i, edge in enumerate(expected_path.get("edges") or []):
        if i in seen_edges:
            continue
        src, dst = edge.get("src", ""), edge.get("dst", "")
        if not src or not dst:
            continue
        # Prefer a link whose endpoints the student has already shown: they have the
        # pieces, so the gap is the mechanism between them — the productive next step.
        if src.lower() in seen_lower or dst.lower() in seen_lower:
            return (f"Probe the causal link from \"{src}\" to \"{dst}\". The student has "
                    f"not yet explained the mechanism connecting them. Ask about that "
                    f"mechanism specifically — do not reveal it.")

    for node in expected_path.get("nodes") or []:
        label = node.get("label", "")
        if label and label.lower() not in seen_lower:
            return (f"Probe the concept \"{label}\", which the student has not yet "
                    f"demonstrated. Ask a question that requires explaining what it "
                    f"means and why it matters here.")

    for i, edge in enumerate(expected_path.get("edges") or []):
        if i not in seen_edges and edge.get("src") and edge.get("dst"):
            return (f"Probe the causal link from \"{edge['src']}\" to \"{edge['dst']}\", "
                    f"which the student has not yet articulated.")

    extensions = expected_path.get("extensions") or []
    if extensions and extensions[0].get("label"):
        return (f"The student has covered the expected path. Probe the extension "
                f"\"{extensions[0]['label']}\" to test whether they can go further.")
    return ""


def _generate_expected_path(settings, question_text: str, concept_labels: list) -> dict:
    """Generate expected reasoning path for a question (used for backfill/lazy generation)."""
    import json as _json
    system_prompt = (
        "You are building the expected reasoning path for an oral exam question. "
        "Given the question and related concepts, produce the CORE causal chain a "
        "strong student should demonstrate. Keep it TIGHT — at most 5 nodes and at "
        "most 5 edges, only the concepts and links this specific question needs "
        "(a single oral answer can't cover more). Keep definitions and explanations "
        "to one short sentence each.\n\n"
        "Return ONLY valid JSON, no prose:\n"
        '{"nodes": [{"label": "...", "definition": "1-sentence def"}], '
        '"edges": [{"src": "concept_A", "dst": "concept_B", "link_type": "CAUSES|ENABLES|PREVENTS|INCREASES|DECREASES", "explanation": "the mechanism"}], '
        '"extensions": [{"label": "...", "connection": "how this goes beyond the base path"}]}'
    )
    user_msg = (
        f"Question: {question_text}\n"
        f"Related concepts: {', '.join(concept_labels)}\n\n"
        "Produce the expected reasoning path (at most 5 nodes and 5 edges)."
    )
    try:
        # Token headroom stays generous (max_tokens is a ceiling, not a cost — only
        # tokens actually generated take time) so the JSON never truncates. What IS
        # bounded is the retry budget and the wall clock: a malformed sample used to
        # cost up to 3 sequential ~10s calls, which is what pushed a first answer
        # past CloudFront's 60s origin read timeout.
        return call_bedrock(settings, system_prompt, user_msg, max_tokens=3000,
                            temperature=0.1, retries=1,
                            timeout=_EXPECTED_PATH_TIMEOUT_S)
    except Exception:
        return {"nodes": [], "edges": [], "extensions": []}


# Expected-path generation is a ~10s Claude call, so it never runs inside a request:
# assign_questions queues it right after the insert, and submit_answer queues it for
# any question that somehow still lacks one. The in-flight set stops concurrent
# sub-turns (or two students on the same question) firing duplicate calls.
# Not a request-sized budget: this call runs in a background thread, so the 60s
# CloudFront origin read timeout that bounds student traffic does not apply to it,
# and a student's exposure is capped separately by _EXPECTED_PATH_WAIT_S below.
# Killing a slow generation early only means the rubric is missing for the next
# turn too, so the budget is generous relative to the ~10s a healthy call takes —
# but it stays under 60s so the number can never be mistaken for a request budget
# if this ever moves onto a request path.
_EXPECTED_PATH_TIMEOUT_S = 40
# How long an answer will wait for an already-queued path rather than grade
# without one. This one IS inside a request, so it stays far under the 60s
# CloudFront origin timeout.
_EXPECTED_PATH_WAIT_S = 6.0
# The per-turn evaluation call, which every answer pays. A healthy one is ~2-4s
# (max_tokens is only 500), so 20s is well clear of normal variance while keeping a
# stalled call from riding to CloudFront's 60s cut-off and 504ing mid-exam.
_EVAL_TIMEOUT_S = 20
_INFLIGHT_PATHS: set = set()
_INFLIGHT_PATHS_LOCK = threading.Lock()


@contextlib.contextmanager
def _timed(step: str, **fields):
    """Log a real student turn's wall-clock for one step of submit_answer.

    The admin perf probe measures a synthetic turn (fixed short question,
    admin-triggered); actual student turns were never timed, so every latency
    number for them was an estimate. These lines are the measurement.

    Emitted on the way out even when the step raises, because a step that failed
    slowly is exactly the case worth seeing. One fixed prefix and key=value pairs
    so a CloudWatch metric filter can pick them up without matching HTTP access
    lines by accident (logging is not structured yet — see BACKLOG item 4).
    """
    start = time.monotonic()
    try:
        yield
    finally:
        extra = "".join(f" {k}={v}" for k, v in fields.items())
        logger.info("turn-timing step=%s ms=%d%s", step,
                    int((time.monotonic() - start) * 1000), extra)


def _store_expected_path(conn, settings, qid: str, text: str, concepts: list) -> None:
    """Generate one question's expected path and commit it."""
    import json as _json
    path = _generate_expected_path(settings, text, concepts)
    if not path.get("nodes"):
        logger.warning("expected_path came back empty for question %s — EDS degrades "
                       "to the no-path Socratic rubric", qid[:8])
        return
    with conn.cursor() as cur:
        cur.execute("UPDATE question SET expected_path = %s::jsonb "
                    "WHERE question_id = %s::uuid", (_json.dumps(path), qid))
    conn.commit()
    logger.info("expected_path stored for question %s: %d nodes, %d edges", qid[:8],
                len(path.get("nodes", [])), len(path.get("edges", [])))


def _fill_expected_paths_bg(settings, org_id: str, items: list) -> None:
    """Generate + persist expected_path for each (question_id, text, concepts).

    Opens its own connection — the request's is long gone. One question's failure
    never stops the rest. Runs synchronously; callers wrap it in a thread.
    """
    conn = None
    try:
        conn = factory.db_connection(settings)
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id', %s, false)", (org_id,))
        conn.commit()
        for qid, text, concepts in items:
            try:
                _store_expected_path(conn, settings, qid, text, concepts)
            except Exception as exc:  # noqa: BLE001 - one question isn't the batch
                logger.warning("expected_path failed for question %s: %s", qid[:8], exc)
                conn.rollback()
    except Exception as exc:  # noqa: BLE001
        logger.warning("expected_path fill aborted for org %s: %s", org_id[:8], exc)
    finally:
        with _INFLIGHT_PATHS_LOCK:
            for it in items:
                _INFLIGHT_PATHS.discard(it[0])
        if conn is not None:
            conn.close()


def _fill_expected_paths_async(settings, org_id: str, items: list) -> None:
    """Queue expected-path generation off the request path, skipping in-flight ids."""
    fresh = []
    with _INFLIGHT_PATHS_LOCK:
        for it in items:
            if it[0] not in _INFLIGHT_PATHS:
                _INFLIGHT_PATHS.add(it[0])
                fresh.append(it)
    if not fresh:
        return
    threading.Thread(target=_fill_expected_paths_bg, args=(settings, org_id, fresh),
                     daemon=True).start()


def _prefill_session_paths(repo, settings, org_id: str, qrows: list) -> None:
    """Queue path generation for a starting session's questions that lack one.

    Deliberately guarded and separate from the question SELECT: expected_path is a
    later migration, and exam start must not break on a DB that predates it.
    """
    try:
        ids = [str(qr[0]) for qr in qrows]
        if not ids:
            return
        with repo.conn.cursor() as cur:
            cur.execute(
                """SELECT question_id FROM question
                   WHERE question_id = ANY(%s::uuid[])
                     AND (expected_path IS NULL
                          OR expected_path->'nodes' IS NULL
                          OR jsonb_array_length(expected_path->'nodes') = 0)""",
                (ids,),
            )
            missing = {str(r[0]) for r in cur.fetchall()}
    except Exception as exc:  # noqa: BLE001 - un-migrated DB: nothing to prefill
        repo.conn.rollback()
        logger.info("expected_path prefill skipped: %s", exc)
        return
    items = [(str(qr[0]), qr[1], qr[2] if isinstance(qr[2], list) else [])
             for qr in qrows if str(qr[0]) in missing]
    if items:
        logger.info("Prefilling %d expected paths at session start", len(items))
        _fill_expected_paths_async(settings, org_id, items)


def _read_expected_path(repo, question_id: str) -> dict:
    """The question's stored expected_path, or {} when it has none yet."""
    import json as _json
    with repo.conn.cursor() as cur:
        cur.execute("SELECT expected_path FROM question WHERE question_id = %s::uuid",
                    (question_id,))
        row = cur.fetchone()
    if not row or not row[0]:
        return {}
    return row[0] if isinstance(row[0], dict) else _json.loads(row[0])


def _await_expected_path(repo, question_id: str, budget_s: float) -> dict:
    """Wait (bounded) for a queued fill to land, but only if one is in flight.

    Grading a turn without the path costs real quality — no eds_components is
    stored, so the NEXT probe can't tell what the student already covered and
    the question's EDS union misses this turn. A few seconds here is cheaper
    than that, and start_exam's pre-queue makes the wait usually zero. Polls
    rather than signals so it works whichever worker thread is filling.
    """
    with _INFLIGHT_PATHS_LOCK:
        if question_id not in _INFLIGHT_PATHS:
            return {}
    deadline = time.monotonic() + budget_s
    while True:
        # READ COMMITTED gives each statement a fresh snapshot, so a poll sees
        # the worker's commit without this request ending its own transaction.
        path = _read_expected_path(repo, question_id)
        if path.get("nodes") or time.monotonic() >= deadline:
            return path
        # An empty path looks the same whether the filler is one second from
        # committing or died five seconds ago, so re-read the in-flight set:
        # once the id is gone with nothing stored, the attempt failed and no
        # one else will write it. Without this the budget is spent in full on
        # every fast failure — a bad key fails in ~0.3s, and five sub-turns a
        # question would each still wait the remaining ~5.7s for nothing.
        with _INFLIGHT_PATHS_LOCK:
            if question_id not in _INFLIGHT_PATHS:
                return path
        time.sleep(0.5)


# Course graph rebuilds run as background threads inside the web process. If the
# ECS task is replaced (a deploy) or recycled mid-rebuild, that thread is killed
# and the `is_stale=true` flag the endpoint set would otherwise never clear — the
# UI then spins "Rebuilding…" forever. We track in-flight rebuilds here so the
# graceful-shutdown hook (see create_app) can clear the flag for any that were
# interrupted. Keyed course_id -> org_id (org needed to satisfy RLS on the clear).
_INFLIGHT_REBUILDS: Dict[str, str] = {}
_INFLIGHT_LOCK = threading.Lock()


def _clear_stale_flag(settings, org_id: str, course_id: str) -> None:
    """Clear is_stale on a course's active graph (RLS-scoped to org_id)."""
    conn = None
    try:
        conn = factory.db_connection(settings)
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id', %s, false)", (org_id,))
            cur.execute("UPDATE graph_version SET is_stale = false "
                        "WHERE org_id = %s AND course_id = %s AND is_active = true",
                        (org_id, course_id))
        conn.commit()
    except Exception:  # noqa: BLE001
        if conn is not None:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
    finally:
        if conn is not None:
            conn.close()


def _rebuild_course_graph_bg(settings, org_id: str, course_id: str, domain: str = "general") -> None:
    """Rebuild a course's concept graph from ITS OWN documents, with provenance.

    Extracts concepts per document (writing document_concept / _edge), then
    recomputes course_concept / _edge from only this course's documents and
    snapshots the result into graph_version. Because the course graph is a pure
    function of the course's current documents, off-subject concepts can never
    accumulate (fixes cross-course leaks) and a deleted document drops out cleanly.
    Runs synchronously — callers wrap it in a thread.
    """
    conn = None
    with _INFLIGHT_LOCK:
        _INFLIGHT_REBUILDS[course_id] = org_id
    try:
        conn = factory.db_connection(settings)
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id', %s, false)", (org_id,))
            conn.commit()
            # Join material_version so we can drop tabular/data uploads (CSV, XLSX),
            # which are ingested but never contribute to the concept graph.
            cur.execute(
                """SELECT DISTINCT c.material_version_id, mv.source_type
                   FROM chunk c JOIN material_version mv ON mv.material_version_id = c.material_version_id
                   WHERE c.course_id = %s ORDER BY c.material_version_id""",
                (course_id,))
            _non_graph = {s.value for s in NON_GRAPH_SOURCE_TYPES}
            mv_ids = [str(r[0]) for r in cur.fetchall() if r[1] not in _non_graph]
            # The syllabus is excluded from the concept graph — don't extract from
            # it (recompute drops it too, but skipping avoids a wasted LLM call).
            syllabus_ids = set(syllabus_version_ids(cur, org_id, course_id))
            mv_ids = [m for m in mv_ids if m not in syllabus_ids]

        if not mv_ids:
            # No materials remain: clear provenance and retire the graph.
            with conn.cursor() as cur:
                for tbl in ("document_concept", "document_concept_edge", "course_concept", "course_concept_edge"):
                    cur.execute("DELETE FROM %s WHERE course_id = %%s::uuid AND org_id = %%s::uuid" % tbl,
                                (course_id, org_id))
                cur.execute("UPDATE graph_version SET is_active = false, is_stale = false "
                            "WHERE org_id = %s AND course_id = %s", (org_id, course_id))
            conn.commit()
            logger.info("Graph retired for course %s (no materials remain)", course_id[:8])
            return

        # Extract per document so every concept keeps its provenance.
        for mv in mv_ids:
            with conn.cursor() as cur:
                cur.execute("SELECT text FROM chunk WHERE material_version_id = %s ORDER BY chunk_index", (mv,))
                chunks = [r[0] for r in cur.fetchall()]
            if not chunks:
                continue
            data = call_bedrock(settings, _GRAPH_EXTRACTION_PROMPT,
                                f"Domain: {domain}\n\n" + "\n\n".join(chunks[:MAX_CHUNKS_FOR_GRAPH]),
                                max_tokens=LLM_MAX_TOKENS_GRAPH, temperature=0.2)
            with conn.cursor() as cur:
                write_document_concepts(cur, org_id, course_id, mv,
                                        data.get("concepts", []), data.get("relations", []))
            conn.commit()

        with conn.cursor() as cur:
            snapshot = snapshot_course_graph(cur, org_id, course_id)
        conn.commit()
        logger.info("Course graph rebuilt for %s from %d documents: %d concepts, %d relations",
                    course_id[:8], len(mv_ids), len(snapshot["concepts"]), len(snapshot["relations"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Course graph rebuild failed for %s: %s", course_id[:8], exc, exc_info=True)
        if conn is not None:
            try:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute("UPDATE graph_version SET is_stale = false "
                                "WHERE org_id = %s AND course_id = %s AND is_active = true", (org_id, course_id))
                conn.commit()
            except Exception:  # noqa: BLE001
                pass
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT_REBUILDS.pop(course_id, None)
        if conn is not None:
            conn.close()


def _rebuild_graph_async(settings, org_id: str, course_id: str) -> None:
    """Background rebuild after a material is deleted (provenance-based)."""
    import threading
    threading.Thread(target=_rebuild_course_graph_bg, args=(settings, org_id, course_id), daemon=True).start()


# Shared extraction prompt: concepts + relationships + a per-concept question bank
# (3 conceptual probes + 1 case-based question). Used by the async graph builder.
_GRAPH_EXTRACTION_PROMPT = (
    "You are building the concept map for an oral exam. From THIS course "
    "material only, extract the core concepts a student would be examined on "
    "(8 to 14). Do not introduce concepts that are not present in the material. "
    "Treat broad or introductory material sparsely, as a few high-level concepts; "
    "for specific, quantitative, or formula-driven material capture concepts more "
    "granularly. For EACH concept also author a DEPTH-TAGGED question bank grounded "
    "strictly in the material, with these tiers: 'recall' = 2 questions on precise "
    "definitions/facts/formulas; 'application' = 2 questions applying the concept to "
    "a straightforward situation; 'in_depth' = 2 higher-order 'why/how' questions on "
    "mechanisms, prerequisite chains, or multi-step reasoning; 'case' = 1 question "
    "that opens with a brief 1-2 sentence mini-case (a realistic scenario from the "
    "material's domain) and asks the student to APPLY the concept to it. Also identify "
    "the prerequisite relationships between concepts. "
    "Return ONLY valid JSON, no prose, no markdown fences: "
    '{"concepts": [{"label": "2-5 word noun phrase", "definition": "1 sentence", '
    '"abstraction_level": 0.5, "questions": {"recall": ["...", "..."], '
    '"application": ["...", "..."], "in_depth": ["...", "..."], '
    '"case": ["Mini-case: <1-2 sentence scenario>. <question applying the concept>"]}}], '
    '"relations": [{"src": "...", "dst": "...", "edge_type": "PREREQUISITE_FOR", "confidence": 0.9}]} '
    "Edge types: PREREQUISITE_FOR, ENABLES, IS_A, PART_OF, APPLIED_IN, CO_REQUIRED_WITH. "
    "src is a prerequisite of dst. Labels are short noun phrases, no numbering."
)


def _build_graph_async(settings, org_id: str, course_id: str, domain: str = "general") -> None:
    """Background full (re)build of a course's concept graph, per-document with
    provenance. The extraction LLM calls are large/slow, so this never runs on a
    web request (that caused the "Load failed" gateway timeouts)."""
    import threading
    threading.Thread(target=_rebuild_course_graph_bg,
                     args=(settings, org_id, course_id, domain), daemon=True).start()


def _build_one_document_graph_bg(settings, org_id: str, course_id: str,
                                 material_version_id: str) -> None:
    """Extract the concept graph for a SINGLE document, then recompute the course
    snapshot. Used to (re)build a document's graph on demand — e.g. when the
    inline build after upload produced nothing (truncated/failed extraction).
    Runs in a background thread; LLM calls are slow. Non-fatal on error."""
    conn = None
    try:
        conn = factory.db_connection(settings)
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id', %s, false)", (org_id,))
            conn.commit()
            cur.execute("SELECT text FROM chunk WHERE material_version_id = %s ORDER BY chunk_index",
                        (material_version_id,))
            chunks = [r[0] for r in cur.fetchall()]
        if not chunks:
            logger.warning("Doc graph build: no chunks for %s", material_version_id[:8])
            return
        data = call_bedrock(settings, _GRAPH_EXTRACTION_PROMPT,
                            "Domain: general\n\n" + "\n\n".join(chunks[:MAX_CHUNKS_FOR_GRAPH]),
                            max_tokens=LLM_MAX_TOKENS_GRAPH, temperature=0.2)
        with conn.cursor() as cur:
            write_document_concepts(cur, org_id, course_id, material_version_id,
                                    data.get("concepts", []), data.get("relations", []))
            snapshot = snapshot_course_graph(cur, org_id, course_id)
        conn.commit()
        logger.info("Doc graph built for %s: %d concepts; course now %d",
                    material_version_id[:8], len(data.get("concepts", [])), len(snapshot["concepts"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Doc graph build failed for %s: %s", material_version_id[:8], exc, exc_info=True)
        if conn is not None:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
    finally:
        if conn is not None:
            conn.close()


def _is_public(path: str) -> bool:
    """True when a route may be reached without a verified token."""
    return path == "/" or path.startswith(_PUBLIC_PATH_PREFIXES)


def _install_auth_middleware(app: FastAPI, deps) -> None:
    """Validate the Cognito access token on every non-public route.

    Handlers read identity from x-user-id / x-role / x-org-name. Rather than change
    31 signatures, this overwrites those headers with the verified identity, so a
    client-supplied role or tenant can never reach a handler. x-org-name carries
    the verified tenant UUID (org_id), which caller_for_org consumes directly.
    Requests are rejected here if the token is missing or unresolvable.
    """
    from starlette.concurrency import run_in_threadpool
    from backend.auth.token import TokenError
    from backend.auth.identity import IdentityError

    @app.middleware("http")
    async def enforce_auth(request, call_next):
        if request.method == "OPTIONS" or _is_public(request.url.path):
            return await call_next(request)

        # EventSource can't set headers, so SSE carries the token as ?token=.
        token = _bearer(request.headers.get("authorization")) or \
            request.query_params.get("token")
        if not token:
            return JSONResponse(status_code=401,
                content={"detail": "Authentication required. Sign in again."})
        try:
            identity = await run_in_threadpool(deps()["resolver"].resolve, token)
        except (TokenError, IdentityError):
            return JSONResponse(status_code=401,
                content={"detail": "Session expired or invalid. Sign in again."})

        # Starlette exposes raw headers as a list of lowercase byte pairs; replacing
        # them here means downstream Header(...) params see only verified values.
        spoofable = {b"x-user-id", b"x-role", b"x-org-name"}
        headers = [(k, v) for k, v in request.scope["headers"] if k not in spoofable]
        headers += [
            (b"x-user-id", identity.user_id.encode()),
            (b"x-role", identity.role.encode()),
            (b"x-org-name", identity.org_id.encode()),  # verified tenant UUID
        ]
        request.scope["headers"] = headers
        return await call_next(request)


def create_app() -> FastAPI:
    """Build the FastAPI app with all module routes wired to real components."""
    app = FastAPI(title="Epistemy — Content Ingestion & Assessment Platform")

    settings = load_settings()
    deps = _lazy_deps(settings)
    _install_auth_middleware(app, deps)

    # CORS must be the OUTERMOST middleware: add_middleware prepends, so adding it
    # LAST (after the auth middleware) wraps everything — including auth 401s that
    # short-circuit before the route runs. Otherwise a cross-origin (Vercel) caller
    # gets a headerless 401 the browser reports as an opaque "Load failed" instead
    # of a handleable 401.
    # TODO(prod): Restrict allow_origins to the actual school domain(s) and remove
    # allow_credentials with wildcard origin (browsers block this combination anyway).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _register_routes(app, deps)
    _mount_demo(app)

    @app.on_event("startup")
    def _warm_llm_on_start() -> None:
        """Warm the LLM client before the task takes traffic. /health stays 503 until
        ready (below), so the ALB won't route a cold task; a 20s backstop flips ready
        even if warmup hangs, so a bad warmup can never hold the task unhealthy."""
        threading.Thread(target=_warmup_llm, args=(settings,), daemon=True).start()

        def _backstop() -> None:
            global _LLM_READY
            if not _LLM_READY:
                logger.warning("LLM warmup backstop fired (20s) — marking ready")
                _LLM_READY = True
        timer = threading.Timer(20.0, _backstop)
        timer.daemon = True
        timer.start()

    @app.on_event("shutdown")
    def _clear_interrupted_rebuilds() -> None:
        """On graceful shutdown (ECS SIGTERM during a deploy), clear the is_stale
        flag for any rebuild still in flight so its course's graph doesn't stay
        wedged in the 'Rebuilding…' state after the task is replaced."""
        with _INFLIGHT_LOCK:
            pending = list(_INFLIGHT_REBUILDS.items())
        for course_id, org_id in pending:
            logger.warning("Clearing stale flag for interrupted rebuild of course %s", course_id[:8])
            _clear_stale_flag(settings, org_id, course_id)

    return app


def _mount_demo(app: FastAPI) -> None:
    """Serve both the old demo UI and the React frontend."""
    import pathlib
    from fastapi.responses import FileResponse
    static = pathlib.Path(__file__).resolve().parent / "static"
    frontend = static / "frontend"
    app.mount("/static", StaticFiles(directory=str(static)), name="static")

    # Serve React frontend at /app and handle SPA routing
    if frontend.exists():
        app.mount("/app/assets", StaticFiles(directory=str(frontend / "assets")), name="frontend-assets")

        @app.get("/app")
        def app_root_redirect():
            # Explicit relative redirect for the no-trailing-slash case. Without
            # it, FastAPI's automatic slash-redirect builds an ABSOLUTE URL from
            # the host the backend sees (the ALB, http), bouncing HTTPS/CloudFront
            # visitors onto the plain-HTTP ALB — which breaks the secure-context
            # crypto the Cognito PKCE sign-in needs. Relative "/app/" stays on the
            # caller's own host + scheme.
            return RedirectResponse(url="/app/")

        @app.get("/app/{path:path}")
        def serve_frontend(path: str = ""):
            # The SPA shell must always revalidate so a redeploy's newly-hashed
            # bundle is picked up immediately; hashed /app/assets stay cacheable.
            return FileResponse(
                str(frontend / "index.html"),
                headers={"Cache-Control": "no-cache, must-revalidate"},
            )

        @app.get("/")
        def root():
            return RedirectResponse(url="/app/")
    else:
        @app.get("/")
        def root():
            return RedirectResponse(url="/static/demo.html")


def _lazy_deps(settings):
    """Defer heavy AWS client creation so /health responds even without credentials.

    Uses a ThreadedConnectionPool. Each request acquires its own connection
    via `_request_repo()` to guarantee tenant isolation (RLS session vars
    never leak between concurrent requests).
    """
    cache: dict = {}

    def deps():
        if "pool" not in cache:
            pool = factory.build_pool(settings)
            storage = factory.build_storage(settings)
            queue = factory.build_queue(settings)
            embedder = factory.build_embedder(settings)
            resolver = factory.build_identity_resolver(settings, pool)
            cache.update(pool=pool, storage=storage, queue=queue,
                         embedder=embedder, resolver=resolver, settings=settings)
        return cache
    return deps


def _request_repo(deps_cache: dict) -> PostgresRepository:
    """Acquire a connection from the pool and return a fresh repo for this request.

    Caller MUST return the connection via _release_repo() in a finally block.
    """
    pool = deps_cache["pool"]
    conn = factory.get_connection_from_pool(pool)
    return PostgresRepository(conn)


def _release_repo(deps_cache: dict, repo: PostgresRepository) -> None:
    """Return the repo's connection back to the pool."""
    pool = deps_cache["pool"]
    try:
        factory.return_connection_to_pool(pool, repo.conn)
    except Exception:
        pass


# ── Credential-free demo links ──────────────────────────────────────────────
# Fully isolated from the live student answer path (option 1b): the demo has its own
# orchestration below and reuses only pure, read-only helpers (prompt builder, EDS
# math, probe target). A demo bug can never touch a real exam/session/grade — demo
# sessions use throwaway anonymous student ids and are practice-mode only.
DEMO_URL_BASE = "https://www.epistemy.ai/app/demo/"


class DemoLinkCreateRequest(BaseModel):
    """POST body: mint a demo link for one assignment."""
    assignment_id: str
    days: int = Field(default=10, ge=1, le=60)
    max_attempts: int = Field(default=10, ge=1, le=500)


class DemoLinkOptionsRequest(BaseModel):
    """POST body (assignment-scoped mint): expiry + attempt-cap knobs (defaults 10/10)."""
    days: int = Field(default=10, ge=1, le=60)
    max_attempts: int = Field(default=10, ge=1, le=500)


class DemoTTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def _demo_link_or_error(cur, token: str):
    """Fetch + validate a demo link. Returns the row or a dict error the caller returns.
    Row: (token, org_id, assignment_id, max_attempts, attempts_used, expires_at)."""
    cur.execute("SELECT to_regclass('public.demo_link')")
    if cur.fetchone()[0] is None:
        return None, {"status": "error", "message": "demo not available"}
    cur.execute("""SELECT token, org_id, assignment_id, max_attempts, attempts_used, expires_at
                   FROM demo_link WHERE token = %s""", (token,))
    row = cur.fetchone()
    if not row:
        return None, {"status": "not_found", "message": "This demo link is invalid."}
    if row[5] is not None and row[5] < datetime.now(timezone.utc):
        return None, {"status": "expired", "message": "This demo link has expired."}
    if row[4] >= row[3]:
        return None, {"status": "exhausted",
                      "message": "This demo has reached its attempt limit."}
    return row, None


def _demo_answer_turn(repo, settings, org_id, course_id, student_id, session_id,
                      question_set_id, question_index, answer_text):
    """Isolated copy of the answer turn for demo sessions (option 1b). Reuses pure
    helpers; persists under the demo identity. Returns the AnswerResponse dict."""
    import json as _json, uuid as _uuid
    with repo.conn.cursor() as cur:
        cur.execute("""SELECT q.question_id, q.text, q.concept_ids, q.expected_path
                       FROM question_set_membership qsm JOIN question q ON q.question_id = qsm.question_id
                       WHERE qsm.question_set_id = %s::uuid AND qsm.position = %s""",
                    (question_set_id, question_index))
        row = cur.fetchone()
    if not row:
        raise AuthorizationError(f"no question at index {question_index}")
    question_id = str(row[0]); question_text = row[1]
    concept_ids = row[2] if isinstance(row[2], list) else []
    expected_path = row[3] if isinstance(row[3], dict) else (_json.loads(row[3]) if row[3] else {})
    if not expected_path.get("nodes"):
        try:
            expected_path = _generate_expected_path(settings, question_text, concept_ids)
        except Exception:
            expected_path = {}

    turn_id = str(_uuid.uuid4()); now = datetime.now(timezone.utc)
    with repo.conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM session_turn WHERE session_id = %s::uuid AND turn_index = %s",
                    (session_id, question_index))
        sub = cur.fetchone()[0]
        cur.execute("""INSERT INTO session_turn
                       (turn_id, session_id, org_id, turn_index, sub_turn_index, question_id, student_answer, answered_at)
                       VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s::uuid, %s, %s)
                       ON CONFLICT (session_id, turn_index, sub_turn_index)
                       DO UPDATE SET student_answer = EXCLUDED.student_answer, answered_at = EXCLUDED.answered_at
                       RETURNING turn_id""",
                    (turn_id, session_id, org_id, question_index, sub, question_id, answer_text, now))
        actual_turn_id = str(cur.fetchone()[0])
        cur.execute("UPDATE exam_session SET current_turn_index = GREATEST(current_turn_index, %s + 1) WHERE session_id = %s::uuid",
                    (question_index, session_id))
    repo.conn.commit()

    with repo.conn.cursor() as cur:
        cur.execute("""SELECT student_answer FROM session_turn
                       WHERE session_id = %s::uuid AND question_id = %s::uuid AND turn_id != %s::uuid
                       ORDER BY answered_at""",
                    (session_id, question_id, actual_turn_id))
        prior = [r[0] for r in cur.fetchall() if r[0]]

    use_eds = bool(expected_path.get("nodes"))
    probe_directive = ""
    if use_eds:
        seen_n, seen_e = _prior_coverage(repo, session_id, question_index)
        target = _probe_target(expected_path, seen_n, seen_e)
        if target:
            probe_directive = f"\nPROBE TARGET (choose your probe to address this):\n{target}\n"
        system_prompt, _ = build_examiner_eval_prompt(repo, org_id, question_text, expected_path, probe_directive)
    else:
        system_prompt = ("You are an Epistemy Socratic oral examiner. "
                         f"The current exam question is: \"{question_text}\". "
                         "When the answer is incomplete, ask ONE short guiding sub-question. "
                         "Respond ONLY with minified JSON: "
                         '{"clarify": false, "answered": true, "adequate": false, '
                         '"feedback": "one sentence", "probe": "one short follow-up"}')

    ctx = f"Exam question: {question_text}\n\n"
    if prior:
        ctx += "Prior exchanges on this question:\n" + "".join(f"Student: {p}\n" for p in prior) + "\n"
    ctx += f"Student's latest answer: {answer_text}"

    answered = adequate = False; feedback = probe = ""; parsed = {}
    try:
        parsed = call_bedrock(settings, system_prompt, ctx,
                              max_tokens=LLM_MAX_TOKENS_EVALUATION, temperature=0.1)
        answered = bool(parsed.get("answered", False)); adequate = bool(parsed.get("adequate", False))
        feedback = (parsed.get("feedback") or "").strip(); probe = (parsed.get("probe") or "").strip()
    except Exception:
        answered, adequate, feedback, probe = _heuristic_eval(answer_text)

    if not use_eds:
        eds_delta = 0 if not answered else (10 if adequate else 4)
        with repo.conn.cursor() as cur:
            cur.execute("""INSERT INTO evaluation
                           (evaluation_id, turn_id, org_id, course_id, student_id, question_id, eds_score, eds_bucket, raw_llm_output)
                           VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s::uuid, %s, %s, %s::jsonb)
                           ON CONFLICT (turn_id) DO UPDATE SET eds_score = EXCLUDED.eds_score,
                               eds_bucket = EXCLUDED.eds_bucket, raw_llm_output = EXCLUDED.raw_llm_output""",
                        (str(_uuid.uuid4()), actual_turn_id, org_id, course_id, student_id, question_id,
                         eds_delta / 10.0, "high" if adequate else ("medium" if answered else "low"),
                         _json.dumps({"answered": answered, "adequate": adequate, "feedback": feedback, "probe": probe})))
        repo.conn.commit()
        return {"answered": answered, "adequate": adequate, "feedback": feedback,
                "probe": probe, "eds_delta": eds_delta}

    eds_raw = parsed.get("eds", {}) if isinstance(parsed, dict) else {}
    exp_nodes = expected_path.get("nodes", []); exp_edges = expected_path.get("edges", [])
    exp_ext = expected_path.get("extensions", [])
    nodes_dem = eds_raw.get("nodes_demonstrated", []); edges_dem = eds_raw.get("edges_demonstrated", [])
    recit = float(eds_raw.get("recitation_score", 0.5)); novel = eds_raw.get("novel_extensions", [])
    R = 1.0 - recit
    node_score = len(nodes_dem) / max(len(exp_nodes), 1)
    edge_score = len(edges_dem) / max(len(exp_edges), 1)
    gen_norm = min(1.0, len(novel) / max(len(exp_ext), 3))
    with repo.conn.cursor() as cur:
        cur.execute("""SELECT e.eds_components FROM evaluation e JOIN session_turn st ON st.turn_id = e.turn_id
                       WHERE st.session_id = %s::uuid AND st.turn_index = %s AND e.turn_id != %s::uuid
                       ORDER BY st.answered_at""",
                    (session_id, question_index, actual_turn_id))
        prior_comp = [r[0] for r in cur.fetchall() if r[0]]
    all_n, all_e, all_x = set(), set(), set(); min_recit = recit
    for pc in prior_comp:
        if isinstance(pc, str): pc = _json.loads(pc)
        all_n.update(pc.get("nodes_detected", [])); all_e.update(pc.get("edges_demonstrated", []))
        all_x.update(pc.get("novel_extensions", [])); min_recit = min(min_recit, pc.get("raw_probe_score", 1.0))
    all_n.update(nodes_dem); all_e.update(edges_dem); all_x.update(novel); min_recit = min(min_recit, recit)
    agg_R = 1.0 - min_recit
    agg_node = len(all_n) / max(len(exp_nodes), 1); agg_edge = len(all_e) / max(len(exp_edges), 1)
    agg_gen = min(1.0, len(all_x) / max(len(exp_ext), 3)); agg_cov = (agg_node + agg_edge) / 2.0
    eds_q = agg_R * (EDS_ALPHA * agg_node + EDS_BETA * agg_edge) + EDS_GAMMA * (1.0 - agg_R * agg_cov) * agg_gen
    eds_q = round(min(1.0, max(0.0, eds_q)), 4)
    comp = {"node_score": node_score, "edge_score": edge_score, "r_gate": R, "gen_score_norm": gen_norm,
            "nodes_detected": list(nodes_dem), "edges_demonstrated": list(edges_dem),
            "novel_extensions": list(novel), "raw_probe_score": recit}
    with repo.conn.cursor() as cur:
        cur.execute("""INSERT INTO evaluation
                       (evaluation_id, turn_id, org_id, course_id, student_id, question_id, eds_score, eds_bucket, raw_llm_output, eds_components)
                       VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s::uuid, %s, %s, %s::jsonb, %s::jsonb)
                       ON CONFLICT (turn_id) DO UPDATE SET eds_score = EXCLUDED.eds_score, eds_bucket = EXCLUDED.eds_bucket,
                           raw_llm_output = EXCLUDED.raw_llm_output, eds_components = EXCLUDED.eds_components""",
                    (str(_uuid.uuid4()), actual_turn_id, org_id, course_id, student_id, question_id, eds_q,
                     "high" if eds_q >= 0.7 else ("medium" if eds_q >= 0.3 else "low"),
                     _json.dumps({"answered": answered, "adequate": adequate, "feedback": feedback,
                                  "probe": probe, "eds_question": eds_q}), _json.dumps(comp)))
        try:
            cur.execute("""INSERT INTO question_eds_aggregate
                           (session_id, question_id, org_id, node_score, edge_score, r_gate, gen_score_norm, coverage, final_eds, turn_details)
                           VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s::jsonb)
                           ON CONFLICT (session_id, question_id) DO UPDATE SET node_score = EXCLUDED.node_score,
                               edge_score = EXCLUDED.edge_score, r_gate = EXCLUDED.r_gate, gen_score_norm = EXCLUDED.gen_score_norm,
                               coverage = EXCLUDED.coverage, final_eds = EXCLUDED.final_eds, turn_details = EXCLUDED.turn_details, computed_at = NOW()""",
                        (session_id, question_id, org_id, agg_node, agg_edge, agg_R, agg_gen, agg_cov, eds_q, _json.dumps(comp)))
        except Exception:
            repo.conn.rollback()
    repo.conn.commit()
    return {"answered": answered, "adequate": adequate, "feedback": feedback, "probe": probe,
            "eds_delta": int(eds_q * 10), "eds_question": eds_q,
            "eds_components": {"node_score": agg_node, "edge_score": agg_edge, "r_gate": agg_R, "gen_score": agg_gen}}


def _register_demo(app: FastAPI, deps) -> None:
    """Admin link-minting (authenticated) + public token-scoped demo endpoints."""
    import uuid as _uuid, secrets as _secrets, json as _json

    @app.post(R.ADMIN_DEMO_LINKS)
    def create_demo_link(req: DemoLinkCreateRequest, x_org_name: str = Header(...),
                         x_user_id: str = Header("operator"),
                         x_role: str = Header("platform_admin")):
        """Mint a credential-free demo link for one assignment (platform_admin)."""
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PLATFORM_ADMIN:
                    raise AuthorizationError("platform_admin role required")
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.demo_link')")
                    if cur.fetchone()[0] is None:
                        return {"status": "error", "message": "demo_link table not present — run migration_026."}
                    cur.execute("SELECT 1 FROM assignment WHERE assignment_id = %s::uuid", (req.assignment_id,))
                    if not cur.fetchone():
                        return {"status": "error", "message": "assignment not found"}
                    token = _secrets.token_urlsafe(16)
                    cur.execute("""INSERT INTO demo_link (token, org_id, assignment_id, max_attempts, expires_at, created_by)
                                   VALUES (%s, %s::uuid, %s::uuid, %s, NOW() + (%s || ' days')::interval, %s)""",
                                (token, caller.org_id, req.assignment_id, req.max_attempts, req.days, caller.user_id))
                repo.conn.commit()
                return {"token": token, "url": DEMO_URL_BASE + token,
                        "assignment_id": req.assignment_id, "max_attempts": req.max_attempts, "days": req.days}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.ASSIGNMENT_DEMO_LINK)
    def create_assignment_demo_link(assignment_id: str, req: DemoLinkOptionsRequest,
                                    x_org_name: str = Header(...),
                                    x_user_id: str = Header("operator"),
                                    x_role: str = Header("professor")):
        """Professor mints a credential-free demo link for one of their org's assignments."""
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role not in (Role.PROFESSOR, Role.PLATFORM_ADMIN):
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)  # RLS scopes the assignment to the caller's org
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.demo_link')")
                    if cur.fetchone()[0] is None:
                        return {"status": "error", "message": "demo_link table not present — run migration_026."}
                    cur.execute("SELECT title FROM assignment WHERE assignment_id = %s::uuid", (assignment_id,))
                    arow = cur.fetchone()
                    if not arow:
                        raise AuthorizationError("assignment not found")
                    token = _secrets.token_urlsafe(16)
                    cur.execute("""INSERT INTO demo_link (token, org_id, assignment_id, max_attempts, expires_at, created_by)
                                   VALUES (%s, %s::uuid, %s::uuid, %s, NOW() + (%s || ' days')::interval, %s)""",
                                (token, caller.org_id, assignment_id, req.max_attempts, req.days, caller.user_id))
                repo.conn.commit()
                return {"token": token, "url": DEMO_URL_BASE + token, "title": arow[0],
                        "assignment_id": assignment_id, "days": req.days, "max_attempts": req.max_attempts}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    def _with_demo(token, fn):
        """Open a repo, validate the link, set tenant, and run fn(d, repo, link)."""
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                with repo.conn.cursor() as cur:
                    link, err = _demo_link_or_error(cur, token)
                if err:
                    return err
                repo.set_tenant(link[1])  # org_id
                return fn(d, repo, link)
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.DEMO_META)
    def demo_meta(token: str):
        """Demo landing info (does NOT consume an attempt)."""
        def _fn(d, repo, link):
            org_id, assignment_id = link[1], str(link[2])
            with repo.conn.cursor() as cur:
                cur.execute("""SELECT a.title, a.question_set_id FROM assignment a WHERE a.assignment_id = %s::uuid""",
                            (assignment_id,))
                a = cur.fetchone()
                cur.execute("SELECT COUNT(*) FROM question_set_membership WHERE question_set_id = %s::uuid",
                            (str(a[1]),) if a else (None,))
                qc = cur.fetchone()[0] if a else 0
            return {"assignment_id": assignment_id, "title": a[0] if a else "Demo",
                    "question_count": qc, "attempts_remaining": link[3] - link[4],
                    "text_first": get_text_first(repo, org_id)}
        return _with_demo(token, _fn)

    @app.get(R.DEMO_CASE)
    def demo_case(token: str):
        def _fn(d, repo, link):
            assignment_id = str(link[2])
            mats = []
            try:
                with repo.conn.cursor() as cur:
                    cur.execute("""SELECT m.material_id, mv.material_version_id, m.file_name, m.source_type
                                   FROM assignment_case ac
                                   JOIN material_version mv ON mv.material_version_id = ac.material_version_id
                                   JOIN material m ON m.material_id = mv.material_id
                                   WHERE ac.assignment_id = %s::uuid""", (assignment_id,))
                    mats = [{"material_id": str(r[0]), "version_id": str(r[1]),
                             "file_name": r[2], "source_type": r[3]} for r in cur.fetchall()]
            except Exception:
                repo.conn.rollback()
            return {"materials": mats}
        return _with_demo(token, _fn)

    @app.post(R.DEMO_START)
    def demo_start(token: str):
        """Start a demo session (consumes one attempt). Anonymous throwaway identity."""
        def _fn(d, repo, link):
            org_id, assignment_id = link[1], str(link[2])
            with repo.conn.cursor() as cur:
                # Consume an attempt atomically (re-check cap under the row lock).
                cur.execute("""UPDATE demo_link SET attempts_used = attempts_used + 1
                               WHERE token = %s AND attempts_used < max_attempts RETURNING attempts_used""",
                            (link[0],))
                got = cur.fetchone()
                if not got:
                    repo.conn.rollback()
                    return {"status": "exhausted", "message": "This demo has reached its attempt limit."}
                cur.execute("SELECT course_id, question_set_id FROM assignment WHERE assignment_id = %s::uuid",
                            (assignment_id,))
                arow = cur.fetchone()
            repo.conn.commit()
            course_id = str(arow[0]); question_set_id = str(arow[1])
            student_id = f"demo:{link[0][:10]}:{_uuid.uuid4().hex[:8]}"
            session_id = str(_uuid.uuid4())
            with repo.conn.cursor() as cur:
                cur.execute("""INSERT INTO exam_session
                               (session_id, assignment_id, student_id, org_id, course_id, status, current_turn_index, questions_delivered, concepts_covered, is_preview)
                               VALUES (%s::uuid, %s::uuid, %s, %s::uuid, %s::uuid, 'active', 0, '[]'::jsonb, '[]'::jsonb, false)""",
                            (session_id, assignment_id, student_id, org_id, course_id))
                cur.execute("""SELECT q.question_id, q.text, q.concept_ids, qsm.position
                               FROM question_set_membership qsm JOIN question q ON q.question_id = qsm.question_id
                               WHERE qsm.question_set_id = %s::uuid ORDER BY qsm.position""", (question_set_id,))
                qrows = cur.fetchall()
            repo.conn.commit()
            questions = [{"question_id": str(q[0]), "topic": (q[2][0] if isinstance(q[2], list) and q[2] else "general"),
                          "text": q[1], "index": q[3]} for q in qrows]
            return {"session_id": session_id, "questions": questions, "demo_student_id": student_id}
        return _with_demo(token, _fn)

    @app.post(R.DEMO_ANSWER)
    def demo_answer(token: str, req: SubmitAnswerRequest, session_id: str):
        """Submit a demo answer. session_id (query) must belong to this token."""
        def _fn(d, repo, link):
            org_id, assignment_id = link[1], str(link[2])
            with repo.conn.cursor() as cur:
                cur.execute("""SELECT student_id, course_id FROM exam_session
                               WHERE session_id = %s::uuid AND assignment_id = %s::uuid AND org_id = %s::uuid""",
                            (session_id, assignment_id, org_id))
                srow = cur.fetchone()
            if not srow or not str(srow[0]).startswith(f"demo:{link[0][:10]}:"):
                return {"status": "forbidden", "message": "session does not belong to this demo"}
            with repo.conn.cursor() as cur:
                cur.execute("SELECT question_set_id FROM assignment WHERE assignment_id = %s::uuid", (assignment_id,))
                qset = str(cur.fetchone()[0])
            return _demo_answer_turn(repo, d["settings"], str(org_id), str(srow[1]), str(srow[0]),
                                     session_id, qset, req.question_index, req.answer_text)
        return _with_demo(token, _fn)

    @app.get(R.DEMO_STATUS)
    def demo_status(token: str, session_id: str):
        def _fn(d, repo, link):
            with repo.conn.cursor() as cur:
                cur.execute("SELECT status, current_turn_index FROM exam_session WHERE session_id = %s::uuid AND org_id = %s::uuid",
                            (session_id, link[1]))
                s = cur.fetchone()
            if not s:
                return {"status": "not_found"}
            return {"session_id": session_id, "status": s[0], "current_turn": s[1] or 0,
                    "total_questions": 0, "eds_score": 0.0, "turns": []}
        return _with_demo(token, _fn)

    @app.post(R.DEMO_COMPLETE)
    def demo_complete(token: str, session_id: str):
        def _fn(d, repo, link):
            with repo.conn.cursor() as cur:
                cur.execute("UPDATE exam_session SET status = 'completed' WHERE session_id = %s::uuid AND org_id = %s::uuid",
                            (session_id, link[1]))
            repo.conn.commit()
            return {"status": "completed"}
        return _with_demo(token, _fn)

    @app.post(R.DEMO_TTS)
    def demo_tts(token: str, req: DemoTTSRequest):
        from backend import tts_helper
        def _fn(d, repo, link):
            audio = tts_helper.synthesize(d["settings"], req.text)
            if not audio:
                raise HTTPException(status_code=503, detail="tts unavailable")
            from fastapi import Response
            return Response(content=audio, media_type="audio/mpeg")
        return _with_demo(token, _fn)


def _register_routes(app: FastAPI, deps) -> None:
    """Attach all module endpoints."""
    _register_auth(app, deps)
    from backend.app.auth_routes import register_auth_routes
    register_auth_routes(app, deps)
    _register_health(app, deps)
    _register_materials(app, deps)
    _register_reads(app, deps)
    _register_search(app, deps)
    _register_dashboard(app, deps)
    _register_student_dashboard(app, deps)
    _register_graph(app, deps)
    _register_questions(app, deps)
    _register_delivery(app, deps)
    _register_evaluation(app, deps)
    _register_delete_endpoints(app, deps)
    _register_course_ops(app, deps)
    _register_tts(app, deps)
    _register_admin_simulations(app, deps)
    _register_admin_testing(app, deps)
    _register_demo(app, deps)


class CourseCreateRequest(BaseModel):
    """POST body to create a course."""
    name: str = Field(..., min_length=1, max_length=200)


class EnrollRequest(BaseModel):
    """POST body to enroll students by email (single or CSV-parsed list)."""
    emails: List[str] = Field(default_factory=list)


class SessionRequest(BaseModel):
    """Body to create or update a class session; all fields optional."""
    session_date: Optional[date] = None
    session_document: Optional[str] = Field(default=None, max_length=100000)
    # Concept-graph node ids that are in scope for this week. None = "not
    # provided" (leave unchanged on update); [] = explicitly no scope.
    in_scope_concepts: Optional[List[str]] = None


def _session_scope_col(cur) -> bool:
    """True if class_session.in_scope_concepts exists (migration_012).

    Guarded so session endpoints keep working on a DB that hasn't run the
    migration yet — they just omit the scope until it's present."""
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name='class_session'
                     AND column_name='in_scope_concepts'""")
    return cur.fetchone() is not None


class SyllabusSetRequest(BaseModel):
    """POST body marking an already-uploaded material as the course syllabus."""
    material_id: Optional[str] = None
    material_version_id: Optional[str] = None
    file_name: Optional[str] = None


class SyllabusProcessRequest(BaseModel):
    """POST body to turn the syllabus into class sessions. When `text` is given
    it is parsed directly; otherwise the stored syllabus's extracted text is used."""
    text: Optional[str] = Field(default=None, max_length=200000)


def _ensure_enrollment_table(repo) -> None:
    """Create the roster table on first use.

    The runtime app role is a non-owner without CREATE on schema public, and
    `CREATE TABLE IF NOT EXISTS` still triggers that privilege check even when
    the table exists — so skip it entirely once the table is present."""
    with repo.conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.enrollment')")
        if cur.fetchone()[0] is not None:
            return
        cur.execute(
            """CREATE TABLE IF NOT EXISTS enrollment (
                   org_id UUID NOT NULL,
                   course_id UUID NOT NULL,
                   student_email TEXT NOT NULL,
                   created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                   PRIMARY KEY (course_id, student_email))""")
    repo.conn.commit()


def _course_has_syllabus(repo, course_id: str) -> bool:
    """True if the course has a syllabus attached (course_syllabus row)."""
    _ensure_syllabus_table(repo)
    with repo.conn.cursor() as cur:
        cur.execute("SELECT 1 FROM course_syllabus WHERE course_id = %s::uuid", (course_id,))
        return cur.fetchone() is not None


def _require_syllabus(repo, course_id: str) -> None:
    """No-op: the syllabus is now OPTIONAL and no longer gates course actions.

    Previously every substantive action (build the graph, generate/assign exams,
    create sessions) required a syllabus first. With the move to
    Course → Material[Topics] → Assignment, a course is fully usable without a
    syllabus, so this gate is intentionally disabled. Kept as a no-op (rather
    than removed) so the call sites and the re-enable path stay intact."""
    return


def _require_unique_session_topic(cur, course_id: str, org_id: str,
                                  document, exclude_session_id: str = None) -> None:
    """409 if another session in the course already uses this topic/title.

    Case-insensitive, trimmed match. Blank topics are exempt (undated/untitled
    sessions may repeat). Reusing an existing topic on the SAME session is fine.
    """
    topic = (document or "").strip()
    if not topic:
        return
    sql = ("SELECT 1 FROM class_session WHERE course_id = %s::uuid AND org_id = %s::uuid "
           "AND lower(btrim(session_document)) = lower(%s)")
    params = [course_id, org_id, topic]
    if exclude_session_id:
        sql += " AND session_id <> %s::uuid"
        params.append(exclude_session_id)
    cur.execute(sql + " LIMIT 1", tuple(params))
    if cur.fetchone():
        raise HTTPException(
            status_code=409,
            detail=f'A session titled "{topic}" already exists in this course.')


def _start_is_preview(status, owner, caller_role, caller_user_id) -> bool:
    """Return True for a professor previewing their own draft; False for a
    normal active start; raise AuthorizationError for anything else."""
    if status == "active":
        return False
    if (status == "draft" and caller_role == Role.PROFESSOR
            and owner == caller_user_id):
        return True
    raise AuthorizationError(f"assignment is not active (status: {status})")


def _ensure_syllabus_table(repo) -> None:
    """Create the per-course syllabus pointer table on first use.

    Skips the CREATE when the table exists — the non-owner app role lacks CREATE
    on schema public, which `CREATE TABLE IF NOT EXISTS` still checks."""
    with repo.conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.course_syllabus')")
        if cur.fetchone()[0] is not None:
            return
        cur.execute(
            """CREATE TABLE IF NOT EXISTS course_syllabus (
                   course_id UUID PRIMARY KEY,
                   org_id UUID NOT NULL,
                   material_id UUID,
                   material_version_id UUID,
                   file_name TEXT,
                   created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    repo.conn.commit()


# Ordered wipe for course deletion — FKs to course have no ON DELETE CASCADE,
# so children are removed before parents (course_id where present, else subquery).
# FK-safe delete order. Several tables reference course / question / exam_session
# with NO ON DELETE CASCADE, so every one must be cleared before its parent or
# "Remove course" fails with a foreign-key violation. `_delete_course_rows`
# skips any table not present in this DB (to_regclass guard), so it's safe across
# environments/migrations.
_COURSE_DELETE_STMTS = (
    "DELETE FROM question_eds_aggregate WHERE session_id IN (SELECT session_id FROM exam_session WHERE course_id = %s::uuid)",
    "DELETE FROM graph_eds_results WHERE course_id = %s::uuid",
    "DELETE FROM evaluation WHERE course_id = %s::uuid",
    "DELETE FROM session_turn WHERE session_id IN (SELECT session_id FROM exam_session WHERE course_id = %s::uuid)",
    "DELETE FROM grade WHERE course_id = %s::uuid",
    "DELETE FROM exam_session WHERE course_id = %s::uuid",
    "DELETE FROM assignment WHERE course_id = %s::uuid",
    "DELETE FROM question_set_membership WHERE question_set_id IN (SELECT question_set_id FROM question_set WHERE course_id = %s::uuid)",
    "DELETE FROM question_set WHERE course_id = %s::uuid",
    "DELETE FROM question WHERE course_id = %s::uuid",
    "DELETE FROM generation_job WHERE course_id = %s::uuid",   # after question (question.generation_job_id → generation_job)
    "DELETE FROM chunk WHERE course_id = %s::uuid",
    "DELETE FROM material_version WHERE material_id IN (SELECT material_id FROM material WHERE course_id = %s::uuid)",
    "DELETE FROM material WHERE course_id = %s::uuid",
    "DELETE FROM graph_version WHERE course_id = %s::uuid",
    "DELETE FROM document_concept WHERE course_id = %s::uuid",
    "DELETE FROM document_concept_edge WHERE course_id = %s::uuid",
    "DELETE FROM course_concept WHERE course_id = %s::uuid",
    "DELETE FROM course_concept_edge WHERE course_id = %s::uuid",
    "DELETE FROM class_session WHERE course_id = %s::uuid",
    "DELETE FROM enrollment WHERE course_id = %s::uuid",
    "DELETE FROM course_syllabus WHERE course_id = %s::uuid",
    "DELETE FROM course WHERE course_id = %s::uuid",
)


def _delete_course_rows(cur, course_id: str) -> None:
    """Run the ordered course-delete statements, skipping tables absent in this
    DB. One statement per savepoint so a skippable error can't abort the txn."""
    for sql in _COURSE_DELETE_STMTS:
        table = sql.split("FROM", 1)[1].split()[0]
        cur.execute("SELECT to_regclass(%s)", ("public." + table,))
        if cur.fetchone()[0] is None:
            continue
        cur.execute(sql, (course_id,))


def _register_course_ops(app: FastAPI, deps) -> None:
    """Course lifecycle (create/delete), roster enrollment, and syllabus."""
    import json as _json  # in scope for every handler below (sessions, syllabus)

    def _pro(x_user_id, x_role, x_org_name, repo, d):
        api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
        caller = api.caller_for_org(x_user_id, x_role, x_org_name)
        if caller.role != Role.PROFESSOR:
            raise AuthorizationError("professor role required")
        repo.set_tenant(caller.org_id)
        return caller

    @app.post(R.COURSE_CREATE)
    def create_course(req: CourseCreateRequest, x_org_name: str = Header(...),
                      x_user_id: str = Header("operator"), x_role: str = Header("professor")):
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                caller = _pro(x_user_id, x_role, x_org_name, repo, d)
                course = repo.get_or_create_course(caller.org_id, req.name.strip(), caller.user_id)
                return {"course_id": str(course.course_id), "course_name": course.course_name}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.delete(R.COURSE_GET)
    def delete_course(course_id: str, x_org_name: str = Header(...),
                      x_user_id: str = Header("operator"), x_role: str = Header("professor")):
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                caller = _pro(x_user_id, x_role, x_org_name, repo, d)
                _ensure_enrollment_table(repo); _ensure_syllabus_table(repo)
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT course_name FROM course WHERE course_id = %s::uuid AND org_id = %s::uuid",
                                (course_id, caller.org_id))
                    row = cur.fetchone()
                if not row:
                    raise AuthorizationError("course not found")
                with repo.conn.cursor() as cur:
                    _delete_course_rows(cur, course_id)
                repo.conn.commit()
                return {"status": "deleted", "course_id": course_id, "course_name": row[0]}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.COURSE_STUDENTS)
    def list_students(course_id: str, x_org_name: str = Header(...),
                      x_user_id: str = Header("operator"), x_role: str = Header("professor")):
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                caller = _pro(x_user_id, x_role, x_org_name, repo, d)
                _ensure_enrollment_table(repo)
                with repo.conn.cursor() as cur:
                    cur.execute("""SELECT student_email, created_at FROM enrollment
                                   WHERE course_id = %s::uuid AND org_id = %s::uuid
                                   ORDER BY student_email""", (course_id, caller.org_id))
                    rows = cur.fetchall()
                return {"students": [{"email": r[0], "enrolled_at": r[1].isoformat() if r[1] else None} for r in rows]}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.COURSE_PERFORMANCE)
    def course_performance(course_id: str, x_org_name: str = Header(...),
                           x_user_id: str = Header("operator"), x_role: str = Header("professor")):
        """Anonymized class performance on this course's practice tests."""
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                caller = _pro(x_user_id, x_role, x_org_name, repo, d)
                return _query_course_performance(repo, caller.org_id, course_id)
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── Class sessions (a course maps to N sessions) ──────────────────────────
    @app.get(R.COURSE_SESSIONS)
    def list_sessions(course_id: str, x_org_name: str = Header(...),
                      x_user_id: str = Header("operator"), x_role: str = Header("professor")):
        """List a course's class sessions (most recent first)."""
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                caller = _pro(x_user_id, x_role, x_org_name, repo, d)
                with repo.conn.cursor() as cur:
                    has_scope = _session_scope_col(cur)
                    scope_col = ", in_scope_concepts" if has_scope else ""
                    cur.execute(
                        f"""SELECT session_id, session_date, session_document, created_at{scope_col}
                           FROM class_session
                           WHERE course_id = %s::uuid AND org_id = %s::uuid
                           ORDER BY session_date DESC NULLS LAST, created_at DESC""",
                        (course_id, caller.org_id))
                    rows = cur.fetchall()
                    # Files attached to each session (a material maps to a session).
                    cur.execute(
                        """SELECT session_id, material_id, display_name FROM material
                           WHERE course_id = %s::uuid AND org_id = %s::uuid
                                 AND session_id IS NOT NULL""",
                        (course_id, caller.org_id))
                    mats: dict = {}
                    for sid, mid, name in cur.fetchall():
                        mats.setdefault(str(sid), []).append(
                            {"material_id": str(mid), "display_name": name})
                def _scope_of(r):
                    if not has_scope:
                        return []
                    raw = r[4]
                    return raw if isinstance(raw, list) else (_json.loads(raw) if raw else [])
                return {"sessions": [
                    {"session_id": str(r[0]),
                     "session_date": r[1].isoformat() if r[1] else None,
                     "session_document": r[2],
                     "created_at": r[3].isoformat() if r[3] else None,
                     "in_scope_concepts": _scope_of(r),
                     "materials": mats.get(str(r[0]), [])}
                    for r in rows]}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.COURSE_SESSIONS)
    def create_session(course_id: str, req: SessionRequest, x_org_name: str = Header(...),
                       x_user_id: str = Header("operator"), x_role: str = Header("professor")):
        """Create a class session on the course."""
        import uuid as _uuid

        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                caller = _pro(x_user_id, x_role, x_org_name, repo, d)
                _require_syllabus(repo, course_id)
                sid = str(_uuid.uuid4())
                scope = req.in_scope_concepts or []
                with repo.conn.cursor() as cur:
                    _require_unique_session_topic(cur, course_id, caller.org_id,
                                                  req.session_document)
                    if _session_scope_col(cur):
                        cur.execute(
                            """INSERT INTO class_session
                               (session_id, course_id, org_id, session_date, session_document,
                                created_by, in_scope_concepts)
                               VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s::jsonb)""",
                            (sid, course_id, caller.org_id, req.session_date,
                             req.session_document, caller.user_id, _json.dumps(scope)))
                    else:
                        cur.execute(
                            """INSERT INTO class_session
                               (session_id, course_id, org_id, session_date, session_document, created_by)
                               VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s)""",
                            (sid, course_id, caller.org_id, req.session_date,
                             req.session_document, caller.user_id))
                repo.conn.commit()
                return {"session_id": sid,
                        "session_date": req.session_date.isoformat() if req.session_date else None,
                        "session_document": req.session_document,
                        "in_scope_concepts": scope}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.put(R.COURSE_SESSION)
    def update_session(course_id: str, session_id: str, req: SessionRequest,
                       x_org_name: str = Header(...), x_user_id: str = Header("operator"),
                       x_role: str = Header("professor")):
        """Update a class session's date and/or document."""
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                caller = _pro(x_user_id, x_role, x_org_name, repo, d)
                with repo.conn.cursor() as cur:
                    # Enforce unique titles only when the title actually changes —
                    # a scope-only save resends the existing title and must not trip
                    # the guard (which would 409 whenever a duplicate title exists,
                    # e.g. from a syllabus with repeated class titles).
                    cur.execute("""SELECT session_document FROM class_session
                                   WHERE session_id = %s::uuid AND course_id = %s::uuid AND org_id = %s::uuid""",
                                (session_id, course_id, caller.org_id))
                    _cur_row = cur.fetchone()
                    _current_title = ((_cur_row[0] if _cur_row else None) or "").strip().lower()
                    if (req.session_document or "").strip().lower() != _current_title:
                        _require_unique_session_topic(cur, course_id, caller.org_id,
                                                      req.session_document, session_id)
                    sets = ["session_date = %s", "session_document = %s"]
                    params = [req.session_date, req.session_document]
                    # Only touch scope when the caller provided it, so a plain
                    # date/document edit doesn't wipe the week's in-scope set.
                    if req.in_scope_concepts is not None and _session_scope_col(cur):
                        sets.append("in_scope_concepts = %s::jsonb")
                        params.append(_json.dumps(req.in_scope_concepts))
                    params += [session_id, course_id, caller.org_id]
                    cur.execute(
                        f"""UPDATE class_session SET {', '.join(sets)}
                           WHERE session_id = %s::uuid AND course_id = %s::uuid AND org_id = %s::uuid""",
                        tuple(params))
                    updated = cur.rowcount
                repo.conn.commit()
                if not updated:
                    return {"status": "error", "message": "session not found"}
                return {"status": "updated", "session_id": session_id}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.delete(R.COURSE_SESSION)
    def delete_session(course_id: str, session_id: str, x_org_name: str = Header(...),
                       x_user_id: str = Header("operator"), x_role: str = Header("professor")):
        """Delete a class session."""
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                caller = _pro(x_user_id, x_role, x_org_name, repo, d)
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """DELETE FROM class_session
                           WHERE session_id = %s::uuid AND course_id = %s::uuid AND org_id = %s::uuid""",
                        (session_id, course_id, caller.org_id))
                repo.conn.commit()
                return {"status": "deleted", "session_id": session_id}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.COURSE_STUDENTS)
    def enroll_students(course_id: str, req: EnrollRequest, x_org_name: str = Header(...),
                        x_user_id: str = Header("operator"), x_role: str = Header("professor")):
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                caller = _pro(x_user_id, x_role, x_org_name, repo, d)
                _ensure_enrollment_table(repo)
                # Accept single emails or comma/semicolon/whitespace-joined strings.
                emails = _parse_emails(req.emails)
                added = 0
                with repo.conn.cursor() as cur:
                    for e in emails:
                        cur.execute("""INSERT INTO enrollment (org_id, course_id, student_email)
                                       VALUES (%s::uuid, %s::uuid, %s)
                                       ON CONFLICT (course_id, student_email) DO NOTHING""",
                                    (caller.org_id, course_id, e))
                        added += cur.rowcount
                    cur.execute("SELECT student_email FROM enrollment WHERE course_id = %s::uuid ORDER BY student_email", (course_id,))
                    roster = [r[0] for r in cur.fetchall()]
                repo.conn.commit()
                return {"status": "ok", "added": added, "skipped": len(emails) - added,
                        "count": len(roster), "students": [{"email": e} for e in roster]}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.delete(R.COURSE_STUDENTS)
    def unenroll_student(course_id: str, email: str, x_org_name: str = Header(...),
                         x_user_id: str = Header("operator"), x_role: str = Header("professor")):
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                _pro(x_user_id, x_role, x_org_name, repo, d)
                _ensure_enrollment_table(repo)
                e = email.strip().lower()
                with repo.conn.cursor() as cur:
                    cur.execute("DELETE FROM enrollment WHERE course_id = %s::uuid AND student_email = %s",
                                (course_id, e))
                    # Also clear the authoritative auth-side enrollment, else a later
                    # public-mirror rebuild would resurrect the dropped student. The
                    # DELETE grant ships in migration_009; the savepoint lets this
                    # degrade cleanly on an un-migrated DB without losing the public delete.
                    cur.execute("SAVEPOINT drop_auth_enrollment")
                    try:
                        cur.execute(
                            """DELETE FROM auth.enrollment ae
                                 USING auth.app_user au
                                WHERE ae.app_user_id = au.id
                                  AND ae.course_id = %s::uuid
                                  AND lower(au.email) = %s""",
                            (course_id, e))
                        cur.execute("RELEASE SAVEPOINT drop_auth_enrollment")
                    except Exception:  # noqa: BLE001 - missing grant on un-migrated DB
                        cur.execute("ROLLBACK TO SAVEPOINT drop_auth_enrollment")
                repo.conn.commit()
                return {"status": "removed", "email": email}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.COURSE_SYLLABUS)
    def get_syllabus(course_id: str, x_org_name: str = Header(...),
                     x_user_id: str = Header("operator"), x_role: str = Header("professor")):
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                _pro(x_user_id, x_role, x_org_name, repo, d)
                _ensure_syllabus_table(repo)
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT material_id, material_version_id, file_name FROM course_syllabus WHERE course_id = %s::uuid", (course_id,))
                    row = cur.fetchone()
                if not row:
                    return {"syllabus": None}
                return {"syllabus": {"material_id": str(row[0]) if row[0] else None,
                                     "version_id": str(row[1]) if row[1] else None,
                                     "file_name": row[2]}}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.COURSE_SYLLABUS)
    def set_syllabus(course_id: str, req: SyllabusSetRequest, x_org_name: str = Header(...),
                     x_user_id: str = Header("operator"), x_role: str = Header("professor")):
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                caller = _pro(x_user_id, x_role, x_org_name, repo, d)
                _ensure_syllabus_table(repo)
                with repo.conn.cursor() as cur:
                    cur.execute("""INSERT INTO course_syllabus
                                   (course_id, org_id, material_id, material_version_id, file_name)
                                   VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s)
                                   ON CONFLICT (course_id) DO UPDATE
                                   SET material_id = EXCLUDED.material_id,
                                       material_version_id = EXCLUDED.material_version_id,
                                       file_name = EXCLUDED.file_name""",
                                (course_id, caller.org_id,
                                 req.material_id or None, req.material_version_id or None,
                                 req.file_name))
                repo.conn.commit()
                # The syllabus is excluded from the concept graph. Its per-document
                # concepts are KEPT (so unmarking restores them) but excluded from
                # every view and from the course-level computation by version id.
                # Recompute the course snapshot now (materials only) so a graph built
                # while this file was an unmarked material drops it. Cheap: pure DB,
                # no LLM. Non-fatal — the pointer is already saved.
                try:
                    with repo.conn.cursor() as cur:
                        snapshot_course_graph(cur, caller.org_id, course_id)
                    repo.conn.commit()
                except Exception as exc:  # noqa: BLE001
                    repo.conn.rollback()
                    logger.warning("Post-syllabus graph recompute failed for %s: %s",
                                   course_id[:8], exc)
                return {"status": "ok", "file_name": req.file_name}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.COURSE_SYLLABUS_PROCESS)
    def process_syllabus(course_id: str, req: SyllabusProcessRequest,
                         x_org_name: str = Header(...), x_user_id: str = Header("operator"),
                         x_role: str = Header("professor")):
        """Parse the course syllabus into class sessions + in-scope topics.

        Uses the pasted text when provided, else the stored syllabus's extracted
        text. Idempotent-ish: if the course already has sessions, it creates
        nothing and returns them, so re-running never duplicates."""
        import uuid as _uuid

        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                caller = _pro(x_user_id, x_role, x_org_name, repo, d)
                _require_syllabus(repo, course_id)

                # Serialize concurrent processing for THIS course. Two overlapping
                # requests (e.g. the auto-run after a syllabus upload racing a
                # manual "Generate sessions" click) would otherwise both pass the
                # "already has sessions?" check below and each insert the full set —
                # the exact-duplicate bug. A transaction-scoped advisory lock makes
                # the check-then-insert atomic per course; it's released when this
                # request's transaction ends (the create path commits; every other
                # path is rolled back on connection release), so the loser sees the
                # winner's sessions and returns "exists". No commits run between the
                # lock and the final insert, so the lock is held across the section.
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", (course_id,))

                # Don't duplicate: if sessions already exist, return them untouched.
                has_scope = None
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM class_session WHERE course_id = %s::uuid AND org_id = %s::uuid",
                                (course_id, caller.org_id))
                    if cur.fetchone()[0] > 0:
                        return {"status": "exists", "created": 0,
                                "message": "This course already has sessions — clear them to regenerate."}
                    has_scope = _session_scope_col(cur)

                # Resolve the schedule. A pasted schedule always uses the text
                # parser. Otherwise prefer the layout/table-aware PDF mapper on the
                # stored syllabus file, falling back to the text parser on the
                # extracted chunks (non-PDF syllabi, or when the mapper finds none).
                pasted = (req.text or "").strip()
                parsed = None
                if pasted:
                    parsed = _parse_syllabus(pasted)
                else:
                    with repo.conn.cursor() as cur:
                        cur.execute("SELECT material_version_id FROM course_syllabus WHERE course_id = %s::uuid",
                                    (course_id,))
                        row = cur.fetchone()
                    vid = str(row[0]) if row and row[0] else None
                    version = repo.get_version(vid) if vid else None
                    if version and getattr(version.source_type, "value", str(version.source_type)) == "pdf":
                        try:
                            pdf_bytes = d["storage"].get_bytes(version.s3_key)
                            parsed = _map_pdf_to_sessions(pdf_bytes)
                            if parsed:
                                logger.info("Syllabus %s parsed via PDF mapper: %d sessions",
                                            course_id[:8], len(parsed))
                        except Exception as exc:  # noqa: BLE001 - fall back to text
                            logger.warning("PDF syllabus mapper failed for %s: %s — falling back to text",
                                           course_id[:8], exc)
                            parsed = None
                    if not parsed:
                        text = ""
                        if vid:
                            chunks = repo.list_chunks(vid)
                            text = "\n".join(c.get("text", "") for c in chunks).strip()
                        if not text:
                            raise HTTPException(
                                status_code=409,
                                detail="The syllabus is still being processed. Try again in a moment, or paste its schedule.")
                        parsed = _parse_syllabus(text)

                if not parsed:
                    raise HTTPException(
                        status_code=422,
                        detail="Couldn't find a weekly schedule in the syllabus. Expected lines like \"Week 1: topic; topic\".")

                year = datetime.now(timezone.utc).year
                # Session titles are unique per course (enforced on manual add/edit).
                # A syllabus can repeat a class title (e.g. two "Introduction"s), so
                # uniquify here — append " (2)", " (3)" — to uphold that invariant.
                # Blank titles are exempt (untitled sessions may repeat).
                _used = set()

                def _uniq_title(t):
                    base = (t or "").strip()
                    if not base:
                        return base
                    if base.lower() not in _used:
                        _used.add(base.lower())
                        return base
                    n = 2
                    while ("%s (%d)" % (base.lower(), n)) in _used:
                        n += 1
                    _used.add("%s (%d)" % (base.lower(), n))
                    return "%s (%d)" % (base, n)

                created = []
                with repo.conn.cursor() as cur:
                    for p in parsed:
                        sid = str(_uuid.uuid4())
                        iso = _normalize_date(p.get("date", ""), year)
                        topics = p.get("topics", [])
                        title = _uniq_title(p.get("title"))
                        if has_scope:
                            cur.execute(
                                """INSERT INTO class_session
                                   (session_id, course_id, org_id, session_date, session_document,
                                    created_by, in_scope_concepts)
                                   VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s::jsonb)""",
                                (sid, course_id, caller.org_id, iso, title,
                                 caller.user_id, _json.dumps(topics)))
                        else:
                            cur.execute(
                                """INSERT INTO class_session
                                   (session_id, course_id, org_id, session_date, session_document, created_by)
                                   VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s)""",
                                (sid, course_id, caller.org_id, iso, title, caller.user_id))
                        created.append({"session_id": sid, "week": p.get("week"),
                                        "title": title, "session_date": iso,
                                        "in_scope_concepts": topics})
                repo.conn.commit()
                return {"status": "created", "created": len(created), "sessions": created}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


class TTSRequest(BaseModel):
    """POST body for the ElevenLabs TTS proxy."""
    text: str = Field(min_length=1, max_length=5000)
    voice_id: Optional[str] = None


def _register_tts(app: FastAPI, deps) -> None:
    """ElevenLabs TTS proxy — keeps the key server-side; 503 when not configured."""
    from starlette.responses import Response
    from backend import tts_helper

    @app.post(R.TTS)
    def synthesize_speech(
        req: TTSRequest,
        x_user_id: str = Header("student"),
        x_role: str = Header("student"),
    ):
        with _timed("tts", chars=len(req.text or "")):
            audio = tts_helper.synthesize(deps()["settings"], req.text, req.voice_id)
        if not audio:
            raise HTTPException(status_code=503, detail="TTS is not configured")
        return Response(content=audio, media_type="audio/mpeg",
                        headers={"Cache-Control": "no-store"})


def _register_auth(app: FastAPI, deps) -> None:
    """Cognito Hosted-UI params (public) + the caller's resolved identity."""

    @app.get("/api/auth/config")
    def auth_config():
        """Public: the PKCE params the SPA needs to reach the Hosted UI.

        Uses settings directly (not deps()) so the login page works even when
        the DB pool is cold or unavailable.
        """
        cognito = factory.build_cognito_config(load_settings())
        return {
            "domain": cognito["domain"],
            "clientId": cognito["client_id"],
            "region": cognito["region"],
            "hostedUiUrl": cognito.get("hosted_ui_url"),
            "scopes": ["openid", "email", "profile"],
        }

    @app.get("/api/auth/me")
    def me(x_user_id: str = Header(...), x_role: str = Header(...),
           x_org_name: str = Header(...)):
        """Authenticated: verified identity for the SPA to route by role."""
        return {"id": x_user_id, "email": x_user_id, "role": x_role,
                "orgId": x_org_name}


def _register_health(app: FastAPI, deps) -> None:
    @app.get(R.HEALTH)
    def health():
        """Deep health check: verifies DB connectivity. Returns 503 if DB is down, or
        while the LLM client is still warming (so the ALB doesn't route a cold task)."""
        if not _LLM_READY:
            raise HTTPException(status_code=503, detail="warming up")
        try:
            d = deps()
            pool = d["pool"]
            conn = factory.get_connection_from_pool(pool)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                return {"status": "ok"}
            finally:
                factory.return_connection_to_pool(pool, conn)
        except Exception as exc:
            logger.warning("Health check failed: %s", exc)
            raise HTTPException(status_code=503, detail="database unavailable")

    @app.get("/config")
    def config():
        return R.frontend_config()


def _register_materials(app: FastAPI, deps) -> None:
    """Name-based presign and register: the UI sends org_name/course_name."""

    # TODO(prod): Replace header-based auth with JWT validation (Cognito ID token)
    # before production deployment.

    @app.post(R.PRESIGN)
    def presign(req: IngestRequest, x_org_name: str = Header(...),
                x_user_id: str = Header("operator"), x_role: str = Header("professor")):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                # x_org_name is the verified tenant UUID; req.org_name is ignored.
                return api.presign_by_name(x_user_id, x_role, x_org_name, req)
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.REGISTER)
    def register(version_id: str, x_org_name: str = Header(...),
                 x_user_id: str = Header("operator"), x_role: str = Header("professor")):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                return api.register_by_name(x_user_id, x_role, x_org_name, version_id)
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


def _register_reads(app: FastAPI, deps) -> None:
    """Name-based read routes so a tester can observe status by names."""
    _register_list_materials(app, deps)
    _register_list_versions(app, deps)
    _register_material_view(app, deps)
    _register_assignment_case(app, deps)


def _register_assignment_case(app: FastAPI, deps) -> None:
    """Case context for an exam: the source material(s) a student can view while
    answering. Student-accessible so the case document stays available throughout."""

    @app.get(R.ASSIGNMENT_CASE)
    def assignment_case(assignment_id: str, x_org_name: str = Header(...),
                        x_user_id: str = Header("operator"),
                        x_role: str = Header("student")):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        "SELECT course_id, config FROM assignment WHERE assignment_id = %s::uuid",
                        (assignment_id,),
                    )
                    row = cur.fetchone()
                if not row:
                    raise AuthorizationError("assignment not found")
                course_id = str(row[0])
                cfg = row[1] if isinstance(row[1], dict) else (_json.loads(row[1]) if row[1] else {})

                # "View Case" is only offered when the professor marked this
                # assignment as case-based. Otherwise return no case materials so
                # the button hides — course docs aren't a "case" (issue S-E-2.1#2).
                if not cfg.get("include_case"):
                    return {"materials": []}

                materials = []
                for m in repo.list_materials(course_id):
                    if not m.current_version_id:
                        continue
                    v = repo.get_version(m.current_version_id)
                    if not v or getattr(v.status, "value", str(v.status)) != "ready":
                        continue
                    materials.append({
                        "material_id": m.material_id,
                        "version_id": v.material_version_id,
                        "file_name": v.file_name,
                        "source_type": getattr(v.source_type, "value",
                                               str(v.source_type)),
                    })
                return {"materials": materials}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


def _register_material_view(app: FastAPI, deps) -> None:
    """GET a short-lived presigned URL so the professor can open the document."""

    @app.get(R.MATERIAL_VIEW)
    def material_view(material_id: str, x_org_name: str = Header(...),
                      x_user_id: str = Header("operator"),
                      x_role: str = Header("professor")):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)

                # The id may be a material_id (use its current version) or a
                # material_version_id directly (the dashboard list surfaces the
                # latter). Resolve either to a concrete version.
                version = None
                material = repo.get_material(material_id)
                if material and material.current_version_id:
                    version = repo.get_version(material.current_version_id)
                if version is None:
                    version = repo.get_version(material_id)
                if version is None:
                    raise AuthorizationError("material not found")

                url = d["storage"].presign_get(
                    version.s3_key, file_name=version.file_name)
                return {
                    "url": url,
                    "file_name": version.file_name,
                    "source_type": getattr(version.source_type, "value",
                                           str(version.source_type)),
                    "version_id": version.material_version_id,
                    "version_no": version.version_no,
                    "status": getattr(version.status, "value",
                                      str(version.status)),
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


def _register_list_materials(app: FastAPI, deps) -> None:
    @app.get(R.LIST_MATERIALS)
    def list_materials(course_name: str, x_org_name: str = Header(...),
                       x_user_id: str = Header("operator"),
                       x_role: str = Header("professor")):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                course_id = api.resolve_course_id(x_org_name, course_name)
                tools = MaterialsTools(repo, api, lambda c, cid: True)
                return tools.list_materials(caller, course_id)
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


def _register_list_versions(app: FastAPI, deps) -> None:
    @app.get(R.LIST_VERSIONS)
    def list_versions(material_id: str, x_org_name: str = Header(...),
                      x_user_id: str = Header("operator"),
                      x_role: str = Header("professor")):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                tools = MaterialsTools(repo, api, lambda c, cid: True)
                return tools.list_material_versions(caller, material_id)
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


class SearchRequest(BaseModel):
    """POST body for /courses/{course_id}/search."""

    query: str = Field(..., min_length=1, max_length=500)
    k: int = Field(default=10, ge=1, le=50)
    material_version_ids: Optional[List[str]] = None


def _register_search(app: FastAPI, deps) -> None:
    """POST /courses/{course_id}/search -- vector retrieval over chunks."""

    @app.post(R.SEARCH_CORPUS)
    def search_corpus(course_id: str, req: SearchRequest,
                      x_org_name: str = Header(...),
                      x_user_id: str = Header("operator"),
                      x_role: str = Header("student")):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                searcher = CorpusSearcher(repo.conn, d["embedder"])
                search_tools = SearchTools(searcher, lambda c, cid: True)
                return search_tools.search_corpus(
                    caller=caller,
                    course_id=course_id,
                    query=req.query,
                    k=req.k,
                    material_version_ids=req.material_version_ids,
                )
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


# ── Dashboard (Professor) ────────────────────────────────────────────────────

def _register_dashboard(app: FastAPI, deps) -> None:
    """Dashboard endpoints using existing M3 tables (material, material_version)."""

    @app.get(R.PROFESSOR_DASHBOARD)
    def professor_dashboard(
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)

                courses = _query_courses(repo, caller.org_id, caller.user_id)
                recent_uploads = _query_recent_uploads(repo, caller.org_id, caller.user_id)
                active_assignments = _query_active_assignments(repo, caller.org_id, caller.user_id)

                return {
                    "courses": courses,
                    "recent_uploads": recent_uploads,
                    "active_assignments": active_assignments,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.PROFESSOR_COURSES)
    def professor_courses(
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)
                return _query_courses(repo, caller.org_id, caller.user_id)
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


def _query_courses(repo, org_id: str, owner: Optional[str] = None) -> list:
    """Courses for this org. When owner is set (a professor), scope to the ones
    they own (course.created_by) — intra-org isolation on top of org RLS."""
    sql = "SELECT course_id, course_name FROM course WHERE org_id = %s"
    params = [org_id]
    if owner is not None:
        sql += " AND created_by = %s"
        params.append(owner)
    sql += " ORDER BY course_name"
    with repo.conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    return [{"course_id": str(r[0]), "course_name": r[1]} for r in rows]


def _assignment_is_practice(cur, assignment_id: str) -> bool:
    """True if the assignment is a practice test. Practice results are shown to
    the professor anonymized (no email, no verbatim transcript) — the same
    privacy stance as the aggregate performance dashboard (issue S-E-2.2).
    Guarded on the assignment_type column so it is safe pre-migration."""
    cur.execute("""SELECT 1 FROM information_schema.columns
                   WHERE table_name='assignment' AND column_name='assignment_type'""")
    if cur.fetchone() is None:
        return False
    cur.execute("SELECT assignment_type FROM assignment WHERE assignment_id = %s::uuid",
                (assignment_id,))
    row = cur.fetchone()
    return bool(row) and (row[0] or "assignment") == "practice"


def _enrolled_sql(course_col: str, has_public_enrollment: bool = True) -> str:
    """SQL predicate (ALWAYS 2 params, both the student's lowercased email) for
    "this student is enrolled in `course_col`". Enrolment is required — a course
    with an empty roster is NOT open to the org, or every student would see every
    course. auth.enrollment (by app_user) is authoritative; public.enrollment is
    the email-keyed mirror the professor UI writes.

    public.enrollment is lazily created and absent on a fresh DB, and referencing
    a missing relation errors at plan time (a runtime guard can't short-circuit
    it). So when `has_public_enrollment` is False we fall back to the authoritative
    auth roster for BOTH param slots — the param count stays 2 so every call site's
    tuple is unchanged."""
    auth_branch = f"""EXISTS (SELECT 1 FROM auth.enrollment ae
                             JOIN auth.app_user au ON au.id = ae.app_user_id
                             WHERE ae.course_id = {course_col}
                               AND lower(au.email) = %s)"""
    if not has_public_enrollment:
        # No public.enrollment table: both email params route to the authoritative
        # auth roster (X OR X == X); nothing references the missing relation.
        return f"({auth_branch}\n                OR {auth_branch})"
    public_branch = f"""EXISTS (SELECT 1 FROM enrollment e
                               WHERE e.course_id = {course_col}
                                 AND lower(e.student_email) = %s)"""
    return f"({public_branch}\n                OR {auth_branch})"


def _has_public_enrollment(cur) -> bool:
    """True when the lazily-created public.enrollment mirror exists."""
    cur.execute("SELECT to_regclass('public.enrollment')")
    return cur.fetchone()[0] is not None


def _query_student_courses(repo, org_id: str, student_email: str) -> list:
    """Courses the student is enrolled in — the same gate as assignment
    visibility, so the course list and the assignments stay consistent."""
    email = (student_email or "").strip().lower()
    with repo.conn.cursor() as cur:
        has_pub = _has_public_enrollment(cur)
        cur.execute(
            f"""SELECT course_id, course_name FROM course c
               WHERE c.org_id = %s AND {_enrolled_sql('c.course_id', has_pub)}
               ORDER BY course_name""",
            (org_id, email, email),
        )
        rows = cur.fetchall()
    return [{"course_id": str(r[0]), "course_name": r[1]} for r in rows]


def _query_recent_uploads(repo, org_id: str, owner: Optional[str] = None) -> list:
    """Last 10 material_versions for this org; scoped to the owner's courses if set."""
    sql = """SELECT mv.material_version_id, mv.file_name, mv.status,
                    mv.created_at, m.display_name, c.course_name
             FROM material_version mv
             JOIN material m ON m.material_id = mv.material_id
             JOIN course c ON c.course_id = mv.course_id
             WHERE mv.org_id = %s"""
    params = [org_id]
    if owner is not None:
        sql += " AND c.created_by = %s"
        params.append(owner)
    sql += " ORDER BY mv.created_at DESC LIMIT 10"
    with repo.conn.cursor() as cur:
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
    return [
        {
            "material_version_id": str(r[0]),
            "file_name": r[1],
            "status": r[2],
            "created_at": r[3].isoformat() if r[3] else None,
            "display_name": r[4],
            "course_name": r[5],
        }
        for r in rows
    ]


def _query_active_assignments(repo, org_id: str, owner: Optional[str] = None) -> list:
    """Active assignments for this org; scoped to the owner's courses if set."""
    import psycopg2
    try:
        sql = """SELECT a.assignment_id, a.title, a.status, a.created_at, c.course_name
                 FROM assignment a
                 JOIN course c ON c.course_id = a.course_id
                 WHERE a.org_id = %s AND a.status = 'active'"""
        params = [org_id]
        if owner is not None:
            sql += " AND c.created_by = %s"
            params.append(owner)
        sql += " ORDER BY a.created_at DESC"
        with repo.conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            rows = cur.fetchall()
        return [
            {
                "assignment_id": str(r[0]),
                "title": r[1],
                "status": r[2],
                "created_at": r[3].isoformat() if r[3] else None,
                "course_name": r[4],
            }
            for r in rows
        ]
    except psycopg2.ProgrammingError:
        # Table may not exist yet in early migrations; degrade gracefully
        logger.warning("assignment table not found for org %s — migration pending", org_id)
        repo.conn.rollback()
        return []


# ── Student Dashboard ──────────────────────────────────────────────────────


def _register_student_dashboard(app: FastAPI, deps) -> None:
    """Student dashboard: all courses + active assignments in the org."""

    @app.get(R.STUDENT_DASHBOARD)
    def student_dashboard(
        x_org_name: str = Header(...),
        x_user_id: str = Header("student"),
        x_role: str = Header("student"),
    ):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)

                # Roster-scoped: only courses the student is enrolled in — never
                # every course in the org. caller.user_id is the student's email,
                # which is what the roster gate matches on.
                courses = _query_student_courses(repo, caller.org_id, caller.user_id)
                assignments = _query_student_assignments(repo, caller.org_id, caller.user_id)

                return {
                    "courses": courses,
                    "assignments": assignments,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.STUDENT_ASSIGNMENTS)
    def student_assignments(
        x_org_name: str = Header(...),
        x_user_id: str = Header("student"),
        x_role: str = Header("student"),
    ):
        """Active assignments available to the student (frontend: listStudentAssignments)."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                return _query_student_assignments(repo, caller.org_id, caller.user_id)
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


def _query_student_assignments(repo, org_id: str, student_email: str = "") -> list:
    """Active assignments for the student's enrolled courses.

    Enrolment is required: an empty roster hides the course rather than opening it
    to the whole org (see _enrolled_sql)."""
    email = (student_email or "").strip().lower()
    with repo.conn.cursor() as cur:
        has_pub = _has_public_enrollment(cur)
        cur.execute("""SELECT 1 FROM information_schema.columns
                       WHERE table_name='assignment' AND column_name='assignment_type'""")
        type_col = "a.assignment_type" if cur.fetchone() is not None else "'assignment'"
        cur.execute(
            f"""SELECT a.assignment_id, a.title, a.status, a.config,
                      a.created_at, c.course_name, a.course_id, {type_col},
                      EXISTS (SELECT 1 FROM exam_session es
                              WHERE es.assignment_id = a.assignment_id
                                AND es.student_id = %s AND es.status = 'completed') AS completed,
                      (SELECT g.status FROM grade g
                        WHERE g.assignment_id = a.assignment_id AND g.student_id = %s
                        ORDER BY g.updated_at DESC LIMIT 1) AS grade_status
               FROM assignment a
               JOIN course c ON c.course_id = a.course_id
               WHERE a.org_id = %s AND a.status = 'active'
                 AND {_enrolled_sql('a.course_id', has_pub)}
               ORDER BY a.created_at DESC""",
            (email, email, org_id, email, email),
        )
        rows = cur.fetchall()
    return [
        {
            "id": str(r[0]),
            "title": r[1],
            "status": r[2],
            "config": r[3] if isinstance(r[3], dict) else {},
            "created_at": r[4].isoformat() if r[4] else None,
            "course_name": r[5],
            "course_id": str(r[6]),
            "assignment_type": r[7] or "assignment",
            "questions_count": (r[3] or {}).get("max_questions") if isinstance(r[3], dict) else None,
            "completed": bool(r[8]),
            # None until a professor grades it, then 'pending' -> 'released'. The
            # score itself stays behind Results; the card only shows which state.
            "grade_status": r[9],
        }
        for r in rows
    ]


def _extract_feedback(raw) -> str:
    """Pull a feedback string out of the stored evaluation LLM output (JSONB)."""
    if isinstance(raw, dict):
        return raw.get("feedback") or raw.get("explanation") or ""
    return ""


def _threshold_rationale(score: float, bucket: str, comp: dict, feedback: str) -> str:
    """Explain, in plain language, why an answer landed in its EDS band.

    Combines the model's qualitative feedback with the quantitative EDS drivers
    (authenticity gate, concept coverage, causal-link coverage) so a professor
    can see *why* a score sits at a given threshold, not just the number.
    """
    pct = round(score * 100)
    if not comp:
        lead = (feedback or "").strip()
        return (f"{lead} " if lead else "") + f"Scored {pct}/100 ({bucket} band)."

    r = comp.get("r_gate")
    node = comp.get("node_score")
    edge = comp.get("edge_score")
    nodes_n = len(comp.get("nodes_detected") or [])
    edges_n = len(comp.get("edges_demonstrated") or [])

    parts = []
    if r is not None:
        if r >= 0.75:
            auth = "authentic reasoning"
        elif r >= 0.4:
            auth = "partly recited"
        else:
            auth = "mostly keyword recitation"
        parts.append(f"authenticity gate R={round(r, 2)} ({auth})")
    if node is not None:
        parts.append(f"concept coverage {round(node * 100)}% ({nodes_n} nodes)")
    if edge is not None:
        parts.append(f"causal-link coverage {round(edge * 100)}% ({edges_n} links)")

    detail = "; ".join(parts)
    lead = (feedback or "").strip()
    tail = f"Scored {pct}/100 → {bucket} band" + (f" because {detail}." if detail else ".")
    return (f"{lead} " if lead else "") + tail


def _query_course_performance(repo, org_id: str, course_id: str) -> dict:
    """Anonymized class performance on this course's PRACTICE tests.

    Aggregates the per-answer EDS components across every completed practice
    session into class-level figures — no per-student rows ever leave here:
      - aspects: Recall (Concepts / node), Application (Causal Links / edge),
        In-depth Understanding (Novel Insight / gen), plus an Authenticity signal
        (r_gate). For each: the % of students at/above a mastery bar, and the
        class average.
      - topics: for each concept examined, the % of students who demonstrated it.
    """
    # Pretty topic names: map stored concept id/label -> the graph's label.
    graph = _query_graph_version(repo, org_id, course_id)
    label_of: dict = {}
    for c in (graph.get("concepts") or []):
        lbl = c.get("label")
        if lbl:
            if c.get("id"):
                label_of[str(c["id"])] = lbl
            label_of[str(lbl)] = lbl

    with repo.conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM information_schema.columns
                       WHERE table_name='assignment' AND column_name='assignment_type'""")
        type_clause = "AND a.assignment_type = 'practice'" if cur.fetchone() is not None else ""
        cur.execute(
            f"""SELECT es.student_id, q.concept_ids, e.eds_components
                FROM exam_session es
                JOIN assignment a ON a.assignment_id = es.assignment_id
                JOIN session_turn st ON st.session_id = es.session_id
                JOIN evaluation e ON e.turn_id = st.turn_id
                JOIN question q ON q.question_id = st.question_id
                WHERE a.course_id = %s::uuid AND es.status = 'completed' {type_clause}""",
            (course_id,),
        )
        rows = cur.fetchall()

    # Deterministic aggregation lives in the web-free performance module (tested).
    return aggregate_performance(rows, label_of, bar=0.5)


def _withhold_unreleased(results: dict) -> dict:
    """Strip every score from a student's results until the professor releases them.

    The auto EDS is an internal draft, not a mark: on a graded item the student
    sees their own answers but no number until grade.status = 'released'. Practice
    tests are exempt — they are never professor-graded, so EDS is all they have."""
    return {
        **results,
        "grade_released": False,
        "score": None,
        "components": None,
        "feedback": "Your professor hasn't released your grade yet.",
        "question_results": [
            {k: v for k, v in q.items() if k not in ("score", "components", "feedback")}
            for q in results.get("question_results", [])
        ],
    }


def _query_exam_results(repo, assignment_id: str, student_id: str,
                        for_student: bool = False) -> dict:
    """Assemble the caller's exam results from their most-recent session.

    `for_student` gates unreleased scores (see _withhold_unreleased); professors
    reviewing the same session always see the draft EDS."""
    with repo.conn.cursor() as cur:
        # Pick the session that represents the result, not merely the newest one:
        # a graded session wins, then any completed session, then most recent. This
        # keeps Results consistent with the "Graded" chip even if a stray later
        # session exists (e.g. an abandoned retake), which otherwise showed no
        # grade and no transcript.
        cur.execute(
            """SELECT es.session_id, es.status, es.completed_at
               FROM exam_session es
               LEFT JOIN grade g ON g.session_id = es.session_id
               WHERE es.assignment_id = %s::uuid AND es.student_id = %s
               ORDER BY (g.grade_id IS NOT NULL) DESC,
                        (es.status = 'completed') DESC,
                        es.completed_at DESC NULLS LAST,
                        es.started_at DESC
               LIMIT 1""",
            (assignment_id, student_id),
        )
        srow = cur.fetchone()
    if not srow:
        return {"assignment_id": assignment_id, "session_id": None, "status": "not_started",
                "score": 0, "total_questions": 0, "questions_answered": 0,
                "feedback": "No exam session found for this assignment.",
                "question_results": [], "completed_at": None}
    session_id = str(srow[0])
    with repo.conn.cursor() as cur:
        cur.execute(
            """SELECT st.question_id, q.text, st.student_answer,
                      COALESCE(e.eds_score, 0), e.raw_llm_output, e.eds_components
               FROM session_turn st
               JOIN question q ON q.question_id = st.question_id
               LEFT JOIN evaluation e ON e.turn_id = st.turn_id
               WHERE st.session_id = %s::uuid ORDER BY st.turn_index""",
            (session_id,),
        )
        rows = cur.fetchall()
    q_results, answered, score_sum = [], 0, 0.0
    # Carry the same EDS component breakdown the in-exam gauge shows, so Results
    # speaks one vocabulary: per-question components + an averaged aggregate.
    comp_keys = ("node_score", "edge_score", "r_gate", "gen_score")
    comp_sums = {k: 0.0 for k in comp_keys}
    comp_n = 0
    for r in rows:
        if r[2]:
            answered += 1
        eds = float(r[3] or 0)
        score_sum += eds
        comp = r[5] if isinstance(r[5], dict) else None
        if comp:
            comp_n += 1
            for k in comp_keys:
                comp_sums[k] += float(comp.get(k) or 0)
        q_results.append({"question_id": str(r[0]), "question_text": r[1],
                          "answer": r[2] or "", "score": round(eds * 100),
                          "feedback": _extract_feedback(r[4]), "components": comp})
    total = len(rows)
    overall = round(score_sum / total * 100) if total else 0
    components = {k: comp_sums[k] / comp_n for k in comp_keys} if comp_n else None
    feedback = f"Answered {answered} of {total} questions · Epistemic Depth Score {overall}/100."

    # A released grade is authoritative: show the professor's final score and
    # overall comment instead of the raw auto EDS.
    with repo.conn.cursor() as cur:
        cur.execute(
            "SELECT final_score, component_scores, status FROM grade WHERE session_id = %s::uuid",
            (session_id,),
        )
        grow = cur.fetchone()
        is_practice = _assignment_is_practice(cur, assignment_id)
    released = bool(grow) and grow[2] == "released"
    if released:
        overall = round(float(grow[0]) * 100)
        comp = grow[1] if isinstance(grow[1], dict) else _json.loads(grow[1] or "{}")
        comment = comp.get("overall_comment")
        if comment:
            feedback = comment

    out = {
        "session_id": session_id, "assignment_id": assignment_id, "status": srow[1],
        "score": overall, "total_questions": total, "questions_answered": answered,
        "feedback": feedback,
        "components": components,
        "question_results": q_results,
        "completed_at": srow[2].isoformat() if srow[2] else None,
        "grade_released": released or is_practice,
    }
    if for_student and not is_practice and not released:
        return _withhold_unreleased(out)
    return out


# ── M4 Graph ─────────────────────────────────────────────────────────────────

class GraphRebuildRequest(BaseModel):
    """POST body for graph rebuild."""
    domain: str = Field(..., min_length=1, max_length=200)
    rebuild: bool = False


class CuratedConcept(BaseModel):
    """One entry in a curated concept set."""
    id: Optional[str] = None
    label: str = Field(..., min_length=1, max_length=200)


class GraphConceptsRequest(BaseModel):
    """PUT body: the professor's curated concept set for a course graph."""
    concepts: List[CuratedConcept] = Field(default_factory=list)


def _register_graph(app: FastAPI, deps) -> None:
    """Graph endpoints — lightweight queries against graph_version + chunks."""

    @app.get(R.GRAPH_GET)
    def get_graph(
        course_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)

                graph_info = _query_graph_version(repo, caller.org_id, course_id)
                return graph_info
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.GRAPH_DOCUMENTS)
    def list_graph_documents(
        course_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Documents in this course that have a per-document concept graph."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.document_concept')")
                    if cur.fetchone()[0] is None:
                        return {"documents": []}
                    # Exclude the syllabus: its per-document concepts are kept in the
                    # table but never surfaced as a document graph (materials only).
                    excluded = syllabus_version_ids(cur, caller.org_id, course_id)
                    cur.execute(
                        """SELECT mv.material_version_id, mv.file_name, count(*)
                           FROM document_concept dc
                           JOIN material_version mv ON mv.material_version_id = dc.material_version_id
                           WHERE dc.course_id = %s::uuid AND dc.org_id = %s::uuid
                             AND dc.material_version_id <> ALL(%s::uuid[])
                           GROUP BY mv.material_version_id, mv.file_name
                           ORDER BY mv.file_name""",
                        (course_id, caller.org_id, excluded))
                    docs = [{"material_version_id": str(r[0]), "file_name": r[1], "concept_count": r[2]}
                            for r in cur.fetchall()]
                return {"documents": docs}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.MATERIAL_GRAPH_BUILD)
    def build_material_graph(
        course_id: str,
        material_version_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """(Re)build one document's concept graph on demand — for a material that
        finished ingest but has no graph (the inline build produced nothing).
        Runs in the background; poll GRAPH_DOCUMENTS for the updated count."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)
                # Don't rebuild the syllabus (excluded from the graph by design).
                with repo.conn.cursor() as cur:
                    if str(material_version_id) in syllabus_version_ids(cur, caller.org_id, course_id):
                        return {"status": "skipped", "message": "The syllabus is excluded from the concept graph."}
                    cur.execute("SELECT count(*) FROM chunk WHERE material_version_id = %s::uuid",
                                (material_version_id,))
                    if cur.fetchone()[0] == 0:
                        return {"status": "error", "message": "This document isn't ingested yet — wait for it to finish, then try again."}
                threading.Thread(
                    target=_build_one_document_graph_bg,
                    args=(d["settings"], caller.org_id, course_id, material_version_id),
                    daemon=True).start()
                return {"status": "building"}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.MATERIAL_GRAPH)
    def material_graph(
        material_version_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """One document's concept graph (its own concepts + edges), same shape as
        the course graph so the UI renders it identically."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    g = document_graph(cur, caller.org_id, material_version_id)
                    cur.execute("SELECT file_name FROM material_version WHERE material_version_id = %s::uuid",
                                (material_version_id,))
                    row = cur.fetchone()
                layout = compute_layout(g["concepts"], g["relations"])
                return {
                    "status": "ready" if g["concepts"] else "empty",
                    "source": row[0] if row else None,
                    "node_count": len(g["concepts"]),
                    "edge_count": len(g["relations"]),
                    "concepts": g["concepts"],
                    "edges": g["relations"],
                    "relations": g["relations"],
                    "nodes": layout["nodes"],
                    "graph_edges": layout["edges"],
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.put(R.GRAPH_CONCEPTS)
    def save_graph_concepts(
        course_id: str,
        req: GraphConceptsRequest,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Persist the professor's curated concept set onto the active graph.

        Rewrites the stored graph JSON so downstream question generation (which
        reads the graph) honors it: kept concepts retain their full data, added
        ones become stubs, and relations touching a removed concept are pruned.
        """
        import json as _json

        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)

                graph = _query_graph_version(repo, caller.org_id, course_id)
                if graph.get("status") != "ready":
                    return {"status": "error",
                            "message": "No concept graph to curate. Build the graph first."}

                kept = [{"id": c.id, "label": c.label} for c in req.concepts]
                if not kept:
                    return {"status": "error", "message": "Keep at least one concept."}

                new_concepts, new_relations = apply_curation(
                    graph.get("concepts", []), graph.get("relations", []), kept)
                graph_json = _json.dumps({"concepts": new_concepts, "relations": new_relations})
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """UPDATE graph_version
                           SET s3_key = %s, node_count = %s, edge_count = %s
                           WHERE org_id = %s::uuid AND course_id = %s::uuid AND is_active = true""",
                        (graph_json, len(new_concepts), len(new_relations),
                         caller.org_id, course_id),
                    )
                repo.conn.commit()
                return {"status": "saved",
                        "node_count": len(new_concepts), "edge_count": len(new_relations)}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.GRAPH_REBUILD)
    def rebuild_graph(
        course_id: str,
        req: GraphRebuildRequest,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Kick off an async graph (re)build with a freshly authored question bank.

        Returns immediately: the extraction LLM call is large and slow, so it runs
        in a background thread (see _build_graph_async) — this endpoint can never
        hit the gateway timeout. The current graph is marked stale so the UI keeps
        polling until the new version (with conceptual + case-based questions)
        becomes active.
        """
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)
                _require_syllabus(repo, course_id)

                with repo.conn.cursor() as cur:
                    cur.execute("SELECT count(*) FROM chunk WHERE course_id = %s", (course_id,))
                    if not cur.fetchone()[0]:
                        return {"status": "error",
                                "message": "No material found. Upload materials first."}
                    # Mark the current graph stale so the frontend keeps polling
                    # until the freshly-built version replaces it.
                    cur.execute(
                        "UPDATE graph_version SET is_stale = true "
                        "WHERE org_id = %s AND course_id = %s AND is_active = true",
                        (caller.org_id, course_id),
                    )
                repo.conn.commit()

                _build_graph_async(d["settings"], caller.org_id, course_id, req.domain)
                return {"status": "building",
                        "message": "Rebuilding the concept graph and question bank — this takes a moment."}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.GRAPH_NEIGHBORS)
    def get_neighbors(
        concept_id: str,
        course_id: Optional[str] = None,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Direct neighbors of a concept within a course's active graph."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                if not course_id:
                    return {"concept_id": concept_id, "neighbors": []}
                graph = _query_graph_version(repo, caller.org_id, course_id)
                nbrs = graph_neighbors(
                    graph.get("relations", []), concept_id, graph.get("concepts", []),
                )
                return {"concept_id": concept_id, "neighbors": nbrs}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


def _query_graph_version(repo, org_id: str, course_id: str) -> dict:
    """Query graph_version table for the active graph; fallback if table doesn't exist."""
    import json as _json
    import psycopg2
    try:
        with repo.conn.cursor() as cur:
            cur.execute(
                """SELECT version_id, graph_version, node_count, edge_count,
                          validation_score, s3_key, created_at,
                          COALESCE(is_stale, false)
                   FROM graph_version
                   WHERE org_id = %s AND course_id = %s AND is_active = true
                   LIMIT 1""",
                (org_id, course_id),
            )
            row = cur.fetchone()
        if row:
            # s3_key column holds inline JSON (not a real S3 path) for MVP
            graph_data = {}
            try:
                graph_data = _json.loads(row[5]) if row[5] and row[5].startswith("{") else {}
            except (ValueError, TypeError):
                pass
            concepts = graph_data.get("concepts", [])
            relations = graph_data.get("relations", [])
            layout = compute_layout(concepts, relations)  # nodes {id,label,x,y} + [from,to] edges
            return {
                "status": "ready",
                "version_id": str(row[0]),
                "graph_version": row[1],
                "node_count": row[2] or 0,
                "edge_count": row[3] or 0,
                "validation_score": float(row[4]) if row[4] else 0.0,
                "created_at": row[6].isoformat() if row[6] else None,
                "is_stale": bool(row[7]),
                "concepts": concepts,
                # `edges` stays the relation objects the UI renders ({src,dst,edge_type,confidence}).
                # Layout for node/edge rendering is exposed separately so it doesn't clobber them.
                "edges": relations,
                "relations": relations,            # same list; consumed by the neighbors endpoint
                "nodes": layout["nodes"],          # {id, label, x, y}
                "graph_edges": layout["edges"],    # [from_id, to_id] pairs
            }
    except psycopg2.ProgrammingError:
        # Table may not exist yet in early migrations
        logger.warning("graph_version table not found — migration pending")
        repo.conn.rollback()

    # No graph built yet — return empty state
    return {
        "status": "empty",
        "node_count": 0,
        "edge_count": 0,
        "concepts": [],
        "edges": [],
    }


# Wall-clock cap for the synchronous Regenerate LLM call. Kept well under the
# 60s CloudFront origin timeout so a slow model degrades to the stored bank
# (returning questions) instead of the connection dropping ("Load failed").
REGEN_LLM_TIMEOUT_S = 45


def _generate_concept_banks(settings, concepts: list, relations: list, difficulty: str) -> dict:
    """Generate a FRESH per-concept oral-exam question bank with the LLM at
    assignment-creation time, grounded in the course's concept graph.

    Returns the same {concept_id/label: [questions]} shape as `_concept_banks`,
    so `build_variants`/`assemble_questions` are unchanged. Temperature is non-zero
    so each assignment gets different questions. Falls back to each concept's
    stored/extracted bank (and ultimately the generic templates) only when
    generation fails or omits a concept — never silently returns nothing.
    """
    labels = [c.get("label", "") for c in concepts if c.get("label")]
    if not labels:
        return _concept_banks(concepts, difficulty)

    focus = DIFFICULTY_FOCUS.get(difficulty, DIFFICULTY_FOCUS["balanced"])
    concept_lines = "\n".join(
        f"- {c.get('label')}: {c.get('definition', '')}"
        for c in concepts if c.get("label"))
    rel_lines = "\n".join(
        f"- {r.get('src')} {r.get('edge_type') or r.get('link_type') or 'RELATED_TO'} {r.get('dst')}"
        for r in (relations or []) if r.get("src") and r.get("dst"))
    system_prompt = (
        "You are writing questions for a university ORAL exam, grounded ONLY in the "
        f"provided concept graph. For EACH concept listed, write exactly 2 questions emphasising {focus}: "
        "the FIRST a single short sentence (max ~20 words) that probes understanding, with no "
        "preamble or restating the concept name; the SECOND a CASE-BASED question that opens "
        "with a brief one-sentence mini-case (a realistic scenario) and then asks the student "
        "to apply the concept to it. "
        "Both are open-ended (never yes/no), specific to the named concept, answerable from the "
        "course concepts and their relationships, and phrased the way an examiner would speak "
        "them aloud. Do NOT invent facts beyond the graph, and do NOT use a generic template. "
        "Return ONLY valid JSON, no prose, no markdown fences: "
        '{"banks": [{"label": "<exact concept label>", '
        '"questions": ["<short probe>", "Mini-case: <one-sentence scenario>. <question applying the concept>"]}]}'
    )
    user = (f"Difficulty focus: {focus}\n\n"
            f"Concepts:\n{concept_lines}\n\n"
            f"Relationships:\n{rel_lines or '(none provided)'}")
    try:
        # This runs synchronously while the professor waits, behind a 60s CloudFront
        # origin timeout. Bound it hard: ONE attempt (no big JSON-reparse retries)
        # and a wall-clock timeout with margin under 60s. On any failure/timeout we
        # degrade to the concepts' stored bank (authored async at graph-build time),
        # so Regenerate always returns questions instead of "Load failed".
        data = call_bedrock(settings, system_prompt, user,
                            max_tokens=8000, temperature=0.6,
                            retries=1, timeout=REGEN_LLM_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - degrade to stored bank, assignment still works
        logger.warning("per-assignment question generation failed (%s) — using stored bank", exc)
        return _concept_banks(concepts, difficulty)

    # Deterministic parse/merge lives in exam_questions (web-free + unit-tested).
    return merge_generated_banks(concepts, data, difficulty)


# ── M5 Questions ─────────────────────────────────────────────────────────────

class UpdateQuestionRequest(BaseModel):
    """PUT body for updating a question's text and/or points."""
    text: Optional[str] = Field(default=None, min_length=1, max_length=5000)
    points: Optional[int] = Field(default=None, ge=1, le=10)


class GenerateQuestionsRequest(BaseModel):
    """POST body for question generation."""
    concept_ids: Optional[List[str]] = None
    material_version_ids: Optional[List[str]] = None
    count: int = Field(default=5, ge=1, le=MAX_QUESTION_COUNT)
    difficulty: str = Field(default="balanced", pattern=r"^(recall|balanced|deep)$")
    domain: str = Field(default="general", min_length=1, max_length=200)


class BuildExamRequest(BaseModel):
    """POST body for deterministic exam assembly (3 variants, no LLM)."""
    concept_ids: Optional[List[str]] = None
    q_count: int = Field(default=12, ge=1, le=MAX_QUESTION_COUNT)
    exam_len: int = Field(default=30, ge=5, le=180)
    difficulty: str = Field(default="balanced", pattern=r"^(recall|balanced|deep)$")


class ExamAssignQuestion(BaseModel):
    """One question from a built exam variant."""
    concept_id: str = ""
    topic: str = ""
    q: str = Field(min_length=1, max_length=5000)


class AssignExamRequest(BaseModel):
    """Persist a built exam variant's questions and create an assignment from them."""
    title: str = Field(min_length=1, max_length=300)
    questions: List[ExamAssignQuestion]
    difficulty: str = Field(default="balanced", pattern=r"^(recall|balanced|deep)$")
    duration_minutes: Optional[int] = None
    assignment_type: str = Field(default="assignment", pattern=r"^(practice|assignment|exam)$")
    # Case-based assessment: when true, students can open the course reference
    # materials ("View Case") during the exam. Default off (issue S-E-2.1#2).
    include_case: bool = False
    # Week scoping: the class session this exam is generated for, and a snapshot
    # of the concept ids that were in scope at publish time. The snapshot means
    # later edits to the session's scope don't recategorize this exam (P-S-2.3).
    session_id: Optional[str] = None
    scope_concepts: Optional[List[str]] = None
    # When true, create the assignment as a draft (student-invisible sandbox)
    # so a professor can dry-run it before publishing. Default: publish live.
    draft: bool = False


def _build_question_dicts(settings, *, concepts, chunks, difficulty, domain, count):
    """Author `count` oral-exam questions for the given concepts + chunks via
    Bedrock and parse them into question dicts — NO DB writes.

    This is the single source of truth for the generation prompt and parse. Both
    the professor `generate_questions` endpoint and the admin QG test bench call
    it, so the bench grades the EXACT questions students would get, not a copy.

    Returns a list of dicts:
      {question_id, topic, question, difficulty, concept_ids, expected_path}
    """
    import json as _json, uuid as _uuid  # noqa: F401  (uuid used for ids)

    concept_descriptions = ""
    if concepts:
        concept_descriptions = "\n".join(
            f"- {c.get('label', 'unknown')}"
            + (f" ({c.get('definition', '')})" if c.get('definition') else "")
            for c in concepts
        )

    combined_chunks = "\n\n---\n\n".join(chunks[:MAX_CHUNKS_FOR_GENERATION])

    difficulty_guidance = {
        "recall": (
            "Generate questions focused on definitional accuracy and formula recall. "
            "Questions should verify the student can correctly state key definitions, "
            "identify components, and reproduce fundamental relationships."
        ),
        "balanced": (
            "Generate questions that mix recall with causal reasoning. "
            "Some questions should verify definitions, while others should require "
            "the student to explain WHY something works, trace mechanisms, or "
            "connect prerequisite concepts to their consequences."
        ),
        "deep": (
            "Generate questions that probe deep causal understanding and high-hop "
            "prerequisite chains. Questions should require the student to trace "
            "multi-step causal mechanisms, synthesize across concepts, explain "
            "trade-offs, and articulate why specific assumptions break down. "
            "Never ask for simple definitions."
        ),
    }.get(difficulty, "Generate questions that mix recall with causal reasoning.")

    system_prompt = (
        "You are an expert Socratic oral examiner designing assessment questions "
        "for university-level courses. Your questions must probe EPISTEMIC DEPTH — "
        "they test whether a student truly understands causal mechanisms, not just "
        "whether they can parrot definitions.\n\n"
        "Design principles:\n"
        "- Prefer 'explain why' and 'trace how' over 'define' or 'list'\n"
        "- Questions should require articulating causal chains and mechanisms\n"
        "- Each question should be standalone and clearly worded\n"
        "- Questions should be answerable from the provided source material\n"
        "- Frame questions as an oral examiner would ask them — direct, probing, "
        "concise (1-2 sentences)\n"
        "- Never ask trivial yes/no questions\n"
        "- Target specific concept clusters from the knowledge graph\n\n"
        f"Difficulty focus: {difficulty_guidance}\n\n"
        "Return ONLY valid JSON. No markdown fences, no prose outside the JSON."
    )

    user_prompt = (
        f"Domain: {domain}\n"
        f"Difficulty: {difficulty}\n"
        f"Number of questions to generate: {count}\n\n"
    )

    if concept_descriptions:
        user_prompt += f"Concept graph (topics to examine):\n{concept_descriptions}\n\n"

    if combined_chunks:
        user_prompt += f"Source material:\n{combined_chunks}\n\n"

    user_prompt += (
        f"Generate exactly {count} oral exam questions. "
        "For each question, return a JSON object with:\n"
        '- "topic": the concept/topic this question targets (short label, 2-5 words)\n'
        '- "question": the actual question text (1-2 sentences, Socratic style)\n'
        '- "difficulty": one of "recall", "balanced", or "deep"\n'
        '- "concept_ids": list of concept labels this question covers\n'
        '- "expected_path": the expected reasoning path a strong student should demonstrate:\n'
        '  {"nodes": [{"label": "concept name", "definition": "1-sentence definition"}] '
        "-- the key concepts that must be DEMONSTRATED with understanding (not just named),\n"
        '  "edges": [{"src": "concept_A", "dst": "concept_B", '
        '"link_type": "CAUSES|ENABLES|PREVENTS|INCREASES|DECREASES", '
        '"explanation": "1 sentence explaining the causal mechanism"}] '
        "-- the causal links between concepts that must be ARTICULATED,\n"
        '  "extensions": [{"label": "concept", "connection": "how this extends beyond the base expected path"}] '
        "-- 1-3 bonus concepts for students who go deeper}\n\n"
        'Return format: {"questions": [...]}'
    )

    data = call_bedrock(
        settings, system_prompt, user_prompt,
        max_tokens=LLM_MAX_TOKENS_GENERATION, temperature=0.3,
    )
    questions_raw = data.get("questions", data if isinstance(data, list) else [])

    out = []
    for q in questions_raw:
        if not isinstance(q, dict) or not q.get("question"):
            continue
        topic = q.get("topic", "general")
        out.append({
            "question_id": str(_uuid.uuid4()),
            "topic": topic,
            "question": q["question"],
            "difficulty": q.get("difficulty", difficulty),
            "concept_ids": q.get("concept_ids", [topic]),
            "expected_path": q.get("expected_path", {}),
        })
    return out


def _register_questions(app: FastAPI, deps) -> None:
    """Question generation (direct Bedrock Converse) and review endpoints."""

    @app.post(R.QUESTIONS_GENERATE)
    def generate_questions(
        course_id: str,
        req: GenerateQuestionsRequest,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Synchronous question generation via Qwen3 32B on Bedrock Converse."""
        import json as _json, uuid as _uuid

        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")

                repo.set_tenant(caller.org_id)
                _require_syllabus(repo, course_id)
                settings = d["settings"]

                graph_data = _query_graph_version(repo, caller.org_id, course_id)
                concepts = graph_data.get("concepts", [])

                if req.concept_ids and concepts:
                    concept_set = set(req.concept_ids)
                    concepts = [c for c in concepts if c.get("label") in concept_set
                                or c.get("id") in concept_set or c.get("node_id") in concept_set]

                with repo.conn.cursor() as cur:
                    if req.material_version_ids:
                        # Cast the array: the ids arrive as strings and the column is uuid.
                        cur.execute(
                            "SELECT text FROM chunk WHERE course_id = %s "
                            "AND material_version_id = ANY(%s::uuid[]) ORDER BY chunk_index",
                            (course_id, [str(v) for v in req.material_version_ids]),
                        )
                    else:
                        cur.execute(
                            "SELECT text FROM chunk WHERE course_id = %s ORDER BY chunk_index",
                            (course_id,),
                        )
                    chunks = [row[0] for row in cur.fetchall()]

                if not chunks and not concepts:
                    return {"status": "error",
                            "message": "No course material or concepts found. Upload material and build the graph first."}

                # Author the questions via the shared generation core (same logic
                # the admin QG test bench exercises), then persist them as drafts.
                stored_questions = _build_question_dicts(
                    settings, concepts=concepts, chunks=chunks,
                    difficulty=req.difficulty, domain=req.domain, count=req.count,
                )

                insert_params = []
                for sq in stored_questions:
                    sq["status"] = "draft"
                    difficulty_json = _json.dumps({
                        "level": sq["difficulty"],
                        "eds_score": {"recall": 0.3, "balanced": 0.55, "deep": 0.8}.get(sq["difficulty"], 0.55),
                    })
                    insert_params.append((
                        sq["question_id"], course_id, caller.org_id,
                        _json.dumps(sq["concept_ids"]), sq["question"],
                        "oral", difficulty_json, caller.user_id,
                        _json.dumps(sq["expected_path"]),
                    ))

                if insert_params:
                    with repo.conn.cursor() as cur:
                        # Try inserting with expected_path column first
                        try:
                            cur.executemany(
                                """INSERT INTO question
                                   (question_id, course_id, org_id, concept_ids, text,
                                    question_type, difficulty, status, created_by, source_chunks,
                                    expected_path)
                                   VALUES (%s::uuid, %s::uuid, %s::uuid, %s::jsonb, %s,
                                           %s, %s::jsonb, 'draft', %s, '[]'::jsonb,
                                           %s::jsonb)""",
                                insert_params,
                            )
                        except Exception:
                            # Column may not exist yet — fall back to INSERT without expected_path
                            repo.conn.rollback()
                            fallback_params = [p[:-1] for p in insert_params]
                            with repo.conn.cursor() as cur2:
                                cur2.executemany(
                                    """INSERT INTO question
                                       (question_id, course_id, org_id, concept_ids, text,
                                        question_type, difficulty, status, created_by, source_chunks)
                                       VALUES (%s::uuid, %s::uuid, %s::uuid, %s::jsonb, %s,
                                               %s, %s::jsonb, 'draft', %s, '[]'::jsonb)""",
                                    fallback_params,
                                )

                repo.conn.commit()

                return {
                    "status": "completed",
                    "generated_count": len(stored_questions),
                    "questions": stored_questions,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.EXAM_BUILD)
    def build_exam(
        course_id: str,
        req: BuildExamRequest,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Assemble 3 exam variants from the graph's per-concept banks — no LLM."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)
                _require_syllabus(repo, course_id)

                graph = _query_graph_version(repo, caller.org_id, course_id)
                concepts = graph.get("concepts", [])
                if not concepts:
                    return {"status": "error",
                            "message": "No concept graph found. Build the graph first."}

                if req.concept_ids:
                    sel = set(req.concept_ids)
                    concepts = [c for c in concepts
                                if c.get("id") in sel or c.get("label") in sel]

                simple = [{"id": c.get("id") or c.get("label", ""), "label": c.get("label", "")}
                          for c in concepts]
                # Assemble from the default question bank authored at graph-build
                # time. NO LLM call in the request path — instant, so exam creation
                # can never hit a gateway timeout ("Load failed"). If a concept's
                # stored bank is empty (an older graph, or a curated stub),
                # assemble_questions falls back to a generic template for it; the
                # `needs_rebuild` flag tells the professor to rebuild the concept
                # graph, which authors real, case-based questions asynchronously.
                banks = _concept_banks(concepts, req.difficulty)
                populated = sum(1 for c in concepts
                                if (banks.get(c.get("id")) or banks.get(c.get("label"))))
                needs_rebuild = populated < max(1, (len(concepts) + 1) // 2)
                # One streamlined variant (even coverage) — no competing angles to pick between.
                variant = build_variants(simple, req.q_count, req.difficulty, req.exam_len)[0]
                variant["title"] = variant["title"].split(" · ")[0]
                variant["angle_label"] = None
                variant["questions"] = assemble_questions(variant["distribution"], banks)
                return {"status": "completed", "concept_count": len(simple),
                        "variants": [variant], "needs_rebuild": needs_rebuild}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.EXAM_REGENERATE)
    def regenerate_exam(
        course_id: str,
        req: BuildExamRequest,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Like build_exam, but authors FRESH questions with the LLM at the chosen
        difficulty (synchronous — the professor pressed Regenerate). Bounded to a
        cap of concepts so one LLM call stays under the gateway timeout; falls back
        to the stored bank per concept the generator skips."""
        # Fewer concepts → smaller JSON that fits in one non-truncated call and
        # returns well inside the 45s LLM cap (see REGEN_LLM_TIMEOUT_S). Questions
        # are still assembled from the whole graph's stored bank when the live
        # authoring covers fewer than the assignment needs.
        MAX_REGEN_CONCEPTS = 12

        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)
                _require_syllabus(repo, course_id)

                graph = _query_graph_version(repo, caller.org_id, course_id)
                concepts = graph.get("concepts", [])
                relations = graph.get("relations", [])
                if not concepts:
                    return {"status": "error",
                            "message": "No concept graph found. Build the graph first."}

                if req.concept_ids:
                    sel = set(req.concept_ids)
                    concepts = [c for c in concepts
                                if c.get("id") in sel or c.get("label") in sel]
                # Keep the LLM call bounded so it returns within the gateway window.
                capped = concepts[:MAX_REGEN_CONCEPTS]

                simple = [{"id": c.get("id") or c.get("label", ""), "label": c.get("label", "")}
                          for c in capped]
                banks = _generate_concept_banks(d["settings"], capped, relations, req.difficulty)
                variant = build_variants(simple, req.q_count, req.difficulty, req.exam_len)[0]
                variant["title"] = variant["title"].split(" · ")[0]
                variant["angle_label"] = None
                variant["questions"] = assemble_questions(variant["distribution"], banks)
                return {"status": "completed", "concept_count": len(simple),
                        "variants": [variant], "regenerated": True}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.EXAM_ASSIGN)
    def assign_exam(
        course_id: str,
        req: AssignExamRequest,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Persist a built exam variant's questions and create an active assignment."""
        import json as _json, uuid as _uuid

        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)
                _require_syllabus(repo, course_id)
                if not req.questions:
                    return {"status": "error", "message": "no questions to assign"}

                diff_json = _json.dumps({
                    "level": req.difficulty,
                    "eds_score": {"recall": 0.3, "balanced": 0.55, "deep": 0.8}.get(req.difficulty, 0.55),
                })
                # Questions are inserted with an empty expected_path and filled by a
                # background thread queued after the commit below. Generating them
                # synchronously here — one large Claude call for every question —
                # blew past CloudFront's 60s origin read timeout and 504'd the
                # assign request.
                paths: dict = {}
                question_ids = []
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'question' AND column_name = 'expected_path'""")
                    has_ep = cur.fetchone() is not None
                    for i, q in enumerate(req.questions):
                        qid = str(_uuid.uuid4())
                        question_ids.append(qid)
                        concept_ids = _json.dumps([q.concept_id or q.topic or "general"])
                        if has_ep:
                            cur.execute(
                                """INSERT INTO question
                                   (question_id, course_id, org_id, concept_ids, text,
                                    question_type, difficulty, status, created_by, source_chunks,
                                    expected_path)
                                   VALUES (%s::uuid, %s::uuid, %s::uuid, %s::jsonb, %s,
                                           'oral', %s::jsonb, 'approved', %s, '[]'::jsonb, %s::jsonb)""",
                                (qid, course_id, caller.org_id, concept_ids, q.q, diff_json,
                                 caller.user_id, _json.dumps(paths.get(i, {}))),
                            )
                        else:
                            cur.execute(
                                """INSERT INTO question
                                   (question_id, course_id, org_id, concept_ids, text,
                                    question_type, difficulty, status, created_by, source_chunks)
                                   VALUES (%s::uuid, %s::uuid, %s::uuid, %s::jsonb, %s,
                                           'oral', %s::jsonb, 'approved', %s, '[]'::jsonb)""",
                                (qid, course_id, caller.org_id, concept_ids, q.q, diff_json,
                                 caller.user_id),
                            )
                    qs_id = str(_uuid.uuid4())
                    cur.execute(
                        """INSERT INTO question_set (question_set_id, course_id, org_id, title, created_by)
                           VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s)""",
                        (qs_id, course_id, caller.org_id, req.title, caller.user_id),
                    )
                    for idx, qid in enumerate(question_ids):
                        cur.execute(
                            """INSERT INTO question_set_membership
                               (question_set_id, question_id, org_id, position)
                               VALUES (%s::uuid, %s::uuid, %s::uuid, %s)""",
                            (qs_id, qid, caller.org_id, idx),
                        )
                    assignment_id = str(_uuid.uuid4())
                    cfg = _json.dumps({
                        "adaptive": True, "max_questions": len(question_ids),
                        "time_limit_minutes": req.duration_minutes,
                        "difficulty": req.difficulty, "shuffle_questions": False,
                        "include_case": req.include_case,
                        # Snapshot the week scope so it stays attributed to this
                        # exam even if the session's scope changes later (P-S-2.3).
                        "scope_session_id": req.session_id,
                        "scope_concepts": req.scope_concepts or [],
                    })
                    cur.execute(
                        """INSERT INTO assignment
                           (assignment_id, course_id, org_id, title, question_set_id, config,
                            status, created_by)
                           VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s::uuid, %s::jsonb,
                                   %s, %s)""",
                        (assignment_id, course_id, caller.org_id, req.title, qs_id, cfg,
                         "draft" if req.draft else "active", caller.user_id),
                    )
                    # Practice / assignment / exam — set only if the column exists
                    # (migration_008), so an un-migrated DB still assigns fine.
                    cur.execute("""SELECT 1 FROM information_schema.columns
                                   WHERE table_name='assignment' AND column_name='assignment_type'""")
                    if cur.fetchone() is not None:
                        cur.execute("UPDATE assignment SET assignment_type = %s WHERE assignment_id = %s::uuid",
                                    (req.assignment_type, assignment_id))
                repo.conn.commit()
                # Now that the questions are committed, build their expected paths in
                # the background. This is the whole point: by the time a student opens
                # the assignment the paths are already there, so no answer pays for
                # one mid-exam.
                if has_ep:
                    _fill_expected_paths_async(
                        d["settings"], caller.org_id,
                        [(qid, q.q, [q.concept_id or q.topic or "general"])
                         for qid, q in zip(question_ids, req.questions)])
                return {"status": "completed", "assignment_id": assignment_id,
                        "question_count": len(question_ids)}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.QUESTIONS_LIST)
    def list_questions(
        course_id: str,
        status: Optional[str] = None,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """List all questions for a course, optionally filtered by status."""
        import json as _json

        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)

                repo.set_tenant(caller.org_id)

                # Check if points column exists
                has_points = False
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT 1 FROM information_schema.columns
                           WHERE table_name = 'question' AND column_name = 'points'"""
                    )
                    has_points = cur.fetchone() is not None

                points_col = ", points" if has_points else ""
                sql = f"""SELECT question_id, course_id, concept_ids, text,
                                question_type, difficulty, status, created_by, created_at{points_col}
                         FROM question
                         WHERE course_id = %s"""
                params = [course_id]

                if status:
                    sql += " AND status = %s"
                    params.append(status)

                sql += " ORDER BY created_at DESC"

                with repo.conn.cursor() as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()

                questions = []
                for r in rows:
                    diff_data = r[5] if isinstance(r[5], dict) else _json.loads(r[5]) if r[5] else {}
                    q_data = {
                        "question_id": str(r[0]),
                        "course_id": str(r[1]),
                        "concept_ids": r[2] if isinstance(r[2], list) else _json.loads(r[2]) if r[2] else [],
                        "text": r[3],
                        "question_type": r[4],
                        "difficulty": diff_data.get("level", "balanced"),
                        "status": r[6],
                        "created_by": r[7],
                        "created_at": r[8].isoformat() if r[8] else None,
                        "points": r[9] if has_points and len(r) > 9 else 1,
                    }
                    questions.append(q_data)

                return {"questions": questions, "total": len(questions)}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.QUESTION_GET)
    def get_question(
        question_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Retrieve a single question by ID."""
        import json as _json

        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)

                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT question_id, course_id, concept_ids, text,
                                  question_type, difficulty, status, created_by, created_at
                           FROM question WHERE question_id = %s""",
                        (question_id,),
                    )
                    row = cur.fetchone()

                if not row:
                    raise AuthorizationError("question not found")

                diff_data = row[5] if isinstance(row[5], dict) else _json.loads(row[5]) if row[5] else {}
                return {
                    "question_id": str(row[0]),
                    "course_id": str(row[1]),
                    "concept_ids": row[2] if isinstance(row[2], list) else _json.loads(row[2]) if row[2] else [],
                    "text": row[3],
                    "question_type": row[4],
                    "difficulty": diff_data.get("level", "balanced"),
                    "status": row[6],
                    "created_by": row[7],
                    "created_at": row[8].isoformat() if row[8] else None,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.put(R.QUESTION_UPDATE)
    def update_question(
        question_id: str,
        req: UpdateQuestionRequest,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Update question text and/or points. Professor only. Only draft/approved questions."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")

                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        "SELECT status FROM question WHERE question_id = %s",
                        (question_id,),
                    )
                    row = cur.fetchone()

                if not row:
                    raise AuthorizationError("question not found")
                if row[0] == "rejected":
                    raise AuthorizationError("cannot edit a rejected question")

                # Ensure points column exists (safe to run multiple times)
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """DO $$ BEGIN
                            ALTER TABLE question ADD COLUMN points INTEGER DEFAULT 1;
                        EXCEPTION WHEN duplicate_column THEN NULL;
                        END $$;"""
                    )

                # Build dynamic UPDATE
                updates = []
                params = []
                if req.text is not None:
                    updates.append("text = %s")
                    params.append(req.text)
                if req.points is not None:
                    updates.append("points = %s")
                    params.append(req.points)

                if not updates:
                    return {"question_id": question_id, "status": "no_changes"}

                updates.append("updated_at = NOW()")
                params.append(question_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE question SET {', '.join(updates)} WHERE question_id = %s",
                        params,
                    )
                repo.conn.commit()

                return {"question_id": question_id, "status": "updated",
                        "text": req.text, "points": req.points}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.QUESTION_APPROVE)
    def approve_question(
        question_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Approve a draft question for use in assignments. Professor only."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")

                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        "SELECT status FROM question WHERE question_id = %s",
                        (question_id,),
                    )
                    row = cur.fetchone()

                if not row:
                    raise AuthorizationError("question not found")
                if row[0] != "draft":
                    raise AuthorizationError(f"cannot approve question in status '{row[0]}'")

                with repo.conn.cursor() as cur:
                    cur.execute(
                        "UPDATE question SET status = 'approved', updated_at = NOW() WHERE question_id = %s",
                        (question_id,),
                    )
                repo.conn.commit()

                return {"question_id": question_id, "status": "approved"}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.QUESTION_REJECT)
    def reject_question(
        question_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Reject a draft question. Professor only."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")

                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        "SELECT status FROM question WHERE question_id = %s",
                        (question_id,),
                    )
                    row = cur.fetchone()

                if not row:
                    raise AuthorizationError("question not found")
                if row[0] != "draft":
                    raise AuthorizationError(f"cannot reject question in status '{row[0]}'")

                with repo.conn.cursor() as cur:
                    cur.execute(
                        "UPDATE question SET status = 'rejected', updated_at = NOW() WHERE question_id = %s",
                        (question_id,),
                    )
                repo.conn.commit()

                return {"question_id": question_id, "status": "rejected"}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


# ── M6 Delivery ──────────────────────────────────────────────────────────────

class CreateAssignmentRequest(BaseModel):
    """POST body for creating an assignment."""
    title: str = Field(..., min_length=1, max_length=500)
    question_ids: List[str] = Field(..., min_length=1, max_length=200)
    config: Optional[dict] = None


def _purge_draft_assignment(conn, assignment_id: str, question_set_id: str) -> None:
    """Tear down a draft assignment and all its (preview) children in FK-safe
    order. Shared by the discard endpoint and the abandoned-draft cleanup. The
    caller has already verified ownership + draft status."""
    with conn.cursor() as cur:
        cur.execute(
            """DELETE FROM evaluation WHERE turn_id IN (
                   SELECT turn_id FROM session_turn WHERE session_id IN (
                       SELECT session_id FROM exam_session WHERE assignment_id = %s::uuid))""",
            (assignment_id,))
    try:
        with conn.cursor() as cur:
            cur.execute(
                """DELETE FROM question_eds_aggregate WHERE session_id IN (
                       SELECT session_id FROM exam_session WHERE assignment_id = %s::uuid)""",
                (assignment_id,))
    except Exception:  # noqa: BLE001 - table may not exist
        conn.rollback()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM grade WHERE assignment_id = %s::uuid", (assignment_id,))
        cur.execute(
            """DELETE FROM session_turn WHERE session_id IN (
                   SELECT session_id FROM exam_session WHERE assignment_id = %s::uuid)""",
            (assignment_id,))
        cur.execute("DELETE FROM exam_session WHERE assignment_id = %s::uuid", (assignment_id,))
        cur.execute("DELETE FROM question_set_membership WHERE question_set_id = %s::uuid",
                    (question_set_id,))
        # Assignment before its question_set (FK), then the set once unreferenced.
        cur.execute("DELETE FROM assignment WHERE assignment_id = %s::uuid AND status = 'draft'",
                    (assignment_id,))
        cur.execute(
            """DELETE FROM question_set WHERE question_set_id = %s::uuid
               AND NOT EXISTS (SELECT 1 FROM assignment WHERE question_set_id = %s::uuid)""",
            (question_set_id, question_set_id))
    conn.commit()


# A draft older than this with no publish is treated as abandoned (dry-run/
# preview the professor navigated away from) and cleaned up opportunistically.
_ABANDONED_DRAFT_AGE = "2 hours"


def _cleanup_abandoned_drafts(repo, course_id: str, org_id: str, user_id: str) -> None:
    """Purge the caller's own stale draft assignments for this course. Runs
    opportunistically on list; non-fatal so a cleanup hiccup never breaks the
    list. Recent drafts (in-progress dry-runs) are kept and shown with a badge."""
    try:
        with repo.conn.cursor() as cur:
            cur.execute(
                """SELECT assignment_id::text, question_set_id::text FROM assignment
                   WHERE course_id = %%s::uuid AND org_id = %%s::uuid AND created_by = %%s
                     AND status = 'draft' AND created_at < now() - interval '%s'""" % _ABANDONED_DRAFT_AGE,
                (course_id, org_id, user_id))
            stale = cur.fetchall()
        for aid, qsid in stale:
            _purge_draft_assignment(repo.conn, aid, qsid)
        if stale:
            logger.info("Cleaned %d abandoned draft(s) for course %s", len(stale), course_id[:8])
    except Exception as exc:  # noqa: BLE001
        try:
            repo.conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning("Abandoned-draft cleanup failed for %s: %s", course_id[:8], exc)


class SubmitAnswerRequest(BaseModel):
    """POST body for submitting an answer."""
    question_index: int = Field(..., ge=0)
    answer_text: str = Field(..., min_length=1, max_length=MAX_ANSWER_LENGTH)


def _register_delivery(app: FastAPI, deps) -> None:
    """Assignment creation, exam start, answer submission, and session status.

    All endpoints implement real DB operations and Bedrock-based Socratic
    evaluation (no stubs).
    """
    import json as _json, uuid as _uuid
    from datetime import datetime, timezone

    # ── POST /api/courses/{course_id}/assignments ─────────────────────────
    @app.post(R.ASSIGNMENTS_LIST)
    def create_assignment(
        course_id: str,
        req: CreateAssignmentRequest,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Create an assignment with an inline list of question_ids."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)

                question_ids = req.question_ids
                if not question_ids:
                    raise AuthorizationError("question_ids must not be empty")

                with repo.conn.cursor() as cur:
                    placeholders = ",".join(["%s"] * len(question_ids))
                    cur.execute(
                        f"SELECT question_id, text, concept_ids FROM question "
                        f"WHERE question_id::text IN ({placeholders}) AND course_id = %s::uuid",
                        (*question_ids, course_id),
                    )
                    found = cur.fetchall()

                if len(found) != len(question_ids):
                    found_ids = {str(r[0]) for r in found}
                    missing = [qid for qid in question_ids if qid not in found_ids]
                    raise AuthorizationError(
                        f"questions not found in this course: {missing[:5]}"
                    )

                qs_id = str(_uuid.uuid4())
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO question_set (question_set_id, course_id, org_id, title, created_by)
                           VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s)""",
                        (qs_id, course_id, caller.org_id, req.title, caller.user_id),
                    )
                    for idx, qid in enumerate(question_ids):
                        cur.execute(
                            """INSERT INTO question_set_membership
                               (question_set_id, question_id, org_id, position)
                               VALUES (%s::uuid, %s::uuid, %s::uuid, %s)""",
                            (qs_id, qid, caller.org_id, idx),
                        )

                config_raw = req.config or {}
                db_config = {
                    "adaptive": config_raw.get("adaptive", True),
                    "max_questions": len(question_ids),
                    "time_limit_minutes": config_raw.get("duration_minutes"),
                    "difficulty": config_raw.get("difficulty", "balanced"),
                    "shuffle_questions": config_raw.get("shuffle_questions", False),
                    "include_case": config_raw.get("include_case", False),
                }

                assignment_id = str(_uuid.uuid4())
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO assignment
                           (assignment_id, course_id, org_id, title, question_set_id, config,
                            status, created_by)
                           VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s::uuid, %s::jsonb,
                                   'active', %s)
                           RETURNING assignment_id, created_at""",
                        (assignment_id, course_id, caller.org_id, req.title,
                         qs_id, _json.dumps(db_config), caller.user_id),
                    )
                    row = cur.fetchone()
                repo.conn.commit()

                return {
                    "assignment_id": str(row[0]),
                    "course_id": course_id,
                    "title": req.title,
                    "question_set_id": qs_id,
                    "question_ids": question_ids,
                    "config": db_config,
                    "status": "active",
                    "created_by": caller.user_id,
                    "created_at": row[1].isoformat() if row[1] else None,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── GET /api/courses/{course_id}/assignments ──────────────────────────
    @app.get(R.ASSIGNMENTS_LIST)
    def list_assignments(
        course_id: str,
        status: Optional[str] = None,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)

                # Opportunistically purge the caller's abandoned (stale) drafts,
                # then return the rest INCLUDING recent drafts — the UI shows them
                # with a "Draft" badge so an unpublished assignment isn't invisible.
                _cleanup_abandoned_drafts(repo, course_id, caller.org_id, caller.user_id)

                query = """SELECT assignment_id, title, question_set_id, config,
                                  status, created_by, created_at
                           FROM assignment
                           WHERE course_id = %s::uuid AND org_id = %s::uuid"""
                params: list = [course_id, caller.org_id]
                if status:
                    query += " AND status = %s"
                    params.append(status)
                query += " ORDER BY created_at DESC"

                with repo.conn.cursor() as cur:
                    cur.execute(query, params)
                    rows = cur.fetchall()

                return [
                    {
                        "assignment_id": str(r[0]),
                        "course_id": course_id,
                        "title": r[1],
                        "question_set_id": str(r[2]),
                        "config": r[3],
                        "status": r[4],
                        "created_by": r[5],
                        "created_at": r[6].isoformat() if r[6] else None,
                    }
                    for r in rows
                ]
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── GET /api/assignments/{assignment_id} ──────────────────────────────
    @app.get(R.ASSIGNMENT_GET)
    def get_assignment(
        assignment_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute("""SELECT 1 FROM information_schema.columns
                                   WHERE table_name='assignment' AND column_name='assignment_type'""")
                    type_col = "assignment_type" if cur.fetchone() is not None else "'assignment'"
                    cur.execute(
                        f"""SELECT assignment_id, course_id, title, question_set_id,
                                  config, status, created_by, created_at, {type_col}
                           FROM assignment WHERE assignment_id = %s::uuid""",
                        (assignment_id,),
                    )
                    row = cur.fetchone()

                if not row:
                    raise AuthorizationError("assignment not found")

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT question_id FROM question_set_membership
                           WHERE question_set_id = %s::uuid ORDER BY position""",
                        (str(row[3]),),
                    )
                    qids = [str(r[0]) for r in cur.fetchall()]

                return {
                    "assignment_id": str(row[0]),
                    "course_id": str(row[1]),
                    "title": row[2],
                    "question_set_id": str(row[3]),
                    "question_ids": qids,
                    "config": row[4],
                    "status": row[5],
                    "created_by": row[6],
                    "created_at": row[7].isoformat() if row[7] else None,
                    "assignment_type": row[8] or "assignment",
                    # Org UI flag: does the student exam render the probe before TTS audio.
                    "text_first": get_text_first(repo, caller.org_id),
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ASSIGNMENT_PREVIEW)
    def preview_assignment(
        assignment_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Read-only view of exactly what a student sees, for a professor.

        Returns the same question payload as ASSIGNMENT_START (question text +
        derived topic + position) plus the meta needed to render the exam chrome
        and the case materials — but never creates an exam_session, submits an
        answer, or grades anything. Professors only (tenant-scoped)."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute("""SELECT 1 FROM information_schema.columns
                                   WHERE table_name='assignment' AND column_name='assignment_type'""")
                    type_col = "assignment_type" if cur.fetchone() is not None else "'assignment'"
                    cur.execute(
                        f"""SELECT course_id, title, question_set_id, config, status, {type_col}
                            FROM assignment WHERE assignment_id = %s::uuid""",
                        (assignment_id,),
                    )
                    arow = cur.fetchone()
                if not arow:
                    raise AuthorizationError("assignment not found")

                question_set_id = str(arow[2])
                cfg = arow[3] if isinstance(arow[3], dict) else (_json.loads(arow[3]) if arow[3] else {})

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT q.question_id, q.text, q.concept_ids, qsm.position
                           FROM question_set_membership qsm
                           JOIN question q ON q.question_id = qsm.question_id
                           WHERE qsm.question_set_id = %s::uuid
                           ORDER BY qsm.position""",
                        (question_set_id,),
                    )
                    qrows = cur.fetchall()

                questions = []
                for qr in qrows:
                    concept_ids = qr[2] if isinstance(qr[2], list) else []
                    questions.append({
                        "question_id": str(qr[0]),
                        "topic": concept_ids[0] if concept_ids else "general",
                        "text": qr[1],
                        "index": qr[3],
                    })

                # Case materials — mirror ASSIGNMENT_CASE (only when case-based).
                case_materials = []
                if cfg.get("include_case"):
                    for m in repo.list_materials(str(arow[0])):
                        if not m.current_version_id:
                            continue
                        v = repo.get_version(m.current_version_id)
                        if not v or getattr(v.status, "value", str(v.status)) != "ready":
                            continue
                        case_materials.append({
                            "material_id": m.material_id,
                            "version_id": v.material_version_id,
                            "file_name": v.file_name,
                            "source_type": getattr(v.source_type, "value", str(v.source_type)),
                        })

                return {
                    "assignment_id": assignment_id,
                    "title": arow[1],
                    "assignment_type": arow[5] or "assignment",
                    "status": arow[4],
                    "difficulty": cfg.get("difficulty", "balanced"),
                    "duration_minutes": cfg.get("time_limit_minutes"),
                    "include_case": bool(cfg.get("include_case")),
                    "question_count": len(questions),
                    "questions": questions,
                    "case_materials": case_materials,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── GET /api/assignments/{assignment_id}/sessions ──────────────────────
    @app.get(R.ASSIGNMENT_SESSIONS)
    def list_assignment_sessions(
        assignment_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """List all exam sessions for an assignment (professor only)."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT assignment_id FROM assignment
                           WHERE assignment_id = %s::uuid AND org_id = %s::uuid""",
                        (assignment_id, caller.org_id),
                    )
                    if not cur.fetchone():
                        raise AuthorizationError("assignment not found")

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT es.session_id, es.student_id, es.status,
                                  es.current_turn_index, es.completed_at,
                                  g.final_score
                           FROM exam_session es
                           LEFT JOIN grade g ON g.session_id = es.session_id
                           WHERE es.assignment_id = %s::uuid AND NOT es.is_preview
                           ORDER BY es.started_at DESC""",
                        (assignment_id,),
                    )
                    rows = cur.fetchall()

                # Practice tests are anonymized to the professor: replace the
                # student's email with a stable "Student N" label so performance
                # is visible but identity is not (issue S-E-2.2). Labels are keyed
                # off the sorted student_id so the mapping is deterministic.
                with repo.conn.cursor() as cur:
                    anon = _assignment_is_practice(cur, assignment_id)
                label_by_student = {}
                if anon:
                    for i, sid in enumerate(sorted({r[1] for r in rows})):
                        label_by_student[sid] = f"Student {i + 1}"

                sessions = []
                for r in rows:
                    overall_eds = round(float(r[5]) * 100, 1) if r[5] is not None else None
                    who = label_by_student.get(r[1], r[1]) if anon else r[1]
                    sessions.append({
                        "session_id": str(r[0]),
                        "student_id": who,
                        "student_email": who,
                        "status": r[2],
                        "current_turn_index": r[3],
                        "overall_eds": overall_eds,
                        "completed_at": r[4].isoformat() if r[4] else None,
                        "anonymized": anon,
                    })

                return sessions
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── POST /api/assignments/{assignment_id}/publish ─────────────────────
    @app.post(R.ASSIGNMENT_PUBLISH)
    def publish_assignment(
        assignment_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Flip a draft assignment to active and purge its preview sessions."""
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """UPDATE assignment SET status = 'active'
                           WHERE assignment_id = %s::uuid AND created_by = %s
                           RETURNING assignment_id""",
                        (assignment_id, caller.user_id),
                    )
                    if not cur.fetchone():
                        raise AuthorizationError("draft not found")
                    # Tear down preview-session children in dependency order before
                    # purging exam_session (no ON DELETE CASCADE on this chain).
                    # Scoped to this assignment's PREVIEW sessions only.
                    # Delete evaluations for turns in preview sessions of this assignment
                    cur.execute(
                        """DELETE FROM evaluation
                           WHERE turn_id IN (
                               SELECT turn_id FROM session_turn
                               WHERE session_id IN (
                                   SELECT session_id FROM exam_session
                                   WHERE assignment_id = %s::uuid AND is_preview
                               )
                           )""",
                        (assignment_id,),
                    )
                    # Delete question_eds_aggregate rows for preview sessions
                    cur.execute(
                        """DELETE FROM question_eds_aggregate
                           WHERE session_id IN (
                               SELECT session_id FROM exam_session
                               WHERE assignment_id = %s::uuid AND is_preview
                           )""",
                        (assignment_id,),
                    )
                    # Delete grades for preview sessions
                    cur.execute(
                        """DELETE FROM grade
                           WHERE session_id IN (
                               SELECT session_id FROM exam_session
                               WHERE assignment_id = %s::uuid AND is_preview
                           )""",
                        (assignment_id,),
                    )
                    # Delete session turns for preview sessions
                    cur.execute(
                        """DELETE FROM session_turn
                           WHERE session_id IN (
                               SELECT session_id FROM exam_session
                               WHERE assignment_id = %s::uuid AND is_preview
                           )""",
                        (assignment_id,),
                    )
                    # Purge the preview sessions themselves
                    cur.execute(
                        "DELETE FROM exam_session WHERE assignment_id = %s::uuid AND is_preview",
                        (assignment_id,),
                    )
                repo.conn.commit()
                return {"status": "active", "assignment_id": assignment_id}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── POST /api/assignments/{assignment_id}/discard ──────────────────────
    @app.post(R.ASSIGNMENT_DISCARD)
    def discard_draft(
        assignment_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Delete a draft assignment and all related data, owner-only (mirrors delete_assignment)."""
        def _do():
            d = deps(); repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)

                # Verify draft exists, is owned by caller, and is still a draft
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT question_set_id FROM assignment
                           WHERE assignment_id = %s::uuid AND org_id = %s::uuid
                             AND created_by = %s AND status = 'draft'""",
                        (assignment_id, caller.org_id, caller.user_id),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise AuthorizationError("draft not found")
                    question_set_id = str(row[0])

                with repo.conn.cursor() as cur:
                    # Delete evaluations for turns in sessions of this assignment
                    cur.execute(
                        """DELETE FROM evaluation
                           WHERE turn_id IN (
                               SELECT turn_id FROM session_turn
                               WHERE session_id IN (
                                   SELECT session_id FROM exam_session
                                   WHERE assignment_id = %s::uuid
                               )
                           )""",
                        (assignment_id,),
                    )
                    # Delete question_eds_aggregate rows for sessions of this assignment
                    try:
                        cur.execute(
                            """DELETE FROM question_eds_aggregate
                               WHERE session_id IN (
                                   SELECT session_id FROM exam_session
                                   WHERE assignment_id = %s::uuid
                               )""",
                            (assignment_id,),
                        )
                    except Exception:
                        repo.conn.rollback()
                        # Table may not exist; continue

                with repo.conn.cursor() as cur:
                    # Delete grades for this assignment
                    cur.execute(
                        "DELETE FROM grade WHERE assignment_id = %s::uuid",
                        (assignment_id,),
                    )
                    # Delete session turns
                    cur.execute(
                        """DELETE FROM session_turn
                           WHERE session_id IN (
                               SELECT session_id FROM exam_session
                               WHERE assignment_id = %s::uuid
                           )""",
                        (assignment_id,),
                    )
                    # Delete exam sessions
                    cur.execute(
                        "DELETE FROM exam_session WHERE assignment_id = %s::uuid",
                        (assignment_id,),
                    )
                    # Delete question_set_membership
                    cur.execute(
                        "DELETE FROM question_set_membership WHERE question_set_id = %s::uuid",
                        (question_set_id,),
                    )
                    # Assignment must go before its question_set: assignment.question_set_id
                    # is a FK, so removing the set first violates the constraint.
                    cur.execute(
                        """DELETE FROM assignment
                           WHERE assignment_id = %s::uuid AND created_by = %s AND status = 'draft'
                           RETURNING assignment_id""",
                        (assignment_id, caller.user_id),
                    )
                    if not cur.fetchone():
                        raise AuthorizationError("draft not found")
                    # Only drop the set once no assignment references it
                    cur.execute(
                        """DELETE FROM question_set
                           WHERE question_set_id = %s::uuid
                             AND NOT EXISTS (
                                 SELECT 1 FROM assignment
                                 WHERE question_set_id = %s::uuid
                             )""",
                        (question_set_id, question_set_id),
                    )
                repo.conn.commit()

                return {"status": "discarded", "assignment_id": assignment_id}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── POST /api/assignments/{assignment_id}/close ────────────────────────
    @app.post(R.ASSIGNMENT_CLOSE)
    def close_assignment(
        assignment_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Professor closes an assignment — no new sessions can be started."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        "UPDATE assignment SET status = 'closed' WHERE assignment_id = %s::uuid AND org_id = %s::uuid RETURNING assignment_id",
                        (assignment_id, caller.org_id),
                    )
                    row = cur.fetchone()
                repo.conn.commit()

                if not row:
                    raise AuthorizationError("assignment not found")
                return {"assignment_id": str(row[0]), "status": "closed"}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── GET /api/assignments/{assignment_id}/results ──────────────────────
    @app.get(R.ASSIGNMENT_RESULTS)
    def exam_results(
        assignment_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("student"),
    ):
        """The caller's most-recent exam results (frontend: getExamResults)."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                return _query_exam_results(repo, assignment_id, caller.user_id,
                                           for_student=caller.role != Role.PROFESSOR)
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── POST /api/assignments/{assignment_id}/start ───────────────────────
    @app.post(R.ASSIGNMENT_START)
    def start_exam(
        assignment_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("student"),
    ):
        """Start an exam session: creates session row, returns questions.

        Uses INSERT ... ON CONFLICT DO NOTHING to prevent duplicate active
        sessions from concurrent requests (race condition fix).
        """
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT assignment_id, course_id, question_set_id, config, status, created_by
                           FROM assignment WHERE assignment_id = %s::uuid""",
                        (assignment_id,),
                    )
                    arow = cur.fetchone()

                if not arow:
                    raise AuthorizationError("assignment not found")
                is_preview = _start_is_preview(arow[4], arow[5], caller.role, caller.user_id)

                # Single-attempt enforcement: assignments and exams can't be retaken
                # once completed — only practice tests may be re-taken. Previews (a
                # professor's dry run) never count and are always allowed.
                if not is_preview:
                    with repo.conn.cursor() as cur:
                        if not _assignment_is_practice(cur, assignment_id):
                            cur.execute(
                                """SELECT 1 FROM exam_session
                                   WHERE assignment_id = %s::uuid AND student_id = %s
                                         AND status = 'completed' LIMIT 1""",
                                (assignment_id, caller.user_id),
                            )
                            if cur.fetchone():
                                raise AuthorizationError(
                                    "This assignment has already been submitted and "
                                    "can't be retaken.")

                course_id = str(arow[1])
                question_set_id = str(arow[2])

                # Atomic upsert: INSERT with ON CONFLICT prevents duplicate active
                # sessions even under concurrent requests from the same student.
                session_id = str(_uuid.uuid4())
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO exam_session
                           (session_id, assignment_id, student_id, org_id, course_id,
                            status, current_turn_index, questions_delivered, concepts_covered,
                            is_preview)
                           VALUES (%s::uuid, %s::uuid, %s, %s::uuid, %s::uuid,
                                   'active', 0, '[]'::jsonb, '[]'::jsonb, %s)
                           ON CONFLICT (assignment_id, student_id)
                              WHERE status = 'active'
                           DO NOTHING
                           RETURNING session_id""",
                        (session_id, assignment_id, caller.user_id,
                         caller.org_id, course_id, is_preview),
                    )
                    inserted = cur.fetchone()

                if inserted:
                    session_id = str(inserted[0])
                    repo.conn.commit()
                else:
                    # Session already exists — retrieve it
                    repo.conn.rollback()
                    with repo.conn.cursor() as cur:
                        cur.execute(
                            """SELECT session_id FROM exam_session
                               WHERE assignment_id = %s::uuid AND student_id = %s
                                     AND status = 'active'""",
                            (assignment_id, caller.user_id),
                        )
                        existing = cur.fetchone()
                    if existing:
                        session_id = str(existing[0])
                    # else: the conflict guard matched but session was completed
                    # between our insert and select — use the new session_id (edge case)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT q.question_id, q.text, q.concept_ids, qsm.position
                           FROM question_set_membership qsm
                           JOIN question q ON q.question_id = qsm.question_id
                           WHERE qsm.question_set_id = %s::uuid
                           ORDER BY qsm.position""",
                        (question_set_id,),
                    )
                    qrows = cur.fetchall()

                questions = []
                for qr in qrows:
                    concept_ids = qr[2] if isinstance(qr[2], list) else []
                    topic = concept_ids[0] if concept_ids else "general"
                    questions.append({
                        "question_id": str(qr[0]),
                        "topic": topic,
                        "text": qr[1],
                        "index": qr[3],
                    })

                # Build any missing reasoning paths NOW, while the student is still
                # reading and speaking their first answer — that is 30-60s of slack.
                # Grading a turn without its path is not free: no eds_components is
                # stored, so the next probe can't see what they already covered and
                # the question's EDS union drops that turn.
                _prefill_session_paths(repo, d["settings"], caller.org_id, qrows)

                return {
                    "session_id": session_id,
                    "questions": questions,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── POST /api/sessions/{session_id}/answer ────────────────────────────
    @app.post(R.SESSION_ANSWER)
    def submit_answer(
        session_id: str,
        req: SubmitAnswerRequest,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("student"),
    ):
        """Submit an answer: record in session_turn, evaluate via Bedrock Qwen3."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                settings = d["settings"]
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT session_id, assignment_id, student_id, course_id,
                                  status, current_turn_index, org_id
                           FROM exam_session WHERE session_id = %s::uuid""",
                        (session_id,),
                    )
                    srow = cur.fetchone()

                if not srow:
                    raise AuthorizationError("session not found")
                if srow[4] != "active":
                    raise AuthorizationError("session is not active")
                if srow[2] != caller.user_id:
                    raise AuthorizationError("not your session")

                assignment_id = str(srow[1])
                course_id = str(srow[3])
                org_id = str(srow[6])
                question_index = req.question_index

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT a.question_set_id FROM assignment a
                           WHERE a.assignment_id = %s::uuid""",
                        (assignment_id,),
                    )
                    qs_row = cur.fetchone()
                if not qs_row:
                    raise AuthorizationError("assignment not found")
                question_set_id = str(qs_row[0])

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT q.question_id, q.text, q.concept_ids
                           FROM question_set_membership qsm
                           JOIN question q ON q.question_id = qsm.question_id
                           WHERE qsm.question_set_id = %s::uuid AND qsm.position = %s""",
                        (question_set_id, question_index),
                    )
                    qrow = cur.fetchone()

                if not qrow:
                    raise AuthorizationError(
                        f"no question at index {question_index} in this assignment"
                    )

                question_id = str(qrow[0])
                question_text = qrow[1]
                concept_ids_for_question = qrow[2] if isinstance(qrow[2], list) else []

                # ── Fetch expected_path for EDS scoring ──────────────────
                expected_path = {}
                with repo.conn.cursor() as cur:
                    cur.execute(
                        "SELECT expected_path FROM question WHERE question_id = %s::uuid",
                        (question_id,),
                    )
                    ep_row = cur.fetchone()
                    if ep_row and ep_row[0]:
                        expected_path = ep_row[0] if isinstance(ep_row[0], dict) else _json.loads(ep_row[0])

                # A missing path used to be generated right here, so the first answer
                # to a question paid ~10s (up to 3x that on a malformed sample) while
                # the student waited. Queue it in the background instead and grade
                # THIS turn on the no-path Socratic rubric below — which submit_answer
                # already supports via use_eds_formula=False. Later turns, and every
                # other student, get full EDS once the thread lands.
                if not expected_path.get("nodes"):
                    _fill_expected_paths_async(
                        settings, org_id,
                        [(question_id, question_text, concept_ids_for_question)])
                    # start_exam already queued this, so normally it has landed by now.
                    # If it is still running, a few seconds is cheaper than grading
                    # blind: without the path no eds_components is written, so the
                    # next probe can't see what this answer covered and the question's
                    # EDS union drops the turn. Past the budget, degrade and move on.
                    with _timed("rubric_wait", qid=question_id[:8]):
                        expected_path = _await_expected_path(
                            repo, question_id, _EXPECTED_PATH_WAIT_S)
                    if not expected_path.get("nodes"):
                        logger.warning("No expected_path for question %s after %.0fs — "
                                       "grading this turn on the no-path rubric",
                                       question_id[:8], _EXPECTED_PATH_WAIT_S)
                        expected_path = {}

                # ── Multi-turn: insert new sub-turn row ──────────────────
                turn_id = str(_uuid.uuid4())
                now = datetime.now(timezone.utc)

                # Count existing sub-turns for this question
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT COUNT(*) FROM session_turn
                           WHERE session_id = %s::uuid AND turn_index = %s""",
                        (session_id, question_index),
                    )
                    sub_turn_count = cur.fetchone()[0]

                with repo.conn.cursor() as cur:
                    # Each sub-turn is its own row (unique on session+turn+sub_turn),
                    # so a retried submit updates that sub-turn rather than the question.
                    cur.execute(
                        """INSERT INTO session_turn
                           (turn_id, session_id, org_id, turn_index, sub_turn_index,
                            question_id, student_answer, answered_at)
                           VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s::uuid, %s, %s)
                           ON CONFLICT (session_id, turn_index, sub_turn_index)
                           DO UPDATE SET student_answer = EXCLUDED.student_answer,
                                         answered_at = EXCLUDED.answered_at
                           RETURNING turn_id""",
                        (turn_id, session_id, org_id, question_index, sub_turn_count,
                         question_id, req.answer_text, now),
                    )
                    actual_turn_id = str(cur.fetchone()[0])

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """UPDATE exam_session
                           SET current_turn_index = GREATEST(current_turn_index, %s + 1)
                           WHERE session_id = %s::uuid""",
                        (question_index, session_id),
                    )
                repo.conn.commit()

                # ── Gather prior answers for context ─────────────────────
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT student_answer FROM session_turn
                           WHERE session_id = %s::uuid AND question_id = %s::uuid
                                 AND turn_id != %s::uuid
                           ORDER BY answered_at""",
                        (session_id, question_id, actual_turn_id),
                    )
                    prior_answers = [r[0] for r in cur.fetchall() if r[0]]

                # ── Decide evaluation strategy based on expected_path ────
                use_eds_formula = bool(expected_path.get("nodes"))

                # Steer this turn's probe at whatever the student has not yet covered,
                # traversing the question's own sub-graph instead of letting the model
                # re-probe ground already demonstrated.
                probe_directive = ""

                if use_eds_formula:
                    seen_n, seen_e = _prior_coverage(repo, session_id, question_index)
                    target = _probe_target(expected_path, seen_n, seen_e)
                    if target:
                        probe_directive = f"\nPROBE TARGET (choose your probe to address this):\n{target}\n"

                    # ── Combined Socratic + EDS evaluation prompt ─────────
                    # Assembled by the shared builder so the admin tone-lab endpoint
                    # renders byte-identically (and an approved override can retune it).
                    system_prompt, _ = build_examiner_eval_prompt(
                        repo, org_id, question_text, expected_path, probe_directive)
                else:
                    # ── Legacy Socratic-only prompt (no expected_path) ────
                    system_prompt = (
                        "You are an Epistemy Socratic oral examiner. "
                        f"The current exam question is: \"{question_text}\". "
                        "You scaffold: when an answer is incomplete, you do NOT give the answer away. "
                        "Instead you ask ONE smaller guiding sub-question about an intermediate concept "
                        "or a single causal link, so the student can build toward the answer themselves. "
                        "First, detect whether the student is NOT answering but instead asking you to "
                        "rephrase, reword, repeat, restate, or clarify the question. If so, set clarify=true. "
                        "Otherwise, decide whether the student genuinely attempted to answer THIS question "
                        "with relevant content. "
                        "Treat 'I don't know', 'not sure', 'no idea', blank replies, gibberish, off-topic "
                        "answers, refusals, or asking to skip as NOT answered. "
                        "Assess whether the answer demonstrates causal understanding (not just recall). "
                        "'adequate' may be true ONLY if 'answered' is true AND the student demonstrates "
                        "clear mechanistic/causal reasoning with specific details — not just a surface-level or partial answer. "
                        "DEFAULT to adequate=false unless the answer is genuinely thorough. "
                        "When adequate=false, you MUST provide a probe sub-question. "
                        "The probe must be grounded in THIS student's actual answer — quote or paraphrase "
                        "the specific thing they said (or the step they skipped) and push on that exact gap. "
                        "Never emit a generic, reusable phrase like 'tell me more' or 'explain the "
                        "mechanism'; the probe should only make sense as a reply to what they just said. "
                        "Respond ONLY with minified JSON, no prose and no code fences: "
                        '{"clarify": bool, "answered": bool, "adequate": bool, '
                        '"feedback": "one sentence on what was strong or thin", '
                        '"probe": "ONE short follow-up that quotes/paraphrases the student and targets their specific gap"}'
                    )

                ctx = f"Exam question: {question_text}\n\n"
                if prior_answers:
                    ctx += "Prior exchanges on this question:\n"
                    for pa in prior_answers:
                        ctx += f"Student: {pa}\n"
                    ctx += "\n"
                ctx += f"Student's latest answer: {req.answer_text}"

                # ── Hybrid eval mode: fast Haiku spoken probe now, Sonnet EDS async ──
                # Off by default (examiner_config.eval_mode='sonnet'). When 'hybrid', the
                # student gets the probe from a fast Haiku call and EDS scoring is computed
                # on Sonnet off the critical path (the score lands a beat later).
                if use_eds_formula and get_eval_mode(repo, org_id) == "hybrid":
                    probe_sys = build_examiner_probe_prompt(question_text, probe_directive)
                    try:
                        pj = call_bedrock(settings, probe_sys, ctx,
                                          max_tokens=LLM_MAX_TOKENS_EVALUATION,
                                          temperature=0.2, model=HYBRID_PROBE_MODEL)
                        h_answered = bool(pj.get("answered", False))
                        h_adequate = bool(pj.get("adequate", False))
                        h_feedback = (pj.get("feedback") or "").strip()
                        h_probe = (pj.get("probe") or "").strip()
                    except Exception as eval_err:
                        logger.warning("Hybrid Haiku probe failed: %s", eval_err)
                        h_answered, h_adequate, h_feedback, h_probe = _heuristic_eval(
                            req.answer_text)
                    # Preliminary evaluation row; EDS score filled in by the async worker.
                    # eds_bucket must satisfy evaluation_bucket_chk (low|medium|high), so use
                    # a provisional bucket from answered/adequate; the async EDS overwrites it.
                    # `eds_pending` in raw_llm_output is the real "not yet scored" signal.
                    prelim = {"answered": h_answered, "adequate": h_adequate,
                              "feedback": h_feedback, "probe": h_probe,
                              "eds_pending": True, "eval_mode": "hybrid"}
                    prelim_bucket = ("high" if h_adequate
                                     else ("medium" if h_answered else "low"))
                    with repo.conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO evaluation
                               (evaluation_id, turn_id, org_id, course_id, student_id,
                                question_id, eds_score, eds_bucket, raw_llm_output)
                               VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s,
                                       %s::uuid, %s, %s, %s::jsonb)
                               ON CONFLICT (turn_id) DO UPDATE
                               SET raw_llm_output = EXCLUDED.raw_llm_output""",
                            (str(_uuid.uuid4()), actual_turn_id, org_id, course_id,
                             caller.user_id, question_id, 0.0, prelim_bucket,
                             _json.dumps(prelim)),
                        )
                    repo.conn.commit()
                    threading.Thread(
                        target=_run_hybrid_eds_bg,
                        args=(settings, org_id, course_id, caller.user_id, session_id,
                              question_index, actual_turn_id, question_id, question_text,
                              expected_path, ctx, h_answered, h_adequate, h_feedback, h_probe),
                        daemon=True).start()
                    return {"answered": h_answered, "adequate": h_adequate,
                            "feedback": h_feedback, "probe": h_probe,
                            "eds_delta": 0, "eds_pending": True}

                answered = False
                adequate = False
                feedback = ""
                probe = ""
                eds_delta = 0
                parsed = {}

                try:
                    # Bounded, unlike before: this call had no timeout at all, so it
                    # inherited the SDK's 600s default and a hung connection rode
                    # until CloudFront cut the student off at 60s with a 504. The
                    # retry budget was 3, and since call_bedrock only retries JSON
                    # parse failures, three bad samples ran back to back while the
                    # student waited. One attempt, 20s ceiling, then degrade.
                    with _timed("eval_llm", eds=use_eds_formula):
                        parsed = call_bedrock(
                            settings, system_prompt, ctx,
                            max_tokens=LLM_MAX_TOKENS_EVALUATION, temperature=0.1,
                            retries=1, timeout=_EVAL_TIMEOUT_S,
                        )
                    answered = bool(parsed.get("answered", False))
                    adequate = bool(parsed.get("adequate", False))
                    feedback = (parsed.get("feedback") or "").strip()
                    probe = (parsed.get("probe") or "").strip()
                except Exception as eval_err:
                    logger.warning("Bedrock Socratic eval failed: %s", eval_err)
                    answered, adequate, feedback, probe = _heuristic_eval(
                        req.answer_text
                    )

                # ── Compute EDS score ────────────────────────────────────
                if not use_eds_formula:
                    # Legacy fallback: fixed 0/4/10 scoring
                    if not answered:
                        eds_delta = 0
                    elif adequate:
                        eds_delta = 10
                    else:
                        eds_delta = 4

                    eval_id = str(_uuid.uuid4())
                    eval_data = {
                        "answered": answered,
                        "adequate": adequate,
                        "feedback": feedback,
                        "probe": probe,
                        "eds_delta": eds_delta,
                    }
                    with repo.conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO evaluation
                               (evaluation_id, turn_id, org_id, course_id, student_id,
                                question_id, eds_score, eds_bucket, raw_llm_output)
                               VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s,
                                       %s::uuid, %s, %s, %s::jsonb)
                               ON CONFLICT (turn_id) DO UPDATE
                               SET eds_score = EXCLUDED.eds_score,
                                   eds_bucket = EXCLUDED.eds_bucket,
                                   raw_llm_output = EXCLUDED.raw_llm_output""",
                            (eval_id, actual_turn_id, org_id, course_id, caller.user_id,
                             question_id,
                             eds_delta / 10.0,
                             "high" if adequate else ("medium" if answered else "low"),
                             _json.dumps(eval_data)),
                        )
                    repo.conn.commit()

                    return {
                        "answered": answered,
                        "adequate": adequate,
                        "feedback": feedback,
                        "probe": probe,
                        "eds_delta": eds_delta,
                    }

                # ── EDS Formula Path ─────────────────────────────────────
                eds_raw = parsed.get("eds", {})
                expected_nodes = expected_path.get("nodes", [])
                expected_edges = expected_path.get("edges", [])
                expected_extensions = expected_path.get("extensions", [])

                nodes_demonstrated = eds_raw.get("nodes_demonstrated", [])
                edges_demonstrated_indices = eds_raw.get("edges_demonstrated", [])
                recitation_score = float(eds_raw.get("recitation_score", 0.5))
                novel_extensions = eds_raw.get("novel_extensions", [])

                # Compute per-turn scores
                R = 1.0 - recitation_score
                node_score = len(nodes_demonstrated) / max(len(expected_nodes), 1)
                edge_score = len(edges_demonstrated_indices) / max(len(expected_edges), 1)
                max_ext = max(len(expected_extensions), 3)
                gen_score_norm = min(1.0, len(novel_extensions) / max_ext)

                # ── Accumulate across sub-turns for this question ─────────
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT e.eds_components FROM evaluation e
                           JOIN session_turn st ON st.turn_id = e.turn_id
                           WHERE st.session_id = %s::uuid AND st.turn_index = %s
                             AND e.turn_id != %s::uuid
                           ORDER BY st.answered_at""",
                        (session_id, question_index, actual_turn_id),
                    )
                    prior_components = [r[0] for r in cur.fetchall() if r[0]]

                # Union across all sub-turns
                all_nodes = set()
                all_edge_indices = set()
                all_extensions = set()
                min_recitation = recitation_score

                for pc in prior_components:
                    if isinstance(pc, str):
                        pc = _json.loads(pc)
                    all_nodes.update(pc.get("nodes_detected", []))
                    all_edge_indices.update(pc.get("edges_demonstrated", []))
                    all_extensions.update(pc.get("novel_extensions", []))
                    min_recitation = min(min_recitation, pc.get("raw_probe_score", 1.0))

                # Add current turn
                all_nodes.update(nodes_demonstrated)
                all_edge_indices.update(edges_demonstrated_indices)
                all_extensions.update(novel_extensions)
                min_recitation = min(min_recitation, recitation_score)

                # Aggregated scores
                agg_R = 1.0 - min_recitation
                agg_node_score = len(all_nodes) / max(len(expected_nodes), 1)
                agg_edge_score = len(all_edge_indices) / max(len(expected_edges), 1)
                agg_gen = min(1.0, len(all_extensions) / max(len(expected_extensions), 3))
                agg_coverage = (agg_node_score + agg_edge_score) / 2.0

                # Apply the EDS formula
                eds_question = (
                    agg_R * (EDS_ALPHA * agg_node_score + EDS_BETA * agg_edge_score)
                    + EDS_GAMMA * (1.0 - agg_R * agg_coverage) * agg_gen
                )
                eds_question = round(min(1.0, max(0.0, eds_question)), 4)

                # ── Store EDS components in evaluation ────────────────────
                eds_components_data = {
                    "node_score": node_score,
                    "edge_score": edge_score,
                    "r_gate": R,
                    "gen_score_norm": gen_score_norm,
                    "nodes_detected": list(nodes_demonstrated) if isinstance(nodes_demonstrated, set) else nodes_demonstrated,
                    "edges_demonstrated": list(edges_demonstrated_indices) if isinstance(edges_demonstrated_indices, set) else edges_demonstrated_indices,
                    "novel_extensions": list(novel_extensions) if isinstance(novel_extensions, set) else novel_extensions,
                    "raw_probe_score": recitation_score,
                }

                eval_id = str(_uuid.uuid4())
                eval_data = {
                    "answered": answered,
                    "adequate": adequate,
                    "feedback": feedback,
                    "probe": probe,
                    "eds_delta": int(eds_question * 10),
                    "eds_question": eds_question,
                }
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO evaluation
                           (evaluation_id, turn_id, org_id, course_id, student_id,
                            question_id, eds_score, eds_bucket, raw_llm_output, eds_components)
                           VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s,
                                   %s::uuid, %s, %s, %s::jsonb, %s::jsonb)
                           ON CONFLICT (turn_id) DO UPDATE
                           SET eds_score = EXCLUDED.eds_score,
                               eds_bucket = EXCLUDED.eds_bucket,
                               raw_llm_output = EXCLUDED.raw_llm_output,
                               eds_components = EXCLUDED.eds_components""",
                        (eval_id, actual_turn_id, org_id, course_id, caller.user_id,
                         question_id,
                         eds_question,
                         "high" if eds_question >= 0.7 else ("medium" if eds_question >= 0.3 else "low"),
                         _json.dumps(eval_data),
                         _json.dumps(eds_components_data)),
                    )

                # ── Upsert question_eds_aggregate ────────────────────────
                try:
                    with repo.conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO question_eds_aggregate
                               (session_id, question_id, org_id, node_score, edge_score,
                                r_gate, gen_score_norm, coverage, final_eds, turn_details)
                               VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, %s, %s, %s, %s::jsonb)
                               ON CONFLICT (session_id, question_id) DO UPDATE SET
                                   node_score = EXCLUDED.node_score,
                                   edge_score = EXCLUDED.edge_score,
                                   r_gate = EXCLUDED.r_gate,
                                   gen_score_norm = EXCLUDED.gen_score_norm,
                                   coverage = EXCLUDED.coverage,
                                   final_eds = EXCLUDED.final_eds,
                                   turn_details = EXCLUDED.turn_details,
                                   computed_at = NOW()""",
                            (session_id, question_id, caller.org_id,
                             agg_node_score, agg_edge_score, agg_R, agg_gen,
                             agg_coverage, eds_question,
                             _json.dumps(eds_components_data)),
                        )
                except Exception as agg_err:
                    # Table may not exist yet; log and continue
                    logger.warning("question_eds_aggregate upsert failed: %s", agg_err)
                    repo.conn.rollback()

                repo.conn.commit()

                return {
                    "answered": answered,
                    "adequate": adequate,
                    "feedback": feedback,
                    "probe": probe,
                    "eds_delta": int(eds_question * 10),
                    "eds_question": eds_question,
                    "eds_components": {
                        "node_score": agg_node_score,
                        "edge_score": agg_edge_score,
                        "r_gate": agg_R,
                        "gen_score": agg_gen,
                    },
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── GET /api/sessions/{session_id}/status ─────────────────────────────
    @app.get(R.SESSION_STATUS)
    def session_status(
        session_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("student"),
    ):
        """Return full session state with per-turn scores."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT session_id, assignment_id, student_id, course_id,
                                  status, current_turn_index
                           FROM exam_session WHERE session_id = %s::uuid""",
                        (session_id,),
                    )
                    srow = cur.fetchone()

                if not srow:
                    raise AuthorizationError("session not found")
                if caller.role != Role.PROFESSOR and srow[2] != caller.user_id:
                    raise AuthorizationError("access denied")

                assignment_id = str(srow[1])

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT config FROM assignment
                           WHERE assignment_id = %s::uuid""",
                        (assignment_id,),
                    )
                    arow = cur.fetchone()
                total_questions = 0
                if arow and arow[0]:
                    cfg = arow[0] if isinstance(arow[0], dict) else _json.loads(arow[0])
                    total_questions = cfg.get("max_questions", 0)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT st.turn_index, st.student_answer IS NOT NULL as answered,
                                  COALESCE(e.eds_score, 0) as score
                           FROM session_turn st
                           LEFT JOIN evaluation e ON e.turn_id = st.turn_id
                           WHERE st.session_id = %s::uuid
                           ORDER BY st.turn_index""",
                        (session_id,),
                    )
                    turn_rows = cur.fetchall()

                turns = [
                    {"index": tr[0], "answered": bool(tr[1]), "score": float(tr[2])}
                    for tr in turn_rows
                ]

                # Normalize EDS to a 0-100 scale for the client
                raw_sum = sum(t["score"] for t in turns)
                if total_questions > 0:
                    eds_score = min(100, round(raw_sum / total_questions * 100))
                else:
                    eds_score = 0

                return {
                    "session_id": session_id,
                    "status": srow[4],
                    "current_turn": int(srow[5]),
                    "total_questions": total_questions,
                    "eds_score": eds_score,
                    "turns": turns,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── POST /api/sessions/{session_id}/complete ────────────────────────────
    @app.post(R.SESSION_COMPLETE)
    def complete_session(
        session_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("student"),
    ):
        """Mark an exam session as completed (student submits)."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """UPDATE exam_session
                           SET status = 'completed', completed_at = NOW()
                           WHERE session_id = %s::uuid
                             AND student_id = %s
                             AND status = 'active'
                           RETURNING session_id""",
                        (session_id, caller.user_id),
                    )
                    row = cur.fetchone()
                repo.conn.commit()

                if not row:
                    raise AuthorizationError("session not found or already completed")
                return {"session_id": session_id, "status": "completed"}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── GET /api/sessions/{session_id}/stream (SSE) ───────────────────────
    # TODO(prod): SSE token passed in URL query param — needs ticket-based auth design
    @app.get(R.SESSION_STREAM)
    def session_stream(
        session_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("student"),
    ):
        """SSE streaming endpoint for real-time exam delivery."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT student_id FROM exam_session
                           WHERE session_id = %s::uuid""",
                        (session_id,),
                    )
                    srow = cur.fetchone()

                if not srow:
                    raise AuthorizationError("session not found")
                if caller.role != Role.PROFESSOR and srow[0] != caller.user_id:
                    raise AuthorizationError("access denied")

                from backend.delivery.sse import SessionEventStream
                stream = SessionEventStream(None, session_id)
                return StreamingResponse(
                    stream.generate(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "X-Accel-Buffering": "no",
                    },
                )
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


# ── M7 Evaluation ────────────────────────────────────────────────────────────

class GradeOverrideRequest(BaseModel):
    """POST body for overriding a grade."""
    new_score: float = Field(ge=0.0, le=1.0)
    reason: str = Field(..., min_length=1, max_length=2000)


class GradeUpsertRequest(BaseModel):
    """Professor edit of a session's grade/comment (0-100 UI scale; both optional)."""
    score: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    comment: Optional[str] = Field(default=None, max_length=5000)


def _register_evaluation(app: FastAPI, deps) -> None:
    """Evaluation and grading endpoints with real DB and Bedrock implementations."""
    import json as _json, uuid as _uuid
    from datetime import datetime, timezone

    # ── GET /api/evaluations/{turn_id} ────────────────────────────────────
    @app.get(R.EVALUATION_GET)
    def get_evaluation(
        turn_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("student"),
    ):
        """Retrieve the evaluation for a specific turn."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT evaluation_id, turn_id, eds_score, eds_bucket,
                                  raw_llm_output, evaluated_at, student_id,
                                  question_id, course_id
                           FROM evaluation WHERE turn_id = %s::uuid""",
                        (turn_id,),
                    )
                    row = cur.fetchone()

                if not row:
                    raise AuthorizationError("evaluation not found for this turn")

                raw = row[4] if isinstance(row[4], dict) else (_json.loads(row[4]) if row[4] else {})
                return {
                    "evaluation_id": str(row[0]),
                    "turn_id": str(row[1]),
                    "eds_score": float(row[2]),
                    "eds_bucket": row[3],
                    "answered": raw.get("answered", True),
                    "adequate": raw.get("adequate", False),
                    "feedback": raw.get("feedback", ""),
                    "probe": raw.get("probe", ""),
                    "eds_delta": raw.get("eds_delta", 0),
                    "evaluated_at": row[5].isoformat() if row[5] else None,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── GET /api/grades/{session_id} ──────────────────────────────────────
    @app.get(R.GRADES_SESSION)
    def get_session_grades(
        session_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("student"),
    ):
        """Get all evaluations for a session as a grade summary."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT session_id, student_id, assignment_id, course_id, status
                           FROM exam_session WHERE session_id = %s::uuid""",
                        (session_id,),
                    )
                    srow = cur.fetchone()

                if not srow:
                    raise AuthorizationError("session not found")
                if caller.role != Role.PROFESSOR and srow[1] != caller.user_id:
                    raise AuthorizationError("access denied")

                # Practice tests are anonymized to the professor: withhold the
                # verbatim student answers (the transcript) so a practice run
                # can't be tied back to what a specific student said (S-E-2.2).
                # The student viewing their own session still sees everything.
                with repo.conn.cursor() as cur:
                    anon = (caller.role == Role.PROFESSOR
                            and _assignment_is_practice(cur, str(srow[2])))

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT grade_id, final_score, component_scores, status,
                                  released_at
                           FROM grade WHERE session_id = %s::uuid""",
                        (session_id,),
                    )
                    grade_row = cur.fetchone()

                if grade_row and grade_row[3] != "released" and caller.role != Role.PROFESSOR:
                    raise AuthorizationError("grades not yet released")

                # Per-turn detail: question text, the student's own answer, the
                # model's feedback, and the quantitative EDS drivers — so a
                # reviewer sees the response and *why* each score sits where it
                # does. Included whether or not a final grade has been released.
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT e.turn_id, e.eds_score, e.eds_bucket,
                                  e.raw_llm_output, e.eds_components,
                                  st.turn_index, st.sub_turn_index,
                                  st.student_answer, q.text
                           FROM evaluation e
                           JOIN session_turn st ON st.turn_id = e.turn_id
                           LEFT JOIN question q ON q.question_id = st.question_id
                           WHERE st.session_id = %s::uuid
                           ORDER BY st.turn_index, st.sub_turn_index""",
                        (session_id,),
                    )
                    eval_rows = cur.fetchall()

                evaluations = []
                total_eds = 0.0
                for er in eval_rows:
                    raw = er[3] if isinstance(er[3], dict) else (_json.loads(er[3]) if er[3] else {})
                    comp = er[4] if isinstance(er[4], dict) else (_json.loads(er[4]) if er[4] else {})
                    score = float(er[1])
                    total_eds += score
                    fb = raw.get("feedback", "")
                    evaluations.append({
                        "turn_id": str(er[0]),  # lets clients drill into GET /api/evaluations/{turn_id}
                        "turn_index": er[5],
                        "sub_turn_index": er[6],
                        "question_text": er[8] or "",
                        "student_answer": "" if anon else (er[7] or ""),
                        "eds_score": score,
                        "eds_bucket": er[2],
                        "answered": raw.get("answered", True),
                        "adequate": raw.get("adequate", False),
                        "feedback": fb,
                        "eds_delta": raw.get("eds_delta", 0),
                        "components": {
                            "node_coverage": comp.get("node_score"),
                            "edge_coverage": comp.get("edge_score"),
                            "recitation_gate": comp.get("r_gate"),
                            "nodes_detected": comp.get("nodes_detected", []),
                            "edges_demonstrated": comp.get("edges_demonstrated", []),
                        },
                        "rationale": _threshold_rationale(score, er[2], comp, fb),
                    })

                if grade_row:
                    comp_all = grade_row[2] if isinstance(grade_row[2], dict) else _json.loads(grade_row[2] or "{}")
                    return {
                        "grade_id": str(grade_row[0]),
                        "session_id": session_id,
                        "final_score": float(grade_row[1]),
                        "overall_comment": comp_all.get("overall_comment", ""),
                        "component_scores": comp_all,
                        "status": grade_row[3],
                        "released_at": grade_row[4].isoformat() if grade_row[4] else None,
                        "total_eds": round(total_eds, 2),
                        "turns_evaluated": len(evaluations),
                        "evaluations": evaluations,
                        "anonymized": anon,
                    }

                # No grade row yet: surface an auto EDS score so the professor's
                # edit form starts from a sensible default they can adjust.
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT config FROM assignment WHERE assignment_id = %s::uuid",
                                (str(srow[2]),))
                    crow = cur.fetchone()
                cfg = crow[0] if crow and isinstance(crow[0], dict) else (_json.loads(crow[0]) if crow and crow[0] else {})
                maxq = cfg.get("max_questions", 10) if isinstance(cfg, dict) else 10
                auto_score = round(min(1.0, total_eds / max(maxq, 1)), 4)
                return {
                    "grade_id": None,
                    "session_id": session_id,
                    "status": "pending",
                    "final_score": auto_score,
                    "overall_comment": "",
                    "total_eds": round(total_eds, 2),
                    "turns_evaluated": len(evaluations),
                    "evaluations": evaluations,
                    "anonymized": anon,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── POST /api/grades/{session_id} — professor edits grade + comment ───
    @app.post(R.GRADES_SESSION)
    def upsert_grade(
        session_id: str,
        req: GradeUpsertRequest,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Create/update a session's grade and overall comment before release.

        Stores the comment in component_scores.overall_comment (no schema change)
        and marks a manually-set score via override_by so release preserves it.
        """
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT student_id, assignment_id, course_id
                           FROM exam_session WHERE session_id = %s::uuid""",
                        (session_id,),
                    )
                    srow = cur.fetchone()
                if not srow:
                    raise AuthorizationError("session not found")
                student_id, assignment_id, course_id = srow[0], str(srow[1]), str(srow[2])

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT grade_id, final_score, component_scores, status, override_by
                           FROM grade WHERE session_id = %s::uuid""",
                        (session_id,),
                    )
                    grow = cur.fetchone()

                now = datetime.now(timezone.utc)
                if grow:
                    comp = grow[2] if isinstance(grow[2], dict) else _json.loads(grow[2] or "{}")
                    if req.comment is not None:
                        comp["overall_comment"] = req.comment
                    if req.score is not None:
                        final = round(req.score / 100.0, 4)
                        override_by = caller.user_id
                    else:
                        final = float(grow[1])
                        override_by = grow[4]
                    with repo.conn.cursor() as cur:
                        cur.execute(
                            """UPDATE grade SET final_score = %s,
                                   component_scores = %s::jsonb, override_by = %s,
                                   updated_at = %s
                               WHERE grade_id = %s::uuid""",
                            (final, _json.dumps(comp), override_by, now, str(grow[0])),
                        )
                    grade_id, status = str(grow[0]), grow[3]
                else:
                    with repo.conn.cursor() as cur:
                        cur.execute(
                            """SELECT COALESCE(SUM(e.eds_score), 0), COUNT(e.evaluation_id)
                               FROM evaluation e JOIN session_turn st ON st.turn_id = e.turn_id
                               WHERE st.session_id = %s::uuid""",
                            (session_id,),
                        )
                        er = cur.fetchone()
                        cur.execute("SELECT config FROM assignment WHERE assignment_id = %s::uuid",
                                    (assignment_id,))
                        crow = cur.fetchone()
                    total_eds = float(er[0]) if er else 0.0
                    cfg = crow[0] if crow and isinstance(crow[0], dict) else (_json.loads(crow[0]) if crow and crow[0] else {})
                    maxq = cfg.get("max_questions", 10) if isinstance(cfg, dict) else 10
                    auto = round(min(1.0, total_eds / max(maxq, 1)), 4)
                    final = round(req.score / 100.0, 4) if req.score is not None else auto
                    override_by = caller.user_id if req.score is not None else None
                    comp = {"total_eds": round(total_eds, 4),
                            "turns_evaluated": int(er[1]) if er else 0,
                            "max_questions": maxq}
                    if req.comment is not None:
                        comp["overall_comment"] = req.comment
                    grade_id = str(_uuid.uuid4())
                    with repo.conn.cursor() as cur:
                        cur.execute(
                            """INSERT INTO grade
                               (grade_id, session_id, student_id, assignment_id,
                                org_id, course_id, final_score, component_scores,
                                override_by, status)
                               VALUES (%s::uuid, %s::uuid, %s, %s::uuid, %s::uuid,
                                       %s::uuid, %s, %s::jsonb, %s, 'pending')""",
                            (grade_id, session_id, student_id, assignment_id,
                             caller.org_id, course_id, final, _json.dumps(comp),
                             override_by),
                        )
                    status = "pending"

                repo.conn.commit()
                return {
                    "grade_id": grade_id,
                    "session_id": session_id,
                    "final_score": final,
                    "overall_comment": comp.get("overall_comment", ""),
                    "status": status,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── POST /api/assignments/{assignment_id}/grades/release ──────────────
    @app.post(R.GRADES_RELEASE)
    def release_grades(
        assignment_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Release grades for all completed sessions in an assignment."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT assignment_id, course_id, config
                           FROM assignment WHERE assignment_id = %s::uuid""",
                        (assignment_id,),
                    )
                    arow = cur.fetchone()

                if not arow:
                    raise AuthorizationError("assignment not found")

                course_id = str(arow[1])
                config = arow[2] if isinstance(arow[2], dict) else _json.loads(arow[2] or "{}")
                max_questions = config.get("max_questions", 10)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT session_id, student_id
                           FROM exam_session
                           WHERE assignment_id = %s::uuid AND status = 'completed'""",
                        (assignment_id,),
                    )
                    sessions = cur.fetchall()

                now = datetime.now(timezone.utc)
                released = []

                for sess_row in sessions:
                    sid = str(sess_row[0])
                    student_id = sess_row[1]

                    with repo.conn.cursor() as cur:
                        cur.execute(
                            """SELECT grade_id, status, final_score, component_scores,
                                      override_by
                               FROM grade WHERE session_id = %s::uuid""",
                            (sid,),
                        )
                        existing_grade = cur.fetchone()

                    if existing_grade and existing_grade[1] == "released":
                        released.append({"session_id": sid, "student_id": student_id,
                                         "grade_id": str(existing_grade[0]),
                                         "status": "already_released"})
                        continue

                    with repo.conn.cursor() as cur:
                        cur.execute(
                            """SELECT COALESCE(SUM(e.eds_score), 0), COUNT(e.evaluation_id)
                               FROM evaluation e
                               JOIN session_turn st ON st.turn_id = e.turn_id
                               WHERE st.session_id = %s::uuid""",
                            (sid,),
                        )
                        score_row = cur.fetchone()

                    total_eds = float(score_row[0]) if score_row else 0.0
                    turns_evaluated = int(score_row[1]) if score_row else 0

                    final_score = round(
                        min(1.0, total_eds / max(max_questions, 1)), 4
                    )

                    component_scores = {
                        "total_eds": round(total_eds, 4),
                        "turns_evaluated": turns_evaluated,
                        "max_questions": max_questions,
                    }

                    if existing_grade:
                        # Preserve the professor's manual grade + overall comment:
                        # a set override_by means the score was hand-adjusted.
                        prev_comp = existing_grade[3] if isinstance(existing_grade[3], dict) else _json.loads(existing_grade[3] or "{}")
                        overall_comment = prev_comp.get("overall_comment")
                        if overall_comment is not None:
                            component_scores["overall_comment"] = overall_comment
                        if existing_grade[4] is not None:  # override_by => manual score
                            final_score = float(existing_grade[2])
                        with repo.conn.cursor() as cur:
                            cur.execute(
                                """UPDATE grade SET final_score = %s,
                                       component_scores = %s::jsonb,
                                       status = 'released', released_at = %s, updated_at = %s
                                   WHERE grade_id = %s::uuid""",
                                (final_score, _json.dumps(component_scores),
                                 now, now, str(existing_grade[0])),
                            )
                        grade_id = str(existing_grade[0])
                    else:
                        grade_id = str(_uuid.uuid4())
                        with repo.conn.cursor() as cur:
                            cur.execute(
                                """INSERT INTO grade
                                   (grade_id, session_id, student_id, assignment_id,
                                    org_id, course_id, final_score, component_scores,
                                    status, released_at)
                                   VALUES (%s::uuid, %s::uuid, %s, %s::uuid,
                                           %s::uuid, %s::uuid, %s, %s::jsonb,
                                           'released', %s)""",
                                (grade_id, sid, student_id, assignment_id,
                                 caller.org_id, course_id, final_score,
                                 _json.dumps(component_scores), now),
                            )

                    released.append({
                        "session_id": sid,
                        "student_id": student_id,
                        "grade_id": grade_id,
                        "final_score": final_score,
                        "status": "released",
                    })

                repo.conn.commit()

                return {
                    "assignment_id": assignment_id,
                    "grades_released": len(released),
                    "grades": released,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── POST /api/grades/{grade_id}/override ──────────────────────────────
    @app.post(R.GRADE_OVERRIDE)
    def override_grade(
        grade_id: str,
        req: GradeOverrideRequest,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Override a grade with a professor's manual score."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT grade_id, final_score, status
                           FROM grade WHERE grade_id = %s::uuid""",
                        (grade_id,),
                    )
                    row = cur.fetchone()

                if not row:
                    raise AuthorizationError("grade not found")

                now = datetime.now(timezone.utc)
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """UPDATE grade
                           SET final_score = %s, override_by = %s,
                               override_reason = %s, updated_at = %s
                           WHERE grade_id = %s::uuid""",
                        (req.new_score, caller.user_id, req.reason, now, grade_id),
                    )
                repo.conn.commit()

                return {
                    "grade_id": grade_id,
                    "new_score": req.new_score,
                    "override_by": caller.user_id,
                    "override_reason": req.reason,
                    "updated_at": now.isoformat(),
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


# ── Delete endpoints ──────────────────────────────────────────────────────────

def _register_delete_endpoints(app: FastAPI, deps) -> None:
    """DELETE endpoints for materials and assignments (professor only)."""

    @app.get(R.COURSE_GET)
    def get_course(
        course_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Course detail with a live student count from distinct exam sessions."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)

                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT course_id, course_name, title, created_at
                           FROM course
                           WHERE course_id = %s::uuid AND org_id = %s::uuid""",
                        (course_id, caller.org_id),
                    )
                    row = cur.fetchone()
                if not row:
                    raise AuthorizationError("course not found")

                # "Students" = enrolled roster (public.enrollment, keyed by email),
                # not exam-takers — so it moves the moment a professor adds students.
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.enrollment')")
                    if cur.fetchone()[0] is not None:
                        cur.execute(
                            """SELECT COUNT(*) FROM enrollment
                               WHERE course_id = %s::uuid AND org_id = %s::uuid""",
                            (course_id, caller.org_id),
                        )
                        student_count = cur.fetchone()[0]
                    else:
                        student_count = 0

                # code/description/join_code have no columns yet; course_name doubles
                # as the code so the UI header renders without inventing data.
                return {
                    "id": str(row[0]),
                    "course_id": str(row[0]),
                    "name": row[1],
                    "course_name": row[1],
                    "code": row[1],
                    "description": row[2] or "",
                    "student_count": student_count,
                    "join_code": "",
                    "created_at": row[3].isoformat() if row[3] else None,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.delete(R.MATERIAL_DELETE)
    def delete_material(
        material_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Delete a material and all related data (chunks, versions)."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)

                # The professor dashboard surfaces uploads by material_version_id, so
                # accept either identifier and resolve to the owning material.
                # Bind to a new name: assigning material_id here would make the
                # handler parameter local to this closure and unreadable above.
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT material_id, course_id FROM material
                           WHERE material_id = %s::uuid AND org_id = %s::uuid
                           UNION
                           SELECT material_id, course_id FROM material_version
                           WHERE material_version_id = %s::uuid AND org_id = %s::uuid
                           LIMIT 1""",
                        (material_id, caller.org_id, material_id, caller.org_id),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise AuthorizationError("material not found")
                    target_id = str(row[0])
                    course_id = str(row[1])

                # Delete in order: chunks -> material_versions -> material
                with repo.conn.cursor() as cur:
                    # Delete chunks associated with this material's versions
                    cur.execute(
                        """DELETE FROM chunk
                           WHERE material_version_id IN (
                               SELECT material_version_id FROM material_version
                               WHERE material_id = %s::uuid
                           )""",
                        (target_id,),
                    )
                    # Delete material versions
                    cur.execute(
                        "DELETE FROM material_version WHERE material_id = %s::uuid",
                        (target_id,),
                    )
                    # Delete the material itself
                    cur.execute(
                        "DELETE FROM material WHERE material_id = %s::uuid",
                        (target_id,),
                    )
                repo.conn.commit()

                # Removing a document must drop its concepts from the graph.
                # A pure recompute (no LLM) rebuilds the course snapshot from the
                # REMAINING documents and purges the deleted doc's now-orphaned
                # concept rows — instant and reliable, no stale window. Only if
                # that fails do we fall back to the async LLM rebuild (marking the
                # graph stale so the UI keeps polling).
                graph_rebuild = "recomputed"
                try:
                    with repo.conn.cursor() as cur:
                        snapshot_course_graph(cur, caller.org_id, course_id)
                    repo.conn.commit()
                except Exception as exc:  # noqa: BLE001
                    repo.conn.rollback()
                    logger.warning("Post-delete graph recompute failed for %s: %s — falling back to async rebuild",
                                   course_id[:8], exc)
                    with repo.conn.cursor() as cur:
                        cur.execute("UPDATE graph_version SET is_stale = true "
                                    "WHERE org_id = %s AND course_id = %s AND is_active = true",
                                    (caller.org_id, course_id))
                    repo.conn.commit()
                    _rebuild_graph_async(d["settings"], caller.org_id, course_id)
                    graph_rebuild = "started"

                return {"deleted": True, "material_id": target_id,
                        "graph_rebuild": graph_rebuild}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.delete(R.ASSIGNMENT_DELETE)
    def delete_assignment(
        assignment_id: str,
        x_org_name: str = Header(...),
        x_user_id: str = Header("operator"),
        x_role: str = Header("professor"),
    ):
        """Delete an assignment and all related data (sessions, turns, evaluations, grades)."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = api.caller_for_org(x_user_id, x_role, x_org_name)
                if caller.role != Role.PROFESSOR:
                    raise AuthorizationError("professor role required")
                repo.set_tenant(caller.org_id)

                # Verify assignment exists and belongs to this org
                with repo.conn.cursor() as cur:
                    cur.execute(
                        "SELECT assignment_id, question_set_id FROM assignment WHERE assignment_id = %s::uuid AND org_id = %s::uuid",
                        (assignment_id, caller.org_id),
                    )
                    row = cur.fetchone()
                    if not row:
                        raise AuthorizationError("assignment not found")
                    question_set_id = str(row[1])

                with repo.conn.cursor() as cur:
                    # Delete evaluations for turns in sessions of this assignment
                    cur.execute(
                        """DELETE FROM evaluation
                           WHERE turn_id IN (
                               SELECT turn_id FROM session_turn
                               WHERE session_id IN (
                                   SELECT session_id FROM exam_session
                                   WHERE assignment_id = %s::uuid
                               )
                           )""",
                        (assignment_id,),
                    )
                    # Delete question_eds_aggregate rows for sessions of this assignment
                    try:
                        cur.execute(
                            """DELETE FROM question_eds_aggregate
                               WHERE session_id IN (
                                   SELECT session_id FROM exam_session
                                   WHERE assignment_id = %s::uuid
                               )""",
                            (assignment_id,),
                        )
                    except Exception:
                        repo.conn.rollback()
                        # Table may not exist; continue

                with repo.conn.cursor() as cur:
                    # Delete grades for this assignment
                    cur.execute(
                        "DELETE FROM grade WHERE assignment_id = %s::uuid",
                        (assignment_id,),
                    )
                    # Delete session turns
                    cur.execute(
                        """DELETE FROM session_turn
                           WHERE session_id IN (
                               SELECT session_id FROM exam_session
                               WHERE assignment_id = %s::uuid
                           )""",
                        (assignment_id,),
                    )
                    # Delete exam sessions
                    cur.execute(
                        "DELETE FROM exam_session WHERE assignment_id = %s::uuid",
                        (assignment_id,),
                    )
                    # Delete question_set_membership
                    cur.execute(
                        "DELETE FROM question_set_membership WHERE question_set_id = %s::uuid",
                        (question_set_id,),
                    )
                    # Assignment must go before its question_set: assignment.question_set_id
                    # is a FK, so removing the set first violates the constraint.
                    cur.execute(
                        "DELETE FROM assignment WHERE assignment_id = %s::uuid",
                        (assignment_id,),
                    )
                    # Only drop the set once no assignment references it
                    cur.execute(
                        """DELETE FROM question_set
                           WHERE question_set_id = %s::uuid
                             AND NOT EXISTS (
                                 SELECT 1 FROM assignment
                                 WHERE question_set_id = %s::uuid
                             )""",
                        (question_set_id, question_set_id),
                    )
                repo.conn.commit()

                return {"deleted": True, "assignment_id": assignment_id}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _guard(deps, fn):
    """Run a handler; map errors to appropriate HTTP status codes.

    Error mapping:
    - AuthorizationError -> 403
    - ValueError, KeyError -> 400 (bad client input)
    - Everything else -> 500 (unexpected server error, logged)
    """
    try:
        return fn()
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=f"Bad request: {str(exc)[:120]}")
    except HTTPException:
        # Re-raise FastAPI exceptions (e.g. 503 from health check) unchanged
        raise
    except Exception:
        logger.exception("Unhandled error in request handler")
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred. Please try again or contact support.",
        )



# Shown when the evaluation LLM is unreachable and grading falls back to keyword
# matching. Deliberately says the SYSTEM struggled, not the student: this path fires
# on a bad API key, a 429, a timeout or unparseable JSON, none of which say anything
# about the answer. The old "Good attempt." read as a verdict on a response nothing
# had actually assessed.
_FALLBACK_FEEDBACK = "I had difficulty processing your response."
_FALLBACK_PROBE = "Can you explain your answer further?"


def _heuristic_eval(answer_text: str) -> tuple:
    """Fallback answer evaluation when Bedrock is unavailable."""
    answer_lower = answer_text.strip().lower()
    if not answer_lower or answer_lower in (
        "i don't know", "idk", "not sure", "no idea", "skip"
    ):
        return False, False, "", "Can you try to think about what key concept relates to this?"
    causal_markers = [
        "because", "therefore", "causes", "leads to",
        "results in", "due to", "since", "so that",
    ]
    has_causal = any(m in answer_lower for m in causal_markers)
    adequate = has_causal and len(answer_lower) > 40
    feedback = _FALLBACK_FEEDBACK if not adequate else ""
    probe = _FALLBACK_PROBE if not adequate else ""
    return True, adequate, feedback, probe


# ── Admin: agent-cohort exam simulations ──────────────────────────────────────
class SimulationRequest(BaseModel):
    """POST body to launch an agent-cohort simulation over an assignment."""
    assignment_id: str
    num_agents: int = Field(4, ge=1, le=10)
    curve: str = "linear"                 # "linear" | "bell"
    max_followups: int = Field(2, ge=0, le=4)


def _ensure_agent_sim_table(repo) -> None:
    """Create the agent_simulation table on first use (non-owner-safe)."""
    with repo.conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.agent_simulation')")
        if cur.fetchone()[0] is not None:
            return
        cur.execute(
            """CREATE TABLE IF NOT EXISTS agent_simulation (
                   simulation_id UUID PRIMARY KEY,
                   org_id UUID NOT NULL,
                   assignment_id UUID NOT NULL,
                   course_id UUID,
                   num_agents INT NOT NULL,
                   curve TEXT NOT NULL,
                   status TEXT NOT NULL,
                   progress JSONB,
                   report JSONB,
                   error TEXT,
                   created_by TEXT,
                   created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    repo.conn.commit()


def _load_sim_questions(cur, assignment_id: str):
    """(course_id, [questions]) for an assignment — text + concept ids + expected_path."""
    import json as _json
    cur.execute("SELECT question_set_id, course_id FROM assignment WHERE assignment_id = %s::uuid",
                (assignment_id,))
    row = cur.fetchone()
    if not row:
        return None, []
    qsid, course_id = str(row[0]), str(row[1])
    cur.execute(
        """SELECT q.question_id, q.text, q.concept_ids, q.expected_path
           FROM question_set_membership qsm
           JOIN question q ON q.question_id = qsm.question_id
           WHERE qsm.question_set_id = %s::uuid ORDER BY qsm.position""",
        (qsid,))
    questions = []
    for r in cur.fetchall():
        cids = r[2] if isinstance(r[2], list) else []
        ep = r[3] if isinstance(r[3], dict) else (_json.loads(r[3]) if r[3] else {})
        questions.append({"question_id": str(r[0]), "text": r[1] or "", "concept_ids": cids,
                          "topic": cids[0] if cids else "general", "expected_path": ep or {}})
    return course_id, questions


def _persist_sim_turns(conn, org_id, simulation_id, assignment_id, questions, report):
    """Write one row per (agent × question × turn) to agent_simulation_turn — the
    normalized, queryable transcript. Idempotent per simulation: clears any prior
    rows for this simulation_id first. No-op if the table isn't present."""
    import json as _json
    from psycopg2.extras import execute_values
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.agent_simulation_turn')")
        if cur.fetchone()[0] is None:
            return
        cur.execute("DELETE FROM agent_simulation_turn WHERE simulation_id = %s::uuid",
                    (simulation_id,))
        qid_by_index = {i: q.get("question_id") for i, q in enumerate(questions)}
        rows = []
        for agent in report.get("agents", []):
            a_idx, a_skill = agent.get("index"), agent.get("skill")
            for qi, pq in enumerate(agent.get("per_q", [])):
                rubric = pq.get("rubric") or {}
                breakdown = pq.get("breakdown") or {}
                q_score = pq.get("score")
                transcript = pq.get("transcript") or []
                for t in transcript:
                    rows.append((
                        simulation_id, org_id, assignment_id, a_idx, a_skill,
                        qid_by_index.get(qi), qi + 1, pq.get("question"), pq.get("topic"),
                        t.get("round"), bool(t.get("is_probe")), t.get("prompt"),
                        t.get("answer"), t.get("probe"), t.get("answered"), t.get("adequate"),
                        t.get("recitation_score"),
                        _json.dumps(t.get("nodes_demonstrated") or []),
                        _json.dumps(t.get("edges_demonstrated") or []),
                        _json.dumps(t.get("novel_extensions") or []),
                        q_score, _json.dumps(rubric), _json.dumps(breakdown),
                    ))
        if rows:
            execute_values(cur,
                """INSERT INTO agent_simulation_turn
                   (simulation_id, org_id, assignment_id, agent_index, agent_skill,
                    question_id, question_index, question_text, topic, round, is_probe,
                    prompt, answer, probe, answered, adequate, recitation_score,
                    nodes_demonstrated, edges_demonstrated, novel_extensions,
                    question_score, rubric, breakdown)
                   VALUES %s""",
                rows,
                template="(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s::uuid,%s,%s,%s,%s,%s,"
                         "%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s::jsonb)")
    conn.commit()
    logger.info("sim %s: persisted %d transcript rows", simulation_id[:8], len(rows))


def _run_simulation_bg(settings, org_id, simulation_id, assignment_id,
                       num_agents, curve, max_followups):
    """Background: ensure expected paths, run the cohort, persist the report."""
    import json as _json
    from backend.app import agent_sim
    conn = None
    try:
        conn = factory.db_connection(settings)
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id', %s, false)", (org_id,))
            conn.commit()
            _course_id, questions = _load_sim_questions(cur, assignment_id)

        # Each question needs an expected reasoning path for EDS scoring — generate
        # (and persist) any that are missing, once, before the cohort runs.
        for q in questions:
            if not q["expected_path"].get("nodes"):
                try:
                    ep = _generate_expected_path(settings, q["text"], q["concept_ids"]) or {}
                    q["expected_path"] = ep
                    if ep.get("nodes"):
                        with conn.cursor() as cur:
                            cur.execute("UPDATE question SET expected_path = %s::jsonb WHERE question_id = %s::uuid",
                                        (_json.dumps(ep), q["question_id"]))
                        conn.commit()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("sim %s: expected_path gen failed for q %s: %s",
                                   simulation_id[:8], q["question_id"][:8], exc)

        def progress(done, total):
            try:
                with conn.cursor() as cur:
                    cur.execute("UPDATE agent_simulation SET progress = %s::jsonb WHERE simulation_id = %s::uuid",
                                (_json.dumps({"agents_done": done, "agents_total": total}), simulation_id))
                conn.commit()
            except Exception:  # noqa: BLE001
                pass

        report = agent_sim.run_simulation(
            questions, num_agents=num_agents, curve=curve,
            answer_fn=agent_sim.default_answer_fn(settings),
            eval_fn=agent_sim.default_eval_fn(settings),
            max_followups=max_followups, progress_fn=progress)

        with conn.cursor() as cur:
            cur.execute("UPDATE agent_simulation SET status = 'completed', report = %s::jsonb WHERE simulation_id = %s::uuid",
                        (_json.dumps(report), simulation_id))
        conn.commit()
        # Persist the normalized per-turn transcript (queryable analysis surface).
        try:
            _persist_sim_turns(conn, org_id, simulation_id, assignment_id, questions, report)
        except Exception as exc:  # noqa: BLE001 - analysis rows are best-effort
            conn.rollback()
            logger.warning("sim %s: persisting turn rows failed: %s", simulation_id[:8], exc)
        logger.info("sim %s completed: %d agents, mean=%s", simulation_id[:8],
                    report["num_agents"], report["aggregate"]["mean"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("sim %s failed: %s", simulation_id[:8], exc, exc_info=True)
        if conn is not None:
            try:
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute("UPDATE agent_simulation SET status = 'failed', error = %s WHERE simulation_id = %s::uuid",
                                (str(exc)[:500], simulation_id))
                conn.commit()
            except Exception:  # noqa: BLE001
                pass
    finally:
        if conn is not None:
            conn.close()


def _active_app_jobs(repo, org_id: str) -> list:
    """Active app background work for this org: in-flight ingest jobs (async_job)
    and running agent simulations, newest first, with human-readable text."""
    import json as _json
    jobs: list = []
    with repo.conn.cursor() as cur:
        # Ingest pipeline jobs still in flight.
        cur.execute(
            """SELECT j.job_id, j.type, j.status, j.step_name, j.progress_pct,
                      j.updated_at, c.course_name,
                      (SELECT mv.file_name FROM material_version mv
                       WHERE mv.ingest_job_id = j.job_id ORDER BY mv.created_at DESC LIMIT 1)
               FROM async_job j LEFT JOIN course c ON c.course_id = j.course_id
               WHERE j.org_id = %s::uuid AND j.status NOT IN ('succeeded','failed')
               ORDER BY j.updated_at DESC LIMIT 25""",
            (org_id,))
        for r in cur.fetchall():
            fname = r[7]
            jobs.append({
                "kind": r[1] or "ingest", "status": r[2], "progress_pct": r[4] or 0,
                "detail": "%s%s%s" % (r[3] or r[2],
                                      " · " + r[6] if r[6] else "",
                                      " · " + fname if fname else ""),
                "updated_at": r[5].isoformat() if r[5] else None,
            })
        # Running agent simulations.
        cur.execute("SELECT to_regclass('public.agent_simulation')")
        if cur.fetchone()[0] is not None:
            cur.execute(
                """SELECT simulation_id, num_agents, curve, progress, created_at
                   FROM agent_simulation
                   WHERE org_id = %s::uuid AND status = 'running'
                   ORDER BY created_at DESC LIMIT 25""",
                (org_id,))
            for r in cur.fetchall():
                prog = r[3] if isinstance(r[3], dict) else (_json.loads(r[3]) if r[3] else {})
                done, total = prog.get("agents_done", 0), prog.get("agents_total", r[1])
                jobs.append({
                    "kind": "simulation", "status": "running",
                    "progress_pct": round(100 * done / total) if total else 0,
                    "detail": "agent cohort · %s curve · %s/%s agents" % (r[2], done, total),
                    "updated_at": r[4].isoformat() if r[4] else None,
                })
    return jobs


def _ecs_deployment_status(settings) -> dict:
    """Current ECS rollout + running-image-vs-ECR-latest check. Returns an
    {available: false, reason} shape if the task role lacks ECS/ECR describe
    (so the panel degrades instead of erroring)."""
    try:
        import boto3
        sess = boto3.Session(region_name=settings.region)
        ecs = sess.client("ecs")
        svc = ecs.describe_services(cluster=settings.cluster_name,
                                    services=[settings.service_name])["services"][0]
        primary = next((dep for dep in svc.get("deployments", []) if dep["status"] == "PRIMARY"), {})
        out = {
            "available": True,
            "service": settings.service_name,
            "desired": svc.get("desiredCount"), "running": svc.get("runningCount"),
            "pending": svc.get("pendingCount"),
            "rollout_state": primary.get("rolloutState"),
            "rollout_started": primary.get("createdAt").isoformat() if primary.get("createdAt") else None,
            "deployments": len(svc.get("deployments", [])),
        }
        # Running task image digest vs ECR :latest → is the newest image live?
        try:
            tasks = ecs.list_tasks(cluster=settings.cluster_name,
                                   serviceName=settings.service_name).get("taskArns", [])
            if tasks:
                td = ecs.describe_tasks(cluster=settings.cluster_name, tasks=tasks[:1])["tasks"][0]
                out["running_image_digest"] = td["containers"][0].get("imageDigest")
            latest = sess.client("ecr").describe_images(
                repositoryName=settings.ecr_repo, imageIds=[{"imageTag": "latest"}]
            )["imageDetails"][0]
            out["ecr_latest_digest"] = latest.get("imageDigest")
            out["latest_pushed_at"] = latest.get("imagePushedAt").isoformat() if latest.get("imagePushedAt") else None
            out["on_latest"] = (out.get("running_image_digest") == out.get("ecr_latest_digest")
                                and out.get("running_image_digest") is not None)
        except Exception as exc:  # noqa: BLE001 - image detail is best-effort
            out["image_check_error"] = str(exc)[:160]
        return out
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": str(exc)[:200]}


def _register_admin_simulations(app: FastAPI, deps) -> None:
    """platform_admin: launch agent-cohort exam simulations and read their reports."""

    def _admin_caller(api, x_user_id, x_role, x_org_name):
        caller = api.caller_for_org(x_user_id, x_role, x_org_name)
        if caller.role != Role.PLATFORM_ADMIN:
            raise AuthorizationError("platform_admin role required")
        return caller

    @app.get(R.ADMIN_ACTIVE_TASKS)
    def admin_active_tasks(x_org_name: str = Header(...),
                           x_user_id: str = Header("operator"),
                           x_role: str = Header("platform_admin")):
        """Active app background jobs + current ECS deployment status, for the
        admin 'Active Deployment Tasks' panel."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                jobs = _active_app_jobs(repo, caller.org_id)
                deployment = _ecs_deployment_status(d["settings"])
                return {"jobs": jobs, "deployment": deployment}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_ASSIGNMENTS)
    def list_admin_assignments(x_org_name: str = Header(...),
                               x_user_id: str = Header("operator"),
                               x_role: str = Header("platform_admin")):
        """Every assignment in the admin's org, for the simulation picker."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)   # RLS scopes both tables to this org
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT a.assignment_id, a.title, a.status, c.course_name,
                                  (SELECT count(*) FROM question_set_membership m
                                   WHERE m.question_set_id = a.question_set_id) AS qcount
                           FROM assignment a JOIN course c ON c.course_id = a.course_id
                           ORDER BY a.created_at DESC LIMIT 200""")
                    rows = cur.fetchall()
                return {"assignments": [
                    {"id": str(r[0]), "title": r[1], "status": r[2],
                     "course_name": r[3], "question_count": r[4]} for r in rows]}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.ADMIN_SIMULATIONS)
    def create_simulation(req: SimulationRequest, x_org_name: str = Header(...),
                          x_user_id: str = Header("operator"),
                          x_role: str = Header("platform_admin")):
        import json as _json, uuid as _uuid

        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                _ensure_agent_sim_table(repo)
                with repo.conn.cursor() as cur:
                    course_id, questions = _load_sim_questions(cur, req.assignment_id)
                if course_id is None:
                    raise AuthorizationError("assignment not found")
                if not questions:
                    return {"status": "error", "message": "This assignment has no questions to take."}

                sim_id = str(_uuid.uuid4())
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO agent_simulation
                           (simulation_id, org_id, assignment_id, course_id, num_agents,
                            curve, status, progress, created_by)
                           VALUES (%s::uuid, %s::uuid, %s::uuid, %s::uuid, %s, %s, 'running', %s::jsonb, %s)""",
                        (sim_id, caller.org_id, req.assignment_id, course_id, req.num_agents,
                         req.curve, _json.dumps({"agents_done": 0, "agents_total": req.num_agents}),
                         caller.user_id))
                repo.conn.commit()

                threading.Thread(
                    target=_run_simulation_bg,
                    args=(d["settings"], caller.org_id, sim_id, req.assignment_id,
                          req.num_agents, req.curve, req.max_followups),
                    daemon=True).start()
                return {"simulation_id": sim_id, "status": "running",
                        "num_agents": req.num_agents, "questions": len(questions)}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_SIMULATION)
    def get_simulation(simulation_id: str, x_org_name: str = Header(...),
                       x_user_id: str = Header("operator"),
                       x_role: str = Header("platform_admin")):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                _ensure_agent_sim_table(repo)
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT assignment_id, num_agents, curve, status, progress, report,
                                  error, created_at
                           FROM agent_simulation
                           WHERE simulation_id = %s::uuid AND org_id = %s::uuid""",
                        (simulation_id, caller.org_id))
                    r = cur.fetchone()
                if not r:
                    raise AuthorizationError("simulation not found")
                return {
                    "simulation_id": simulation_id,
                    "assignment_id": str(r[0]),
                    "num_agents": r[1], "curve": r[2], "status": r[3],
                    "progress": r[4], "report": r[5], "error": r[6],
                    "created_at": r[7].isoformat() if r[7] else None,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_SIMULATIONS)
    def list_simulations(x_org_name: str = Header(...),
                         x_user_id: str = Header("operator"),
                         x_role: str = Header("platform_admin")):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                _ensure_agent_sim_table(repo)
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT simulation_id, assignment_id, num_agents, curve, status,
                                  report, created_at
                           FROM agent_simulation WHERE org_id = %s::uuid
                           ORDER BY created_at DESC LIMIT 50""",
                        (caller.org_id,))
                    rows = cur.fetchall()
                sims = []
                for r in rows:
                    report = r[5] if isinstance(r[5], dict) else None
                    mean = (report or {}).get("aggregate", {}).get("mean") if report else None
                    sims.append({
                        "simulation_id": str(r[0]), "assignment_id": str(r[1]),
                        "num_agents": r[2], "curve": r[3], "status": r[4],
                        "mean_score": mean,
                        "created_at": r[6].isoformat() if r[6] else None,
                    })
                return {"simulations": sims}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_SIMULATION_TURNS)
    def list_simulation_turns(simulation_id: str, x_org_name: str = Header(...),
                              x_user_id: str = Header("operator"),
                              x_role: str = Header("platform_admin")):
        """Normalized per-turn transcript for one simulation — the queryable
        analysis surface (persisted so this LLM detail is never re-paid for)."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.agent_simulation_turn')")
                    if cur.fetchone()[0] is None:
                        return {"turns": []}
                    cur.execute(
                        """SELECT agent_index, agent_skill, question_index, question_text,
                                  topic, round, is_probe, prompt, answer, probe, answered,
                                  adequate, recitation_score, nodes_demonstrated,
                                  edges_demonstrated, novel_extensions, question_score
                           FROM agent_simulation_turn
                           WHERE simulation_id = %s::uuid AND org_id = %s::uuid
                           ORDER BY agent_index, question_index, round""",
                        (simulation_id, caller.org_id))
                    cols = [c[0] for c in cur.description]
                    turns = [dict(zip(cols, row)) for row in cur.fetchall()]
                return {"turns": turns}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_PROFESSORS)
    def list_professors(x_org_name: str = Header(...),
                        x_user_id: str = Header("operator"),
                        x_role: str = Header("platform_admin")):
        """Every professor in the org mapped to their courses and each course's
        enrolled students — a read-only view for debugging/analysis. Surfaces
        roster mismatches (authoritative auth.enrollment vs the public.enrollment
        mirror the professor UI reads) and courses with no owner."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    # Professors in this org.
                    cur.execute(
                        """SELECT email, status, created_at FROM auth.app_user
                           WHERE role = 'professor' ORDER BY lower(email)""")
                    profs = [{"email": r[0], "status": r[1],
                              "created_at": r[2].isoformat() if r[2] else None}
                             for r in cur.fetchall()]
                    # Student counts in the org (for a small header stat).
                    cur.execute("SELECT count(*) FROM auth.app_user WHERE role = 'student'")
                    student_total_org = cur.fetchone()[0]
                    # All courses in the org (created_by = owning professor email).
                    cur.execute(
                        """SELECT course_id, course_name, created_by, created_at
                           FROM course ORDER BY lower(course_name)""")
                    courses = [{"course_id": str(r[0]), "course_name": r[1],
                                "created_by": r[2], "created_at": r[3].isoformat() if r[3] else None}
                               for r in cur.fetchall()]
                    # Authoritative rosters: auth.enrollment → auth.app_user (student email).
                    cur.execute(
                        """SELECT ae.course_id, su.email
                           FROM auth.enrollment ae
                           JOIN auth.app_user su ON su.id = ae.app_user_id
                           ORDER BY lower(su.email)""")
                    students_by_course: Dict[str, list] = {}
                    for cid, email in cur.fetchall():
                        students_by_course.setdefault(str(cid), []).append(email)
                    # Public mirror counts (what the professor UI reads) — flag divergence.
                    mirror_by_course: Dict[str, int] = {}
                    cur.execute("SELECT to_regclass('public.enrollment')")
                    if cur.fetchone()[0] is not None:
                        cur.execute(
                            "SELECT course_id, count(*) FROM enrollment WHERE org_id = %s::uuid GROUP BY course_id",
                            (caller.org_id,))
                        mirror_by_course = {str(r[0]): r[1] for r in cur.fetchall()}

                def _course_obj(c):
                    cid = c["course_id"]
                    students = students_by_course.get(cid, [])
                    roster = mirror_by_course.get(cid, 0)
                    return {**c, "student_count": len(students), "roster_count": roster,
                            "mismatch": len(students) != roster, "students": students}

                by_owner: Dict[str, list] = {}
                unassigned = []
                prof_emails = {p["email"].lower() for p in profs}
                for c in courses:
                    obj = _course_obj(c)
                    owner = (c.get("created_by") or "").lower()
                    if owner and owner in prof_emails:
                        by_owner.setdefault(owner, []).append(obj)
                    else:
                        unassigned.append(obj)

                out_profs = []
                for p in profs:
                    pcourses = by_owner.get(p["email"].lower(), [])
                    out_profs.append({
                        **p,
                        "course_count": len(pcourses),
                        "student_total": sum(c["student_count"] for c in pcourses),
                        "courses": pcourses,
                    })
                return {"professors": out_profs, "unassigned_courses": unassigned,
                        "org_student_total": student_total_org}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.ADMIN_PERF_PROBE)
    def create_perf_probe(req: PerfProbeRequest, x_org_name: str = Header(...),
                          x_user_id: str = Header("operator"),
                          x_role: str = Header("platform_admin")):
        """Start an end-to-end answer-flow latency probe (eval LLM + TTS per run,
        plus a one-off expected_path generation). Runs in the background; poll the
        GET endpoint for results."""
        import uuid as _uuid, json as _json
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)  # platform_admin gate
                repo.set_tenant(caller.org_id)
                probe_id = str(_uuid.uuid4())
                provided = req.params if req.params is not None else {"runs": req.runs}
                eff = _perf_effective_params(d["settings"], provided)
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.perf_probe')")
                    if cur.fetchone()[0] is None:
                        return {"status": "error",
                                "message": "perf_probe table not present — run the migration."}
                    cur.execute(
                        """INSERT INTO perf_probe (probe_id, org_id, runs, status, created_by, title, params)
                           VALUES (%s::uuid, %s::uuid, %s, 'running', %s, %s, %s::jsonb)""",
                        (probe_id, caller.org_id, eff["runs"], caller.user_id, req.title, _json.dumps(eff)))
                repo.conn.commit()
                threading.Thread(target=_run_perf_probe_bg,
                                 args=(d["settings"], caller.org_id, probe_id, eff),
                                 daemon=True).start()
                return {"probe_id": probe_id, "status": "running", "runs": eff["runs"],
                        "title": req.title, "params": eff}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_PERF_PROBE_GET)
    def get_perf_probe(probe_id: str, x_org_name: str = Header(...),
                       x_user_id: str = Header("operator"),
                       x_role: str = Header("platform_admin")):
        """Poll one performance probe's status + full result (incl. traces)."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.perf_probe')")
                    if cur.fetchone()[0] is None:
                        return {"probe_id": probe_id, "status": "not_found"}
                    cur.execute(
                        """SELECT status, result, error, title, params FROM perf_probe
                           WHERE probe_id = %s::uuid AND org_id = %s::uuid""",
                        (probe_id, caller.org_id))
                    row = cur.fetchone()
                if not row:
                    return {"probe_id": probe_id, "status": "not_found"}
                return {"probe_id": probe_id, "status": row[0], "result": row[1], "error": row[2],
                        "title": row[3], "params": row[4]}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_PERF_PROBES)
    def list_perf_probes(x_org_name: str = Header(...),
                         x_user_id: str = Header("operator"),
                         x_role: str = Header("platform_admin")):
        """History of saved performance probes for the org (newest first)."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.perf_probe')")
                    if cur.fetchone()[0] is None:
                        return {"probes": []}
                    cur.execute(
                        """SELECT probe_id, title, runs, status, provider, eval_model, tts_model,
                                  eval_p50_ms, tts_p50_ms, total_p50_ms, path_penalty_ms,
                                  created_by, created_at
                           FROM perf_probe WHERE org_id = %s::uuid
                           ORDER BY created_at DESC LIMIT 50""",
                        (caller.org_id,))
                    cols = [c[0] for c in cur.description]
                    out = []
                    for r in cur.fetchall():
                        rec = dict(zip(cols, r))
                        rec["probe_id"] = str(rec["probe_id"])
                        rec["created_at"] = rec["created_at"].isoformat() if rec["created_at"] else None
                        out.append(rec)
                return {"probes": out}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_PERF_DEFAULTS)
    def perf_defaults(x_org_name: str = Header(...),
                      x_user_id: str = Header("operator"),
                      x_role: str = Header("platform_admin")):
        """The probe's default params, read from the ACTUAL backend implementation
        (settings + constants) so the admin form is seeded with what really runs."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                _admin_caller(api, x_user_id, x_role, x_org_name)
                return {"defaults": _perf_defaults(d["settings"])}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    # ── Examiner Tone Lab ────────────────────────────────────────────────────
    # Read-only eval endpoints (the live examiner prompt + curated cases) plus the
    # experiment-tracking and prompt-override governance surface for /admin/tone-lab.

    @app.get(R.ADMIN_EVAL_PROMPT)
    def eval_examiner_prompt(course: str = "", instructor: str = "", question_id: str = "",
                             x_org_name: str = Header(...),
                             x_user_id: str = Header("operator"),
                             x_role: str = Header("platform_admin")):
        """Return the examiner evaluation system prompt exactly as production renders it
        (same code path via build_examiner_eval_prompt), so the tone lab's A0 baseline
        never drifts from what ships. Reflects an active override if one is live."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                qtext, epath, resolved_qid = _resolve_examiner_case(
                    repo, caller.org_id, question_id)
                system, version = build_examiner_eval_prompt(
                    repo, caller.org_id, qtext, epath, "")
                settings = d["settings"]
                return {
                    "system": system,
                    "prompt_version": version,
                    "model": getattr(settings, "anthropic_model", None),
                    "temperature": 0.1,          # matches the answer-flow call_bedrock
                    "input_mode": "block",       # question in system, answer in user msg
                    "source": "render",
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "context": {
                        "org": x_org_name,
                        "course": course or None,
                        "instructor": instructor or None,
                        "question_id": resolved_qid,
                        "has_expected_path": bool((epath or {}).get("nodes")),
                        "note": ("probe_directive rendered empty (the turn-0 production "
                                 "value); it steers probe target selection, not tone."),
                    },
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_EVAL_CASES)
    def eval_cases(x_org_name: str = Header(...),
                   x_user_id: str = Header("operator"),
                   x_role: str = Header("platform_admin")):
        """Return the org's curated question stems + expected-path context graphs, the
        way the examiner is actually given them, for the lab to merge by question id."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                return {"cases": _build_eval_cases(repo, caller.org_id)}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.ADMIN_EVAL_EXPERIMENTS)
    def save_tone_experiment(req: ToneExperimentSaveRequest,
                             x_org_name: str = Header(...),
                             x_user_id: str = Header("operator"),
                             x_role: str = Header("platform_admin")):
        """Persist a completed tone-lab run and compute a server-side recommendation."""
        import uuid as _uuid, json as _json
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                recommendation = _tone_recommendation(req.summary or {})
                experiment_id = str(_uuid.uuid4())
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.tone_experiment')")
                    if cur.fetchone()[0] is None:
                        return {"status": "error",
                                "message": "tone_experiment table not present — run migration_023."}
                    cur.execute(
                        """INSERT INTO tone_experiment
                           (experiment_id, org_id, title, prompt_version, config,
                            summary, recommendation, created_by)
                           VALUES (%s::uuid, %s::uuid, %s, %s, %s::jsonb, %s::jsonb,
                                   %s::jsonb, %s)""",
                        (experiment_id, caller.org_id, req.title, req.prompt_version,
                         _json.dumps(req.config or {}), _json.dumps(req.summary or {}),
                         _json.dumps(recommendation), caller.user_id))
                repo.conn.commit()
                return {"experiment_id": experiment_id, "status": "saved",
                        "recommendation": recommendation}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_EVAL_EXPERIMENTS)
    def list_tone_experiments(x_org_name: str = Header(...),
                              x_user_id: str = Header("operator"),
                              x_role: str = Header("platform_admin")):
        """Saved tone-lab experiment history (newest first) for the dashboard table."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.tone_experiment')")
                    if cur.fetchone()[0] is None:
                        return {"experiments": []}
                    cur.execute(
                        """SELECT experiment_id, title, prompt_version, summary,
                                  recommendation, created_by, created_at
                           FROM tone_experiment WHERE org_id = %s::uuid
                           ORDER BY created_at DESC LIMIT 50""",
                        (caller.org_id,))
                    rows = cur.fetchall()
                return {"experiments": [
                    {"experiment_id": str(r[0]), "title": r[1], "prompt_version": r[2],
                     "summary": r[3], "recommendation": r[4], "created_by": r[5],
                     "created_at": r[6].isoformat() if r[6] else None}
                    for r in rows]}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_EVAL_EXPERIMENT)
    def get_tone_experiment(experiment_id: str, x_org_name: str = Header(...),
                            x_user_id: str = Header("operator"),
                            x_role: str = Header("platform_admin")):
        """One saved experiment's full config, summary and recommendation."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.tone_experiment')")
                    if cur.fetchone()[0] is None:
                        return {"status": "not_found"}
                    cur.execute(
                        """SELECT experiment_id, title, prompt_version, config, summary,
                                  recommendation, created_by, created_at
                           FROM tone_experiment
                           WHERE experiment_id = %s::uuid AND org_id = %s::uuid""",
                        (experiment_id, caller.org_id))
                    r = cur.fetchone()
                if not r:
                    return {"status": "not_found"}
                return {"experiment_id": str(r[0]), "title": r[1], "prompt_version": r[2],
                        "config": r[3], "summary": r[4], "recommendation": r[5],
                        "created_by": r[6],
                        "created_at": r[7].isoformat() if r[7] else None}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.ADMIN_EVAL_OVERRIDES)
    def create_prompt_override(req: PromptOverrideCreateRequest,
                               x_org_name: str = Header(...),
                               x_user_id: str = Header("operator"),
                               x_role: str = Header("platform_admin")):
        """Create a DRAFT examiner-prompt override. Validates the template carries the
        placeholder tokens so activation can never brick the answer flow."""
        import uuid as _uuid
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                missing = [t for t in _OVERRIDE_REQUIRED_TOKENS if t not in (req.template or "")]
                if missing:
                    return {"status": "invalid",
                            "message": "template is missing required tokens: " + ", ".join(missing)}
                override_id = str(_uuid.uuid4())
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.examiner_prompt_override')")
                    if cur.fetchone()[0] is None:
                        return {"status": "error",
                                "message": "examiner_prompt_override table not present — run migration_023."}
                    cur.execute(
                        """INSERT INTO examiner_prompt_override
                           (override_id, org_id, template, notes, status,
                            based_on_experiment_id, created_by)
                           VALUES (%s::uuid, %s::uuid, %s, %s, 'draft', %s, %s)""",
                        (override_id, caller.org_id, req.template, req.notes,
                         req.based_on_experiment_id, caller.user_id))
                repo.conn.commit()
                return {"override_id": override_id, "status": "draft"}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_EVAL_OVERRIDES)
    def list_prompt_overrides(x_org_name: str = Header(...),
                              x_user_id: str = Header("operator"),
                              x_role: str = Header("platform_admin")):
        """List examiner-prompt overrides (newest first) + the shipped default version."""
        import hashlib as _hashlib
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                default_version = "default:" + _hashlib.sha1(
                    EXAMINER_EVAL_TEMPLATE.encode("utf-8")).hexdigest()[:8]
                overrides = []
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.examiner_prompt_override')")
                    if cur.fetchone()[0] is not None:
                        cur.execute(
                            """SELECT override_id, notes, status, based_on_experiment_id,
                                      created_by, created_at, reviewed_by, reviewed_at,
                                      activated_at
                               FROM examiner_prompt_override WHERE org_id = %s::uuid
                               ORDER BY created_at DESC LIMIT 50""",
                            (caller.org_id,))
                        for r in cur.fetchall():
                            overrides.append({
                                "override_id": str(r[0]), "notes": r[1], "status": r[2],
                                "based_on_experiment_id": str(r[3]) if r[3] else None,
                                "created_by": r[4],
                                "created_at": r[5].isoformat() if r[5] else None,
                                "reviewed_by": r[6],
                                "reviewed_at": r[7].isoformat() if r[7] else None,
                                "activated_at": r[8].isoformat() if r[8] else None,
                            })
                active = next((o for o in overrides if o["status"] == "active"), None)
                return {"overrides": overrides, "default_version": default_version,
                        "active_version": ("active:" + active["override_id"][:8]) if active
                                          else default_version,
                        "default_template": EXAMINER_EVAL_TEMPLATE}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    def _override_transition(override_id, x_user_id, x_role, x_org_name,
                             *, from_status, to_status, stamp):
        """Shared status-transition for the override lifecycle. `stamp` names the audit
        column to set to NOW() with the caller (reviewed_by / activated)."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.examiner_prompt_override')")
                    if cur.fetchone()[0] is None:
                        return {"status": "error",
                                "message": "examiner_prompt_override table not present — run migration_023."}
                    cur.execute(
                        """SELECT status FROM examiner_prompt_override
                           WHERE override_id = %s::uuid AND org_id = %s::uuid""",
                        (override_id, caller.org_id))
                    row = cur.fetchone()
                    if not row:
                        return {"status": "not_found"}
                    if from_status is not None and row[0] != from_status:
                        return {"status": "conflict",
                                "message": f"override is '{row[0]}', expected '{from_status}'"}
                    # Activation is exclusive: archive whatever is currently active first,
                    # so the one-active-per-org unique index is never violated.
                    if to_status == "active":
                        cur.execute(
                            """UPDATE examiner_prompt_override SET status = 'archived'
                               WHERE org_id = %s::uuid AND status = 'active'""",
                            (caller.org_id,))
                    sets = ["status = %s"]
                    vals = [to_status]
                    if stamp == "reviewed":
                        sets += ["reviewed_by = %s", "reviewed_at = NOW()"]
                        vals += [caller.user_id]
                    elif stamp == "activated":
                        sets += ["activated_at = NOW()"]
                    cur.execute(
                        "UPDATE examiner_prompt_override SET " + ", ".join(sets) +
                        " WHERE override_id = %s::uuid AND org_id = %s::uuid",
                        (*vals, override_id, caller.org_id))
                repo.conn.commit()
                return {"override_id": override_id, "status": to_status}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.ADMIN_EVAL_OVERRIDE_APPROVE)
    def approve_prompt_override(override_id: str, x_org_name: str = Header(...),
                                x_user_id: str = Header("operator"),
                                x_role: str = Header("platform_admin")):
        """Review gate: draft → approved (records reviewer)."""
        return _override_transition(override_id, x_user_id, x_role, x_org_name,
                                    from_status="draft", to_status="approved",
                                    stamp="reviewed")

    @app.post(R.ADMIN_EVAL_OVERRIDE_ACTIVATE)
    def activate_prompt_override(override_id: str, x_org_name: str = Header(...),
                                 x_user_id: str = Header("operator"),
                                 x_role: str = Header("platform_admin")):
        """Go live: approved → active. The prior active override is archived atomically."""
        return _override_transition(override_id, x_user_id, x_role, x_org_name,
                                    from_status="approved", to_status="active",
                                    stamp="activated")

    @app.post(R.ADMIN_EVAL_OVERRIDE_REVERT)
    def revert_prompt_override(override_id: str, x_org_name: str = Header(...),
                               x_user_id: str = Header("operator"),
                               x_role: str = Header("platform_admin")):
        """Instant revert: active → archived, so the answer flow falls back to the
        shipped default template."""
        return _override_transition(override_id, x_user_id, x_role, x_org_name,
                                    from_status="active", to_status="archived",
                                    stamp=None)

    @app.post(R.ADMIN_EVAL_OVERRIDE_REJECT)
    def reject_prompt_override(override_id: str, x_org_name: str = Header(...),
                               x_user_id: str = Header("operator"),
                               x_role: str = Header("platform_admin")):
        """Discard a proposal: draft → rejected."""
        return _override_transition(override_id, x_user_id, x_role, x_org_name,
                                    from_status="draft", to_status="rejected",
                                    stamp="reviewed")

    @app.get(R.ADMIN_EXAMINER_EVAL_MODE)
    def get_examiner_eval_mode(x_org_name: str = Header(...),
                               x_user_id: str = Header("operator"),
                               x_role: str = Header("platform_admin")):
        """Current answer-flow eval mode for this org ('sonnet' default | 'hybrid')."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                mode, updated_by, updated_at = "sonnet", None, None
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.examiner_config')")
                    if cur.fetchone()[0] is not None:
                        cur.execute(
                            "SELECT eval_mode, updated_by, updated_at FROM examiner_config "
                            "WHERE org_id = %s::uuid", (caller.org_id,))
                        row = cur.fetchone()
                        if row:
                            mode = row[0]
                            updated_by = row[1]
                            updated_at = row[2].isoformat() if row[2] else None
                return {"eval_mode": mode, "text_first": get_text_first(repo, caller.org_id),
                        "updated_by": updated_by, "updated_at": updated_at,
                        "available": ["sonnet", "hybrid"]}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.put(R.ADMIN_EXAMINER_TEXT_FIRST)
    def set_examiner_text_first(req: TextFirstRequest, x_org_name: str = Header(...),
                                x_user_id: str = Header("operator"),
                                x_role: str = Header("platform_admin")):
        """Toggle the student-UI text-first render (probe shown before vs with TTS audio)."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.examiner_config')")
                    if cur.fetchone()[0] is None:
                        return {"status": "error",
                                "message": "examiner_config table not present — run migration_024/025."}
                    cur.execute(
                        """INSERT INTO examiner_config (org_id, text_first, updated_by, updated_at)
                           VALUES (%s::uuid, %s, %s, NOW())
                           ON CONFLICT (org_id) DO UPDATE
                           SET text_first = EXCLUDED.text_first, updated_by = EXCLUDED.updated_by,
                               updated_at = NOW()""",
                        (caller.org_id, req.text_first, caller.user_id))
                repo.conn.commit()
                return {"text_first": req.text_first, "status": "updated"}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.put(R.ADMIN_EXAMINER_EVAL_MODE)
    def set_examiner_eval_mode(req: EvalModeRequest, x_org_name: str = Header(...),
                               x_user_id: str = Header("operator"),
                               x_role: str = Header("platform_admin")):
        """Flip the answer-flow eval mode ('sonnet' | 'hybrid'). Effective on the next turn."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.examiner_config')")
                    if cur.fetchone()[0] is None:
                        return {"status": "error",
                                "message": "examiner_config table not present — run migration_024."}
                    cur.execute(
                        """INSERT INTO examiner_config (org_id, eval_mode, updated_by, updated_at)
                           VALUES (%s::uuid, %s, %s, NOW())
                           ON CONFLICT (org_id) DO UPDATE
                           SET eval_mode = EXCLUDED.eval_mode, updated_by = EXCLUDED.updated_by,
                               updated_at = NOW()""",
                        (caller.org_id, req.eval_mode, caller.user_id))
                repo.conn.commit()
                return {"eval_mode": req.eval_mode, "status": "updated"}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


class QGTestRunRequest(BaseModel):
    """POST body to run the QG quality bench for one subject."""
    course_id: str
    count: int = Field(default=6, ge=1, le=MAX_QUESTION_COUNT)
    difficulty: str = Field(default="balanced", pattern=r"^(recall|balanced|deep)$")
    concept_ids: Optional[List[str]] = None
    domain: str = Field(default="general", min_length=1, max_length=200)


class QGHumanEvalRequest(BaseModel):
    """PUT body: a reviewer's human evaluation of one generated question."""
    verdict: Optional[str] = Field(default=None, pattern=r"^(good|needs_edit|reject)$")
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    notes: Optional[str] = Field(default=None, max_length=4000)


class PerfProbeRequest(BaseModel):
    """POST body: an experiment title + the params to run the perf probe with.
    Params are seeded client-side from GET /perf/defaults (the backend's actual
    values) so admin perception stays in sync with the real implementation."""
    runs: int = Field(default=3, ge=1, le=8)
    title: Optional[str] = Field(default=None, max_length=200)
    params: Optional[Dict[str, object]] = None


# ── Examiner Tone Lab: request models + helpers ─────────────────────────────
_OVERRIDE_REQUIRED_TOKENS = ("{{QUESTION_TEXT}}", "{{EXPECTED_PATH_JSON}}",
                             "{{PROBE_DIRECTIVE}}")


class ToneExperimentSaveRequest(BaseModel):
    """POST body: a completed tone-lab run to persist. `summary` is the per-arm tally
    the lab computed; the server derives the recommendation from it."""
    title: Optional[str] = Field(default=None, max_length=200)
    prompt_version: Optional[str] = Field(default=None, max_length=120)
    config: Optional[Dict[str, object]] = None
    summary: Optional[Dict[str, object]] = None


class PromptOverrideCreateRequest(BaseModel):
    """POST body: a proposed examiner-prompt override template (created as a draft)."""
    template: str = Field(min_length=40, max_length=20000)
    notes: Optional[str] = Field(default=None, max_length=4000)
    based_on_experiment_id: Optional[str] = Field(default=None, max_length=64)


class EvalModeRequest(BaseModel):
    """PUT body: the answer-flow evaluation mode toggle."""
    eval_mode: str = Field(pattern=r"^(sonnet|hybrid)$")


class TextFirstRequest(BaseModel):
    """PUT body: the student-UI text-first render toggle."""
    text_first: bool


def _tone_recommendation(summary: dict) -> dict:
    """Deterministically rank arms by combined leakage + tone-violation penalty and
    pick the best. `summary["arms"]` is a list of per-arm metric dicts from the lab."""
    arms = (summary or {}).get("arms") or []
    scored = []
    for a in arms:
        lr = float(a.get("leak_rate", 0) or 0)
        pen = (lr * 100
               + 3 * int(a.get("evaluative", 0) or 0)
               + 3 * int(a.get("confirms", 0) or 0)
               + 1 * int(a.get("compound", 0) or 0)
               + 2 * int(a.get("empty_probe", 0) or 0)
               + 5 * int(a.get("parse_fails", 0) or 0))
        scored.append({"arm": a.get("arm"), "penalty": round(pen, 2), "leak_rate": lr,
                       "evaluative": int(a.get("evaluative", 0) or 0),
                       "confirms": int(a.get("confirms", 0) or 0)})
    scored.sort(key=lambda x: x["penalty"])
    if not scored:
        return {"pick": None, "rationale": "No per-arm metrics were provided.", "ranking": []}
    best = scored[0]
    baseline = next((s for s in scored if s["arm"] == "A0"), None)
    rationale = (f"{best['arm']} scores lowest on the combined leakage + tone-violation "
                 f"penalty ({best['penalty']}).")
    if best["arm"] == "A0":
        rationale += " The shipped baseline already wins on this run — no change recommended yet."
    elif baseline is not None:
        delta = round(baseline["penalty"] - best["penalty"], 2)
        rationale += (f" It beats the shipped A0 baseline by {delta} "
                      f"(A0 leak rate {baseline['leak_rate']:.0%}, "
                      f"{best['arm']} {best['leak_rate']:.0%}).")
    return {"pick": best["arm"], "rationale": rationale, "ranking": scored}


def _resolve_examiner_case(repo, org_id, question_id: str):
    """Resolve (question_text, expected_path, question_id) for the prompt endpoint.

    With a question_id, return that question. Otherwise pick the org's most recent
    question that already has a non-empty expected_path (so A0 renders a real graph
    without a Bedrock generation side effect). Falls back to a placeholder when the
    org has no questions yet. Defensive against an un-migrated expected_path column.
    """
    import json as _json
    def _epath(val):
        if not val:
            return {}
        return val if isinstance(val, dict) else _json.loads(val)
    with repo.conn.cursor() as cur:
        try:
            if question_id:
                cur.execute(
                    """SELECT text, expected_path FROM question
                       WHERE question_id = %s::uuid AND org_id = %s::uuid""",
                    (question_id, org_id))
                row = cur.fetchone()
                if row:
                    return row[0], _epath(row[1]), question_id
            cur.execute(
                """SELECT question_id, text, expected_path FROM question
                   WHERE org_id = %s::uuid AND expected_path IS NOT NULL
                         AND jsonb_array_length(COALESCE(expected_path->'nodes', '[]'::jsonb)) > 0
                   ORDER BY created_at DESC LIMIT 1""",
                (org_id,))
            row = cur.fetchone()
            if row:
                return row[1], _epath(row[2]), str(row[0])
            # No graph-backed question: return any question's stem so A0 still renders.
            cur.execute(
                """SELECT question_id, text FROM question WHERE org_id = %s::uuid
                   ORDER BY created_at DESC LIMIT 1""",
                (org_id,))
            row = cur.fetchone()
            if row:
                return row[1], {}, str(row[0])
        except Exception:
            repo.conn.rollback()  # un-migrated expected_path column, etc.
    return ("(no questions in this org yet — showing the prompt skeleton)", {}, None)


def _build_eval_cases(repo, org_id) -> dict:
    """Build the tone-lab cases map from the org's graph-backed questions. Each case is
    {domain, stem, graph:[{id,eps,text}]}, derived from question.text + expected_path."""
    import json as _json
    cases = {}
    with repo.conn.cursor() as cur:
        try:
            cur.execute(
                """SELECT q.question_id, q.text, q.expected_path, c.name
                   FROM question q LEFT JOIN course c ON c.course_id = q.course_id
                   WHERE q.org_id = %s::uuid AND q.expected_path IS NOT NULL
                         AND jsonb_array_length(COALESCE(q.expected_path->'nodes', '[]'::jsonb)) > 0
                   ORDER BY q.created_at DESC LIMIT 50""",
                (org_id,))
            rows = cur.fetchall()
        except Exception:
            repo.conn.rollback()  # expected_path column not present yet
            return cases
    for qid, text, epath_raw, course_name in rows:
        epath = epath_raw if isinstance(epath_raw, dict) else _json.loads(epath_raw or "{}")
        edges = epath.get("edges") or []
        graph = []
        for i, e in enumerate(edges):
            src, dst = e.get("src", ""), e.get("dst", "")
            if not src or not dst:
                continue
            expl = (e.get("explanation") or "").strip()
            text_edge = f"{src} -> {dst}" + (f", {expl}" if expl else "")
            # expected_path carries no saliency; approximate a descending epsilon by
            # position so the lab's highest-first probe ordering has something to sort on.
            eps = round(max(0.5, 0.9 - 0.1 * i), 2)
            graph.append({"id": f"E{i + 1}", "eps": eps, "text": text_edge})
        cases[str(qid)] = {"domain": course_name or "Course",
                           "stem": text, "graph": graph}
    return cases


def _ensure_qg_test_table(repo) -> None:
    """Create the qg_test_run history table on first use (non-owner-safe)."""
    with repo.conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.qg_test_run')")
        if cur.fetchone()[0] is not None:
            return
        cur.execute(
            """CREATE TABLE IF NOT EXISTS qg_test_run (
                   run_id UUID PRIMARY KEY,
                   org_id UUID NOT NULL,
                   course_id UUID NOT NULL,
                   course_name TEXT,
                   difficulty TEXT NOT NULL,
                   requested_count INT NOT NULL,
                   generated_count INT NOT NULL,
                   report JSONB,
                   status TEXT NOT NULL,
                   error TEXT,
                   created_by TEXT,
                   created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")
    repo.conn.commit()


def _persist_qg_questions(cur, run_id, org_id, course_id, questions, report):
    """Save one row per generated question (text, difficulty, rubric + the
    per-question automated QG verdicts) to qg_test_question, so the batch can be
    human-evaluated later. No-op if the table isn't present."""
    import json as _json
    from collections import defaultdict
    from psycopg2.extras import execute_values
    cur.execute("SELECT to_regclass('public.qg_test_question')")
    if cur.fetchone()[0] is None:
        return
    # Group the automated per-question verdicts by question id (skip batch checks).
    by_qid = defaultdict(list)
    for r in report.get("results", []):
        rid = r.get("id")
        if rid and rid != "BATCH":
            by_qid[rid].append({"criterion": r.get("criterion"),
                                "status": r.get("status"), "reasoning": r.get("reasoning")})
    rows = []
    for i, q in enumerate(questions):
        qid = q.get("question_id") or ("Q%d" % (i + 1))
        auto = by_qid.get(qid, [])
        rows.append((
            run_id, org_id, course_id, i + 1, q.get("question"),
            q.get("_declared") or q.get("difficulty"), q.get("_classified"),
            _json.dumps(q.get("concept_ids") or []),
            _json.dumps(q.get("expected_path") or {}),
            _json.dumps(auto),
            sum(1 for a in auto if a["status"] == "pass"),
            sum(1 for a in auto if a["status"] == "fail"),
        ))
    if rows:
        execute_values(cur,
            """INSERT INTO qg_test_question
               (run_id, org_id, course_id, position, question_text,
                declared_difficulty, classified_difficulty, concept_ids,
                expected_path, auto_results, auto_pass, auto_fail)
               VALUES %s""",
            rows,
            template="(%s::uuid,%s::uuid,%s::uuid,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)")


def _perf_defaults(settings) -> dict:
    """The perf probe's params seeded from the ACTUAL implementation values, so the
    admin form shows what the backend really uses (keeps perception in sync)."""
    return {
        "runs": 3,
        "eval_model": getattr(settings, "anthropic_model", None),
        "eval_max_tokens": LLM_MAX_TOKENS_EVALUATION,
        "eval_temperature": 0.1,
        "tts_model": getattr(settings, "elevenlabs_model", None),
        "tts_voice": getattr(settings, "elevenlabs_voice_id", None),
        "provider": getattr(settings, "llm_provider", None),
        "expected_path_max_tokens": 3000,
    }


def _perf_effective_params(settings, provided: dict) -> dict:
    """Merge admin-provided params over the backend defaults, clamped to safe ranges."""
    eff = {**_perf_defaults(settings), **(provided or {})}
    try:
        eff["runs"] = max(1, min(8, int(eff.get("runs", 3))))
    except (TypeError, ValueError):
        eff["runs"] = 3
    try:
        eff["eval_max_tokens"] = max(64, min(4000, int(eff.get("eval_max_tokens", LLM_MAX_TOKENS_EVALUATION))))
    except (TypeError, ValueError):
        eff["eval_max_tokens"] = LLM_MAX_TOKENS_EVALUATION
    try:
        eff["eval_temperature"] = max(0.0, min(1.0, float(eff.get("eval_temperature", 0.1))))
    except (TypeError, ValueError):
        eff["eval_temperature"] = 0.1
    return eff


def _persist_perf_probe(settings, org_id, probe_id, status, result=None, error=None):
    """Update the perf_probe row (owner-migrated table) with a run's outcome.
    Best-effort: a missing table or write error is logged, not raised."""
    import json as _json
    conn = None
    try:
        conn = factory.db_connection(settings)
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id', %s, false)", (org_id,))
            cur.execute("SELECT to_regclass('public.perf_probe')")
            if cur.fetchone()[0] is None:
                return
            r = result or {}
            cur.execute(
                """UPDATE perf_probe SET status = %s, result = %s::jsonb, error = %s,
                          provider = %s, eval_model = %s, tts_model = %s,
                          eval_p50_ms = %s, tts_p50_ms = %s, total_p50_ms = %s, path_penalty_ms = %s
                   WHERE probe_id = %s::uuid AND org_id = %s::uuid""",
                (status, _json.dumps(r) if result else None, error,
                 r.get("provider"), r.get("eval_model"), r.get("tts_model"),
                 (r.get("eval_ms") or {}).get("p50"), (r.get("tts_ms") or {}).get("p50"),
                 (r.get("total_per_answer_ms") or {}).get("p50"), r.get("first_answer_path_penalty_ms"),
                 probe_id, org_id))
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("perf probe %s: persist failed: %s", probe_id[:8], exc)
    finally:
        if conn is not None:
            conn.close()


def _run_perf_probe_bg(settings, org_id, probe_id, params):
    """Background: time the end-to-end student answer flow — the eval LLM call and
    the TTS round-trip per run, plus a one-off expected_path generation (the cost
    a first answer to an un-prebuilt question pays). Runs with the experiment's
    `params` (model/tokens/temp/voice), persists to perf_probe. Bounded work,
    off-request, so the probe never trips a gateway timeout."""
    import time as _t, statistics as _st, json as _json
    runs = int(params.get("runs", 3))
    eval_model = params.get("eval_model") or None
    eval_max_tokens = int(params.get("eval_max_tokens", LLM_MAX_TOKENS_EVALUATION))
    eval_temp = float(params.get("eval_temperature", 0.1))
    tts_model = params.get("tts_model") or None
    tts_voice = params.get("tts_voice") or None
    from backend.bedrock_helper import call_bedrock
    from backend import tts_helper
    try:
        expected = {"nodes": [{"label": "Increase in supply", "definition": "more of the good is available"},
                              {"label": "Equilibrium price", "definition": "price where supply meets demand"}],
                    "edges": [{"src": "Increase in supply", "dst": "Equilibrium price", "link_type": "DECREASES",
                               "explanation": "a rightward supply shift lowers the market-clearing price"}],
                    "extensions": []}
        system = ("You are an Epistemy Socratic oral examiner performing two tasks. TASK 1: evaluate the "
                  "student's answer; when adequate=false give a probe. TASK 2: identify demonstrated "
                  "nodes/edges. EXPECTED PATH:\n" + _json.dumps(expected) + "\nRespond ONLY with minified "
                  'JSON: {"answered":true,"adequate":false,"feedback":"..","probe":"..","eds":{}}')
        question_text = "How does an increase in supply affect the equilibrium price?"
        answer_text = ("When supply goes up the price usually falls because there's more of the good "
                       "available than people want at the old price, so sellers cut prices to clear it.")
        ctx = f"Exam question: {question_text}\n\nStudent's latest answer: {answer_text}"
        fallback_probe = ("You said the price falls, but can you explain the mechanism: what happens to the "
                          "quantity supplied at the original price, and how does that push the price down?")
        eval_ms, tts_ms, total_ms = [], [], []
        traces = []
        audio_bytes = 0
        sample_probe = sample_feedback = ""
        for i in range(runs):
            steps = []
            # Step 1 — evaluation LLM (the response-processing cost); capture the probe it emits.
            t0 = _t.time(); probe_out = fb_out = ""
            try:
                parsed = call_bedrock(settings, system, ctx, max_tokens=eval_max_tokens,
                                      temperature=eval_temp, model=eval_model)
                if isinstance(parsed, dict):
                    probe_out = (parsed.get("probe") or "").strip()
                    fb_out = (parsed.get("feedback") or "").strip()
            except Exception:  # noqa: BLE001 - a failed call still yields a timing sample
                pass
            te = round((_t.time() - t0) * 1000)
            steps.append({"name": "eval_llm", "ms": te, "detail": eval_model or getattr(settings, "anthropic_model", None)})
            logger.info("perf-trace probe=%s run=%d step=eval_llm ms=%d", probe_id[:8], i + 1, te)
            # Step 2 — TTS the examiner's probe (what the eval actually produced).
            t1 = _t.time()
            try:
                a = tts_helper.synthesize(settings, probe_out or fallback_probe, voice_id=tts_voice, model=tts_model)
                audio_bytes = len(a) if a else 0
            except Exception:  # noqa: BLE001
                pass
            tt = round((_t.time() - t1) * 1000)
            steps.append({"name": "tts", "ms": tt, "detail": tts_model or getattr(settings, "elevenlabs_model", None)})
            logger.info("perf-trace probe=%s run=%d step=tts ms=%d bytes=%d", probe_id[:8], i + 1, tt, audio_bytes)

            eval_ms.append(te); tts_ms.append(tt); total_ms.append(te + tt)
            traces.append({"run": i + 1, "total_ms": te + tt, "steps": steps, "probe": probe_out})
            if probe_out and not sample_probe:
                sample_probe = probe_out
            if fb_out and not sample_feedback:
                sample_feedback = fb_out
        # First-answer penalty: expected_path generation (only paid once, on an un-prebuilt question).
        pt0 = _t.time()
        try:
            _generate_expected_path(settings, question_text, ["Increase in supply", "Equilibrium price"])
        except Exception:  # noqa: BLE001
            pass
        path_ms = round((_t.time() - pt0) * 1000)
        logger.info("perf-trace probe=%s step=expected_path_gen ms=%d", probe_id[:8], path_ms)

        def _summ(xs):
            return {"min": min(xs), "p50": round(_st.median(xs)), "max": max(xs), "runs": xs} if xs else {}
        result = {
            "runs": runs,
            "provider": getattr(settings, "llm_provider", None),
            "eval_model": eval_model or getattr(settings, "anthropic_model", None),
            "tts_model": tts_model or getattr(settings, "elevenlabs_model", None),
            "params": params,
            "eval_ms": _summ(eval_ms),
            "tts_ms": _summ(tts_ms),
            "total_per_answer_ms": _summ(total_ms),
            "first_answer_path_penalty_ms": path_ms,
            "tts_audio_bytes": audio_bytes,
            "test_question": question_text,
            "test_answer": answer_text,
            "sample_probe": sample_probe,
            "sample_feedback": sample_feedback,
            "traces": traces,
        }
        _persist_perf_probe(settings, org_id, probe_id, "completed", result=result)
    except Exception as exc:  # noqa: BLE001
        logger.warning("perf probe %s failed: %s", probe_id[:8], exc, exc_info=True)
        _persist_perf_probe(settings, org_id, probe_id, "failed", error=str(exc)[:300])


def _run_qg_test_bg(d, org_id, run_id, course_id, course_name, difficulty,
                    count, concept_ids, domain):
    """Background: generate a batch with the real generator, embed + grade it,
    and persist the report + per-question rows. Updates the qg_test_run row to
    'completed'/'failed'. Run off-request so a slow LLM batch can't hold the
    HTTP connection past CloudFront's origin timeout (→ 504)."""
    import json as _json
    from backend.app import qg_bench
    settings = d["settings"]
    repo = _request_repo(d)
    try:
        repo.set_tenant(org_id)
        graph_data = _query_graph_version(repo, org_id, course_id)
        concepts = graph_data.get("concepts", [])
        if concept_ids:
            sel = set(concept_ids)
            concepts = [c for c in concepts
                        if c.get("label") in sel or c.get("id") in sel]
        with repo.conn.cursor() as cur:
            cur.execute("SELECT text FROM chunk WHERE course_id = %s ORDER BY chunk_index",
                        (course_id,))
            chunks = [row[0] for row in cur.fetchall()]

        questions = _build_question_dicts(
            settings, concepts=concepts, chunks=chunks,
            difficulty=difficulty, domain=domain, count=count)
        if not questions:
            raise RuntimeError("The generator returned no usable questions.")

        embeddings = None
        try:
            embeddings = d["embedder"].embed([q["question"] for q in questions])
        except Exception as exc:  # embedding is best-effort; QG-06 falls back to BoW
            logger.warning("QG bench %s: embedding failed, QG-06 uses bag-of-words: %s",
                           run_id[:8], exc)

        report = qg_bench.run_qg_checks(
            course_name, graph_data, questions, embeddings=embeddings)

        with repo.conn.cursor() as cur:
            cur.execute(
                """UPDATE qg_test_run SET status = 'completed', report = %s::jsonb,
                          generated_count = %s
                   WHERE run_id = %s::uuid AND org_id = %s::uuid""",
                (_json.dumps(report), len(questions), run_id, org_id))
            _persist_qg_questions(cur, run_id, org_id, course_id, questions, report)
        repo.conn.commit()
        logger.info("QG bench %s completed: %d questions", run_id[:8], len(questions))
    except Exception as exc:  # noqa: BLE001
        logger.warning("QG bench %s failed: %s", run_id[:8], exc, exc_info=True)
        try:
            repo.conn.rollback()
            with repo.conn.cursor() as cur:
                cur.execute(
                    """UPDATE qg_test_run SET status = 'failed', error = %s
                       WHERE run_id = %s::uuid AND org_id = %s::uuid""",
                    (str(exc)[:500], run_id, org_id))
            repo.conn.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        _release_repo(d, repo)


def _register_admin_testing(app: FastAPI, deps) -> None:
    """platform_admin: the QG (question-generation) quality test bench.

    Sources a real subject's concept graph, live-generates a question batch with
    the SAME generator students get (``_build_question_dicts``), then grades it
    with ``qg_bench.run_qg_checks`` against the graph + each question's
    ``expected_path`` + real embeddings. Ephemeral: test questions are NEVER
    persisted to the ``question`` table, only the run's report is kept for history.
    """
    from backend.app import qg_bench

    def _admin_caller(api, x_user_id, x_role, x_org_name):
        caller = api.caller_for_org(x_user_id, x_role, x_org_name)
        if caller.role != Role.PLATFORM_ADMIN:
            raise AuthorizationError("platform_admin role required")
        return caller

    @app.get(R.ADMIN_TESTING_SUBJECTS)
    def testing_subjects(x_org_name: str = Header(...),
                         x_user_id: str = Header("operator"),
                         x_role: str = Header("platform_admin")):
        """Courses in the admin's org that have a concept graph to test against."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)   # RLS scopes course + graph_version
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT c.course_id, c.course_name,
                                  COALESCE(g.node_count, 0), COALESCE(g.edge_count, 0)
                           FROM course c
                           LEFT JOIN graph_version g
                             ON g.course_id = c.course_id AND g.is_active = true
                           ORDER BY c.created_at DESC LIMIT 200""")
                    rows = cur.fetchall()
                subjects = [
                    {"course_id": str(r[0]), "course_name": r[1],
                     "node_count": r[2], "edge_count": r[3],
                     "testable": (r[2] or 0) > 0}
                    for r in rows]
                return {"subjects": subjects}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.post(R.ADMIN_TESTING_RUNS)
    def create_testing_run(req: QGTestRunRequest, x_org_name: str = Header(...),
                           x_user_id: str = Header("operator"),
                           x_role: str = Header("platform_admin")):
        """Kick off a generate+grade run in the background and return immediately.

        The heavy work (LLM generation, embeddings, grading) runs off-request in a
        thread so it can't hold the HTTP connection past CloudFront's origin
        timeout (which surfaced as intermittent 504s). The client polls
        GET /runs/{id} until status flips to completed/failed.
        """
        import uuid as _uuid

        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                _ensure_qg_test_table(repo)

                # Fast validation only — subject exists and has a concept graph.
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT course_name FROM course WHERE course_id = %s::uuid",
                                (req.course_id,))
                    crow = cur.fetchone()
                if not crow:
                    raise AuthorizationError("course not found")
                course_name = crow[0]

                graph_data = _query_graph_version(repo, caller.org_id, req.course_id)
                if not graph_data.get("concepts"):
                    return {"status": "error",
                            "message": "This subject has no concept graph yet. Build the graph first."}

                # Record a 'running' row, then generate + grade in the background.
                run_id = str(_uuid.uuid4())
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO qg_test_run
                           (run_id, org_id, course_id, course_name, difficulty,
                            requested_count, generated_count, status, created_by)
                           VALUES (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s, 0, 'running', %s)""",
                        (run_id, caller.org_id, req.course_id, course_name, req.difficulty,
                         req.count, caller.user_id))
                repo.conn.commit()

                threading.Thread(
                    target=_run_qg_test_bg,
                    args=(d, caller.org_id, run_id, req.course_id, course_name,
                          req.difficulty, req.count, req.concept_ids, req.domain),
                    daemon=True).start()

                return {"status": "running", "run_id": run_id,
                        "course_id": req.course_id, "course_name": course_name,
                        "difficulty": req.difficulty}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_TESTING_RUNS)
    def list_testing_runs(x_org_name: str = Header(...),
                          x_user_id: str = Header("operator"),
                          x_role: str = Header("platform_admin")):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                _ensure_qg_test_table(repo)
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT run_id, course_id, course_name, difficulty,
                                  generated_count, report, status, created_at
                           FROM qg_test_run WHERE org_id = %s::uuid
                           ORDER BY created_at DESC LIMIT 50""",
                        (caller.org_id,))
                    rows = cur.fetchall()
                runs = []
                for r in rows:
                    report = r[5] if isinstance(r[5], dict) else None
                    summary = (report or {}).get("summary") if report else None
                    fails = sum(v.get("fail", 0) for v in (summary or {}).values())
                    runs.append({
                        "run_id": str(r[0]), "course_id": str(r[1]),
                        "course_name": r[2], "difficulty": r[3],
                        "generated_count": r[4], "status": r[6],
                        "fail_count": fails,
                        "created_at": r[7].isoformat() if r[7] else None,
                    })
                return {"runs": runs}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_TESTING_RUN)
    def get_testing_run(run_id: str, x_org_name: str = Header(...),
                        x_user_id: str = Header("operator"),
                        x_role: str = Header("platform_admin")):
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                _ensure_qg_test_table(repo)
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """SELECT course_id, course_name, difficulty, generated_count,
                                  report, status, error, created_at
                           FROM qg_test_run
                           WHERE run_id = %s::uuid AND org_id = %s::uuid""",
                        (run_id, caller.org_id))
                    r = cur.fetchone()
                if not r:
                    raise AuthorizationError("test run not found")
                return {
                    "run_id": run_id, "course_id": str(r[0]), "course_name": r[1],
                    "difficulty": r[2], "generated_count": r[3], "report": r[4],
                    "status": r[5], "error": r[6],
                    "created_at": r[7].isoformat() if r[7] else None,
                }
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.get(R.ADMIN_TESTING_RUN_QUESTIONS)
    def list_testing_run_questions(run_id: str, x_org_name: str = Header(...),
                                   x_user_id: str = Header("operator"),
                                   x_role: str = Header("platform_admin")):
        """Per-question records for a run: text, difficulty, the automated QG
        verdicts, and any human evaluation recorded so far."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('public.qg_test_question')")
                    if cur.fetchone()[0] is None:
                        return {"questions": []}
                    cur.execute(
                        """SELECT question_id, position, question_text, declared_difficulty,
                                  classified_difficulty, concept_ids, expected_path,
                                  auto_results, auto_pass, auto_fail, human_verdict,
                                  human_rating, human_notes, reviewed_by, reviewed_at
                           FROM qg_test_question
                           WHERE run_id = %s::uuid AND org_id = %s::uuid
                           ORDER BY position""",
                        (run_id, caller.org_id))
                    cols = [c[0] for c in cur.description]
                    out = []
                    for row in cur.fetchall():
                        rec = dict(zip(cols, row))
                        rec["question_id"] = str(rec["question_id"])
                        rec["reviewed_at"] = (rec["reviewed_at"].isoformat()
                                              if rec["reviewed_at"] else None)
                        out.append(rec)
                return {"questions": out}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)

    @app.put(R.ADMIN_TESTING_QUESTION_EVAL)
    def save_testing_question_eval(question_id: str, req: QGHumanEvalRequest,
                                   x_org_name: str = Header(...),
                                   x_user_id: str = Header("operator"),
                                   x_role: str = Header("platform_admin")):
        """Record (or update) a reviewer's human evaluation of one question."""
        def _do():
            d = deps()
            repo = _request_repo(d)
            try:
                api = factory.build_api(d["settings"], repo, d["storage"], d["queue"])
                caller = _admin_caller(api, x_user_id, x_role, x_org_name)
                repo.set_tenant(caller.org_id)
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """UPDATE qg_test_question
                           SET human_verdict = %s, human_rating = %s, human_notes = %s,
                               reviewed_by = %s, reviewed_at = NOW()
                           WHERE question_id = %s::uuid AND org_id = %s::uuid
                           RETURNING question_id""",
                        (req.verdict, req.rating, req.notes, caller.user_id,
                         question_id, caller.org_id))
                    if cur.fetchone() is None:
                        raise AuthorizationError("question not found")
                repo.conn.commit()
                return {"status": "saved", "question_id": question_id}
            finally:
                _release_repo(d, repo)
        return _guard(deps, _do)


app = create_app()
