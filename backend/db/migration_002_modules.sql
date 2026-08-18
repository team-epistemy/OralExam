-- migration_002_modules.sql
-- Adds M4 (graph_version), M5 (questions), M6 (delivery), M7 (evaluation) tables.
-- Idempotent: safe to re-run. All tables enforce RLS keyed on app.org_id.
-- Depends on M3 base tables: org, course, material_version, async_job.

BEGIN;

-- =============================================================================
-- M4: Knowledge-Graph Version Metadata (backend.graph.metadata)
-- =============================================================================

CREATE TABLE IF NOT EXISTS graph_version (
    version_id       UUID PRIMARY KEY,
    org_id           UUID NOT NULL REFERENCES org(org_id),
    course_id        UUID NOT NULL REFERENCES course(course_id),
    graph_version    INT NOT NULL DEFAULT 1,
    s3_key           TEXT NOT NULL DEFAULT '',
    node_count       INT NOT NULL DEFAULT 0,
    edge_count       INT NOT NULL DEFAULT 0,
    validation_score DOUBLE PRECISION,
    job_id           UUID,
    is_active        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_graph_version_tenant
    ON graph_version(org_id, course_id);
CREATE INDEX IF NOT EXISTS idx_graph_version_active
    ON graph_version(org_id, course_id, is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_graph_version_job
    ON graph_version(job_id) WHERE job_id IS NOT NULL;

ALTER TABLE graph_version ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'graph_version' AND policyname = 'graph_version_tenant'
    ) THEN
        CREATE POLICY graph_version_tenant ON graph_version
            USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;


CREATE TABLE IF NOT EXISTS graph_eds_results (
    run_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assessment_id  UUID NOT NULL,
    student_id     TEXT NOT NULL,
    org_id         UUID NOT NULL REFERENCES org(org_id),
    course_id      UUID NOT NULL REFERENCES course(course_id),
    eds_score      DOUBLE PRECISION NOT NULL,
    components     JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_graph_eds_tenant
    ON graph_eds_results(org_id, course_id, assessment_id);
CREATE INDEX IF NOT EXISTS idx_graph_eds_student
    ON graph_eds_results(student_id, course_id);

ALTER TABLE graph_eds_results ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'graph_eds_results' AND policyname = 'graph_eds_results_tenant'
    ) THEN
        CREATE POLICY graph_eds_results_tenant ON graph_eds_results
            USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;


-- =============================================================================
-- M5: Question Generation (backend.questions)
-- =============================================================================

CREATE TABLE IF NOT EXISTS question (
    question_id       UUID PRIMARY KEY,
    course_id         UUID NOT NULL REFERENCES course(course_id),
    org_id            UUID NOT NULL REFERENCES org(org_id),
    concept_ids       JSONB NOT NULL DEFAULT '[]',
    text              TEXT NOT NULL,
    question_type     TEXT NOT NULL DEFAULT 'free_text',
    difficulty        JSONB NOT NULL DEFAULT '{"level": "medium"}',
    status            TEXT NOT NULL DEFAULT 'draft',
    created_by        TEXT NOT NULL DEFAULT 'system',
    source_chunks     JSONB NOT NULL DEFAULT '[]',
    generation_job_id UUID,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT question_type_chk CHECK (question_type IN ('free_text', 'oral')),
    CONSTRAINT question_status_chk CHECK (status IN ('draft', 'approved', 'rejected'))
);

CREATE INDEX IF NOT EXISTS question_course_idx ON question(course_id);
CREATE INDEX IF NOT EXISTS question_status_idx ON question(course_id, status);
CREATE INDEX IF NOT EXISTS question_concept_gin ON question USING gin(concept_ids);
CREATE INDEX IF NOT EXISTS question_org_idx ON question(org_id);

ALTER TABLE question ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'question' AND policyname = 'question_tenant'
    ) THEN
        CREATE POLICY question_tenant ON question
            USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;


CREATE TABLE IF NOT EXISTS question_set (
    question_set_id UUID PRIMARY KEY,
    course_id       UUID NOT NULL REFERENCES course(course_id),
    org_id          UUID NOT NULL REFERENCES org(org_id),
    title           TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS question_set_course_idx ON question_set(course_id);
CREATE INDEX IF NOT EXISTS question_set_org_idx ON question_set(org_id);

ALTER TABLE question_set ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'question_set' AND policyname = 'question_set_tenant'
    ) THEN
        CREATE POLICY question_set_tenant ON question_set
            USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;


CREATE TABLE IF NOT EXISTS question_set_membership (
    question_set_id UUID NOT NULL REFERENCES question_set(question_set_id),
    question_id     UUID NOT NULL REFERENCES question(question_id),
    org_id          UUID NOT NULL REFERENCES org(org_id),
    position        INT NOT NULL DEFAULT 0,
    PRIMARY KEY (question_set_id, question_id)
);

CREATE INDEX IF NOT EXISTS qsm_question_idx ON question_set_membership(question_id);

ALTER TABLE question_set_membership ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'question_set_membership' AND policyname = 'question_set_membership_tenant'
    ) THEN
        CREATE POLICY question_set_membership_tenant ON question_set_membership
            USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;


CREATE TABLE IF NOT EXISTS generation_job (
    job_id          UUID PRIMARY KEY,
    org_id          UUID NOT NULL REFERENCES org(org_id),
    course_id       UUID NOT NULL REFERENCES course(course_id),
    concept_ids     JSONB NOT NULL DEFAULT '[]',
    requested_count INT NOT NULL DEFAULT 5,
    status          TEXT NOT NULL DEFAULT 'queued',
    generated_count INT NOT NULL DEFAULT 0,
    question_ids    JSONB NOT NULL DEFAULT '[]',
    error           JSONB,
    created_by      TEXT NOT NULL DEFAULT 'system',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT generation_job_status_chk CHECK (status IN ('queued', 'running', 'succeeded', 'failed'))
);

CREATE INDEX IF NOT EXISTS generation_job_course_idx ON generation_job(course_id);
CREATE INDEX IF NOT EXISTS generation_job_status_idx ON generation_job(org_id, status);

ALTER TABLE generation_job ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'generation_job' AND policyname = 'generation_job_tenant'
    ) THEN
        CREATE POLICY generation_job_tenant ON generation_job
            USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;

-- FK back-reference: question.generation_job_id -> generation_job.job_id
-- Added after both tables exist to avoid ordering issues.
-- The column is added idempotently first: an existing `question` table (created by
-- schema.sql) predates this column, so the CREATE TABLE above is a no-op for it.
ALTER TABLE question ADD COLUMN IF NOT EXISTS generation_job_id UUID;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE constraint_name = 'question_generation_job_fk'
    ) THEN
        ALTER TABLE question
            ADD CONSTRAINT question_generation_job_fk
            FOREIGN KEY (generation_job_id) REFERENCES generation_job(job_id);
    END IF;
END $$;


-- =============================================================================
-- M6: Assignment Delivery (backend.delivery)
-- =============================================================================

CREATE TABLE IF NOT EXISTS assignment (
    assignment_id   UUID PRIMARY KEY,
    course_id       UUID NOT NULL REFERENCES course(course_id),
    org_id          UUID NOT NULL REFERENCES org(org_id),
    title           TEXT NOT NULL,
    question_set_id UUID NOT NULL REFERENCES question_set(question_set_id),
    config          JSONB NOT NULL DEFAULT '{"adaptive": true, "max_questions": 10}',
    status          TEXT NOT NULL DEFAULT 'draft',
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT assignment_status_chk CHECK (status IN ('draft', 'active', 'closed'))
);

CREATE INDEX IF NOT EXISTS assignment_course_idx ON assignment(course_id);
CREATE INDEX IF NOT EXISTS assignment_status_idx ON assignment(course_id, status);
CREATE INDEX IF NOT EXISTS assignment_org_idx ON assignment(org_id);

ALTER TABLE assignment ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'assignment' AND policyname = 'assignment_tenant'
    ) THEN
        CREATE POLICY assignment_tenant ON assignment
            USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;


CREATE TABLE IF NOT EXISTS exam_session (
    session_id         UUID PRIMARY KEY,
    assignment_id      UUID NOT NULL REFERENCES assignment(assignment_id),
    student_id         TEXT NOT NULL,
    org_id             UUID NOT NULL REFERENCES org(org_id),
    course_id          UUID NOT NULL REFERENCES course(course_id),
    status             TEXT NOT NULL DEFAULT 'active',
    started_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at       TIMESTAMPTZ,
    current_turn_index INT NOT NULL DEFAULT 0,
    questions_delivered JSONB NOT NULL DEFAULT '[]',
    concepts_covered   JSONB NOT NULL DEFAULT '[]',
    CONSTRAINT session_status_chk CHECK (status IN ('active', 'completed', 'abandoned'))
);

CREATE INDEX IF NOT EXISTS session_assignment_idx ON exam_session(assignment_id);
CREATE INDEX IF NOT EXISTS session_student_idx ON exam_session(student_id, course_id);
-- Clean up duplicate active sessions before creating unique index
DELETE FROM exam_session WHERE session_id IN (
    SELECT session_id FROM (
        SELECT session_id, ROW_NUMBER() OVER (PARTITION BY assignment_id, student_id ORDER BY started_at DESC) as rn
        FROM exam_session WHERE status = 'active'
    ) dupes WHERE rn > 1
);
CREATE UNIQUE INDEX IF NOT EXISTS session_active_unique_idx ON exam_session(assignment_id, student_id)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS session_org_idx ON exam_session(org_id);

ALTER TABLE exam_session ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'exam_session' AND policyname = 'exam_session_tenant'
    ) THEN
        CREATE POLICY exam_session_tenant ON exam_session
            USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;


CREATE TABLE IF NOT EXISTS session_turn (
    turn_id            UUID PRIMARY KEY,
    session_id         UUID NOT NULL REFERENCES exam_session(session_id),
    org_id             UUID NOT NULL REFERENCES org(org_id),
    turn_index         INT NOT NULL,
    question_id        UUID NOT NULL REFERENCES question(question_id),
    student_answer     TEXT,
    answered_at        TIMESTAMPTZ,
    time_spent_seconds INT,
    UNIQUE (session_id, turn_index)
);

CREATE INDEX IF NOT EXISTS turn_session_idx ON session_turn(session_id);
CREATE INDEX IF NOT EXISTS turn_question_idx ON session_turn(question_id);

ALTER TABLE session_turn ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'session_turn' AND policyname = 'session_turn_tenant'
    ) THEN
        CREATE POLICY session_turn_tenant ON session_turn
            USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;


-- =============================================================================
-- M7: Evaluation and Grading (backend.evaluation)
-- =============================================================================

CREATE TABLE IF NOT EXISTS evaluation (
    evaluation_id     UUID PRIMARY KEY,
    turn_id           UUID NOT NULL REFERENCES session_turn(turn_id),
    org_id            UUID NOT NULL REFERENCES org(org_id),
    course_id         UUID NOT NULL REFERENCES course(course_id),
    student_id        TEXT NOT NULL,
    question_id       UUID NOT NULL REFERENCES question(question_id),
    claims            JSONB NOT NULL DEFAULT '{"claims": [], "total_claims": 0}',
    concept_coverage  JSONB NOT NULL DEFAULT '{"coverage_ratio": 0.0}',
    eds_score         FLOAT NOT NULL DEFAULT 0.0,
    eds_bucket        TEXT NOT NULL DEFAULT 'low',
    raw_llm_output    JSONB,
    evaluated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    evaluation_job_id UUID,
    CONSTRAINT evaluation_bucket_chk CHECK (eds_bucket IN ('low', 'medium', 'high')),
    UNIQUE (turn_id)
);

CREATE INDEX IF NOT EXISTS evaluation_student_idx ON evaluation(student_id, course_id);
CREATE INDEX IF NOT EXISTS evaluation_turn_idx ON evaluation(turn_id);
CREATE INDEX IF NOT EXISTS evaluation_session_lookup ON evaluation(question_id, student_id);
CREATE INDEX IF NOT EXISTS evaluation_org_idx ON evaluation(org_id);

ALTER TABLE evaluation ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'evaluation' AND policyname = 'evaluation_tenant'
    ) THEN
        CREATE POLICY evaluation_tenant ON evaluation
            USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;


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
CREATE INDEX IF NOT EXISTS grade_org_idx ON grade(org_id);

ALTER TABLE grade ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'grade' AND policyname = 'grade_tenant'
    ) THEN
        CREATE POLICY grade_tenant ON grade
            USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;

COMMIT;
