"""脱敏 ExamIR 与 staged-pipeline golden evaluator。

该评测不调用模型和数据库，只验证来源守恒、四阶段形状、4 步/3 卡契约以及核验门禁，
适合在提示词或切分规则变更前后做快速回归。
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from application.services.lesson_generation import _stage_artifact_cache, _staged_lesson
from application.services.stage_artifact_cache import StageArtifactCache
from application.services.staged_question_generation import STAGES, can_run_tutor_script
from domain.questions.exam_ir import build_exam_ir
from domain.questions.ir import stage_cache_key
from infrastructure.runtime.model_runtime import ModelSelection, runtime


def evaluate_exam_ir_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    source = str(fixture.get("source", ""))
    with TemporaryDirectory() as directory:
        asset_dir = Path(directory)
        structured = fixture.get("structuredContentList")
        if isinstance(structured, list):
            (asset_dir / "source.content_list.json").write_text(
                json.dumps(structured, ensure_ascii=False), encoding="utf-8"
            )
        exam = build_exam_ir(
            source,
            asset_dir=asset_dir,
            batch_id=str(fixture.get("batchId", "golden")),
            start_page=int(fixture.get("startPage", 1)),
            end_page=int(fixture.get("endPage", fixture.get("startPage", 1))),
        )
    expected = fixture.get("expect", {})
    expected_block = str(expected.get("sourceBlockId", ""))
    checks = {
        "questionCount": len(exam.questions) == int(expected.get("questionCount", len(exam.questions))),
        "sourceBlockIds": all(bool(question.source_block_ids) for question in exam.questions),
        "sourcePages": all(bool(question.source_pages) for question in exam.questions),
        "assetIds": all(
            set(question.visual_asset_ids) >= set(expected.get("assetIds", []))
            for question in exam.questions
        ),
        "structuredOrigin": all(
            question.source_origin.startswith("mineru")
            for question in exam.questions
        ) if fixture.get("structuredContentList") else True,
        "expectedSourceBlock": all(
            expected_block in question.source_block_ids
            for question in exam.questions
        ) if expected_block else True,
    }
    return {"passed": all(checks.values()), "checks": checks, "examIR": exam.as_dict()}


def evaluate_staged_pipeline_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    stages = fixture.get("stages") or {}
    verification = stages.get("verification") if isinstance(stages, dict) else None
    verification_status = str((stages.get("verification") or {}).get("status") or "needs_review")
    model_responses = [
        ({"questionNumber": "7", "stem": "7. 求一个数的两倍。", "questionType": "numeric", "options": []}, {"provider": "mock", "model": "golden", "fallback": False}),
        ({"questionType": "numeric", "correctAnswer": "6", "correctAnswers": [], "answerSpec": {"answerType": "numeric", "expected": "6", "accepted": ["6"], "tolerance": 0, "unit": ""}}, {"provider": "mock", "model": "golden", "fallback": False}),
        ({"status": verification_status, "conflicts": [], "checks": [], "confidence": 1, "needsHumanReview": verification_status != "verified"}, {"provider": "mock", "model": "golden", "fallback": False}),
        ({"lessonSteps": [{"title": "读题", "text": "找条件", "speechText": "找条件"}] * 4, "guideCards": [{"hint": "看条件", "question": "条件是什么？"}] * 3}, {"provider": "mock", "model": "golden", "fallback": False}),
    ]
    original_selection = runtime.selection
    runtime.selection = ModelSelection("codex", "golden")
    try:
        with TemporaryDirectory() as directory, patch.dict(_stage_artifact_cache, {}, clear=True), patch(
            "application.services.lesson_generation.runtime.generate_json",
            side_effect=model_responses,
        ) as generate:
            _payload, _cards, actual_run = _staged_lesson(
                "7. 求一个数的两倍。",
                asset_dir=Path(directory),
                question_ir={
                    "sourceQuestionKey": "golden-stage-question",
                    "sourceStableId": "golden-stage-question",
                    "number": "7",
                    "stem": "7. 求一个数的两倍。",
                    "sourceText": "7. 求一个数的两倍。",
                    "options": [],
                    "visualAssetIds": [],
                },
            )
            actual_calls = generate.call_count
    finally:
        runtime.selection = original_selection
    actual_stage_names = tuple(item.get("name") for item in actual_run.get("stages", []))
    actual_tutor_called = actual_calls == 4 and not actual_run["stages"][-1].get("skipped")
    with TemporaryDirectory() as directory:
        cache = StageArtifactCache(Path(directory), max_entries=8, max_bytes=8_000)
        base_key = stage_cache_key(
            "solution", "golden prompt", provider="mock", model="golden",
            prompt_version="p1", schema_version="s1",
        )
        cache.save("golden-question", "solution", base_key, {"answer": "3"})
        cache_hit = cache.load("golden-question", "solution", base_key) == {"answer": "3"}
        forced_key = stage_cache_key(
            "solution", "golden prompt", provider="mock", model="golden",
            prompt_version="p1", schema_version="s1", rerun_token="rerun-1",
        )
        forced_miss = cache.load("golden-question", "solution", forced_key) is None
    checks = {
        "fourStages": actual_stage_names == STAGES and tuple(stages) == STAGES,
        "fourSteps": len((stages.get("tutor-script") or {}).get("lessonSteps", [])) == (4 if verification_status == "verified" else 0),
        "threeCards": len((stages.get("tutor-script") or {}).get("guideCards", [])) == (3 if verification_status == "verified" else 0),
        "verificationGate": actual_tutor_called == can_run_tutor_script(verification),
        "actualModelCallCount": actual_calls == (4 if verification_status == "verified" else 3),
        "cacheHit": cache_hit,
        "forcedRerunMissesOldKey": forced_miss,
    }
    return {"passed": all(checks.values()), "checks": checks}


__all__ = ["evaluate_exam_ir_fixture", "evaluate_staged_pipeline_fixture"]
