CREATE TABLE IF NOT EXISTS review_tasks (
    task_id VARCHAR(64) PRIMARY KEY,
    mistake_id VARCHAR(64) NOT NULL,
    learner_id VARCHAR(128) NOT NULL,
    interval_days INTEGER NOT NULL,
    due_at DOUBLE PRECISION NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'scheduled',
    question_payload_json JSONB,
    model_run_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    assessment VARCHAR(32),
    feedback TEXT NOT NULL DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL,
    started_at DOUBLE PRECISION,
    completed_at DOUBLE PRECISION,
    UNIQUE (mistake_id, interval_days)
);

CREATE INDEX IF NOT EXISTS idx_review_tasks_learner_due
    ON review_tasks (learner_id, due_at);
