-- migration_026: public credential-free demo links. A token maps to one assignment
-- (practice-mode) and is bounded by expiry + a total attempt cap, so a forwarded link
-- can't run up unbounded LLM/TTS cost. Demo sessions use throwaway anonymous student
-- ids (never a real roster). Owner-created (migrate task); runtime role lacks CREATE.
CREATE TABLE IF NOT EXISTS demo_link (
    token          TEXT PRIMARY KEY,
    org_id         UUID NOT NULL,
    assignment_id  UUID NOT NULL,
    max_attempts   INT  NOT NULL DEFAULT 10,
    attempts_used  INT  NOT NULL DEFAULT 0,
    expires_at     TIMESTAMPTZ NOT NULL,
    created_by     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_demo_link_org ON demo_link(org_id, created_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON demo_link TO epistemy_app;
