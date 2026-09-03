-- Professor dry-run sessions: excluded from results, purged on publish.
ALTER TABLE exam_session
  ADD COLUMN IF NOT EXISTS is_preview BOOLEAN NOT NULL DEFAULT false;
