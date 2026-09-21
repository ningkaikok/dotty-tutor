from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from application import create_app
from public_protection import PublicProtection


class PublicProtectionUnitTests(unittest.TestCase):
    def test_expensive_paths_include_model_and_background_work(self) -> None:
        self.assertTrue(PublicProtection.is_expensive_path("/api/help"))
        self.assertTrue(PublicProtection.is_expensive_path("/api/tutor/threads/t1/messages"))
        self.assertFalse(PublicProtection.is_expensive_path("/api/health"))

    def test_rate_limit_is_scoped_to_client(self) -> None:
        protection = PublicProtection(request_limit=1, request_window_seconds=60)
        first = protection.requests.allow("client-a", 1.0)
        second = protection.requests.allow("client-a", 1.1)
        other = protection.requests.allow("client-b", 1.1)
        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertTrue(other.allowed)

    def test_request_body_limit_returns_413(self) -> None:
        with patch.dict(os.environ, {"PUBLIC_MAX_REQUEST_BYTES": "10"}, clear=False):
            app = create_app()

        @app.post("/api/test-body")
        async def test_body() -> dict[str, bool]:
            return {"ok": True}

        response = TestClient(app).post(
            "/api/test-body",
            content=b"01234567890",
            headers={"Content-Length": "11"},
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["errorCode"], "REQUEST_TOO_LARGE")

    def test_rate_limit_returns_retryable_problem(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PUBLIC_RATE_LIMIT_REQUESTS": "1",
                "PUBLIC_RATE_LIMIT_WINDOW_SECONDS": "60",
            },
            clear=False,
        ):
            app = create_app()

        @app.get("/api/test-rate")
        async def test_rate() -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app)
        self.assertEqual(client.get("/api/test-rate").status_code, 200)
        response = client.get("/api/test-rate")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["errorCode"], "PUBLIC_RATE_LIMITED")
        self.assertEqual(response.headers["Retry-After"], "60")
