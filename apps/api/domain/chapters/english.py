"""Deterministic evidence-aware evaluation for reviewed English chapter items.

This module only grades objective choices and explicitly approved short-answer
variants. It never interprets an unlisted paraphrase or invents source evidence.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

EVALUATOR_VERSION = "chapter-english-evaluator-v1"
_QUESTION_KINDS = {"word_meaning", "reference", "explicit", "inference", "short_answer"}
_ANSWER_MODES = {"objective", "short_answer"}


def _normalise(value: str) -> str:
    folded = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.findall(r"[\w']+", folded))


def _ref_key(ref: Mapping[str, Any]) -> tuple[str, int, str, str] | None:
    revision = ref.get("sourceRevisionId")
    page = ref.get("page")
    region = ref.get("regionId", "")
    sentence = ref.get("sentenceId", "")
    if (
        not isinstance(revision, str)
        or not revision.strip()
        or not isinstance(page, int)
        or isinstance(page, bool)
        or page < 1
        or (region is not None and not isinstance(region, str))
        or (sentence is not None and not isinstance(sentence, str))
    ):
        return None
    return revision, page, region or "", sentence or ""


def _answer_values(answer: Mapping[str, Any]) -> list[str]:
    scalar_values: list[str] = []
    for key in ("text", "numericAnswer", "value"):
        if key not in answer:
            continue
        value = answer[key]
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            return []
        if len(value) > 2_000:
            return []
        normalised = _normalise(value)
        if normalised:
            scalar_values.append(normalised)
    if "selectedOptions" in answer:
        selected = answer["selectedOptions"]
        if scalar_values or not isinstance(selected, list) or len(selected) != 1 or not isinstance(selected[0], str):
            return []
        normalised = _normalise(selected[0])
        return [normalised] if normalised else []
    distinct = list(dict.fromkeys(scalar_values))
    return distinct if len(distinct) == 1 else []


def _page_index(source_pages: Sequence[Mapping[str, Any]]) -> dict[tuple[str, int], Mapping[str, Any]] | None:
    index: dict[tuple[str, int], Mapping[str, Any]] = {}
    for page in source_pages:
        key = _ref_key({"sourceRevisionId": page.get("sourceRevisionId"), "page": page.get("page")})
        if (
            key is None
            or not isinstance(page.get("text"), str)
            or len(page["text"]) > 20_000
            or key[:2] in index
        ):
            return None
        regions = page.get("regions", [])
        if not isinstance(regions, list) or len(regions) > 100:
            return None
        region_ids: set[str] = set()
        for region in regions:
            if not isinstance(region, Mapping):
                return None
            region_id = region.get("regionId")
            if region_id is not None and (not isinstance(region_id, str) or not region_id.strip()):
                return None
            if isinstance(region_id, str):
                if region_id in region_ids:
                    return None
                region_ids.add(region_id)
        sentences = page.get("sentences")
        if sentences is not None:
            if not isinstance(sentences, list) or len(sentences) > 500:
                return None
            sentence_ids: set[str] = set()
            for sentence in sentences:
                if (
                    not isinstance(sentence, Mapping)
                    or not isinstance(sentence.get("sentenceId"), str)
                    or not sentence["sentenceId"].strip()
                    or not isinstance(sentence.get("text"), str)
                ):
                    return None
                sentence_id = sentence["sentenceId"]
                if sentence_id in sentence_ids:
                    return None
                sentence_ids.add(sentence_id)
                sentence_region = sentence.get("regionId")
                if sentence_region is not None and sentence_region not in region_ids:
                    return None
        index[key[:2]] = page
    return index


def _location_resolves(
    ref: Mapping[str, Any],
    expected_revision: str,
    page_index: Mapping[tuple[str, int], Mapping[str, Any]],
) -> bool:
    key = _ref_key(ref)
    if key is None or key[0] != expected_revision:
        return False
    page = page_index.get(key[:2])
    if page is None:
        return False
    region_id = key[2]
    if region_id:
        regions = page.get("regions", [])
        if not any(isinstance(region, Mapping) and region.get("regionId") == region_id for region in regions):
            return False
    sentence_id = key[3]
    sentence = None
    sentences = page.get("sentences")
    if sentence_id:
        if not isinstance(sentences, list):
            return False
        sentence = next(
            (item for item in sentences if isinstance(item, Mapping) and item.get("sentenceId") == sentence_id),
            None,
        )
        if sentence is None:
            return False
        sentence_region = sentence.get("regionId")
        if key[2] and sentence_region != key[2]:
            return False
    quote = ref.get("quote")
    if quote is not None:
        if not isinstance(quote, str) or not quote.strip():
            return False
        quote_normal = _normalise(quote)
        if not quote_normal or quote_normal not in _normalise(str(page.get("text", ""))):
            return False
        if sentence_id:
            if sentence is None or quote_normal not in _normalise(str(sentence.get("text", ""))):
                return False
    return True


def _evaluate_evidence(
    question: Mapping[str, Any],
    evidence_refs: Sequence[Mapping[str, Any]],
    page_index: Mapping[tuple[str, int], Mapping[str, Any]] | None,
) -> str:
    required = question.get("requiredEvidenceRefs")
    if not isinstance(required, list) or not required:
        return "missing"
    if len(required) > 20:
        return "mismatch"
    if not evidence_refs:
        return "missing"
    revision = question.get("sourceRevisionId")
    if not isinstance(revision, str) or not revision.strip() or page_index is None:
        return "mismatch"
    required_keys = [_ref_key(ref) for ref in required if isinstance(ref, Mapping)]
    evidence_keys = [_ref_key(ref) for ref in evidence_refs if isinstance(ref, Mapping)]
    if (
        len(required_keys) != len(required)
        or len(evidence_keys) != len(evidence_refs)
        or any(key is None for key in (*required_keys, *evidence_keys))
    ):
        return "mismatch"
    target = {key for key in required_keys if key is not None}
    cited = {key for key in evidence_keys if key is not None}
    if len(target) != len(required_keys) or len(cited) != len(evidence_keys):
        return "mismatch"
    if not target.issubset(cited):
        return "missing" if cited.issubset(target) else "mismatch"
    if cited != target:
        return "mismatch"
    for ref in (*required, *evidence_refs):
        if not isinstance(ref, Mapping) or not _location_resolves(ref, revision, page_index):
            return "mismatch"
    return "supported"


def evaluate_english_chapter_attempt(
    *,
    question: Mapping[str, Any],
    answer: Mapping[str, Any],
    evidence_refs: Sequence[Mapping[str, Any]],
    source_pages: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Grade a reviewed English attempt while preserving source evidence lineage.

    Objective answers can be correct or incorrect against reviewed variants.
    Short answers outside those explicit variants and unsupported inferences
    abstain to ``needs_review``. Every decisive result still requires all pinned
    source references to resolve against the same source revision.
    """
    if not all(isinstance(value, Mapping) for value in (question, answer)):
        question = {}
        answer = {}
    if not isinstance(evidence_refs, Sequence) or isinstance(evidence_refs, (str, bytes)):
        evidence_refs = []
    if not isinstance(source_pages, Sequence) or isinstance(source_pages, (str, bytes)):
        source_pages = []
    all_evidence_refs_valid = all(isinstance(ref, Mapping) for ref in evidence_refs)
    safe_evidence_refs = [ref for ref in evidence_refs if isinstance(ref, Mapping)]
    safe_source_pages = [page for page in source_pages if isinstance(page, Mapping)]
    page_index = (
        _page_index(safe_source_pages)
        if len(safe_source_pages) == len(source_pages) and len(safe_source_pages) <= 80
        else None
    )
    evidence_verdict = (
        _evaluate_evidence(question, safe_evidence_refs, page_index)
        if all_evidence_refs_valid and len(safe_evidence_refs) <= 20
        else "mismatch"
    )

    raw_kind = question.get("questionKind")
    raw_mode = question.get("answerMode")
    kind = raw_kind if isinstance(raw_kind, str) and raw_kind in _QUESTION_KINDS else "unknown"
    mode = raw_mode if isinstance(raw_mode, str) and raw_mode in _ANSWER_MODES else "unknown"
    accepted = question.get("acceptedAnswers")
    answer_values = _answer_values(answer)
    accepted_values = (
        {_normalise(value) for value in accepted if isinstance(value, str) and _normalise(value)}
        if isinstance(accepted, list) and len(accepted) <= 20
        else set()
    )
    answer_matched = bool(answer_values and accepted_values.intersection(answer_values))
    unsupported_inference = kind == "inference" and (
        not isinstance(question.get("rubric"), Mapping)
        or question["rubric"].get("supportStatus") != "supported"
    )

    if (
        kind == "unknown"
        or mode == "unknown"
        or not accepted_values
        or not answer_values
        or unsupported_inference
        or (kind == "short_answer" and mode != "short_answer")
    ):
        assessment = "needs_review"
    elif mode == "objective" and not answer_matched:
        assessment = "incorrect"
    elif not answer_matched or evidence_verdict != "supported":
        assessment = "needs_review"
    else:
        assessment = "correct"

    feedback_message = {
        "correct": "答案与指定原文依据均匹配。",
        "incorrect": "答案与已审核的客观答案不匹配。",
        "needs_review": "答案或原文依据需要教师复核。",
    }[assessment]
    return {
        "assessment": assessment,
        "evidenceVerdict": evidence_verdict,
        "feedback": {
            "message": feedback_message,
            "answerStatus": "matched" if answer_matched else "unresolved",
            "evidenceStatus": evidence_verdict,
        },
        "evaluationEvidence": {
            "evaluatorVersion": EVALUATOR_VERSION,
            "questionKind": kind,
            "answerMode": mode,
            "assessment": assessment,
            "answerMatched": answer_matched,
            "evidenceVerdict": evidence_verdict,
            "evidenceRefs": [
                {
                    key: ref[key]
                    for key in ("sourceRevisionId", "page", "regionId", "sentenceId")
                    if key in ref
                }
                for ref in safe_evidence_refs
            ],
        },
    }


__all__ = ["EVALUATOR_VERSION", "evaluate_english_chapter_attempt"]
