-- migration_019: qg_test_run history table for the admin QG (question-generation)
-- quality test bench. Same fix as agent_simulation (migration_017): the runtime
-- role (epistemy_app) lacks CREATE on schema public, so the lazy
-- _ensure_qg_test_table 500s ("permission denied for schema public"). Create it
-- here as the owner (migrate task) + grant read/write to the runtime role.
CREATE TABLE IF NOT EXISTS qg_test_run (
    run_id          UUID PRIMARY KEY,
    org_id          UUID NOT NULL,
    course_id       UUID NOT NULL,
    course_name     TEXT,
    difficulty      TEXT NOT NULL,
    requested_count INT  NOT NULL,
    generated_count INT  NOT NULL,
    report          JSONB,
    status          TEXT NOT NULL,
    error           TEXT,
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_qg_test_run_org ON qg_test_run(org_id, created_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON qg_test_run TO epistemy_app;
