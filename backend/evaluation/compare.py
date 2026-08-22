"""两次重放报告的对比工具——"修复前后比较"的落地形态。

用法::

    cd backend
    ../.venv/bin/python -m evaluation.replay                 # 当前行为 → 报告 A
    # ...修改切分规则/提示词/Schema...
    ../.venv/bin/python -m evaluation.replay                 # 修改后 → 报告 B
    ../.venv/bin/python -m evaluation.compare \
        output/eval-reports/replay-report.json <修改前备份>.json

输出四类变化并决定退出码：

- ``regressions``：原本通过的条目失败（最危险，退出码 1）
- ``fixes``：预期外失败被修复（应同步更新语料或登记簿状态）
- ``bugSignatureChanged``：特征化条目行为翻转（无论方向都需要人工确认：
  pass→fail 可能是修好了，fail→pass 说明退回了缺陷行为）
- ``added`` / ``removed``：语料本身发生了增删，对比时要知道基线变了

只对比确定性维度（结构结果）；评分/耗时/调用次数等模型维度在评测集接入
模型调用后扩展到本模块，避免现在假装有这些数据。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _entry_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {entry["id"]: entry for entry in report["entries"]}


def compare_reports(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_entries = _entry_map(old)
    new_entries = _entry_map(new)
    added = sorted(set(new_entries) - set(old_entries))
    removed = sorted(set(old_entries) - set(new_entries))

    regressions: list[str] = []
    fixes: list[str] = []
    bug_signature_changed: list[str] = []
    for entry_id in sorted(set(old_entries) & set(new_entries)):
        was, now = old_entries[entry_id]["passed"], new_entries[entry_id]["passed"]
        documenting = bool(new_entries[entry_id].get("documenting_bug"))
        if documenting:
            if was and not now:
                bug_signature_changed.append(entry_id)
            elif not was and now:
                bug_signature_changed.append(entry_id)
            continue
        if was and not now:
            regressions.append(entry_id)
        elif not was and now:
            fixes.append(entry_id)

    return {
        "oldCorpusVersion": old.get("corpusVersion"),
        "newCorpusVersion": new.get("corpusVersion"),
        "corpusChanged": old.get("corpusVersion") != new.get("corpusVersion"),
        "added": added,
        "removed": removed,
        "regressions": regressions,
        "fixes": fixes,
        "bugSignatureChanged": bug_signature_changed,
    }


def format_summary(comparison: dict[str, Any]) -> str:
    lines = ["# 重放对比", ""]
    if comparison["corpusChanged"]:
        lines.append(
            f"- 注意：语料版本变化 {comparison['oldCorpusVersion']} → "
            f"{comparison['newCorpusVersion']}，条目集合可能不同"
        )
    for key, title in (
        ("regressions", "回归（原通过 → 失败）"),
        ("fixes", "修复（原失败 → 通过）"),
        ("bugSignatureChanged", "已知缺陷行为变化"),
        ("added", "新增条目"),
        ("removed", "移除条目"),
    ):
        items = comparison[key]
        lines.append(f"- {title}: {len(items)}")
        for item in items:
            lines.append(f"    - {item}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two replay reports.")
    parser.add_argument("new_report", type=Path)
    parser.add_argument("base_report", type=Path)
    args = parser.parse_args()
    comparison = compare_reports(load_report(args.base_report), load_report(args.new_report))
    print(format_summary(comparison))
    has_regression = bool(comparison["regressions"]) or bool(
        comparison["bugSignatureChanged"]
    )
    if has_regression:
        print("\nRESULT: REGRESSION — 请先定位回归再合并。")
        return 1
    print("\nRESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
