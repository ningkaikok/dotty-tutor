"""Durable, isolated schema for voice tutor conversations.

Voice conversations are deliberately not learning attempts. Keeping their
transcripts and assistant prompts in this domain table prevents a confirmed
transcript from becoming answer or mastery evidence by accident.
"""

from sqlalchemy import Column, Float, Index, Integer, MetaData, String, Table, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

voice_metadata = MetaData()
json_document = JSON().with_variant(JSONB(), "postgresql")

voice_sessions = Table(
    "voice_sessions",
    voice_metadata,
    Column("session_id", String(36), primary_key=True),
    Column("capability_hash", String(64), nullable=False),
    Column("learner_id", String(128), nullable=False),
    Column("chapter_id", String(128), nullable=False),
    Column("source_kind", String(16), nullable=False, server_default="chapter"),
    Column("courseware_id", String(64)),
    Column("publication_id", String(128), nullable=False),
    Column("lesson_id", String(128), nullable=False),
    Column("block_id", String(128), nullable=False),
    Column("generation", Integer, nullable=False, default=0, server_default="0"),
    Column("active_turn_id", String(128)),
    Column("draft_json", json_document),
    Column("confirmed_json", json_document),
    Column(
        "turns_json", json_document, nullable=False, default=list, server_default="[]"
    ),
    Column("created_at", Float, nullable=False),
    Column("updated_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("context_label", Text, nullable=False, default="", server_default=""),
)

Index("idx_voice_sessions_expiry", voice_sessions.c.expires_at)
Index(
    "idx_voice_sessions_scope",
    voice_sessions.c.publication_id,
    voice_sessions.c.lesson_id,
    voice_sessions.c.block_id,
)
