"""统一的 HTTP 错误契约。

业务层仍可以抛出现有的 ``HTTPException``；本模块只负责在应用边界把不同
来源的异常收敛成同一份、不会泄露内部堆栈的 Problem Details 风格响应。
"""

from __future__ import annotations

from typing import Any, Mapping


class AppError(Exception):
    """可安全返回给客户端的应用错误。

    ``details`` 仅用于结构化的、非敏感诊断信息。调用方不应把原始教材文本、
    学生输入、密钥或第三方响应正文放入其中；未捕获异常会由应用层统一隐藏。
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 500,
        error_code: str = "APP_ERROR",
        retryable: bool = False,
        details: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.retryable = retryable
        self.details = details
        self.headers = dict(headers or {})

    def to_problem(self, request_id: str) -> dict[str, Any]:
        """返回稳定的 camelCase JSON，不包含 Python 异常对象。"""
        return {
            "requestId": request_id,
            "errorCode": self.error_code,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


def problem_details(
    *,
    request_id: str,
    error_code: str,
    message: str,
    retryable: bool = False,
    details: Any = None,
) -> dict[str, Any]:
    """构造错误响应；单独函数便于 HTTP handler 和单元测试复用。"""
    return {
        "requestId": request_id,
        "errorCode": error_code,
        "message": message,
        "retryable": retryable,
        "details": details,
    }


__all__ = ["AppError", "problem_details"]
