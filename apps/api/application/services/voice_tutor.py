"""Orchestrate confirmed voice turns against an immutable published lesson step."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from typing import Any

from application.errors import AppError
from domain.contracts.voice import VoiceSessionCreate
from infrastructure.runtime.voice_transcription import (
    VoiceTranscriptionRuntime,
)
from persistence.voice_store import (
    VoiceStore,
)

VOICE_REPLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"reply": {"type": "string"}},
    "required": ["reply"],
    "additionalProperties": False,
}
MAX_TURN_TEXT = 5000
MAX_HISTORY_TURNS = 12


class VoiceTutorService:
    """Keep voice conversation state separate from grading and learning evidence."""

    def __init__(
        self,
        *,
        store: VoiceStore,
        chapter_service: Any,
        tutor_runtime: Any,
        stt_runtime: VoiceTranscriptionRuntime,
    ) -> None:
        self.store = store
        self.chapter_service = chapter_service
        self.tutor_runtime = tutor_runtime
        self.stt_runtime = stt_runtime

    def create_session(
        self, request: VoiceSessionCreate | dict[str, Any]
    ) -> dict[str, Any]:
        values = (
            request.model_dump() if isinstance(request, VoiceSessionCreate) else request
        )
        context = self._published_step(
            chapter_id=values["chapterId"],
            publication_id=values["publicationId"],
            lesson_id=values["lessonId"],
            block_id=values["blockId"],
        )
        recovery_token = secrets.token_urlsafe(32)
        session_id = str(uuid.uuid4())
        session = self.store.create(
            {
                "sessionId": session_id,
                "capabilityHash": self._token_hash(recovery_token),
                "learnerId": values["learnerId"],
                "chapterId": values["chapterId"],
                "publicationId": values["publicationId"],
                "lessonId": values["lessonId"],
                "blockId": values["blockId"],
                "contextLabel": context["contextLabel"],
            }
        )
        return self._response_session(session, recovery_token=recovery_token)

    def get_session(
        self, session_id: str, recovery_token: str, *, learner_id: str
    ) -> dict[str, Any]:
        return self._response_session(
            self.store.get(
                session_id, self._token_hash(recovery_token), learner_id=learner_id
            ),
        )

    def save_manual_transcript(
        self,
        session_id: str,
        recovery_token: str,
        *,
        learner_id: str,
        text: str,
        source: str = "manual",
        browser_confidence: float | None = None,
    ) -> dict[str, Any]:
        cleaned = self._validated_transcript(text)
        confidence = (
            {"kind": "browser_recognition", "score": browser_confidence}
            if source == "browser_speech_recognition" and browser_confidence is not None
            else None
        )
        ambiguity_flags = self._ambiguity_flags(cleaned)
        if source == "browser_speech_recognition" and browser_confidence is None:
            ambiguity_flags.append("confidence_unavailable")
        elif (
            source == "browser_speech_recognition"
            and browser_confidence is not None
            and browser_confidence < 0.4
        ):
            ambiguity_flags.append("browser_low_confidence")
        session = self.store.save_draft(
            session_id,
            self._token_hash(recovery_token),
            learner_id=learner_id,
            text=cleaned,
            source=source,
            confidence=confidence,
            ambiguity_flags=ambiguity_flags,
        )
        return self._response_session(session)

    def transcribe(
        self,
        session_id: str,
        recovery_token: str,
        *,
        learner_id: str,
        audio: bytes,
        filename: str,
        content_type: str,
    ) -> dict[str, Any]:
        # Check the bearer capability before expensive audio processing.
        self.store.get(
            session_id, self._token_hash(recovery_token), learner_id=learner_id
        )
        transcript = self.stt_runtime.transcribe(
            audio=audio,
            filename=filename,
            content_type=content_type,
        )
        text = self._validated_transcript(transcript["text"])
        session = self.store.save_draft(
            session_id,
            self._token_hash(recovery_token),
            learner_id=learner_id,
            text=text,
            source="speech_to_text",
            confidence=transcript.get("confidence"),
            ambiguity_flags=transcript.get("ambiguityFlags", []),
        )
        return {
            "session": self._response_session(session),
            "transcript": {
                "text": text,
                "confidence": transcript.get("confidence"),
                "ambiguityFlags": transcript.get("ambiguityFlags", []),
                "needsConfirmation": True,
            },
        }

    def confirm_transcript(
        self,
        session_id: str,
        recovery_token: str,
        *,
        learner_id: str,
        revision: int,
        text: str,
    ) -> dict[str, Any]:
        cleaned = self._validated_transcript(text)
        session = self.store.confirm_transcript(
            session_id,
            self._token_hash(recovery_token),
            learner_id=learner_id,
            revision=revision,
            text=cleaned,
        )
        return self._response_session(session)

    def respond(
        self,
        session_id: str,
        recovery_token: str,
        *,
        learner_id: str,
        turn_id: str,
        transcript_revision: int,
        expected_generation: int,
    ) -> dict[str, Any]:
        token_hash = self._token_hash(recovery_token)
        claim = self.store.claim_response(
            session_id,
            token_hash,
            learner_id=learner_id,
            expected_generation=expected_generation,
            transcript_revision=transcript_revision,
            turn_id=turn_id,
        )
        if "existingTurn" in claim:
            session = claim["session"]
            return {
                "status": "duplicate",
                "sessionId": session_id,
                "generation": session["generation"],
                "turn": claim["existingTurn"],
            }

        generation = claim["generation"]
        try:
            session = self.store.get(session_id, token_hash, learner_id=learner_id)
            context = self._published_step(
                chapter_id=session["chapterId"],
                publication_id=session["publicationId"],
                lesson_id=session["lessonId"],
                block_id=session["blockId"],
            )
            prompt = self._prompt(
                context=context,
                transcript=claim["confirmedTranscript"]["text"],
                turns=claim["turns"],
            )
            if self.tutor_runtime.selection.provider == "mock":
                raise AppError(
                    "陪练模型未配置，当前不能生成语音追问",
                    status_code=503,
                    error_code="VOICE_TUTOR_UNAVAILABLE",
                    retryable=True,
                )
            generated, _run = self.tutor_runtime.generate_json(
                prompt,
                VOICE_REPLY_SCHEMA,
                max_tokens=300,
                task="voice_tutor",
            )
            reply = self._validated_reply(generated.get("reply"))
            turn = self.store.complete_response(
                session_id,
                token_hash,
                learner_id=learner_id,
                generation=generation,
                turn_id=turn_id,
                response=reply,
            )
            if turn is None:
                latest = self.store.get(session_id, token_hash, learner_id=learner_id)
                return {
                    "status": "superseded",
                    "sessionId": session_id,
                    "generation": latest["generation"],
                    "turn": None,
                }
            return {
                "status": "completed",
                "sessionId": session_id,
                "generation": generation,
                "turn": turn,
            }
        except Exception:
            self.store.release_response(
                session_id,
                token_hash,
                learner_id=learner_id,
                generation=generation,
                turn_id=turn_id,
            )
            raise

    def cancel_response(
        self,
        session_id: str,
        recovery_token: str,
        *,
        learner_id: str,
        expected_generation: int,
    ) -> dict[str, Any]:
        return self.store.cancel_response(
            session_id,
            self._token_hash(recovery_token),
            learner_id=learner_id,
            expected_generation=expected_generation,
        )

    @staticmethod
    def transcription_status(stt_runtime: VoiceTranscriptionRuntime) -> dict[str, Any]:
        return {
            "available": stt_runtime.available,
            "provider": stt_runtime.provider,
            "detail": None if stt_runtime.available else stt_runtime.unavailable_detail,
        }

    def _published_step(
        self,
        *,
        chapter_id: str,
        publication_id: str,
        lesson_id: str,
        block_id: str,
    ) -> dict[str, Any]:
        published = self.chapter_service.get_published(chapter_id, publication_id)
        if published.get("publicationId") != publication_id:
            raise LookupError("已发布课程版本不存在")
        lesson = next(
            (
                item
                for item in published.get("lessons", [])
                if item.get("lessonId") == lesson_id
            ),
            None,
        )
        if not lesson:
            raise LookupError("课程不属于该已发布版本")
        block = next(
            (item for item in lesson.get("blocks", []) if item.get("id") == block_id),
            None,
        )
        if not block or block.get("type") == "quiz":
            raise LookupError("学习步骤不属于该已发布课程")
        payload = block.get("payload") if isinstance(block.get("payload"), dict) else {}
        block_type = block.get("type")
        if block_type == "markdown":
            step_content = payload.get("markdown", "")
        elif block_type == "formula":
            step_content = payload.get("latex", "")
        elif block_type == "diagram":
            step_content = payload.get("speechText") or payload.get("text", "")
        elif block_type == "animation":
            step_content = payload.get("caption", "")
        elif block_type == "annotation":
            step_content = payload.get("text", "")
        elif block_type == "hint":
            step_content = " ".join(
                str(payload.get(key, "")) for key in ("hint", "question")
            )
        else:
            step_content = ""
        safe_context = {
            "subject": published.get("subject", ""),
            "chapterTitle": published.get("title", ""),
            "lessonTitle": lesson.get("title", ""),
            "knowledgePoints": lesson.get("knowledgePoints", [])[:16],
            "stepTitle": block.get("title", ""),
            "stepContent": str(step_content)[:6000],
        }
        return {
            **safe_context,
            "contextLabel": " · ".join(
                filter(
                    None,
                    [
                        str(safe_context["chapterTitle"]),
                        str(safe_context["lessonTitle"]),
                        str(safe_context["stepTitle"]),
                    ],
                )
            ),
        }

    @classmethod
    def _prompt(
        cls, *, context: dict[str, Any], transcript: str, turns: list[dict[str, Any]]
    ) -> str:
        history = [
            {"student": item.get("transcript", ""), "tutor": item.get("response", "")}
            for item in turns[-MAX_HISTORY_TURNS:]
        ]
        return (
            "你是这节已发布课程中的语音导师。请围绕当前步骤，对学生刚才明确确认的问题做简短、温和的回应，"
            "然后提出一个能帮助学生继续思考的口头追问。只使用提供的步骤内容与知识点；如果上下文不足就直接请学生指出哪一处不清楚。"
            "不要判定对错、不要给出标准答案、不要评价掌握程度，也不要生成作答记录。回复用简体中文，最多两句话。\n"
            f"当前教学上下文：{json.dumps(context, ensure_ascii=False)}\n"
            f"历史对话：{json.dumps(history, ensure_ascii=False)}\n"
            f"学生已确认的本轮转写：{transcript[:MAX_TURN_TEXT]}"
        )

    @staticmethod
    def _validated_transcript(value: str) -> str:
        cleaned = re.sub(r"\s+", " ", value).strip()
        if not cleaned:
            raise ValueError("转写文字不能为空")
        if len(cleaned) > MAX_TURN_TEXT:
            raise ValueError("转写文字不能超过 5000 字")
        return cleaned

    @staticmethod
    def _validated_reply(value: Any) -> str:
        if not isinstance(value, str):
            raise AppError(
                "陪练模型没有生成可用回复，请重试",
                status_code=503,
                error_code="VOICE_TUTOR_INVALID_RESPONSE",
                retryable=True,
            )
        reply = re.sub(r"\s+", " ", value).strip()
        if not reply or len(reply) > 1200:
            raise AppError(
                "陪练模型没有生成可用回复，请重试",
                status_code=503,
                error_code="VOICE_TUTOR_INVALID_RESPONSE",
                retryable=True,
            )
        return reply

    @staticmethod
    def _ambiguity_flags(text: str) -> list[str]:
        flags: list[str] = []
        if len(text) <= 3:
            flags.append("short_transcription")
        if text.count("?") + text.count("？") > 1:
            flags.append("multiple_uncertainty_markers")
        return flags

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _response_session(
        self, session: dict[str, Any], *, recovery_token: str | None = None
    ) -> dict[str, Any]:
        return {
            **session,
            "transcription": self.transcription_status(self.stt_runtime),
            **({"recoveryToken": recovery_token} if recovery_token else {}),
        }
