-- Phase B of transcription-review: store the S3 key of the recorded audio for a
-- spoken answer so a professor can re-listen and confirm a suspected mishear.
-- Authenticated exam sessions only; null when no audio was captured.
ALTER TABLE session_turn
  ADD COLUMN IF NOT EXISTS audio_key TEXT;
