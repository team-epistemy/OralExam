-- Allow professors to drop students: grant DELETE on the enrollment join.
-- Tenant-safe — the enrollment_tenant RLS policy still scopes deletes to the org.
BEGIN;
GRANT DELETE ON auth.enrollment TO epistemy_app;
COMMIT;
