from __future__ import annotations

import unittest

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from application import AppError
from app_factory import create_app
from infrastructure.runtime.contracts import RuntimeConfigSnapshot


class _Payload(BaseModel):
    count: int


class ProblemDetailsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()

        @self.app.get("/test/http-error")
        def http_error() -> None:
            raise HTTPException(status_code=409, detail="资源状态冲突")

        @self.app.post("/test/validation")
        def validation(payload: _Payload) -> dict[str, int]:
            return payload.model_dump()

        @self.app.get("/test/app-error")
        def app_error() -> None:
            raise AppError(
                "外部服务暂不可用",
                status_code=503,
                error_code="UPSTREAM_UNAVAILABLE",
                retryable=True,
                details={"provider": "test"},
            )

        @self.app.get("/test/unhandled")
        def unhandled() -> None:
            raise RuntimeError("secret provider response")

    def test_http_exception_preserves_status_and_chinese_message(self) -> None:
        response = TestClient(self.app).get("/test/http-error", headers={"X-Request-ID": "req-1"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {
            "requestId": "req-1",
            "errorCode": "HTTP_ERROR",
            "message": "资源状态冲突",
            "retryable": False,
            "details": None,
        })

    def test_validation_uses_same_shape_without_echoing_input(self) -> None:
        response = TestClient(self.app).post("/test/validation", json={"count": "not-a-number"})
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["errorCode"], "VALIDATION_ERROR")
        self.assertEqual(body["message"], "请求参数校验失败")
        self.assertNotIn("not-a-number", str(body))
        self.assertTrue(body["requestId"])

    def test_app_error_and_unhandled_error_are_safe(self) -> None:
        client = TestClient(self.app, raise_server_exceptions=False)
        app_response = client.get("/test/app-error")
        self.assertEqual(app_response.status_code, 503)
        self.assertEqual(app_response.json()["errorCode"], "UPSTREAM_UNAVAILABLE")
        self.assertTrue(app_response.json()["retryable"])

        internal_response = client.get("/test/unhandled")
        self.assertEqual(internal_response.status_code, 500)
        self.assertEqual(internal_response.json()["message"], "服务器内部错误")
        self.assertNotIn("secret provider response", internal_response.text)


class RuntimeConfigSnapshotTests(unittest.TestCase):
    def test_snapshot_hashes_prompt_and_schema_and_keeps_timeout(self) -> None:
        snapshot = RuntimeConfigSnapshot.for_model(
            "codex",
            "default",
            schema={"type": "object"},
            prompt="教材题目内容",
            runtime="tutor",
            timeout=45,
        )
        data = snapshot.to_dict()
        self.assertEqual(data["provider"], "codex")
        self.assertEqual(data["model"], "default")
        self.assertEqual(data["runtime"], "tutor")
        self.assertEqual(data["timeout"], 45)
        self.assertEqual(len(data["schema"]), 16)
        self.assertEqual(len(data["prompt"]), 16)
        self.assertNotIn("教材题目内容", str(data))


if __name__ == "__main__":
    unittest.main()
