"""FastAPI application factory and cross-cutting HTTP middleware."""

from __future__ import annotations

import os
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from observability import log_event, request_id_var


def _csv_env(name: str, default: str) -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def create_app() -> FastAPI:
    app = FastAPI(title="Dotty Tutor", version="0.5.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_csv_env(
            "CORS_ORIGINS",
            "http://localhost:5174,http://127.0.0.1:5174",
        ),
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
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
