"""FastAPI HTTP surface: health, presign, register, read tools, search, graph, questions, delivery, evaluation, dashboard.

TODO(prod): Extract route handlers into domain service classes to improve testability and reduce file size.
"""
from __future__ import annotations

import logging
import os
import re
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
    LLM_MAX_TOKENS_EVALUATION,
    EDS_ALPHA, EDS_BETA, EDS_GAMMA,
)
from backend.models import Role, IngestRequest
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
from backend.app.exam_questions import (
    stored_concept_banks as _concept_banks,
    merge_generated_banks,
    sanitize_bank as _sanitize_bank,
    DIFFICULTY_FOCUS,
)
from backend.app.concept_graph import write_document_concepts, recompute_course_graph, document_graph
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
        # Bounded path + token headroom so this single call finishes fast and never
        # truncates → no 3x retry, so the first answer to a question can't time out.
        return call_bedrock(settings, system_prompt, user_msg, max_tokens=3000, temperature=0.1)
    except Exception:
        return {"nodes": [], "edges": [], "extensions": []}


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
    import json as _json, uuid as _uuid
    conn = None
    with _INFLIGHT_LOCK:
        _INFLIGHT_REBUILDS[course_id] = org_id
    try:
        conn = factory.db_connection(settings)
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.org_id', %s, false)", (org_id,))
            conn.commit()
            cur.execute(
                "SELECT DISTINCT material_version_id FROM chunk WHERE course_id = %s ORDER BY material_version_id",
                (course_id,))
            mv_ids = [str(r[0]) for r in cur.fetchall()]

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
                                max_tokens=8000, temperature=0.2)
            with conn.cursor() as cur:
                write_document_concepts(cur, org_id, course_id, mv,
                                        data.get("concepts", []), data.get("relations", []))
            conn.commit()

        with conn.cursor() as cur:
            snapshot = recompute_course_graph(cur, org_id, course_id)
            cur.execute("UPDATE graph_version SET is_active = false WHERE org_id = %s AND course_id = %s",
                        (org_id, course_id))
            cur.execute(
                """INSERT INTO graph_version
                   (version_id, org_id, course_id, graph_version, node_count,
                    edge_count, validation_score, is_active, s3_key)
                   VALUES (%s::uuid, %s::uuid, %s::uuid, 1, %s, %s, %s, true, %s)""",
                (str(_uuid.uuid4()), org_id, course_id, len(snapshot["concepts"]),
                 len(snapshot["relations"]), 0.8, _json.dumps(snapshot)))
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
    """Gate a course action behind an uploaded syllabus (409 if missing).

    Every substantive course action (build the graph, generate/assign exams,
    create sessions) requires the syllabus first — only enrollment is exempt.
    Returns a 409 so the client can prompt "Add the syllabus to continue"."""
    if not _course_has_syllabus(repo, course_id):
        raise HTTPException(
            status_code=409,
            detail="Add the course syllabus before this action.")


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

                # Source text: explicit paste wins; otherwise the syllabus's chunks.
                text = (req.text or "").strip()
                if not text:
                    with repo.conn.cursor() as cur:
                        cur.execute("SELECT material_version_id FROM course_syllabus WHERE course_id = %s::uuid",
                                    (course_id,))
                        row = cur.fetchone()
                    vid = str(row[0]) if row and row[0] else None
                    if vid:
                        chunks = repo.list_chunks(vid)
                        text = "\n".join(c.get("text", "") for c in chunks).strip()
                if not text:
                    raise HTTPException(
                        status_code=409,
                        detail="The syllabus is still being processed. Try again in a moment, or paste its schedule.")

                # Don't duplicate: if sessions already exist, return them untouched.
                has_scope = None
                with repo.conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM class_session WHERE course_id = %s::uuid AND org_id = %s::uuid",
                                (course_id, caller.org_id))
                    if cur.fetchone()[0] > 0:
                        return {"status": "exists", "created": 0,
                                "message": "This course already has sessions — clear them to regenerate."}
                    has_scope = _session_scope_col(cur)

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
        """Deep health check: verifies DB connectivity. Returns 503 if DB is down."""
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


def _query_student_courses(repo, org_id: str, student_email: str) -> list:
    """Courses a student can access: those with no roster (open) OR where the
    student is enrolled — the same gate as assignment visibility, so the course
    list and the assignments the student sees stay consistent."""
    _ensure_enrollment_table(repo)
    email = (student_email or "").strip().lower()
    with repo.conn.cursor() as cur:
        cur.execute(
            """SELECT course_id, course_name FROM course c
               WHERE c.org_id = %s
                 AND (NOT EXISTS (SELECT 1 FROM enrollment e WHERE e.course_id = c.course_id)
                      OR EXISTS (SELECT 1 FROM enrollment e
                                 WHERE e.course_id = c.course_id AND e.student_email = %s))
               ORDER BY course_name""",
            (org_id, email),
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

                # Roster-scoped: only courses the student is enrolled in (or open,
                # rosterless courses) — not every course in the org.
                courses = _query_student_courses(repo, caller.org_id, caller.user_id)
                # Pass the student's email so the roster gate matches — without it
                # student_email defaults to "", which matches no roster entry, so
                # every course that HAS a roster has its assignments hidden.
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

    A course with no roster stays open to everyone (backward-compatible); once a
    roster exists, only enrolled students see that course's assignments."""
    _ensure_enrollment_table(repo)
    email = (student_email or "").strip().lower()
    with repo.conn.cursor() as cur:
        cur.execute("""SELECT 1 FROM information_schema.columns
                       WHERE table_name='assignment' AND column_name='assignment_type'""")
        type_col = "a.assignment_type" if cur.fetchone() is not None else "'assignment'"
        cur.execute(
            f"""SELECT a.assignment_id, a.title, a.status, a.config,
                      a.created_at, c.course_name, a.course_id, {type_col},
                      EXISTS (SELECT 1 FROM exam_session es
                              WHERE es.assignment_id = a.assignment_id
                                AND es.student_id = %s AND es.status = 'completed') AS completed
               FROM assignment a
               JOIN course c ON c.course_id = a.course_id
               WHERE a.org_id = %s AND a.status = 'active'
                 AND (NOT EXISTS (SELECT 1 FROM enrollment e WHERE e.course_id = a.course_id)
                      OR EXISTS (SELECT 1 FROM enrollment e
                                 WHERE e.course_id = a.course_id AND e.student_email = %s))
               ORDER BY a.created_at DESC""",
            (email, org_id, email),
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


def _query_exam_results(repo, assignment_id: str, student_id: str) -> dict:
    """Assemble the caller's exam results from their most-recent session."""
    with repo.conn.cursor() as cur:
        cur.execute(
            """SELECT session_id, status, completed_at FROM exam_session
               WHERE assignment_id = %s::uuid AND student_id = %s
               ORDER BY started_at DESC LIMIT 1""",
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
    if grow and grow[2] == "released":
        overall = round(float(grow[0]) * 100)
        comp = grow[1] if isinstance(grow[1], dict) else _json.loads(grow[1] or "{}")
        comment = comp.get("overall_comment")
        if comment:
            feedback = comment

    return {
        "session_id": session_id, "assignment_id": assignment_id, "status": srow[1],
        "score": overall, "total_questions": total, "questions_answered": answered,
        "feedback": feedback,
        "components": components,
        "question_results": q_results,
        "completed_at": srow[2].isoformat() if srow[2] else None,
    }


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
                    cur.execute(
                        """SELECT mv.material_version_id, mv.file_name, count(*)
                           FROM document_concept dc
                           JOIN material_version mv ON mv.material_version_id = dc.material_version_id
                           WHERE dc.course_id = %s::uuid AND dc.org_id = %s::uuid
                           GROUP BY mv.material_version_id, mv.file_name
                           ORDER BY mv.file_name""",
                        (course_id, caller.org_id))
                    docs = [{"material_version_id": str(r[0]), "file_name": r[1], "concept_count": r[2]}
                            for r in cur.fetchall()]
                return {"documents": docs}
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
        # Generous token ceiling so the whole JSON returns in ONE call — truncation
        # here yields invalid JSON, which call_bedrock retries 3x, and three large
        # LLM calls blow past the 60s ALB idle timeout (surfaces as "Load failed").
        data = call_bedrock(settings, system_prompt, user,
                            max_tokens=8000, temperature=0.6)
    except Exception as exc:  # noqa: BLE001 - degrade to stored bank, assignment still works
        logger.warning("per-assignment question generation failed: %s", exc)
        return _concept_banks(concepts, difficulty)

    # Deterministic parse/merge lives in exam_questions (web-free + unit-tested).
    return merge_generated_banks(concepts, data, difficulty)


def _generate_expected_paths(settings, questions: list, concepts: list, relations: list) -> dict:
    """Claude-generate an expected reasoning path per question (for EDS scoring).

    Grounds nodes/edges in the course concept graph. Returns {index: {nodes,edges,extensions}};
    empty on any failure so assignment still succeeds (EDS just degrades on those questions).
    """
    if not questions:
        return {}
    concept_lines = "\n".join(f"- {c.get('label')}: {c.get('definition', '')}" for c in concepts)
    rel_lines = "\n".join(
        f"- {r.get('src')} {r.get('edge_type')} {r.get('dst')}" for r in relations)
    q_lines = "\n".join(f"{i}. {q.q}" for i, q in enumerate(questions))
    system_prompt = (
        "For each oral-exam question, produce the EXPECTED REASONING PATH a strong answer must "
        "demonstrate, grounded in the course concept graph. For each question return: "
        '"nodes" (key concepts that must be DEMONSTRATED with understanding, each {"label","definition"}), '
        '"edges" (causal links that must be ARTICULATED, each {"src","dst",'
        '"link_type":"CAUSES|ENABLES|PREVENTS|INCREASES|DECREASES","explanation"}), and '
        '"extensions" (1-3 bonus concepts, each {"label","connection"}). Prefer concepts from the graph. '
        'Return ONLY JSON: {"paths": [{"index": 0, "nodes": [...], "edges": [...], "extensions": [...]}]}'
    )
    user = (f"Concept graph:\n{concept_lines}\n\nRelations:\n{rel_lines}\n\n"
            f"Questions:\n{q_lines}")
    try:
        data = call_bedrock(settings, system_prompt, user,
                            max_tokens=LLM_MAX_TOKENS_GENERATION, temperature=0.2)
        out = {}
        for p in (data.get("paths") or []):
            idx = p.get("index")
            if isinstance(idx, int):
                out[idx] = {"nodes": p.get("nodes", []), "edges": p.get("edges", []),
                            "extensions": p.get("extensions", [])}
        return out
    except Exception as exc:  # noqa: BLE001 - EDS degrades, assignment still succeeds
        logger.warning("expected_path generation failed: %s", exc)
        return {}


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
                }.get(req.difficulty, "Generate questions that mix recall with causal reasoning.")

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
                    f"Domain: {req.domain}\n"
                    f"Difficulty: {req.difficulty}\n"
                    f"Number of questions to generate: {req.count}\n\n"
                )

                if concept_descriptions:
                    user_prompt += f"Concept graph (topics to examine):\n{concept_descriptions}\n\n"

                if combined_chunks:
                    user_prompt += f"Source material:\n{combined_chunks}\n\n"

                user_prompt += (
                    f"Generate exactly {req.count} oral exam questions. "
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

                # Batch insert: collect all valid questions, then insert in one executemany call
                stored_questions = []
                insert_params = []
                for q in questions_raw:
                    if not isinstance(q, dict) or not q.get("question"):
                        continue
                    question_id = str(_uuid.uuid4())
                    topic = q.get("topic", "general")
                    text = q["question"]
                    diff = q.get("difficulty", req.difficulty)
                    concept_ids = q.get("concept_ids", [topic])
                    expected_path = q.get("expected_path", {})

                    difficulty_json = _json.dumps({
                        "level": diff,
                        "eds_score": {"recall": 0.3, "balanced": 0.55, "deep": 0.8}.get(diff, 0.55),
                    })

                    insert_params.append((
                        question_id, course_id, caller.org_id,
                        _json.dumps(concept_ids), text,
                        "oral", difficulty_json, caller.user_id,
                        _json.dumps(expected_path),
                    ))

                    stored_questions.append({
                        "question_id": question_id,
                        "topic": topic,
                        "question": text,
                        "difficulty": diff,
                        "concept_ids": concept_ids,
                        "expected_path": expected_path,
                        "status": "draft",
                    })

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
        MAX_REGEN_CONCEPTS = 20

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
                # Expected reasoning paths are filled in lazily on the first answer
                # to each question (see submit_answer's backfill). Generating them
                # synchronously here — one large Claude call for every question —
                # blew past the 60s ALB idle timeout and 504'd the assign request.
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
                                   'active', %s)""",
                        (assignment_id, course_id, caller.org_id, req.title, qs_id, cfg, caller.user_id),
                    )
                    # Practice / assignment / exam — set only if the column exists
                    # (migration_008), so an un-migrated DB still assigns fine.
                    cur.execute("""SELECT 1 FROM information_schema.columns
                                   WHERE table_name='assignment' AND column_name='assignment_type'""")
                    if cur.fetchone() is not None:
                        cur.execute("UPDATE assignment SET assignment_type = %s WHERE assignment_id = %s::uuid",
                                    (req.assignment_type, assignment_id))
                repo.conn.commit()
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
                           WHERE es.assignment_id = %s::uuid
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
                return _query_exam_results(repo, assignment_id, caller.user_id)
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
                        """SELECT assignment_id, course_id, question_set_id, config, status
                           FROM assignment WHERE assignment_id = %s::uuid""",
                        (assignment_id,),
                    )
                    arow = cur.fetchone()

                if not arow:
                    raise AuthorizationError("assignment not found")
                if arow[4] != "active":
                    raise AuthorizationError(f"assignment is not active (status: {arow[4]})")

                course_id = str(arow[1])
                question_set_id = str(arow[2])

                # Atomic upsert: INSERT with ON CONFLICT prevents duplicate active
                # sessions even under concurrent requests from the same student.
                session_id = str(_uuid.uuid4())
                with repo.conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO exam_session
                           (session_id, assignment_id, student_id, org_id, course_id,
                            status, current_turn_index, questions_delivered, concepts_covered)
                           VALUES (%s::uuid, %s::uuid, %s, %s::uuid, %s::uuid,
                                   'active', 0, '[]'::jsonb, '[]'::jsonb)
                           ON CONFLICT (assignment_id, student_id)
                              WHERE status = 'active'
                           DO NOTHING
                           RETURNING session_id""",
                        (session_id, assignment_id, caller.user_id,
                         caller.org_id, course_id),
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

                # Questions generated before expected_path existed have none; build it
                # once on first use and commit, or every turn pays for a Bedrock call.
                if not expected_path.get("nodes"):
                    try:
                        expected_path = _generate_expected_path(
                            settings, question_text, concept_ids_for_question
                        )
                        if expected_path.get("nodes"):
                            with repo.conn.cursor() as cur:
                                cur.execute(
                                    "UPDATE question SET expected_path = %s::jsonb WHERE question_id = %s::uuid",
                                    (_json.dumps(expected_path), question_id),
                                )
                            repo.conn.commit()
                            logger.info("Generated expected_path for question %s: %d nodes, %d edges",
                                        question_id[:8], len(expected_path.get("nodes", [])),
                                        len(expected_path.get("edges", [])))
                        else:
                            logger.warning("expected_path generation returned no nodes for question %s "
                                           "— EDS will score 0", question_id[:8])
                    except Exception as exc:
                        logger.warning("expected_path generation failed for question %s: %s",
                                       question_id[:8], exc)
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
                    system_prompt = (
                        "You are an Epistemy Socratic oral examiner performing two tasks:\n\n"
                        "TASK 1: SOCRATIC EVALUATION\n"
                        f"The exam question is: \"{question_text}\"\n"
                        "Evaluate the student's answer. When adequate=false, provide a scaffolding probe.\n\n"
                        "TASK 2: EDS COMPONENT EXTRACTION\n"
                        "Given the expected reasoning path below, identify which concepts and causal links "
                        "the student DEMONSTRATED WITH UNDERSTANDING (not just named).\n\n"
                        f"EXPECTED PATH:\n{_json.dumps(expected_path)}\n\n"
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
                        + probe_directive + "\n"
                        "Respond ONLY with minified JSON, no prose, no code fences:\n"
                        '{"clarify": false, "answered": true, "adequate": false, '
                        '"feedback": "one sentence", "probe": "follow-up question", '
                        '"eds": {"nodes_demonstrated": ["list of node labels demonstrated"], '
                        '"edges_demonstrated": [0, 1], '
                        '"recitation_score": 0.3, '
                        '"novel_extensions": ["any valid concepts beyond expected path"]}}'
                    )
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

                answered = False
                adequate = False
                feedback = ""
                probe = ""
                eds_delta = 0
                parsed = {}

                try:
                    parsed = call_bedrock(
                        settings, system_prompt, ctx,
                        max_tokens=LLM_MAX_TOKENS_EVALUATION, temperature=0.1,
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
                    # Flag before the rebuild so a failed/killed thread leaves the
                    # graph visibly stale rather than silently wrong.
                    cur.execute(
                        "UPDATE graph_version SET is_stale = true "
                        "WHERE org_id = %s AND course_id = %s AND is_active = true",
                        (caller.org_id, course_id),
                    )
                repo.conn.commit()

                # Concepts from this material would otherwise persist in the active
                # graph; rebuild off-request so delete stays fast.
                _rebuild_graph_async(d["settings"], caller.org_id, course_id)

                return {"deleted": True, "material_id": target_id,
                        "graph_rebuild": "started"}
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
    feedback = "Good attempt." if not adequate else ""
    probe = "Can you explain the causal mechanism behind your answer?" if not adequate else ""
    return True, adequate, feedback, probe


app = create_app()
