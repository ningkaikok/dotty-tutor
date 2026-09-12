"""分阶段题目生成的公共编排契约。

实际模型提示词仍由 lesson_generation 维护，以保持旧调用兼容；本模块提供阶段 DAG 和
局部失效规则，审核接口与测试不需要依赖模型实现细节。
"""

from __future__ import annotations

from typing import Final

STAGES: Final[tuple[str, ...]] = ("extraction", "solution", "verification", "tutor-script")
DOWNSTREAM: Final[dict[str, tuple[str, ...]]] = {
    "extraction": STAGES,
    "solution": STAGES[1:],
    "verification": STAGES[2:],
    "tutor-script": ("tutor-script",),
}


def stages_for_rerun(stage: str) -> tuple[str, ...]:
    """返回目标阶段及所有受影响下游，拒绝未知阶段。"""
    if stage not in DOWNSTREAM:
        raise ValueError(f"unknown stage: {stage}")
    return DOWNSTREAM[stage]


def normalize_stage(stage: str | None) -> str:
    """标准化阶段名，供 API 和生成器共用。"""
    value = str(stage or "").strip().lower().replace("_", "-")
    value = {"extract": "extraction", "solve": "solution", "verify": "verification", "tutor": "tutor-script"}.get(value, value)
    if value not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    return value


def stages_from(stage: str | None) -> tuple[str, ...]:
    return stages_for_rerun(normalize_stage(stage))


def can_run_tutor_script(verification: dict[str, object] | None) -> bool:
    return bool(verification and verification.get("status") == "verified")


__all__ = ["DOWNSTREAM", "STAGES", "can_run_tutor_script", "normalize_stage", "stages_for_rerun", "stages_from"]
