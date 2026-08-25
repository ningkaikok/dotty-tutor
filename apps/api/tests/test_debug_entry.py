"""管理员调试入口的验收测试（P1 收尾）。

三重门控与脱敏边界：
1. 环境未配置 token → 404（对外等于端点不存在）；
2. token 不匹配 → 403；
3. 匹配才返回环形缓冲中的脱敏摘要（类型 + 截断消息，无完整堆栈）。
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from application import create_app


class DebugEntryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()

        @self.app.get("/test/boom")
        async def boom() -> dict[str, str]:
            raise RuntimeError("secret provider response with internals")

        # raise_server_exceptions=False：让中间件走异常处理并记录环形缓冲。
        self.client = TestClient(self.app, raise_server_exceptions=False)
        # 默认清空 token，单个用例再按需设置（with patch.dict 显式管理）。
        os.environ.pop("DOTTY_DEBUG_TOKEN", None)

    def _trigger_failure(self, request_id: str = "req-debug-1") -> None:
        response = self.client.get(
            "/test/boom", headers={"X-Request-ID": request_id}
        )
        self.assertEqual(response.status_code, 500)
        # 响应保持脱敏：内部消息不得出现。
        self.assertNotIn("secret provider response", response.text)

    def test_endpoint_hidden_without_token(self) -> None:
        self._trigger_failure()
        response = self.client.get("/api/debug/errors")
        self.assertEqual(response.status_code, 404)

    def test_wrong_token_forbidden(self) -> None:
        with patch.dict(os.environ, {"DOTTY_DEBUG_TOKEN": "real-token"}):
            self._trigger_failure()
            response = self.client.get(
                "/api/debug/errors", headers={"X-Debug-Token": "wrong"}
            )
            self.assertEqual(response.status_code, 403)

    def test_correct_token_returns_sanitized_entries(self) -> None:
        with patch.dict(os.environ, {"DOTTY_DEBUG_TOKEN": "real-token"}):
            self._trigger_failure("req-debug-1")
            response = self.client.get(
                "/api/debug/errors?request_id=req-debug-1",
                headers={"X-Debug-Token": "real-token"},
            )
            self.assertEqual(response.status_code, 200)
            items = response.json()["items"]
            self.assertEqual(len(items), 1)
            entry = items[0]
            self.assertEqual(entry["requestId"], "req-debug-1")
            self.assertEqual(entry["errorType"], "RuntimeError")
            # 摘要允许包含错误消息片段（运维需要），但完整堆栈只进日志。
            self.assertIn("secret provider response", entry["error"])

    def test_request_id_filter_no_match_returns_empty(self) -> None:
        with patch.dict(os.environ, {"DOTTY_DEBUG_TOKEN": "real-token"}):
            self._trigger_failure("req-a")
            response = self.client.get(
                "/api/debug/errors?request_id=req-does-not-exist",
                headers={"X-Debug-Token": "real-token"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["items"], [])


if __name__ == "__main__":
    unittest.main()
