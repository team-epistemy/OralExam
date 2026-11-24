-- Exam integrity logging (detect-and-flag): per-session counts of client-side
-- events that may indicate outside help — tab switches, window blur, paste,
-- copy, context menu, fullscreen exit. Surfaced to the professor in GradeView;
-- non-punitive. A browser cannot BLOCK other apps, so this is detection only.
ALTER TABLE exam_session
  ADD COLUMN IF NOT EXISTS integrity_flags JSONB NOT NULL DEFAULT '{}';
