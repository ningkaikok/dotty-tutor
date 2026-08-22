"""Deterministic OCR source quality gate and bounded retry policy.

This module validates the OCR markdown before question generation.  It does not
call an OCR provider or mutate a job, so the upload service can use the result
to retry only the affected page or question block and persist the decision.
"""

from __future__ import annotations

import re
from typing import Any, Literal

MAX_OCR_RETRIES = 2
QualityStatus = Literal["ready", "retry", "quarantine"]

_QUESTION_NUMBER = re.compile(
    r"(?m)^\s*(?:[【\[]\s*)?(?:第\s*)?(\d{1,3})(?:(?:\s*题\s*(?:[:：]|\s))|[.．、]|[】\]])\s*"
)
# 选项常被 OCR 压到同一行，不能只检查行首；负向前瞻避免把英文单词中的字母误判为选项。
_CHOICE_MARKER = re.compile(r"(?<![A-Za-z])(?:\(([A-H])\)|([A-H])[.．、:：])\s*")
_IMAGE_REFERENCE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_GARBLED = re.compile(r"[\uFFFD\x00-\x08\x0B\x0C\x0E-\x1F]")
_FORMULA_DAMAGE = re.compile(r"\\(?:textbackslash|textcirc|textdegree)\b", re.IGNORECASE)


def _garbled_rate(text: str) -> float:
    """Return the ratio of replacement/control characters in non-space text."""
    visible = [character for character in text if not character.isspace()]
    if not visible:
        return 1.0
    return sum(bool(_GARBLED.fullmatch(character)) for character in visible) / len(visible)


def _formula_problems(text: str) -> list[str]:
    problems: list[str] = []
    if _FORMULA_DAMAGE.search(text):
        problems.append("formula_command_corrupted")
    if text.count("$$") % 2:
        problems.append("display_formula_delimiter_unbalanced")
    # Remove paired display delimiters before inspecting inline dollar signs.
    without_display = re.sub(r"\$\$", "", text)
    if without_display.count("$") % 2:
        problems.append("inline_formula_delimiter_unbalanced")
    return problems


def _choice_problems(text: str) -> list[str]:
    labels = [first or second for first, second in _CHOICE_MARKER.findall(text)]
    if not labels:
        return []
    expected = [chr(ord("A") + offset) for offset in range(len(labels))]
    if labels != expected:
        return ["choice_labels_not_sequential"]
    if labels[0] == "A" and len(labels) < 4:
        return ["choice_options_incomplete"]
    return []


def _provider_for_retry(provider: str) -> str | None:
    """Select the next OCR provider without allowing a fidelity downgrade."""
    normalized = provider.lower().strip()
    if normalized in {"pypdf", "none", "auto"}:
        return "mineru"
    if normalized == "mineru":
        return "mineru"
    # Pasted text has no provider upgrade path and should be reviewed instead.
    return None


def _decision(
    *,
    problems: list[str],
    provider: str,
    retry_count: int,
    retry_scope: dict[str, Any],
) -> dict[str, Any]:
    if not problems:
        return {
            "status": "ready",
            "problems": [],
            "recommendedProvider": None,
            "retryScope": None,
            "retryCount": retry_count,
            "maxRetries": MAX_OCR_RETRIES,
        }
    recommended = _provider_for_retry(provider)
    can_retry = retry_count < MAX_OCR_RETRIES and recommended is not None
    return {
        "status": "retry" if can_retry else "quarantine",
        "problems": problems,
        "recommendedProvider": recommended if can_retry else None,
        "retryScope": retry_scope if can_retry else None,
        "retryCount": retry_count,
        "maxRetries": MAX_OCR_RETRIES,
    }


def evaluate_page_quality(
    text: str,
    *,
    page_number: int,
    provider: str,
    retry_count: int = 0,
) -> dict[str, Any]:
    """Assess one OCR page and return a bounded provider retry decision."""
    source = str(text or "")
    problems: list[str] = []
    if not source.strip():
        problems.append("empty_text")
    if _garbled_rate(source) >= 0.03:
        problems.append("garbled_text_rate_high")
    problems.extend(_formula_problems(source))
    return _decision(
        problems=problems,
        provider=provider,
        retry_count=retry_count,
        retry_scope={"type": "page", "pageNumbers": [page_number]},
    )


def evaluate_question_quality(
    text: str,
    *,
    page_number: int,
    question_number: str | int | None,
    provider: str,
    expected_images: list[str] | None = None,
    retry_count: int = 0,
) -> dict[str, Any]:
    """Assess a segmented question block and request only that block on retry."""
    source = str(text or "")
    problems: list[str] = []
    if not source.strip():
        problems.append("empty_text")
    if _garbled_rate(source) >= 0.03:
        problems.append("garbled_text_rate_high")
    if question_number is not None:
        found = _QUESTION_NUMBER.search(source)
        if not found or found.group(1) != str(question_number):
            problems.append("question_number_missing_or_mismatched")
    problems.extend(_choice_problems(source))
    problems.extend(_formula_problems(source))
    if expected_images is not None:
        found_images = _IMAGE_REFERENCE.findall(source)
        if found_images != expected_images:
            problems.append("image_references_mismatched")
    return _decision(
        problems=problems,
        provider=provider,
        retry_count=retry_count,
        retry_scope={
            "type": "question",
            "pageNumbers": [page_number],
            "questionNumbers": [str(question_number)] if question_number is not None else [],
        },
    )
