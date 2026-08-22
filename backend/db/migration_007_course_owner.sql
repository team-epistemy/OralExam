-- migration_007_course_owner.sql
-- Per-professor course ownership: course.created_by (professor email, matching
-- the material.created_by convention). RLS still isolates orgs; ownership adds
-- intra-org isolation so a professor sees only their own courses.
-- Idempotent: safe to re-run. Backfills each course from the professor who
-- first uploaded material to it; courses with no materials stay NULL (orphan).

BEGIN;

ALTER TABLE course ADD COLUMN IF NOT EXISTS created_by TEXT;

UPDATE course c
   SET created_by = sub.created_by
  FROM (
        SELECT DISTINCT ON (course_id) course_id, created_by
          FROM material
         ORDER BY course_id, created_at ASC
       ) sub
 WHERE c.course_id = sub.course_id
   AND c.created_by IS NULL;

COMMIT;
