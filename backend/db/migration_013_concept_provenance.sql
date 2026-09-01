-- migration_013: concept provenance — two mappings, course-bounded.
--
--   (1) document -> concept-list -> concept-graph
--         document_concept        (per-document concepts)
--         document_concept_edge   (per-document relations)
--   (2) subject(course) -> its documents -> cumulative concept-list -> graph
--         course_concept          (deduped union of this course's document concepts,
--                                  with provenance back to the documents)
--         course_concept_edge     (the connected graph for the course)
--
-- The course graph is RECOMPUTED from only this course's document_concept rows on
-- every build, so off-subject concepts can never accumulate (fixes cross-course
-- leaks) and deleting a document cleanly drops its concepts. graph_version stays
-- as the published JSON snapshot the UI/exam read, derived from course_concept.
-- Tenant-scoped by org RLS like the other public tables. Idempotent.
BEGIN;

CREATE TABLE IF NOT EXISTS document_concept (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id              UUID NOT NULL,
    course_id           UUID NOT NULL REFERENCES course(course_id) ON DELETE CASCADE,
    material_version_id UUID NOT NULL,
    label               TEXT NOT NULL,
    definition          TEXT,
    abstraction_level   DOUBLE PRECISION,
    questions           JSONB NOT NULL DEFAULT '{}'::jsonb,   -- depth-tagged bank
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_document_concept_course ON document_concept(course_id);
CREATE INDEX IF NOT EXISTS idx_document_concept_matver ON document_concept(material_version_id);

CREATE TABLE IF NOT EXISTS document_concept_edge (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id              UUID NOT NULL,
    course_id           UUID NOT NULL REFERENCES course(course_id) ON DELETE CASCADE,
    material_version_id UUID NOT NULL,
    src_label           TEXT NOT NULL,
    dst_label           TEXT NOT NULL,
    edge_type           TEXT NOT NULL DEFAULT 'PREREQUISITE_FOR',
    confidence          DOUBLE PRECISION DEFAULT 0.8
);
CREATE INDEX IF NOT EXISTS idx_document_concept_edge_matver ON document_concept_edge(material_version_id);

CREATE TABLE IF NOT EXISTS course_concept (
    concept_id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id                      UUID NOT NULL,
    course_id                   UUID NOT NULL REFERENCES course(course_id) ON DELETE CASCADE,
    label                       TEXT NOT NULL,
    definition                  TEXT,
    abstraction_level           DOUBLE PRECISION,
    questions                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_material_version_ids JSONB NOT NULL DEFAULT '[]'::jsonb,   -- provenance
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (course_id, label)
);
CREATE INDEX IF NOT EXISTS idx_course_concept_course ON course_concept(course_id);

CREATE TABLE IF NOT EXISTS course_concept_edge (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     UUID NOT NULL,
    course_id  UUID NOT NULL REFERENCES course(course_id) ON DELETE CASCADE,
    src_label  TEXT NOT NULL,
    dst_label  TEXT NOT NULL,
    edge_type  TEXT NOT NULL DEFAULT 'PREREQUISITE_FOR',
    confidence DOUBLE PRECISION DEFAULT 0.8,
    UNIQUE (course_id, src_label, dst_label, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_course_concept_edge_course ON course_concept_edge(course_id);

-- RLS: tenant isolation by org, consistent with course/material/graph_version.
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['document_concept','document_concept_edge','course_concept','course_concept_edge'] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = t AND policyname = t || '_tenant') THEN
            EXECUTE format('CREATE POLICY %I ON %I USING (org_id = current_setting(''app.org_id'')::uuid)', t || '_tenant', t);
        END IF;
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON %I TO epistemy_app', t);
    END LOOP;
END $$;

COMMIT;
