-- migration_018: normalized per-turn transcript for agent simulations.
-- One row per (agent × question × Socratic turn). The full report also lives
-- in agent_simulation.report (JSONB, drives the dashboard), but this table is
-- the queryable analysis surface — GROUP BY / JOIN across runs to study the
-- evaluation approach without re-spending LLM tokens.
-- Created by the owner (migrate task); the runtime role lacks CREATE on public.
CREATE TABLE IF NOT EXISTS agent_simulation_turn (
    turn_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    simulation_id      UUID NOT NULL,
    org_id             UUID NOT NULL,
    assignment_id      UUID,
    agent_index        INT  NOT NULL,
    agent_skill        REAL,
    question_id        UUID,
    question_index     INT  NOT NULL,
    question_text      TEXT,
    topic              TEXT,
    round              INT  NOT NULL,
    is_probe           BOOLEAN NOT NULL DEFAULT FALSE,
    prompt             TEXT,          -- the question (round 1) or examiner probe
    answer             TEXT,          -- the agent's spoken answer this turn
    probe              TEXT,          -- the examiner's follow-up probe (if any)
    answered           BOOLEAN,
    adequate           BOOLEAN,
    recitation_score   REAL,          -- 0 authentic … 1 pure recitation
    nodes_demonstrated JSONB,         -- expected nodes shown this turn
    edges_demonstrated JSONB,         -- indices of expected edges shown this turn
    novel_extensions   JSONB,         -- reasoning beyond the expected path
    question_score     INT,           -- final EDS score for the question (0-100)
    rubric             JSONB,         -- expected reasoning path (what it was graded on)
    breakdown          JSONB,         -- EDS component breakdown for the question
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_sim_turn_sim ON agent_simulation_turn(simulation_id);
CREATE INDEX IF NOT EXISTS idx_agent_sim_turn_org ON agent_simulation_turn(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_sim_turn_asg ON agent_simulation_turn(assignment_id);

GRANT SELECT, INSERT, UPDATE, DELETE ON agent_simulation_turn TO epistemy_app;
