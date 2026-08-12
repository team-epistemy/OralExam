-- Migration 004: mark a course graph as stale when it no longer matches its material.
--
-- Set on material delete (the delete commits immediately; the rebuild runs in a
-- background thread that can fail silently). Cleared when a rebuild succeeds.
-- Without this the UI cannot distinguish a current graph from one whose rebuild
-- never landed.

ALTER TABLE graph_version ADD COLUMN IF NOT EXISTS is_stale BOOLEAN NOT NULL DEFAULT FALSE;

-- Partial index: the only query is "is the active graph for this course stale?"
CREATE INDEX IF NOT EXISTS idx_graph_version_stale
    ON graph_version(org_id, course_id) WHERE is_stale = TRUE;
