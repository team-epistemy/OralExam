-- migration_020: per-question record for the QG test bench, so generated
-- questions are saved for human evaluation alongside their automated QG grading.
-- One row per generated question per run. The run's aggregate report still lives
-- in qg_test_run.report (JSONB); this is the queryable, human-annotatable surface.
-- Created by the owner (migrate task); the runtime role lacks CREATE on public.
CREATE TABLE IF NOT EXISTS qg_test_question (
    question_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                UUID NOT NULL,
    org_id                UUID NOT NULL,
    course_id             UUID,
    position              INT  NOT NULL,
    question_text         TEXT,
    declared_difficulty   TEXT,
    classified_difficulty TEXT,
    concept_ids           JSONB,          -- concepts the question was grounded on
    expected_path         JSONB,          -- expected reasoning path (rubric)
    auto_results          JSONB,          -- per-criterion automated verdicts for this question
    auto_pass             INT  NOT NULL DEFAULT 0,
    auto_fail             INT  NOT NULL DEFAULT 0,
    -- Human evaluation (null until a reviewer records it):
    human_verdict         TEXT,           -- 'good' | 'needs_edit' | 'reject'
    human_rating          INT,            -- 1..5
    human_notes           TEXT,
    reviewed_by           TEXT,
    reviewed_at           TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_qg_test_question_run ON qg_test_question(run_id, position);
CREATE INDEX IF NOT EXISTS idx_qg_test_question_org ON qg_test_question(org_id, created_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON qg_test_question TO epistemy_app;
