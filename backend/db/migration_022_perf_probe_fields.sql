-- migration_022: add experiment Title + Params to perf_probe records, so each
-- perf-eval attempt is labeled and records the exact params it ran with (seeded
-- from the backend's actual implementation). ALTER needs the owner (migrate task).
ALTER TABLE perf_probe ADD COLUMN IF NOT EXISTS title  TEXT;
ALTER TABLE perf_probe ADD COLUMN IF NOT EXISTS params JSONB;
