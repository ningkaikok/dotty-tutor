"""模型调用边界指标：存储聚合与运行时挂钩的验收测试（roadmap T2）。

三条不变量：
1. 指标只追加写入，聚合是纯只读查询；
2. 成功/失败都会记录，失败行带可解释的 error_type；
3. 指标记录的任何异常绝不影响模型调用主流程。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from application.services.learning_funnel import (
    build_funnel_snapshot,  # noqa: F401  确认应用装配可导入
)
from infrastructure.runtime.contracts import PromptParts
from infrastructure.runtime.model_runtime import ModelRuntime, ModelSelection
from persistence.app_store import AppStore
from persistence.metrics_store import MetricsStore
from routers.runtime_routes import build_runtime_router
from tests.postgres_test_support import PostgresTestCase


class FallbackMetricsTests(PostgresTestCase):
    """回退信息必须真的落库。

    此前 ``_record_metric`` 会把 ``provider_attempts`` 和 ``schema_fallback``
    放进 entry，但表里没有对应列、``_ALLOWED_KEYS`` 也不含它们，而 ``record()``
    的策略是"白名单外字段直接忽略"——两个值被静默丢弃。调用方算了、传了，
    以为记下了，其实没有。
    """

    def setUp(self) -> None:
        super().setUp()
        store = AppStore(self.database_url, self.data_root)
        self.addCleanup(store.close)
        self.metrics = MetricsStore(engine=store.engine)

    def _record(self, **overrides: object) -> None:
        entry = {
            "runtime": "generation",
            "task": "lesson-generation",
            "provider": "ollama",
            "model": "qwen2.5:7b",
            "prompt_chars": 100,
            "duration_ms": 100.0,
            "status": "succeeded",
        }
        entry.update(overrides)
        self.metrics.record(entry)

    def test_provider_attempts_and_schema_fallback_are_persisted(self) -> None:
        self._record(provider_attempts=3, schema_fallback=True)
        item = self.metrics.aggregate()[0]
        self.assertEqual(item["providerAttempts"], 3)
        self.assertEqual(item["schemaFallbacks"], 1)

    def test_defaults_do_not_overstate_retries_or_fallbacks(self) -> None:
        """不传时按"一次逻辑调用 = 一次请求、未降级"计，避免夸大问题。"""
        self._record()
        item = self.metrics.aggregate()[0]
        self.assertEqual(item["providerAttempts"], 1)
        self.assertEqual(item["schemaFallbacks"], 0)

    def test_report_separates_logical_calls_from_provider_attempts(self) -> None:
        """重试会让 Provider 请求数大于逻辑调用数，二者不能混为一谈。"""
        self._record(provider_attempts=1)
        self._record(provider_attempts=4, schema_fallback=True)
        summary = self.metrics.aggregate_report()["summary"]
        self.assertEqual(summary["logicalCalls"], 2)
        self.assertEqual(summary["providerAttempts"], 5)
        self.assertEqual(summary["retryAmplification"], 2.5)
        self.assertEqual(summary["schemaFallbacks"], 1)
        self.assertEqual(summary["schemaFallbackRate"], 0.5)

    def test_runtime_hook_actually_lands_both_fields(self) -> None:
        """端到端：从 ModelRuntime._record_metric 走一遍，而不是只测 store。"""
        runtime = ModelRuntime(metrics_store=self.metrics)
        runtime.selection = ModelSelection(provider="ollama", model="qwen2.5:7b")
        runtime._record_metric(
            task="tutoring",
            provider="ollama",
            model="qwen2.5:7b",
            started=0.0,
            status="succeeded",
            prompt_chars=10,
            max_tokens=100,
            provider_attempts=2,
            schema_fallback=True,
        )
        item = self.metrics.aggregate()[0]
        self.assertEqual(item["providerAttempts"], 2)
        self.assertEqual(item["schemaFallbacks"], 1)


class MetricsStoreRoundtripTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        store = AppStore(self.database_url, self.data_root)
        self.addCleanup(store.close)
        self.metrics = MetricsStore(engine=store.engine)

    def test_aggregate_groups_and_counts_failures(self) -> None:
        base = {
            "runtime": "generation",
            "task": "lesson-generation",
            "provider": "ollama",
            "model": "qwen2.5:7b",
            "prompt_chars": 100,
        }
        self.metrics.record({**base, "duration_ms": 120.5, "status": "succeeded",
                             "output_tokens": 50, "prompt_tokens": 20})
        self.metrics.record({**base, "duration_ms": 80.0, "status": "failed",
                             "error_type": "RuntimeExecutionError"})
        rows = self.metrics.aggregate()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["calls"], 2)
        # Python round(100.25, 1) == 100.2（银行家舍入），断言固化该行为。
        self.assertEqual(row["avgDurationMs"], 100.2)
        self.assertEqual(row["failures"], 1)
        self.assertEqual(row["totalOutputTokens"], 50)

    def test_unknown_fields_are_dropped_on_record(self) -> None:
        self.metrics.record({
            "runtime": "review", "task": "review", "provider": "codex",
            "model": "gpt-5.6-sol", "duration_ms": 5.0, "status": "succeeded",
            "correctAnswers": ["secret"],
        })
        import json

        from sqlalchemy import select

        from persistence.metrics_store import model_call_metrics
        with self.metrics.engine.connect() as connection:
            row = connection.execute(select(model_call_metrics)).mappings().one()
        serialized = json.dumps(dict(row), default=str)
        self.assertNotIn("secret", serialized)

    def test_aggregate_report_exposes_weighted_summary_and_token_coverage(self) -> None:
        base = {
            "runtime": "generation", "task": "lesson-generation", "provider": "ollama",
            "model": "qwen2.5:7b", "prompt_chars": 100,
        }
        self.metrics.record({**base, "duration_ms": 120, "status": "succeeded",
                             "prompt_tokens": 20, "output_tokens": 50})
        self.metrics.record({**base, "duration_ms": 80, "status": "failed"})
        report = self.metrics.aggregate_report(days=1)
        summary = report["summary"]
        self.assertEqual(summary["logicalCalls"], 2)
        self.assertEqual(summary["failures"], 1)
        self.assertEqual(summary["failureRate"], 0.5)
        self.assertEqual(summary["avgDurationMs"], 100.0)
        self.assertEqual(summary["totalPromptTokens"], 20)
        self.assertEqual(summary["totalOutputTokens"], 50)
        self.assertEqual(summary["tokenMeasuredCalls"], 1)
        self.assertEqual(summary["tokenCoverageRate"], 0.5)
        self.assertEqual(len(report["items"]), 1)

    def test_empty_report_does_not_present_zero_as_observed_metric(self) -> None:
        summary = self.metrics.aggregate_report(days=1)["summary"]
        self.assertEqual(summary["logicalCalls"], 0)
        self.assertEqual(summary["failures"], 0)
        self.assertIsNone(summary["failureRate"])
        self.assertIsNone(summary["avgDurationMs"])
        self.assertIsNone(summary["totalPromptTokens"])
        self.assertIsNone(summary["totalOutputTokens"])
        self.assertIsNone(summary["tokenCoverageRate"])

    def test_learning_cost_report_has_explicit_scopes_and_clamped_window(self) -> None:
        router = build_runtime_router(
            store=SimpleNamespace(engine=self.metrics.engine),
            question_payload=lambda: {},
            tutor_runtime=SimpleNamespace(catalog=lambda: {}),
            metrics_store=self.metrics,
        )
        endpoint = next(route.endpoint for route in router.routes if route.path == "/api/reports/learning-cost")
        report = endpoint(learnerId="learner-1", days=999)
        self.assertEqual(report["learnerId"], "learner-1")
        self.assertEqual(report["days"], 90)
        self.assertEqual(report["scope"], {
            "learning": "learner_cumulative",
            "modelCalls": "global_rolling_window",
            "costUnit": "proxy_only",
        })
        self.assertIn("不提供学生级成本归因", " ".join(report["limitations"]))


class RuntimeHookTests(PostgresTestCase):
    def setUp(self) -> None:
        super().setUp()
        store = AppStore(self.database_url, self.data_root)
        self.addCleanup(store.close)
        self.metrics = MetricsStore(engine=store.engine)
        self.runtime = ModelRuntime(metrics_store=self.metrics)
        self.runtime.selection = ModelSelection("ollama", "qwen2.5:7b")

    def test_success_records_metric_with_token_usage(self) -> None:
        with patch.object(
            self.runtime, "_ollama_json",
            return_value=({"ok": True}, {"prompt_tokens": 10, "output_tokens": 7}),
        ):
            payload, _run = self.runtime.generate_json("提示词", {"type": "object"})
        assert payload is not None
        self.assertEqual(payload, {"ok": True})
        rows = self.metrics.aggregate()
        self.assertEqual(rows[0]["calls"], 1)
        self.assertEqual(rows[0]["failures"], 0)
        self.assertEqual(rows[0]["totalOutputTokens"], 7)

    def test_failure_records_error_type_without_breaking_flow(self) -> None:
        with patch.object(
            self.runtime, "_ollama_json",
            side_effect=RuntimeError("连接失败"),
        ):
            with self.assertRaises(Exception):
                self.runtime.generate_json("提示词", {"type": "object"})
        rows = self.metrics.aggregate()
        self.assertEqual(rows[0]["calls"], 1)
        self.assertEqual(rows[0]["failures"], 1)

class PromptPrefixSplitMetricsTests(PostgresTestCase):
    """稳定段占比是判断 Prefix Cache 值不值得做的唯一依据，必须可测且不被污染。"""

    def setUp(self) -> None:
        super().setUp()
        store = AppStore(self.database_url, self.data_root)
        self.addCleanup(store.close)
        self.metrics = MetricsStore(engine=store.engine)
        self.runtime = ModelRuntime(metrics_store=self.metrics)
        self.runtime.selection = ModelSelection("ollama", "qwen2.5:7b")

    def _call(self, prompt) -> None:
        with patch.object(
            self.runtime, "_ollama_json",
            return_value=({"ok": True}, {"prompt_tokens": 10, "output_tokens": 7}),
        ):
            self.runtime.generate_json(prompt, {"type": "object"})

    def test_prompt_parts_are_recorded_and_summed_into_a_share(self) -> None:
        self._call(PromptParts(stable="s" * 90, dynamic="d" * 10))
        report = self.metrics.aggregate_report()["summary"]
        self.assertEqual(report["stablePromptChars"], 90)
        self.assertEqual(report["dynamicPromptChars"], 10)
        self.assertEqual(report["stablePromptShare"], 0.9)
        self.assertEqual(report["prefixSplitCalls"], 1)
        self.assertEqual(report["prefixSplitCoverageRate"], 1.0)

    def test_plain_string_prompt_records_null_not_zero(self) -> None:
        """未切分的调用写 0 会把它算进分母，压低占比——必须是 None。"""
        self._call("没有做切分的提示词")
        report = self.metrics.aggregate_report()["summary"]
        self.assertIsNone(report["stablePromptChars"])
        self.assertIsNone(report["stablePromptShare"])
        self.assertEqual(report["prefixSplitCalls"], 0)
        self.assertEqual(report["prefixSplitCoverageRate"], 0.0)

    def test_unsplit_calls_do_not_dilute_the_share(self) -> None:
        self._call(PromptParts(stable="s" * 80, dynamic="d" * 20))
        self._call("没有做切分的提示词")
        report = self.metrics.aggregate_report()["summary"]
        # 占比只由切分过的那一次决定；覆盖率单独暴露"只测到一半"这件事。
        self.assertEqual(report["stablePromptShare"], 0.8)
        self.assertEqual(report["logicalCalls"], 2)
        self.assertEqual(report["prefixSplitCalls"], 1)
        self.assertEqual(report["prefixSplitCoverageRate"], 0.5)

    def test_failed_call_still_records_the_split(self) -> None:
        """失败调用同样耗掉了前缀 token，漏记会高估缓存收益。"""
        with patch.object(self.runtime, "_ollama_json", side_effect=RuntimeError("连接失败")):
            with self.assertRaises(Exception):
                self.runtime.generate_json(
                    PromptParts(stable="s" * 60, dynamic="d" * 40), {"type": "object"}
                )
        report = self.metrics.aggregate_report()["summary"]
        self.assertEqual(report["failures"], 1)
        self.assertEqual(report["stablePromptShare"], 0.6)

    def test_aggregate_items_expose_the_share_per_model(self) -> None:
        self._call(PromptParts(stable="s" * 75, dynamic="d" * 25))
        row = self.metrics.aggregate()[0]
        self.assertEqual(row["stablePromptShare"], 0.75)
        self.assertEqual(row["prefixSplitCalls"], 1)


class RuntimeHookConstructionTests(unittest.TestCase):
    def test_constructor_does_not_touch_engine(self) -> None:
        """惰性建表约定：组合根在无数据库环境 import 时也必须安全。"""

        class ExplodingEngine:
            def connect(self):
                raise AssertionError("constructor must not connect")

            def begin(self):
                raise AssertionError("constructor must not connect")

        MetricsStore(engine=ExplodingEngine())  # 不应抛出

    def test_missing_store_is_noop(self) -> None:
        runtime = ModelRuntime()
        runtime.selection = ModelSelection("ollama", "qwen2.5:7b")
        with patch.object(runtime, "_ollama_json", return_value=({"ok": True}, None)):
            payload, _run = runtime.generate_json("提示词", {"type": "object"})
        self.assertEqual(payload, {"ok": True})


if __name__ == "__main__":
    unittest.main()
