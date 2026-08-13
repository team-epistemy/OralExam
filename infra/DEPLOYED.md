# M3 — Deployed resources (account 883353268066, us-west-2)

Provisioned end-to-end via `AWS_PROFILE=personal python -m infra.provision --account 883353268066 --env dev`.

Public endpoint: **http://epistemy-m3-int-571630445.us-west-2.elb.amazonaws.com**
(`/health` returns `{"status":"ok"}`).

| Resource | Identifier |
|----------|-----------|
| KMS key (rotation on) | `arn:aws:kms:us-west-2:883353268066:key/2b14d444-a709-483e-b157-1ca88bcde675` |
| KMS alias | `alias/epistemy-materials-dev` |
| S3 bucket | `epistemy-materials-dev-usw2-883353268066` (SSE-KMS, versioning, public access blocked, tenant-prefix policy) |
| SQS queue | `https://sqs.us-west-2.amazonaws.com/883353268066/epistemy-ingest-dev` |
| ECR repo | `883353268066.dkr.ecr.us-west-2.amazonaws.com/epistemy-m3:latest` |
| IAM task role | `arn:aws:iam::883353268066:role/epistemy-m3-task-dev` |
| IAM execution role | `arn:aws:iam::883353268066:role/epistemy-m3-exec-dev` |
| IAM build role | `arn:aws:iam::883353268066:role/epistemy-m3-build-dev` |
| RDS PostgreSQL 18.3 (db.t4g.micro) | instance `epistemy-process-db`, endpoint `epistemy-process-db.cra6uky6mvqw.us-west-2.rds.amazonaws.com` |
| DB secret | `arn:aws:secretsmanager:us-west-2:883353268066:secret:epistemy/db-dev-SjoHDq` (username, password, dbname=`epistemy`, host, port) |
| Cognito user pool | `us-west-2_sHF8IZp5L`, app client `4nik8riq0mlavgvafbcqnjjvml` |
| ECS cluster | `epistemy-dev` |
| ECS Fargate service | `epistemy-m3-dev` (running, containers `http`:8080 + `worker`) |
| Internet-facing ALB | `epistemy-m3-int` → `epistemy-m3-int-571630445.us-west-2.elb.amazonaws.com` (HTTP:80) |
| Target group | `epistemy-m3-int-tg` (HTTP:8080, `/health`) — target healthy |
| CloudWatch log group | `/epistemy/m3/dev` |

## Status: running end-to-end

- Image built in CodeBuild (no local Docker) and pushed to ECR.
- `schema.sql` (material, material_version, chunk + RLS + pgvector IVFFlat, async_job)
  applied to RDS by a one-off Fargate `migrate` task — log shows `schema applied`.
- ALB target is healthy; `GET /health` over the public ALB returns `200 {"status":"ok"}`.

## How provisioning runs (one command)

`infra.provision` executes phases in order, each idempotent:
1. **core** — service-linked roles (ECS/ELB/RDS), KMS, S3 (+ bucket policy), SQS, task/exec IAM roles.
2. **image** — zip source → S3 → CodeBuild builds Dockerfile → push to ECR.
3. **foundations** — RDS PostgreSQL instance, Cognito user pool, DB secret (+ host).
4. **schema** — one-off Fargate task runs `migrate` to apply `schema.sql`.
5. **loadbalancer** — internet-facing ALB, target group, HTTP:80 listener.
6. **compute** — ECS cluster, task definition (http + worker), Fargate service bound to the target group.

## Account differences from the original (881432542692)

This is a **personal AWS account on the new Free Plan**, which drove three changes:
- **RDS instead of Aurora.** The Free Plan blocks standard Aurora and only allows
  express-config Aurora (IAM-only auth, no custom database, RDS-managed networking),
  which is incompatible with the app's password + `pgvector` + VPC-SG model. A
  standard RDS PostgreSQL instance (`db.t4g.micro`) is free-tier friendly and works
  with the app unchanged. To switch back to Aurora, upgrade the account plan and
  restore `foundations.ensure_postgres` → Aurora cluster creation.
- **Internet-facing ALB.** The internal-ALB + CloudFront-VPC-origin design only
  exists to dodge the Amazon-internal Epoxy control (which deletes HTTP:80 listeners
  on internet-facing ALBs). That control does not apply here, so the ALB serves
  HTTP:80 directly.
- **Service-linked roles** (ECS/ELB/RDS) are created up front; a fresh account does
  not have them until first use.

## Pilot simplifications (revisit for prod)

- Tasks run in the default VPC's public subnets with public IPs (no NAT). For
  prod, move to private subnets + NAT gateway and set `assignPublicIp=DISABLED`.
- The ALB is public and the API RBAC is currently open (`authorize -> True`) until
  Cognito JWT validation is wired; tenant isolation still holds via RLS and the
  bucket policy. Anyone with the ALB DNS can reach the API — add auth before real use.
- RDS, ECS, and the ALB share the default security group (self-referencing 5432 +
  8080, public 80).

## Region note — Bedrock

Compute runs in us-west-2. Titan Text Embeddings v2 is available in us-west-2,
so `EPISTEMY_BEDROCK_REGION` defaults to us-west-2 and `EPISTEMY_USE_BEDROCK=1`
is set on the task. Enable Titan v2 model access in the Bedrock console for the
account if it is not already granted.
