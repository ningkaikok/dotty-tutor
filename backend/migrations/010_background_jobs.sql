-- Generic durable work queue.  ``upload_jobs`` remains upload metadata and
-- is intentionally not used as an execution queue.
CREATE TABLE IF NOT EXISTS background_jobs (
    job_id VARCHAR(64) PRIMARY KEY,
    job_type VARCHAR(128) NOT NULL,
    idempotency_key VARCHAR(255),
    payload_json JSONB NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
    max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_error_json JSONB,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    lease_owner VARCHAR(128),
    lease_expires_at DOUBLE PRECISION,
    result_json JSONB,
    progress INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    started_at DOUBLE PRECISION,
    completed_at DOUBLE PRECISION
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_background_jobs_idempotency
    ON background_jobs (idempotency_key);
CREATE INDEX IF NOT EXISTS idx_background_jobs_claim
    ON background_jobs (status, lease_expires_at, created_at);
CREATE INDEX IF NOT EXISTS idx_background_jobs_type
    ON background_jobs (job_type, created_at DESC);

ALTER TABLE background_jobs ADD COLUMN IF NOT EXISTS progress INTEGER NOT NULL DEFAULT 0;
ALTER TABLE background_jobs ADD COLUMN IF NOT EXISTS message TEXT NOT NULL DEFAULT '';
