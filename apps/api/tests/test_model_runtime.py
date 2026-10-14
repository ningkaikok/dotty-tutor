from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from infrastructure.runtime.contracts import RuntimeExecutionError
from infrastructure.runtime.model_runtime import (
    ModelRuntime,
    codex_command,
    codex_models,
)


class CodexCommandTests(unittest.TestCase):
    def test_default_catalog_includes_supported_subscription_models(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(codex_models(), [
                "default", "gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.5", "gpt-5.4",
            ])

    def test_catalog_can_be_limited_by_environment(self) -> None:
        with patch.dict(os.environ, {"CODEX_MODELS": "default, gpt-5.6-sol"}, clear=True):
            self.assertEqual(codex_models(), ["default", "gpt-5.6-sol"])

    def test_defaults_to_path_available_in_desktop_install(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(codex_command().endswith("/codex"))

    def test_respects_override_for_bundled_installs(self) -> None:
        bundled_path = "/Applications/ChatGPT.app/Contents/Resources/codex"
        with patch.dict(os.environ, {"CODEX_COMMAND": bundled_path}, clear=True):
            self.assertEqual(codex_command(), bundled_path)

    def test_tutor_runtime_reads_independent_environment_prefix(self) -> None:
        with patch.dict(os.environ, {
            "MODEL_PROVIDER": "ollama",
            "MODEL_NAME": "qwen2.5:3b",
            "TUTOR_MODEL_PROVIDER": "codex",
            "TUTOR_MODEL_NAME": "gpt-5.6-sol",
        }, clear=True):
            content_runtime = ModelRuntime()
            tutor_runtime = ModelRuntime(env_prefix="TUTOR_")

        self.assertEqual((content_runtime.selection.provider, content_runtime.selection.model), ("ollama", "qwen2.5:3b"))
        self.assertEqual((tutor_runtime.selection.provider, tutor_runtime.selection.model), ("codex", "gpt-5.6-sol"))

    def test_explicit_review_run_contains_real_provider_metadata(self) -> None:
        runtime = ModelRuntime()
        payload = {"ok": True}
        usage = {
            "prompt_tokens": 12,
            "output_tokens": 7,
            "providerAttempts": 2,
            "schemaFallback": {"used": True, "reason": "RuntimeError"},
        }
        with patch.object(runtime, "_ollama_json", return_value=(payload, usage)):
            result, run = runtime.generate_json_as(
                "ollama", "qwen", "prompt", {"type": "object"}, max_tokens=10
            )
        self.assertEqual(result, payload)
        self.assertEqual(run["providerAttempts"], 2)
        self.assertEqual(run["usage"], {"promptTokens": 12, "outputTokens": 7})
        self.assertEqual(run["schemaFallback"]["used"], True)
        self.assertGreaterEqual(run["durationMs"], 0)
        self.assertEqual(run["modelRequest"]["task"], "review")
        self.assertEqual(run["modelRequest"]["schemaVersion"], run["config"]["schema"])
        self.assertFalse(run["modelRequest"]["allowFallback"])
        self.assertEqual(run["modelResult"]["actualProvider"], "ollama")
        self.assertEqual(run["modelResult"]["actualModel"], "qwen")
        self.assertIsNone(run["modelResult"]["error"])

    def test_generation_failure_carries_normalized_contract_metadata(self) -> None:
        runtime = ModelRuntime()
        runtime.selection.provider = "ollama"
        runtime.selection.model = "qwen"
        with patch.object(runtime, "_ollama_json", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeExecutionError) as context:
                runtime.generate_json(
                    "prompt",
                    {"type": "object"},
                    task="tutoring",
                    allow_fallback=False,
                )

        run = context.exception.runtime_run
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run["modelRequest"]["task"], "tutoring")
        self.assertFalse(run["modelRequest"]["allowFallback"])
        self.assertEqual(run["modelResult"]["actualProvider"], "ollama")
        self.assertEqual(run["modelResult"]["error"]["type"], "RuntimeExecutionError")
        self.assertIsInstance(run["error"], str)
        self.assertNotIn("prompt", str(run["modelRequest"]).lower())


class DeepSeekProviderTests(unittest.TestCase):
    def test_user_sets_a_bounded_review_timeout_and_gateway_passes_it_to_adapter(self) -> None:
        # Given an explicitly selected model and a 2.5 second comparison cap
        runtime = ModelRuntime()
        with patch.object(runtime, "_ollama_json", return_value=({"ok": True}, {})) as adapter:
            # When the structured runtime issues that model request
            _result, run = runtime.generate_json_as(
                "ollama", "qwen", "prompt", {"type": "object"}, timeout_seconds=2.5
            )
        # Then both the audited request and actual transport adapter receive the cap
        self.assertEqual(run["modelRequest"]["timeout"], 2.5)
        self.assertEqual(adapter.call_args.kwargs["timeout_seconds"], 2.5)

    def test_user_limits_deepseek_http_transport_timeout(self) -> None:
        # Given a real HTTP adapter call with a finite per-call budget
        response_payload = {"choices": [{"message": {"content": '{"reply":"ok"}'}}]}

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self, *_: object) -> bytes:
                return json.dumps(response_payload).encode()

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=True), patch(
            "urllib.request.urlopen", return_value=FakeResponse()
        ) as urlopen:
            # When the adapter performs its HTTP request
            ModelRuntime()._deepseek_json(
                "deepseek-flash", "prompt", {"type": "object"}, max_tokens=100, timeout_seconds=3.25
            )
        # Then urllib's actual socket timeout is set to the run budget
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 3.25)

    def test_user_limits_codex_subprocess_timeout(self) -> None:
        # Given the command-line provider and a bounded review call
        import subprocess

        class CommandProcess:
            returncode = 0

            def __init__(self, command: list[str], **kwargs: object):
                self.output_path = command[command.index("--output-last-message") + 1]

            def communicate(self, prompt: str, *, timeout: float):
                if timeout < 5:
                    raise subprocess.TimeoutExpired("codex-test", timeout)
                from pathlib import Path

                Path(self.output_path).write_text('{"reply":"ok"}', encoding="utf-8")
                return "", ""

        with patch.dict(os.environ, {"CODEX_COMMAND": "/bin/echo"}, clear=True), patch(
            "subprocess.Popen", side_effect=CommandProcess
        ):
            # When the adapter starts Codex
            result, _usage = ModelRuntime()._codex_json(
                "default", "prompt", {"type": "object"}, timeout_seconds=5
            )
        # Then the real boundary's result is parsed under the finite call budget
        self.assertEqual(result, {"reply": "ok"})

    @unittest.skipUnless(os.name == "posix", "Process-group cleanup is POSIX-specific")
    def test_user_times_out_codex_then_the_child_process_group_is_stopped_and_prompt_is_not_in_error(self) -> None:
        import subprocess

        class CommandProcess:
            pid = 12345
            stopped = False

            def communicate(self, prompt=None, *, timeout=None):
                if prompt is not None:
                    raise subprocess.TimeoutExpired("codex", timeout)
                if not self.stopped:
                    raise AssertionError("native model process was left running")
                return "", ""

        process = CommandProcess()

        def stop_group(pid, _signal):
            if pid == process.pid:
                process.stopped = True

        # Given a model launcher whose native child outlives a request budget
        with patch("subprocess.Popen", return_value=process), patch("os.killpg", side_effect=stop_group):
            # When the adapter times out, Then the process boundary closes and returns an actionable error
            with self.assertRaisesRegex(RuntimeError, "已停止本次模型进程") as failed:
                ModelRuntime()._codex_json("default", "private textbook prompt", {"type": "object"}, timeout_seconds=1)
        self.assertNotIn("private textbook prompt", str(failed.exception))

    def test_user_calls_deepseek_then_gateway_keeps_provider_identity_and_usage(self) -> None:
        # Given the existing cloud provider at the external model boundary
        runtime = ModelRuntime()
        with patch.object(runtime, "_deepseek_json", return_value=({"reply": "提示"}, {"prompt_tokens": 17, "output_tokens": 6})):
            # When the shared gateway performs a structured call
            result, run = runtime.generate_json_as("deepseek", "deepseek-flash", "private prompt", {"type": "object"})
        # Then normalized metadata preserves the cloud identity and no content
        self.assertEqual(result, {"reply": "提示"})
        self.assertEqual(run["modelRequest"]["provider"], "deepseek")
        self.assertEqual(run["modelResult"]["actualModel"], "deepseek-flash")
        self.assertEqual(run["modelResult"]["usage"], {"promptTokens": 17, "outputTokens": 6})
        self.assertFalse(run["modelRequest"]["allowFallback"])
        self.assertNotIn("private prompt", str(run))

    def test_catalog_exposes_deepseek_only_when_key_is_configured(self) -> None:
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret"}, clear=True):
            providers = ModelRuntime().providers()

        deepseek = next(item for item in providers if item["id"] == "deepseek")
        self.assertTrue(deepseek["available"])
        self.assertIn("deepseek-flash", deepseek["models"])

    def test_calls_deepseek_and_normalizes_usage_metadata(self) -> None:
        response_payload = {
            "choices": [{
                "message": {"content": '{"reply":"先移项"}'},
            }],
            "usage": {"prompt_tokens": 17, "completion_tokens": 6},
        }

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self, *_: object) -> bytes:
                import json

                return json.dumps(response_payload).encode("utf-8")

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret"}, clear=True), \
                patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            result, usage = ModelRuntime()._deepseek_json(
                "deepseek-flash", "请给出一步提示", {"type": "object"}, max_tokens=80
            )

        self.assertEqual(result, {"reply": "先移项"})
        self.assertEqual(usage["prompt_tokens"], 17)
        self.assertEqual(usage["output_tokens"], 6)
        self.assertEqual(usage["providerAttempts"], 1)
        self.assertFalse(usage["schemaFallback"]["used"])
        request = urlopen.call_args.args[0]
        self.assertIn("api.deepseek.com/chat/completions", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        request_payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request_payload["thinking"], {"type": "disabled"})


    def test_provider_http_error_does_not_expose_response_body(self) -> None:
        import io
        import urllib.error

        error = urllib.error.HTTPError(
            "https://api.deepseek.com/chat/completions", 401, "unauthorized", {}, io.BytesIO(b"secret and student text")
        )
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "secret"}, clear=True), \
                patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as caught:
                ModelRuntime()._deepseek_json("deepseek-flash", "prompt", {"type": "object"}, 80)
        self.assertNotIn("student text", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    def test_missing_key_is_reported_before_request(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "DEEPSEEK_API_KEY"):
                ModelRuntime()._deepseek_json("deepseek-flash", "prompt", {"type": "object"}, 80)


class OpenAICompatibleProviderTests(unittest.TestCase):
    """A generic Chat Completions endpoint lets benchmarks compare models across vendors."""

    COMPATIBLE_ENV = {
        "OPENAI_COMPATIBLE_BASE_URL": "https://gateway.example/v1/",
        "OPENAI_COMPATIBLE_API_KEY": "secret",
        "OPENAI_COMPATIBLE_MODELS": "qwen-max, claude-sonnet ,",
    }

    def test_user_sees_compatible_endpoint_only_when_fully_configured(self) -> None:
        # Given only a base URL without key or model list
        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_BASE_URL": "https://gateway.example/v1"}, clear=True):
            partial = next(item for item in ModelRuntime().providers() if item["id"] == "openai_compatible")
        # When the endpoint, key and models are all configured
        with patch.dict(os.environ, self.COMPATIBLE_ENV, clear=True):
            complete = next(item for item in ModelRuntime().providers() if item["id"] == "openai_compatible")
        # Then the catalog hides the incomplete endpoint and lists the configured models
        self.assertFalse(partial["available"])
        self.assertTrue(complete["available"])
        self.assertEqual(complete["models"], ["qwen-max", "claude-sonnet"])

    def test_user_selects_compatible_model_and_it_persists_as_selection(self) -> None:
        # Given a configured compatible endpoint
        with patch.dict(os.environ, self.COMPATIBLE_ENV, clear=True), \
                patch("infrastructure.runtime.model_runtime.selection_store.save"):
            runtime = ModelRuntime()
            # When the user picks one of its models
            catalog = runtime.select("openai_compatible", "qwen-max")
        # Then it becomes the active selection
        self.assertEqual(catalog["selected"], {"provider": "openai_compatible", "model": "qwen-max"})

    def test_user_compares_compatible_model_and_request_targets_configured_endpoint(self) -> None:
        response_payload = {
            "choices": [{"message": {"content": '{"reply":"先通分"}'}}],
            "usage": {"prompt_tokens": 21, "completion_tokens": 4},
        }

        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, *_: object) -> None:
                return None

            def read(self, *_: object) -> bytes:
                return json.dumps(response_payload).encode("utf-8")

        # Given a configured gateway at the external model boundary
        with patch.dict(os.environ, self.COMPATIBLE_ENV, clear=True), \
                patch("urllib.request.urlopen", return_value=FakeResponse()) as urlopen:
            # When an evaluation calls a compatible model explicitly
            result, run = ModelRuntime().generate_json_as(
                "openai_compatible", "qwen-max", "private prompt", {"type": "object"}
            )
        # Then the answer, provider identity and token usage are preserved
        self.assertEqual(result, {"reply": "先通分"})
        self.assertEqual(run["modelResult"]["actualModel"], "qwen-max")
        self.assertEqual(run["modelResult"]["usage"], {"promptTokens": 21, "outputTokens": 4})
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://gateway.example/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        sent = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent["model"], "qwen-max")
        # DeepSeek-only parameters must not leak to other vendors.
        self.assertNotIn("thinking", sent)

    def test_user_sees_missing_configuration_before_any_request(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_COMPATIBLE_BASE_URL"):
                ModelRuntime()._openai_compatible_json("qwen-max", "prompt", {"type": "object"}, 80)
