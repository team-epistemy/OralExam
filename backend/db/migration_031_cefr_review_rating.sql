-- migration_031: extend cefr_calibration into a full human CEFR *rating* record so
-- an examiner can review a speaking attempt (transcript + acoustic metrics +
-- reliability signals) and record a complete, multi-rater CEFR assessment.
--
-- The table already keys one rater's band override by (session, turn, dimension,
-- rater) — exactly the grain inter-rater agreement needs. This migration adds the
-- rating-level attributes a review carries beyond a single band:
--   * notes                — the rater's free-text justification for the attempt.
--   * corrected_transcript — the rater's transcript correction (per rater, so two
--                            raters can disagree; distinct from
--                            speaking_reliability.corrected_transcript, which is a
--                            single per-attempt field with no rater grain).
--   * created_at           — first-rated timestamp (updated_at already tracks the
--                            last edit); lets us order ratings and audit history.
--
-- notes + corrected_transcript are rating-level (per attempt+rater), not per
-- dimension. A rating is submitted as one row per dimension (fluency / range /
-- accuracy / coherence / overall), all sharing the same (session, turn, rater);
-- the save endpoint writes the same notes + corrected_transcript onto every row
-- of that group, so a read can recover them from any one row. This keeps a SINGLE
-- table (no new table, no duplication of the band grain) and preserves the
-- existing upsert key.
--
-- DELIBERATELY NOT ADDED: an `overall_band` column. "overall" is already a valid
-- CEFR dimension (see _CEFR_DIMENSIONS), so the overall band is stored as the
-- dimension='overall' row — adding a separate column would duplicate it and split
-- the source of truth.
--
-- Owner-created (migrate task); the runtime role (epistemy_app) lacks CREATE /
-- ALTER on public, so this is an owner-run migration, NOT a lazy runtime ALTER.
-- All statements are idempotent (ADD COLUMN IF NOT EXISTS).

ALTER TABLE cefr_calibration ADD COLUMN IF NOT EXISTS notes TEXT;
ALTER TABLE cefr_calibration ADD COLUMN IF NOT EXISTS corrected_transcript TEXT;
ALTER TABLE cefr_calibration ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- List "attempts to rate" is driven off speaking_reliability (needs_review first);
-- the current ratings are then looked up per attempt. Index the lookup key.
CREATE INDEX IF NOT EXISTS idx_cefr_calibration_rater ON cefr_calibration(session_id, rater_id);

-- The table has no CREATE-time grant beyond migration_027's; re-assert it so the
-- runtime role can read/write the new columns (GRANT is table-wide, but keep this
-- explicit + idempotent alongside the ALTERs).
GRANT SELECT, INSERT, UPDATE, DELETE ON cefr_calibration TO epistemy_app;
