"""上传目录内的阶段产物缓存。

缓存只保存成功的 JSON。每个文件名就是规范化输入的 hash；读取失败、结构不符或超过
大小限制都安全地视为 miss，不会阻塞题目生成。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CACHE_FORMAT_VERSION = "stage-cache-v1"


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


class StageArtifactCache:
    """内容寻址、原子写入且有大小上限的本地阶段缓存。"""

    def __init__(
        self,
        asset_dir: Path | None,
        *,
        enabled: bool | None = None,
        max_entries: int | None = None,
        max_bytes: int | None = None,
    ) -> None:
        if asset_dir is not None:
            candidate = Path(asset_dir)
            self.root = candidate if candidate.name == "stage-cache" else candidate / "stage-cache"
        else:
            self.root = None
        configured_enabled = _env_bool("DOTTY_STAGE_CACHE_ENABLED", True)
        configured_entries = _env_int("DOTTY_STAGE_CACHE_MAX_ENTRIES", 128)
        configured_bytes = _env_int("DOTTY_STAGE_CACHE_MAX_BYTES", 8_000_000)
        self.enabled = bool((configured_enabled if enabled is None else enabled) and self.root is not None)
        self.max_entries = max(1, int(configured_entries if max_entries is None else max_entries))
        self.max_bytes = max(1024, int(configured_bytes if max_bytes is None else max_bytes))
        self.hits = 0
        self.misses = 0

    def _path(self, source_stable_id: str, stage: str, cache_key: str) -> Path | None:
        if not self.enabled or self.root is None:
            return None
        safe_source = "".join(char for char in source_stable_id if char.isalnum() or char in "-_")[:80] or "source"
        safe_stage = "".join(char for char in stage if char.isalnum() or char in "-_")[:40]
        return self.root / safe_source / safe_stage / f"{cache_key}.json"

    def load(self, source_stable_id: str, stage: str, cache_key: str) -> dict[str, Any] | None:
        path = self._path(source_stable_id, stage, cache_key)
        if path is None or not path.is_file():
            self.misses += 1
            return None
        try:
            if path.stat().st_size > self.max_bytes:
                raise ValueError("stage cache artifact too large")
            data = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(data, dict)
                or data.get("cacheVersion") != CACHE_FORMAT_VERSION
                or data.get("cacheKey") != cache_key
                or data.get("stage") != stage
            ):
                raise ValueError("stage cache identity mismatch")
            result = data.get("result")
            if not isinstance(result, dict):
                raise ValueError("stage cache result is not an object")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            self.misses += 1
            return None
        self.hits += 1
        return result

    def save(self, source_stable_id: str, stage: str, cache_key: str, result: dict[str, Any]) -> bool:
        path = self._path(source_stable_id, stage, cache_key)
        if path is None:
            return False
        document = {
            "cacheVersion": CACHE_FORMAT_VERSION,
            "stage": stage,
            "cacheKey": cache_key,
            "result": result,
        }
        encoded = json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(encoded) > self.max_bytes:
            return False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_bytes(encoded)
            os.replace(temporary, path)
            self._trim()
            return True
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def _trim(self) -> None:
        if self.root is None:
            return
        files = sorted(
            (item for item in self.root.rglob("*.json") if item.is_file()),
            key=lambda item: item.stat().st_mtime,
        )
        total_bytes = sum(path.stat().st_size for path in files)
        while files and (len(files) > self.max_entries or total_bytes > self.max_bytes):
            path = files.pop(0)
            try:
                total_bytes -= path.stat().st_size
                path.unlink()
            except OSError:
                continue

    def get(self, cache_key: str) -> dict[str, Any] | None:
        """兼容旧编排器的平面 key 读取。"""
        if not self.enabled or self.root is None:
            return None
        paths = list(self.root.rglob(f"{cache_key}.json"))
        if not paths:
            return None
        try:
            data = json.loads(paths[0].read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def put(self, cache_key: str, value: dict[str, Any]) -> bool:
        """兼容旧编排器的平面 key 原子写入。"""
        if not self.enabled or self.root is None:
            return False
        path = self.root / f"{cache_key}.json"
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(encoded) > self.max_bytes:
            return False
        temporary = path.with_suffix(".json.tmp")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(encoded)
            os.replace(temporary, path)
            self._trim()
            return True
        except OSError:
            temporary.unlink(missing_ok=True)
            return False
