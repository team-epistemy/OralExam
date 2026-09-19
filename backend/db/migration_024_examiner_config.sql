-- migration_024: per-org examiner runtime config. Currently one knob: eval_mode,
-- selecting the answer-flow evaluation path — 'sonnet' (single Sonnet call producing
-- probe + EDS, the default) or 'hybrid' (fast Haiku spoken probe + async Sonnet EDS).
-- Owner-created (migrate task); runtime role (epistemy_app) lacks CREATE on public.
CREATE TABLE IF NOT EXISTS examiner_config (
    org_id      UUID PRIMARY KEY,
    eval_mode   TEXT NOT NULL DEFAULT 'sonnet',   -- 'sonnet' | 'hybrid'
    updated_by  TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT examiner_config_mode_chk CHECK (eval_mode IN ('sonnet', 'hybrid'))
);

GRANT SELECT, INSERT, UPDATE, DELETE ON examiner_config TO epistemy_app;
