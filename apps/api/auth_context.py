"""Request identity helpers shared by protected-mode HTTP adapters."""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Request

from domain.constants import DEMO_LEARNER_ID


def auth_mode() -> str:
    return os.getenv("AUTH_MODE", "demo").strip().lower()


def learner_for_request(request: Request, supplied: str | None = None) -> str:
    """Resolve student identity from the session in protected mode."""
    if auth_mode() == "demo":
        return supplied or DEMO_LEARNER_ID
    actor: dict[str, Any] | None = getattr(request.state, "actor", None)
    if not actor or actor.get("role") != "student" or not actor.get("learnerId"):
        raise HTTPException(status_code=403, detail="此操作需要学生会话")
    learner_id = str(actor["learnerId"])
    if supplied and supplied != learner_id:
        raise HTTPException(status_code=403, detail="请求身份与当前学生会话不匹配")
    return learner_id


def require_owner(request: Request, learner_id: str) -> None:
    if auth_mode() == "demo":
        return
    actor: dict[str, Any] | None = getattr(request.state, "actor", None)
    if not actor or actor.get("role") != "student" or actor.get("learnerId") != learner_id:
        raise HTTPException(status_code=404, detail="资源不存在")
