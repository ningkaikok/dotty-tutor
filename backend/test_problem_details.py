from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

import app


class ProblemDetailsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app.app)

    def test_http_exception_becomes_a_problem_document(self) -> None:
        response = self.client.get("/api/lessons/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        body = response.json()
        self.assertEqual(body["type"], "about:blank")
        self.assertEqual(body["title"], "Not Found")
        self.assertEqual(body["status"], 404)
        # detail keeps the exact route message — the frontend's `data?.detail` reads
        # need no change to keep working against the new envelope.
        self.assertEqual(body["detail"], "课程不存在")
        self.assertEqual(body["instance"], "/api/lessons/does-not-exist")

    def test_request_validation_error_becomes_a_problem_document(self) -> None:
        response = self.client.post("/api/ocr/select", json={"provider": "bogus"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.headers["content-type"], "application/problem+json")
        body = response.json()
        self.assertEqual(body["status"], 422)
        self.assertIn("provider", body["detail"])
        self.assertTrue(body["errors"])

    def test_security_headers_still_apply_to_problem_responses(self) -> None:
        response = self.client.get("/api/lessons/does-not-exist")
        self.assertTrue(response.headers.get("x-request-id"))
        self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")

    def test_successful_response_is_unaffected(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/json")
