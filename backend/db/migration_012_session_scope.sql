-- migration_012: in-scope concept set per class session (week scoping).
--
-- A class session (a "week") can declare which concept-graph nodes are in scope.
-- Stored as a JSONB array of concept ids on class_session. Exam generation for
-- that week draws only from these concepts, and the chosen set is snapshotted
-- onto the assignment at publish so later scope edits don't recategorize past
-- exams (P-S-2.1 / 2.2 / 2.3).
-- Defaults to '[]' (no scope = whole graph, unchanged behavior).
-- Idempotent (IF NOT EXISTS), safe to re-run.
BEGIN;

ALTER TABLE class_session
    ADD COLUMN IF NOT EXISTS in_scope_concepts JSONB NOT NULL DEFAULT '[]'::jsonb;

COMMIT;
