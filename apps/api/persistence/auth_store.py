"""Opaque, revocable browser sessions and one-time learner invitations."""

from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    MetaData,
    String,
    Table,
    select,
    update,
)
from sqlalchemy.engine import Engine

auth_metadata = MetaData()
auth_sessions = Table(
    "auth_sessions", auth_metadata,
    Column("session_id", String(64), primary_key=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("role", String(16), nullable=False),
    Column("learner_id", String(128)),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("revoked_at", Float),
    CheckConstraint("role IN ('teacher', 'student')", name="ck_auth_sessions_role"),
    CheckConstraint(
        "(role = 'teacher' AND learner_id IS NULL) OR (role = 'student' AND learner_id IS NOT NULL)",
        name="ck_auth_sessions_identity",
    ),
)
auth_invites = Table(
    "auth_invites", auth_metadata,
    Column("invite_hash", String(64), primary_key=True),
    Column("learner_id", String(128), nullable=False),
    Column("created_by_session_id", String(64), ForeignKey("auth_sessions.session_id"), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
    Column("consumed_at", Float),
)
Index("idx_auth_sessions_expiry", auth_sessions.c.expires_at)
Index("idx_auth_invites_expiry", auth_invites.c.expires_at)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthSessionStore:
    """Persist only hashes of high-entropy session and invitation tokens."""

    def __init__(self, *, engine: Engine) -> None:
        self.engine = engine

    def create_session(self, *, role: str, learner_id: str | None, lifetime_seconds: int = 43_200) -> tuple[str, dict[str, Any]]:
        if role not in {"teacher", "student"} or (role == "student") != bool(learner_id):
            raise ValueError("无效的会话角色")
        token = secrets.token_urlsafe(32)
        now = time.time()
        item = {
            "sessionId": uuid.uuid4().hex,
            "role": role,
            "learnerId": learner_id,
            "createdAt": now,
            "expiresAt": now + lifetime_seconds,
        }
        with self.engine.begin() as connection:
            connection.execute(auth_sessions.insert().values(
                session_id=item["sessionId"], token_hash=_hash(token), role=role,
                learner_id=learner_id, created_at=now, expires_at=item["expiresAt"],
                revoked_at=None,
            ))
        return token, item

    def resolve(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self.engine.connect() as connection:
            row = connection.execute(select(auth_sessions).where(
                auth_sessions.c.token_hash == _hash(token),
                auth_sessions.c.revoked_at.is_(None),
                auth_sessions.c.expires_at > time.time(),
            )).mappings().first()
        if not row:
            return None
        return {
            "sessionId": row["session_id"], "role": row["role"],
            "learnerId": row["learner_id"], "expiresAt": row["expires_at"],
        }

    def create_invite(self, *, learner_id: str, teacher_session_id: str, lifetime_seconds: int = 86_400) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.engine.begin() as connection:
            connection.execute(auth_invites.insert().values(
                invite_hash=_hash(token), learner_id=learner_id,
                created_by_session_id=teacher_session_id, created_at=now,
                expires_at=now + lifetime_seconds, consumed_at=None,
            ))
        return token

    def consume_invite(self, token: str) -> tuple[str, dict[str, Any]] | None:
        now = time.time()
        with self.engine.begin() as connection:
            invite = connection.execute(select(auth_invites).where(
                auth_invites.c.invite_hash == _hash(token),
                auth_invites.c.consumed_at.is_(None),
                auth_invites.c.expires_at > now,
            ).with_for_update()).mappings().first()
            if not invite:
                return None
            claimed = connection.execute(update(auth_invites).where(
                auth_invites.c.invite_hash == invite["invite_hash"],
                auth_invites.c.consumed_at.is_(None),
            ).values(consumed_at=now))
            if claimed.rowcount != 1:
                return None
            raw_session = secrets.token_urlsafe(32)
            item = {
                "sessionId": uuid.uuid4().hex, "role": "student",
                "learnerId": invite["learner_id"], "createdAt": now,
                "expiresAt": now + 43_200,
            }
            connection.execute(auth_sessions.insert().values(
                session_id=item["sessionId"], token_hash=_hash(raw_session),
                role="student", learner_id=item["learnerId"], created_at=now,
                expires_at=item["expiresAt"], revoked_at=None,
            ))
        return raw_session, item

    def revoke(self, session_id: str, *, now: float | None = None) -> bool:
        with self.engine.begin() as connection:
            result = connection.execute(update(auth_sessions).where(
                auth_sessions.c.session_id == session_id,
                auth_sessions.c.revoked_at.is_(None),
            ).values(revoked_at=now if now is not None else time.time()))
        return bool(result.rowcount)


__all__ = ["AuthSessionStore", "auth_invites", "auth_metadata", "auth_sessions"]
