"""Small process-local protections for the public demo deployment.

This is intentionally a first line of defence rather than an authentication
system. Render's free service currently runs one API instance, so a bounded
in-memory window is useful for stopping accidental bursts and cheap model-call
abuse. A restart clears the counters; production deployments should still put
an edge rate limiter and real user authentication in front of the API.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque

from fastapi import Request


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return max(minimum, value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ProtectionDecision:
    """The result of applying one request's public-demo limits."""

    allowed: bool
    limit: int
    retry_after: int = 0
    reason: str = ""


class _SlidingWindow:
    def __init__(self, limit: int, window_seconds: int) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.events: dict[str, Deque[float]] = {}

    def allow(self, key: str, now: float) -> ProtectionDecision:
        events = self.events.setdefault(key, deque())
        cutoff = now - self.window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= self.limit:
            retry_after = max(1, int(events[0] + self.window_seconds - now + 0.999))
            return ProtectionDecision(
                allowed=False,
                limit=self.limit,
                retry_after=retry_after,
                reason="rate_limit",
            )
        events.append(now)
        return ProtectionDecision(allowed=True, limit=self.limit)

    def prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        expired = []
        for key, events in self.events.items():
            while events and events[0] <= cutoff:
                events.popleft()
            if not events:
                expired.append(key)
        for key in expired:
            self.events.pop(key, None)


class PublicProtection:
    """Apply bounded request, model-call, body-size and concurrency limits."""

    # These paths can call DeepSeek directly or enqueue work that will call it.
    # Reads and health checks remain available while an abusive client is being
    # throttled.
    EXPENSIVE_PREFIXES = (
        "/api/help",
        "/api/textbook/",
        "/api/mistakes/",
        "/api/tutor/",
        "/api/variations/",
        "/api/classes/",
    )

    def __init__(
        self,
        *,
        enabled: bool = True,
        request_limit: int = 120,
        request_window_seconds: int = 60,
        model_limit: int = 6,
        model_window_seconds: int = 60,
        model_daily_limit: int = 60,
        max_request_bytes: int = 12 * 1024 * 1024,
        model_concurrency: int = 2,
        trust_proxy_headers: bool = False,
    ) -> None:
        self.enabled = enabled
        self.max_request_bytes = max_request_bytes
        self.trust_proxy_headers = trust_proxy_headers
        self.requests = _SlidingWindow(request_limit, request_window_seconds)
        self.model_requests = _SlidingWindow(model_limit, model_window_seconds)
        self.model_daily = _SlidingWindow(model_daily_limit, 24 * 60 * 60)
        self.model_semaphore = asyncio.Semaphore(model_concurrency)
        self._last_prune = 0.0

    @classmethod
    def from_env(cls) -> "PublicProtection":
        return cls(
            enabled=_env_bool("PUBLIC_PROTECTION_ENABLED", True),
            request_limit=_env_int("PUBLIC_RATE_LIMIT_REQUESTS", 120),
            request_window_seconds=_env_int("PUBLIC_RATE_LIMIT_WINDOW_SECONDS", 60),
            model_limit=_env_int("PUBLIC_MODEL_RATE_LIMIT_REQUESTS", 6),
            model_window_seconds=_env_int("PUBLIC_MODEL_RATE_LIMIT_WINDOW_SECONDS", 60),
            model_daily_limit=_env_int("PUBLIC_MODEL_DAILY_LIMIT", 60),
            max_request_bytes=_env_int("PUBLIC_MAX_REQUEST_BYTES", 12 * 1024 * 1024),
            model_concurrency=_env_int("PUBLIC_MODEL_CONCURRENCY", 2),
            trust_proxy_headers=_env_bool("TRUST_PROXY_HEADERS", False),
        )

    def client_key(self, request: Request) -> str:
        """Resolve a client key without trusting proxy headers by default."""
        if self.trust_proxy_headers:
            forwarded = request.headers.get("X-Forwarded-For", "")
            if forwarded:
                return forwarded.split(",", 1)[0].strip() or "unknown"
            real_ip = request.headers.get("X-Real-IP", "")
            if real_ip.strip():
                return real_ip.strip()
        return request.client.host if request.client else "unknown"

    @classmethod
    def is_expensive_path(cls, path: str) -> bool:
        return path.startswith(cls.EXPENSIVE_PREFIXES)

    def check(self, request: Request) -> ProtectionDecision:
        if not self.enabled or request.method == "OPTIONS":
            return ProtectionDecision(allowed=True, limit=0)
        path = request.url.path
        if path == "/api/health":
            return ProtectionDecision(allowed=True, limit=0)

        now = time.monotonic()
        if now - self._last_prune > 60:
            self.requests.prune(now)
            self.model_requests.prune(now)
            self.model_daily.prune(time.time())
            self._last_prune = now

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_request_bytes:
                    return ProtectionDecision(
                        allowed=False,
                        limit=self.max_request_bytes,
                        reason="request_too_large",
                    )
            except ValueError:
                return ProtectionDecision(
                    allowed=False,
                    limit=self.max_request_bytes,
                    reason="invalid_content_length",
                )

        key = self.client_key(request)
        decision = self.requests.allow(key, now)
        if not decision.allowed:
            return decision
        if self.is_expensive_path(path):
            decision = self.model_requests.allow(key, now)
            if not decision.allowed:
                return decision
            decision = self.model_daily.allow(key, time.time())
            if not decision.allowed:
                return ProtectionDecision(
                    allowed=False,
                    limit=decision.limit,
                    retry_after=decision.retry_after,
                    reason="model_daily_limit",
                )
        return ProtectionDecision(allowed=True, limit=decision.limit)

    async def acquire_model_slot(self) -> bool:
        """Reject excess concurrent model work instead of queuing indefinitely."""
        if self.model_semaphore.locked():
            return False
        await self.model_semaphore.acquire()
        return True

    def release_model_slot(self) -> None:
        self.model_semaphore.release()
