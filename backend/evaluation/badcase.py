"""Badcase 登记簿：坏样本的生命周期管理与语料/报告之间的引用完整性。

登记簿（``badcases.json``）回答"我们已知哪些坏样本、各自处于什么状态"；
语料（``corpus.py``）回答"当前管线对它们的确定性期望是什么"。两者通过
``corpusEntryId`` 关联，关联完整性由单元测试强制：

- open 状态的坏样本应当有对应的特征化语料条目（缺陷在每次重放中可见）；
- fixed 状态必须带 ``resolutionNote`` 和 ``fixedRelease``，说明怎么修的、
  哪个版本修的——这是"修复后的样本自动进入回归集"的落点。

隐私边界：登记簿只保存脱敏描述和指向测试夹具/语料条目的引用，
不保存学生数据、完整教材原文或密钥。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evaluation.labels import BADCASE_LABELS, validate_tags

BADCASES_PATH = Path(__file__).resolve().parent / "badcases.json"

# 状态机：open → fixed | open → wontfix；fixed/wontfix 可以重新打开。
LEGAL_TRANSITIONS = {
    "open": {"fixed", "wontfix"},
    "fixed": {"open"},
    "wontfix": {"open"},
}
VALID_STATUSES = set(LEGAL_TRANSITIONS) | {"reopened"}
STAGES = {"ocr", "segmentation", "generation", "review", "storage", "tutoring", "review-model"}


def load_badcases(path: Path = BADCASES_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def find_badcase(badcases: dict[str, Any], badcase_id: str) -> dict[str, Any] | None:
    return next((b for b in badcases["badcases"] if b["id"] == badcase_id), None)


def transition_status(
    badcases: dict[str, Any],
    badcase_id: str,
    new_status: str,
    *,
    resolution_note: str = "",
    fixed_release: str = "",
) -> dict[str, Any]:
    """状态迁移并就地更新记录。返回被更新的记录，找不到时抛 KeyError。

    规则：迁入 ``fixed`` 必须写清修复说明和版本号——没有证据的"已修复"
    比未修复更危险，因为它会让别人以为问题已经不存在。
    """
    record = find_badcase(badcases, badcase_id)
    if record is None:
        raise KeyError(f"unknown badcase: {badcase_id}")
    current = record["status"]
    if current == "reopened":
        current = "open"
    if new_status not in LEGAL_TRANSITIONS.get(current, set()):
        raise ValueError(f"illegal transition {current} -> {new_status} for {badcase_id}")
    if new_status == "fixed":
        if not resolution_note.strip():
            raise ValueError("transition to 'fixed' requires a resolution note")
        if not fixed_release.strip():
            raise ValueError("transition to 'fixed' requires the release that fixed it")
        record["resolutionNote"] = resolution_note
        record["fixedRelease"] = fixed_release
    record["status"] = new_status
    return record


def validate_registry(data: dict[str, Any]) -> list[str]:
    """返回登记簿的全部一致性问题；空列表表示通过。供单元测试和 CLI 使用。"""
    problems: list[str] = []
    ids = [b["id"] for b in data["badcases"]]
    problems += [f"duplicate badcase id: {i}" for i in ids if ids.count(i) > 1]
    for record in data["badcases"]:
        badcase_id = record["id"]
        # 主标签必须是已注册的失败模式；流程性标记（known-bug 等）只允许出现在
        # 语料条目的 tags 里，不能充当登记簿的 label。
        if record["label"] not in BADCASE_LABELS:
            problems.append(f"{badcase_id}: unknown label '{record['label']}'")
        if record["stage"] not in STAGES:
            problems.append(f"{badcase_id}: unknown stage '{record['stage']}'")
        status = record["status"]
        if status == "fixed":
            if not record.get("resolutionNote"):
                problems.append(f"{badcase_id}: fixed without resolutionNote")
            if not record.get("fixedRelease"):
                problems.append(f"{badcase_id}: fixed without fixedRelease")
        elif status not in {"open", "wontfix"}:
            problems.append(f"{badcase_id}: unknown status '{status}'")
    return problems


def cross_check_with_corpus(
    data: dict[str, Any], corpus_entries: list[dict[str, Any]]
) -> list[str]:
    """登记簿与语料的引用完整性：open↔特征化条目、fixed↔回归条目互相可见。"""
    problems: list[str] = []
    corpus_by_id = {entry["id"]: entry for entry in corpus_entries}
    documented_bugs = {
        entry["documenting_bug"]: entry
        for entry in corpus_entries
        if entry.get("documenting_bug")
    }
    for record in data["badcases"]:
        badcase_id = record["id"]
        corpus_entry_id = record.get("corpusEntryId")
        if corpus_entry_id and corpus_entry_id not in corpus_by_id:
            problems.append(
                f"{badcase_id}: references missing corpus entry '{corpus_entry_id}'"
            )
        if record["status"] == "open" and badcase_id in documented_bugs:
            continue
    # 反向：语料里的特征化条目必须在登记簿中有对应记录，否则缺陷脱离了生命周期管理。
    for bug_ref, entry in documented_bugs.items():
        record = find_badcase(data, bug_ref)
        if record is None:
            problems.append(
                f"corpus entry '{entry['id']}' documents bug '{bug_ref}' "
                "which has no badcase record"
            )
        elif record["status"] != "open":
            problems.append(
                f"corpus entry '{entry['id']}' still characterizes '{bug_ref}' but the "
                f"badcase is '{record['status']}'; promote or fix the corpus entry"
            )
    return problems
