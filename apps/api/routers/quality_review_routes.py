"""Teacher-facing quality review package and single-review endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError

from application.services.quality_review import quality_review_actor
from domain.contracts.quality_review import (
    QualityReviewDecisionRequest,
    QualityReviewInbox,
    QualityReviewPackageImport,
    QualityReviewRevisionRequest,
)


def build_quality_review_router(service: Any) -> APIRouter:
    router = APIRouter(prefix="/api/quality-reviews", tags=["quality-reviews"])

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except PermissionError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except IntegrityError as error:
            raise HTTPException(status_code=409, detail="当前用户已对该版本提交过决定") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post("/packages", status_code=201)
    def import_package(request: Request, payload: QualityReviewPackageImport) -> dict[str, Any]:
        actor = call(quality_review_actor, request)
        data = payload.model_dump()
        return call(service.import_package, data, actor)

    @router.get("/packages")
    def list_packages() -> dict[str, Any]:
        return service.list_packages()

    @router.get("/inbox", response_model=QualityReviewInbox)
    def list_inbox(
        chapter_id: str | None = Query(default=None, alias="chapterId", min_length=1, max_length=64),
        upload_id: str | None = Query(default=None, alias="uploadId", min_length=1, max_length=64),
        include_reviewed: bool = Query(default=False, alias="includeReviewed"),
    ) -> dict[str, Any]:
        return service.list_inbox(
            chapter_id=chapter_id,
            upload_id=upload_id,
            include_reviewed=include_reviewed,
        )

    @router.get("/packages/{package_id}/cases")
    def list_cases(package_id: str) -> dict[str, Any]:
        return call(service.list_cases, package_id)

    @router.get("/cases/{case_id}")
    def get_case(case_id: str) -> dict[str, Any]:
        return call(service.get_case, case_id)

    @router.get("/cases/{case_id}/source-page", response_class=FileResponse)
    def get_source_page(case_id: str) -> FileResponse:
        path = call(service.get_source_page_preview, case_id)
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, no-store"})

    @router.post("/cases/{case_id}/decisions")
    def decide(case_id: str, request: Request, payload: QualityReviewDecisionRequest) -> dict[str, Any]:
        actor = call(quality_review_actor, request, display_name=payload.displayName)
        return call(
            service.decide,
            case_id=case_id,
            expected_record_version=payload.expectedRecordVersion,
            actor=actor,
            decision=payload.decision,
            rationale=payload.rationale,
            verified_quote=payload.verifiedQuote,
        )

    @router.post("/cases/{case_id}/revisions")
    def revise_case(case_id: str, request: Request, payload: QualityReviewRevisionRequest) -> dict[str, Any]:
        actor = call(quality_review_actor, request)
        return call(
            service.revise_case,
            case_id=case_id,
            expected_record_version=payload.expectedRecordVersion,
            actor=actor,
            input_value=payload.input,
            expected=payload.expected,
            actual=payload.actual,
        )

    return router


__all__ = ["build_quality_review_router"]
