# Schema Registry — Agentic EdTech Platform

**Purpose:** A condensed, single-page map of every schema/table, the datastore it lives in, and the owning service/milestone. This is a subset of the [HLD](./HLD-Agentic-EdTech-Platform.md) intended for a quick principal-engineer spot check — not a full design doc.

**Last updated:** 2026-06-22 · **Source of truth:** HLD-Agentic-EdTech-Platform.md + `m3-content-ingestion/` + `concept-graph-uthiraingest/`

---

## Datastore legend

| Store | Engine | Role |
|-------|--------|------|
| **Aurora** | Aurora Serverless v2 (Postgres) + `pgvector`, RLS on `org_id` | System of record — all relational data |
| **S3** | Object storage, prefix-partitioned by `org_id/course_id` | Raw materials, `.kuzu` graph files, prompt/output archives, audit bodies |
| **Kuzu** | Embedded property graph (one `.kuzu` file per course-version on S3) | The Concept Graph (concepts + typed edges + evidence) |
| **Cognito** | Managed identity | Credentials, MFA, JWT signing |
| **Bedrock** | Managed LLM + embeddings | Stateless inference (no persistent storage) |
| **DynamoDB** | Managed KV/NoSQL | *(deferred)* live-exam autosave drafts |
| **Qdrant** | Vector DB *(prototype, concept-graph workstream)* | Concept/subgraph/assessment embeddings |

> Note: Two implementations exist for the graph workstream. The **HLD-locked design** uses Aurora `graph_version` metadata + `.kuzu` files on S3. The **`concept-graph-uthiraingest/` prototype** uses a separate Postgres metadata schema + Kuzu + Qdrant. Both are listed below and flagged.

---

## S1 — User & Auth / Course & Enrollment

**Owning module:** M1 (User & Auth), M2 (Course & Enrollment) · **Store:** Aurora (RLS by `org_id`) + Cognito (identity)

| Table | Store | Key fields | Notes |
|-------|-------|-----------|-------|
| `org` | Aurora | `org_id (PK)`, `name`, `email_domain`, `sso_config (jsonb)` | A university (tenant). One row at pilot. |
| `user_profile` | Aurora | `user_id (PK = Cognito id)`, `org_id (FK)`, `email`, `role{professor,student}`, `display_name`, `status{approved,pending}`, `created_at` | Mirrors the Cognito user. |
| `course` | Aurora | `course_id (PK)`, `org_id (FK)`, `owner_prof_id (FK→user)`, `title`, `term`, `join_code`, `status` | Single professor at MVP. |
| `enrollment` | Aurora | `(student_id, course_id) (composite PK)`, `enrolled_at`, `status` | Many-to-many student↔course bridge. |
| `course_instructor` *(future)* | Aurora | `(course_id, prof_id) (composite PK)`, `role{owner,co_instructor,ta}`, `added_at` | Deferred — enables co-teaching. Non-breaking add. |

**Identity (no app table):** Cognito User Pool holds credentials, MFA, JWT signing keys.

---

## S2 — Backend Foundations (cross-cutting)

**Owning module:** S2 · **Store:** Aurora

| Table | Store | Key fields | Notes |
|-------|-------|-----------|-------|
| `async_job` | Aurora | `job_id (PK)`, `org_id (FK)`, `course_id (FK, nullable)`, `type{ingest,graph_build,question_gen,grade}`, `status{queued,running,succeeded,failed}`, `progress_pct`, `step_name`, `created_by`, `created_at`, `updated_at`, `error (jsonb)` | Single source of truth for any async pipeline. Module tables FK to `job_id`. |

> Tool-call audit log (every REST + MCP invocation w/ cost/latency/outcome) is owned by the Security module (X1) — not yet schematized.

---

## M3 — Content Ingestion ✅ (implemented: `m3-content-ingestion/backend/db/schema.sql`)

**Owning module:** M3 · **Store:** Aurora (RLS by `org_id`) + S3 (raw bytes) · **Inference:** Bedrock Titan Text Embeddings v2 (1024-dim)

| Table | Store | Key fields | Notes |
|-------|-------|-----------|-------|
| `material` | Aurora | `material_id (PK)`, `course_id (FK)`, `org_id (FK)`, `created_by (FK→user)`, `display_name`, `current_version_id (FK→material_version, nullable)`, `created_at`, `updated_at` | The logical material. RLS by `org_id`. |
| `material_version` | Aurora | `material_version_id (PK)`, `material_id (FK)`, `course_id (FK)`, `org_id (FK)`, `version_no`, `uploaded_by (FK→user)`, `source_type`, `mime_type`, `file_name`, `s3_key`, `bytes`, `checksum`, `status{pending,uploaded,extracting,chunking,embedding,ready,failed}`, `error (jsonb)`, `ingest_job_id (FK→async_job)`, `superseded_at`, `created_at`, `updated_at` | One row per upload. `UNIQUE(material_id, version_no)`. Same-checksum re-upload short-circuits. |
| `chunk` | Aurora | `chunk_id (PK)`, `material_version_id (FK)`, `course_id (FK)`, `org_id (FK)`, `chunk_index`, `text`, `token_count`, `position (jsonb)`, `embedding vector(1024)` | Unit of retrieval. `UNIQUE(material_version_id, chunk_index)`. IVFFlat cosine index (`lists=1000`). |

**S3 layout:** `s3://{bucket}/{org_id}/{course_id}/materials/{material_id}/v{version_no}/{file_name}`

**Implementation cross-check:** `schema.sql` matches the HLD. RLS enabled on all three tables via `app.org_id` session var; `vector` extension + IVFFlat index present. ⚠️ Minor: `chunk` has no explicit `org_id` FK constraint to `org` (RLS-enforced only) — consistent with HLD intent.

---

## M4 — Concept Graph 🚧 (HLD placeholder + `concept-graph-uthiraingest/` prototype)

**Owning module:** M4 · **Store:** Kuzu (`.kuzu` on S3) + Aurora `graph_version` metadata

### HLD-locked design (target)

| Table / Artifact | Store | Key fields | Notes |
|------------------|-------|-----------|-------|
| `graph_version` | Aurora | active-version pointer, `validation_score`, build `job_id`, `is_active` | Small metadata; other tables FK to it. Atomic `is_active` flip on rebuild. |
| Concept Graph file | S3 | `s3://.../{org}/{course}/graph/{version}.kuzu` | Source of truth. Versioned per build; old versions retained for rollback. |
| `Concept` nodes / typed edges | Kuzu | edge types: `prereq_of`, `part_of`, `causes`, `defined_by`, `applies_to`, `related_to` | v1 taxonomy (not yet locked). |

### Prototype implementation (`concept-graph-uthiraingest/`) — ⚠️ diverges from HLD, reconcile before build

> **`org_id` recommendation applied below.** The prototype currently has **no `org_id` anywhere**. The "+`org_id`/`course_id`" annotations mark fields that should be added — these are not cosmetic; they fix a real tenant-isolation and concept-bleed bug (see *What's the problem with M4* below).

**Kuzu property graph** (`schema/kuzu_schema.py`):

| Node/Rel table | Store | Key fields | Add for isolation |
|----------------|-------|-----------|-------------------|
| `Concept` (node) | Kuzu | `node_id (PK)`, `label`, `definition`, `domain`, `graph_type`, `aliases`, `abstraction_level`, `depth_level`, `status`, `corpus_id`, `version` | **+`org_id`, +`course_id`** |
| `Assessment` (node) | Kuzu | `node_id (PK)`, `content`, `professor_id`, `course_id`, `corpus_id`, `assessment_type`, `depth_level`, `version` | **+`org_id`** (has `course_id`) |
| `CorpusDoc` (node) | Kuzu | `corpus_id (PK)`, `title`, `domain`, `source_url`, `version` | **+`org_id`, +`course_id`** |
| `ConceptEdge` (rel) | Kuzu | `Concept→Concept`, `edge_id`, `edge_type` (e.g. `PREREQUISITE_FOR`, `ENABLES`), `confidence`, `corpus_id`, `version` | **+`org_id`** |
| `AssessmentUsesConcept` (rel) | Kuzu | `Assessment→Concept`, `weight` | — |
| `DocMentionsConcept` (rel) | Kuzu | `CorpusDoc→Concept`, `frequency` | — |

**Postgres metadata** (`schema/postgres_schema.py`) — ⚠️ separate from HLD Aurora schema, **no RLS**:

| Table | Store | Key fields | Add for isolation |
|-------|-------|-----------|-------------------|
| `corpus_registry` | Postgres | `corpus_id (PK)`, `title`, `domain`, `source_url`, `professor_id`, `course_id`, `status`, `created_at` | **+`org_id` (FK) + RLS** |
| `ingestion_jobs` | Postgres | `job_id (PK serial)`, `corpus_id (FK)`, `job_type`, `status`, `attempts`, `last_error` — `FOR UPDATE SKIP LOCKED` queue | **+`org_id` + RLS** |
| `chunk_cache` | Postgres | `content_hash (PK)`, `corpus_id`, `chunk_index` — content-hash dedup | **+`org_id`** (scope dedup per tenant) |
| `eds_results` | Postgres | `run_id (PK serial)`, `assessment_id`, `student_id`, `eds_score`, `components (jsonb)`, `probe_model` | **+`org_id`, +`course_id` + RLS** (holds student data — FERPA) |
| `causal_probe_results` | Postgres | `probe_id (PK serial)`, `assessment_id`, `probe_model`, `direct_score`, `paraphrase_score`, `raw_response` | **+`org_id` + RLS** |
| `graph_versions` | Postgres | `version_id (PK serial)`, `corpus_id`, `domain`, `node_count`, `edge_count`, `snapshot (jsonb)` | **+`org_id`, +`course_id` + RLS** |

**Qdrant vector store** (`schema/vector_store.py`) — 3 collections, 384-dim, cosine:

| Collection | Store | Payload-indexed fields | Add for isolation |
|------------|-------|------------------------|-------------------|
| `concept_nodes` | Qdrant | `domain`, `graph_type`, `status`, `corpus_id`, `depth_level` | **+`org_id`, +`course_id`** (scope dedup search) |
| `concept_subgraphs` | Qdrant | `domain`, `graph_type`, `corpus_id` (mean-pooled k-hop neighborhood) | **+`org_id`, +`course_id`** |
| `assessment_items` | Qdrant | `professor_id`, `course_id`, `corpus_id`, `eds_score_bucket` | **+`org_id`** (has `course_id`) |

### Does `org_id` add extra benefit in M4? — Yes

It is not just consistency-for-consistency's-sake. Adding `org_id` (and `course_id`) to M4 buys three concrete things:

1. **Fixes a real concept-bleed bug.** Dedup and similarity search (`ConceptDeduplicator` → `search_similar_concepts(label, domain)`) filter only by `domain`. Two universities both teaching "machine learning" would **match and merge each other's concept nodes** — a cross-tenant data leak and a correctness bug. An `org_id`/`course_id` filter on the Qdrant payload scopes dedup to one tenant.
2. **Restores FERPA tenant isolation.** Every other tier (M3, S1, S2) enforces RLS on `org_id` (HLD tenet 2: "course data isolation is non-negotiable, enforced at the data layer"). `eds_results` holds `student_id` + scores with no `org_id` and no RLS today — student records sitting outside the isolation model.
3. **Enables scoped queries + audit.** Per-tenant graph reads, per-course rebuild, and the tool-call audit trail all need `org_id` on the row to filter and log correctly.

> If the HLD's **per-course `.kuzu` file** design is adopted (one physical file per `org/course`), the Kuzu nodes get physical isolation "for free" and in-node `org_id` becomes defense-in-depth rather than the primary control. But the **Postgres metadata** and **Qdrant collections** are shared across tenants regardless, so `org_id` + RLS / payload filter there is mandatory, not optional.

### What's the problem with M4?

The M4 prototype is a working graph-build pipeline, but it was built as a **standalone research stack** and does not yet line up with the platform the rest of the HLD describes. The gaps, in priority order:

| # | Problem | Why it matters |
|---|---------|----------------|
| 1 | **No tenant isolation.** No `org_id`, no RLS anywhere (Kuzu, Postgres, Qdrant). | Violates the non-negotiable isolation tenet; FERPA risk. Student data in `eds_results` is unscoped. |
| 2 | **Cross-tenant concept bleed.** Dedup/similarity scoped only by `domain`. | Different orgs/courses in the same domain silently share or merge concept nodes — leak + correctness bug. |
| 3 | **Doesn't consume M3's output.** Re-chunks raw corpus text with its own `TextChunker`/`chunk_cache` instead of reading M3's `chunk` table. | Duplicate chunking; loses M3's structure-aware `position` metadata; two sources of truth for "the text." |
| 4 | **Wrong keying model.** Keys on `corpus_id`; M3/HLD key on `org_id`/`course_id`/`material_version_id`. No link to `material_version`. | Graph evidence can't be pinned to a content version → breaks the grade-dispute reproducibility M3 was designed to guarantee. |
| 5 | **Storage diverges from HLD.** Single shared Kuzu DB + Postgres `graph_versions` JSONB adjacency dump, instead of per-course `.kuzu` files on S3 + thin Aurora `graph_version` pointer. | No per-course physical isolation, no atomic `is_active` flip, no clean rollback story. Snapshot duplicates what Kuzu already holds. |
| 6 | **Two embedding stacks.** 384-dim MiniLM/Qdrant here vs. 1024-dim Bedrock Titan v2 / `pgvector` in M3. | Duplicate vector infra and cost; embeddings not comparable across modules. |
| 7 | **Parallel job system.** Own `ingestion_jobs` queue instead of the platform `async_job` table + SQS. | Two job/status systems; the web app's `/jobs/{id}` polling can't see graph builds. |
| 8 | **Weak versioning.** `version` is a bare int on nodes/edges; no active-version pointer. | Can't atomically activate/roll back a graph build the way M3 flips `current_version_id`. |

**Bottom line:** M4 is still a 🚧 placeholder in the HLD for a reason. The prototype proves the build algorithm (extract → dedup → Kuzu → embed → topological depth → EDS), but before it ships it needs to (a) adopt `org_id`/`course_id` + RLS, (b) read M3 chunks instead of re-chunking, (c) key on `material_version_id` for reproducibility, and (d) move to the HLD's per-course `.kuzu`-on-S3 + `graph_version`-pointer storage. Items 1 and 2 are the ones a principal engineer should block on.

---

## M5 — Question Generation ⬜ (not started)

**Owning module:** M5 · **Store:** Aurora (per S2 data layer list) + S3 (prompt/output archives)

| Table | Store | Status |
|-------|-------|--------|
| `question` | Aurora | Schema not yet defined. Listed in S2 data-layer inventory. |

---

## M6 — Assignment Delivery ⬜ (not started)

**Owning module:** M6 · **Store:** Aurora (+ DynamoDB deferred for autosave)

| Table | Store | Status |
|-------|-------|--------|
| `assignment` | Aurora | Schema not yet defined (S2 inventory). |
| `exam_session` | Aurora | Schema not yet defined (S2 inventory). Resumable, stateful session. |
| `session_turn` | Aurora | Schema not yet defined (S2 inventory). Per-question turn record. |
| `live_drafts` *(deferred)* | DynamoDB | Promotion target when autosave volume exceeds Postgres comfort. |

---

## M7 — Evaluation ⬜ (not started)

**Owning module:** M7 · **Store:** Aurora + S3 (evaluation prompt/output archives)

| Table | Store | Status |
|-------|-------|--------|
| `evaluation` | Aurora | Schema not yet defined (S2 inventory). Pinned-version reads for grade reproducibility. |
| `grade` | Aurora | Schema not yet defined (S2 inventory). |

---

## X1 — Cross-cutting (Security / FERPA / Audit) ⬜ (not started)

**Owning module:** X1 · **Store:** Aurora (metadata) + S3 (payload bodies)

| Table | Store | Status |
|-------|-------|--------|
| Tool-call audit log | Aurora + S3 | Every REST + MCP invocation w/ identity, cost, latency, outcome. Schema not yet defined. |

---

## Quick spot-check summary

| Milestone | Store(s) | Tables defined? | Implemented in code? |
|-----------|----------|-----------------|----------------------|
| S1 User/Course | Aurora + Cognito | ✅ HLD | not in this repo |
| S2 Foundations | Aurora | ✅ HLD (`async_job`) | not in this repo |
| M3 Ingestion | Aurora + S3 + Bedrock | ✅ HLD | ✅ `schema.sql` (matches) |
| M4 Concept Graph | Kuzu + S3 + Aurora | 🚧 HLD placeholder | ⚠️ prototype (Kuzu+Postgres+Qdrant, diverges) |
| M5 Questions | Aurora + S3 | ⬜ inventory only | ❌ |
| M6 Delivery | Aurora (+DynamoDB) | ⬜ inventory only | ❌ |
| M7 Evaluation | Aurora + S3 | ⬜ inventory only | ❌ |
| X1 Security | Aurora + S3 | ⬜ named only | ❌ |
