CREATE TABLE IF NOT EXISTS variation_exercises (
    variation_id VARCHAR(64) PRIMARY KEY,
    mistake_id VARCHAR(64) NOT NULL,
    learner_id VARCHAR(128) NOT NULL,
    strategy VARCHAR(32) NOT NULL,
    level VARCHAR(32) NOT NULL,
    sequence INTEGER NOT NULL,
    question_payload_json JSONB NOT NULL,
    model_run_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR(32) NOT NULL DEFAULT 'ready',
    assessment VARCHAR(32),
    response_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    feedback TEXT NOT NULL DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL,
    answered_at DOUBLE PRECISION,
    UNIQUE (mistake_id, sequence)
);

CREATE INDEX IF NOT EXISTS idx_variation_exercises_learner
    ON variation_exercises (learner_id, created_at DESC);
