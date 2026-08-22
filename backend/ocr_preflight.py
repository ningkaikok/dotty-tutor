"""正式 OCR 之前的页面预检（脏页报告）。

roadmap T2 约束的三条边界：
1. 只使用 OCR 前就能拿到的低成本信号——pypdf 文字层和 PDF 图片计数，不调用
   任何模型；预检本身必须是毫秒级的，否则"预检省成本"就不成立。
2. 预检结果只决定路由和提示，**不删除任何页面**。空白页、出版信息页仍会进入
   后续流程并留下审计记录；页面级质量门禁、局部重试和人工修正才是最终保障。
3. 分类必须可解释：每个页面携带命中的原因列表，误判样本可以直接带着原因
   进入 Badcase 登记簿。

分类是互斥的单主类别 + 附加标志位。优先级从高到低：blank > publication-info >
formula-dense > image-mixed > question-likely > text-only；``needsVisualOcr``
是独立于主类别的路由信号（图片多、文字层稀少的扫描页），供 auto 模式决定
是否需要版面 OCR。
"""

from __future__ import annotations

import re
from typing import Any

PREFLIGHT_VERSION = "page-preflight-v1"

# 页面类别常量。中文名称只出现在报告里，代码统一用 kebab-case。
CATEGORY_BLANK = "blank"
CATEGORY_PUBLICATION = "publication-info"
CATEGORY_FORMULA_DENSE = "formula-dense"
CATEGORY_IMAGE_MIXED = "image-mixed"
CATEGORY_QUESTION_LIKELY = "question-likely"
CATEGORY_TEXT_ONLY = "text-only"

# 出版信息页的强特征：这些词几乎不会出现在题目页，而在版权页/前言里高频出现。
# 刻意不收录"出版社""目录"这类弱词——教材正文里也可能提到它们。
_PUBLICATION_MARKER = re.compile(
    r"(?:ISBN|印\s*次|印\s*张|开\s*本|版权所有|出版发行|责任编辑|定价\s*[：:]?\s*\d)"
)
# 行首题号密度：一页出现两处及以上行首 "12." / "3、" 时判为疑似题目页。
_QUESTION_NUMBER_LINE = re.compile(r"^\s*\d{1,3}\s*(?:[.．、]|题)", re.MULTILINE)
_BLANK_TEXT_THRESHOLD = 20


def classify_page(text: str, image_count: int, formula_likelihood: float) -> dict[str, Any]:
    """对单页做互斥主类别分类，返回 ``{"category", "reasons", "needsVisualOcr"}``。

    ``text`` 是 pypdf 文字层原文，``image_count`` 是该页 PDF 图片对象数，
    ``formula_likelihood`` 来自 :mod:`ocr_pipeline` 的 ``probe_page``。
    """
    stripped = (text or "").strip()
    reasons: list[str] = []
    category = CATEGORY_TEXT_ONLY

    if len(stripped) < _BLANK_TEXT_THRESHOLD and image_count == 0:
        category = CATEGORY_BLANK
        reasons.append(f"文字层不足 {_BLANK_TEXT_THRESHOLD} 字符且无图片对象")
    elif _PUBLICATION_MARKER.search(stripped):
        category = CATEGORY_PUBLICATION
        reasons.append("命中出版信息强特征词")
    elif formula_likelihood >= 0.5:
        category = CATEGORY_FORMULA_DENSE
        reasons.append(f"公式信号密度 {formula_likelihood}")
    elif image_count > 0:
        if stripped:
            category = CATEGORY_IMAGE_MIXED
            reasons.append(f"含 {image_count} 个图片对象且有 {len(stripped)} 字符文字")
        else:
            # 图片多而文字层接近空：典型扫描页。不算 blank（有内容可提取），
            # 但 auto 模式应直接走版面 OCR。
            category = CATEGORY_IMAGE_MIXED
            reasons.append("仅有图片对象、文字层接近空（疑似扫描页）")
    else:
        matches = _QUESTION_NUMBER_LINE.findall(stripped)
        if len(matches) >= 2:
            category = CATEGORY_QUESTION_LIKELY
            reasons.append(f"行首题号密度 {len(matches)} 处")
        else:
            reasons.append("无强特征信号")

    needs_visual_ocr = bool(image_count) and len(stripped) < _BLANK_TEXT_THRESHOLD
    return {
        "category": category,
        "reasons": reasons,
        "needsVisualOcr": needs_visual_ocr,
    }


def summarize_preflight(classifications: list[dict[str, Any]]) -> dict[str, Any]:
    """把逐页分类聚合为 roadmap 要求的教材级脏页摘要。

    ``processablePages`` 是"值得投入 OCR 成本"的页数：排除空白页和出版信息页，
    但**不排除**它们进入后续流程——这里只是报告数字，删除与否由人工决定。
    """
    pages_by_category: dict[str, list[int]] = {}
    for index, classification in enumerate(classifications):
        pages_by_category.setdefault(classification["category"], []).append(index + 1)

    blank_pages = pages_by_category.get(CATEGORY_BLANK, [])
    publication_pages = pages_by_category.get(CATEGORY_PUBLICATION, [])
    total = len(classifications)

    def numbers(category: str) -> list[int]:
        return pages_by_category.get(category, [])

    return {
        "totalPages": total,
        "processablePages": total - len(blank_pages) - len(publication_pages),
        "suspectedDirtyCount": len(blank_pages) + len(publication_pages),
        "blankPages": blank_pages,
        "publicationInfoPages": publication_pages,
        "formulaDensePages": numbers(CATEGORY_FORMULA_DENSE),
        "imageMixedPages": numbers(CATEGORY_IMAGE_MIXED),
        "questionLikelyPages": numbers(CATEGORY_QUESTION_LIKELY),
        "textOnlyPages": numbers(CATEGORY_TEXT_ONLY),
        "visualOcrNeededPages": [
            index + 1
            for index, classification in enumerate(classifications)
            if classification.get("needsVisualOcr")
        ],
    }
