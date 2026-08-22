from __future__ import annotations

import json
import logging
import unittest

from observability import JsonFormatter, request_id_var


class ObservabilityTests(unittest.TestCase):
    def test_json_formatter_includes_request_id_and_excludes_content_fields(self) -> None:
        token = request_id_var.set("request-test-123")
        try:
            record = logging.getLogger("test").makeRecord(
                "test",
                logging.INFO,
                __file__,
                1,
                "upload.completed",
                (),
                None,
                extra={
                    "event": "upload.completed",
                    "fields": {
                        "upload_id": "upload-1",
                        "prompt": "教材原文不应进入日志",
                    },
                },
            )
            payload = json.loads(JsonFormatter().format(record))
        finally:
            request_id_var.reset(token)

        self.assertEqual(payload["event"], "upload.completed")
        self.assertEqual(payload["request_id"], "request-test-123")
        self.assertEqual(payload["upload_id"], "upload-1")
        self.assertNotIn("prompt", payload)


if __name__ == "__main__":
    unittest.main()
