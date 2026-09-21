"""依赖自检的只读 HTTP 入口（roadmap T2：环境依赖自检）。

单独开一个路由模块而不是塞进 ``textbook_routes.py``——那个文件另有并行任务在
改字段编辑/回滚端点，冲突面很大；依赖自检本身也和教材上传无关，独立路由更清晰。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from dependency_preflight import DependencyPreflightReport, run_dependency_preflight


def build_dependency_preflight_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/system/dependency-preflight", response_model=DependencyPreflightReport)
    def dependency_preflight() -> dict[str, Any]:
        """返回"现在这条链路能不能跑"的环境依赖自检报告；只读，不做任何写入。"""
        return run_dependency_preflight()

    return router
