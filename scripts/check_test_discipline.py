"""检查后端测试是否遵守"外部边界才允许 mock、时间用注入的 now= 而不是 sleep"的纪律。

这不是禁止一切 mock：模型/OCR/TTS 这类真正的外部系统允许注入 fake 并校验契约。
下面两份清单是当前经过审阅、确认例外成立的文件；新增文件想要用 mock 或
time.sleep()，必须先证明理由和这里的先例一样站得住脚，再显式把文件名加进来——
这样每一次新增都会在 PR diff 里被看到，而不是悄悄溜进测试套件。

用法：
    python scripts/check_test_discipline.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "apps" / "api" / "tests"

# Windows 本地终端默认用 cp1252 打开 stdout，打印中文会直接抛
# UnicodeEncodeError；显式切到 UTF-8，Linux/CI 上这个调用是安全的空操作。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# 这些文件里的 unittest.mock 只打在真正的外部边界上——模型 Runtime、OCR Runtime、
# 审校 Runtime 等——不是同进程业务逻辑（Store、领域算法、状态机）的内部实现细节。
ALLOWED_MOCK_FILES = {
    "test_app.py",
    "test_capabilities.py",
    "test_debug_entry.py",
    "test_evaluation_judge_cli.py",
    "test_model_call_metrics.py",
    "test_model_runtime.py",
    "test_ocr_runtime.py",
    "test_question_ir.py",
    "test_question_processing.py",
    "test_review_model_selection.py",
    "test_run_audit.py",
    "test_selection_store.py",
    "test_storage_config.py",
    "test_textbook_jobs.py",
    "test_textbook_ocr_pipeline.py",
    "test_textbook_processing.py",
}

# 这些文件里的 time.sleep() 不是"业务时间逻辑靠真实等待"，而是真实多线程/多连接
# 竞争场景里，让另一个真实线程/连接有机会真正尝试并失败——这类真并发测试没有
# 可注入的时钟可以替代，必须真的经过一段墙钟时间。业务时间逻辑（幂等窗口、
# 租约过期、复习排期等）仍然必须用显式 now= 参数，参考 test_background_jobs.py
# 里那些不依赖真实等待的用例。
ALLOWED_SLEEP_FILES = {
    "test_background_jobs.py",
    "test_postgres_integration.py",
}

MOCK_IMPORT_PATTERN = re.compile(r"^\s*(from unittest\.mock import|import unittest\.mock\b)", re.MULTILINE)
SLEEP_PATTERN = re.compile(r"\btime\.sleep\s*\(")


def check_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []
    relative = path.relative_to(REPO_ROOT)

    if MOCK_IMPORT_PATTERN.search(text) and path.name not in ALLOWED_MOCK_FILES:
        problems.append(
            f"{relative}: 引入了 unittest.mock，但不在 scripts/check_test_discipline.py 的 "
            "ALLOWED_MOCK_FILES 清单里。如果这里 mock 的确实是外部边界（模型/OCR/TTS/审校 "
            "Runtime），请把文件名加入该清单并在 PR 描述里说明理由；如果 mock 的是同进程业务 "
            "逻辑（Store、领域算法、状态机），请改成调用真实实现，或者用 PostgresTestCase 接真实数据库。"
        )

    for match in SLEEP_PATTERN.finditer(text):
        if path.name in ALLOWED_SLEEP_FILES:
            continue
        line_number = text.count("\n", 0, match.start()) + 1
        problems.append(
            f"{relative}:{line_number}: 测试里出现 time.sleep()。时间相关的行为请给被测代码显式"
            "传入 now= 参数（参考 test_background_jobs.py 里不依赖真实等待的用例），不要靠真实"
            "等待再断言；只有测试真实线程/连接竞争、没有可注入时钟的场景才允许 sleep，且需要显式"
            "加入 ALLOWED_SLEEP_FILES 并说明理由。"
        )

    return problems


def main() -> int:
    if not TESTS_DIR.is_dir():
        print(f"跳过：{TESTS_DIR} 不存在", file=sys.stderr)
        return 0

    test_files = sorted(TESTS_DIR.glob("test_*.py"))
    all_problems: list[str] = []
    for path in test_files:
        all_problems.extend(check_file(path))

    if all_problems:
        print("测试纪律检查未通过：\n")
        for problem in all_problems:
            print(f"  - {problem}")
        print(f"\n共 {len(all_problems)} 处问题。规则背景见 apps/api/tests/README.md。")
        return 1

    print(f"测试纪律检查通过（{len(test_files)} 个测试文件，0 处未登记的 mock/sleep）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
