BEGIN;

CREATE TABLE IF NOT EXISTS lesson_documents (
    lesson_id VARCHAR(128) PRIMARY KEY,
    source_upload_id VARCHAR(64),
    title TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'draft',
    knowledge_points_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    blocks_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_sessions (
    session_id VARCHAR(64) PRIMARY KEY,
    learner_id VARCHAR(128) NOT NULL,
    lesson_id VARCHAR(128) NOT NULL,
    started_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS exercise_attempts (
    attempt_id VARCHAR(64) PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL REFERENCES learning_sessions(session_id) ON DELETE CASCADE,
    question_id VARCHAR(128) NOT NULL,
    knowledge_point VARCHAR(160) NOT NULL,
    response_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    assessment VARCHAR(32) NOT NULL,
    hint_level INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS mastery_states (
    learner_id VARCHAR(128) NOT NULL,
    knowledge_point VARCHAR(160) NOT NULL,
    score DOUBLE PRECISION NOT NULL DEFAULT 0,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    correct_count INTEGER NOT NULL DEFAULT 0,
    last_practiced_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (learner_id, knowledge_point)
);

CREATE INDEX IF NOT EXISTS idx_learning_sessions_learner
    ON learning_sessions (learner_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_exercise_attempts_session
    ON exercise_attempts (session_id, created_at DESC);

COMMIT;
