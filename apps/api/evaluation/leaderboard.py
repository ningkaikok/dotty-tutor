"""多模型评测排行榜：评审一致性 + 生成质量对比（roadmap ChatGPT 评审 S 优先级）。

两个报告回答两个不同的问题，不要混用：

- ``run_judge_agreement``：多个模型评审**同一批固定讲解**（``EXPLANATION_SAMPLES``
  里手写的静态文本），衡量评审员打分是否一致。回答"评审员之间靠不靠谱"。
- ``run_generation_comparison``：多个模型各自**生成**讲解，固定同一个评审模型打分。
  回答"哪个模型讲得更好"。

变量是什么、什么保持固定，两个函数刻意不共用同一套 provider/model 命名参数，
避免调用方把"被评审的生成模型"和"评审模型"传反。

生成步骤是评测专用的独立 prompt，不接生产 guideCards 流水线——评测必须保持
只读、不进入学生状态或课程发布路径，这一点与 ``judge.py`` 的职责边界一致。

样本量当前只有两位数出头，任何报告都必须显式声明不构成统计显著性；
不要把描述性统计包装成模型选型结论。

一致性不等于有效性：两个评审模型可以稳定地打出同一个分，同时**一起看不出**讲解里
的数学错误。判断评审是否有效要看 ``judgeMetrics.scoreDiscrimination``——它按语料的
事实性标注分组比较均分，由 ``judge_cli`` 计算，两个报告都会带上。
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from evaluation.corpus import (
    EXPLANATION_CORPUS_VERSION,
    EXPLANATION_SAMPLES,
    sample_set_hash,
)
from evaluation.judge import RUBRIC, run_judge_detailed
from evaluation.judge_cli import run_all
from infrastructure.runtime.model_runtime import runtime as model_runtime

JUDGE_AGREEMENT_REPORT_KIND = "judge-agreement"
JUDGE_AGREEMENT_REPORT_VERSION = "judge-agreement-v1"
GENERATION_COMPARISON_REPORT_KIND = "generation-comparison"
GENERATION_COMPARISON_REPORT_VERSION = "generation-comparison-v1"

GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"explanation": {"type": "string"}},
    "required": ["explanation"],
}


def statistical_note(sample_count: int) -> str:
    return (
        f"N={sample_count}，样本量过小，不构成统计显著性；"
        "仅作方向性参考，不得作为模型选型的唯一依据。"
    )


def build_generation_prompt(question_context: str) -> str:
    """评测专用的讲解生成提示词。风格对齐语料里手写引导卡的语气，
    但故意保持独立、简单——不复用生产 guideCards 提示词，避免评测调用
    意外耦合课程发布或学生状态路径。"""
    return (
        "你是一名数学辅导老师。针对下面的题目，写一段简短的引导讲解，"
        "帮助学生找到下一步该做什么，而不是直接给出最终答案或解题过程。\n"
        "要求：三句话以内，先指出学生可能卡在哪一步，再点出关键概念或操作，"
        "最后用一个引导性问题收尾，不要直接写出最终数值答案。\n\n"
        f"题目：{question_context}\n\n"
        "只输出符合 JSON Schema 的 JSON。"
    )


def _duration_ms(run: dict[str, Any], started: float) -> float:
    value = run.get("durationMs")
    if isinstance(value, (int, float)) and value >= 0:
        return round(float(value), 1)
    return round((time.perf_counter() - started) * 1000, 1)


def generate_explanation(
    *,
    generate_json_as: Callable[..., tuple[dict[str, Any], dict[str, Any]]],
    provider: str,
    model: str,
    question_context: str,
) -> dict[str, Any]:
    """调用候选模型生成一段讲解；失败返回 explanation=None 并保留耗时与错误类型。

    与 ``judge.run_judge_detailed`` 对称：只返回可比较的非内容元数据必需字段，
    调用方决定要不要把 explanation 本身写进报告（当前报告不写，避免报告体积失控
    和把未审校的模型原文当成"标准答案"外泄）。
    """
    prompt = build_generation_prompt(question_context)
    started = time.perf_counter()
    try:
        result, run = generate_json_as(provider, model, prompt, GENERATION_SCHEMA, max_tokens=300)
    except Exception as error:  # noqa: BLE001
        runtime_run = getattr(error, "runtime_run", None)
        runtime_run = runtime_run if isinstance(runtime_run, dict) else {}
        return {
            "explanation": None,
            "durationMs": _duration_ms(runtime_run, started),
            "errorType": type(error).__name__,
        }
    raw_explanation = result.get("explanation") if isinstance(result, dict) else None
    stripped = raw_explanation.strip() if isinstance(raw_explanation, str) else ""
    safe_run = run if isinstance(run, dict) else {}
    return {
        "explanation": stripped or None,
        "durationMs": _duration_ms(safe_run, started),
        "errorType": None if stripped else "empty_generation",
    }


def run_judge_agreement(
    judge_candidates: list[tuple[str, str]],
    *,
    generate_json_as: Callable[..., tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """多个评审模型对同一批固定讲解打分，衡量评审一致性（不比较讲解质量）。

    ``judge_candidates`` 里的每一项都是"评审员"，不是"讲解生成者"——
    这批讲解全部来自 ``EXPLANATION_SAMPLES`` 里手写的静态文本，不会因为
    换了评审模型而改变。

    本报告只回答"评审员彼此是否一致"。"评审员是否真的评对了"由每个评审各自的
    ``judgeMetrics.scoreDiscrimination`` 回答，不要用极差替代它下结论。
    """
    if len(judge_candidates) < 2:
        raise ValueError("至少需要两个评审模型才能比较一致性")
    generator = generate_json_as or model_runtime.generate_json_as
    per_judge_reports = {
        f"{provider}:{model}": run_all(provider, model, generate_json_as=generator)
        for provider, model in judge_candidates
    }

    per_sample_spread: dict[str, dict[str, float | None]] = {}
    for sample in EXPLANATION_SAMPLES:
        sample_id = sample["id"]
        dimension_spread: dict[str, float | None] = {}
        for dimension in RUBRIC:
            values: list[int] = []
            for report in per_judge_reports.values():
                result = next((item for item in report["results"] if item["id"] == sample_id), None)
                if result and result["judgeSucceeded"] and result["scores"]:
                    values.append(result["scores"][dimension])
            dimension_spread[dimension] = float(max(values) - min(values)) if len(values) >= 2 else None
        per_sample_spread[sample_id] = dimension_spread

    dimension_mean_spread: dict[str, float | None] = {}
    for dimension in RUBRIC:
        spreads: list[float] = [
            value
            for spread in per_sample_spread.values()
            if (value := spread[dimension]) is not None
        ]
        dimension_mean_spread[dimension] = round(sum(spreads) / len(spreads), 2) if spreads else None

    return {
        "reportKind": JUDGE_AGREEMENT_REPORT_KIND,
        "reportVersion": JUDGE_AGREEMENT_REPORT_VERSION,
        "corpusVersion": EXPLANATION_CORPUS_VERSION,
        "sampleSetHash": sample_set_hash(EXPLANATION_SAMPLES),
        "runId": uuid.uuid4().hex,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "statisticalNote": statistical_note(len(EXPLANATION_SAMPLES)),
        "judges": [f"{provider}:{model}" for provider, model in judge_candidates],
        "perJudgeMetrics": {
            label: report["judgeMetrics"] for label, report in per_judge_reports.items()
        },
        # 每道样本、每个评分维度上，各评审模型打分的极差（max-min）；
        # 只有 >=2 个评审都成功评出分才有值，否则为 None（不是 0——0 意味着"完全一致"）。
        # 极差小只说明评审员之间一致，不说明评审有效；有效性看
        # perJudgeMetrics[label].scoreDiscrimination。
        "perSampleScoreSpread": per_sample_spread,
        "dimensionMeanSpread": dimension_mean_spread,
    }


def run_generation_comparison(
    generator_candidates: list[tuple[str, str]],
    *,
    judge_provider: str,
    judge_model: str,
    generate_json_as: Callable[..., tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """多个模型各自生成讲解，固定同一评审模型打分，比较生成质量。

    评审模型（``judge_provider``/``judge_model``）在整次比较里必须保持不变——
    否则观察到的分数差异分不清是"生成模型不同"还是"评审标准不同"造成的。

    这里**没有** ``scoreDiscrimination``，而且不应该有：语料的 ``factualLabel``
    标注的是手写讲解原文对不对，而本函数评的是模型现场生成的新文本，标注对它不成立。
    只有 ``judge_cli.run_all`` 那条评审静态语料的路径才能算区分度。
    """
    generator = generate_json_as or model_runtime.generate_json_as
    per_generator: dict[str, Any] = {}
    for provider, model in generator_candidates:
        label = f"{provider}:{model}"
        results: list[dict[str, Any]] = []
        for sample in EXPLANATION_SAMPLES:
            generation = generate_explanation(
                generate_json_as=generator,
                provider=provider,
                model=model,
                question_context=sample["questionContext"],
            )
            if generation["explanation"] is None:
                results.append({
                    "id": sample["id"],
                    "generated": False,
                    "judgeSucceeded": False,
                    "scores": None,
                    "generationDurationMs": generation["durationMs"],
                    "judgeDurationMs": None,
                    "errorType": generation["errorType"],
                })
                continue
            detail = run_judge_detailed(
                generate_json_as=generator,
                provider=judge_provider,
                model=judge_model,
                question_context=sample["questionContext"],
                explanation=generation["explanation"],
            )
            outcome = detail["outcome"]
            results.append({
                "id": sample["id"],
                "generated": True,
                "judgeSucceeded": outcome is not None,
                "scores": outcome["scores"] if outcome else None,
                "generationDurationMs": generation["durationMs"],
                "judgeDurationMs": detail["durationMs"],
                "errorType": None if outcome is not None else detail["errorType"],
            })

        scored = [item for item in results if item["judgeSucceeded"]]
        average_scores = {
            dimension: round(sum(item["scores"][dimension] for item in scored) / len(scored), 2)
            for dimension in RUBRIC
        } if scored else {}
        per_generator[label] = {
            "sampleCount": len(results),
            "generated": sum(1 for item in results if item["generated"]),
            "judged": len(scored),
            "successRate": round(len(scored) / len(results), 4) if results else 0.0,
            "averageScores": average_scores,
            "results": results,
        }

    return {
        "reportKind": GENERATION_COMPARISON_REPORT_KIND,
        "reportVersion": GENERATION_COMPARISON_REPORT_VERSION,
        "corpusVersion": EXPLANATION_CORPUS_VERSION,
        "sampleSetHash": sample_set_hash(EXPLANATION_SAMPLES),
        "runId": uuid.uuid4().hex,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "statisticalNote": statistical_note(len(EXPLANATION_SAMPLES)),
        "judge": {"provider": judge_provider, "model": judge_model},
        "generators": [f"{provider}:{model}" for provider, model in generator_candidates],
        "perGeneratorMetrics": per_generator,
    }


def _parse_model_arg(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise ValueError(f"模型参数必须是 provider:model 形式，收到：{value}")
    provider, model = value.split(":", 1)
    if not provider or not model:
        raise ValueError(f"模型参数必须是 provider:model 形式，收到：{value}")
    return provider, model


def main() -> int:
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="多模型评测排行榜（评审一致性 / 生成质量对比）。")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    agreement_parser = subparsers.add_parser("agreement", help="评审一致性：多个模型评审同一批固定讲解")
    agreement_parser.add_argument(
        "--judges", nargs="+", required=True, metavar="provider:model",
        help="至少两个，例如 ollama:qwen2.5:3b ollama:qwen2.5:7b",
    )

    generation_parser = subparsers.add_parser("generation", help="生成质量对比：多个模型各自生成讲解，固定评审模型打分")
    generation_parser.add_argument(
        "--generators", nargs="+", required=True, metavar="provider:model",
    )
    generation_parser.add_argument("--judge-provider", required=True)
    generation_parser.add_argument("--judge-model", required=True)

    for sub in (agreement_parser, generation_parser):
        sub.add_argument(
            "--output-dir", type=Path,
            default=Path(__file__).resolve().parents[3] / "output" / "eval-reports" / "leaderboard",
        )

    args = parser.parse_args()

    if args.mode == "agreement":
        judges = [_parse_model_arg(item) for item in args.judges]
        report = run_judge_agreement(judges)
    else:
        generators = [_parse_model_arg(item) for item in args.generators]
        report = run_generation_comparison(
            generators, judge_provider=args.judge_provider, judge_model=args.judge_model,
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / f"{report['reportKind']}-{report['runId']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"statisticalNote: {report['statisticalNote']}")
    print(f"report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
