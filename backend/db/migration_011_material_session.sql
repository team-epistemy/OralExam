-- migration_011: map every material to a class session.
--
-- New uploads always set material.session_id (a session is created if none is
-- given). Nullable so legacy materials remain valid; ON DELETE SET NULL so
-- deleting a session detaches its materials rather than deleting them.
-- Idempotent (IF NOT EXISTS), safe to re-run.
BEGIN;

ALTER TABLE material
    ADD COLUMN IF NOT EXISTS session_id UUID
    REFERENCES class_session(session_id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_material_session ON material(session_id);

COMMIT;
