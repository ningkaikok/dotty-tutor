"""把新阶段结果投影成历史 questionPayload 形状。"""

from __future__ import annotations

from typing import Any


def project_question_ir(question_ir: dict[str, Any], question: dict[str, Any]) -> dict[str, Any]:
    """覆盖来源事实字段，避免审核或模型改写原题。"""
    projected = dict(question)
    projected["questionNumber"] = question_ir.get("number", "")
    projected["prompt"] = question_ir.get("stem") or question_ir.get("sourceText", "")
    projected["options"] = list(question_ir.get("options") or [])
    projected["givens"] = list(question_ir.get("givens") or [])
    projected["subQuestions"] = [dict(item) for item in question_ir.get("subQuestions") or []]
    projected["imageReferences"] = list(question_ir.get("visualAssetIds") or [])
    existing_provenance = projected.get("sourceProvenance")
    provenance = dict(existing_provenance) if isinstance(existing_provenance, dict) else {}
    projected["sourceProvenance"] = {
        **provenance,
        "sourceQuestionKey": question_ir.get("sourceQuestionKey"),
        "sourceStableId": question_ir.get("sourceStableId"),
        "sourcePages": list(question_ir.get("sourcePages") or []),
        "sourceBlockIds": list(question_ir.get("sourceBlockIds") or []),
        "visualAssetIds": list(question_ir.get("visualAssetIds") or []),
        "confidence": question_ir.get("confidence", 0),
        "warnings": list(question_ir.get("warnings") or []),
    }
    return projected
