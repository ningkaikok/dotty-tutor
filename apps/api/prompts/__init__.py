"""版本化教学模板：Git 基线在启动时固定，在线修订在任务开始时固定；不执行模板代码。

目录保存教学文本；上下文裁剪、Schema、判题和发布规则仍归业务模块所有。
运行审计仅记录模板身份，不包含渲染后的教材或学生输入。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from string import Template
from types import MappingProxyType
from typing import Mapping, ParamSpec, TypeVar

_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class PromptTemplate:
    """不可变的模板修订；内容哈希可识别忘记递增版本号的文本修改。"""

    id: str
    version: str
    text: str
    variables: tuple[str, ...]

    def identity(self) -> dict[str, str]:
        return {
            "id": self.id,
            "version": self.version,
            "contentHash": hashlib.sha256(self.text.encode("utf-8")).hexdigest(),
        }

    def render(self, **values: object) -> str:
        """严格匹配变量；插入值不再作为模板解析，避免输入中的占位符被执行。"""
        expected = set(self.variables)
        if set(values) != expected:
            raise ValueError(f"{self.id}: missing={sorted(expected - values.keys())}, unexpected={sorted(values.keys() - expected)}")
        return Template(self.text).substitute({key: str(value) for key, value in values.items()})


def _load_catalog() -> Mapping[str, PromptTemplate]:
    catalog = json.loads((_ROOT / "catalog.json").read_text(encoding="utf-8"))
    templates = {}
    for key, entry in catalog.items():
        text = (_ROOT / "templates" / entry["file"]).read_text(encoding="utf-8")
        template = Template(text)
        variables = tuple(entry["variables"])
        if not template.is_valid() or set(template.get_identifiers()) != set(variables):
            raise ValueError(f"invalid prompt variables: {key}")
        templates[key] = PromptTemplate(key, entry["version"], text, variables)
    return MappingProxyType(templates)


# 新模板只随下一次服务启动生效，单个任务不会在中途读到不同文件内容。
CATALOG = _load_catalog()


def render_prompt(template_id: str, **values: object) -> str:
    """渲染已登记模板；未知标识直接失败，不回退到其他教学阶段。"""
    return current_templates()[template_id].render(**values)


def prompt_identity(template_id: str) -> dict[str, str]:
    """返回可安全写入历史运行记录的模板身份。"""
    return current_templates()[template_id].identity()


def validate_template(template_id: str, text: str) -> None:
    """在线编辑只允许原变量契约，禁止省略上下文或引入程序表达式。"""
    if template_id not in CATALOG:
        raise KeyError(template_id)
    if not text.strip() or len(text) > 24000:
        raise ValueError("提示词不能为空，且不能超过 24000 字")
    template = Template(text)
    if not template.is_valid() or set(template.get_identifiers()) != set(CATALOG[template_id].variables):
        raise ValueError("模板变量必须与声明一致；使用 ${变量名}，字面美元符号使用 $$")


_snapshot: ContextVar[Mapping[str, PromptTemplate] | None] = ContextVar("prompt_snapshot", default=None)
_source: Callable[[], Mapping[str, PromptTemplate]] | None = None
_P = ParamSpec("_P")
_R = TypeVar("_R")


def configure_prompt_source(source: Callable[[], Mapping[str, PromptTemplate]] | None) -> None:
    """组合根配置发布来源；未启用在线管理时使用 Git 基线。"""
    global _source
    _source = source


def current_templates() -> Mapping[str, PromptTemplate]:
    """调用链内使用同一快照；直接渲染默认保持 Git 基线。"""
    return _snapshot.get() or CATALOG


@contextmanager
def prompt_snapshot(templates: Mapping[str, PromptTemplate]) -> Iterator[None]:
    """隔离并发任务的模板版本，退出后恢复调用者快照。"""
    token = _snapshot.set(MappingProxyType(dict(templates)))
    try:
        yield
    finally:
        _snapshot.reset(token)


def freeze_prompts(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """任务入口固定整套已发布模板；嵌套调用沿用现有快照。"""
    @wraps(function)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        if _snapshot.get() is not None:
            return function(*args, **kwargs)
        templates = _source() if _source is not None else CATALOG
        with prompt_snapshot(templates):
            return function(*args, **kwargs)
    return wrapped
