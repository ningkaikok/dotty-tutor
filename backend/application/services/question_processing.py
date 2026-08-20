"""生成并检查一个 OCR 批次中提取出的每道题。

本模块故意不依赖 FastAPI。当前同步 PDF 路由直接调用它；未来迁移到后台 Worker 时也能复用
同一函数，不需要复制规范化、审核、重试和进度规则。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from application.services.lesson_generation import attach_question_source, generate_lesson, review_lesson_payload
from observability import log_event
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


ProgressUpdater = Callable[[dict[str, Any], str, int, str], None]
# A failed question gets one targeted repair. A third full generation/review pass has a
# poor quality-to-cost ratio and can be retried explicitly from the workbench instead.
QUALITY_REPAIR_ATTEMPTS = 1


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
        payload, guide_cards, model_run = generate_lesson(
            block,
            repair_errors=repair_errors or None,
        )
        attach_question_source(payload, batch, ocr_run, images)
        if number:
            payload["question"]["questionNumber"] = number
        payload["question"]["sourceQuestionKey"] = question_key(batch["id"], number, index)
        payload, review_run = review_lesson_payload(
            payload,
            block,
            question_image_paths(asset_dir, images),
            guide_cards,
        )
        # 审核模型可能会删掉图片或把文件名写回文字字段。来源图片是 OCR 的确定性事实，
        # 审核只能补充说明，不能改变题目与图片的归属；因此审核后再次绑定来源。
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
        quality = apply_question_quality_gate(payload, block, images)
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


def process_question_sources(
    question_sources: list[tuple[str, str, list[str]]],
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
    log_event("question.batch.started", question_count=len(question_sources), batch_id=batch.get("id"), run_id=run_id)
    for index, (number, block, images) in enumerate(question_sources):
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
        )
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
