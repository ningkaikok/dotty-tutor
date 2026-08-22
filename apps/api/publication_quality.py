"""发布质量门禁在持久化层与 HTTP 层之间共享的错误契约。"""

from __future__ import annotations

from typing import Any


class PublicationQualityError(ValueError):
    """表示整份试卷的候选题在自动修复后仍全部处于隔离状态。"""

    code = "publication_quality_blocked"

    def __init__(self, blockers: list[dict[str, Any]]) -> None:
        super().__init__("自动修复后仍没有可安全发布的题目，请重试生成")
        self.blockers = blockers

    def detail(self) -> dict[str, Any]:
        """返回稳定诊断结构，但不把教材题干原文写入日志或错误响应。"""
        return {
            "code": self.code,
            "message": str(self),
            "blockedCount": len(self.blockers),
            "blockedLessons": self.blockers,
        }
