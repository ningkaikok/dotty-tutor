"""生成并检查一个 OCR 批次中提取出的每道题。

本模块故意不依赖 FastAPI。当前同步 PDF 路由直接调用它；未来迁移到后台 Worker 时也能复用
同一函数，不需要复制规范化、审核、重试和进度规则。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Sequence

from application.services.legacy_question_adapter import project_question_ir
from application.services.lesson_generation import (
    attach_question_source,
    generate_lesson,
    generate_question_from_ir,
    review_lesson_payload,
)
from domain.questions.exam_ir import build_exam_ir
from domain.questions.pipeline import (
    apply_question_quality_gate,
    normalize_image_choice_question,
    normalize_model_math_text,
    normalize_stacked_equation_choices,
    normalize_text_choice_labels,
    normalize_text_choices_from_source,
    strip_choice_text_from_prompt,
)
from domain.questions.source import question_image_paths, question_key, safe_string_list
from observability import log_event

ProgressUpdater = Callable[[dict[str, Any], str, int, str], None]
# A failed question gets one targeted repair. A third full generation/review pass has a
# poor quality-to-cost ratio and can be retried explicitly from the workbench instead.
QUALITY_REPAIR_ATTEMPTS = 1


def _attach_question_ir_provenance(
    payload: dict[str, Any],
    *,
    number: str,
    block: str,
    images: list[str],
    batch: dict[str, Any],
    ocr_run: dict[str, Any],
    asset_dir: Path,
    question_ir: dict[str, Any] | None = None,
) -> None:
    """在模型审核后写回来源 IR，防止模型篡改题号、图片或来源键。"""
    exam = None if question_ir is not None else build_exam_ir(
        block,
        asset_dir=asset_dir,
        batch_id=str(batch["id"]),
        start_page=int(ocr_run.get("startPage") or batch.get("startPage") or 1),
        end_page=int(ocr_run.get("endPage") or batch.get("endPage") or 1),
        provider=str(ocr_run.get("provider") or "unknown"),
    )
    question = payload["question"]
    candidate = question_ir or (exam.questions[0].as_dict() if exam and exam.questions else {})
    provenance = {
        "sourceQuestionKey": question.get("sourceQuestionKey") or question_key(batch["id"], number, 0),
        "sourceStableId": candidate.get("sourceStableId") or question.get("sourceStableId") or "",
        "sourcePages": candidate.get("sourcePages") or list(range(batch["startPage"], batch["endPage"] + 1)),
        "sourceBlockIds": candidate.get("sourceBlockIds") or [],
        "visualAssetIds": [Path(image).name for image in images],
        "confidence": candidate.get("confidence", 0.2),
        "warnings": candidate.get("warnings", []),
        "diagnostics": question_ir.get("diagnostics", {}) if question_ir else (exam.diagnostics if exam else {}),
        "sourceAnswerReference": candidate.get("sourceAnswerReference"),
        "sourceOrigin": candidate.get("sourceOrigin", "markdown-fallback"),
        "sourceBlocks": candidate.get("sourceBlocks", []),
        "documentId": str(ocr_run.get("sourceFingerprint") or ""),
        "ocrProvider": str(ocr_run.get("provider") or "unknown"),
        "ocrVersion": str(ocr_run.get("pipelineVersion") or "unknown"),
        "segmentationVersion": str(ocr_run.get("questionSegmentationVersion") or "unknown"),
        "version": "question-ir-v1",
    }
    question["sourceProvenance"] = provenance


def _runtime_available(model_run: dict[str, Any], review_run: dict[str, Any]) -> bool:
    """当再次生成只会重复同一次服务故障时返回 False，避免无意义重试。"""
    if model_run.get("fallback") or model_run.get("provider") == "mock":
        return False
    review_models = [
        review_run.get("textModelRun", {}),
        review_run.get("visionModelRun", {}),
    ]
    return not any(run.get("fallback") for run in review_models if isinstance(run, dict))


def _generate_validated_question(
    *,
    number: str,
    block: str,
    images: list[str],
    index: int,
    batch: dict[str, Any],
    ocr_run: dict[str, Any],
    asset_dir: Path,
    run_id: str | None = None,
    question_ir: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """生成单题，并对未通过质量门禁的结果进行有限自动重试。

    重试只处理当前异常题，复用已有 OCR 原文和图片。次数上限可防止坏来源或提示词回归把同步
    请求拖成无限模型循环。最终仍不合格的候选题保留为工作台诊断，但发布边界绝不会把它
    暴露给学生。
    """
    attempts = QUALITY_REPAIR_ATTEMPTS + 1
    final: tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], dict[str, Any]] | None = None
    completed_attempts = 0
    repair_errors: list[str] = []
    for attempt in range(1, attempts + 1):
        completed_attempts = attempt
        if question_ir is not None:
            payload, guide_cards, model_run = generate_question_from_ir(
                question_ir,
                repair_errors=repair_errors or None,
                asset_dir=asset_dir,
            )
        else:
            try:
                payload, guide_cards, model_run = generate_lesson(
                    block,
                    repair_errors=repair_errors or None,
                    asset_dir=asset_dir,
                )
            except TypeError as error:
                # Preserve third-party/test adapters implementing the old two-argument
                # generator while the built-in path uses the durable stage cache.
                if "unexpected keyword argument 'asset_dir'" not in str(error):
                    raise
                payload, guide_cards, model_run = generate_lesson(
                    block,
                    repair_errors=repair_errors or None,
                )
        projection_ir = dict(question_ir) if question_ir is not None else None
        if projection_ir is not None:
            # Freeze extraction-owned fields before the advisory review call. This gives
            # the reviewer context without allowing its response to become a new source.
            if not projection_ir.get("givens"):
                projection_ir["givens"] = list(payload["question"].get("givens") or [])
            if not projection_ir.get("subQuestions"):
                projection_ir["subQuestions"] = [
                    dict(item) for item in payload["question"].get("subQuestions") or []
                    if isinstance(item, dict)
                ]
        attach_question_source(payload, batch, ocr_run, images)
        if number:
            payload["question"]["questionNumber"] = number
        payload["question"]["sourceQuestionKey"] = question_key(batch["id"], number, index)
        try:
            payload, review_run = review_lesson_payload(
                payload,
                block,
                question_image_paths(asset_dir, images),
                guide_cards,
                question_ir=projection_ir,
            )
        except TypeError as error:
            if "unexpected keyword argument 'question_ir'" not in str(error):
                raise
            payload, review_run = review_lesson_payload(
                payload,
                block,
                question_image_paths(asset_dir, images),
                guide_cards,
            )
            if projection_ir is not None:
                payload["question"] = project_question_ir(projection_ir, payload["question"])
        # 审核模型可能会删掉图片或把文件名写回文字字段。来源图片是 OCR 的确定性事实，
        # 审核只能补充说明，不能改变题目与图片的归属；因此审核后再次绑定来源。
        attach_question_source(payload, batch, ocr_run, images)
        _attach_question_ir_provenance(
            payload,
            number=number,
            block=block,
            images=images,
            batch=batch,
            ocr_run=ocr_run,
            asset_dir=asset_dir,
            question_ir=projection_ir,
        )
        attach_question_source(payload, batch, ocr_run, images)
        normalize_stacked_equation_choices(payload, block)
        normalize_text_choices_from_source(payload, str(payload["question"].get("prompt", "")))
        normalize_text_choices_from_source(payload, block)
        options = normalize_text_choice_labels(
            safe_string_list(payload["question"].get("options"), [], 6)
        )
        payload["question"]["options"] = [normalize_model_math_text(option) for option in options]
        payload["question"]["prompt"] = normalize_model_math_text(
            strip_choice_text_from_prompt(
                str(payload["question"].get("prompt", "")),
                payload["question"]["options"],
            )
        )
        normalize_image_choice_question(payload, block, images)
        if projection_ir is not None:
            payload["question"] = project_question_ir(projection_ir, payload["question"])
        placeholder_audits = [
            audit
            for audit in (model_run.get("imagePlaceholderAudit"), review_run.get("imagePlaceholderAudit"))
            if isinstance(audit, dict)
        ]
        if placeholder_audits:
            # 这是质量门禁的瞬时输入，不进入最终题目契约；门禁会把失败证据写入 quality。
            payload["_imagePlaceholderAudits"] = placeholder_audits
        quality = apply_question_quality_gate(payload, block, images)
        verification = payload["question"].get("verification")
        if isinstance(verification, dict) and verification.get("status") != "verified":
            reason = "答案核验未通过"
            conflicts = safe_string_list(verification.get("conflicts"), [], 3)
            if conflicts:
                reason = f"{reason}：{'；'.join(conflicts)}"
            quality.setdefault("errors", []).append(reason[:300])
            quality["status"] = "needs_review"
            payload["question"]["publicationStatus"] = "needs_review"
        can_retry = (
            quality["status"] != "ready"
            and attempt < attempts
            and _runtime_available(model_run, review_run)
        )
        payload["qualityRecovery"] = {
            "attempts": attempt,
            "recovered": attempt > 1 and quality["status"] == "ready",
            "quarantined": quality["status"] != "ready" and not can_retry,
        }
        final = payload, guide_cards, model_run, review_run
        if quality["status"] == "ready":
            if attempt > 1:
                log_event(
                    "question.quality.repair.succeeded",
                    batch_id=batch.get("id"),
                    question_number=number or index + 1,
                    attempts=attempt,
                    validator_version=quality.get("validatorVersion"),
                    run_id=run_id,
                )
            return final
        if can_retry:
            repair_errors = [str(error) for error in quality.get("errors", [])]
            log_event(
                "question.quality.repair.started",
                level=30,
                batch_id=batch.get("id"),
                question_number=number or index + 1,
                attempt=attempt + 1,
                error_count=len(quality.get("errors", [])),
                validator_version=quality.get("validatorVersion"),
                run_id=run_id,
            )
        else:
            break

    assert final is not None
    final_quality = final[0].get("quality", {})
    log_event(
        "question.quality.quarantined",
        level=40,
        batch_id=batch.get("id"),
        question_number=number or index + 1,
        attempts=completed_attempts,
        question_type=final[0].get("question", {}).get("questionType"),
        model_provider=final[2].get("provider"),
        model=final[2].get("model"),
        error_count=len(final_quality.get("errors", [])),
        validation_errors=[str(error)[:180] for error in final_quality.get("errors", [])[:5]],
        validator_version=final_quality.get("validatorVersion"),
        run_id=run_id,
    )
    return final


def _quarantine_duplicate_question_number(
    payload: dict[str, Any],
    batch: dict[str, Any],
    number: str,
    index: int,
) -> None:
    """为批次内重复题号生成独立 key，防止静默覆盖同批次里另一道题。

    ``split_question_sources`` 里"相邻同号合并"已经处理了合法的跨页续题情况；一个重复
    题号能走到这里，说明两个块并不相邻，通常是 OCR 把题干中间的段落断点（或粘连在上一题
    选项末尾、没有换行的题号）误判成了新题号。这类块很可能包含真实但边界不完整的内容，
    不能被当垃圾丢弃，也不能沿用原始 ``number`` 走 ``question_key()`` 的常规路径——两个块
    共享同一个 key 正是导致同批次内一道题在持久化时静默覆盖另一道题的根因。这里改用
    ``question_key(batch_id, "", index)`` 触发已有的 ``index-{NNN}`` 回退命名，
    ``index`` 是该题在本批次里的位置，批次内必然唯一。
    """
    question = payload["question"]
    unique_key = question_key(batch["id"], "", index)
    if question.get("sourceQuestionKey") == unique_key:
        return
    question["sourceQuestionKey"] = unique_key
    quality = payload.setdefault("quality", {})
    quality.setdefault("errors", []).append(
        f"题号 {number} 与批次内另一道题重复，可能是相邻题目文本被误判为新题号，已隔离为独立候选待人工核对"
    )
    quality["status"] = "needs_review"
    question["publicationStatus"] = "needs_review"
    review = payload.get("review")
    if isinstance(review, dict):
        review["status"] = "needs_review"
        review["needsHumanReview"] = True


def process_question_sources(
    question_sources: Sequence[tuple[str, str, list[str]] | dict[str, Any]],
    batch: dict[str, Any],
    ocr_run: dict[str, Any],
    asset_dir: Path,
    job: dict[str, Any] | None = None,
    update_job: ProgressUpdater | None = None,
    run_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """生成、绑定来源、审核并返回一个 OCR 批次中的所有题目。

    确定性修复安排在模型审核之后，防止后续模型响应撤销选项标签规范化或发布质量门禁。
    ``update_job`` 通过参数注入，因此同一流程可运行于 HTTP、单元测试和未来后台 Worker。
    """
    payloads: list[dict[str, Any]] = []
    guide_cards_list: list[list[dict[str, Any]]] = []
    model_runs: list[dict[str, Any]] = []
    review_runs: list[dict[str, Any]] = []
    total = max(1, len(question_sources))
    seen_numbers: set[str] = set()
    log_event("question.batch.started", question_count=len(question_sources), batch_id=batch.get("id"), run_id=run_id)
    for index, source_item in enumerate(question_sources):
        if isinstance(source_item, dict):
            question_ir = source_item
            number = str(source_item.get("number") or "")
            block = str(source_item.get("sourceText") or "")
            images = [str(item) for item in source_item.get("visualAssetIds", []) if str(item).strip()]
        else:
            question_ir = None
            number, block, images = source_item
        log_event("question.started", batch_id=batch.get("id"), question_number=number or index + 1, image_count=len(images), run_id=run_id)
        payload, guide_cards, model_run, review_run = _generate_validated_question(
            number=number,
            block=block,
            images=images,
            index=index,
            batch=batch,
            ocr_run=ocr_run,
            asset_dir=asset_dir,
            run_id=run_id,
            question_ir=question_ir,
        )
        if number:
            if number in seen_numbers:
                # 同一批次内已经出现过这个题号：split_question_sources 的相邻合并没有
                # 生效，说明这是一个可疑的独立块，必须隔离 key，不能让它覆盖前一道题。
                _quarantine_duplicate_question_number(payload, batch, number, index)
            else:
                seen_numbers.add(number)
        payloads.append(payload)
        guide_cards_list.append(guide_cards)
        model_runs.append(model_run)
        review_runs.append(review_run)
        if job is not None and update_job is not None:
            update_job(job, "generating", min(94, 88 + round(((index + 1) / total) * 6)), f"正在处理第 {index + 1}/{len(question_sources)} 道题")
        log_event(
            "question.completed",
            batch_id=batch.get("id"),
            question_number=number or index + 1,
            question_type=payload.get("question", {}).get("questionType"),
            model_provider=model_run.get("provider"),
            review_provider=review_run.get("provider"),
            run_id=run_id,
        )
    log_event("question.batch.completed", question_count=len(payloads), batch_id=batch.get("id"), run_id=run_id)
    return payloads, guide_cards_list, model_runs, review_runs
