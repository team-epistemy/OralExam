-- migration_010: class sessions.
--
-- A course is mapped to N class sessions. Each session has:
--   session_id       (PK)
--   session_date     (optional)
--   session_document (the session's document — content or a reference)
-- Named class_session to avoid collision with the existing exam_session (a
-- student's exam attempt). Tenant-scoped by org RLS like the other public tables;
-- FK to course with cascade delete so a course's sessions go with it.
-- Idempotent (IF NOT EXISTS + guarded policy), safe to re-run.
BEGIN;

CREATE TABLE IF NOT EXISTS class_session (
    session_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id        UUID NOT NULL REFERENCES course(course_id) ON DELETE CASCADE,
    org_id           UUID NOT NULL,
    session_date     DATE,          -- optional
    session_document TEXT,          -- the session's document (content or reference)
    created_by       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_class_session_course ON class_session(course_id);
CREATE INDEX IF NOT EXISTS idx_class_session_org    ON class_session(org_id);

-- RLS: tenant isolation by org, consistent with course/assignment/material.
ALTER TABLE class_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE class_session FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies
                   WHERE tablename='class_session' AND policyname='class_session_tenant') THEN
        CREATE POLICY class_session_tenant ON class_session
            USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;

-- Least-privilege grants for the runtime role.
GRANT SELECT, INSERT, UPDATE, DELETE ON class_session TO epistemy_app;

COMMIT;
