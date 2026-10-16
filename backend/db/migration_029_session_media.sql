-- Exam proctoring / identity / response snapshots (camera stills captured during
-- an exam). One row per stored image; the object lives in the materials bucket
-- under a per-session proctoring/ prefix. Tenant-scoped via RLS like other session
-- data. Owner-run migration + explicit grant to the runtime role (never lazy).
-- Idempotent; safe to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS session_media (
    media_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID NOT NULL REFERENCES exam_session(session_id) ON DELETE CASCADE,
    org_id       UUID NOT NULL REFERENCES org(org_id),
    kind         TEXT NOT NULL CHECK (kind IN ('identity', 'proctor', 'response')),
    turn_index   INT,                       -- set for 'response' snapshots (the answer's turn)
    storage_key  TEXT NOT NULL,
    mime_type    TEXT NOT NULL DEFAULT 'image/jpeg',
    captured_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_session_media_session ON session_media(session_id, captured_at);

ALTER TABLE session_media ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='session_media' AND policyname='session_media_tenant') THEN
        CREATE POLICY session_media_tenant ON session_media USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;

GRANT SELECT, INSERT, DELETE ON session_media TO epistemy_app;

COMMIT;
