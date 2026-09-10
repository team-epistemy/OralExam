-- migration_017: agent_simulation table for the admin agent-cohort exam
-- simulator. Created here (by the owner, via the migrate task) rather than
-- lazily at runtime, because the non-owner runtime role (epistemy_app) lacks
-- CREATE on schema public — the lazy _ensure_agent_sim_table 500s otherwise.
CREATE TABLE IF NOT EXISTS agent_simulation (
    simulation_id UUID PRIMARY KEY,
    org_id        UUID NOT NULL,
    assignment_id UUID NOT NULL,
    course_id     UUID,
    num_agents    INT NOT NULL,
    curve         TEXT NOT NULL,
    status        TEXT NOT NULL,
    progress      JSONB,
    report        JSONB,
    error         TEXT,
    created_by    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_simulation_org ON agent_simulation(org_id, created_at DESC);

-- Runtime role needs read/write (queried with an explicit org_id filter).
GRANT SELECT, INSERT, UPDATE, DELETE ON agent_simulation TO epistemy_app;
