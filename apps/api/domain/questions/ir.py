"""试卷题目中间表示。

IR 是 OCR 与模型之间的稳定边界：它保存来源证据和题目边界，但不携带模型推断的
答案。这样后续求解或教学脚本出错时，可以重新执行对应阶段，而不必重新 OCR 或重切题。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class DocumentArtifact:
    """一次 OCR 产物的可追溯摘要。"""

    artifact_id: str
    source_pages: tuple[int, ...] = ()
    text_length: int = 0
    provider: str = "unknown"
    pipeline_version: str = "unknown"
    content_hash: str = ""
    structured_content_hash: str = ""
    structured_middle_hash: str = ""
    coordinate_system: str = "pdf-points"


@dataclass(frozen=True)
class QuestionIR:
    """一道原题的来源级表示，不包含答案和讲解。"""

    source_question_key: str
    number: str
    source_text: str
    stem: str
    options: tuple[str, ...] = ()
    givens: tuple[str, ...] = ()
    sub_questions: tuple[dict[str, Any], ...] = ()
    visual_asset_ids: tuple[str, ...] = ()
    source_pages: tuple[int, ...] = ()
    source_block_ids: tuple[str, ...] = ()
    source_answer: str = ""
    source_answer_reference: dict[str, Any] | None = None
    section_title: str = ""
    confidence: float = 0.0
    warnings: tuple[str, ...] = ()
    source_blocks: tuple[dict[str, Any], ...] = ()
    source_stable_id: str = ""
    source_origin: str = "markdown-fallback"

    def as_dict(self) -> dict[str, Any]:
        """转换为 JSON 友好的字段名，供模型提示词和持久化快照使用。"""
        raw = asdict(self)
        source_blocks = [dict(item) for item in raw.pop("source_blocks")]
        source_origin = raw.pop("source_origin")
        return {
            "schemaVersion": "question-ir-v1",
            "sourceQuestionKey": raw.pop("source_question_key"),
            "sourceText": raw.pop("source_text"),
            "givens": list(raw.pop("givens")),
            "subQuestions": [dict(item) for item in raw.pop("sub_questions")],
            "visualAssetIds": list(raw.pop("visual_asset_ids")),
            "sourcePages": list(raw.pop("source_pages")),
            "sourceBlockIds": list(raw.pop("source_block_ids")),
            "sourceAnswer": raw.pop("source_answer"),
            "sourceAnswerReference": raw.pop("source_answer_reference"),
            "sectionTitle": raw.pop("section_title"),
            "confidence": raw.pop("confidence"),
            "warnings": list(raw.pop("warnings")),
            "sourceBlocks": source_blocks,
            "sourceStableId": raw.pop("source_stable_id"),
            "sourceOrigin": source_origin or (
                "mineru"
                if any(str(item.get("origin", "")).startswith("mineru") for item in source_blocks)
                else "markdown-fallback"
            ),
            **raw,
        }


@dataclass(frozen=True)
class ExamIR:
    """整份试卷的结构摘要及题目列表。"""

    artifact: DocumentArtifact
    questions: tuple[QuestionIR, ...] = ()
    sections: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)
    version: str = "exam-ir-v1"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "exam-ir-v1",
            "version": self.version,
            "artifact": asdict(self.artifact),
            "sections": [dict(section) for section in self.sections],
            "questions": [question.as_dict() for question in self.questions],
            "questionCount": len(self.questions),
            "warnings": list(self.warnings),
            "diagnostics": dict(self.diagnostics),
        }


@dataclass(frozen=True)
class SolutionIR:
    """独立求解阶段的结果。"""

    question_type: str = "short-answer"
    correct_answer: str = ""
    correct_answers: tuple[str, ...] = ()
    answer_spec: dict[str, Any] | None = None
    blanks: tuple[dict[str, Any], ...] = ()
    interaction: dict[str, Any] | None = None
    chapter: str = "教材练习"
    knowledge_point: str = "分步推理"


@dataclass(frozen=True)
class VerificationIR:
    """独立核验阶段的结果，不直接覆盖求解结论。"""

    status: str = "needs_review"
    solver_agreement: bool = False
    source_answer: str = ""
    conflicts: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    confidence: float = 0.0
    needs_human_review: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "solverAgreement": self.solver_agreement,
            "sourceAnswer": self.source_answer,
            "conflicts": list(self.conflicts),
            "checks": list(self.checks),
            "confidence": self.confidence,
            "needsHumanReview": self.needs_human_review,
        }


@dataclass(frozen=True)
class TutorScript:
    """教学脚本阶段的结果。"""

    lesson_steps: tuple[dict[str, Any], ...] = ()
    guide_cards: tuple[dict[str, Any], ...] = ()


def bounded_confidence(value: Any, default: float = 0.0) -> float:
    """将不可信的模型/解析器分数限制在 0 到 1。"""
    try:
        return round(max(0.0, min(float(value), 1.0)), 3)
    except (TypeError, ValueError):
        return default


def stage_cache_key(
    stage: str,
    prompt: str,
    *,
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
    rerun_token: str = "",
) -> str:
    """生成阶段产物的内容寻址键，避免未来缓存把不同契约的结果混用。"""
    material = {
        "stage": stage,
        "prompt": prompt,
        "provider": provider,
        "model": model,
        "promptVersion": prompt_version,
        "schemaVersion": schema_version,
        "rerunToken": rerun_token,
    }
    return hashlib.sha256(json.dumps(material, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
