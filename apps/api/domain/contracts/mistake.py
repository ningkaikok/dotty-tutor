"""Validated contracts for mistake capture and student confirmation."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MistakeErrorReason = Literal[
    "concept",
    "reading",
    "calculation",
    "missing_step",
    "unknown",
    "careless",
]


class MistakeConfirmation(BaseModel):
    prompt: str = Field(min_length=1, max_length=8_000)
    originalAnswer: str = Field(default="", max_length=2_000)
    subject: str = Field(default="数学", min_length=1, max_length=80)
    gradeBand: str = Field(default="初中", min_length=1, max_length=80)
    # 章节/知识点在确认页不再强制：AI 已经预填，学生保存错题时不应被这两项卡住。
    # 上限仍然保留，防止异常输入。
    chapter: str = Field(default="", max_length=160)
    knowledgePoint: str = Field(default="", max_length=200)
    # 错因归因迁移到陪练首轮自评（见 turn_plan.py 的 unknown 回退策略），确认时
    # 允许为空；非法枚举值仍会被 Pydantic 拒绝。
    errorReason: MistakeErrorReason | None = None
    notes: str = Field(default="", max_length=2_000)


class MistakeArchiveRequest(BaseModel):
    archived: bool = True
