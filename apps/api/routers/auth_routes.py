"""HTTP lifecycle for protected-mode teacher and learner sessions."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from auth_context import auth_mode

COOKIE_NAME = "dotty_session"


class SessionCreate(BaseModel):
    teacherSecret: str | None = Field(default=None, min_length=1, max_length=256)
    inviteToken: str | None = Field(default=None, min_length=1, max_length=256)


class InviteCreate(BaseModel):
    learnerId: str = Field(min_length=1, max_length=128)


def _cookie(response: Response, token: str, *, max_age: int = 43_200) -> None:
    response.set_cookie(
        COOKIE_NAME, token, max_age=max_age, path="/api", httponly=True,
        secure=True, samesite="strict",
    )


def build_auth_router(*, session_store: Any) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["authentication"])

    @router.get("/config")
    def auth_config() -> dict[str, bool]:
        return {"protected": auth_mode() == "protected"}

    @router.get("/sessions")
    def current_session(request: Request) -> dict[str, Any]:
        actor = getattr(request.state, "actor", None)
        if not actor:
            raise HTTPException(status_code=401, detail="请先登录")
        return {key: actor.get(key) for key in ("role", "learnerId", "expiresAt")}

    @router.post("/sessions")
    def create_session(request: SessionCreate, response: Response) -> dict[str, Any]:
        if auth_mode() != "protected":
            raise HTTPException(status_code=404, detail="Not Found")
        if bool(request.teacherSecret) == bool(request.inviteToken):
            raise HTTPException(status_code=422, detail="教师凭证和学生邀请只能提供其中一项")
        if request.teacherSecret:
            expected = os.getenv("TEACHER_BOOTSTRAP_SECRET", "")
            if not expected or not __import__("secrets").compare_digest(request.teacherSecret, expected):
                raise HTTPException(status_code=401, detail="教师凭证无效")
            token, session = session_store.create_session(role="teacher", learner_id=None)
        else:
            result = session_store.consume_invite(request.inviteToken or "")
            if not result:
                raise HTTPException(status_code=401, detail="学生邀请无效、已使用或已过期")
            token, session = result
        _cookie(response, token, max_age=max(1, int(session["expiresAt"] - session["createdAt"])))
        return {"role": session["role"], "learnerId": session["learnerId"], "expiresAt": session["expiresAt"]}

    @router.delete("/sessions")
    def end_session(request: Request, response: Response) -> dict[str, bool]:
        actor = getattr(request.state, "actor", None)
        if actor:
            session_store.revoke(str(actor["sessionId"]))
        response.delete_cookie(COOKIE_NAME, path="/api", secure=True, httponly=True, samesite="strict")
        return {"ended": True}

    @router.post("/invites")
    def create_invite(request: Request, invite: InviteCreate) -> dict[str, Any]:
        actor = getattr(request.state, "actor", None)
        if not actor or actor.get("role") != "teacher":
            raise HTTPException(status_code=403, detail="只有教师可以签发学生邀请")
        token = session_store.create_invite(
            learner_id=invite.learnerId,
            teacher_session_id=str(actor["sessionId"]),
        )
        return {"inviteToken": token, "learnerId": invite.learnerId, "expiresInSeconds": 86_400}

    @router.delete("/sessions/{session_id}")
    def revoke_session(request: Request, session_id: str) -> dict[str, bool]:
        actor = getattr(request.state, "actor", None)
        if not actor or actor.get("role") != "teacher":
            raise HTTPException(status_code=403, detail="只有教师可以撤销其他会话")
        return {"revoked": session_store.revoke(session_id)}

    return router


__all__ = ["COOKIE_NAME", "build_auth_router"]
