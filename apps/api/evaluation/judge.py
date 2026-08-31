"""LLM-as-Judge 讲解质量评估（roadmap T1#5 基建）。

职责边界：
- 用**独立审核模型**按固定 rubric 为讲解/提示文本评分，输出分数、依据和置信度——
  服务于"讲解清晰度、是否针对误区等主观质量由独立审核模型按固定 rubric 评分"
  的验收项；判据器与判题证据（answer_evaluator）不受本模块影响。
- 评分结果**只读**：不写学生状态、不改变判题、不进入掌握度——纯评测产物。
- 与确定性重放的边界：judge 需要真实模型调用，**不进入** `evaluation.replay`
  的确定性链路；通过独立 CLI（``python -m evaluation.judge``）按需运行。

Prompt 模板版本化：``JUDGE_PROMPT_VERSION`` 参与响应溯源；修改 rubric 文案时递增。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

# 固定评分 rubric：每个维度 1-5 分，附判分说明。修改任何文案都必须递增版本号。
JUDGE_PROMPT_VERSION = "judge-rubric-v1"

RUBRIC: dict[str, str] = {
    "clarity": "清晰度：步骤是否循序渐进、语言是否无歧义、学生能否独立跟随。",
    "targeting": "针对性：是否针对学生的具体卡点/误区，而不是泛泛重复题干。",
    "factual": "事实性：数学事实是否正确，有无编造条件或错误结论。",
}

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "scores": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                key: {"type": "integer", "minimum": 1, "maximum": 5}
                for key in RUBRIC
            },
            "required": list(RUBRIC),
        },
        "rationale": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["scores", "rationale", "confidence"],
}

_VALID_SCORE = re.compile(r"^[1-5]$")


def build_judge_prompt(question_context: str, explanation: str) -> str:
    """构建锁定 rubric 的评审提示词；只读输入，不包含学生个人信息。"""
    rubric_lines = "\n".join(f"- {key}：{desc}" for key, desc in RUBRIC.items())
    return (
        f"你是独立的讲解质量审核员。按以下 rubric 对讲解文本逐维度打 1-5 分，"
        f"并给出简短依据（rationale）与整体置信度 confidence（0-1）。\n"
        f"评分维度：\n{rubric_lines}\n\n"
        f"【题目上下文】\n{question_context[:600]}\n\n"
        f"【讲解文本】\n{explanation[:1500]}\n\n"
        f"只输出符合 JSON Schema 的 JSON；分数必须为 1-5 的整数；"
        f"不得改写讲解或给出新的解题步骤。"
    )


def parse_judge_response(content: str) -> dict[str, Any] | None:
    """解析并校验评审输出；结构或分值非法时返回 None（调用方记为评审失败）。"""
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    scores = data.get("scores")
    if not isinstance(scores, dict):
        return None
    validated: dict[str, int] = {}
    for key in RUBRIC:
        value = scores.get(key)
        if not isinstance(value, int) or not _VALID_SCORE.match(str(value)):
            return None
        validated[key] = value
    rationale = data.get("rationale")
    confidence = data.get("confidence")
    if not isinstance(rationale, str) or not rationale.strip():
        return None
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        return None
    return {
        "scores": validated,
        "rationale": rationale.strip()[:600],
        "confidence": round(float(confidence), 2),
        "judgePromptVersion": JUDGE_PROMPT_VERSION,
    }


def run_judge(
    *,
    generate_json_as: Callable[..., tuple[dict[str, Any], dict[str, Any]]],
    provider: str,
    model: str,
    question_context: str,
    explanation: str,
) -> dict[str, Any] | None:
    """调用独立审核模型执行一次评审；失败返回 None（由调用方决定是否重试）。"""
    return run_judge_detailed(
        generate_json_as=generate_json_as,
        provider=provider,
        model=model,
        question_context=question_context,
        explanation=explanation,
    )["outcome"]


def run_judge_detailed(
    *,
    generate_json_as: Callable[..., tuple[dict[str, Any], dict[str, Any]]],
    provider: str,
    model: str,
    question_context: str,
    explanation: str,
) -> dict[str, Any]:
    """执行一次评审并保留可比较的非内容元数据。

    评测报告需要回答“模型是否变好、是否变慢、是否更费调用”，但不能把学生输入或
    模型原始输出写进报告。这里仅返回调用次数、耗时、失败类型和已校验的评分；
    ``run_judge`` 继续只返回评分，保持线上调用方的旧契约不变。
    """
    prompt = build_judge_prompt(question_context, explanation)
    started = time.perf_counter()
    logical_calls = 1
    try:
        result, run = generate_json_as(
            provider, model, prompt, JUDGE_SCHEMA, max_tokens=400
        )
    except Exception as error:  # noqa: BLE001
        runtime_run = getattr(error, "runtime_run", None)
        runtime_run = runtime_run if isinstance(runtime_run, dict) else {}
        return {
            "outcome": None,
            "run": runtime_run or None,
            "logicalCalls": logical_calls,
            # ``calls`` is retained as an internal compatibility alias; reports use
            # the unambiguous ``logicalCalls`` field.
            "calls": logical_calls,
            "providerAttempts": _provider_attempts(runtime_run),
            "usage": _usage(runtime_run),
            "schemaFallback": _schema_fallback(runtime_run),
            "durationMs": _duration_ms(runtime_run, started),
            "errorType": type(error).__name__,
        }
    normalized = parse_judge_response(json.dumps(result, ensure_ascii=False))
    safe_run = run if isinstance(run, dict) else {}
    return {
        "outcome": normalized,
        "run": safe_run or None,
        "logicalCalls": logical_calls,
        "calls": logical_calls,
        "providerAttempts": _provider_attempts(safe_run),
        "usage": _usage(safe_run),
        "schemaFallback": _schema_fallback(safe_run),
        "durationMs": _duration_ms(safe_run, started),
        "errorType": None if normalized is not None else "invalid_judge_result",
    }


def _provider_attempts(run: dict[str, Any]) -> int:
    value = run.get("providerAttempts", 1)
    return value if isinstance(value, int) and value > 0 else 1


def _usage(run: dict[str, Any]) -> dict[str, int | None]:
    usage = run.get("usage")
    if not isinstance(usage, dict):
        return {"promptTokens": None, "outputTokens": None}
    return {
        "promptTokens": usage.get("promptTokens"),
        "outputTokens": usage.get("outputTokens"),
    }


def _schema_fallback(run: dict[str, Any]) -> dict[str, Any]:
    value = run.get("schemaFallback")
    if isinstance(value, dict):
        return {"used": bool(value.get("used")), "reason": value.get("reason")}
    return {"used": False, "reason": None}


def _duration_ms(run: dict[str, Any], started: float) -> float:
    value = run.get("durationMs")
    if isinstance(value, (int, float)) and value >= 0:
        return round(float(value), 1)
    return round((time.perf_counter() - started) * 1000, 1)
