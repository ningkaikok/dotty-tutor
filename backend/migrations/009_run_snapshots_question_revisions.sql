-- Immutable content-production audit evidence.
-- A run's config and a question revision are append-only. Only a running run
-- may transition once to succeeded or failed; no update endpoint is provided.
CREATE TABLE IF NOT EXISTS run_snapshots (
    run_id VARCHAR(64) PRIMARY KEY,
    operation VARCHAR(64) NOT NULL,
    scope VARCHAR(64) NOT NULL,
    target_upload_id VARCHAR(64),
    target_question_key VARCHAR(255),
    target_publication_id VARCHAR(64),
    status VARCHAR(16) NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    config_json JSONB NOT NULL,
    result_json JSONB,
    error_json JSONB,
    started_at DOUBLE PRECISION NOT NULL,
    completed_at DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_run_snapshots_target
    ON run_snapshots (target_upload_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_snapshots_operation
    ON run_snapshots (operation, started_at DESC);

CREATE TABLE IF NOT EXISTS question_revisions (
    revision_id VARCHAR(64) PRIMARY KEY,
    upload_id VARCHAR(64) NOT NULL REFERENCES upload_jobs(upload_id) ON DELETE CASCADE,
    source_question_key VARCHAR(255) NOT NULL,
    revision_number INTEGER NOT NULL CHECK (revision_number > 0),
    operation VARCHAR(64) NOT NULL,
    previous_revision_id VARCHAR(64),
    payload_json JSONB NOT NULL,
    guide_cards_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    run_id VARCHAR(64) NOT NULL REFERENCES run_snapshots(run_id),
    created_at DOUBLE PRECISION NOT NULL,
    UNIQUE (upload_id, source_question_key, revision_number)
);

CREATE INDEX IF NOT EXISTS idx_question_revisions_source
    ON question_revisions (upload_id, source_question_key, revision_number DESC);
