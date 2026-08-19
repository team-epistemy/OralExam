-- migration_005_rls_hardening.sql
-- Makes RLS actually enforce tenant isolation:
--   1. FORCE RLS on every tenant table so even the table owner obeys policies.
--   2. Close the two gaps (course, async_job) that had no RLS at all.
-- Idempotent: safe to re-run. Pairs with the non-owner runtime role (setup_app_role).

BEGIN;

-- 1. Close the gaps: enable + policy on course and async_job (both carry org_id).
ALTER TABLE course ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='course' AND policyname='course_tenant') THEN
        CREATE POLICY course_tenant ON course USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;

ALTER TABLE async_job ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='async_job' AND policyname='async_job_tenant') THEN
        CREATE POLICY async_job_tenant ON async_job USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;

-- 2. FORCE RLS on all tenant tables (org is the tenant root, intentionally excluded).
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'assignment','async_job','chunk','course','evaluation','exam_session',
        'grade','graph_version','material','material_version','question',
        'question_eds_aggregate','question_set','question_set_membership','session_turn'
    ] LOOP
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    END LOOP;
END $$;

COMMIT;
