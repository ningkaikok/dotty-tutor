"""FastAPI application factory and cross-cutting HTTP middleware."""

from __future__ import annotations

import os
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from application.errors import AppError, problem_details
from observability import log_event, request_id_var


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
            "http://localhost:5174,http://127.0.0.1:5174",
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
