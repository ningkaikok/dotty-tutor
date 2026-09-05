"""FastAPI application factory and cross-cutting HTTP middleware."""

from __future__ import annotations

import os
import time
import uuid
from collections import deque
from secrets import compare_digest

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from application.errors import AppError, problem_details
from observability import log_event, request_id_var

# 管理员调试环形缓冲：最近 50 条失败请求的脱敏摘要；进程内存态，重启即清。
_DEBUG_ERROR_RING: deque[dict[str, str | float]] = deque(maxlen=50)


def _csv_env(name: str, default: str) -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def create_app() -> FastAPI:
    app = FastAPI(title="Dotty Tutor", version="0.21.1")

    def current_request_id(request: Request) -> str:
        """Prefer the middleware context, with a safe fallback for direct handlers."""
        return request_id_var.get() or request.headers.get("X-Request-ID", "")

    def response(
        request: Request,
        *,
        status_code: int,
        payload: dict,
        headers: dict[str, str] | None = None,
    ) -> JSONResponse:
        response_headers = {"X-Request-ID": current_request_id(request)}
        response_headers.update(headers or {})
        return JSONResponse(status_code=status_code, content=payload, headers=response_headers)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, error: AppError) -> JSONResponse:
        return response(
            request,
            status_code=error.status_code,
            payload=error.to_problem(current_request_id(request)),
            headers=error.headers,
        )

    @app.exception_handler(HTTPException)
    async def http_error_handler(request: Request, error: HTTPException) -> JSONResponse:
        detail = error.detail
        if isinstance(detail, str):
            message = detail
            details = None
        elif detail is None:
            message = "请求失败"
            details = None
        else:
            message = "请求失败"
            details = detail
        retryable = error.status_code in {408, 425, 429, 502, 503, 504}
        return response(
            request,
            status_code=error.status_code,
            payload=problem_details(
                request_id=current_request_id(request),
                error_code="HTTP_ERROR",
                message=message,
                retryable=retryable,
                details=details,
            ),
            headers=dict(error.headers or {}),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, error: RequestValidationError) -> JSONResponse:
        # ``input`` and ``ctx`` may contain full student/document content or an
        # unserialisable exception; location and message are sufficient to fix a request.
        details = [
            {
                "loc": list(item.get("loc", ())),
                "message": str(item.get("msg", "参数无效")),
                "type": str(item.get("type", "value_error")),
            }
            for item in error.errors()
        ]
        return response(
            request,
            status_code=422,
            payload=problem_details(
                request_id=current_request_id(request),
                error_code="VALIDATION_ERROR",
                message="请求参数校验失败",
                details=details,
            ),
        )

    @app.get("/api/debug/errors")
    def debug_errors(
        request: Request,
        request_id: str = "",
    ) -> JSONResponse:
        """管理员调试入口（P1 收尾）：按 request_id 查最近失败请求的内部摘要。

        三重门控：环境未配置 token 时端点返回 404（对外等于不存在）；
        token 不匹配返回 403；匹配才返回环形缓冲中的脱敏摘要。
        正常 Problem JSON 响应仍然不含任何内部信息，两者互补。
        """
        # token 只从 X-Debug-Token 读取。此前签名上还有一个 x_debug_token 查询
        # 参数，但函数体从不读它——按签名传查询参数的人只会拿到 403 并误判成
        # token 配错，因此删掉而不是补上支持：凭据不该出现在 URL 里（会进访问
        # 日志、浏览器历史和 Referer）。docstring 会进公开 OpenAPI 描述，
        # 所以这段说明留在注释里。
        expected = os.getenv("DOTTY_DEBUG_TOKEN", "").strip()
        if not expected:
            raise HTTPException(status_code=404, detail="Not Found")
        if not compare_digest(request.headers.get("X-Debug-Token") or "", expected):
            raise HTTPException(status_code=403, detail="Forbidden")
        items = list(_DEBUG_ERROR_RING)
        if request_id:
            items = [item for item in items if item["requestId"] == request_id]
        return JSONResponse({"items": items})

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, error: Exception) -> JSONResponse:
        # Middleware records the exception with request metadata. Keep the HTTP
        # response deliberately generic so provider errors and paths do not leak.
        log_event(
            "http.exception.unhandled",
            level=40,
            method=request.method,
            path=request.url.path,
            error_type=type(error).__name__,
            exc_info=True,
        )
        return response(
            request,
            status_code=500,
            payload=problem_details(
                request_id=current_request_id(request),
                error_code="INTERNAL_ERROR",
                message="服务器内部错误",
                retryable=False,
            ),
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_csv_env(
            "CORS_ORIGINS",
            "http://localhost:59174,http://127.0.0.1:59174",
        ),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID", "Idempotency-Key"],
    )
    trusted_hosts = _csv_env("TRUSTED_HOSTS", "")
    if trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        response = None
        try:
            response = await call_next(request)
            return response
        except Exception as error:
            # 管理员调试环形缓冲：只存脱敏摘要（类型 + 截断消息），完整堆栈仍只进日志。
            _DEBUG_ERROR_RING.append({
                "requestId": request_id,
                "method": request.method,
                "path": request.url.path,
                "errorType": type(error).__name__,
                "error": str(error)[:300],
                "at": time.time(),
            })
            log_event(
                "http.request.failed",
                level=40,
                method=request.method,
                path=request.url.path,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                error_type=type(error).__name__,
                exc_info=True,
            )
            raise
        finally:
            if response is not None:
                response.headers["X-Request-ID"] = request_id
                response.headers.setdefault("X-Content-Type-Options", "nosniff")
                response.headers.setdefault("X-Frame-Options", "DENY")
                response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=(self)")
                status_code = response.status_code
                log_event(
                    "http.request",
                    level=30 if status_code >= 400 else 20,
                    method=request.method,
                    path=request.url.path,
                    status_code=status_code,
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )
            request_id_var.reset(token)

    return app
