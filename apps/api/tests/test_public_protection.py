from __future__ import annotations

import asyncio
import unittest
from contextlib import ExitStack

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from application import create_app
from persistence.auth_store import AuthSessionStore
from public_protection import PublicProtection
from tests.auth_test_support import environment
from tests.postgres_test_support import PostgresTestCase

# 只用于“不打开连接”的匿名用例：resolve(None) 在建立连接前就返回 None。
UNUSED_DATABASE_URL = "postgresql+psycopg://unused:unused@127.0.0.1:1/dotty_unused"


class PublicProtectionUnitTests(unittest.TestCase):
    def test_expensive_paths_include_model_and_background_work(self) -> None:
        self.assertTrue(PublicProtection.is_expensive_path("/api/help"))
        self.assertTrue(PublicProtection.is_expensive_path("/api/tutor/threads/t1/messages"))
        self.assertTrue(PublicProtection.is_expensive_path("/api/chapters/c1/generate-ai"))
        self.assertTrue(PublicProtection.is_expensive_path("/api/chapters/from-upload/u1"))
        self.assertTrue(PublicProtection.is_expensive_path("/api/quality-review-runs"))
        self.assertFalse(PublicProtection.is_expensive_path("/api/quality-review-runs/models"))
        self.assertFalse(PublicProtection.is_expensive_path("/api/quality-review-runs/run-1/ratings"))
        self.assertFalse(PublicProtection.is_expensive_path("/api/chapters/c1/lessons/l1/review"))
        self.assertFalse(PublicProtection.is_expensive_path("/api/health"))

    def test_courseware_source_explanation_consumes_model_quota_while_reads_do_not(self) -> None:
        from starlette.requests import Request

        def request(path: str, method: str) -> Request:
            return Request({"type": "http", "method": method, "path": path,
                            "headers": [], "client": ("client-a", 80)})

        explain = "/api/courseware/cw-1/source-search/explain"
        self.assertTrue(PublicProtection.is_expensive_path(explain))
        self.assertTrue(PublicProtection.is_expensive_request(request(explain, "POST")))
        # 同一路由下的检索与证据预览不调模型，仍然保持可用。
        self.assertFalse(PublicProtection.is_expensive_request(
            request("/api/courseware/cw-1/source-search", "GET")))
        self.assertFalse(PublicProtection.is_expensive_request(
            request("/api/courseware/cw-1/source-search/evidence/d1/preview", "GET")))
        # 只匹配 explain 本身，避免以后新增的子路径意外免费。
        self.assertFalse(PublicProtection.is_expensive_path(explain + "/extra"))

    def test_quality_review_and_voice_cancel_do_not_consume_model_work_quota(self) -> None:
        from starlette.requests import Request

        def request(path: str, method: str = "POST") -> Request:
            return Request({"type": "http", "method": method, "path": path,
                            "headers": [], "client": ("client-a", 80)})

        self.assertFalse(PublicProtection.is_expensive_request(request("/api/quality-reviews/packages")))
        self.assertTrue(PublicProtection.is_expensive_request(request("/api/quality-review-runs")))
        self.assertFalse(PublicProtection.is_expensive_request(request("/api/quality-review-runs/run-1/ratings")))
        self.assertFalse(PublicProtection.is_expensive_request(request("/api/quality-review-runs/run-1/cancel")))
        self.assertTrue(PublicProtection.is_expensive_request(request("/api/voice/sessions/s1/respond")))
        self.assertFalse(PublicProtection.is_expensive_request(request("/api/voice/sessions/s1/cancel")))

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


class ProtectedAnonymousProtectionTests(unittest.TestCase):
    """匿名请求在认证拒绝时也要经过限流/体积检查与安全头。

    这组用例不触数据库：``AuthSessionStore.resolve(None)`` 在连接前就返回 None，
    因此只要一个从不连接的 PostgreSQL URL 即可，不需要另一种数据库测试基础设施。
    """

    def build(self, **overrides: str):
        values = {
            "AUTH_MODE": "protected",
            "AUTH_COOKIE_SECURE": "1",
            "TEACHER_BOOTSTRAP_SECRET": "a" * 40,
            "CORS_ORIGINS": "https://testserver",
            "PUBLIC_PROTECTION_ENABLED": "true",
            "PUBLIC_RATE_LIMIT_REQUESTS": "100",
            **overrides,
        }
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(environment(values))
        app = create_app()
        engine = create_engine(UNUSED_DATABASE_URL, future=True)
        self.addCleanup(engine.dispose)
        app.state.auth_session_store = AuthSessionStore(engine=engine)
        # 真实存在的教师模型路由形状：认证拒绝时不应该走到它。
        app.add_api_route("/api/chapters/c1/generate-ai", lambda: {"ok": True}, methods=["POST"])
        client = TestClient(app, base_url="https://testserver")
        self.addCleanup(client.close)
        return app, client

    def test_user_calls_a_protected_route_without_a_session_then_the_rejection_carries_request_protection(self) -> None:
        _app, client = self.build()

        response = client.get("/api/classes")

        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.headers.get("X-Request-ID"))
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")

    def test_user_repeats_unauthenticated_requests_then_they_are_rate_limited_like_any_other_request(self) -> None:
        _app, client = self.build(PUBLIC_RATE_LIMIT_REQUESTS="2")

        statuses = [client.get("/api/classes").status_code for _ in range(3)]

        self.assertEqual(statuses, [401, 401, 429])

    def test_user_posts_from_an_untrusted_origin_then_the_403_also_carries_request_protection(self) -> None:
        _app, client = self.build()

        # 受保护模式对浏览器写请求校验 Origin；这不是认证失败，但同样是拒绝响应。
        response = client.post("/api/chapters/c1/generate-ai", headers={"Origin": "https://evil.example"})

        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.headers.get("X-Request-ID"))
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")

    def test_user_posts_an_oversized_body_without_a_session_then_the_size_limit_applies_first(self) -> None:
        _app, client = self.build(PUBLIC_MAX_REQUEST_BYTES="4")

        response = client.post("/api/chapters/c1/generate-ai", content=b"0123456789")

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["errorCode"], "REQUEST_TOO_LARGE")

    def test_user_calls_a_model_route_anonymously_while_slots_are_busy_then_no_slot_is_taken(self) -> None:
        app, client = self.build(PUBLIC_MODEL_CONCURRENCY="1")
        # Given every model slot is busy with someone else's work
        app.state.public_protection.model_semaphore = asyncio.Semaphore(0)

        # When an unauthenticated client calls a model route
        response = client.post("/api/chapters/c1/generate-ai")

        # Then it still receives the authentication rejection, not a concurrency rejection
        self.assertEqual(response.status_code, 401)
        self.assertNotEqual(response.json().get("errorCode"), "MODEL_CONCURRENCY_LIMITED")


class ProtectedModelConcurrencyTests(PostgresTestCase):
    """真实会话下，模型并发槽仍然保护实际模型工作。"""

    def test_user_calls_a_teacher_model_route_while_slots_are_busy_then_concurrency_limit_applies(self) -> None:
        from routers.auth_routes import COOKIE_NAME

        store = AuthSessionStore(engine=self.engine)
        token, _session = store.create_session(role="teacher", learner_id=None)
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(environment({
            "AUTH_MODE": "protected",
            "AUTH_COOKIE_SECURE": "1",
            "TEACHER_BOOTSTRAP_SECRET": "a" * 40,
            "CORS_ORIGINS": "https://testserver",
            "PUBLIC_PROTECTION_ENABLED": "true",
            "PUBLIC_MODEL_CONCURRENCY": "1",
            "PUBLIC_RATE_LIMIT_REQUESTS": "100",
        }))
        app = create_app()
        app.state.auth_session_store = store
        app.add_api_route("/api/chapters/c1/generate-ai", lambda: {"ok": True}, methods=["POST"])
        app.state.public_protection.model_semaphore = asyncio.Semaphore(0)
        client = TestClient(app, base_url="https://testserver")
        self.addCleanup(client.close)
        client.cookies.set(COOKIE_NAME, token)

        busy = client.post("/api/chapters/c1/generate-ai")

        self.assertEqual(busy.status_code, 429)
        self.assertEqual(busy.json()["errorCode"], "MODEL_CONCURRENCY_LIMITED")

        # 释放槽后同一教师请求可以继续，证明上面的 429 确实来自并发限制。
        app.state.public_protection.model_semaphore = asyncio.Semaphore(1)
        retry = client.post("/api/chapters/c1/generate-ai")
        self.assertEqual(retry.status_code, 200)


class VoiceCorsPreflightTests(unittest.TestCase):
    def test_user_voice_session_headers_are_allowed_by_preflight(self) -> None:
        with environment({"AUTH_MODE": "demo", "CORS_ORIGINS": "https://review.example"}):
            app = create_app()
        response = TestClient(app).options(
            "/api/voice/sessions",
            headers={
                "Origin": "https://review.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-learner-id,x-voice-session-token",
            },
        )
        self.assertEqual(response.status_code, 200)
        allowed = response.headers.get("access-control-allow-headers", "").lower()
        self.assertIn("x-learner-id", allowed)
        self.assertIn("x-voice-session-token", allowed)
