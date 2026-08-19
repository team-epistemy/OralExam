-- migration_006_auth_schema.sql
-- Auth schema: app_user, invitation, enrollment, audit_log + the SECURITY DEFINER
-- identity resolver (the pre-tenant cognito_sub -> app_user chokepoint).
-- Idempotent: safe to re-run. Pairs with the non-owner runtime role epistemy_app
-- (migration_005). Depends on public.org(org_id), public.course(course_id).

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()
CREATE SCHEMA IF NOT EXISTS auth;

-- ── Tables ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS auth.app_user (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cognito_sub TEXT NOT NULL UNIQUE,
    email       TEXT NOT NULL,
    org_id      UUID NOT NULL REFERENCES public.org(org_id),
    role        TEXT NOT NULL CHECK (role IN ('platform_admin','professor','student')),
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','invited','disabled')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_app_user_org ON auth.app_user(org_id);

CREATE TABLE IF NOT EXISTS auth.invitation (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     UUID NOT NULL REFERENCES public.org(org_id),
    course_id  UUID REFERENCES public.course(course_id),
    code_hash  TEXT NOT NULL UNIQUE,
    capacity   INT NOT NULL DEFAULT 1 CHECK (capacity > 0),
    used_count INT NOT NULL DEFAULT 0,
    expires_at TIMESTAMPTZ,
    created_by UUID REFERENCES auth.app_user(id),
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_invitation_org ON auth.invitation(org_id);

CREATE TABLE IF NOT EXISTS auth.enrollment (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_user_id UUID NOT NULL REFERENCES auth.app_user(id),
    course_id   UUID NOT NULL REFERENCES public.course(course_id),
    org_id      UUID NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (app_user_id, course_id)
);
CREATE INDEX IF NOT EXISTS idx_enrollment_org ON auth.enrollment(org_id);

-- Append-only: only INSERT + SELECT are granted below (never UPDATE/DELETE).
CREATE TABLE IF NOT EXISTS auth.audit_log (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_user_id UUID,
    action        TEXT NOT NULL,
    target        TEXT,
    org_id        UUID,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    meta          JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_audit_log_org ON auth.audit_log(org_id, ts);

-- ── RLS ───────────────────────────────────────────────────────────────────
-- app_user is ENABLE but intentionally NOT FORCE: the SECURITY DEFINER resolver
-- below runs BEFORE app.org_id is set, and FORCE would subject its owner to the
-- policy too (current_setting('app.org_id') then errors). The runtime role
-- epistemy_app is non-owner + NOBYPASSRLS, so it is still tenant-constrained.
ALTER TABLE auth.app_user   ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.invitation ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.enrollment ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.audit_log  ENABLE ROW LEVEL SECURITY;

-- Only the tenant-scoped tables are FORCEd (accessed after app.org_id is set).
ALTER TABLE auth.invitation FORCE ROW LEVEL SECURITY;
ALTER TABLE auth.enrollment FORCE ROW LEVEL SECURITY;
ALTER TABLE auth.audit_log  FORCE ROW LEVEL SECURITY;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['app_user','invitation','enrollment','audit_log'] LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_policies
            WHERE schemaname='auth' AND tablename=t AND policyname=t||'_tenant'
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON auth.%I USING (org_id = current_setting(''app.org_id'')::uuid)',
                t||'_tenant', t);
        END IF;
    END LOOP;
END $$;

-- ── Identity-resolution chokepoint ───────────────────────────────────────────
-- SECURITY DEFINER (owned by the migration runner = table owner) so it reads
-- app_user regardless of RLS, resolving cognito_sub -> user BEFORE tenant is known.
-- Returns ONLY the caller's row; never a broad scan.
CREATE OR REPLACE FUNCTION auth.resolve_user(p_sub TEXT)
RETURNS TABLE(id UUID, email TEXT, org_id UUID, role TEXT, status TEXT)
LANGUAGE sql
SECURITY DEFINER
SET search_path = auth, public
AS $$
    SELECT id, email, org_id, role, status
    FROM auth.app_user
    WHERE cognito_sub = p_sub AND status <> 'disabled'
$$;
REVOKE ALL ON FUNCTION auth.resolve_user(TEXT) FROM PUBLIC;

-- ── Invitation redemption (self-serve join) ──────────────────────────────────
-- SECURITY DEFINER: a redeeming student has a Cognito account but no app_user yet,
-- so this runs before any tenant is known. FOR UPDATE serializes concurrent
-- redemptions of the same code so capacity can't be oversold. Atomically:
-- validate the code, create/return the student app_user, enroll, bump used_count.
CREATE OR REPLACE FUNCTION auth.redeem_invitation(
    p_code_hash TEXT, p_sub TEXT, p_email TEXT)
RETURNS TABLE(org_id UUID, role TEXT, course_id UUID)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = auth, public
AS $$
DECLARE inv auth.invitation%ROWTYPE; v_user UUID;
BEGIN
    SELECT * INTO inv FROM auth.invitation
      WHERE code_hash = p_code_hash FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'invalid code'; END IF;
    IF inv.revoked_at IS NOT NULL THEN RAISE EXCEPTION 'revoked'; END IF;
    IF inv.expires_at IS NOT NULL AND inv.expires_at < NOW() THEN
        RAISE EXCEPTION 'expired'; END IF;
    IF inv.used_count >= inv.capacity THEN RAISE EXCEPTION 'exhausted'; END IF;

    INSERT INTO auth.app_user (cognito_sub, email, org_id, role, status)
      VALUES (p_sub, p_email, inv.org_id, 'student', 'active')
      ON CONFLICT (cognito_sub) DO UPDATE SET email = EXCLUDED.email
      RETURNING id INTO v_user;

    IF inv.course_id IS NOT NULL THEN
        INSERT INTO auth.enrollment (app_user_id, course_id, org_id)
          VALUES (v_user, inv.course_id, inv.org_id)
          ON CONFLICT (app_user_id, course_id) DO NOTHING;
    END IF;

    UPDATE auth.invitation SET used_count = used_count + 1 WHERE id = inv.id;
    RETURN QUERY SELECT inv.org_id, 'student'::TEXT, inv.course_id;
END $$;
REVOKE ALL ON FUNCTION auth.redeem_invitation(TEXT, TEXT, TEXT) FROM PUBLIC;

-- ── Least-privilege grants for the runtime role ───────────────────────────────
GRANT USAGE ON SCHEMA auth TO epistemy_app;
GRANT SELECT, INSERT, UPDATE ON auth.app_user, auth.invitation, auth.enrollment TO epistemy_app;
GRANT SELECT, INSERT ON auth.audit_log TO epistemy_app;   -- append-only
GRANT EXECUTE ON FUNCTION auth.resolve_user(TEXT) TO epistemy_app;
GRANT EXECUTE ON FUNCTION auth.redeem_invitation(TEXT, TEXT, TEXT) TO epistemy_app;

COMMIT;
