"""教材 PDF 的页面路由、局部升级与 OCR 中间结果复用。

该模块位于 Provider 适配器和教材业务编排之间：它不生成题目，也不持久化课程，
只负责把一段 PDF 稳定地还原为 Markdown。自动模式先读取廉价的 PDF 文字层，
再把扫描页、公式页或质量门禁不合格的页局部升级到 MinerU，避免整本书统一跑
昂贵 OCR。
"""

from __future__ import annotations

import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from ocr_pipeline import (
    OcrResultCache,
    build_ocr_cache_key,
    choose_ocr_provider,
    probe_page,
    has_visual_hint,
)
from ocr_quality import MAX_OCR_RETRIES, evaluate_page_quality, evaluate_question_quality
from domain.questions.source import (
    QUESTION_SEGMENTATION_VERSION,
    QUESTION_START_PATTERN,
    split_question_sources,
)
from infrastructure.runtime.contracts import RuntimeConfigSnapshot, attach_runtime_config


# 渲染矢量页图属于缓存结果的一部分；升级版本可以避免旧缓存继续沿用“只有文字”的结果。
OCR_PIPELINE_VERSION = "page-routing-v2"


def _with_ocr_config(run: dict[str, Any], *, provider: str, prompt: str = "page-routing") -> dict[str, Any]:
    """Attach an audit identity while retaining the existing OCR run fields."""
    return attach_runtime_config(
        run,
        RuntimeConfigSnapshot(
            provider=provider,
            runtime="ocr",
            schema=OCR_PIPELINE_VERSION,
            prompt=prompt,
            timeout=900.0,
        ),
    )


def _inject_page_render(markdown: str, page_number: int, reference: str) -> str:
    """把矢量页渲染图放入该页明确依赖图形的题块，而不是页尾。"""
    marker = re.compile(rf"<!--\s*page\s+{page_number}\s*-->", re.IGNORECASE)
    match = marker.search(markdown)
    if not match:
        # MinerU 的 Markdown 版本不一定保留页标记。仍按题号和视觉提示寻找
        # 尚未绑定图片的题块；这是比把图片直接丢到文末更可靠的保底策略。
        section = markdown
        question_matches = list(QUESTION_START_PATTERN.finditer(section))
        for index in range(len(question_matches) - 1, -1, -1):
            question_match = question_matches[index]
            block_end = question_matches[index + 1].start() if index + 1 < len(question_matches) else len(section)
            block = section[question_match.start():block_end]
            if not has_visual_hint(block) or "![](" in block:
                continue
            return section[:block_end] + f"\n\n![]({reference})\n\n" + section[block_end:]
        return markdown
    next_marker = re.search(r"<!--\s*page\s+\d+\s*-->", markdown[match.end():], re.IGNORECASE)
    section_end = match.end() + next_marker.start() if next_marker else len(markdown)
    section = markdown[match.end():section_end]
    question_matches = list(QUESTION_START_PATTERN.finditer(section))
    injected = False
    # 从后往前插入，保持前面题目的字符偏移稳定；同页有多道“如图”题时，每题
    # 都需要拿到该页渲染图，而不是只给第一题绑定。
    for index in range(len(question_matches) - 1, -1, -1):
        question_match = question_matches[index]
        block_end = question_matches[index + 1].start() if index + 1 < len(question_matches) else len(section)
        block = section[question_match.start():block_end]
        if not has_visual_hint(block) or "![](" in block:
            continue
        # 保留题号所在行的换行，否则后一个“4．”会紧贴图片语法，题目切分器
        # 无法再把它识别为新题。
        section = section[:block_end] + f"\n\n![]({reference})\n\n" + section[block_end:]
        injected = True
    return markdown[:match.end()] + section + markdown[section_end:] if injected else markdown


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
    refresh: bool = False,
) -> tuple[str, dict[str, Any]]:
    """执行一个连续页段；默认复用缓存，显式刷新时才重新启动 Provider。"""
    provider = routes[0]["provider"]
    start_page = routes[0]["pageIndex"]
    end_page = routes[-1]["pageIndex"]
    key = _cache_key(content_hash, start_page, end_page, provider)
    cached = None if refresh else cache.load(key)
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
    refresh: bool = False,
) -> tuple[str, dict[str, Any]]:
    """按页解析 PDF，并返回当前契约的 ``ocrRun`` 审计记录。

    显式粘贴文本仍具有最高优先级。显式选择 pypdf 时不会擅自调用 MinerU；只有
    ``auto`` 模式会在质量门禁失败后局部升级。显式选择 MinerU 时按整个连续页段
    执行，以尊重内容生产者的选择。
    """
    if source_text.strip():
        return source_text.strip(), _with_ocr_config({
            "requestedProvider": "manual",
            "provider": "manual",
            "mode": "pasted-text",
            "fallback": False,
            "output": "text",
            "cacheHit": False,
            "pageRoutes": [],
            "quality": [],
            "retries": [],
            "questionSegmentationVersion": QUESTION_SEGMENTATION_VERSION,
        }, provider="manual", prompt="pasted-text")

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
                refresh=refresh,
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
            span_run["cacheKey"] = _cache_key(
                content_hash,
                group[0]["pageIndex"],
                group[-1]["pageIndex"],
                span_run["provider"],
            )
        quality = evaluate_page_quality(
            markdown,
            page_number=group[0]["pageNumber"],
            provider=span_run.get("provider", "none"),
            # MinerU 已是当前最高保真 Provider；仍失败应隔离，不能无限重跑。
            retry_count=MAX_OCR_RETRIES if span_run.get("provider") == "mineru" else 0,
        )
        span_run["quality"] = quality
        render_page = getattr(runtime, "render_page_image", None)
        if not span_run.get("imageUrls") and callable(render_page):
            rendered_urls: list[str] = []
            for route in group:
                if not has_visual_hint(route.get("text", "")):
                    continue
                rendered = render_page(
                    source_path, route["pageNumber"], asset_dir, asset_url_prefix
                )
                if rendered:
                    reference, url = rendered
                    markdown = _inject_page_render(markdown, route["pageNumber"], reference)
                    rendered_urls.append(url)
            if rendered_urls:
                span_run["imageUrls"] = rendered_urls
                span_run["renderedPageImages"] = rendered_urls
                # 页面渲染图也是 OCR 中间结果；写回同一个内容寻址缓存，后续批次只需
                # 复用已存在的 PNG，不会因为命中文字缓存而重复判断或丢失图片绑定。
                cache.save(
                    span_run["cacheKey"],
                    markdown=markdown,
                    image_urls=rendered_urls,
                    metadata=span_run,
                )
        if markdown.strip():
            markdown_parts.append(
                f"<!-- pages {group[0]['pageNumber']}-{group[-1]['pageNumber']} -->\n{markdown.strip()}"
            )
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
        for number, block, images in split_question_sources(lesson_source, asset_dir=asset_dir)
    ]
    return lesson_source, _with_ocr_config({
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
        "questionSegmentationVersion": QUESTION_SEGMENTATION_VERSION,
    }, provider=provider)
