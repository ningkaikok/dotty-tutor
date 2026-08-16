"""题目生成模型的统一适配层。

本模块把 Ollama、Codex CLI 和 Mock 暴露为相同的 JSON 生成接口。业务层只关心
``generate_json`` 返回的结构化数据，不需要知道 HTTP、子进程或模型登录细节。

需要特别注意：这里的 ``selection`` 是进程级演示配置，不是用户偏好。生产环境如果需要
多租户，应把模型选择放入请求上下文或租户配置，不能继续修改这个全局对象。
"""

from __future__ import annotations

import json
import os
import base64
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from observability import log_event


Provider = Literal["ollama", "codex", "mock"]
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")


def codex_command() -> str:
    """返回 Codex CLI 路径。

    macOS 桌面应用可能把 CLI 放在应用资源目录而不是 ``PATH``，因此允许通过
    ``CODEX_COMMAND`` 显式指定，Docker 中也会据此判断宿主机能力是否可见。
    """
    return os.getenv("CODEX_COMMAND", "codex")


@dataclass
class ModelSelection:
    provider: Provider
    model: str


class ModelRuntime:
    """管理生成模型目录、当前选择和结构化调用。

    Catalog 方法可以修正已经失效的选择；实际生成前会复制一次选择快照，避免长请求执行
    期间用户切换下拉框而让日志中的 provider/model 与真实调用不一致。
    """

    def __init__(self, *, env_prefix: str = "") -> None:
        """Create a runtime with an independent environment/config namespace.

        ``MODEL_*`` controls content generation. The tutor passes ``TUTOR_*`` so
        changing the student-facing陪练 model cannot silently alter textbook
        generation or review jobs.
        """
        provider_name = f"{env_prefix}MODEL_PROVIDER" if env_prefix else "MODEL_PROVIDER"
        model_name = f"{env_prefix}MODEL_NAME" if env_prefix else "MODEL_NAME"
        self.selection = ModelSelection(
            provider=os.getenv(provider_name, "ollama"),  # type: ignore[arg-type]
            model=os.getenv(model_name, "qwen2.5:3b"),
        )

    def ollama_models(self) -> tuple[list[str], str | None]:
        try:
            request = urllib.request.Request(f"{OLLAMA_BASE_URL}/api/tags")
            with urllib.request.urlopen(request, timeout=3) as response:
                payload = json.load(response)
            models = [item["name"] for item in payload.get("models", []) if item.get("name")]
            return models, None
        except (OSError, ValueError, urllib.error.URLError) as error:
            return [], str(error)

    def providers(self) -> list[dict[str, Any]]:
        """探测可用后端，但不修改当前生成模型选择。"""
        local_models, ollama_error = self.ollama_models()
        codex_binary = codex_command()
        codex_available = bool(shutil.which(codex_binary) or Path(codex_binary).is_file())
        return [
            {
                "id": "ollama",
                "label": "Ollama 本地模型",
                "available": bool(local_models),
                "models": local_models,
                "detail": "完全本地运行，不产生 API 费用" if local_models else (ollama_error or "未安装模型"),
            },
            {
                "id": "codex",
                "label": "Codex 订阅",
                "available": codex_available,
                "models": ["default", "gpt-5.6-sol"],
                "detail": (
                    "复用本机 ChatGPT/Codex 登录与套餐额度"
                    if codex_available else
                    "当前后端找不到 Codex CLI；请使用宿主机后端或配置 CODEX_COMMAND"
                ),
            },
            {
                "id": "mock",
                "label": "Mock 固定模式",
                "available": True,
                "models": ["static-demo"],
                "detail": "不调用模型，用于离线回退和界面对照",
            },
        ]

    def catalog(self) -> dict[str, Any]:
        providers = self.providers()
        local_provider = next(item for item in providers if item["id"] == "ollama")
        local_models = local_provider["models"]
        if self.selection.provider == "ollama" and self.selection.model not in local_models:
            if local_models:
                self.selection.model = local_models[0]
            else:
                self.selection = ModelSelection("mock", "static-demo")

        return {
            "selected": {
                "provider": self.selection.provider,
                "model": self.selection.model,
            },
            "providers": providers,
        }

    def select(self, provider: Provider, model: str) -> dict[str, Any]:
        catalog = self.catalog()
        provider_info = next((item for item in catalog["providers"] if item["id"] == provider), None)
        if not provider_info or not provider_info["available"]:
            raise ValueError(f"{provider} 当前不可用")
        if model not in provider_info["models"]:
            raise ValueError(f"{provider} 中没有模型 {model}")
        self.selection = ModelSelection(provider, model)
        return self.catalog()

    def generate_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int = 1200,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """用当前生成模型返回满足 Schema 的对象及可追踪运行记录。"""
        selection = ModelSelection(self.selection.provider, self.selection.model)
        if selection.provider == "mock":
            raise RuntimeError("Mock 模式不调用模型")
        started = time.perf_counter()
        log_event("model.request.started", provider=selection.provider, model=selection.model)
        try:
            if selection.provider == "ollama":
                result = self._ollama_json(selection.model, prompt, schema, max_tokens)
            else:
                result = self._codex_json(selection.model, prompt, schema)
        except Exception as error:
            log_event(
                "model.request.failed",
                level=40,
                provider=selection.provider,
                model=selection.model,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                error_type=type(error).__name__,
                error=str(error)[:300],
                exc_info=True,
            )
            raise
        log_event(
            "model.request.completed",
            provider=selection.provider,
            model=selection.model,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return result, {
            "requestedProvider": selection.provider,
            "provider": selection.provider,
            "model": selection.model,
            "fallback": False,
        }

    def generate_json_as(
        self,
        provider: Provider,
        model: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int = 1200,
        image_paths: list[Path] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """用显式 provider/model 调用模型，供独立审核流程使用。

        该方法不会修改生成模型下拉框的全局选择，避免“切换审核模型”意外影响下一道题。
        """
        images = image_paths or []
        if provider == "mock":
            raise RuntimeError("Mock 模式不调用模型")
        started = time.perf_counter()
        log_event("model.review.started", provider=provider, model=model, image_count=len(images))
        try:
            if provider == "ollama":
                result = self._ollama_json(model, prompt, schema, max_tokens, images)
            else:
                result = self._codex_json(model, prompt, schema, images)
        except Exception as error:
            log_event(
                "model.review.failed",
                level=30,
                provider=provider,
                model=model,
                image_count=len(images),
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                error_type=type(error).__name__,
                error=str(error)[:300],
                exc_info=True,
            )
            raise
        log_event(
            "model.review.completed",
            provider=provider,
            model=model,
            image_count=len(images),
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return result, {
            "requestedProvider": provider,
            "provider": provider,
            "model": model,
            "fallback": False,
        }

    def _ollama_json(
        self,
        model: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int,
        image_paths: list[Path] | None = None,
    ) -> dict[str, Any]:
        user_message: dict[str, Any] = {"role": "user", "content": prompt}
        if image_paths:
            user_message["images"] = [
                base64.b64encode(path.read_bytes()).decode("ascii")
                for path in image_paths
            ]
        request_payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "format": schema,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是严谨的中文中学辅导老师。只输出符合 JSON Schema 的 JSON；"
                        "不输出 Markdown，不编造教材中没有的条件。"
                    ),
                },
                user_message,
            ],
            "options": {
                "temperature": 0.2,
                "num_ctx": 8192,
                "num_predict": max_tokens,
            },
        }

        def send() -> dict[str, Any]:
            request = urllib.request.Request(
                f"{OLLAMA_BASE_URL}/api/chat",
                data=json.dumps(request_payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    response_payload = json.load(response)
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Ollama 请求失败：{detail[:500]}") from error
            except (OSError, ValueError, urllib.error.URLError) as error:
                raise RuntimeError(f"无法连接 Ollama：{error}") from error
            if not isinstance(response_payload, dict):
                raise RuntimeError("Ollama 返回值不是 JSON 对象")
            return response_payload

        try:
            payload = send()
        except RuntimeError as error:
            # 部分旧版 Ollama/llama.cpp 无法把复杂 JSON Schema 转成 grammar。
            # 此时退到普通 JSON 模式，并把 Schema 写入系统提示；兼容性提高，但后续
            # 仍必须经过 Pydantic 和确定性质量门禁，不能把它当成可信结构。
            if "grammar" not in str(error).lower() and "sampler" not in str(error).lower():
                raise
            request_payload["format"] = "json"
            schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            request_payload["messages"][0]["content"] += (
                "\n当前运行时不支持直接加载复杂 Schema，请仍严格使用以下字段结构：\n"
                + schema_text
            )
            payload = send()
        content = payload.get("message", {}).get("content", "")
        return parse_json_object(content)

    def _codex_json(
        self,
        model: str,
        prompt: str,
        schema: dict[str, Any],
        image_paths: list[Path] | None = None,
    ) -> dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="dotty-codex-") as directory:
            root = Path(directory)
            schema_path = root / "schema.json"
            output_path = root / "response.json"
            schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
            command = [
                codex_command(), "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox", "read-only",
                "--skip-git-repo-check",
                "--output-schema", str(schema_path),
                "--output-last-message", str(output_path),
                "--cd", str(root),
            ]
            if model != "default":
                command.extend(["--model", model])
            for index, image_path in enumerate(image_paths or []):
                copied_image = root / f"review-image-{index}{image_path.suffix.lower()}"
                shutil.copy2(image_path, copied_image)
                # --image 接受可变数量参数；使用等号形式可以防止它吞掉后面的 stdin 标记 "-"。
                command.append(f"--image={copied_image}")
            full_prompt = (
                "不要调用任何工具。你是严谨的中文中学辅导老师。"
                "只按照给定 JSON Schema 输出，不要输出 Markdown。\n\n" + prompt
            )
            command.append("-")
            try:
                completed = subprocess.run(
                    command,
                    input=full_prompt,
                    capture_output=True,
                    text=True,
                    timeout=240,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise RuntimeError(f"Codex 运行失败：{error}") from error
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip()
                raise RuntimeError(f"Codex 返回错误：{detail[-800:]}")
            if not output_path.exists():
                raise RuntimeError("Codex 没有生成结构化输出")
            return parse_json_object(output_path.read_text(encoding="utf-8"))


def parse_json_object(content: str) -> dict[str, Any]:
    """解析模型输出，并拒绝数组、纯文本和不完整 JSON。"""
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"模型没有返回有效 JSON：{cleaned[:300]}") from error
    if not isinstance(result, dict):
        raise RuntimeError("模型返回值不是 JSON 对象")
    return result


runtime = ModelRuntime()
