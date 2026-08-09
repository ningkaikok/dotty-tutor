BEGIN;

ALTER TABLE lesson_documents
    ADD COLUMN IF NOT EXISTS question_json JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE lesson_documents
    ADD COLUMN IF NOT EXISTS guide_cards_json JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS lesson_publications (
    publication_id VARCHAR(64) PRIMARY KEY,
    title TEXT NOT NULL,
    source_upload_id VARCHAR(64),
    lesson_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_lesson_publications_status
    ON lesson_publications (status, updated_at DESC);

COMMIT;
