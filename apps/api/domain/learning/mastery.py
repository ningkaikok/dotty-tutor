"""知识点身份与掌握度派生规则。

掌握度只消费不可变作答证据，不保存会随请求顺序变化的 EMA 中间状态。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping

ALGORITHM_VERSION = "mastery-v2"
_WHITESPACE = re.compile(r"\s+")
_LEGACY_ALIASES = {
    "correct": "correct",
    "partial": "partial",
    "incorrect": "incorrect",
    "wrong": "incorrect",
    "error": "incorrect",
}
_ASSESSMENT_SCORE = {"correct": 1.0, "partial": 0.55, "incorrect": 0.0}
_CONFIDENCE_BY_EVIDENCE_COUNT = {1: 0.6, 2: 0.7, 3: 0.8, 4: 0.9, 5: 1.0}


def normalize_knowledge_point_name(value: object) -> str:
    """规范化旧字符串/枚举值，保证同一发布版本只创建一个知识点实体。"""
    raw = getattr(value, "value", value)
    name = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", str(raw or ""))).strip()
    if not name:
        raise ValueError("知识点名称不能为空")
    return name[:160]


def knowledge_point_id(publication_id: str, name: object) -> str:
    """生成发布版本作用域内稳定、不可由客户端猜测语义的知识点 ID。"""
    normalized = normalize_knowledge_point_name(name)
    digest = hashlib.sha256(f"{publication_id}\0{normalized}".encode("utf-8")).hexdigest()[:32]
    return f"kp-{digest}"


@dataclass(frozen=True)
class KnowledgePoint:
    knowledge_point_id: str
    publication_id: str
    name: str
    normalized_name: str


def assessment_score(assessment: object) -> float:
    """兼容旧枚举别名，但只允许三档掌握度证据进入计算。"""
    key = getattr(assessment, "value", assessment)
    normalized = _LEGACY_ALIASES.get(str(key).strip().lower(), str(key).strip().lower())
    try:
        return _ASSESSMENT_SCORE[normalized]
    except KeyError as error:
        raise ValueError(f"不支持的作答判定：{assessment}") from error


def _timestamp(value: object) -> float:
    try:
        return float(str(value or 0))
    except (TypeError, ValueError):
        return 0.0


def derive_mastery(evidence: Iterable[Mapping[str, object]]) -> dict[str, float | int | str | None]:
    """从每道题的最新证据派生掌握度。

    输入可以包含历史重复作答；同一 ``(publication_id, question_id)`` 只保留
    ``created_at`` 最大、同刻以 ``attempt_id`` 稳定打破平局的一条。这样离线
    乱序补传只会重算为同一个结果。
    """
    latest: dict[tuple[str, str], Mapping[str, object]] = {}
    for item in evidence:
        publication_id = str(item.get("publication_id") or "")
        question_id = str(item.get("question_id") or "")
        if not publication_id or not question_id:
            continue
        key = (publication_id, question_id)
        current = latest.get(key)
        candidate_order = (_timestamp(item.get("created_at")), str(item.get("attempt_id") or ""))
        if current is None:
            latest[key] = item
            continue
        current_order = (_timestamp(current.get("created_at")), str(current.get("attempt_id") or ""))
        if candidate_order > current_order:
            latest[key] = item

    scores = [assessment_score(item.get("assessment")) for item in latest.values()]
    evidence_count = min(len(scores), 5)
    confidence = _CONFIDENCE_BY_EVIDENCE_COUNT.get(evidence_count, 0.0)
    raw_score = round(sum(scores) / len(scores), 4) if scores else 0.0
    last_practiced_at = max(
        (_timestamp(item.get("created_at")) for item in latest.values()),
        default=None,
    )
    return {
        "raw_score": raw_score,
        "score": round(raw_score * confidence, 4),
        "evidence_confidence": confidence,
        "evidence_count": evidence_count,
        "attempt_count": len(scores),
        "correct_count": sum(assessment_score(item.get("assessment")) == 1.0 for item in latest.values()),
        "last_practiced_at": last_practiced_at,
        "algorithm_version": ALGORITHM_VERSION,
    }
