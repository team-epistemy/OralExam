-- migration_009: let professors DROP students from a course.
--
-- unenroll_student clears the public `enrollment` mirror, but the authoritative
-- row lives in auth.enrollment — and the runtime role was only granted
-- SELECT/INSERT/UPDATE there (migration_006), never DELETE. So a dropped student
-- lingered in auth.enrollment and a later public-mirror rebuild could resurrect
-- them. Grant DELETE so the drop can remove the authoritative row too.
--
-- Tenant-safe: auth.enrollment is FORCE row-level-security; the app.org_id policy
-- still scopes every delete to the caller's org, and epistemy_app is NOBYPASSRLS.
BEGIN;
GRANT DELETE ON auth.enrollment TO epistemy_app;
COMMIT;
