# Epistemy — Complete System View

## Data Flow (end-to-end)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            PROFESSOR FLOW                                         │
│                                                                                   │
│  ① Upload PDF ──► S3 presigned PUT                                               │
│        │                                                                          │
│  ② Register ──► SQS enqueue                                                      │
│        │                                                                          │
│  ③ Worker picks up job:                                                           │
│        ├─ Download from S3                                                        │
│        ├─ Extract text (PyPDF/PPTX/DOCX/Markdown)                                │
│        ├─ Structure-aware chunking (800 tokens, overlap)                          │
│        ├─ Embed via Bedrock Titan v2 (1024 dims) ──► pgvector                    │
│        ├─ Status: ready                                                           │
│        └─ AUTO-TRIGGER: Graph build ──► Qwen3 32B (concept extraction)           │
│                                              │                                    │
│  ④ Professor views Concept Graph             ▼                                    │
│        └─ Concepts + Relations stored in graph_version                            │
│                                                                                   │
│  ⑤ Generate Questions ──► Qwen3 32B                                              │
│        └─ Input: concepts + chunks ──► Socratic oral exam questions               │
│        └─ Stored in question table                                                │
│                                                                                   │
│  ⑥ Create Assignment                                                             │
│        └─ Select questions ──► assignment + question_set created                  │
│                                                                                   │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│                            STUDENT FLOW                                           │
│                                                                                   │
│  ⑦ Student starts exam ──► exam_session created                                  │
│        └─ Returns all questions for the assignment                                │
│                                                                                   │
│  ⑧ Student answers question ──► session_turn recorded                            │
│        │                                                                          │
│        ├─ Qwen3 32B evaluates (Socratic):                                        │
│        │   - Is it a clarification request?                                       │
│        │   - answered? adequate?                                                  │
│        │   - If inadequate: generates probing follow-up                           │
│        │   - EDS delta scored                                                     │
│        │                                                                          │
│        ├─ evaluation row stored                                                   │
│        └─ Returns: {answered, adequate, feedback, probe, eds_delta}               │
│                                                                                   │
│  ⑨ Student submits exam ──► session status = completed                           │
│                                                                                   │
│  ⑩ Professor releases grades ──► grade rows computed from evaluations            │
│                                                                                   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Database Schema (Aurora Postgres + RLS)

All tables have `org_id` with Row-Level Security: `current_setting('app.org_id')::uuid = org_id`

### S2 — Foundations

| Table | Key Fields | Purpose |
|-------|-----------|---------|
| `org` | org_id (PK), org_name (UNIQUE) | Tenant (university) |
| `course` | course_id (PK), org_id (FK), course_name, UNIQUE(org_id, course_name) | Course within an org |
| `async_job` | job_id (PK), org_id, course_id, type, status, progress_pct, step_name, error (jsonb) | Tracks async pipeline jobs |

### M3 — Content Ingestion

| Table | Key Fields | Purpose |
|-------|-----------|---------|
| `material` | material_id (PK), course_id (FK), org_id (FK), display_name, current_version_id (FK) | Logical material ("Lecture 1") |
| `material_version` | material_version_id (PK), material_id (FK), version_no, source_type, s3_key, status (pending→uploaded→extracting→chunking→embedding→ready\|failed), ingest_job_id (FK) | One row per upload |
| `chunk` | chunk_id (PK), material_version_id (FK), course_id, chunk_index, text, token_count, position (jsonb), embedding vector(1024), UNIQUE(material_version_id, chunk_index) | Unit of retrieval. IVFFlat cosine index. |

### M4 — Concept Graph

| Table | Key Fields | Purpose |
|-------|-----------|---------|
| `graph_version` | version_id (PK, UUID), org_id (FK), course_id (FK), graph_version (INT), node_count, edge_count, validation_score, is_active (BOOL), s3_key (stores JSON: {concepts, relations}), created_at | One active graph per course. Stores extracted concepts + relationships. |

### M5 — Question Generation

| Table | Key Fields | Purpose |
|-------|-----------|---------|
| `question` | question_id (PK, UUID), course_id (FK), org_id (FK), topic, question (TEXT), question_type (oral), difficulty (recall\|balanced\|deep), concept_ids (jsonb), status (draft\|approved\|rejected) | Generated Socratic questions |
| `question_set` | question_set_id (PK), org_id, course_id, title | Groups questions for an assignment |
| `question_set_membership` | question_set_id (FK), question_id (FK), org_id | Links questions to sets |

### M6 — Assignment Delivery

| Table | Key Fields | Purpose |
|-------|-----------|---------|
| `assignment` | assignment_id (PK, UUID), course_id (FK), org_id (FK), title, question_set_id (FK), config (jsonb: difficulty, duration_minutes, adaptive), status (active\|closed), created_by | Professor-created exam |
| `exam_session` | session_id (PK, UUID), assignment_id (FK), student_id, org_id, course_id, status (active\|completed\|abandoned), current_turn_index, started_at, completed_at | One session per student per attempt |
| `session_turn` | turn_id (PK, UUID), session_id (FK), org_id, turn_index, question_id (FK), student_answer (TEXT), answered_at, time_spent_seconds | Each answer submitted |

### M7 — Evaluation

| Table | Key Fields | Purpose |
|-------|-----------|---------|
| `evaluation` | evaluation_id (PK, UUID), turn_id (FK), org_id, course_id, student_id, question_id, answered (BOOL), adequate (BOOL), feedback (TEXT), probe (TEXT), eds_delta (INT), raw_llm_output (jsonb), evaluated_at | Socratic evaluation per turn |
| `grade` | grade_id (PK, UUID), session_id (FK), student_id, assignment_id, org_id, course_id, final_score, component_scores (jsonb), override_by (FK, nullable), override_reason, status (pending\|released), released_at | Final grade per session |

---

## Infrastructure (AWS account 883353268066, us-west-2)

| Resource | Identifier | Purpose |
|----------|-----------|---------|
| ECS Fargate Service | `epistemy-m3-dev` on cluster `epistemy-dev` | Runs http (FastAPI :8080) + worker (SQS consumer) |
| ALB | `epistemy-m3-int` → `epistemy-m3-int-571630445.us-west-2.elb.amazonaws.com` | Public HTTP:80 endpoint |
| RDS PostgreSQL 18.3 | `epistemy-process-db` (db.t4g.micro) | System of record (15 tables + pgvector + RLS) |
| S3 Bucket | `epistemy-materials-dev-usw2-883353268066` | Raw materials, SSE-KMS, tenant-prefix policy |
| SQS Queue | `epistemy-ingest-dev` | Async ingest job queue |
| KMS Key | `alias/epistemy-materials-dev` | Encryption for S3 |
| ECR | `epistemy-m3:latest` | Docker image repository |
| Cognito | `us-west-2_sHF8IZp5L` | User pool (deployed, not yet wired to frontend) |
| CloudWatch | `/epistemy/m3/dev` | Logs |
| IAM Roles | `epistemy-m3-task-dev`, `epistemy-m3-exec-dev`, `epistemy-m3-build-dev` | ECS task, execution, CodeBuild |

### AI / LLM (Bedrock)

| Model | Model ID | Use | Cost |
|-------|----------|-----|------|
| Amazon Titan Text Embeddings v2 | `amazon.titan-embed-text-v2:0` | Chunk embeddings (1024 dims) | ~$0.02/1M tokens |
| Qwen3 32B | `qwen.qwen3-32b-v1:0` | Concept extraction, question generation, Socratic evaluation | $0.15/$0.62 per 1M input/output tokens |

### S3 Layout

```
s3://epistemy-materials-dev-usw2-883353268066/
├── {org_id}/
│   └── {course_id}/
│       ├── materials/{material_id}/v{version_no}/{filename}
│       └── graph/{version}.tar.gz  (future — currently JSON in Postgres)
└── build/dev/source.zip  (CodeBuild source)
```

---

## API Endpoints (all live)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| POST | `/materials:presign` | Get S3 presigned upload URL |
| POST | `/versions/{id}/register` | Confirm upload, trigger ingest |
| GET | `/orgs/{org}/courses/{course}/materials` | List materials by name |
| GET | `/materials/{id}/versions` | List material versions |
| POST | `/courses/{id}/search` | Vector search over chunks (pgvector cosine) |
| GET | `/api/professor/dashboard` | Courses + uploads + active assignments |
| GET | `/api/professor/courses` | List courses for org |
| GET | `/api/student/dashboard` | Courses + active assignments for students |
| GET | `/api/courses/{id}/graph` | Get concept graph (concepts + relations) |
| POST | `/api/courses/{id}/graph/rebuild` | Trigger graph build via Qwen3 |
| POST | `/api/courses/{id}/questions/generate` | Generate Socratic questions via Qwen3 |
| GET | `/api/courses/{id}/questions` | List questions (optional ?status= filter) |
| POST | `/api/questions/{id}/approve` | Approve a draft question |
| POST | `/api/questions/{id}/reject` | Reject a draft question |
| POST | `/api/courses/{id}/assignments` | Create assignment from question IDs |
| GET | `/api/courses/{id}/assignments` | List assignments for a course |
| GET | `/api/assignments/{id}` | Get assignment detail |
| POST | `/api/assignments/{id}/start` | Student starts exam session |
| POST | `/api/sessions/{id}/answer` | Submit answer (Socratic evaluation via Qwen3) |
| GET | `/api/sessions/{id}/status` | Session state + per-turn EDS scores |
| GET | `/api/evaluations/{turn_id}` | Get evaluation for a specific turn |
| GET | `/api/grades/{session_id}` | Get grades for a session |
| POST | `/api/assignments/{id}/grades/release` | Compute + release grades |
| POST | `/api/grades/{id}/override` | Professor overrides a grade |

---

## Frontend Routes (localhost:5173)

| Role | Path | Page |
|------|------|------|
| — | `/login` | Login (mock auth: email containing "prof" → professor, else → student) |
| Professor | `/professor/dashboard` | Dashboard: courses, recent uploads, active assignments |
| Professor | `/professor/courses/:id` | Course detail with tabs: Materials, Concept Graph, Questions, Assignments |
| Professor | `/professor/upload` | Upload material (org + course name → S3 → ingest pipeline) |
| Professor | `/professor/assignments/new` | Create assignment: select course, pick questions, set config |
| Student | `/student/dashboard` | Active exams (all assignments in org) + courses |
| Student | `/student/exam/:assignmentId` | Take exam: multi-turn Socratic dialogue, question grid, EDS gauge |

---

## Deploy

```bash
cd /Users/uthira/Desktop/epistemy/m3-content-ingestion
EPISTEMY_ACCOUNT=883353268066 AWS_PROFILE=personal python3 -m infra.deploy_full
```

Steps: Build image (CodeBuild) → Apply migration (Fargate task) → Redeploy ECS service.

---

## What's Deferred

| Item | Notes |
|------|-------|
| Real Cognito auth | User pool exists, needs hosted UI or custom login wired to JWT validation |
| Course enrollment | Join codes, student↔course membership (M2) |
| Scanned PDF (Textract) | T8 — only native text PDFs supported currently |
| Audio/video (Transcribe) | T13 — oral answer via voice |
| SSE streaming | Currently REST polling for exam delivery |
| Voice input/output (STT/TTS) | v2 feature |
| Graph visualization (D3/Cytoscape) | Currently shows concept chips, not a graph diagram |
| Kuzu persistent storage | Currently JSON in Postgres; HLD targets .kuzu files on S3 |
| MCP layer | M8 — agentic tool exposure |
| Production infra | Private subnets, HTTPS, custom domain, WAF |
| FERPA audit log | X1 — tool-call logging |
| DLQ / retries | T4-hard — production-grade async |
| Cost guards | T17 — usage caps and dashboards |
