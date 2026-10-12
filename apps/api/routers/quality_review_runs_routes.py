"""Teacher API for bounded model comparisons and human gold export."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import IntegrityError

from application.services.quality_review import quality_review_actor
from domain.contracts.quality_review_runs import (
    QualityReviewRunCreate,
    QualityReviewRunRating,
)


def build_quality_review_runs_router(service: Any) -> APIRouter:
    router = APIRouter(prefix="/api/quality-review-runs", tags=["quality-review-runs"])

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except PermissionError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        except IntegrityError as error:
            raise HTTPException(status_code=409, detail="当前用户已为该案例提交过评分") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("/models")
    def models(request: Request) -> dict[str, Any]:
        call(quality_review_actor, request)
        return service.model_catalog()

    @router.get("/gold-export")
    def gold_export(request: Request) -> dict[str, Any]:
        call(quality_review_actor, request)
        return service.gold_export()

    @router.post("", status_code=201)
    def create_run(request: Request, payload: QualityReviewRunCreate) -> dict[str, Any]:
        actor = call(quality_review_actor, request)
        return call(service.create_run, payload.model_dump(), actor)

    @router.get("")
    def list_runs(request: Request) -> dict[str, Any]:
        actor = call(quality_review_actor, request)
        return call(service.list_runs, actor)

    @router.get("/{run_id}/summary")
    def summary(run_id: str, request: Request) -> dict[str, Any]:
        actor = call(quality_review_actor, request)
        return call(service.statistics, run_id, actor)

    @router.post("/{run_id}/ratings")
    def rate(run_id: str, request: Request, payload: QualityReviewRunRating) -> dict[str, Any]:
        actor = call(quality_review_actor, request)
        return call(service.rate_case, run_id, payload.model_dump(), actor)

    @router.post("/{run_id}/cancel")
    def cancel(run_id: str, request: Request) -> dict[str, Any]:
        actor = call(quality_review_actor, request)
        return call(service.cancel_run, run_id, actor)

    @router.get("/{run_id}")
    def get_run(run_id: str, request: Request) -> dict[str, Any]:
        actor = call(quality_review_actor, request)
        return call(service.get_run, run_id, actor)

    return router


__all__ = ["build_quality_review_runs_router"]
