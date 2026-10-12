"""PostgreSQL persistence for resumable voice tutor sessions."""

from __future__ import annotations

import hmac
import time
from typing import Any

from sqlalchemy import select, update

from persistence.base import DatabaseStore
from persistence.database import decode_json
from persistence.voice_schema import voice_sessions

VOICE_SESSION_RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_VOICE_TURNS = 40


class VoiceSessionNotFound(LookupError):
    """The session does not exist, expired, or the recovery token is invalid."""


class VoiceSessionConflict(ValueError):
    """A session operation lost its optimistic concurrency check."""


class VoiceTranscriptNotConfirmed(VoiceSessionConflict):
    """A voice reply was requested before the current transcript was confirmed."""


class VoiceStore(DatabaseStore):
    """Store transcript drafts, confirmed conversation turns, and generation CAS."""

    def create(
        self, session: dict[str, Any], *, now: float | None = None
    ) -> dict[str, Any]:
        current = now if now is not None else time.time()
        with self.atomic() as connection:
            connection.execute(
                voice_sessions.insert().values(
                    session_id=session["sessionId"],
                    capability_hash=session["capabilityHash"],
                    learner_id=session["learnerId"],
                    chapter_id=session["chapterId"],
                    source_kind=session.get("sourceKind", "chapter"),
                    courseware_id=session.get("coursewareId"),
                    publication_id=session["publicationId"],
                    lesson_id=session["lessonId"],
                    block_id=session["blockId"],
                    generation=0,
                    active_turn_id=None,
                    draft_json=None,
                    confirmed_json=None,
                    turns_json=[],
                    context_label=session.get("contextLabel", ""),
                    created_at=current,
                    updated_at=current,
                    expires_at=current + VOICE_SESSION_RETENTION_SECONDS,
                )
            )
        return self.get(
            session["sessionId"],
            session["capabilityHash"],
            learner_id=session["learnerId"],
            now=current,
        )

    def get(
        self,
        session_id: str,
        capability_hash: str,
        *,
        learner_id: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        current = now if now is not None else time.time()
        with self.read_connection() as connection:
            row = (
                connection.execute(
                    select(voice_sessions).where(
                        voice_sessions.c.session_id == session_id,
                    )
                )
                .mappings()
                .first()
            )
        if (
            row is None
            or row["learner_id"] != learner_id
            or row["expires_at"] <= current
            or not hmac.compare_digest(
                str(row["capability_hash"]),
                capability_hash,
            )
        ):
            raise VoiceSessionNotFound("语音会话不存在或已过期")
        return self._public_row(row)

    def save_draft(
        self,
        session_id: str,
        capability_hash: str,
        *,
        learner_id: str,
        text: str,
        source: str,
        confidence: dict[str, Any] | None = None,
        ambiguity_flags: list[str] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        current = now if now is not None else time.time()
        with self.atomic() as connection:
            row = self._authorized_row(
                connection, session_id, capability_hash, learner_id, current
            )
            previous = self._json(row["draft_json"])
            revision = int(previous.get("revision", 0)) + 1 if previous else 1
            if (
                previous
                and previous.get("text") == text
                and previous.get("source") == source
                and confidence is None
            ):
                # Saving the same machine transcript immediately before confirm
                # must not erase its confidence and ambiguity diagnostics.
                confidence = previous.get("confidence")
                ambiguity_flags = previous.get("ambiguityFlags", ambiguity_flags)
            draft = {
                "revision": revision,
                "text": text,
                "source": source,
                "confidence": confidence,
                "ambiguityFlags": ambiguity_flags or [],
                "needsConfirmation": True,
                "updatedAt": current,
            }
            # Editing the transcript makes an in-flight answer obsolete. Bump
            # its generation so a late model result cannot commit or strand the slot.
            active_turn = row["active_turn_id"] is not None
            connection.execute(
                update(voice_sessions)
                .where(
                    voice_sessions.c.session_id == session_id,
                )
                .values(
                    draft_json=draft,
                    confirmed_json=None,
                    generation=int(row["generation"]) + int(active_turn),
                    active_turn_id=None if active_turn else row["active_turn_id"],
                    updated_at=current,
                )
            )
        return self.get(session_id, capability_hash, learner_id=learner_id, now=current)

    def confirm_transcript(
        self,
        session_id: str,
        capability_hash: str,
        *,
        learner_id: str,
        revision: int,
        text: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        current = now if now is not None else time.time()
        with self.atomic() as connection:
            row = self._authorized_row(
                connection, session_id, capability_hash, learner_id, current
            )
            draft = self._json(row["draft_json"])
            if not draft or int(draft.get("revision", 0)) != revision:
                raise VoiceSessionConflict("转写已更新，请先查看最新文字")
            draft["text"] = text
            draft["needsConfirmation"] = False
            draft["updatedAt"] = current
            confirmed = {
                "revision": revision,
                "text": text,
                "confirmedAt": current,
            }
            connection.execute(
                update(voice_sessions)
                .where(
                    voice_sessions.c.session_id == session_id,
                )
                .values(draft_json=draft, confirmed_json=confirmed, updated_at=current)
            )
        return self.get(session_id, capability_hash, learner_id=learner_id, now=current)

    def claim_response(
        self,
        session_id: str,
        capability_hash: str,
        *,
        learner_id: str,
        expected_generation: int,
        transcript_revision: int,
        turn_id: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Claim one turn and increment its generation in a locked transaction."""
        current = now if now is not None else time.time()
        with self.atomic() as connection:
            row = self._authorized_row(
                connection, session_id, capability_hash, learner_id, current
            )
            turns = self._json(row["turns_json"]) or []
            existing = next(
                (item for item in turns if item.get("turnId") == turn_id), None
            )
            if existing:
                return {"existingTurn": existing, "session": self._public_row(row)}
            confirmed = self._json(row["confirmed_json"])
            if (
                not confirmed
                or int(confirmed.get("revision", 0)) != transcript_revision
            ):
                raise VoiceTranscriptNotConfirmed("请先确认当前转写文字")
            if int(row["generation"]) != expected_generation:
                raise VoiceSessionConflict("语音回复已被更新，请重试")
            if row["active_turn_id"]:
                raise VoiceSessionConflict("当前步骤已有回复正在生成")
            next_generation = int(row["generation"]) + 1
            connection.execute(
                update(voice_sessions)
                .where(
                    voice_sessions.c.session_id == session_id,
                    voice_sessions.c.generation == expected_generation,
                )
                .values(
                    generation=next_generation,
                    active_turn_id=turn_id,
                    updated_at=current,
                )
            )
        return {
            "generation": next_generation,
            "confirmedTranscript": confirmed,
            "turns": turns,
        }

    def complete_response(
        self,
        session_id: str,
        capability_hash: str,
        *,
        learner_id: str,
        generation: int,
        turn_id: str,
        response: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """Append a response only if its generation still owns the session."""
        current = now if now is not None else time.time()
        with self.atomic() as connection:
            row = self._authorized_row(
                connection, session_id, capability_hash, learner_id, current
            )
            if int(row["generation"]) != generation or row["active_turn_id"] != turn_id:
                return None
            confirmed = self._json(row["confirmed_json"])
            if not confirmed:
                return None
            turns = self._json(row["turns_json"]) or []
            turn = {
                "turnId": turn_id,
                "transcriptRevision": confirmed["revision"],
                "transcript": confirmed["text"],
                "response": response,
                "createdAt": current,
            }
            turns.append(turn)
            turns = turns[-MAX_VOICE_TURNS:]
            connection.execute(
                update(voice_sessions)
                .where(
                    voice_sessions.c.session_id == session_id,
                    voice_sessions.c.generation == generation,
                    voice_sessions.c.active_turn_id == turn_id,
                )
                .values(turns_json=turns, active_turn_id=None, updated_at=current)
            )
        return turn

    def release_response(
        self,
        session_id: str,
        capability_hash: str,
        *,
        learner_id: str,
        generation: int,
        turn_id: str,
        now: float | None = None,
    ) -> bool:
        current = now if now is not None else time.time()
        with self.atomic() as connection:
            row = self._authorized_row(
                connection, session_id, capability_hash, learner_id, current
            )
            if int(row["generation"]) != generation or row["active_turn_id"] != turn_id:
                return False
            connection.execute(
                update(voice_sessions)
                .where(
                    voice_sessions.c.session_id == session_id,
                    voice_sessions.c.generation == generation,
                    voice_sessions.c.active_turn_id == turn_id,
                )
                .values(active_turn_id=None, updated_at=current)
            )
        return True

    def cancel_response(
        self,
        session_id: str,
        capability_hash: str,
        *,
        learner_id: str,
        expected_generation: int,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Invalidate outstanding work; a stale response can no longer commit."""
        current = now if now is not None else time.time()
        with self.atomic() as connection:
            row = self._authorized_row(
                connection, session_id, capability_hash, learner_id, current
            )
            generation = int(row["generation"])
            if generation != expected_generation:
                return {"cancelled": False, "session": self._public_row(row)}
            if row["active_turn_id"] is None:
                return {"cancelled": False, "session": self._public_row(row)}
            connection.execute(
                update(voice_sessions)
                .where(
                    voice_sessions.c.session_id == session_id,
                    voice_sessions.c.generation == expected_generation,
                )
                .values(
                    generation=expected_generation + 1,
                    active_turn_id=None,
                    updated_at=current,
                )
            )
        return {
            "cancelled": True,
            "session": self.get(
                session_id, capability_hash, learner_id=learner_id, now=current
            ),
        }

    @staticmethod
    def _authorized_row(
        connection, session_id: str, capability_hash: str, learner_id: str, now: float
    ):
        row = (
            connection.execute(
                select(voice_sessions)
                .where(
                    voice_sessions.c.session_id == session_id,
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if (
            row is None
            or row["learner_id"] != learner_id
            or row["expires_at"] <= now
            or not hmac.compare_digest(
                str(row["capability_hash"]),
                capability_hash,
            )
        ):
            raise VoiceSessionNotFound("语音会话不存在或已过期")
        return row

    @classmethod
    def _public_row(cls, row: Any) -> dict[str, Any]:
        return {
            "sessionId": row["session_id"],
            "learnerId": row["learner_id"],
            "chapterId": row["chapter_id"],
            "sourceKind": row["source_kind"],
            "coursewareId": row["courseware_id"],
            "publicationId": row["publication_id"],
            "lessonId": row["lesson_id"],
            "blockId": row["block_id"],
            "contextLabel": row["context_label"],
            "generation": int(row["generation"]),
            "active": row["active_turn_id"] is not None,
            "draftTranscript": cls._json(row["draft_json"]),
            "confirmedTranscript": cls._json(row["confirmed_json"]),
            "turns": cls._json(row["turns_json"]) or [],
            "updatedAt": row["updated_at"],
            "expiresAt": row["expires_at"],
        }

    @staticmethod
    def _json(value: Any) -> Any:
        return decode_json(value) if value is not None else None
