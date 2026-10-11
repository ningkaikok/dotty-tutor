"""依赖自检（roadmap T2：环境依赖自检）的单元测试。

核心约束是"任何检查都不抛异常"：这里直接构造必然失败的探测函数，断言
``_safe_check``/``run_dependency_preflight`` 把异常收敛成失败记录而不是让
异常向上传播；再验证整体 ``ok`` 只看非 optional 项。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import dependency_preflight as dp


class SafeCheckTests(unittest.TestCase):
    def test_a_probe_that_always_raises_becomes_a_failed_record_not_a_crash(self) -> None:
        def _always_raises() -> tuple[bool, str]:
            raise RuntimeError("模拟必然失败的探测")

        record = dp._safe_check("boom", "故意失败探测", optional=True, probe=_always_raises)

        self.assertEqual(record["key"], "boom")
        self.assertEqual(record["label"], "故意失败探测")
        self.assertFalse(record["ok"])
        self.assertIn("RuntimeError", record["detail"])
        self.assertTrue(record["optional"])

    def test_a_probe_that_raises_an_import_error_is_also_caught(self) -> None:
        """缺失的可选依赖典型表现是 ImportError；这条专门覆盖这种失败形态。"""
        def _missing_dependency() -> tuple[bool, str]:
            raise ImportError("no module named fake_mineru_sdk")

        record = dp._safe_check("missing", "缺失依赖探测", optional=True, probe=_missing_dependency)

        self.assertFalse(record["ok"])
        self.assertIn("ImportError", record["detail"])

    def test_a_successful_probe_passes_through_unchanged(self) -> None:
        record = dp._safe_check("ok-item", "正常探测", optional=False, probe=lambda: (True, "一切正常"))

        self.assertTrue(record["ok"])
        self.assertEqual(record["detail"], "一切正常")
        self.assertFalse(record["optional"])


class RunDependencyPreflightTests(unittest.TestCase):
    _ALL_PROBES = (
        "_probe_pypdf", "_probe_lesson_media_renderer", "_probe_postgresql",
        "_probe_mineru", "_probe_ollama", "_probe_codex_cli", "_probe_azure_speech",
        "_probe_qwen_tts",
    )

    def test_never_raises_even_when_every_single_probe_breaks(self) -> None:
        """构造一个必然失败的场景：每个探测都抛异常，报告仍必须正常返回。"""
        patches = [
            patch.object(dp, name, side_effect=RuntimeError(f"{name} 挂了"))
            for name in self._ALL_PROBES
        ]
        for patcher in patches:
            patcher.start()
        try:
            report = dp.run_dependency_preflight()
        finally:
            for patcher in patches:
                patcher.stop()

        self.assertFalse(report["ok"])
        self.assertEqual(len(report["checks"]), 8)
        self.assertTrue(all(not item["ok"] for item in report["checks"]))
        self.assertTrue(all(item["detail"] for item in report["checks"]))

    def test_overall_ok_ignores_optional_checks(self) -> None:
        """媒体、OCR、模型与语音可选依赖缺失时，pypdf 和 PostgreSQL 仍可保持 ready。"""
        with (
            patch.object(dp, "_probe_pypdf", return_value=(True, "已安装")),
            patch.object(dp, "_probe_lesson_media_renderer", return_value=(False, "未安装")),
            patch.object(dp, "_probe_postgresql", return_value=(True, "已连接")),
            patch.object(dp, "_probe_mineru", return_value=(False, "未安装")),
            patch.object(dp, "_probe_ollama", return_value=(False, "不可达")),
            patch.object(dp, "_probe_codex_cli", return_value=(False, "未安装")),
            patch.object(dp, "_probe_azure_speech", return_value=(False, "未配置")),
            patch.object(dp, "_probe_qwen_tts", return_value=(False, "未启动")),
        ):
            report = dp.run_dependency_preflight()

        self.assertTrue(report["ok"])
        required_keys = {item["key"] for item in report["checks"] if not item["optional"]}
        self.assertEqual(required_keys, {"pypdf", "postgresql"})
        optional_keys = {item["key"] for item in report["checks"] if item["optional"]}
        self.assertEqual(
            optional_keys,
            {"lesson_media_renderer", "mineru", "ollama", "codex_cli", "azure_speech", "qwen_tts"},
        )

    def test_renderer_available_is_reported_without_becoming_a_global_readiness_requirement(self) -> None:
        """A working optional renderer is visible in the report and preserves overall readiness."""
        with (
            patch.object(dp, "_probe_pypdf", return_value=(True, "已安装")),
            patch.object(dp, "_probe_lesson_media_renderer", return_value=(True, "CairoSVG、FFmpeg 和字体可用")),
            patch.object(dp, "_probe_postgresql", return_value=(True, "已连接")),
            patch.object(dp, "_probe_mineru", return_value=(False, "未安装")),
            patch.object(dp, "_probe_ollama", return_value=(False, "不可达")),
            patch.object(dp, "_probe_codex_cli", return_value=(False, "未安装")),
            patch.object(dp, "_probe_azure_speech", return_value=(False, "未配置")),
            patch.object(dp, "_probe_qwen_tts", return_value=(False, "未启动")),
        ):
            report = dp.run_dependency_preflight()

        renderer = next(item for item in report["checks"] if item["key"] == "lesson_media_renderer")
        self.assertTrue(report["ok"])
        self.assertTrue(renderer["ok"])
        self.assertTrue(renderer["optional"])
        self.assertEqual(renderer["detail"], "CairoSVG、FFmpeg 和字体可用")

    def test_overall_ok_is_false_when_a_required_check_fails(self) -> None:
        with (
            patch.object(dp, "_probe_pypdf", return_value=(True, "已安装")),
            patch.object(dp, "_probe_lesson_media_renderer", return_value=(False, "未安装")),
            patch.object(dp, "_probe_postgresql", return_value=(False, "连接失败")),
            patch.object(dp, "_probe_mineru", return_value=(False, "未安装")),
            patch.object(dp, "_probe_ollama", return_value=(False, "不可达")),
            patch.object(dp, "_probe_codex_cli", return_value=(False, "未安装")),
            patch.object(dp, "_probe_azure_speech", return_value=(False, "未配置")),
            patch.object(dp, "_probe_qwen_tts", return_value=(False, "未启动")),
        ):
            report = dp.run_dependency_preflight()

        self.assertFalse(report["ok"])


if __name__ == "__main__":
    unittest.main()
