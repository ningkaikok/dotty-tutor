"""Structured, privacy-aware application logging.

The API writes one JSON object per line so Docker, journald and log collectors
can index events without parsing free-form text. Request IDs are kept in a
context variable and automatically attached to application events.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from typing import Any

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "dotty_request_id", default=""
)
SENSITIVE_FIELDS = {"prompt", "source_text", "student_input", "token"}


class JsonFormatter(logging.Formatter):
    """Render log records as compact JSON without user document contents."""

    def format(self, record: logging.LogRecord) -> str:
        fields = {
            key: value
            for key, value in getattr(record, "fields", {}).items()
            if key not in SENSITIVE_FIELDS
        }
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id and "request_id" not in fields:
            payload["request_id"] = request_id
        payload.update(fields)
        if record.exc_info:
            payload["exception"] = "".join(traceback.format_exception(*record.exc_info)).strip()
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> logging.Logger:
    logger = logging.getLogger("dotty")
    if logger.handlers:
        return logger

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


logger = configure_logging()


def log_event(
    event: str,
    *,
    level: int = logging.INFO,
    exc_info: bool | BaseException = False,
    **fields: Any,
) -> None:
    """Write a bounded event payload; callers must pass metadata, not content."""

    safe_fields = {
        key: value
        for key, value in fields.items()
        if value is not None and key not in SENSITIVE_FIELDS
    }
    logger.log(
        level,
        event,
        extra={"event": event, "fields": safe_fields},
        exc_info=exc_info,
    )
