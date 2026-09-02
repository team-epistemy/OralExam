-- Migration 014: make "Remove course" succeed when the course has students.
--
-- The authoritative enrollment/invitation rows live in the auth schema
-- (auth.enrollment, auth.invitation) with FKs to public.course. The course-delete
-- path only clears the public.enrollment mirror (which has no FK), so those auth
-- rows survived and the final `DELETE FROM course` failed with:
--   ForeignKeyViolation: ... "enrollment_course_id_fkey" on table "enrollment"
--
-- Fix: make both course FKs ON DELETE CASCADE (same pattern as the provenance
-- tables in migration 013). Referential-action deletes are performed by the
-- system regardless of the invoking role's privileges or RLS, so the non-owner
-- runtime role can delete a course and the auth rows fall away with it.
-- auth.invitation.course_id is nullable (org-level invites), so only
-- course-scoped invitations are affected.

ALTER TABLE auth.enrollment DROP CONSTRAINT IF EXISTS enrollment_course_id_fkey;
ALTER TABLE auth.enrollment
    ADD CONSTRAINT enrollment_course_id_fkey
    FOREIGN KEY (course_id) REFERENCES public.course(course_id) ON DELETE CASCADE;

ALTER TABLE auth.invitation DROP CONSTRAINT IF EXISTS invitation_course_id_fkey;
ALTER TABLE auth.invitation
    ADD CONSTRAINT invitation_course_id_fkey
    FOREIGN KEY (course_id) REFERENCES public.course(course_id) ON DELETE CASCADE;
