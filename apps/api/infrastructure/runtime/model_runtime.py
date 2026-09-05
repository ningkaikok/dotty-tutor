"""题目生成模型的统一适配层。

本模块把 Ollama、Codex CLI 和 Mock 暴露为相同的 JSON 生成接口。业务层只关心
``generate_json`` 返回的结构化数据，不需要知道 HTTP、子进程或模型登录细节。

需要特别注意：这里的 ``selection`` 是进程级演示配置，不是用户偏好。生产环境如果需要
多租户，应把模型选择放入请求上下文或租户配置，不能继续修改这个全局对象。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from infrastructure.runtime.capabilities import HEALTH_BOOK
from infrastructure.runtime.contracts import (
    PromptParts,
    RuntimeConfigSnapshot,
    RuntimeExecutionError,
    attach_runtime_config,
)
from observability import log_event

Provider = Literal["ollama", "codex", "mock"]
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")


def codex_command() -> str:
    """返回 Codex CLI 路径。

    macOS 桌面应用可能把 CLI 放在应用资源目录而不是 ``PATH``，因此允许通过
    ``CODEX_COMMAND`` 显式指定，Docker 中也会据此判断宿主机能力是否可见。
    """
    configured = os.getenv("CODEX_COMMAND", "").strip()
    if configured:
        return configured
    # Desktop installs do not always expose the bundled CLI on PATH.
    return shutil.which("codex") or "/Applications/ChatGPT.app/Contents/Resources/codex"


def codex_models() -> list[str]:
    """Return the subscription models exposed by the local Codex CLI."""
    configured = os.getenv("CODEX_MODELS", "").strip()
    if configured:
        return [item.strip() for item in configured.split(",") if item.strip()]
    return ["default", "gpt-5.6-sol", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.5", "gpt-5.4"]


@dataclass
class ModelSelection:
    provider: Provider
    model: str


def _prompt_split_chars(prompt: str | PromptParts) -> tuple[int | None, int | None]:
    """返回稳定段/动态段字符数；未切分的调用返回 (None, None)。"""
    if isinstance(prompt, PromptParts):
        return len(prompt.stable), len(prompt.dynamic)
    return None, None


class ModelRuntime:
    """管理生成模型目录、当前选择和结构化调用。

    Catalog 方法可以修正已经失效的选择；实际生成前会复制一次选择快照，避免长请求执行
    期间用户切换下拉框而让日志中的 provider/model 与真实调用不一致。
    """

    def __init__(self, *, env_prefix: str = "", metrics_store: Any = None) -> None:
        """Create a runtime with an independent environment/config namespace.

        ``MODEL_*`` controls content generation. The tutor passes ``TUTOR_*`` so
        changing the student-facing陪练 model cannot silently alter textbook
        generation or review jobs.
        """
        provider_name = f"{env_prefix}MODEL_PROVIDER" if env_prefix else "MODEL_PROVIDER"
        model_name = f"{env_prefix}MODEL_NAME" if env_prefix else "MODEL_NAME"
        self.selection = ModelSelection(
            provider=os.getenv(provider_name, "codex"),  # type: ignore[arg-type]
            model=os.getenv(model_name, "default"),
        )
        # 调用边界指标（roadmap T2）：只追加写入；存储缺失时为 no-op。
        self.metrics_store = metrics_store
        self.runtime_name = "tutoring" if env_prefix else "generation"

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
        """探测可用后端，但不修改当前生成模型选择。

        ``models`` 保持字符串列表（既有契约）；能力与健康元数据放在平行的
        ``modelDetails`` 里，界面和调用方按需取用，互不干扰。
        """
        from infrastructure.runtime.capabilities import annotate_model_entry

        local_models, ollama_error = self.ollama_models()
        codex_binary = codex_command()
        codex_available = bool(shutil.which(codex_binary) or Path(codex_binary).is_file())
        provider_specs = [
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
                "models": codex_models(),
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
        for spec in provider_specs:
            spec["modelDetails"] = [
                annotate_model_entry(spec["id"], model) for model in spec["models"]
            ]
        return provider_specs

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

    def config_snapshot(
        self,
        provider: str,
        model: str,
        *,
        schema: dict[str, Any] | None = None,
        prompt: str | None = None,
        runtime_name: str = "generation",
    ) -> RuntimeConfigSnapshot:
        """Create a content-free snapshot shared by generation and review calls."""
        timeout = 180.0 if provider == "ollama" else 240.0
        return RuntimeConfigSnapshot.for_model(
            provider,
            model,
            schema=schema,
            prompt=prompt,
            runtime=runtime_name,
            timeout=timeout,
        )

    def _record_metric(
        self,
        *,
        task: str,
        provider: str,
        model: str,
        started: float,
        status: str,
        error_type: str | None = None,
        usage: dict[str, Any] | None = None,
        prompt_chars: int | None = None,
        max_tokens: int | None = None,
        provider_attempts: int = 1,
        schema_fallback: bool = False,
        stable_prompt_chars: int | None = None,
        dynamic_prompt_chars: int | None = None,
    ) -> None:
        """把一次调用写入边界指标；存储异常绝不影响主流程。"""
        if self.metrics_store is None:
            return
        entry = {
            "runtime": self.runtime_name,
            "task": task,
            "provider": provider,
            "model": model,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "prompt_chars": int(prompt_chars or 0),
            "max_output_tokens": max_tokens,
            "prompt_tokens": usage.get("prompt_tokens") if usage else None,
            "output_tokens": usage.get("output_tokens") if usage else None,
            "status": status,
            "error_type": error_type,
            "provider_attempts": provider_attempts,
            "schema_fallback": schema_fallback,
            # 未做前缀切分的调用写 None 而不是 0：0 会被平均值当成"稳定段长度为零"，
            # 把没测过的调用算进分母，直接压低稳定占比。
            "stable_prompt_chars": stable_prompt_chars,
            "dynamic_prompt_chars": dynamic_prompt_chars,
        }
        try:
            self.metrics_store.record(entry)
        except Exception as record_error:  # noqa: BLE001
            log_event("model.metrics.record_failed", level=30, error=str(record_error)[:200])

    def generate_json(
        self,
        prompt: str | PromptParts,
        schema: dict[str, Any],
        max_tokens: int = 1200,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """用当前生成模型返回满足 Schema 的对象及可追踪运行记录。

        传入 ``PromptParts`` 时额外记录稳定段/动态段的字符数，用于判断 Prefix Cache
        是否值得做；传入普通字符串时两个字段记 None（不适用），行为不变。
        """
        stable_chars, dynamic_chars = _prompt_split_chars(prompt)
        prompt = prompt.text if isinstance(prompt, PromptParts) else prompt
        selection = ModelSelection(self.selection.provider, self.selection.model)
        if selection.provider == "mock":
            raise RuntimeError("Mock 模式不调用模型")
        snapshot = self.config_snapshot(
            selection.provider,
            selection.model,
            schema=schema,
            prompt=prompt,
            runtime_name="generation",
        )
        started = time.perf_counter()
        prompt_chars = len(prompt)
        log_event(
            "model.request.started",
            provider=selection.provider,
            model=selection.model,
            prompt_chars=prompt_chars,
            max_output_tokens=max_tokens,
        )
        try:
            if selection.provider == "ollama":
                result, usage = self._ollama_json(selection.model, prompt, schema, max_tokens)
            else:
                result, usage = self._codex_json(selection.model, prompt, schema)
        except Exception as error:
            execution_error = error if isinstance(error, RuntimeExecutionError) else RuntimeExecutionError(
                f"模型调用失败：{error}", snapshot=snapshot, cause=error
            )
            failed_run = self._build_run(
                requested_provider=selection.provider,
                provider=selection.provider,
                model=selection.model,
                started=started,
                prompt_chars=prompt_chars,
                max_tokens=max_tokens,
                usage=None,
                provider_attempts=self._provider_attempts(error),
                schema_fallback=self._schema_fallback(error),
            )
            attach_runtime_config(failed_run, snapshot)
            execution_error.runtime_run = failed_run
            HEALTH_BOOK.mark_failure(
                selection.provider, selection.model, str(execution_error)
            )
            self._record_metric(
                task="generation",
                provider=selection.provider,
                model=selection.model,
                started=started,
                status="failed",
                error_type=type(execution_error).__name__,
                prompt_chars=prompt_chars,
                max_tokens=max_tokens,
                provider_attempts=failed_run["providerAttempts"],
                schema_fallback=failed_run["schemaFallback"]["used"],
                stable_prompt_chars=stable_chars,
                dynamic_prompt_chars=dynamic_chars,
            )
            log_event(
                "model.request.failed",
                level=40,
                provider=selection.provider,
                model=selection.model,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                error_type=type(execution_error).__name__,
                error=str(execution_error)[:300],
                exc_info=True,
            )
            raise execution_error from error
        HEALTH_BOOK.mark_success(selection.provider, selection.model)
        self._record_metric(
            task="generation",
            provider=selection.provider,
            model=selection.model,
            started=started,
            status="succeeded",
            usage=usage,
            prompt_chars=prompt_chars,
            max_tokens=max_tokens,
            provider_attempts=self._provider_attempts_from_usage(usage),
            schema_fallback=self._schema_fallback_from_usage(usage)["used"],
            stable_prompt_chars=stable_chars,
            dynamic_prompt_chars=dynamic_chars,
        )
        log_event(
            "model.request.completed",
            provider=selection.provider,
            model=selection.model,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        run = self._build_run(
            requested_provider=selection.provider,
            provider=selection.provider,
            model=selection.model,
            started=started,
            prompt_chars=prompt_chars,
            max_tokens=max_tokens,
            usage=usage,
            provider_attempts=self._provider_attempts_from_usage(usage),
            schema_fallback=self._schema_fallback_from_usage(usage),
        )
        attach_runtime_config(run, snapshot)
        return result, run

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
        snapshot = self.config_snapshot(
            provider,
            model,
            schema=schema,
            prompt=prompt,
            runtime_name="review",
        )
        started = time.perf_counter()
        prompt_chars = len(prompt)
        log_event(
            "model.review.started",
            provider=provider,
            model=model,
            image_count=len(images),
            prompt_chars=prompt_chars,
            max_output_tokens=max_tokens,
        )
        try:
            if provider == "ollama":
                result, usage = self._ollama_json(model, prompt, schema, max_tokens, images)
            else:
                result, usage = self._codex_json(model, prompt, schema, images)
        except Exception as error:
            execution_error = error if isinstance(error, RuntimeExecutionError) else RuntimeExecutionError(
                f"模型审核调用失败：{error}", snapshot=snapshot, cause=error
            )
            failed_run = self._build_run(
                requested_provider=provider,
                provider=provider,
                model=model,
                started=started,
                prompt_chars=prompt_chars,
                max_tokens=max_tokens,
                usage=None,
                provider_attempts=self._provider_attempts(error),
                schema_fallback=self._schema_fallback(error),
            )
            attach_runtime_config(failed_run, snapshot)
            execution_error.runtime_run = failed_run
            HEALTH_BOOK.mark_failure(provider, model, str(execution_error))
            self._record_metric(
                task="review",
                provider=provider,
                model=model,
                started=started,
                status="failed",
                error_type=type(execution_error).__name__,
                prompt_chars=prompt_chars,
                max_tokens=max_tokens,
                provider_attempts=failed_run["providerAttempts"],
                schema_fallback=failed_run["schemaFallback"]["used"],
            )
            log_event(
                "model.review.failed",
                level=30,
                provider=provider,
                model=model,
                image_count=len(images),
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                error_type=type(execution_error).__name__,
                error=str(execution_error)[:300],
                exc_info=True,
            )
            raise execution_error from error
        HEALTH_BOOK.mark_success(provider, model)
        self._record_metric(
            task="review",
            provider=provider,
            model=model,
            started=started,
            status="succeeded",
            usage=usage,
            prompt_chars=prompt_chars,
            max_tokens=max_tokens,
            provider_attempts=self._provider_attempts_from_usage(usage),
            schema_fallback=self._schema_fallback_from_usage(usage)["used"],
        )
        log_event(
            "model.review.completed",
            provider=provider,
            model=model,
            image_count=len(images),
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        run = self._build_run(
            requested_provider=provider,
            provider=provider,
            model=model,
            started=started,
            prompt_chars=prompt_chars,
            max_tokens=max_tokens,
            usage=usage,
            provider_attempts=self._provider_attempts_from_usage(usage),
            schema_fallback=self._schema_fallback_from_usage(usage),
        )
        attach_runtime_config(run, snapshot)
        return result, run

    @staticmethod
    def _provider_attempts_from_usage(usage: dict[str, Any] | None) -> int:
        if not isinstance(usage, dict):
            return 1
        value = usage.get("providerAttempts", 1)
        return value if isinstance(value, int) and value > 0 else 1

    @staticmethod
    def _schema_fallback_from_usage(usage: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(usage, dict):
            return {"used": False, "reason": None}
        value = usage.get("schemaFallback")
        if isinstance(value, dict):
            return {"used": bool(value.get("used")), "reason": value.get("reason")}
        return {"used": False, "reason": None}

    @staticmethod
    def _provider_attempts(error: Exception) -> int:
        value = getattr(error, "provider_attempts", 1)
        return value if isinstance(value, int) and value > 0 else 1

    @staticmethod
    def _schema_fallback(error: Exception) -> dict[str, Any]:
        value = getattr(error, "schema_fallback", None)
        if isinstance(value, dict):
            return {"used": bool(value.get("used")), "reason": value.get("reason")}
        return {"used": False, "reason": None}

    @staticmethod
    def _attach_provider_metadata(
        error: Exception,
        provider_attempts: int,
        used: bool,
        reason: str | None,
    ) -> None:
        # Adapter errors are internal and are converted to RuntimeExecutionError
        # by the caller; these attributes carry only safe execution metadata.
        error.provider_attempts = provider_attempts  # type: ignore[attr-defined]
        error.schema_fallback = {"used": used, "reason": reason}  # type: ignore[attr-defined]

    @staticmethod
    def _build_run(
        *,
        requested_provider: str,
        provider: str,
        model: str,
        started: float,
        prompt_chars: int,
        max_tokens: int,
        usage: dict[str, Any] | None,
        provider_attempts: int,
        schema_fallback: dict[str, Any],
    ) -> dict[str, Any]:
        usage = usage or {}
        return {
            "requestedProvider": requested_provider,
            "provider": provider,
            "model": model,
            "fallback": False,
            "promptChars": prompt_chars,
            "maxOutputTokens": max_tokens,
            "durationMs": round((time.perf_counter() - started) * 1000, 1),
            "usage": {
                "promptTokens": usage.get("prompt_tokens"),
                "outputTokens": usage.get("output_tokens"),
            },
            "providerAttempts": provider_attempts,
            "schemaFallback": schema_fallback,
        }

    def _ollama_json(
        self,
        model: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int,
        image_paths: list[Path] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
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

        provider_attempts = 0

        def send_with_attempt() -> dict[str, Any]:
            nonlocal provider_attempts
            provider_attempts += 1
            return send()

        schema_fallback = False
        fallback_reason: str | None = None
        try:
            payload = send_with_attempt()
        except RuntimeError as error:
            # 部分 Ollama/llama.cpp 运行时无法把复杂 JSON Schema 转成 grammar。
            # 此时退到普通 JSON 模式，并把 Schema 写入系统提示；可用性提高，但后续
            # 仍必须经过 Pydantic 和确定性质量门禁，不能把它当成可信结构。
            if "grammar" not in str(error).lower() and "sampler" not in str(error).lower():
                self._attach_provider_metadata(error, provider_attempts, False, None)
                raise
            schema_fallback = True
            fallback_reason = type(error).__name__
            request_payload["format"] = "json"
            schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            request_payload["messages"][0]["content"] += (
                "\n当前运行时不支持直接加载复杂 Schema，请仍严格使用以下字段结构：\n"
                + schema_text
            )
            try:
                payload = send_with_attempt()
            except RuntimeError as retry_error:
                self._attach_provider_metadata(
                    retry_error, provider_attempts, True, fallback_reason
                )
                raise
        # Ollama 原生透出 token 计数；Codex 路径暂无对应字段，由调用方记 None。
        usage = {
            "prompt_tokens": payload.get("prompt_eval_count"),
            "output_tokens": payload.get("eval_count"),
            "providerAttempts": provider_attempts,
            "schemaFallback": {
                "used": schema_fallback,
                "reason": fallback_reason,
            },
        }
        content = payload.get("message", {}).get("content", "")
        try:
            parsed = parse_json_object(content)
        except RuntimeError as error:
            self._attach_provider_metadata(
                error, provider_attempts, schema_fallback, fallback_reason
            )
            raise
        return parsed, usage

    def _codex_json(
        self,
        model: str,
        prompt: str,
        schema: dict[str, Any],
        image_paths: list[Path] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
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
            # Codex CLI 不透出 token 计数；记 None 而不是用字符数冒充。
            return parse_json_object(output_path.read_text(encoding="utf-8")), {
                "prompt_tokens": None,
                "output_tokens": None,
                "providerAttempts": 1,
                "schemaFallback": {"used": False, "reason": None},
            }


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
