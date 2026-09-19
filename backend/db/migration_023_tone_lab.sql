-- migration_023: Examiner Tone Lab — persist tone-eval experiments and the governed
-- examiner-prompt override lifecycle. Same ownership rule as perf_probe (021) and
-- qg_test_run (019): the runtime role (epistemy_app) lacks CREATE on schema public, so
-- these are created here by the owner (migrate task) and granted to the runtime role.
-- A lazy runtime CREATE would 500 with "permission denied for schema public".

-- Each saved tone-lab run: the arms/questions/personas config it ran, the summary
-- tally (leak rates, tone flags, word counts per arm), the A0 prompt_version it was
-- measured against, and the server-computed recommendation.
CREATE TABLE IF NOT EXISTS tone_experiment (
    experiment_id  UUID PRIMARY KEY,
    org_id         UUID NOT NULL,
    title          TEXT,
    prompt_version TEXT,                 -- the A0/examiner prompt version under test
    config         JSONB,                -- arms, questions, personas, model, temp, reps
    summary        JSONB,                -- per-arm tally the lab computed
    recommendation JSONB,                -- server-side pick + ranked rationale
    created_by     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tone_experiment_org
    ON tone_experiment(org_id, created_at DESC);

-- The examiner-prompt override lifecycle. build_examiner_eval_prompt() reads the single
-- active row per org (else the shipped default). template holds the placeholder tokens
-- {{QUESTION_TEXT}}, {{EXPECTED_PATH_JSON}}, {{PROBE_DIRECTIVE}}.
CREATE TABLE IF NOT EXISTS examiner_prompt_override (
    override_id           UUID PRIMARY KEY,
    org_id                UUID NOT NULL,
    template              TEXT NOT NULL,
    notes                 TEXT,
    status                TEXT NOT NULL,   -- draft | approved | active | rejected | archived
    based_on_experiment_id UUID,
    created_by            TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reviewed_by           TEXT,
    reviewed_at           TIMESTAMPTZ,
    activated_at          TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_examiner_prompt_override_org
    ON examiner_prompt_override(org_id, created_at DESC);
-- At most one active override per org (the one production reads).
CREATE UNIQUE INDEX IF NOT EXISTS uq_examiner_prompt_override_active
    ON examiner_prompt_override(org_id) WHERE status = 'active';

GRANT SELECT, INSERT, UPDATE, DELETE ON tone_experiment TO epistemy_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON examiner_prompt_override TO epistemy_app;
