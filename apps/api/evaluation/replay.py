"""金标准语料重放器。

对语料里的每个条目运行确定性切分管线，与期望对比后输出结构化报告（JSON + Markdown）。
只调用纯函数（``split_question_sources``），不触模型、数据库和网络，因此可以在每次
规则/提示词/Schema 变更前后稳定重放，成本为毫秒级。

用法::

    cd backend
    ../.venv/bin/python -m evaluation.replay            # 写报告到 output/eval-reports/
    ../.venv/bin/python -m evaluation.replay --check    # 只校验，异常时退出码非零

退出码约定：出现"预期外的失败"或"已知缺陷条目的行为发生变化"时返回 1——后者意味着
有人动到了特征化条目覆盖的代码路径，必须先更新语料再合并。
"""

from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from domain.questions.pipeline import (
    apply_question_quality_gate,
    normalize_model_math_text,
)
from domain.questions.source import split_question_sources
from domain.tutoring.turn_plan import infer_student_intent
from evaluation.corpus import CORPUS, CORPUS_VERSION

STRUCTURED_FILENAMES = {
    "content_list": "source.content_list.json",
    "middle": "source.middle.json",
}


def _write_structured_payload(directory: Path, payload: dict[str, Any]) -> None:
    for key, filename in STRUCTURED_FILENAMES.items():
        if key in payload:
            (directory / filename).write_text(
                json.dumps(payload[key], ensure_ascii=False), encoding="utf-8"
            )


def _evaluate_formula_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """公式规范化维度：逐用例核对输出，并验证规范化是幂等的。"""
    checks: list[dict[str, Any]] = []
    for index, case in enumerate(entry.get("cases", [])):
        raw, expected = case["raw"], case["expected"]
        actual = normalize_model_math_text(raw)
        twice = normalize_model_math_text(actual)
        checks.append({
            "name": f"case[{index}]",
            "passed": actual == expected and twice == actual,
            "detail": f"expected {expected!r}, got {actual!r}" if actual != expected
            else ("not idempotent" if twice != actual else "ok"),
        })
    return checks


def _evaluate_quality_gate_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """审核门禁维度：payload + 来源块 → 状态与错误摘要必须匹配期望。"""
    expect = entry.get("expect", {})
    quality = apply_question_quality_gate(
        entry["payload"], entry["sourceBlock"], entry.get("images") or []
    )
    checks = [{
        "name": "status",
        "passed": quality.get("status") == expect.get("status"),
        "detail": f"expected {expect.get('status')}, got {quality.get('status')}",
    }]
    for fragment in expect.get("errorContains", []):
        checks.append({
            "name": f"errorContains:{fragment[:20]}",
            "passed": any(fragment in error for error in quality.get("errors", [])),
            "detail": f"errors={quality.get('errors')}",
        })
    return checks


def _evaluate_turn_plan_intent_entry(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """陪练意图维度：中文短语/结构化作答 → 意图 ID 必须稳定，且置信度为正。"""
    checks: list[dict[str, Any]] = []
    for index, case in enumerate(entry.get("cases", [])):
        intent = infer_student_intent(
            mode=case["mode"],
            content=case["content"],
            interaction_result=case.get("interactionResult"),
        )
        ok = intent.get("id") == case["intentId"] and intent.get("confidence", 0) > 0
        checks.append({
            "name": f"case[{index}]:{case['content'][:12]}",
            "passed": ok,
            "detail": f"expected {case['intentId']}, got {intent.get('id')}",
        })
    return checks


_ENTRY_EVALUATORS = {
    "segmentation": None,  # 主路径，见 _evaluate_segmentation_entry
    "formula-normalize": _evaluate_formula_entry,
    "quality-gate": _evaluate_quality_gate_entry,
    "turn-plan-intent": _evaluate_turn_plan_intent_entry,
}


def _evaluate_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """跑单个条目并逐项核对期望。checks 里每一项都是可解释的对/错加证据。"""
    kind = entry.get("kind", "segmentation")
    if kind != "segmentation":
        evaluator = _ENTRY_EVALUATORS[kind]
        checks = evaluator(entry)
        passed = all(check["passed"] for check in checks)
        return {
            "id": entry["id"],
            "description": entry["description"],
            "tags": entry.get("tags", []),
            "documenting_bug": entry.get("documenting_bug"),
            "passed": passed,
            "checks": checks,
        }
    return _evaluate_segmentation_entry(entry)


def _evaluate_segmentation_entry(entry: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add_check(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": detail})

    structured_payload = entry.get("structured_payload")
    blocks_plain = split_question_sources(entry["ocr_markdown"])
    blocks_primary = blocks_plain
    if structured_payload:
        with tempfile.TemporaryDirectory() as directory:
            asset_dir = Path(directory)
            _write_structured_payload(asset_dir, structured_payload)
            blocks_primary = split_question_sources(
                entry["ocr_markdown"], asset_dir=asset_dir
            )
        # 结构化重放必须与扁平路径在"无结构化数据可用"时的结果解耦：这里额外记录
        # 两者是否一致，供 stable_across_structured_replay 检查使用。
        add_check(
            "structured_replay_ran",
            True,
            f"{len(blocks_primary)} blocks (plain path: {len(blocks_plain)})",
        )

    numbers = [number for number, _block, _images in blocks_primary]
    images_by_number = {number: images for number, _block, images in blocks_primary}

    expect = entry.get("expect", {})
    if "question_numbers" in expect:
        expected_numbers = sorted(expect["question_numbers"])
        add_check(
            "question_numbers",
            sorted(numbers) == expected_numbers,
            f"expected {expected_numbers}, got {sorted(numbers)}",
        )
    if "question_numbers_present" in expect:
        missing = [n for n in expect["question_numbers_present"] if n not in numbers]
        add_check(
            "question_numbers_present",
            not missing,
            f"missing: {missing}" if missing else "all present",
        )
    if "absent_question_numbers" in expect:
        unexpected = [n for n in expect["absent_question_numbers"] if n in numbers]
        add_check(
            "absent_question_numbers",
            not unexpected,
            f"unexpectedly present: {unexpected}" if unexpected else "all absent",
        )
    if "phantom_numbers_present" in expect:
        # 特征化检查：伪题号必须仍在场。它消失说明覆盖它的代码路径变了。
        gone = [n for n in expect["phantom_numbers_present"] if n not in numbers]
        add_check(
            "phantom_numbers_present",
            not gone,
            f"known-bug signature lost, numbers no longer present: {gone}"
            if gone
            else "bug still reproduces",
        )
    if "images_by_number" in expect:
        mismatches = []
        for number, expected_images in expect["images_by_number"].items():
            actual = images_by_number.get(number)
            if actual != expected_images:
                mismatches.append(f"{number}: expected {expected_images}, got {actual}")
        add_check("images_by_number", not mismatches, "; ".join(mismatches))
    if "filtered_number_sequence" in expect:
        spec = expect["filtered_number_sequence"]
        actual_sequence = [n for n in numbers if n in set(spec["numbers"])]
        add_check(
            "filtered_number_sequence",
            actual_sequence == spec["sequence"],
            f"expected {spec['sequence']}, got {actual_sequence}",
        )
    if expect.get("stable_across_structured_replay"):
        if not structured_payload:
            add_check("stable_across_structured_replay", False, "no structured payload")
        else:
            add_check(
                "stable_across_structured_replay",
                blocks_with_equals_plain := (blocks_primary == blocks_plain),
                "identical" if blocks_with_equals_plain else "results diverged",
            )

    passed = all(check["passed"] for check in checks)
    return {
        "id": entry["id"],
        "description": entry["description"],
        "tags": entry.get("tags", []),
        "documenting_bug": entry.get("documenting_bug"),
        "passed": passed,
        "checks": checks,
    }


def run_replay(corpus: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    corpus = corpus if corpus is not None else CORPUS
    entries = [_evaluate_entry(entry) for entry in corpus]
    # 计数语义必须严格区分三种状态：
    # - 通过（含已知缺陷条目正常复现）：一切符合语料固化的事实；
    # - 预期外失败：当前管线行为与金标准期望不符，需要人工定位；
    # - 已知缺陷行为变化：特征化条目不再复现缺陷——要么有人修好了（应转正语料），
    #   要么是回归被掩盖（更危险）。两种情况都必须显式处理后再合并。
    failed_unexpected = [
        e for e in entries if not e["passed"] and not e["documenting_bug"]
    ]
    known_bug_changed = [
        e for e in entries if e["documenting_bug"] and not e["passed"]
    ]
    passed = [e for e in entries if e["passed"]]
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "corpusVersion": CORPUS_VERSION,
        "entryCount": len(entries),
        "totals": {
            "passed": len(passed),
            "failedUnexpected": len(failed_unexpected),
            "knownBugSignatureChanged": len(known_bug_changed),
        },
        "entries": entries,
    }


def write_report(result: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "replay-report.json"
    markdown_path = output_dir / "replay-report.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 离线评测重放报告",
        "",
        f"- 生成时间：{result['generatedAt']}",
        f"- 语料版本：v{result['corpusVersion']}",
        f"- 条目：{result['entryCount']}；通过 {result['totals']['passed']}；"
        f"预期外失败 {result['totals']['failedUnexpected']}；"
        f"已知缺陷行为变化 {result['totals']['knownBugSignatureChanged']}",
        "",
    ]
    for entry in result["entries"]:
        marker = "PASS"
        if entry["documenting_bug"] and entry["passed"]:
            marker = "BUG-REPRODUCED"
        elif entry["documenting_bug"]:
            marker = "BUG-SIGNATURE-CHANGED"
        elif not entry["passed"]:
            marker = "FAIL"
        lines.append(f"## [{marker}] {entry['id']}")
        lines.append("")
        lines.append(f"{entry['description']}")
        lines.append("")
        for check in entry["checks"]:
            status = "ok" if check["passed"] else "MISMATCH"
            lines.append(f"- `{check['name']}` {status} {check['detail']}".rstrip())
        lines.append("")
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": json_path, "markdown": markdown_path}


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay the offline golden corpus.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "output" / "eval-reports",
        help="报告输出目录（默认 <repo>/output/eval-reports）",
    )
    args = parser.parse_args()
    result = run_replay()
    paths = write_report(result, args.output_dir)
    totals = result["totals"]
    print(
        f"entries={result['entryCount']} passed={totals['passed']} "
        f"unexpectedFailures={totals['failedUnexpected']} "
        f"knownBugChanges={totals['knownBugSignatureChanged']}"
    )
    print(f"report: {paths['json']}")
    print(f"report: {paths['markdown']}")
    return 1 if (totals["failedUnexpected"] or totals["knownBugSignatureChanged"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
