-- migration_029: per-attempt transcription-reliability / intelligibility metadata
-- for the CEFR speaking exam. One row per (session, turn): the raw Azure NBest
-- JSON plus the derived reliability signals, and the post-human-review fields an
-- examiner fills when an attempt is routed for review.
--
-- FAIRNESS: everything here is METADATA ONLY. None of these values ever feed the
-- CEFR band (pronunciation/prosody are excluded for the same accent-bias reason;
-- this mirrors that). Reliability measures how sure the RECOGNIZER was of what it
-- heard — it is a routing signal for human review, not a judgement of the speaker.
--
-- Owner-created (migrate task); the runtime role (epistemy_app) lacks CREATE on
-- public, so this must NOT be a lazy runtime CREATE TABLE (that 500s). Follows
-- migration_027 (cefr_calibration): org_id column for tenant scoping + GRANT; no
-- RLS, consistent with the other calibration/metadata tables on this schema.
CREATE TABLE IF NOT EXISTS speaking_reliability (
    org_id                UUID NOT NULL,
    session_id            UUID NOT NULL,
    turn_number           INT  NOT NULL,
    -- Raw Azure Detailed NBest JSON for the attempt — the ground truth to later
    -- calibrate the provisional reliability weights against human labels.
    raw_nbest_json        JSONB,
    -- Derived reliability metadata (all nullable: NULL = unmeasured, never 0).
    confidence_mean       DOUBLE PRECISION,  -- word-weighted mean NBest[0].Confidence (0–1)
    transcript_reliability DOUBLE PRECISION, -- conservative 0–1 combination (provisional)
    intelligibility       DOUBLE PRECISION,  -- PLACEHOLDER: future learned head; always NULL for now
    needs_review          BOOLEAN NOT NULL DEFAULT FALSE,
    needs_review_reason   TEXT,
    -- Post-human-review fields (filled when an examiner reviews a flagged attempt).
    corrected_transcript  TEXT,
    reviewed_by           TEXT,
    rescored_bands        JSONB,
    -- FAIRNESS AUDIT HOOK: we want to audit needs_review rate by learner L1 group
    -- to prove gating is on recognition reliability, NOT accentedness. There is no
    -- L1 field anywhere in the data model yet, so no column is invented here. When
    -- an L1 attribute is added to the learner/session model, JOIN it to this table
    -- (session_id) and group needs_review by L1 — do NOT add L1 here speculatively.
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (session_id, turn_number)
);
CREATE INDEX IF NOT EXISTS idx_speaking_reliability_session ON speaking_reliability(session_id);
CREATE INDEX IF NOT EXISTS idx_speaking_reliability_review ON speaking_reliability(org_id, needs_review);

GRANT SELECT, INSERT, UPDATE, DELETE ON speaking_reliability TO epistemy_app;
