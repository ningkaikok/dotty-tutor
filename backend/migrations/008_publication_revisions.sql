BEGIN;

ALTER TABLE lesson_publications
    ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE lesson_publications
    ADD COLUMN IF NOT EXISTS revision_of VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_lesson_publications_revision
    ON lesson_publications (revision_of, version DESC);

COMMIT;
