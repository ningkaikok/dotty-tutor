"""内容平台提示词路由：凭据能力由服务端检查，教师/学生页面不提供编辑入口。"""

from __future__ import annotations

import os
from secrets import compare_digest

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from domain.prompts.contracts import (
    ActivateRequest,
    DraftRequest,
    PreviewRequest,
    PreviewResponse,
    PromptDetail,
    PromptList,
    PromptRevision,
)
from persistence.prompt_store import PromptConflict, PromptStore
from prompts import CATALOG


def content_role(request: Request, response: Response) -> str:
    """未配置时拒绝开放；凭据不进入 URL/日志，敏感模板响应禁止浏览器缓存。"""
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "X-Content-Token"
    editor = os.getenv("DOTTY_CONTENT_EDITOR_TOKEN", "").strip()
    publisher = os.getenv("DOTTY_CONTENT_PUBLISHER_TOKEN", "").strip()
    if not editor and not publisher:
        raise HTTPException(503, "提示词管理未启用，请由维护者配置内容平台凭据")
    if editor and publisher and compare_digest(editor.encode(), publisher.encode()):
        raise HTTPException(503, "编辑与发布凭据必须分别配置，不能相同")
    token = request.headers.get("X-Content-Token", "")
    if publisher and compare_digest(token.encode(), publisher.encode()):
        return "content-publisher"
    if editor and compare_digest(token.encode(), editor.encode()):
        return "content-editor"
    raise HTTPException(403, "内容平台凭据无效")


def build_prompt_router(*, store: PromptStore) -> APIRouter:
    router = APIRouter(prefix="/api/content/prompts", tags=["content-prompts"])

    def require_template(template_id: str) -> None:
        if template_id not in CATALOG:
            raise HTTPException(404, "提示词不存在")

    @router.get("", response_model=PromptList)
    def list_prompts(role: str = Depends(content_role)) -> dict:
        return {"canPublish": role == "content-publisher", "items": [store.detail(key) for key in CATALOG]}

    @router.get("/{template_id}", response_model=PromptDetail)
    def detail(template_id: str, role: str = Depends(content_role)) -> dict:
        require_template(template_id)
        return store.detail(template_id)

    @router.post("/{template_id}/drafts", response_model=PromptRevision)
    def draft(template_id: str, body: DraftRequest, role: str = Depends(content_role)) -> dict:
        require_template(template_id)
        try:
            return store.save_draft(template_id, body.text, body.baseRevisionId, actor=role)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/{template_id}/preview", response_model=PreviewResponse)
    def preview(template_id: str, body: PreviewRequest, role: str = Depends(content_role)) -> dict:
        require_template(template_id)
        if len(body.variables) > 20 or sum(len(value) for value in body.variables.values()) > 24000:
            raise HTTPException(422, "预览样例过长")
        try:
            return {"rendered": store.preview(template_id, body.revisionId, body.variables)}
        except KeyError as error:
            raise HTTPException(404, "版本不存在") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/{template_id}/activate", response_model=PromptDetail)
    def activate(template_id: str, body: ActivateRequest, role: str = Depends(content_role)) -> dict:
        require_template(template_id)
        if role != "content-publisher":
            raise HTTPException(403, "需要内容发布凭据")
        try:
            store.activate(template_id, body.revisionId, body.expectedActiveRevisionId,
                           actor=role, rollback=body.action == "rollback")
            return store.detail(template_id)
        except PromptConflict as error:
            raise HTTPException(409, str(error)) from error
        except KeyError as error:
            raise HTTPException(404, "版本不存在") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return router
