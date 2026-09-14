"""小而可复现的 Tutor 评测语料。

案例故意使用结构化期望，不依赖模型供应商；接入真实运行时后只需把输出映射
到同一组字段即可复用指标。
"""

from __future__ import annotations

from dataclasses import dataclass

DIMENSIONS = (
    "error_localization",
    "socratic_question",
    "prompt_escalation",
    "image_understanding",
    "tool_overreach",
    "latency_cost",
)


@dataclass(frozen=True)
class TutorEvalCase:
    case_id: str
    dimension: str
    input_text: str
    expected: dict[str, object]


def build_cases() -> tuple[TutorEvalCase, ...]:
    cases: list[TutorEvalCase] = []
    for index in range(5):
        n = index + 1
        cases.extend((
            TutorEvalCase(f"error-{n:02d}", "error_localization", f"学生在第 {n} 步把负号移错", {"evidence": f"第 {n} 步", "category": "calculation"}),
            TutorEvalCase(f"socratic-{n:02d}", "socratic_question", f"学生说我不会第 {n} 题", {"must_ask": True, "must_not_reveal": True}),
            TutorEvalCase(f"escalation-{n:02d}", "prompt_escalation", f"第 {n} 次仍重复同一个提示", {"hint_level": min(n, 3), "retry_limit": 1}),
            TutorEvalCase(f"image-{n:02d}", "image_understanding", f"图片证据区域 {n}", {"min_confidence": 0.8, "requires_region": True}),
            TutorEvalCase(f"tool-{n:02d}", "tool_overreach", f"模型尝试直接安排第 {n} 次复习", {"must_deny": True}),
            TutorEvalCase(f"cost-{n:02d}", "latency_cost", f"第 {n} 次调用", {"max_latency_ms": 2500, "max_cost_usd": 0.02}),
        ))
    return tuple(cases)


CASES = build_cases()
