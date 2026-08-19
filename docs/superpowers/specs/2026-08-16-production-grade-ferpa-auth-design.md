# Production-Grade FERPA Auth — Design & Spec

**Date:** 2026-08-16  **Branch:** `feature/auth`  **DB:** `epistemy-process-db` (existing)

## Goal

Replace the hardcoded `_USERS` dict + inline HS256 JWT in `http_app.py` with Cognito-backed,
multi-tenant, FERPA-aligned authentication and authorization, on the existing DB.

## Non-goals

- SSO / IdP federation — design token-validation IdP-agnostically; wire federation later.
- COPPA / under-13 — segments are higher-ed and K-12 private (no under-13 accounts).
- We never store passwords — Cognito owns credentials, reset, MFA.

## Principles

- Cognito holds credentials; we hold only authorization data (org membership, enrollment, invites).
- One hardened chokepoint turns a token into `(cognito_sub → app_user → org, role)`.
- RLS is *the* tenant-isolation control. It must be **enforced**, not merely enabled.
- Terse, reusable code; comments ≤2 lines.

## Confirmed substrate defects (must fix before/with auth)

1. **Session-level GUC leak.** `set_config('app.org_id', v, false)` in `http_app.py:198`,
   `db/postgres.py:24`, `search/corpus_search.py:82` persists across pooled-connection reuse →
   cross-tenant read. Must be transaction-local (`true` / `SET LOCAL`) inside a txn, reset per request.
2. **RLS not forced.** All policies use `ENABLE ROW LEVEL SECURITY`, never `FORCE`. The table owner
   bypasses RLS. If the app connects as the owning role, isolation is a no-op today.
3. **Hardcoded identity.** `_USERS` + plaintext passwords + `DEFAULT_ORG_NAME="epistemy"` + dev JWT
   secret fallback in `http_app.py`. Remove entirely.

## Architecture

- **IdP:** one Cognito user pool, multi-tenant via app-side `org` mapping. Hosted UI, OAuth
  **authorization-code + PKCE** (never implicit). App client per environment.
- **Token:** validate the **access token** — `iss`, `client_id`, `token_use=access`, `exp` — with
  cached, rotating JWKS (RS256). No shared secret.
- **Roles:** `platform_admin`, `professor`, `student`.
- **Tenancy:** `org` = institution. RLS keyed on `app.org_id`.
- **Data:** new `auth` schema in `epistemy-process-db` alongside existing `org`/`course`.

## Data model (`auth` schema; `org`/`course` already exist)

- `app_user(id, cognito_sub UNIQUE, email, org_id → org, role, status, created_at)`
- `invitation(id, org_id, course_id → course, code_hash, capacity, used_count, expires_at, created_by → app_user, revoked_at)`
- `enrollment(id, app_user_id → app_user, course_id → course, org_id, created_at, UNIQUE(app_user_id, course_id))`
- `audit_log(id, actor_user_id, action, target, org_id, ts, meta JSONB)` — append-only.

FKs are intra-database (the reason we co-locate). Provisioning a professor or redeeming an invite
is one atomic transaction.

## Identity-resolution chokepoint

Single request dependency:
1. Verify token → `cognito_sub`.
2. Resolve `app_user` by `cognito_sub`. This runs **before** tenant is known, so it must not be
   tenant-scoped: use a `SECURITY DEFINER` function that returns only the caller's row by
   `cognito_sub` (avoids granting the app role blanket cross-tenant read).
3. Open the request transaction; `SET LOCAL app.org_id = <org>` and role; all downstream queries
   inherit it and it resets at commit/rollback.

## RLS hardening

- App connects as a **non-owner** role (`epistemy_app`) with no `BYPASSRLS`.
- `FORCE ROW LEVEL SECURITY` on every tenant table (existing + new).
- Policy shape unchanged: `USING (org_id = current_setting('app.org_id')::uuid)`.

## FERPA control mapping

| Control | Mechanism |
|---|---|
| Access control | Cognito auth + RBAC (role) + RLS |
| Tenant isolation | `FORCE` RLS + non-owner role + txn-local `app.org_id` |
| Encryption at rest | KMS on `epistemy-process-db` (verified) |
| Encryption in transit | TLS to RDS, HTTPS to client |
| Audit | `audit_log` on provisioning, invite create/redeem, role change, login |
| Least privilege | non-owner app role; `SECURITY DEFINER` scoped resolver |

## Flows

- **Login:** Hosted UI → auth code + PKCE → token exchange → access token → API validates → chokepoint.
- **Professor provisioning:** `platform_admin` → `POST /api/admin/professors {email, org}` →
  `AdminCreateUser` (temp password, emailed) → insert `app_user` → forced reset on first login.
  Seconds, no CDK deploy.
- **Student onboarding:** professor creates invitation (course, capacity, expiry) → student signs up
  via Hosted UI with code → higher-ed validates email domain vs `org` → `enrollment`. Redemption
  row-locks `used_count` (race/capacity safe); honors `expires_at`/`revoked_at`.
- **Password reset / forgot:** Cognito hosted flows.

## Task breakdown

**Phase 0a — Verify substrate (gates all else; one-off Fargate task, DB is VPC-only)**
1. Dump `pg_roles.rolbypassrls`, table ownership, and current `relforcerowsecurity` for tenant tables.
2. Confirm `org`/`course` columns for FK targets. Decide app-role model from findings.

**Phase 0 — Remediate substrate**
3. Create non-owner role `epistemy_app`; grant least privilege; `FORCE ROW LEVEL SECURITY` on all tenant tables.
4. Make `app.org_id` transaction-local everywhere (`http_app.py`, `db/postgres.py`, `search/corpus_search.py`); reset per request.
5. Remove `_USERS`, plaintext passwords, inline HS256 JWT, dev-secret fallback from `http_app.py`.
6. Replace `DEFAULT_ORG_NAME` usage with resolved org from the chokepoint.

**Phase 1 — Cognito + token validation**
7. Provision Cognito user pool + Hosted UI + app client (auth-code+PKCE) via infra script (no full CDK); parametrize domain + callback URLs.
8. Cached RS256 JWKS access-token validator (`iss`, `client_id`, `token_use`, `exp`).
9. Auth dependency (chokepoint): token → `cognito_sub` → `app_user` (`SECURITY DEFINER` resolver) → `SET LOCAL` org+role.

**Phase 2 — Schema + provisioning**
10. Migration: `auth` schema (`app_user`, `invitation`, `enrollment`, `audit_log`) + FORCE RLS + `SECURITY DEFINER` resolver.
11. Bootstrap: seed one `platform_admin` + real org(s), idempotent.
12. `POST /api/admin/professors` (platform_admin only): `AdminCreateUser` + insert `app_user` + audit.
13. Invitation endpoints: create (professor) + redeem-on-signup (student) with capacity/expiry/revoke row-locking + higher-ed domain gate.
14. Wire exam endpoints (`exam_session`, `session_turn`, `evaluation`, `grade`) to authenticated user/org.

**Phase 3 — Decommission + verify**
15. `epistemyauth-db`: snapshot → confirm unused → delete (orphaned; currently public + unencrypted).
16. Tests: token validation, **cross-tenant RLS negative test**, invite race/capacity, forced-reset flow.
17. Deploy via `infra/deploy_full.py`; smoke.

## Open items for user

- **Data-reality gate:** real student PII imminent, or test data first? Affects whether signed DPAs
  are launch-blocking (they are org/contractual, outside code).
- Environment count for Cognito app clients (dev only, or dev+prod now?).
