"""HTTP adapter for versioned TutorInput evidence."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from domain.constants import DEMO_LEARNER_ID
from domain.contracts.tutoring import TutorObservationDecision
from observability import log_event

MAX_TUTOR_PHOTO_BYTES = 10 * 1024 * 1024
ALLOWED_PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


def _interaction_result(value: str) -> dict[str, Any]:
    if not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=422, detail="interactionResult 必须是 JSON 对象") from error
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="interactionResult 必须是 JSON 对象")
    return parsed


def _json_object(value: str, *, field_name: str) -> dict[str, Any] | None:
    if not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=422, detail=f"{field_name} 必须是 JSON 对象") from error
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail=f"{field_name} 必须是 JSON 对象")
    return parsed


def _json_list(value: str, *, field_name: str) -> list[dict[str, Any]]:
    if not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=422, detail=f"{field_name} 必须是 JSON 数组") from error
    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise HTTPException(status_code=422, detail=f"{field_name} 必须是 JSON 对象数组")
    return parsed[:32]


def build_tutor_input_router(*, tutoring_store: Any, input_service: Any, mistake_store: Any) -> APIRouter:
    router = APIRouter(tags=["tutor-inputs"])

    @router.post("/api/tutor/threads/{thread_id}/inputs")
    async def create_input(
        thread_id: str,
        content: str = Form(default="", max_length=2_000),
        mode: str = Form(default="text"),
        interactionResult: str = Form(default="{}"),
        formulaRecognitions: str = Form(default="[]"),
        canvasState: str = Form(default=""),
        learnerId: str = Form(default=DEMO_LEARNER_ID, min_length=1, max_length=128),
        photo: UploadFile | None = File(default=None),
        questionImage: UploadFile | None = File(default=None),
    ) -> dict[str, Any]:
        thread = tutoring_store.get(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="辅导线程不存在")
        if thread["learnerId"] != learnerId:
            raise HTTPException(status_code=403, detail="不能访问其他学生的辅导线程")
        if mode not in {"text", "structured"}:
            raise HTTPException(status_code=422, detail="输入模式无效")
        mistake = mistake_store.get(thread["mistakeId"])
        if not mistake or mistake["learnerId"] != learnerId:
            raise HTTPException(status_code=403, detail="不能访问其他学生的错题")
        interaction = _interaction_result(interactionResult)
        formula_recognitions = _json_list(formulaRecognitions, field_name="formulaRecognitions")
        canvas_state = _json_object(canvasState, field_name="canvasState")
        if photo is not None and questionImage is not None:
            raise HTTPException(status_code=422, detail="一次 TutorInput 只能附加一种图片")
        upload = questionImage or photo
        artifact_kind = "question-image" if questionImage is not None else "solution-photo"
        photo_path: Path | None = None
        filename = "solution.jpg"
        media_type = "image/jpeg"
        if upload is not None:
            filename = Path(upload.filename or filename).name
            media_type = (upload.content_type or "").lower()
            if media_type not in ALLOWED_PHOTO_TYPES:
                raise HTTPException(status_code=415, detail="解题步骤照片格式不受支持")
            data = await upload.read(MAX_TUTOR_PHOTO_BYTES + 1)
            await upload.close()
            if not data:
                raise HTTPException(status_code=400, detail="解题步骤照片不能为空")
            if len(data) > MAX_TUTOR_PHOTO_BYTES:
                raise HTTPException(status_code=413, detail="解题步骤照片不能超过 10 MB")
            directory = mistake_store.item_directory(thread["mistakeId"]) / "tutor-inputs"
            directory.mkdir(parents=True, exist_ok=True)
            saved_photo_path = directory / f"{uuid.uuid4().hex}{Path(filename).suffix.lower() or '.jpg'}"
            saved_photo_path.write_bytes(data)
            photo_path = saved_photo_path
        try:
            return input_service.create(
                thread=thread, mistake_id=thread["mistakeId"], learner_id=learnerId, mode=mode, content=content.strip(),
                interaction_result=interaction, photo_path=photo_path,
                artifact_kind=artifact_kind,
                photo_filename=filename, photo_media_type=media_type,
                formula_recognitions=formula_recognitions, canvas_state=canvas_state,
            )
        except ValueError as error:
            if photo_path is not None and photo_path.is_file():
                photo_path.unlink()
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.patch("/api/tutor/inputs/{input_id}/observations")
    def decide_observation(
        input_id: str,
        decision: TutorObservationDecision,
        learnerId: str = Query(default=DEMO_LEARNER_ID, min_length=1, max_length=128),
    ) -> dict[str, Any]:
        item = tutoring_store.get_input(input_id)
        if not item:
            raise HTTPException(status_code=404, detail="TutorInput 不存在")
        if item["learnerId"] != learnerId:
            raise HTTPException(status_code=403, detail="不能访问其他学生的输入证据")
        saved = input_service.decide(input_id, learner_id=learnerId, decision=decision.model_dump())
        if not saved:
            raise HTTPException(status_code=404, detail="TutorInput 不存在")
        log_event(
            "tutor.observation.reviewed",
            input_id=input_id,
            decision=decision.decision,
            status=saved.get("status"),
        )
        return saved

    @router.get("/api/tutor/inputs/{input_id}/artifacts/{artifact_id}")
    def get_artifact(
        input_id: str,
        artifact_id: str,
        learnerId: str = Query(default=DEMO_LEARNER_ID, min_length=1, max_length=128),
    ) -> FileResponse:
        input_item = tutoring_store.get_input(input_id)
        if not input_item:
            raise HTTPException(status_code=404, detail="TutorInput 不存在")
        artifact = tutoring_store.get_artifact(artifact_id)
        if not artifact or artifact["inputId"] != input_id:
            raise HTTPException(status_code=404, detail="证据资源不存在")
        if artifact["learnerId"] != learnerId:
            raise HTTPException(status_code=403, detail="不能访问其他学生的证据资源")
        mistake_root = mistake_store.item_directory(input_item["mistakeId"]).resolve()
        path = Path(str(artifact["storagePath"])).resolve()
        if mistake_root not in path.parents:
            raise HTTPException(status_code=404, detail="证据文件不存在")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="证据文件不存在")
        return FileResponse(path, media_type=artifact["mediaType"], filename=artifact["filename"])

    return router


__all__ = ["build_tutor_input_router"]
