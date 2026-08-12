-- migration_003_eds_formula.sql
-- Adds schema changes for the real EDS (Epistemic Depth Score) formula.
-- Idempotent: safe to re-run. Uses IF NOT EXISTS / IF EXISTS patterns throughout.

BEGIN;

-- =============================================================================
-- 1. Add `expected_path` column to question table
--    Stores the expected reasoning graph per question as JSONB:
--    { "nodes": [...], "edges": [...], "extensions": [...] }
-- =============================================================================

ALTER TABLE question ADD COLUMN IF NOT EXISTS expected_path JSONB NOT NULL DEFAULT '{}';


-- =============================================================================
-- 2. Add `sub_turn_index` to session_turn table
--    Supports 3 sub-turns per question (initial + 2 follow-ups)
-- =============================================================================

ALTER TABLE session_turn ADD COLUMN IF NOT EXISTS sub_turn_index INT NOT NULL DEFAULT 0;

-- Drop old unique constraint if it exists and create new composite one
DO $$ BEGIN
    -- Try to drop old constraint (various names it might have)
    BEGIN
        ALTER TABLE session_turn DROP CONSTRAINT IF EXISTS session_turn_session_id_turn_index_key;
    EXCEPTION WHEN undefined_object THEN NULL;
    END;
    BEGIN
        DROP INDEX IF EXISTS session_turn_session_id_turn_index_key;
    EXCEPTION WHEN undefined_object THEN NULL;
    END;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS session_turn_unique_idx
    ON session_turn(session_id, turn_index, sub_turn_index);


-- =============================================================================
-- 3. Add `eds_components` column to evaluation table
--    Stores per-turn EDS breakdown (node_hits, edge_hits, etc.)
-- =============================================================================

ALTER TABLE evaluation ADD COLUMN IF NOT EXISTS eds_components JSONB NOT NULL DEFAULT '{}';


-- =============================================================================
-- 4. Create question_eds_aggregate table
--    Stores the final aggregated EDS per question per session
-- =============================================================================

CREATE TABLE IF NOT EXISTS question_eds_aggregate (
    aggregate_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES exam_session(session_id),
    question_id     UUID NOT NULL REFERENCES question(question_id),
    org_id          UUID NOT NULL REFERENCES org(org_id),
    node_score      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    edge_score      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    r_gate          DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    gen_score_norm  DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    coverage        DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    final_eds       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    turn_details    JSONB NOT NULL DEFAULT '[]',
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (session_id, question_id)
);

CREATE INDEX IF NOT EXISTS qea_session_idx ON question_eds_aggregate(session_id);
CREATE INDEX IF NOT EXISTS qea_org_idx ON question_eds_aggregate(org_id);

ALTER TABLE question_eds_aggregate ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'question_eds_aggregate' AND policyname = 'qea_tenant'
    ) THEN
        CREATE POLICY qea_tenant ON question_eds_aggregate
            USING (org_id = current_setting('app.org_id')::uuid);
    END IF;
END $$;


-- =============================================================================
-- 5. Add `points` column to question table
--    Default point value per question for weighted scoring
-- =============================================================================

ALTER TABLE question ADD COLUMN IF NOT EXISTS points INT NOT NULL DEFAULT 1;


COMMIT;
