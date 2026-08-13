-- Epistemy M3 schema (T1): org, course, material, material_version, chunk.
-- RLS keyed on app.org_id; IVFFlat cosine index on the 1024-dim embedding.
-- Clean-slate rebuild: drop M3 tables first so migrate is reproducible.

DROP TABLE IF EXISTS chunk CASCADE;
DROP TABLE IF EXISTS material_version CASCADE;
DROP TABLE IF EXISTS material CASCADE;
DROP TABLE IF EXISTS async_job CASCADE;
DROP TABLE IF EXISTS course CASCADE;
DROP TABLE IF EXISTS org CASCADE;

CREATE EXTENSION IF NOT EXISTS vector;

-- Tenant + course registry. Human-readable names map to server-minted UUIDs.
-- org_name is globally unique; course_name is unique within an org.
CREATE TABLE IF NOT EXISTS org (
    org_id     UUID PRIMARY KEY,
    org_name   TEXT NOT NULL UNIQUE,
    title      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS course (
    course_id   UUID PRIMARY KEY,
    org_id      UUID NOT NULL REFERENCES org(org_id),
    course_name TEXT NOT NULL,
    title       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (org_id, course_name)
);

CREATE TABLE IF NOT EXISTS material (
    material_id        UUID PRIMARY KEY,
    course_id          UUID NOT NULL,
    org_id             UUID NOT NULL,
    created_by         TEXT NOT NULL,
    display_name       TEXT NOT NULL,
    current_version_id UUID,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS material_version (
    material_version_id UUID PRIMARY KEY,
    material_id         UUID NOT NULL REFERENCES material(material_id),
    course_id           UUID NOT NULL,
    org_id              UUID NOT NULL,
    version_no          INT  NOT NULL,
    uploaded_by         TEXT NOT NULL,
    source_type         TEXT NOT NULL,
    mime_type           TEXT NOT NULL,
    file_name           TEXT NOT NULL,
    s3_key              TEXT NOT NULL,
    bytes               BIGINT NOT NULL DEFAULT 0,
    checksum            TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    error               JSONB,
    ingest_job_id       UUID,
    superseded_at       TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (material_id, version_no),
    CONSTRAINT material_version_status_chk CHECK (status IN
        ('pending','uploaded','extracting','chunking','embedding','ready','failed'))
);

CREATE TABLE IF NOT EXISTS chunk (
    chunk_id            UUID PRIMARY KEY,
    material_version_id UUID NOT NULL REFERENCES material_version(material_version_id),
    course_id           UUID NOT NULL,
    org_id              UUID NOT NULL,
    chunk_index         INT  NOT NULL,
    text                TEXT NOT NULL,
    token_count         INT  NOT NULL,
    position            JSONB NOT NULL DEFAULT '{}',
    embedding           vector(1024),
    UNIQUE (material_version_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS chunk_embedding_ivfflat
    ON chunk USING ivfflat (embedding vector_cosine_ops) WITH (lists = 1000);

-- RLS: every row visible only to its own org via the app.org_id session var.
ALTER TABLE material          ENABLE ROW LEVEL SECURITY;
ALTER TABLE material_version  ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunk             ENABLE ROW LEVEL SECURITY;

CREATE POLICY material_org_isolation ON material
    USING (current_setting('app.org_id')::uuid = org_id);
CREATE POLICY material_version_org_isolation ON material_version
    USING (current_setting('app.org_id')::uuid = org_id);
CREATE POLICY chunk_org_isolation ON chunk
    USING (current_setting('app.org_id')::uuid = org_id);

-- async_job is S2-owned; included here so M3 runs end-to-end at pilot.
CREATE TABLE IF NOT EXISTS async_job (
    job_id       UUID PRIMARY KEY,
    org_id       UUID NOT NULL,
    course_id    UUID,
    type         TEXT NOT NULL DEFAULT 'ingest',
    status       TEXT NOT NULL DEFAULT 'queued',
    progress_pct INT  NOT NULL DEFAULT 0,
    step_name    TEXT,
    created_by   TEXT,
    error        JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
