from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from application import create_app
from public_protection import PublicProtection
from tests.auth_test_support import environment


class PublicProtectionUnitTests(unittest.TestCase):
    def test_expensive_paths_include_model_and_background_work(self) -> None:
        self.assertTrue(PublicProtection.is_expensive_path("/api/help"))
        self.assertTrue(PublicProtection.is_expensive_path("/api/tutor/threads/t1/messages"))
        self.assertTrue(PublicProtection.is_expensive_path("/api/chapters/c1/generate-ai"))
        self.assertFalse(PublicProtection.is_expensive_path("/api/chapters/c1/lessons/l1/review"))
        self.assertFalse(PublicProtection.is_expensive_path("/api/health"))

    def test_rate_limit_is_scoped_to_client(self) -> None:
        protection = PublicProtection(enabled=True, request_limit=1, request_window_seconds=60)
        first = protection.requests.allow("client-a", 1.0)
        second = protection.requests.allow("client-a", 1.1)
        other = protection.requests.allow("client-b", 1.1)
        self.assertTrue(first.allowed)
        self.assertFalse(second.allowed)
        self.assertTrue(other.allowed)

    def test_request_body_limit_returns_413(self) -> None:
        with environment({"PUBLIC_PROTECTION_ENABLED": "true", "PUBLIC_MAX_REQUEST_BYTES": "10"}):
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
        with environment(
            {
                "PUBLIC_PROTECTION_ENABLED": "true",
                "PUBLIC_RATE_LIMIT_REQUESTS": "1",
                "PUBLIC_RATE_LIMIT_WINDOW_SECONDS": "60",
            },
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


    def test_user_reads_do_not_consume_model_quota_and_window_recovers(self) -> None:
        # Given a one-request model window, ordinary reads remain available.
        from starlette.requests import Request

        def request(method: str) -> Request:
            return Request({"type": "http", "method": method, "path": "/api/classes/c1",
                            "headers": [], "client": ("client-a", 80)})

        protection = PublicProtection(enabled=True, model_limit=1, model_window_seconds=60)
        # When the user reads twice then requests model work.
        self.assertTrue(protection.check(request("GET"), now=100, wall_now=100).allowed)
        self.assertTrue(protection.check(request("GET"), now=101, wall_now=101).allowed)
        self.assertTrue(protection.check(request("POST"), now=102, wall_now=102).allowed)
        blocked = protection.check(request("POST"), now=103, wall_now=103)
        # Then another model request is blocked until the window expires.
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.retry_after, 59)
        self.assertTrue(protection.check(request("GET"), now=104, wall_now=104).allowed)
        self.assertTrue(protection.check(request("POST"), now=162, wall_now=162).allowed)

    def test_user_default_configuration_keeps_existing_requests_available(self) -> None:
        # Given an unset opt-in flag, even configured limits are inactive.
        with environment({"PUBLIC_PROTECTION_ENABLED": "", "PUBLIC_MAX_REQUEST_BYTES": "1"}):
            app = create_app()

        @app.post("/api/classes/test-default")
        async def user_request() -> dict[str, bool]:
            return {"ok": True}

        client = TestClient(app)
        # When the user submits several requests, then all reach the route.
        for _ in range(4):
            response = client.post("/api/classes/test-default", content=b"longer than one byte")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"ok": True})
        self.assertFalse(PublicProtection().enabled)


class PublicProtectionConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_can_retry_after_other_model_work_finishes(self) -> None:
        # Given one available model slot.
        protection = PublicProtection(enabled=True, model_concurrency=1)
        self.assertTrue(await protection.acquire_model_slot())
        # When a second request arrives, then it is rejected without queueing.
        self.assertFalse(await protection.acquire_model_slot())
        # When the first finishes, then the retry can start.
        protection.release_model_slot()
        self.assertTrue(await protection.acquire_model_slot())
        protection.release_model_slot()
