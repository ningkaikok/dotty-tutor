"""记住本机最近一次的模型/OCR 选择，重启进程后沿用。

只做单机本地持久化：一个 JSON 文件，键是 runtime 名字（generation/tutoring/review/ocr），
值是该 runtime 的选择。不写数据库、不跨机器同步，符合 model_runtime.py 里“selection 是
进程级配置，不是用户偏好”的约束——这里只是把同一份进程级配置搬到重启后还能读到的地方。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_DEFAULT_PATH = Path(__file__).resolve().parents[4] / ".dotty-runtime-selection.json"


def _path() -> Path:
    configured = os.getenv("DOTTY_RUNTIME_SELECTION_FILE", "").strip()
    return Path(configured).expanduser() if configured else _DEFAULT_PATH


def load(key: str) -> dict[str, Any] | None:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, dict) else None


def save(key: str, value: dict[str, Any]) -> None:
    path = _path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[key] = value
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
