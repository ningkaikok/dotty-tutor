from __future__ import annotations

import unittest

from infrastructure.runtime.contracts import ModelRequest, ModelResult


class ModelGatewayContractTests(unittest.TestCase):
    def test_request_is_content_free_and_uses_stable_field_names(self) -> None:
        request = ModelRequest(
            task="tutoring",
            provider="codex",
            model="gpt-5.6-sol",
            timeout=45,
            allow_fallback=False,
            schema_version="schema-v1",
            runtime="tutor",
        )

        self.assertEqual(request.to_dict(), {
            "task": "tutoring",
            "provider": "codex",
            "model": "gpt-5.6-sol",
            "timeout": 45,
            "allowFallback": False,
            "schemaVersion": "schema-v1",
            "runtime": "tutor",
        })
        self.assertNotIn("prompt", str(request.to_dict()).lower())

    def test_request_rejects_implicit_or_invalid_policy(self) -> None:
        with self.assertRaises(ValueError):
            ModelRequest(
                task="",
                provider="codex",
                model="default",
                timeout=45,
                allow_fallback=False,
                schema_version="schema-v1",
            )
        with self.assertRaises(ValueError):
            ModelRequest(
                task="generation",
                provider="codex",
                model="default",
                timeout=0,
                allow_fallback=False,
                schema_version="schema-v1",
            )
        with self.assertRaises(ValueError):
            ModelRequest(
                task="generation",
                provider="codex",
                model="default",
                timeout=45,
                allow_fallback=1,  # type: ignore[arg-type]
                schema_version="schema-v1",
            )

    def test_result_requires_an_explained_fallback(self) -> None:
        with self.assertRaises(ValueError):
            ModelResult.succeeded(
                {"ok": True},
                actual_provider="ollama",
                actual_model="qwen",
                duration_ms=1,
                fallback=True,
            )

    def test_result_adapts_to_legacy_run_without_persisting_output(self) -> None:
        request = ModelRequest(
            task="review",
            provider="ollama",
            model="qwen",
            timeout=180,
            allow_fallback=False,
            schema_version="schema-v1",
            runtime="review",
        )
        result = ModelResult.succeeded(
            {"privateAnswer": "不应进入运行记录"},
            actual_provider="ollama",
            actual_model="qwen",
            duration_ms=12.5,
            usage={"promptTokens": 10, "outputTokens": 6},
        )

        run = result.to_run(
            request,
            prompt_chars=20,
            max_output_tokens=100,
            provider_attempts=1,
            schema_fallback={"used": False, "reason": None},
        )

        self.assertEqual(run["actualProvider"], "ollama")
        self.assertEqual(run["actualModel"], "qwen")
        self.assertFalse(run["modelRequest"]["allowFallback"])
        self.assertEqual(run["modelResult"]["usage"]["outputTokens"], 6)
        self.assertNotIn("output", run["modelResult"])
        self.assertNotIn("不应进入运行记录", str(run))


if __name__ == "__main__":
    unittest.main()
