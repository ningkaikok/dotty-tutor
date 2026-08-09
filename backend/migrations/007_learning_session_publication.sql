BEGIN;

-- v0.6.0 stored publication IDs in this column even though its name still
-- described a single lesson. The rename is data-preserving and idempotent.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'learning_sessions'
          AND column_name = 'lesson_id'
    ) AND NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'learning_sessions'
          AND column_name = 'publication_id'
    ) THEN
        ALTER TABLE learning_sessions
            RENAME COLUMN lesson_id TO publication_id;
    END IF;
END $$;

COMMIT;
