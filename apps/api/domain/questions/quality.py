"""导入前题源质量报告的确定性规则。"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from ocr_pipeline import PAGE_MARKER


def build_import_quality_report(
    batches: list[dict[str, Any]],
    *,
    total_pages: int,
) -> dict[str, Any]:
    """汇总 OCR 题块、题号、页面和图片归属信号。

    ``batches`` 只包含 OCR 与切分结果，不触发模型调用。批次间重复题号是预期的跨页
    上下文，只在同一批次内重复时记为阻断项；这样报告不会把正常的批次 overlap 误报为错误。
    """
    detected: list[str] = []
    duplicate_numbers: set[str] = set()
    image_numbers: defaultdict[str, set[str]] = defaultdict(set)
    image_attribution_audit: list[dict[str, Any]] = []
    unidentified_pages: set[int] = set()
    sparse_batches: list[str] = []
    page_markers_seen = False

    for batch in batches:
        batch_id = str(batch.get("id", ""))
        source = str(batch.get("source", ""))
        blocks = batch.get("blocks") or []
        numbers = [str(item[0]).strip() for item in blocks if item and str(item[0]).strip()]
        detected.extend(numbers)
        duplicate_numbers.update(number for number, count in Counter(numbers).items() if count > 1)
        for number, _block, images in blocks:
            for image in images or []:
                image_numbers[str(image)].add(number)
        image_attribution_audit.extend(
            item for item in (batch.get("imageAttributionAudit") or []) if isinstance(item, dict)
        )

        markers = [int(value) for value in PAGE_MARKER.findall(source)]
        if markers:
            page_markers_seen = True
            start_page = int(batch.get("startPage", 1))
            end_page = int(batch.get("endPage", start_page))
            unidentified_pages.update(
                page for page in range(start_page, end_page + 1) if page not in markers
            )
        elif not source.strip():
            start_page = int(batch.get("startPage", 1))
            end_page = int(batch.get("endPage", start_page))
            unidentified_pages.update(range(start_page, end_page + 1))

        if len(source) >= 1200 and len(numbers) < 2:
            sparse_batches.append(batch_id)

    unique_numbers = list(dict.fromkeys(detected))
    numeric_numbers = sorted({int(number) for number in unique_numbers if number.isdigit()})
    missing_numbers: list[int] = []
    if numeric_numbers:
        missing_numbers = [
            number for number in range(numeric_numbers[0], numeric_numbers[-1] + 1)
            if number not in numeric_numbers
        ]
    image_conflicts = [
        {"image": image, "questionNumbers": sorted(numbers, key=lambda value: (not value.isdigit(), value))}
        for image, numbers in image_numbers.items()
        if len(numbers) > 1
    ]

    blockers: list[str] = []
    warnings: list[str] = []
    if not unique_numbers:
        blockers.append("没有识别到可用题号")
    if duplicate_numbers:
        blockers.append(f"同一批次出现重复题号：{', '.join(sorted(duplicate_numbers))}")
    if sparse_batches:
        blockers.append(f"长 OCR 文本但题号过少：{', '.join(sparse_batches)}")
    if image_conflicts:
        blockers.append(f"{len(image_conflicts)} 张图片被归属到多个题号")
    uncertain_attributions = [
        item for item in image_attribution_audit if item.get("status") == "needs_review"
    ]
    if uncertain_attributions:
        blockers.append(f"{len(uncertain_attributions)} 张无明确图注的图片无法可靠归属题号")
    if unidentified_pages:
        warnings.append(f"{len(unidentified_pages)} 页缺少可确认的 OCR 页面标记")
    if missing_numbers:
        warnings.append(f"题号序列存在缺口：{', '.join(map(str, missing_numbers))}")
    if not page_markers_seen and total_pages:
        warnings.append("OCR 未提供逐页标记，无法精确核对未识别页")

    status = "blocked" if blockers else "warning" if warnings else "ready"
    return {
        "status": status,
        "readyForFullPaper": not blockers,
        "totalPages": total_pages,
        "expectedQuestionCount": len(unique_numbers),
        "detectedQuestionNumbers": unique_numbers,
        "questionRange": (
            f"{numeric_numbers[0]}–{numeric_numbers[-1]}" if numeric_numbers else "—"
        ),
        "duplicateQuestionNumbers": sorted(duplicate_numbers),
        "missingQuestionNumbers": missing_numbers,
        "unidentifiedPages": sorted(unidentified_pages),
        "imageAttributionConflicts": image_conflicts,
        "imageAttributionAudit": image_attribution_audit,
        "warnings": warnings,
        "blockers": blockers,
        "checkedBatchCount": len(batches),
    }
