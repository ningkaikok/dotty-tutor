"""互动课程集合的送审、发布和版本化 HTTP 边界。"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException

from domain.contracts.audit import PublicationRevisionResponse
from domain.contracts.lesson import PublicationCreate, PublicationStatusUpdate
from observability import log_event
from publication_quality import PublicationQualityError


def _public_lesson(lesson: dict[str, Any]) -> dict[str, Any]:
    """课程进入学生端前移除工作台诊断信息和真实模型标识。"""
    payload = dict(lesson.get("questionPayload") or {})
    question = dict(payload.get("question") or {})
    for key in ("publicationStatus", "sourceArtifactUrl", "promptArtifactUrl"):
        question.pop(key, None)
    payload["question"] = question
    payload.pop("review", None)
    payload.pop("quality", None)
    payload["architecture"] = {}
    payload["modelRun"] = {
        "requestedProvider": "published",
        "provider": "published",
        "model": "published",
        "fallback": False,
    }
    return {
        "lessonId": lesson["lessonId"],
        "title": lesson["title"],
        "version": lesson["version"],
        "status": lesson["status"],
        "knowledgePoints": lesson["knowledgePoints"],
        "blocks": lesson["blocks"],
        "questionPayload": payload,
        "guideCards": [],
    }


def build_publication_router(*, store: Any, revision_service: Any | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/publications", tags=["publications"])

    @router.post("")
    def create_publication(request: PublicationCreate) -> dict[str, Any]:
        try:
            publication = store.create_publication(
                publication_id=uuid.uuid4().hex,
                title=request.title,
                source_upload_id=request.sourceUploadId,
                lesson_ids=request.lessonIds,
                status="draft",
                created_at=time.time(),
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        log_event(
            "publication.created",
            publication_id=publication["publicationId"],
            lesson_count=len(request.lessonIds),
            status="draft",
        )
        return publication

    @router.get("")
    def list_publications(status: str = "published") -> dict[str, Any]:
        if status not in {"draft", "in_review", "review", "published", "archived"}:
            raise HTTPException(status_code=400, detail="无效的发布状态")
        return {"items": store.list_publications(status=status)}

    @router.get("/source/{source_upload_id}")
    def get_latest_publication_for_source(source_upload_id: str) -> dict[str, Any]:
        """页面刷新后恢复该教材最新的内容生产状态。

        学生读取接口只返回脱敏课程；工作台需要质量诊断，因此使用独立的来源范围接口，
        而不是放宽 ``/learn`` 数据边界。
        """
        summary = next((
            item
            for item in store.list_publications()
            if item.get("sourceUploadId") == source_upload_id
            and item.get("status") != "archived"
        ), None)
        if not summary:
            return {"publication": None, "questionPayloads": []}
        publication = store.load_publication(summary["publicationId"])
        if not publication:
            return {"publication": None, "questionPayloads": []}
        lessons = publication.pop("lessons", [])
        publication["lessonCount"] = len(publication.get("lessonIds") or [])
        return {
            "publication": publication,
            "questionPayloads": [lesson["questionPayload"] for lesson in lessons],
        }

    @router.post("/{publication_id}/revisions", response_model=PublicationRevisionResponse)
    def create_publication_revision(publication_id: str) -> dict[str, Any]:
        if revision_service is None:
            raise HTTPException(status_code=501, detail="试卷版本服务未配置")
        try:
            result = revision_service.create(publication_id)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        revised = result["publication"]
        log_event(
            "publication.revision.created",
            publication_id=revised["publicationId"],
            revision_of=publication_id,
            version=revised["version"],
            lesson_count=len(revised["lessonIds"]),
            run_id=(result.get("run") or {}).get("runId"),
        )
        return result

    @router.get("/{publication_id}")
    def get_publication(publication_id: str) -> dict[str, Any]:
        publication = store.load_publication(publication_id)
        if not publication:
            raise HTTPException(status_code=404, detail="互动试卷不存在")
        if publication["status"] != "published":
            raise HTTPException(status_code=404, detail="互动试卷尚未发布")
        publication["lessons"] = [_public_lesson(lesson) for lesson in publication["lessons"]]
        return publication

    @router.patch("/{publication_id}/status")
    def update_publication_status(
        publication_id: str,
        request: PublicationStatusUpdate,
    ) -> dict[str, Any]:
        try:
            publication = store.update_publication_status(publication_id, request.status)
        except PublicationQualityError as error:
            log_event(
                "publication.quality.blocked",
                level=40,
                publication_id=publication_id,
                blocked_count=len(error.blockers),
                blocked_lesson_ids=[item["lessonId"] for item in error.blockers],
                validation_errors=list(dict.fromkeys(
                    str(message)[:180]
                    for item in error.blockers
                    for message in item.get("errors", [])
                ))[:8],
            )
            raise HTTPException(status_code=409, detail=error.detail()) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if not publication:
            raise HTTPException(status_code=404, detail="互动试卷不存在")
        log_event(
            "publication.status.updated",
            publication_id=publication_id,
            status=request.status,
            quarantined_count=(publication.get("qualityRecovery") or {}).get("quarantinedCount"),
        )
        return publication

    return router
