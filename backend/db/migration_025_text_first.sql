-- migration_025: add the text_first render toggle to examiner_config. When true
-- (default), the student exam UI renders the examiner probe immediately and plays TTS
-- asynchronously; when false, the probe text is revealed together with the audio.
-- Owner-run ALTER (migrate task). Column default keeps existing rows on the optimization.
ALTER TABLE examiner_config ADD COLUMN IF NOT EXISTS text_first BOOLEAN NOT NULL DEFAULT true;
