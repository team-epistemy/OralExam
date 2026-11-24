-- Per-session question overrides: when a stressed student triggers the backup
-- flow, the replacement question (text + concept + expected_path) is stored here
-- keyed by position, instead of mutating the shared question_set. submit_answer
-- reads this before the question_set lookup. Empty for a normal session.
ALTER TABLE exam_session
  ADD COLUMN IF NOT EXISTS question_overrides JSONB NOT NULL DEFAULT '{}';
