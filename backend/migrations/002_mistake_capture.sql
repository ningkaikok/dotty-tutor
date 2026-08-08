BEGIN;

CREATE TABLE IF NOT EXISTS mistake_items (
    mistake_id VARCHAR(64) PRIMARY KEY,
    learner_id VARCHAR(128) NOT NULL,
    source_filename TEXT NOT NULL,
    content_type VARCHAR(255) NOT NULL,
    source_image_path TEXT NOT NULL,
    source_image_url TEXT NOT NULL,
    question_payload_json JSONB NOT NULL,
    guide_cards_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    ocr_run_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_run_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    original_answer TEXT NOT NULL DEFAULT '',
    subject VARCHAR(80) NOT NULL DEFAULT '数学',
    grade_band VARCHAR(80) NOT NULL DEFAULT '初中',
    chapter TEXT NOT NULL,
    knowledge_point TEXT NOT NULL,
    error_reason VARCHAR(32),
    notes TEXT NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL DEFAULT 'pending_confirmation',
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    confirmed_at DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_mistake_items_learner
    ON mistake_items (learner_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_mistake_items_status
    ON mistake_items (learner_id, status);

COMMIT;
