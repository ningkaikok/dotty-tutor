"""模型调用边界指标：存储聚合与运行时挂钩的验收测试（roadmap T2）。

三条不变量：
1. 指标只追加写入，聚合是纯只读查询；
2. 成功/失败都会记录，失败行带可解释的 error_type；
3. 指标记录的任何异常绝不影响模型调用主流程。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from application.services.learning_funnel import (
    build_funnel_snapshot,  # noqa: F401  确认应用装配可导入
)
from infrastructure.runtime.model_runtime import ModelRuntime, ModelSelection
from persistence.app_store import AppStore
from persistence.metrics_store import MetricsStore


class MetricsStoreRoundtripTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        store = AppStore(f"sqlite+pysqlite:///{root / 'metrics.sqlite3'}", root)
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


class RuntimeHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        store = AppStore(f"sqlite+pysqlite:///{root / 'hook.sqlite3'}", root)
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

    def test_missing_store_is_noop(self) -> None:
        runtime = ModelRuntime()
        runtime.selection = ModelSelection("ollama", "qwen2.5:7b")
        with patch.object(runtime, "_ollama_json", return_value=({"ok": True}, None)):
            payload, _run = runtime.generate_json("提示词", {"type": "object"})
        self.assertEqual(payload, {"ok": True})


if __name__ == "__main__":
    unittest.main()
