"""Generate and quality-check every question extracted from one OCR batch.

The function is deliberately independent of FastAPI. Today the synchronous PDF
route calls it directly; a future worker can call the same function without
copying normalization, review or progress rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from lesson_generation import attach_question_source, generate_lesson, review_lesson_payload
from observability import log_event
from question_pipeline import (
    apply_question_quality_gate,
    normalize_image_choice_question,
    normalize_model_math_text,
    normalize_stacked_equation_choices,
    normalize_text_choice_labels,
    normalize_text_choices_from_source,
    strip_choice_text_from_prompt,
)
from question_source import question_image_paths, question_key, safe_string_list


ProgressUpdater = Callable[[dict[str, Any], str, int, str], None]


def process_question_sources(
    question_sources: list[tuple[str, str, list[str]]],
    batch: dict[str, Any],
    ocr_run: dict[str, Any],
    asset_dir: Path,
    job: dict[str, Any] | None = None,
    update_job: ProgressUpdater | None = None,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate, attach, review and return every question in one OCR batch.

    Deterministic repairs run after model review so a later model response cannot
    undo label normalization or the publication quality gate. ``update_job`` is
    injected so the same operation works in HTTP, tests and a future worker.
    """
    payloads: list[dict[str, Any]] = []
    guide_cards_list: list[list[dict[str, Any]]] = []
    model_runs: list[dict[str, Any]] = []
    review_runs: list[dict[str, Any]] = []
    total = max(1, len(question_sources))
    log_event("question.batch.started", question_count=len(question_sources), batch_id=batch.get("id"))
    for index, (number, block, images) in enumerate(question_sources):
        log_event("question.started", batch_id=batch.get("id"), question_number=number or index + 1, image_count=len(images))
        payload, guide_cards, model_run = generate_lesson(block)
        attach_question_source(payload, batch, ocr_run, images)
        if number:
            payload["question"]["questionNumber"] = number
        payload["question"]["sourceQuestionKey"] = question_key(batch["id"], number, index)
        payload, review_run = review_lesson_payload(payload, block, question_image_paths(asset_dir, images), guide_cards)
        normalize_stacked_equation_choices(payload, block)
        normalize_text_choices_from_source(payload, str(payload["question"].get("prompt", "")))
        normalize_text_choices_from_source(payload, block)
        options = normalize_text_choice_labels(safe_string_list(payload["question"].get("options"), [], 6))
        options = [normalize_model_math_text(option) for option in options]
        payload["question"]["options"] = options
        payload["question"]["prompt"] = normalize_model_math_text(
            strip_choice_text_from_prompt(str(payload["question"].get("prompt", "")), options)
        )
        normalize_image_choice_question(payload, block, images)
        apply_question_quality_gate(payload, block, images)
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
        )
    log_event("question.batch.completed", question_count=len(payloads), batch_id=batch.get("id"))
    return payloads, guide_cards_list, model_runs, review_runs
