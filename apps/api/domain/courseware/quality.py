"""Deterministic fail-closed checks shared by review and publication gates."""

from __future__ import annotations

import re
from typing import Any

BLOCKING_SOURCE_FLAGS = frozenset({"missing_page", "unreadable", "missing_conditions", "wrong_figure"})


def source_readiness_issues(pages: list[dict[str, Any]]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for page in pages:
        number = int(page.get("page", 0))
        flags = set(page.get("flags") or []) & BLOCKING_SOURCE_FLAGS
        if not str(page.get("text") or "").strip():
            flags.add("unreadable")
        for flag in sorted(flags):
            issues.append({"code": flag, "message": f"来源第 {number} 页需要先核验：{flag}"})
    return issues


def document_page_issues(document: dict[str, Any], pages: list[dict[str, Any]]) -> list[dict[str, str | None]]:
    """Return actionable structural issues; semantic fidelity still needs a teacher."""
    issues: list[dict[str, str | None]] = []
    page_map = {int(item["page"]): item for item in pages}
    assets = {str(item.get("assetId")): item for item in document.get("assets", [])}
    for page in document.get("pages", []):
        page_id = str(page.get("pageId", ""))
        if page.get("generationStatus") != "completed":
            issues.append({"code": "page_not_generated", "message": "页面尚未完成生成", "pageId": page_id})
        if not page.get("blocks"):
            issues.append({"code": "page_empty", "message": "页面没有可审核的展示内容", "pageId": page_id})
        refs = page.get("sourceRefs") or []
        if not refs:
            issues.append({"code": "page_source_missing", "message": "页面缺少教材来源引用", "pageId": page_id})
        ref_pages = {int(ref.get("page", 0)) for ref in refs if isinstance(ref, dict)}
        if any(number not in page_map for number in ref_pages):
            issues.append({"code": "page_source_out_of_scope", "message": "页面引用超出选定来源范围", "pageId": page_id})
        for block in page.get("blocks", []):
            kind = block.get("kind")
            if kind == "activity" and not block.get("sourceRefs"):
                issues.append({"code": "activity_source_missing", "message": "互动活动缺少来源依据", "pageId": page_id})
            if kind == "text" and block.get("authorship") == "source_excerpt" and not block.get("sourceRefs"):
                issues.append({"code": "text_source_missing", "message": "教材摘录缺少来源依据", "pageId": page_id})
            if kind == "text" and block.get("authorship") == "source_excerpt" and block.get("sourceRefs"):
                # Whitespace can change during PDF extraction; wording cannot.
                excerpt = re.sub(r"\s+", " ", str(block.get("text") or "")).strip()
                cited_texts = [str(page_map.get(int(ref.get("page", 0)), {}).get("text") or "")
                               for ref in block["sourceRefs"] if isinstance(ref, dict)]
                if not excerpt or not any(excerpt in re.sub(r"\s+", " ", text).strip() for text in cited_texts):
                    issues.append({"code": "excerpt_not_verbatim", "message": "标为教材摘录的内容与引用原文不一致，请改为补充讲解或恢复原文", "pageId": page_id})
            if kind == "image":
                asset = assets.get(str(block.get("assetId")))
                if not asset or asset.get("status") != "available" or not asset.get("src"):
                    issues.append({"code": "image_asset_unavailable", "message": "图片素材没有可解析的受控资源", "pageId": page_id})
        for reveal in page.get("revealSteps", []):
            if not reveal.get("sourceRefs"):
                issues.append({"code": "reveal_source_missing", "message": "答案揭晓内容缺少来源依据", "pageId": page_id})
    return issues
