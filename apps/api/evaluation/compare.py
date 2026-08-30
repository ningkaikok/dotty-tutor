"""比较确定性重放和 Judge 报告。

确定性报告比较结构行为并对回归返回失败；Judge 报告只比较运行质量和共同成功
样本的配对评分。评分变化是观察数据，不会在没有人工阈值时自动阻断流程。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _entry_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = report.get("entries") or report.get("results") or []
    return {
        entry["id"]: entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }


def _number(value: Any) -> float | int | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _metric_delta(old: dict[str, Any], new: dict[str, Any], key: str) -> dict[str, Any]:
    old_value = _number(old.get(key))
    new_value = _number(new.get(key))
    return {
        "old": old_value,
        "new": new_value,
        "delta": round(float(new_value) - float(old_value), 4)
        if old_value is not None and new_value is not None else None,
    }


def _judge_comparison(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    if old.get("reportKind") != "judge" or new.get("reportKind") != "judge":
        reasons.append("reportKind differs or is not judge")
    if old.get("corpusVersion") != new.get("corpusVersion"):
        reasons.append("corpusVersion differs")
    if old.get("sampleSetHash") != new.get("sampleSetHash"):
        reasons.append("sampleSetHash differs")
    old_judge_value = old.get("judge")
    new_judge_value = new.get("judge")
    old_judge = cast(dict[str, Any], old_judge_value) if isinstance(old_judge_value, dict) else {}
    new_judge = cast(dict[str, Any], new_judge_value) if isinstance(new_judge_value, dict) else {}
    for key in ("provider", "model", "promptVersion"):
        if old_judge.get(key) != new_judge.get(key):
            reasons.append(f"judge.{key} differs")

    old_metrics = old.get("judgeMetrics")
    new_metrics = new.get("judgeMetrics")
    old_metrics = old_metrics if isinstance(old_metrics, dict) else {}
    new_metrics = new_metrics if isinstance(new_metrics, dict) else {}
    metric_keys = (
        "successRate", "failureCount", "p50DurationMs", "p95DurationMs",
        "logicalCalls", "providerAttempts", "schemaFallbackCount",
    )
    metrics = {key: _metric_delta(old_metrics, new_metrics, key) for key in metric_keys}
    unavailable_metrics = [
        key for key, value in metrics.items()
        if value["old"] is None or value["new"] is None
    ]

    old_entries = _entry_map(old)
    new_entries = _entry_map(new)
    common_ids = sorted(set(old_entries) & set(new_entries))
    paired: list[str] = []
    old_scores: dict[str, list[float]] = {}
    new_scores: dict[str, list[float]] = {}
    for entry_id in common_ids:
        old_entry, new_entry = old_entries[entry_id], new_entries[entry_id]
        old_succeeded = old_entry.get("judgeSucceeded", old_entry.get("passed")) is True
        new_succeeded = new_entry.get("judgeSucceeded", new_entry.get("passed")) is True
        old_score = old_entry.get("scores")
        new_score = new_entry.get("scores")
        if old_score is None and isinstance(old_entry.get("outcome"), dict):
            old_score = old_entry["outcome"].get("scores")
        if new_score is None and isinstance(new_entry.get("outcome"), dict):
            new_score = new_entry["outcome"].get("scores")
        if old_succeeded and new_succeeded and isinstance(old_score, dict) and isinstance(new_score, dict):
            if set(RUBRIC_DIMENSIONS) <= set(old_score) and set(RUBRIC_DIMENSIONS) <= set(new_score):
                paired.append(entry_id)
                for dimension in RUBRIC_DIMENSIONS:
                    old_scores.setdefault(dimension, []).append(float(old_score[dimension]))
                    new_scores.setdefault(dimension, []).append(float(new_score[dimension]))

    score_comparison: dict[str, Any] | None = None
    if not reasons:
        old_average = {
            key: round(sum(values) / len(values), 2)
            for key, values in old_scores.items() if values
        }
        new_average = {
            key: round(sum(values) / len(values), 2)
            for key, values in new_scores.items() if values
        }
        score_comparison = {
            "pairedSampleCount": len(paired),
            "sampleIds": paired,
            "old": old_average,
            "new": new_average,
            "delta": {
                key: round(new_average[key] - old_average[key], 2)
                for key in sorted(set(old_average) & set(new_average))
            },
        }
    else:
        reasons.append("Judge 配置或语料不一致，评分不可直接横比")

    return {
        "comparable": not reasons,
        "incomparableReasons": reasons,
        "commonSampleCount": len(common_ids),
        "added": sorted(set(new_entries) - set(old_entries)),
        "removed": sorted(set(old_entries) - set(new_entries)),
        "metrics": metrics,
        "unavailableMetrics": unavailable_metrics,
        "scoreComparison": score_comparison,
    }


RUBRIC_DIMENSIONS = ("clarity", "targeting", "factual")


def _deterministic_comparison(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    old_entries = _entry_map(old)
    new_entries = _entry_map(new)
    added = sorted(set(new_entries) - set(old_entries))
    removed = sorted(set(old_entries) - set(new_entries))
    regressions: list[str] = []
    fixes: list[str] = []
    bug_signature_changed: list[str] = []
    for entry_id in sorted(set(old_entries) & set(new_entries)):
        old_entry, new_entry = old_entries[entry_id], new_entries[entry_id]
        old_passed, new_passed = old_entry.get("passed") is True, new_entry.get("passed") is True
        if old_passed and not new_passed and not (
            old_entry.get("documenting_bug") or new_entry.get("documenting_bug")
        ):
            regressions.append(entry_id)
        elif not old_passed and new_passed and not old_entry.get("documenting_bug"):
            fixes.append(entry_id)
        if (
            old_entry.get("documenting_bug") or new_entry.get("documenting_bug")
        ) and old_passed != new_passed:
            bug_signature_changed.append(entry_id)
    return {
        "reportKind": "deterministic",
        "added": added,
        "removed": removed,
        "regressions": regressions,
        "fixes": fixes,
        "bugSignatureChanged": bug_signature_changed,
        "judge": None,
    }


def compare_reports(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """按报告类型分派比较策略。"""
    if old.get("reportKind") == "judge" or new.get("reportKind") == "judge":
        judge = _judge_comparison(old, new)
        return {
            "reportKind": "judge",
            "added": judge["added"],
            "removed": judge["removed"],
            "regressions": [],
            "fixes": [],
            "bugSignatureChanged": [],
            "judge": judge,
            # 旧调用方可通过这个别名读取整块 Judge 比较结果；不再使用 modelMetrics。
            "judgeMetrics": judge["metrics"],
        }
    return _deterministic_comparison(old, new)


def format_summary(comparison: dict[str, Any]) -> str:
    lines = ["# 评测报告对比", ""]
    lines.append(f"- 报告类型：{comparison.get('reportKind', 'deterministic')}")
    for key, title in (("added", "新增"), ("removed", "移除"), ("regressions", "回归"), ("fixes", "修复"), ("bugSignatureChanged", "已知缺陷特征变化")):
        items = comparison.get(key, [])
        lines.append(f"- {title}：{len(items)}")
        for item in items:
            lines.append(f"    - {item}")
    judge = comparison.get("judge")
    if judge:
        lines.extend(["", "## Judge 运行维度"])
        if judge["incomparableReasons"]:
            lines.append("- 评分不可直接比较：" + "；".join(judge["incomparableReasons"]))
        else:
            score = judge.get("scoreComparison")
            if score:
                lines.append(f"- 共同成功样本：{score['pairedSampleCount']}")
                if score["delta"]:
                    lines.append("- 配对评分变化：" + ", ".join(
                        f"{key} {value:+.2f}" for key, value in score["delta"].items()
                    ))
        for key, metric in judge["metrics"].items():
            if metric["delta"] is not None:
                lines.append(f"- {key}：{metric['old']} → {metric['new']}（{metric['delta']:+g}）")
            else:
                lines.append(f"- {key}：不可用（缺少一侧指标）")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare two evaluation reports.")
    parser.add_argument("new_report", type=Path)
    parser.add_argument("base_report", type=Path)
    args = parser.parse_args()
    comparison = compare_reports(load_report(args.base_report), load_report(args.new_report))
    print(format_summary(comparison))
    if comparison.get("reportKind") == "judge":
        return 0
    if comparison["regressions"] or comparison["bugSignatureChanged"]:
        print("\nRESULT: REGRESSION — 请先定位回归再合并。")
        return 1
    print("\nRESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
