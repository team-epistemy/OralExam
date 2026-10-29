-- migration_032: anonymous user-engagement telemetry for the credential-free DEMO
-- speaking tests. One row per instrumented event (opened / started / mic_granted /
-- mic_denied / question_shown / answer_submitted / completed / abandoned /
-- results_viewed / error). The public POST /api/demo/{token}/event endpoint writes
-- these, gated the same way every other demo endpoint is (valid, non-exhausted,
-- unexpired demo link → org_id resolved from the link).
--
-- ANONYMOUS by construction: there is no user identity in the demo flow. We store
-- only the demo token, an optional throwaway session_id, a turn index, a duration
-- (ms), and a small free-form meta blob (user-agent / language / referrer on the
-- `opened` event). No PII, no learner identity, no transcript content.
--
-- Tenant-scoped via RLS like the other session-grain tables (session_media,
-- material): org_id = current_setting('app.org_id')::uuid. Owner-run migration +
-- explicit GRANT to the runtime role (epistemy_app) — NEVER a lazy runtime CREATE
-- TABLE (the app role lacks CREATE on public and that path 500s). Idempotent; safe
-- to re-run.

BEGIN;

CREATE TABLE IF NOT EXISTS demo_event (
    event_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id      UUID NOT NULL,
    demo_token  TEXT NOT NULL,
    session_id  UUID,            -- throwaway demo session; NULL before `started`
    event_type  TEXT NOT NULL,
    turn_index  INT,             -- set on question_shown / answer_submitted
    value_ms    BIGINT,          -- speaking duration (answer_submitted), when measured
    meta        JSONB,           -- {ua, lang, ref} on `opened`; capped server-side
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Rollups filter by (org, token) over a time window; per-session aggregates
-- (duration, active speaking, questions answered) group by session_id.
CREATE INDEX IF NOT EXISTS idx_demo_event_org_token ON demo_event(org_id, demo_token, created_at);
CREATE INDEX IF NOT EXISTS idx_demo_event_session ON demo_event(session_id);

ALTER TABLE demo_event ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='demo_event' AND policyname='demo_event_tenant') THEN
        CREATE POLICY demo_event_tenant ON demo_event USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;

GRANT SELECT, INSERT ON demo_event TO epistemy_app;

COMMIT;
