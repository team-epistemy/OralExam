-- migration_027: per-dimension human calibration of CEFR speaking-exam scores.
-- A rater overrides the machine band for a given turn + CEFR dimension; stored so
-- the scoring can be validated against human judgement over time. One row per
-- (session, turn, dimension, rater), upserted. Owner-created (migrate task);
-- runtime role (epistemy_app) lacks CREATE on public.
CREATE TABLE IF NOT EXISTS cefr_calibration (
    org_id        UUID NOT NULL,
    session_id    UUID NOT NULL,
    turn_number   INT  NOT NULL,
    dimension     TEXT NOT NULL,   -- overall|range|accuracy|fluency|coherence|interaction
    machine_band  TEXT,            -- the band the model assigned (A1..C2), for comparison
    human_band    TEXT NOT NULL,   -- the rater's band (A1..C2)
    rater_id      TEXT NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_id, turn_number, dimension, rater_id)
);
CREATE INDEX IF NOT EXISTS idx_cefr_calibration_session ON cefr_calibration(session_id, turn_number);

GRANT SELECT, INSERT, UPDATE, DELETE ON cefr_calibration TO epistemy_app;
