-- migration_008_assignment_type.sql
-- Categorise assignments so students see them under Practice / Assignment / Exam.
-- Idempotent: safe to re-run.
BEGIN;

ALTER TABLE assignment
  ADD COLUMN IF NOT EXISTS assignment_type TEXT NOT NULL DEFAULT 'assignment';

ALTER TABLE assignment DROP CONSTRAINT IF EXISTS assignment_type_check;
ALTER TABLE assignment
  ADD CONSTRAINT assignment_type_check
  CHECK (assignment_type IN ('practice', 'assignment', 'exam'));

COMMIT;
