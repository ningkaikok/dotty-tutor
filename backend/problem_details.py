"""RFC 7807 (Problem Details for HTTP APIs) error responses.

Every error response carries ``type``, ``title``, ``status`` and ``detail``
with ``Content-Type: application/problem+json`` instead of FastAPI's default
bare ``{"detail": ...}``. ``detail`` keeps the exact message the route already
raises, so the frontend's existing ``response.json().detail`` reads need no
change — this only adds structure around it, it never removes information.

``type`` stays ``about:blank`` (the value RFC 7807 itself designates for
problems with no more specific documentation page) since this project does
not publish a per-error-code docs site.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_MEDIA_TYPE = "application/problem+json"


def _title_for_status(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Error"


def problem_response(
    request: Request,
    status_code: int,
    detail: str,
    **extensions: Any,
) -> JSONResponse:
    body = {
        "type": "about:blank",
        "title": _title_for_status(status_code),
        "status": status_code,
        "detail": detail,
        "instance": request.url.path,
        **extensions,
    }
    return JSONResponse(status_code=status_code, content=body, media_type=PROBLEM_MEDIA_TYPE)


def _validation_error_detail(exc: RequestValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error.get("loc", []) if item != "body")
        message = error.get("msg", "invalid value")
        parts.append(f"{location}: {message}" if location else message)
    return "；".join(parts) or "请求参数校验失败"


def register_problem_handlers(app: Any) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return problem_response(request, exc.status_code, detail)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return problem_response(
            request,
            422,
            _validation_error_detail(exc),
            errors=jsonable_encoder(exc.errors()),
        )
