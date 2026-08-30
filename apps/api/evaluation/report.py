"""开发期只读状态报告：把分散的评测产物聚合成一页可读摘要（roadmap T2 只读工具）。

聚合三类只读产物，全部不触碰学生数据与生产状态：
1. Badcase 登记簿（``evaluation/badcases.json``）：按状态计数 + open 条目标题；
2. 最近一次确定性重放报告（``output/eval-reports/replay-report.json``）：通过/失败计数；
3. LLM-as-Judge 报告（``output/eval-reports/judge/*.json``）：最近一次评审的样本与均分。

用法::

    cd apps/api
    ../../.venv/bin/python -m evaluation.report
    ../../.venv/bin/python -m evaluation.report --output-dir <自定义重放输出目录>

只读约定：本 CLI 不写任何文件、不修改登记簿、不触发模型调用。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.badcase import load_badcases, validate_registry

EVALUATION_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "output" / "eval-reports"


def collect_badcase_status(evaluation_dir: Path) -> dict[str, Any]:
    path = evaluation_dir / "badcases.json"
    if not path.is_file():
        return {"available": False}
    data = load_badcases(path)
    problems = validate_registry(data)
    by_status: dict[str, list[str]] = {}
    for record in data["badcases"]:
        by_status.setdefault(record["status"], []).append(
            f"{record['id']}（{record['title']}）"
        )
    return {
        "available": True,
        "total": len(data["badcases"]),
        "byStatus": {status: items for status, items in sorted(by_status.items())},
        "registryProblems": problems,
    }


def collect_replay_status(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "replay-report.json"
    if not path.is_file():
        return {"available": False, "hint": "先运行 python -m evaluation.replay 生成报告"}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        "available": True,
        "generatedAt": data.get("generatedAt"),
        "totals": data.get("totals", {}),
    }


def collect_judge_status(output_dir: Path) -> dict[str, Any]:
    judge_dir = output_dir / "judge"
    if not judge_dir.is_dir():
        return {"available": False, "hint": "先运行 python -m evaluation.judge_cli 生成报告"}
    reports = sorted(judge_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not reports:
        return {"available": False}
    latest = json.loads(reports[0].read_text(encoding="utf-8"))
    metrics = latest.get("judgeMetrics")
    if isinstance(metrics, dict) and isinstance(metrics.get("averageScores"), dict):
        averages = metrics["averageScores"]
    else:
        scored = [
            item["outcome"]["scores"]
            for item in latest.get("results", [])
            if item.get("passed") and isinstance(item.get("outcome"), dict)
            and isinstance(item["outcome"].get("scores"), dict)
        ]
        averages = {
            dimension: round(sum(s[dimension] for s in scored) / len(scored), 2)
            for dimension in ("clarity", "targeting", "factual")
            if scored
        } if scored else {}
    judge = latest.get("judge") if isinstance(latest.get("judge"), dict) else {}
    return {
        "available": True,
        "latestReport": reports[0].name,
        "provider": judge.get("provider", latest.get("provider")),
        "model": judge.get("model", latest.get("model")),
        "samples": latest.get("totals", {}),
        "averageScores": averages,
        "judgeMetrics": metrics if isinstance(metrics, dict) else None,
    }


def collect_status(evaluation_dir: Path | str, output_dir: Path | str) -> dict[str, Any]:
    evaluation_dir = Path(evaluation_dir)
    output_dir = Path(output_dir)
    return {
        "badcase": collect_badcase_status(evaluation_dir),
        "replay": collect_replay_status(output_dir),
        "judge": collect_judge_status(output_dir),
    }


def format_status(status: dict[str, Any]) -> str:
    lines = ["# 开发期只读状态报告", ""]
    badcase = status["badcase"]
    lines.append("## Badcase 登记簿")
    if badcase.get("available"):
        lines.append(f"- 总数：{badcase['total']}")
        for state, items in badcase["byStatus"].items():
            lines.append(f"- {state}：{len(items)}")
            for item in items:
                lines.append(f"    - {item}")
        if badcase.get("registryProblems"):
            lines.append(f"- ⚠ 登记簿一致性问题：{badcase['registryProblems']}")
    else:
        lines.append("- 未找到 badcases.json")
    lines.append("")

    replay = status["replay"]
    lines.append("## 确定性重放")
    if replay.get("available"):
        lines.append(f"- 生成时间：{replay['generatedAt']}")
        lines.append(f"- 计数：{replay['totals']}")
    else:
        lines.append(f"- 暂无报告（{replay.get('hint', '')}）")
    lines.append("")

    judge = status["judge"]
    lines.append("## LLM-as-Judge（按需）")
    if judge.get("available"):
        lines.append(f"- 最近报告：{judge['latestReport']}（{judge['provider']}/{judge['model']}）")
        lines.append(f"- 样本：{judge['samples']}")
        if judge.get("averageScores"):
            lines.append(f"- 均分：{judge['averageScores']}")
    else:
        lines.append("- 暂无评审报告（按需运行 python -m evaluation.judge_cli）")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only evaluation status report.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="重放/Judge 报告所在目录（默认 <repo>/output/eval-reports）",
    )
    args = parser.parse_args()
    status = collect_status(EVALUATION_DIR, args.output_dir)
    print(format_status(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
