"""LLM-as-Judge 的按需 CLI：对语料中的讲解样本运行独立审核模型评分。

与确定性重放（evaluation.replay）的边界：judge 需要真实模型调用，不进入
确定性链路；本 CLI 是**按需**运行，输出报告到 output/eval-reports/judge/。

用法::

    cd apps/api
    ../../.venv/bin/python -m evaluation.judge_cli --provider ollama --model qwen2.5:7b
    ../../.venv/bin/python -m evaluation.judge_cli --provider codex --model default
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.judge import JUDGE_PROMPT_VERSION, RUBRIC, run_judge
from infrastructure.runtime.model_runtime import runtime as model_runtime

# 讲解样本语料（脱敏）：来自产品内置的分层引导卡（确定性模板产物），
# 代表真实讲解形态；新增样本在此登记。
EXPLANATION_SAMPLES: list[dict[str, Any]] = [
    {
        "id": "guide-cards-perpendicular-bisector",
        "questionContext": "PA=PB，M 是 AB 中点，求证 PM 垂直 AB。",
        "explanation": (
            "还没有把“到两点距离相等”转化为可以证明的几何关系。"
            "先连接 PA、PB，再利用 M 是 AB 的中点。"
            "比较三角形 PAM 和 PBM，你能找到哪三组相等的边？"
        ),
    },
    {
        "id": "guide-cards-ssr-congruence",
        "questionContext": "接上题，已证 PA=PB、AM=BM，继续求证 PM⊥AB。",
        "explanation": (
            "已经找到相等的边，但还没有使用全等三角形。"
            "PA = PB、AM = BM，另外 PM 是两个三角形的公共边。"
            "两个三角形全等后，∠PMA 和 ∠PMB 有什么关系？"
        ),
    },
    {
        "id": "guide-cards-adjacent-supplementary",
        "questionContext": "接上题，已证 ∠PMA=∠PMB，求证 PM⊥AB。",
        "explanation": (
            "已经证明两个邻角相等，还差最后的垂直关系。"
            "∠PMA 与 ∠PMB 相等，并且它们组成一个平角。"
            "两个相等的邻补角分别是多少度？这说明 PM 与 AB 有什么关系？"
        ),
    },
]


def run_all(provider: str, model: str) -> dict[str, Any]:
    results = []
    for sample in EXPLANATION_SAMPLES:
        outcome = run_judge(
            generate_json_as=model_runtime.generate_json_as,
            provider=provider,
            model=model,
            question_context=sample["questionContext"],
            explanation=sample["explanation"],
        )
        results.append({
            "id": sample["id"],
            "passed": outcome is not None,
            "outcome": outcome or "judge-failed",
        })
    succeeded = [r for r in results if r["passed"] is True]
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "judgePromptVersion": JUDGE_PROMPT_VERSION,
        "provider": provider,
        "model": model,
        "totals": {
            "samples": len(results),
            "judged": len(succeeded),
            "failed": len(results) - len(succeeded),
        },
        "results": results,
    }


def write_report(result: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"judge-{result['provider']}-{result['model']}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run LLM-as-Judge over explanation samples.")
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "output" / "eval-reports" / "judge",
    )
    args = parser.parse_args()
    result = run_all(args.provider, args.model)
    path = write_report(result, args.output_dir)
    totals = result["totals"]
    print(
        f"samples={totals['samples']} judged={totals['judged']} failed={totals['failed']}"
    )
    print(f"report: {path}")
    print(f"rubric dimensions: {', '.join(RUBRIC)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
