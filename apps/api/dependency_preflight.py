"""运行环境依赖自检（roadmap T2 约束）。

本项目依赖 MinerU、pypdf、Ollama、Codex CLI、Azure Speech、Qwen3-TTS 和
PostgreSQL；任何一个没配好，现状都是等到真正调用时才在业务流程里报错。这个模块
做的是"现在这条链路能不能跑"的一次性环境自检，不涉及任何业务数据。

三条边界（与 ``ocr_preflight.py`` 对照，那条检查的是内容，这条检查的是环境，
两者不合并，但复用同一套 ``{key, label, ok, detail, optional}`` 报告结构）：
1. 任何一项检查都不允许抛异常——失败的导入、缺失的命令、连不上的服务本身就是
   ``ok: False`` 的一条记录，不是异常路径；调用方（HTTP 端点、脚本、测试）永远
   能拿到完整报告。
2. 每项都标注 ``optional``：MinerU 没装可以回退到 pypdf 文字层，Ollama/Codex CLI
   两个模型 Provider 只要有一个可用即可继续生成，Azure Speech/Qwen3-TTS 没配置
   会回退到浏览器语音——这五项都标 ``optional``；pypdf 和 PostgreSQL 没有等价
   回退路径，标记为必需。整体 ``ok`` = 所有非 optional 项都通过。
3. 只做只读探测（读环境变量、找命令、探活网络端口/连接数据库），不写入任何状态、
   不修改任何配置。
"""

from __future__ import annotations

import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel

Probe = Callable[[], tuple[bool, str]]


class DependencyCheck(BaseModel):
    """单项依赖检查结果；``_safe_check`` 保证这里永远是一条记录，不是异常。"""

    key: str
    label: str
    ok: bool
    detail: str
    optional: bool


class DependencyPreflightReport(BaseModel):
    """整体依赖自检报告；``response_model`` 声明后前端才能拿到真正的类型，
    而不是 OpenAPI 里裸的 ``{[key: string]: unknown}``。"""

    ok: bool
    checks: list[DependencyCheck]


def _safe_check(key: str, label: str, *, optional: bool, probe: Probe) -> dict[str, Any]:
    """执行一次探测；探测函数内部未捕获的异常在这里统一收敛为失败记录。"""
    try:
        ok, detail = probe()
    except Exception as error:  # noqa: BLE001 - 依赖自检的核心约束就是绝不向上抛异常
        ok, detail = False, f"{type(error).__name__}: {error}"
    return {
        "key": key,
        "label": label,
        "ok": bool(ok),
        "detail": str(detail)[:300],
        "optional": optional,
    }


def _probe_pypdf() -> tuple[bool, str]:
    import pypdf

    return True, f"pypdf 已安装（版本 {getattr(pypdf, '__version__', '未知')}）"


def _probe_mineru() -> tuple[bool, str]:
    from infrastructure.runtime.ocr_runtime import OcrRuntime

    command = OcrRuntime().mineru_command()
    if command:
        return True, f"MinerU 命令：{command}"
    return False, "未找到 MinerU 命令；auto/mineru 模式会回退到 pypdf 文字层"


def _probe_ollama() -> tuple[bool, str]:
    from infrastructure.runtime.model_runtime import OLLAMA_BASE_URL

    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=2) as response:
            response.read(1)
        return True, f"Ollama 可达：{OLLAMA_BASE_URL}"
    except (OSError, urllib.error.URLError) as error:
        return False, f"Ollama 不可达（{OLLAMA_BASE_URL}）：{error}"


def _probe_codex_cli() -> tuple[bool, str]:
    from infrastructure.runtime.model_runtime import codex_command

    command = codex_command()
    available = bool(shutil.which(command) or Path(command).is_file())
    if available:
        return True, f"Codex CLI 命令：{command}"
    return False, f"未找到 Codex CLI（{command}）；请安装 Codex CLI 或设置 CODEX_COMMAND"


def _probe_azure_speech() -> tuple[bool, str]:
    key = os.getenv("AZURE_SPEECH_KEY", "").strip()
    region = os.getenv("AZURE_SPEECH_REGION", "").strip()
    if key and region:
        return True, f"Azure Speech 已配置（region={region}）"
    missing = [name for name, value in (("AZURE_SPEECH_KEY", key), ("AZURE_SPEECH_REGION", region)) if not value]
    return False, f"缺少环境变量：{', '.join(missing)}；TTS 会回退到浏览器语音或 Qwen3-TTS"


def _probe_qwen_tts() -> tuple[bool, str]:
    url = os.getenv("QWEN_TTS_URL", "http://127.0.0.1:8020").rstrip("/")
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=2) as response:
            response.read(1)
        return True, f"Qwen3-TTS 可达：{url}"
    except (OSError, urllib.error.URLError) as error:
        return False, f"Qwen3-TTS 不可达（{url}）：{error}；TTS 会回退到浏览器语音"


def _probe_postgresql() -> tuple[bool, str]:
    from persistence.app_store import get_application_store

    store = get_application_store()
    if store.ping():
        return True, f"PostgreSQL 可达且 schema 就绪（backend={store.backend}）"
    return False, "PostgreSQL 连接失败或 schema 未就绪；请检查 DATABASE_URL/POSTGRES_* 配置或执行迁移"


def run_dependency_preflight() -> dict[str, Any]:
    """执行全部依赖检查并返回整体报告。

    返回 ``{"ok": bool, "checks": [{"key","label","ok","detail","optional"}, ...]}``；
    ``ok`` 只看非 optional 项，与页面级脏页预检（``ocr_preflight.summarize_preflight``）
    的聚合口径互相独立。
    """
    checks = [
        _safe_check("pypdf", "pypdf 文字层解析", optional=False, probe=_probe_pypdf),
        _safe_check("postgresql", "PostgreSQL 数据库", optional=False, probe=_probe_postgresql),
        _safe_check("mineru", "MinerU OCR", optional=True, probe=_probe_mineru),
        _safe_check("ollama", "Ollama 本地模型", optional=True, probe=_probe_ollama),
        _safe_check("codex_cli", "Codex CLI", optional=True, probe=_probe_codex_cli),
        _safe_check("azure_speech", "Azure Speech 语音合成", optional=True, probe=_probe_azure_speech),
        _safe_check("qwen_tts", "Qwen3-TTS 语音合成", optional=True, probe=_probe_qwen_tts),
    ]
    ok = all(item["ok"] for item in checks if not item["optional"])
    return {"ok": ok, "checks": checks}
