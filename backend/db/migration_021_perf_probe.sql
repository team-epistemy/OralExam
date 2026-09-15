-- migration_021: persist admin performance-probe records so latency runs are
-- saved and retrievable for analysis (previously in-memory only, lost on restart).
-- Denormalized p50 columns for quick listing/analysis; full detail (per-run
-- traces, test Q/A, summaries) in result JSONB.
-- Owner-created (migrate task); the runtime role lacks CREATE on schema public.
CREATE TABLE IF NOT EXISTS perf_probe (
    probe_id        UUID PRIMARY KEY,
    org_id          UUID NOT NULL,
    runs            INT  NOT NULL,
    status          TEXT NOT NULL,       -- running | completed | failed
    provider        TEXT,
    eval_model      TEXT,
    tts_model       TEXT,
    eval_p50_ms     INT,
    tts_p50_ms      INT,
    total_p50_ms    INT,
    path_penalty_ms INT,
    result          JSONB,               -- full result incl. per-run traces + test Q/A
    error           TEXT,
    created_by      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_perf_probe_org ON perf_probe(org_id, created_at DESC);

GRANT SELECT, INSERT, UPDATE, DELETE ON perf_probe TO epistemy_app;
