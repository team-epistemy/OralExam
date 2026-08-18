-- Epistemy M5 schema: question generation tables.
-- RLS keyed on app.org_id; questions belong to a course and org.

DROP TABLE IF EXISTS question_set_membership CASCADE;
DROP TABLE IF EXISTS question_set CASCADE;
DROP TABLE IF EXISTS question CASCADE;
DROP TABLE IF EXISTS generation_job CASCADE;

-- A generated question, pending professor review.
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

-- A named collection of questions for assignment use.
CREATE TABLE IF NOT EXISTS question_set (
    question_set_id UUID PRIMARY KEY,
    course_id       UUID NOT NULL REFERENCES course(course_id),
    org_id          UUID NOT NULL REFERENCES org(org_id),
    title           TEXT NOT NULL,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Many-to-many join: questions in a set, with ordering.
CREATE TABLE IF NOT EXISTS question_set_membership (
    question_set_id UUID NOT NULL REFERENCES question_set(question_set_id),
    question_id     UUID NOT NULL REFERENCES question(question_id),
    position        INT NOT NULL DEFAULT 0,
    PRIMARY KEY (question_set_id, question_id)
);

-- Tracks batch generation jobs (also stored in async_job but with M5-specific fields).
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

-- RLS policies: every row visible only to its own org.
ALTER TABLE question     ENABLE ROW LEVEL SECURITY;
ALTER TABLE question_set ENABLE ROW LEVEL SECURITY;
ALTER TABLE generation_job ENABLE ROW LEVEL SECURITY;

CREATE POLICY question_org_isolation ON question
    USING (current_setting('app.org_id')::uuid = org_id);
CREATE POLICY question_set_org_isolation ON question_set
    USING (current_setting('app.org_id')::uuid = org_id);
CREATE POLICY generation_job_org_isolation ON generation_job
    USING (current_setting('app.org_id')::uuid = org_id);
