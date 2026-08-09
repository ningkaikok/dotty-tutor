"""SQLAlchemy Core tables shared by textbook and learning stores.

Keeping declarations separate from CRUD code gives learners one compact place
to inspect the relational model and lets migrations be compared with runtime
metadata. New product domains such as mistakes and tutoring intentionally keep
their own schemas next to their stores.
"""

from sqlalchemy import BigInteger, Column, Float, ForeignKey, Index, Integer, JSON, MetaData, String, Table, Text
from sqlalchemy.dialects.postgresql import JSONB


metadata = MetaData()
json_document = JSON().with_variant(JSONB(), "postgresql")

upload_jobs = Table(
    "upload_jobs", metadata,
    Column("upload_id", String(64), primary_key=True),
    Column("import_id", String(128)),
    Column("filename", Text, nullable=False),
    Column("content_type", String(255), nullable=False),
    Column("size", BigInteger, nullable=False),
    Column("chunk_size", Integer, nullable=False),
    Column("total_chunks", Integer, nullable=False),
    Column("source_text", Text, nullable=False, default=""),
    Column("directory", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("progress", Integer, nullable=False, default=0),
    Column("message", Text, nullable=False, default=""),
    Column("result_json", json_document),
    Column("started_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("completed_at", Float),
)

batch_questions = Table(
    "batch_questions", metadata,
    Column("upload_id", String(64), ForeignKey("upload_jobs.upload_id", ondelete="CASCADE"), primary_key=True),
    Column("batch_id", String(128), primary_key=True),
    Column("question_id", String(128), nullable=False),
    Column("payload_json", json_document, nullable=False),
    Column("guide_cards_json", json_document, nullable=False, default=list),
    Column("created_at", Float, nullable=False),
)

lesson_documents = Table(
    "lesson_documents", metadata,
    Column("lesson_id", String(128), primary_key=True),
    Column("source_upload_id", String(64)),
    Column("title", Text, nullable=False),
    Column("version", Integer, nullable=False, default=1),
    Column("status", String(32), nullable=False, default="draft"),
    Column("knowledge_points_json", json_document, nullable=False, default=list),
    Column("blocks_json", json_document, nullable=False, default=list),
    Column("question_json", json_document, nullable=False, default=dict),
    Column("guide_cards_json", json_document, nullable=False, default=list),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

lesson_publications = Table(
    "lesson_publications", metadata,
    Column("publication_id", String(64), primary_key=True),
    Column("title", Text, nullable=False),
    Column("source_upload_id", String(64)),
    Column("lesson_ids_json", json_document, nullable=False, default=list),
    Column("status", String(32), nullable=False, default="draft"),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

learning_sessions = Table(
    "learning_sessions", metadata,
    Column("session_id", String(64), primary_key=True),
    Column("learner_id", String(128), nullable=False),
    Column("lesson_id", String(128), nullable=False),
    Column("started_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

exercise_attempts = Table(
    "exercise_attempts", metadata,
    Column("attempt_id", String(64), primary_key=True),
    Column("session_id", String(64), ForeignKey("learning_sessions.session_id", ondelete="CASCADE"), nullable=False),
    Column("question_id", String(128), nullable=False),
    Column("knowledge_point", String(160), nullable=False),
    Column("response_json", json_document, nullable=False, default=dict),
    Column("assessment", String(32), nullable=False),
    Column("hint_level", Integer, nullable=False, default=0),
    Column("duration_ms", Integer, nullable=False, default=0),
    Column("created_at", Float, nullable=False),
)

mastery_states = Table(
    "mastery_states", metadata,
    Column("learner_id", String(128), primary_key=True),
    Column("knowledge_point", String(160), primary_key=True),
    Column("score", Float, nullable=False, default=0),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("correct_count", Integer, nullable=False, default=0),
    Column("last_practiced_at", Float, nullable=False),
)

Index("idx_upload_jobs_updated", upload_jobs.c.updated_at.desc())
Index("idx_lesson_publications_status", lesson_publications.c.status, lesson_publications.c.updated_at.desc())
Index("idx_learning_sessions_learner", learning_sessions.c.learner_id, learning_sessions.c.updated_at.desc())
Index("idx_exercise_attempts_session", exercise_attempts.c.session_id, exercise_attempts.c.created_at.desc())
