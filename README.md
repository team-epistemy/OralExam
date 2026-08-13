# Epistemy M3 — Content Ingestion

Turns uploaded course material into chunked, embedded text the Concept Graph (M4) can use.

Implements the MVP cut from `M3-tasks.md`: **T1, T2, T3, T4-lite, T5, T6, T7, T9, T14**.

## Layered layout

```
backend/
├── config.py              Settings: account, region, bucket, queue, KMS, model ids
├── models/                Pydantic schemas + enums (material, version, chunk, tool I/O)
├── storage/               S3 key builder + presigned PUT (T3)
├── db/                    Postgres schema (RLS + pgvector), repos, RLS session vars (T1, T6)
│   └── schema.sql         material, material_version, chunk + RLS + IVFFlat index
├── extract/               Per-format extractors: Markdown (T7), PPTX (T9)
├── chunking/              Structure-aware chunker library, swappable tokenizer (T5)
├── embedding/             Bedrock Titan v2 embeddings + idempotent persistence (T6)
├── async_jobs/            SQS queue, async_job repo, worker dispatcher + pipeline (T4-lite, T7)
├── api/                   presign + register endpoints, professor RBAC (T3)
└── tools/                 upload_material, list_materials, get_material,
                           list_material_versions — shared REST/MCP handlers (T14)

infra/
├── provision.py           boto3 provisioner: KMS, S3 bucket+policy, SQS, IAM role (T2, T4-lite)
└── policies.py            bucket policy + IAM trust/permission documents

tests/                     Local tests — run with in-memory fakes, no AWS needed
```

## Design rules honored

- Every function is **≤ 15 lines**; comments are **≤ 2 lines**.
- Pure-Python domain core (chunking, extraction) has no AWS/DB/IO coupling.
- AWS and DB clients are injected; in-memory fakes let the whole pipeline run offline.
- Tenant isolation enforced four ways: RBAC, presigned-key binding, Postgres RLS, S3 bucket policy.

## Quick start (offline, no AWS)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. pytest -q
```

```python
from backend.async_jobs.worker import IngestWorker
from backend.testing.fakes import build_offline_worker

worker = build_offline_worker()                       # in-memory S3, DB, queue, embedder
result = worker.run_demo_markdown("org_acme", "course_cs101")
print(result["status"])                               # -> ready
```

## Deploy to AWS (account 881432542692)

Run from a host with admin credentials for the account:

```bash
export AWS_REGION=us-east-1
PYTHONPATH=. python -m infra.provision --account 881432542692 --env dev
```

This creates: a customer-managed KMS key (rotation on), the materials S3 bucket
(SSE-KMS, versioning, public access blocked, prefix-bound bucket policy), the
`ingest` SQS queue, and the prefix-scoped IAM role for the HTTP/Worker processes.

> Note: Aurora Postgres + `pgvector` and the Bedrock model grants are account-level
> resources; apply `db/schema.sql` to the Aurora cluster after it is reachable.
