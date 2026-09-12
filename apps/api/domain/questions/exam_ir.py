"""从 OCR 产物构建 ExamIR/QuestionIR。

正则在这里仍然只负责提出候选边界；每个候选都保留原始文本、页面标记、块 ID 和图片
引用。后续模型只能在这个边界内工作，不能通过补写内容“修复”缺失的题源。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from domain.questions.ir import DocumentArtifact, ExamIR, QuestionIR, bounded_confidence
from domain.questions.pipeline import (
    clean_question_stem,
    normalize_text_choices_from_source,
)
from domain.questions.source import (
    ANSWER_SECTION_PATTERN,
    QUESTION_SECTION_PATTERN,
    QUESTION_SEGMENTATION_VERSION,
    split_question_sources,
)
from domain.questions.structured_source import (
    ANSWER_REFERENCE_AMBIGUOUS,
    BOUNDARY_LOW_CONFIDENCE,
    IMAGE_OWNER_CONFLICT,
    SOURCE_BLOCK_MISSING,
    SOURCE_TEXT_NOT_RECONSTRUCTABLE,
    load_structured_source,
    merge_diagnostics,
    refs_for_question,
)
from ocr_pipeline import PAGE_MARKER

EXAM_IR_VERSION = "exam-ir-v1"


def _pages_for_block(block: str, start_page: int, end_page: int) -> tuple[int, ...]:
    markers = [int(value) for value in PAGE_MARKER.findall(block)]
    if markers:
        return tuple(dict.fromkeys(markers))
    return tuple(range(start_page, end_page + 1)) if end_page >= start_page else (start_page,)


def _block_id(number: str, block: str, index: int) -> str:
    digest = hashlib.sha256(block.encode("utf-8")).hexdigest()[:12]
    return f"ocr-block-{number or index + 1}-{digest}"


def _section_title(source: str, offset: int) -> str:
    heading = None
    for match in QUESTION_SECTION_PATTERN.finditer(source):
        if match.start() <= offset:
            heading = match.group(0)
        else:
            break
    return re.sub(r"^\s*#+\s*", "", heading or "").strip()


def _candidate_options(block: str, images: list[str]) -> list[str]:
    payload = {"question": {"options": [""]}}
    normalize_text_choices_from_source(payload, block)
    options = payload["question"].get("options") or []
    if options == [""] and len(images) in {4, 5}:
        return ["(A)", "(B)", "(C)", "(D)"]
    return [str(item) for item in options if str(item).strip()]


def build_exam_ir(
    source: str,
    *,
    asset_dir: Path | None = None,
    batch_id: str = "batch",
    start_page: int = 1,
    end_page: int | None = None,
    provider: str = "unknown",
) -> ExamIR:
    """把一份 OCR 文本变成可审计的试卷结构。

    ``split_question_sources`` 的结果是候选，不合格或低置信度题仍然返回，并通过
    ``warnings`` 标记，避免切分器悄悄丢题或让模型凭空生成题目。
    """
    raw_source = str(source or "")
    effective_end = end_page if end_page is not None else start_page
    structured = load_structured_source(
        asset_dir,
        ocr_start_page=start_page,
        ocr_end_page=effective_end,
    )
    candidates = split_question_sources(raw_source, asset_dir=asset_dir)
    questions: list[QuestionIR] = []
    for index, (number, block, images) in enumerate(candidates):
        refs = refs_for_question(block, structured)
        pages = tuple(dict.fromkeys(ref.page for ref in refs)) or _pages_for_block(block, start_page, effective_end)
        stem = clean_question_stem(number, block)
        warnings: list[str] = []
        if not number:
            warnings.append("未识别到题号")
        if not stem:
            warnings.append("题干为空")
        if not pages:
            warnings.append("缺少来源页码")
        if structured.available and not refs:
            warnings.extend((SOURCE_BLOCK_MISSING, BOUNDARY_LOW_CONFIDENCE))
        if not structured.available:
            warnings.extend((SOURCE_TEXT_NOT_RECONSTRUCTABLE, BOUNDARY_LOW_CONFIDENCE))
        confidence = 0.45
        confidence += 0.2 if number else 0
        confidence += 0.15 if stem else 0
        confidence += 0.1 if images else 0
        confidence += 0.1 if PAGE_MARKER.search(block) else 0
        confidence += 0.15 if refs else 0
        confidence = min(confidence, 1.0)
        key_number = number or f"index-{index + 1:03d}"
        answer_matches = list(ANSWER_SECTION_PATTERN.finditer(raw_source))
        answer_reference: dict[str, Any] = {
            "present": bool(answer_matches),
            "documentHash": hashlib.sha256(raw_source.encode("utf-8")).hexdigest()[:16],
        }
        if answer_matches:
            answer_start = answer_matches[0].start()
            answer_text = raw_source[answer_start:]
            answer_refs = tuple(
                ref for ref in structured.blocks
                if ref.text and re.sub(r"\s+", "", ref.text) in re.sub(r"\s+", "", answer_text)
            )
            answer_reference["sourceBlockIds"] = [ref.block_id for ref in answer_refs]
            answer_reference["sourcePages"] = [ref.page for ref in answer_refs]
            answer_reference["textHash"] = hashlib.sha256(answer_text.encode("utf-8")).hexdigest()
        if len(answer_matches) > 1:
            answer_reference["diagnostic"] = ANSWER_REFERENCE_AMBIGUOUS
            warnings.append(ANSWER_REFERENCE_AMBIGUOUS)
        reference_assets = [asset for ref in refs for asset in ref.asset_ids]
        questions.append(QuestionIR(
            source_question_key=f"{batch_id}-q-{re.sub(r'[^0-9A-Za-z_-]+', '', key_number)}",
            number=number,
            source_text=block,
            stem=stem,
            options=tuple(_candidate_options(block, images)),
            visual_asset_ids=tuple(dict.fromkeys([Path(image).name for image in images] + reference_assets)),
            source_pages=pages,
            source_block_ids=tuple(ref.block_id for ref in refs) or (_block_id(number, block, index),),
            source_answer_reference=answer_reference,
            section_title=_section_title(raw_source, raw_source.find(block)),
            confidence=bounded_confidence(confidence),
            warnings=tuple(warnings),
            source_blocks=tuple(ref.as_dict() for ref in refs) or ({
                "blockId": _block_id(number, block, index),
                "page": pages[0] if pages else start_page,
                "type": "text",
                "bbox": None,
                "order": index,
                "textHash": hashlib.sha256(block.encode("utf-8")).hexdigest(),
                "assetIds": [Path(image).name for image in images],
                "origin": "markdown-fallback",
                "text": block,
            },),
            source_stable_id=hashlib.sha256(
                (f"{batch_id}:{key_number}:" + "|".join(ref.block_id for ref in refs)).encode("utf-8")
            ).hexdigest()[:24],
            source_origin=refs[0].origin if refs else "markdown-fallback",
        ))

    sections_map: dict[str, list[dict[str, Any]]] = {}
    for question in questions:
        title = question.section_title or "未分类题目"
        sections_map.setdefault(title, []).append(question.as_dict())
    sections = tuple({"title": title, "questions": items} for title, items in sections_map.items())
    warnings: list[str] = []
    if not questions and raw_source.strip():
        warnings.append("OCR 文本存在但没有形成题目候选")
    diagnostics = validate_exam_ir(ExamIR(
        artifact=DocumentArtifact(
            artifact_id=hashlib.sha256(raw_source.encode("utf-8")).hexdigest()[:16],
            source_pages=tuple(range(start_page, effective_end + 1)) if effective_end >= start_page else (),
            text_length=len(raw_source),
            provider=provider,
            pipeline_version="ocr-pipeline-v2",
            content_hash=hashlib.sha256(raw_source.encode("utf-8")).hexdigest(),
        ),
        questions=tuple(questions),
        sections=sections,
        warnings=tuple(warnings),
        version=f"{EXAM_IR_VERSION}+{QUESTION_SEGMENTATION_VERSION}",
    ), raw_source)
    diagnostics = dict(diagnostics)
    diagnostics["structuredSource"] = structured.as_dict()
    diagnostics["issues"] = merge_diagnostics(
        structured.diagnostics,
        ({"code": warning, "message": warning} for question in questions for warning in question.warnings),
        diagnostics.get("issues", []),
    )
    exam = ExamIR(
        artifact=DocumentArtifact(
            artifact_id=hashlib.sha256(raw_source.encode("utf-8")).hexdigest()[:16],
            source_pages=tuple(range(start_page, effective_end + 1)) if effective_end >= start_page else (),
            text_length=len(raw_source),
            provider=provider,
            pipeline_version="ocr-pipeline-v2",
            content_hash=hashlib.sha256(raw_source.encode("utf-8")).hexdigest(),
        ),
        questions=tuple(questions),
        sections=sections,
        warnings=tuple(warnings),
        diagnostics=diagnostics,
        version=f"{EXAM_IR_VERSION}+{QUESTION_SEGMENTATION_VERSION}",
    )
    persist_exam_ir(exam, asset_dir)
    return exam


def persist_exam_ir(exam: ExamIR, asset_dir: Path | None) -> Path | None:
    """把 ExamIR 原子写入批次目录，供重启恢复和审核队列读取。"""
    if asset_dir is None:
        return None
    asset_dir.mkdir(parents=True, exist_ok=True)
    target = asset_dir / "exam-ir.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(exam.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return target


def validate_exam_ir(exam: ExamIR, source: str) -> dict[str, Any]:
    """执行 IR 层守恒检查，返回机器可读的诊断而不是猜测修复。"""
    errors: list[str] = []
    seen_blocks: set[str] = set()
    duplicate_blocks: list[str] = []
    image_owners: dict[str, list[str]] = {}
    for question in exam.questions:
        normalized_source = re.sub(r"\s+", "", source)
        normalized_question = re.sub(r"\s+", "", question.source_text)
        if normalized_question and normalized_question not in normalized_source:
            errors.append(f"题目 {question.number or question.source_question_key} 的原文无法在 OCR 产物中重建")
        for block_id in question.source_block_ids:
            if block_id in seen_blocks:
                duplicate_blocks.append(block_id)
            seen_blocks.add(block_id)
        for asset_id in question.visual_asset_ids:
            image_owners.setdefault(asset_id, []).append(question.number or question.source_question_key)
    if duplicate_blocks:
        errors.append("同一 OCR 块被多个题目重复归属")
    image_conflicts = {
        asset: owners for asset, owners in image_owners.items() if len(set(owners)) > 1
    }
    if image_conflicts:
        errors.append("同一图片被多个题目归属")
    return {
        "status": "needs_review" if errors else "ready",
        "errors": errors,
        "duplicateSourceBlockIds": sorted(set(duplicate_blocks)),
        "imageConflicts": image_conflicts,
        "issues": [
            {"code": SOURCE_TEXT_NOT_RECONSTRUCTABLE, "message": error}
            for error in errors if "原文无法" in error
        ] + ([{"code": SOURCE_BLOCK_MISSING, "message": "同一 OCR 块被多个题目重复归属"}] if duplicate_blocks else [])
        + ([{"code": IMAGE_OWNER_CONFLICT, "assets": image_conflicts}] if image_conflicts else [])
        + [
            {"code": BOUNDARY_LOW_CONFIDENCE, "question": question.source_question_key}
            for question in exam.questions if question.confidence < 0.6
        ],
        "questionCount": len(exam.questions),
        "sourceTextLength": len(source),
    }


def first_question_ir(source: str, **kwargs: Any) -> dict[str, Any]:
    """为单题生成路径返回一个稳定的 QuestionIR 字典。"""
    exam = build_exam_ir(source, **kwargs)
    if exam.questions:
        result = exam.questions[0].as_dict()
        result["diagnostics"] = exam.diagnostics
        return result
    result = QuestionIR(
        source_question_key="unidentified-q-001",
        number="",
        source_text=str(source or ""),
        stem=str(source or "").strip()[:4_000],
        confidence=0.2,
        warnings=("未形成结构化题目候选",),
    ).as_dict()
    result["diagnostics"] = {"status": "needs_review", "errors": ["未形成结构化题目候选"]}
    return result
