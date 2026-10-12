"""Bounded normalization for the courseware activity response types."""

from __future__ import annotations

import re
from typing import Any


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return re.sub(r"\s+", " ", str(value)).strip().casefold()


def _sequence(value: Any) -> tuple[str, ...] | None:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(_text(item) for item in value)
    return None


def assess_activity(activity: dict[str, Any], response: dict[str, Any]) -> str:
    """Grade only supported objective response shapes; send all other answers to review."""
    mode = activity.get("assessmentMode")
    if mode == "participation":
        return "participation"
    if mode not in {"objective", "self_check"}:
        return "needs_review"

    kind = activity.get("activityKind")
    answer_key = activity.get("answerKey")
    accepted = activity.get("acceptedAnswers") or []
    if answer_key is None and not accepted:
        return "needs_review"
    if kind == "choice":
        submitted = response.get("selectedOptions")
        if submitted is None:
            submitted = response.get("selectedOption", response.get("answer"))
        if isinstance(submitted, list):
            submitted_values = _sequence(submitted)
            if not submitted_values or len(submitted_values) > 12:
                return "needs_review"
            expected_values = _sequence(answer_key)
            if expected_values is not None:
                is_correct = tuple(sorted(submitted_values)) == tuple(sorted(expected_values))
            else:
                is_correct = len(submitted_values) == 1 and _text(answer_key) == submitted_values[0]
            if isinstance(answer_key, str):
                is_correct = is_correct or (len(submitted_values) == 1 and _text(answer_key) == submitted_values[0])
            for alternative in accepted:
                alternative_values = _sequence(alternative)
                if alternative_values is not None:
                    is_correct = is_correct or tuple(sorted(submitted_values)) == tuple(sorted(alternative_values))
                else:
                    is_correct = is_correct or (len(submitted_values) == 1 and _text(alternative) == submitted_values[0])
        else:
            if submitted is None or isinstance(submitted, (dict, list)):
                return "needs_review"
            candidate = _text(submitted)
            is_correct = candidate == _text(answer_key)
            is_correct = is_correct or any(candidate == _text(item) for item in accepted)
        return "correct" if is_correct else "incorrect"

    if kind == "fill_blank":
        submitted = response.get("answers", response.get("answer", response.get("text")))
        submitted_values = _sequence(submitted)
        if submitted_values is None:
            if not isinstance(submitted, (str, int, float)):
                return "needs_review"
            submitted_values = (_text(submitted),)
        if not submitted_values or len(submitted_values) > 20 or any(not item for item in submitted_values):
            return "needs_review"
        expected_values = _sequence(answer_key)
        if expected_values is not None:
            is_correct = submitted_values == expected_values
        else:
            is_correct = len(submitted_values) == 1 and submitted_values[0] == _text(answer_key)
        if len(submitted_values) == 1:
            is_correct = is_correct or submitted_values[0] in {_text(item) for item in accepted}
        return "correct" if is_correct else "incorrect"

    # Matching schemas and open-ended responses need a teacher decision until a
    # reviewed, type-specific grader is registered.
    return "needs_review"
