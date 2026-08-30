"""按需运行 LLM-as-Judge，并生成可比较的离线质量报告。

该 CLI 只调用评测用的独立审核模型，不写生产状态。报告中的 ``judgeMetrics``
描述审核运行本身；它不冒充被评讲解模型的质量或成本指标。
"""

from __future__ import annotations

import argparse
import json
import re
import uuid
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from typing import Any

from evaluation.corpus import (
    EXPLANATION_CORPUS_VERSION,
    EXPLANATION_SAMPLES,
    sample_set_hash,
)
from evaluation.judge import JUDGE_PROMPT_VERSION, RUBRIC, run_judge_detailed
from infrastructure.runtime.model_runtime import runtime as model_runtime

JUDGE_REPORT_KIND = "judge"
JUDGE_REPORT_VERSION = "judge-report-v2"


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, ceil(len(ordered) * percentile / 100) - 1)
    return round(ordered[index], 1)


def _numeric_sum(values: list[Any]) -> int | None:
    numbers = [value for value in values if isinstance(value, int) and value >= 0]
    return sum(numbers) if numbers and len(numbers) == len(values) else None


def _build_judge_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    succeeded = [result for result in results if result["judgeSucceeded"]]
    durations = [float(result["durationMs"]) for result in results]
    scores = [result["scores"] for result in succeeded if result["scores"]]
    average_scores = {
        dimension: round(sum(item[dimension] for item in scores) / len(scores), 2)
        for dimension in RUBRIC
    } if scores else {}
    prompt_tokens = _numeric_sum(
        [result["tokenUsage"]["promptTokens"] for result in results]
    )
    output_tokens = _numeric_sum(
        [result["tokenUsage"]["outputTokens"] for result in results]
    )
    return {
        "sampleCount": len(results),
        "succeeded": len(succeeded),
        "successRate": round(len(succeeded) / len(results), 4) if results else 0.0,
        "failureCount": len(results) - len(succeeded),
        "p50DurationMs": _percentile(durations, 50),
        "p95DurationMs": _percentile(durations, 95),
        "logicalCalls": sum(result["logicalCalls"] for result in results),
        "providerAttempts": sum(result["providerAttempts"] for result in results),
        "averageScores": average_scores,
        "tokenUsage": {
            "promptTokens": prompt_tokens,
            "outputTokens": output_tokens,
        },
        "schemaFallbackCount": sum(
            1 for result in results if result["schemaFallback"]["used"]
        ),
    }


def run_all(
    provider: str,
    model: str,
    *,
    generate_json_as: Any = None,
) -> dict[str, Any]:
    """运行讲解语料并返回不包含原始讲解/审核依据的安全报告。"""
    generator = generate_json_as if generate_json_as is not None else model_runtime.generate_json_as
    results: list[dict[str, Any]] = []
    for sample in EXPLANATION_SAMPLES:
        detail = run_judge_detailed(
            generate_json_as=generator,
            provider=provider,
            model=model,
            question_context=sample["questionContext"],
            explanation=sample["explanation"],
        )
        outcome = detail["outcome"]
        scores = outcome.get("scores") if isinstance(outcome, dict) else None
        results.append({
            "id": sample["id"],
            "judgeSucceeded": outcome is not None,
            "scores": scores if isinstance(scores, dict) else None,
            "durationMs": detail["durationMs"],
            "logicalCalls": detail["logicalCalls"],
            "providerAttempts": detail["providerAttempts"],
            "tokenUsage": detail["usage"],
            "schemaFallback": detail["schemaFallback"],
            "errorType": detail["errorType"],
        })

    metrics = _build_judge_metrics(results)
    return {
        "reportKind": JUDGE_REPORT_KIND,
        "reportVersion": JUDGE_REPORT_VERSION,
        "corpusVersion": EXPLANATION_CORPUS_VERSION,
        "sampleSetHash": sample_set_hash(EXPLANATION_SAMPLES),
        "runId": uuid.uuid4().hex,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "judge": {
            "provider": provider,
            "model": model,
            "promptVersion": JUDGE_PROMPT_VERSION,
        },
        "totals": {
            "samples": len(results),
            "judged": metrics["succeeded"],
            "failed": metrics["failureCount"],
        },
        "judgeMetrics": metrics,
        "results": results,
        # 只保留确定性回放器需要的轻量形状，便于统一状态页读取；不放 rationale。
        "entries": [
            {
                "id": result["id"],
                "passed": result["judgeSucceeded"],
                "documenting_bug": None,
                "checks": [{
                    "name": "judge",
                    "passed": result["judgeSucceeded"],
                    "detail": result["errorType"] or "ok",
                }],
                "scores": result["scores"],
            }
            for result in results
        ],
    }


def validate_report(report: dict[str, Any]) -> list[str]:
    """校验 Judge 报告契约，返回全部问题而不是在 CLI 中静默写坏报告。"""
    problems: list[str] = []
    required = {
        "reportKind": str,
        "reportVersion": str,
        "corpusVersion": str,
        "sampleSetHash": str,
        "judge": dict,
        "judgeMetrics": dict,
        "results": list,
    }
    for key, expected_type in required.items():
        if not isinstance(report.get(key), expected_type):
            problems.append(f"missing or invalid {key}")
    if report.get("reportKind") != JUDGE_REPORT_KIND:
        problems.append("reportKind must be 'judge'")
    judge = report.get("judge")
    if isinstance(judge, dict):
        for key in ("provider", "model", "promptVersion"):
            if not isinstance(judge.get(key), str) or not judge[key]:
                problems.append(f"judge.{key} is required")
    results = report.get("results")
    if isinstance(results, list):
        ids: set[str] = set()
        for index, result in enumerate(results):
            prefix = f"results[{index}]"
            if not isinstance(result, dict):
                problems.append(f"{prefix} must be an object")
                continue
            result_id = result.get("id")
            if not isinstance(result_id, str) or not result_id:
                problems.append(f"{prefix}.id is required")
            elif result_id in ids:
                problems.append(f"duplicate result id: {result_id}")
            else:
                ids.add(result_id)
            if not isinstance(result.get("judgeSucceeded"), bool):
                problems.append(f"{prefix}.judgeSucceeded is required")
            for key in ("durationMs", "logicalCalls", "providerAttempts"):
                value = result.get(key)
                if not isinstance(value, (int, float)) or value < 0:
                    problems.append(f"{prefix}.{key} is invalid")
            usage = result.get("tokenUsage")
            if not isinstance(usage, dict):
                problems.append(f"{prefix}.tokenUsage is required")
            fallback = result.get("schemaFallback")
            if not isinstance(fallback, dict) or not isinstance(fallback.get("used"), bool):
                problems.append(f"{prefix}.schemaFallback is invalid")
            if result.get("judgeSucceeded") and not isinstance(result.get("scores"), dict):
                problems.append(f"{prefix}.scores is required for successful judge")
            scores = result.get("scores")
            if isinstance(scores, dict):
                for dimension in RUBRIC:
                    value = scores.get(dimension)
                    if not isinstance(value, int) or not 1 <= value <= 5:
                        problems.append(f"{prefix}.scores.{dimension} is invalid")
    metrics = report.get("judgeMetrics")
    if isinstance(metrics, dict):
        for key in (
            "sampleCount", "succeeded", "successRate", "failureCount",
            "p50DurationMs", "p95DurationMs", "logicalCalls", "providerAttempts",
            "averageScores", "tokenUsage", "schemaFallbackCount",
        ):
            if key not in metrics:
                problems.append(f"judgeMetrics.{key} is required")
    return problems


def _safe_model_name(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model).strip("-") or "model"


def write_report(result: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = (
        f"judge-{_safe_model_name(str(result['judge']['provider']))}-"
        f"{_safe_model_name(str(result['judge']['model']))}-"
        f"{result['runId']}.json"
    )
    path = output_dir / filename
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
    parser.add_argument(
        "--check",
        action="store_true",
        help="评测失败或报告契约不合法时返回 1",
    )
    args = parser.parse_args()
    result = run_all(args.provider, args.model)
    problems = validate_report(result)
    if problems:
        print("contract errors: " + "; ".join(problems))
        return 1
    path = write_report(result, args.output_dir)
    metrics = result["judgeMetrics"]
    print(
        f"samples={metrics['sampleCount']} judged={metrics['succeeded']} "
        f"failed={metrics['failureCount']} successRate={metrics['successRate']}"
    )
    print(f"report: {path}")
    if args.check and (problems or metrics["failureCount"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
