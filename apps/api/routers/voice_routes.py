"""Student-facing routes for confirmed voice follow-ups on published steps."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile

from application.errors import AppError
from application.services.voice_tutor import VoiceTutorService
from auth_context import learner_for_request
from domain.contracts.voice import (
    VoiceCancelRequest,
    VoiceResponseRequest,
    VoiceSessionCreate,
    VoiceTranscriptConfirm,
    VoiceTranscriptDraft,
)
from infrastructure.runtime.voice_transcription import (
    MAX_AUDIO_BYTES,
    TranscriptionUnavailable,
)
from persistence.voice_store import (
    VoiceSessionConflict,
    VoiceSessionNotFound,
)


def build_voice_router(*, service: VoiceTutorService) -> APIRouter:
    """Build the isolated voice API; app_factory owns student authorization."""
    router = APIRouter(prefix="/api/voice", tags=["voice-tutor"])

    def capability(token: str | None) -> str:
        if not token or len(token) > 256:
            raise HTTPException(status_code=401, detail="语音会话凭据无效")
        return token

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except VoiceSessionNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except VoiceSessionConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except TranscriptionUnavailable as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except AppError:
            raise

    @router.get("/status")
    def get_status() -> dict[str, Any]:
        return service.transcription_status(service.stt_runtime)

    @router.post("/sessions", status_code=201)
    def create_session(
        http_request: Request, request: VoiceSessionCreate
    ) -> dict[str, Any]:
        learner_id = learner_for_request(http_request, request.learnerId)
        return call(
            service.create_session, request.model_copy(update={"learnerId": learner_id})
        )

    @router.get("/sessions/{session_id}")
    def get_session(
        request: Request,
        session_id: str,
        token: str | None = Header(default=None, alias="X-Voice-Session-Token"),
    ) -> dict[str, Any]:
        learner_id = learner_for_request(request, request.headers.get("X-Learner-ID"))
        return call(
            service.get_session, session_id, capability(token), learner_id=learner_id
        )

    @router.put("/sessions/{session_id}/transcript")
    def save_transcript(
        http_request: Request,
        session_id: str,
        request: VoiceTranscriptDraft,
        token: str | None = Header(default=None, alias="X-Voice-Session-Token"),
    ) -> dict[str, Any]:
        learner_id = learner_for_request(
            http_request, http_request.headers.get("X-Learner-ID")
        )
        return call(
            service.save_manual_transcript,
            session_id,
            capability(token),
            learner_id=learner_id,
            text=request.text,
            source=request.source,
            browser_confidence=request.browserConfidence,
        )

    @router.post("/sessions/{session_id}/transcribe")
    def transcribe(
        http_request: Request,
        session_id: str,
        audio: UploadFile = File(...),
        token: str | None = Header(default=None, alias="X-Voice-Session-Token"),
    ) -> dict[str, Any]:
        content = audio.file.read(MAX_AUDIO_BYTES + 1)
        if len(content) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="录音不能超过 16 MB")
        if not audio.filename or not audio.content_type:
            raise HTTPException(status_code=415, detail="录音缺少文件名或媒体格式")
        learner_id = learner_for_request(
            http_request, http_request.headers.get("X-Learner-ID")
        )
        return call(
            service.transcribe,
            session_id,
            capability(token),
            learner_id=learner_id,
            audio=content,
            filename=audio.filename,
            content_type=audio.content_type,
        )

    @router.post("/sessions/{session_id}/confirm-transcript")
    def confirm_transcript(
        http_request: Request,
        session_id: str,
        request: VoiceTranscriptConfirm,
        token: str | None = Header(default=None, alias="X-Voice-Session-Token"),
    ) -> dict[str, Any]:
        learner_id = learner_for_request(
            http_request, http_request.headers.get("X-Learner-ID")
        )
        return call(
            service.confirm_transcript,
            session_id,
            capability(token),
            learner_id=learner_id,
            revision=request.revision,
            text=request.text,
        )

    @router.post("/sessions/{session_id}/responses")
    def respond(
        http_request: Request,
        session_id: str,
        request: VoiceResponseRequest,
        token: str | None = Header(default=None, alias="X-Voice-Session-Token"),
    ) -> dict[str, Any]:
        learner_id = learner_for_request(
            http_request, http_request.headers.get("X-Learner-ID")
        )
        return call(
            service.respond,
            session_id,
            capability(token),
            learner_id=learner_id,
            turn_id=request.turnId,
            transcript_revision=request.transcriptRevision,
            expected_generation=request.expectedGeneration,
        )

    @router.post("/sessions/{session_id}/cancel")
    def cancel(
        http_request: Request,
        session_id: str,
        request: VoiceCancelRequest,
        token: str | None = Header(default=None, alias="X-Voice-Session-Token"),
    ) -> dict[str, Any]:
        learner_id = learner_for_request(
            http_request, http_request.headers.get("X-Learner-ID")
        )
        return call(
            service.cancel_response,
            session_id,
            capability(token),
            learner_id=learner_id,
            expected_generation=request.expectedGeneration,
        )

    return router
