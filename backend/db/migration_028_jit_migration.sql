-- JIT Cognito -> GCIP migration support.
-- A SECURITY DEFINER read so the pre-tenant POST /api/auth/migrate endpoint can
-- look up an active app_user by email (the runtime role can't read auth.app_user
-- directly under RLS). Returns the existing IdP subject so the GCIP user is
-- created with uid == that sub and the GCIP token resolves to the same app_user
-- with no row rewrite. Idempotent; safe to re-run. Pairs with migration_006.

BEGIN;

CREATE OR REPLACE FUNCTION auth.user_for_migration(p_email TEXT)
RETURNS TABLE(cognito_sub TEXT, org_id UUID, role TEXT, status TEXT)
LANGUAGE sql
SECURITY DEFINER
SET search_path = auth, public
AS $$
    SELECT cognito_sub, org_id, role, status
    FROM auth.app_user
    WHERE lower(email) = lower(p_email) AND status <> 'disabled'
    ORDER BY created_at
    LIMIT 1
$$;
REVOKE ALL ON FUNCTION auth.user_for_migration(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION auth.user_for_migration(TEXT) TO epistemy_app;

COMMIT;
