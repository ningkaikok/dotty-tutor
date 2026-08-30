"""SQLAlchemy Core tables shared by textbook and learning stores.

Keeping declarations separate from CRUD code gives learners one compact place
to inspect the relational model and lets migrations be compared with runtime
metadata. New product domains such as mistakes and tutoring intentionally keep
their own schemas next to their stores.
"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
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

# Generic executable work is deliberately separate from upload metadata.  An
# upload may produce several jobs (OCR, generation, review), while its upload
# row remains the durable domain record and is not a queue state machine.
background_jobs = Table(
    "background_jobs", metadata,
    Column("job_id", String(64), primary_key=True),
    Column("job_type", String(128), nullable=False),
    Column("idempotency_key", String(255)),
    Column("payload_json", json_document, nullable=False),
    Column("status", String(16), nullable=False, default="queued"),
    Column("max_attempts", Integer, nullable=False, default=3),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("last_error_json", json_document),
    Column("cancel_requested", Boolean, nullable=False, default=False),
    Column("lease_owner", String(128)),
    Column("lease_expires_at", Float),
    Column("result_json", json_document),
    Column("progress", Integer, nullable=False, default=0),
    Column("message", Text, nullable=False, default=""),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("started_at", Float),
    Column("completed_at", Float),
    CheckConstraint(
        "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
        name="ck_background_jobs_status",
    ),
    CheckConstraint("max_attempts > 0", name="ck_background_jobs_max_attempts"),
    CheckConstraint("attempt_count >= 0", name="ck_background_jobs_attempt_count"),
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
    Column("version", Integer, nullable=False, default=1),
    Column("revision_of", String(64)),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

learning_classes = Table(
    "learning_classes", metadata,
    Column("class_id", String(64), primary_key=True),
    Column("name", String(160), nullable=False),
    Column("subject", String(80), nullable=False, default="数学"),
    Column("grade_band", String(80), nullable=False, default="初中"),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

class_memberships = Table(
    "class_memberships", metadata,
    Column("class_id", String(64), ForeignKey("learning_classes.class_id", ondelete="CASCADE"), primary_key=True),
    Column("learner_id", String(128), primary_key=True),
    Column("display_name", String(160), nullable=False),
    Column("joined_at", Float, nullable=False),
)

assignments = Table(
    "assignments", metadata,
    Column("assignment_id", String(64), primary_key=True),
    Column("class_id", String(64), ForeignKey("learning_classes.class_id", ondelete="CASCADE"), nullable=False),
    Column("publication_id", String(64), ForeignKey("lesson_publications.publication_id"), nullable=False),
    # A confirmed plan is the idempotency boundary for teacher assignment creation.
    Column("assignment_plan_id", String(64), ForeignKey("assignment_plans.plan_id"), nullable=True),
    Column("title", String(200), nullable=False),
    Column("due_at", Float),
    Column("status", String(32), nullable=False, default="active"),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

assignment_plans = Table(
    "assignment_plans", metadata,
    Column("plan_id", String(64), primary_key=True),
    Column("class_id", String(64), ForeignKey("learning_classes.class_id", ondelete="CASCADE"), nullable=False),
    Column("publication_id", String(64), ForeignKey("lesson_publications.publication_id"), nullable=False),
    Column("publication_version", Integer, nullable=False),
    Column("source_fingerprint", String(128), nullable=False),
    Column("status", String(32), nullable=False, default="draft"),
    Column("input_snapshot_json", json_document, nullable=False),
    Column("result_json", json_document, nullable=False),
    Column("warnings_json", json_document, nullable=False, default=list),
    Column("run_id", String(64), nullable=True),
    Column("assignment_id", String(64), nullable=True),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("confirmed_at", Float),
)

knowledge_points = Table(
    "knowledge_points", metadata,
    Column("knowledge_point_id", String(64), primary_key=True),
    Column("publication_id", String(128), ForeignKey("lesson_publications.publication_id", ondelete="CASCADE"), nullable=False),
    Column("name", String(160), nullable=False),
    Column("normalized_name", String(160), nullable=False),
    Column("created_at", Float, nullable=False),
)

run_snapshots = Table(
    "run_snapshots", metadata,
    Column("run_id", String(64), primary_key=True),
    Column("operation", String(64), nullable=False),
    Column("scope", String(64), nullable=False),
    Column("target_upload_id", String(64)),
    Column("target_question_key", String(255)),
    Column("target_publication_id", String(64)),
    Column("status", String(16), nullable=False, default="running"),
    Column("config_json", json_document, nullable=False),
    Column("result_json", json_document),
    Column("error_json", json_document),
    Column("started_at", Float, nullable=False),
    Column("completed_at", Float),
)

question_revisions = Table(
    "question_revisions", metadata,
    Column("revision_id", String(64), primary_key=True),
    Column("upload_id", String(64), ForeignKey("upload_jobs.upload_id", ondelete="CASCADE"), nullable=False),
    Column("source_question_key", String(255), nullable=False),
    Column("revision_number", Integer, nullable=False),
    Column("operation", String(64), nullable=False),
    Column("previous_revision_id", String(64)),
    Column("payload_json", json_document, nullable=False),
    Column("guide_cards_json", json_document, nullable=False, default=list),
    Column("run_id", String(64), nullable=False),
    Column("created_at", Float, nullable=False),
)

learning_sessions = Table(
    "learning_sessions", metadata,
    Column("session_id", String(64), primary_key=True),
    Column("learner_id", String(128), nullable=False),
    Column("publication_id", String(128), nullable=False),
    # 新作答必须保留作业实例，旧的自由练习会话允许为空以兼容历史数据。
    Column("assignment_id", String(64), ForeignKey("assignments.assignment_id"), nullable=True),
    Column("started_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
)

exercise_attempts = Table(
    "exercise_attempts", metadata,
    Column("attempt_id", String(64), primary_key=True),
    Column("session_id", String(64), ForeignKey("learning_sessions.session_id", ondelete="CASCADE"), nullable=False),
    Column("publication_id", String(128), nullable=True),
    Column("question_id", String(128), nullable=False),
    Column("knowledge_point_id", String(64), ForeignKey("knowledge_points.knowledge_point_id"), nullable=True),
    # Legacy display value. New writes derive it from knowledge_points and never trust it as an ID.
    Column("knowledge_point", String(160), nullable=True),
    Column("response_json", json_document, nullable=False, default=dict),
    Column("assessment", String(32), nullable=False),
    Column("hint_level", Integer, nullable=False, default=0),
    Column("duration_ms", Integer, nullable=False, default=0),
    Column("created_at", Float, nullable=False),
)

mastery_states = Table(
    "mastery_states", metadata,
    Column("learner_id", String(128), primary_key=True),
    Column("knowledge_point_id", String(64), ForeignKey("knowledge_points.knowledge_point_id"), primary_key=True),
    Column("knowledge_point", String(160), nullable=True),
    Column("score", Float, nullable=False, default=0),
    Column("raw_score", Float, nullable=False, default=0),
    Column("evidence_confidence", Float, nullable=False, default=0),
    Column("evidence_count", Integer, nullable=False, default=0),
    Column("algorithm_version", String(64), nullable=False, default="mastery-v2"),
    Column("computed_at", Float, nullable=False, default=0),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("correct_count", Integer, nullable=False, default=0),
    Column("last_practiced_at", Float),
)

Index("idx_upload_jobs_updated", upload_jobs.c.updated_at.desc())
Index("uq_background_jobs_idempotency", background_jobs.c.idempotency_key, unique=True)
Index("idx_background_jobs_claim", background_jobs.c.status, background_jobs.c.lease_expires_at, background_jobs.c.created_at)
Index("idx_background_jobs_type", background_jobs.c.job_type, background_jobs.c.created_at.desc())
Index("idx_run_snapshots_target", run_snapshots.c.target_upload_id, run_snapshots.c.started_at.desc())
Index("idx_run_snapshots_operation", run_snapshots.c.operation, run_snapshots.c.started_at.desc())
Index("idx_question_revisions_source", question_revisions.c.upload_id, question_revisions.c.source_question_key, question_revisions.c.revision_number.desc())
Index("uq_question_revisions_source_number", question_revisions.c.upload_id, question_revisions.c.source_question_key, question_revisions.c.revision_number, unique=True)
Index("idx_lesson_publications_status", lesson_publications.c.status, lesson_publications.c.updated_at.desc())
Index("idx_lesson_publications_revision", lesson_publications.c.revision_of, lesson_publications.c.version.desc())
Index("idx_class_memberships_learner", class_memberships.c.learner_id, class_memberships.c.class_id)
Index("idx_assignments_class", assignments.c.class_id, assignments.c.created_at.desc())
Index("idx_assignments_publication", assignments.c.publication_id, assignments.c.created_at.desc())
Index("idx_assignments_plan", assignments.c.assignment_plan_id, assignments.c.created_at.desc())
Index("idx_assignment_plans_class", assignment_plans.c.class_id, assignment_plans.c.created_at.desc())
Index("idx_learning_sessions_learner", learning_sessions.c.learner_id, learning_sessions.c.updated_at.desc())
Index("idx_learning_sessions_assignment", learning_sessions.c.assignment_id, learning_sessions.c.learner_id, learning_sessions.c.updated_at.desc())
Index("idx_exercise_attempts_session", exercise_attempts.c.session_id, exercise_attempts.c.created_at.desc())
Index("uq_knowledge_points_publication_name", knowledge_points.c.publication_id, knowledge_points.c.normalized_name, unique=True)
Index("idx_exercise_attempts_publication_question", exercise_attempts.c.publication_id, exercise_attempts.c.question_id, exercise_attempts.c.created_at.desc())
Index("uq_mastery_states_learner_knowledge_point", mastery_states.c.learner_id, mastery_states.c.knowledge_point_id, unique=True)
