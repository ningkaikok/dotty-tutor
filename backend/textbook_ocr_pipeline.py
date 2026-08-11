"""教材 PDF 的页面路由、局部升级与 OCR 中间结果复用。

该模块位于 Provider 适配器和教材业务编排之间：它不生成题目，也不持久化课程，
只负责把一段 PDF 稳定地还原为 Markdown。自动模式先读取廉价的 PDF 文字层，
再把扫描页、公式页或质量门禁不合格的页局部升级到 MinerU，避免整本书统一跑
昂贵 OCR。
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from ocr_pipeline import (
    OcrResultCache,
    build_ocr_cache_key,
    choose_ocr_provider,
    probe_page,
)
from ocr_quality import MAX_OCR_RETRIES, evaluate_page_quality, evaluate_question_quality
from question_source import split_question_sources


OCR_PIPELINE_VERSION = "page-routing-v1"


def _safe_page_text(page: Any) -> str:
    """读取损坏页面时返回空文本，让质量门禁决定是否升级 OCR。"""
    try:
        return (page.extract_text() or "").strip()
    except Exception:
        return ""


def _safe_image_count(page: Any) -> int:
    """图片计数只是路由信号，解析失败不应阻塞整份 PDF。"""
    try:
        return len(page.images)
    except Exception:
        return 0


def _group_routes(routes: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """把相邻且 Provider 相同的页面合并，减少 MinerU 子进程启动次数。"""
    groups: list[list[dict[str, Any]]] = []
    for route in routes:
        if groups and groups[-1][-1]["provider"] == route["provider"]:
            groups[-1].append(route)
        else:
            groups.append([route])
    return groups


def _cache_key(
    content_hash: str,
    start_page: int,
    end_page: int,
    provider: str,
) -> str:
    return build_ocr_cache_key(
        content_hash,
        start_page=start_page,
        end_page=end_page,
        provider=provider,  # type: ignore[arg-type]
        provider_version=OCR_PIPELINE_VERSION,
    )


def _run_span(
    *,
    runtime: Any,
    cache: OcrResultCache,
    content_hash: str,
    source_path: Path,
    routes: list[dict[str, Any]],
    asset_dir: Path,
    asset_url_prefix: str,
) -> tuple[str, dict[str, Any]]:
    """执行一个连续页段；命中缓存时不会再次启动 Provider。"""
    provider = routes[0]["provider"]
    start_page = routes[0]["pageIndex"]
    end_page = routes[-1]["pageIndex"]
    key = _cache_key(content_hash, start_page, end_page, provider)
    cached = cache.load(key)
    if cached:
        run = dict(cached.metadata)
        run.update({"cacheHit": True, "cacheKey": key, "imageUrls": list(cached.image_urls)})
        return cached.markdown, run

    if provider == "mineru":
        markdown, run = runtime.parse(
            source_path,
            start_page,
            end_page,
            asset_dir,
            asset_url_prefix,
        )
    else:
        markdown = "\n\n".join(
            f"<!-- page {route['pageNumber']} -->\n{route['text']}"
            for route in routes
            if route["text"]
        )
        run = {
            "requestedProvider": runtime.selection.provider,
            "provider": "pypdf",
            "mode": "text-layer",
            "fallback": False,
            "output": "text",
            "startPage": start_page + 1,
            "endPage": end_page + 1,
            "imageUrls": [],
        }

    run.update({"cacheHit": False, "cacheKey": key, "pipelineVersion": OCR_PIPELINE_VERSION})
    cache.save(
        key,
        markdown=markdown,
        image_urls=run.get("imageUrls", []),
        metadata=run,
    )
    return markdown, run


def resolve_routed_ocr_source(
    *,
    runtime: Any,
    source_text: str,
    source_path: Path,
    start_page: int,
    end_page: int,
    asset_dir: Path,
    asset_url_prefix: str,
    cache_dir: Path,
    content_hash: str,
) -> tuple[str, dict[str, Any]]:
    """按页解析 PDF，并返回向前兼容的 ``ocrRun`` 审计记录。

    显式粘贴文本仍具有最高优先级。显式选择 pypdf 时不会擅自调用 MinerU；只有
    ``auto`` 模式会在质量门禁失败后局部升级。显式选择 MinerU 时按整个连续页段
    执行，以尊重内容生产者的选择。
    """
    if source_text.strip():
        return source_text.strip(), {
            "requestedProvider": "manual",
            "provider": "manual",
            "mode": "pasted-text",
            "fallback": False,
            "output": "text",
            "cacheHit": False,
            "pageRoutes": [],
            "quality": [],
            "retries": [],
        }

    reader = PdfReader(str(source_path))
    requested = runtime.selection.provider
    mineru_available = bool(runtime.mineru_command())
    routes: list[dict[str, Any]] = []
    retries: list[dict[str, Any]] = []
    for page_index in range(start_page, end_page + 1):
        page = reader.pages[page_index]
        text = _safe_page_text(page)
        probe = probe_page(text, image_count=_safe_image_count(page))
        provider = choose_ocr_provider(
            probe,
            requested_provider=requested,
            mineru_available=mineru_available,
        )
        quality = evaluate_page_quality(
            text,
            page_number=page_index + 1,
            provider="pypdf",
        )
        # 自动模式只升级门禁失败的页面。显式 pypdf 常用于无 MinerU 环境，不能
        # 因为空文字层而偷偷改变用户选择。
        if requested == "auto" and mineru_available and quality["status"] == "retry":
            provider = "mineru"
            retries.append({
                **quality,
                "fromProvider": "pypdf",
                "toProvider": "mineru",
            })
        routes.append({
            "pageIndex": page_index,
            "pageNumber": page_index + 1,
            "provider": provider,
            "text": text,
            "probe": asdict(probe),
            "preflightQuality": quality,
        })

    cache = OcrResultCache(cache_dir)
    markdown_parts: list[str] = []
    span_runs: list[dict[str, Any]] = []
    for group in _group_routes(routes):
        try:
            markdown, span_run = _run_span(
                runtime=runtime,
                cache=cache,
                content_hash=content_hash,
                source_path=source_path,
                routes=group,
                asset_dir=asset_dir,
                asset_url_prefix=asset_url_prefix,
            )
        except Exception as error:
            # OCR 失败时保留该页已有文字层，发布门禁仍会阻止空白或损坏题目进入学生端。
            markdown = "\n\n".join(route["text"] for route in group if route["text"])
            span_run = {
                "requestedProvider": requested,
                "provider": "pypdf" if markdown else "none",
                "mode": "text-layer-fallback" if markdown else "ocr-failed",
                "fallback": True,
                "error": str(error),
                "output": "text",
                "startPage": group[0]["pageNumber"],
                "endPage": group[-1]["pageNumber"],
                "imageUrls": [],
                "cacheHit": False,
            }
        if markdown.strip():
            markdown_parts.append(
                f"<!-- pages {group[0]['pageNumber']}-{group[-1]['pageNumber']} -->\n{markdown.strip()}"
            )
        quality = evaluate_page_quality(
            markdown,
            page_number=group[0]["pageNumber"],
            provider=span_run.get("provider", "none"),
            # MinerU 已是当前最高保真 Provider；仍失败应隔离，不能无限重跑。
            retry_count=MAX_OCR_RETRIES if span_run.get("provider") == "mineru" else 0,
        )
        span_run["quality"] = quality
        span_runs.append(span_run)

    lesson_source = "\n\n".join(markdown_parts)[:40_000]
    asset_dir.mkdir(parents=True, exist_ok=True)
    (asset_dir / "source.md").write_text(lesson_source, encoding="utf-8")
    providers = list(dict.fromkeys(run.get("provider", "none") for run in span_runs))
    image_urls = [
        url
        for run in span_runs
        for url in run.get("imageUrls", [])
        if isinstance(url, str)
    ]
    provider = providers[0] if len(providers) == 1 else "hybrid"
    question_quality = [
        evaluate_question_quality(
            block,
            page_number=start_page + 1,
            question_number=number or None,
            provider=provider,
            expected_images=images,
        )
        for number, block, images in split_question_sources(lesson_source)
    ]
    return lesson_source, {
        "requestedProvider": requested,
        "provider": provider,
        "mode": "page-routing",
        "fallback": any(run.get("fallback") for run in span_runs),
        "output": "markdown" if "mineru" in providers else "text",
        "startPage": start_page + 1,
        "endPage": end_page + 1,
        "imageUrls": list(dict.fromkeys(image_urls)),
        "cacheHit": bool(span_runs) and all(run.get("cacheHit") for run in span_runs),
        "pageRoutes": [
            {key: value for key, value in route.items() if key != "text"}
            for route in routes
        ],
        "quality": [run["quality"] for run in span_runs],
        "questionQuality": question_quality,
        "retries": retries,
        "spans": span_runs,
        "pipelineVersion": OCR_PIPELINE_VERSION,
    }
