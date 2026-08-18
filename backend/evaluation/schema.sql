-- Epistemy M7 schema: evaluation and grading tables.
-- RLS keyed on app.org_id; evaluations belong to turns, grades to sessions.

DROP TABLE IF EXISTS grade CASCADE;
DROP TABLE IF EXISTS evaluation CASCADE;

-- Per-turn evaluation: claims, coverage, and EDS score.
CREATE TABLE IF NOT EXISTS evaluation (
    evaluation_id    UUID PRIMARY KEY,
    turn_id          UUID NOT NULL REFERENCES session_turn(turn_id),
    org_id           UUID NOT NULL REFERENCES org(org_id),
    course_id        UUID NOT NULL REFERENCES course(course_id),
    student_id       TEXT NOT NULL,
    question_id      UUID NOT NULL REFERENCES question(question_id),
    claims           JSONB NOT NULL DEFAULT '{"claims": [], "total_claims": 0}',
    concept_coverage JSONB NOT NULL DEFAULT '{"coverage_ratio": 0.0}',
    eds_score        FLOAT NOT NULL DEFAULT 0.0,
    eds_bucket       TEXT NOT NULL DEFAULT 'low',
    raw_llm_output   JSONB,
    evaluated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    evaluation_job_id UUID,
    CONSTRAINT evaluation_bucket_chk CHECK (eds_bucket IN ('low', 'medium', 'high')),
    UNIQUE (turn_id)
);

CREATE INDEX IF NOT EXISTS evaluation_student_idx ON evaluation(student_id, course_id);
CREATE INDEX IF NOT EXISTS evaluation_turn_idx ON evaluation(turn_id);
CREATE INDEX IF NOT EXISTS evaluation_session_lookup
    ON evaluation(question_id, student_id);

-- Session-level grade: aggregated from per-turn evaluations.
CREATE TABLE IF NOT EXISTS grade (
    grade_id         UUID PRIMARY KEY,
    session_id       UUID NOT NULL REFERENCES exam_session(session_id),
    student_id       TEXT NOT NULL,
    assignment_id    UUID NOT NULL REFERENCES assignment(assignment_id),
    org_id           UUID NOT NULL REFERENCES org(org_id),
    course_id        UUID NOT NULL REFERENCES course(course_id),
    final_score      FLOAT NOT NULL DEFAULT 0.0,
    component_scores JSONB NOT NULL DEFAULT '{}',
    override_by      TEXT,
    override_reason  TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    released_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT grade_status_chk CHECK (status IN ('pending', 'released')),
    UNIQUE (session_id)
);

CREATE INDEX IF NOT EXISTS grade_student_idx ON grade(student_id, course_id);
CREATE INDEX IF NOT EXISTS grade_assignment_idx ON grade(assignment_id);
CREATE INDEX IF NOT EXISTS grade_status_idx ON grade(assignment_id, status);

-- RLS policies: every row visible only to its own org.
ALTER TABLE evaluation ENABLE ROW LEVEL SECURITY;
ALTER TABLE grade      ENABLE ROW LEVEL SECURITY;

CREATE POLICY evaluation_org_isolation ON evaluation
    USING (current_setting('app.org_id')::uuid = org_id);
CREATE POLICY grade_org_isolation ON grade
    USING (current_setting('app.org_id')::uuid = org_id);
