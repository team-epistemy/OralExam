-- Epistemy M6 schema: assignment delivery tables.
-- RLS keyed on app.org_id; sessions belong to an assignment and org.

DROP TABLE IF EXISTS session_turn CASCADE;
DROP TABLE IF EXISTS exam_session CASCADE;
DROP TABLE IF EXISTS assignment CASCADE;

-- An assignment linking a question set to delivery configuration.
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

-- A student's exam session for an assignment.
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
-- Unique partial index: prevents duplicate active sessions per student+assignment (race condition guard)
CREATE UNIQUE INDEX IF NOT EXISTS session_active_unique_idx ON exam_session(assignment_id, student_id)
    WHERE status = 'active';

-- A single question-answer turn within a session.
CREATE TABLE IF NOT EXISTS session_turn (
    turn_id            UUID PRIMARY KEY,
    session_id         UUID NOT NULL REFERENCES exam_session(session_id),
    turn_index         INT NOT NULL,
    question_id        UUID NOT NULL REFERENCES question(question_id),
    student_answer     TEXT,
    answered_at        TIMESTAMPTZ,
    time_spent_seconds INT,
    UNIQUE (session_id, turn_index)
);

CREATE INDEX IF NOT EXISTS turn_session_idx ON session_turn(session_id);
CREATE INDEX IF NOT EXISTS turn_question_idx ON session_turn(question_id);

-- RLS policies: every row visible only to its own org.
ALTER TABLE assignment   ENABLE ROW LEVEL SECURITY;
ALTER TABLE exam_session ENABLE ROW LEVEL SECURITY;

CREATE POLICY assignment_org_isolation ON assignment
    USING (current_setting('app.org_id')::uuid = org_id);
CREATE POLICY session_org_isolation ON exam_session
    USING (current_setting('app.org_id')::uuid = org_id);

-- session_turn inherits isolation via the session FK; add explicit RLS
-- through a subquery for defense-in-depth.
ALTER TABLE session_turn ENABLE ROW LEVEL SECURITY;
CREATE POLICY turn_org_isolation ON session_turn
    USING (
        EXISTS (
            SELECT 1 FROM exam_session es
            WHERE es.session_id = session_turn.session_id
            AND current_setting('app.org_id')::uuid = es.org_id
        )
    );
