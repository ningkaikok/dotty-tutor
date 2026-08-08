CREATE TABLE IF NOT EXISTS tutor_threads (
    thread_id VARCHAR(64) PRIMARY KEY,
    mistake_id VARCHAR(64) NOT NULL,
    learner_id VARCHAR(128) NOT NULL,
    stage VARCHAR(32) NOT NULL DEFAULT 'diagnose',
    summary TEXT NOT NULL DEFAULT '',
    hint_level INTEGER NOT NULL DEFAULT 0,
    message_count INTEGER NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    CONSTRAINT uq_tutor_threads_mistake_learner UNIQUE (mistake_id, learner_id)
);

CREATE TABLE IF NOT EXISTS tutor_messages (
    message_id VARCHAR(64) PRIMARY KEY,
    thread_id VARCHAR(64) NOT NULL REFERENCES tutor_threads(thread_id) ON DELETE CASCADE,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    input_mode VARCHAR(32) NOT NULL DEFAULT 'text',
    assessment VARCHAR(32),
    action_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    model_run_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tutor_threads_updated
    ON tutor_threads (learner_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_tutor_messages_thread
    ON tutor_messages (thread_id, created_at);
