"""ExamIR 与分阶段题目生成的回归测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from application.services.lesson_generation import (
    generate_lesson,
    lesson_store,
    review_lesson_payload,
)
from application.services.stage_artifact_cache import StageArtifactCache
from application.services.staged_question_generation import (
    STAGES,
    can_run_tutor_script,
    stages_for_rerun,
)
from domain.questions.exam_ir import build_exam_ir
from infrastructure.runtime.model_runtime import ModelSelection, runtime


class QuestionIRTests(unittest.TestCase):
    def test_exam_ir_keeps_boundary_and_visual_provenance(self) -> None:
        source = """<!-- page 2 -->
## 一、选择题
7. 如图，求线段 AB 的长度。
![](images/q7.png)
"""
        exam = build_exam_ir(source, batch_id="batch-001", start_page=2, end_page=2, provider="mineru")

        self.assertEqual(len(exam.questions), 1)
        question = exam.questions[0].as_dict()
        self.assertEqual(question["number"], "7")
        self.assertEqual(question["sourcePages"], [2])
        self.assertEqual(question["visualAssetIds"], ["q7.png"])
        self.assertTrue(question["sourceBlockIds"])
        self.assertEqual(question["sourceQuestionKey"], "batch-001-q-7")
        self.assertLessEqual(question["confidence"], 1)


class StagedGenerationTests(unittest.TestCase):
    def test_stage_cache_is_persistent_bounded_and_corruption_safe(self) -> None:
        with TemporaryDirectory() as directory:
            cache = StageArtifactCache(Path(directory))
            self.assertTrue(cache.save("q-7", "solution", "key-1", {"answer": "3"}))
            self.assertEqual(cache.load("q-7", "solution", "key-1"), {"answer": "3"})
            cache_file = Path(directory) / "stage-cache" / "q-7" / "solution" / "key-1.json"
            cache_file.write_text("{broken", encoding="utf-8")
            self.assertIsNone(cache.load("q-7", "solution", "key-1"))

    def test_reviewer_cannot_rewrite_question_ir_facts(self) -> None:
        question_ir = {
            "sourceQuestionKey": "batch-q-7",
            "sourceStableId": "stable-7",
            "number": "7",
            "stem": "7. 原题题干",
            "options": ["A. 原选项", "B. 另一选项"],
            "givens": ["AB=3"],
            "subQuestions": [{"label": "(1)", "prompt": "原小问"}],
            "visualAssetIds": ["diagram.png"],
            "sourcePages": [2],
            "sourceBlockIds": ["mineru-7"],
        }
        payload = {"question": {"id": "generated-7", "prompt": "原题题干"}, "lessonSteps": []}
        reviewed = {"question": {**payload["question"], "prompt": "审核改写", "options": ["X"]}, "lessonSteps": []}
        with patch(
            "application.services.lesson_generation.runtime_reviewer.review",
            return_value=(reviewed, {"status": "reviewed"}),
        ):
            result, _review = review_lesson_payload(payload, question_ir["stem"], [], [], question_ir=question_ir)
        question = result["question"]
        self.assertEqual(question["prompt"], question_ir["stem"])
        self.assertEqual(question["questionNumber"], "7")
        self.assertEqual(question["options"], question_ir["options"])
        self.assertEqual(question["givens"], question_ir["givens"])
        self.assertEqual(question["subQuestions"], question_ir["subQuestions"])
        self.assertEqual(question["imageReferences"], question_ir["visualAssetIds"])

    def test_stage_cache_total_bytes_and_rerun_key(self) -> None:
        with TemporaryDirectory() as directory:
            cache = StageArtifactCache(Path(directory), max_entries=10, max_bytes=1024)
            self.assertTrue(cache.save("q-7", "solution", "key-1", {"text": "x" * 600}))
            self.assertTrue(cache.save("q-7", "verification", "key-2", {"text": "y" * 600}))
            files = list((Path(directory) / "stage-cache").rglob("*.json"))
            self.assertLessEqual(sum(path.stat().st_size for path in files), 1024)
            self.assertEqual(stages_for_rerun("solution"), STAGES[1:])
            self.assertTrue(can_run_tutor_script({"status": "verified"}))

    def test_forced_stage_rerun_calls_target_and_skips_extraction(self) -> None:
        original_selection = runtime.selection
        runtime.selection = ModelSelection("codex", "default")
        prior = {
            "extraction": {"raw": {"questionNumber": "7", "stem": "7. 求 x"}, "run": {}},
            "solution": {"raw": {"questionType": "numeric", "correctAnswer": "3"}, "run": {}},
            "verification": {"raw": {"status": "verified", "solverAgreement": True}, "run": {}},
        }
        script = {"lessonSteps": [{"title": "读题", "text": "先读题", "speechText": "先读题"}] * 4,
                  "guideCards": [{"hint": "看条件", "question": "条件是什么？"}] * 3}
        try:
            with TemporaryDirectory() as directory, patch(
                "application.services.lesson_generation.runtime.generate_json",
                return_value=(script, {"provider": "codex", "model": "default", "fallback": False}),
            ):
                _payload, _cards, run = generate_lesson(
                    "7. 求 x", asset_dir=Path(directory), target_stage="tutor-script",
                    prior_stage_artifacts=prior, rerun_token="forced-1",
                )
        finally:
            runtime.selection = original_selection
        stages = {stage["name"]: stage for stage in run["stages"]}
        # 只重跑 tutor-script：前三个阶段必须原样复用产物，而不是重新调用模型。
        for name in ("extraction", "solution", "verification"):
            self.assertEqual(stages[name]["provider"], "artifact")
            self.assertTrue(stages[name]["cacheHit"])
        self.assertEqual(stages["tutor-script"]["provider"], "codex")
        self.assertFalse(stages["tutor-script"]["cacheHit"])
        self.assertTrue(stages["tutor-script"]["cacheKey"])

    def test_solution_rerun_reuses_extraction_and_runs_downstream(self) -> None:
        original_selection = runtime.selection
        runtime.selection = ModelSelection("codex", "default")
        prior = {
            "extraction": {"raw": {"questionNumber": "7", "stem": "7. 求 x"}, "run": {}},
        }
        responses = [
            ({"questionType": "numeric", "correctAnswer": "3"}, {"provider": "codex", "model": "default", "fallback": False}),
            ({"status": "verified", "solverAgreement": True}, {"provider": "codex", "model": "default", "fallback": False}),
            ({"lessonSteps": [{"title": "读题", "text": "先读题", "speechText": "先读题"}] * 4,
              "guideCards": [{"hint": "看条件", "question": "条件是什么？"}] * 3},
             {"provider": "codex", "model": "default", "fallback": False}),
        ]
        try:
            with TemporaryDirectory() as directory, patch(
                "application.services.lesson_generation.runtime.generate_json",
                side_effect=responses,
            ):
                _payload, _cards, run = generate_lesson(
                    "7. 求 x", asset_dir=Path(directory), target_stage="solution",
                    prior_stage_artifacts=prior, rerun_token="forced-2",
                )
        finally:
            runtime.selection = original_selection
        stages = {stage["name"]: stage for stage in run["stages"]}
        # 只有 extraction 在 prior 里，重跑目标是 solution：extraction 必须复用，
        # 其余三个阶段必须真正重新生成，而不是从缓存搬运。
        self.assertEqual(stages["extraction"]["provider"], "artifact")
        self.assertTrue(stages["extraction"]["cacheHit"])
        for name in ("solution", "verification", "tutor-script"):
            self.assertEqual(stages[name]["provider"], "codex")
            self.assertFalse(stages[name]["cacheHit"])

    def test_stage_rerun_invalidates_only_target_and_downstream(self) -> None:
        self.assertEqual(stages_for_rerun("solution"), STAGES[1:])
        self.assertEqual(stages_for_rerun("tutor-script"), ("tutor-script",))
        self.assertFalse(can_run_tutor_script({"status": "needs_review"}))
        self.assertTrue(can_run_tutor_script({"status": "verified"}))

    def test_generation_has_separate_extraction_solution_and_script_calls(self) -> None:
        original_selection = runtime.selection
        runtime.selection = ModelSelection("codex", "default")
        source = "7. 如图，计算 AB。\n![](images/q7.png)"
        runs = [{"provider": "codex", "model": "default", "fallback": False}] * 4
        responses = [
            ({"questionNumber": "7", "stem": "7. 如图，计算 AB。 ⟦IMG_1⟧", "questionType": "numeric", "chapter": "几何", "knowledgePoint": "长度计算", "givens": ["AB"], "options": [], "subQuestions": [], "imageReferences": []}, runs[0]),
            ({"questionType": "numeric", "correctAnswer": "3", "correctAnswers": [], "blanks": [], "answerSpec": {"answerType": "numeric", "expected": "3", "accepted": ["3"], "tolerance": 0, "unit": ""}, "interaction": {"type": "none", "instruction": "", "points": [], "requiredConnections": []}, "chapter": "几何", "knowledgePoint": "长度计算"}, runs[1]),
            ({"status": "verified", "solverAgreement": True, "sourceAnswer": "", "conflicts": [], "checks": ["题干完整"], "confidence": 0.9, "needsHumanReview": False}, runs[2]),
            ({"lessonSteps": [{"title": "读题", "text": "找出条件", "speechText": "先找条件"}] * 4, "guideCards": [{"stuckAt": "", "knowledge": [], "hint": "看已知", "question": "有哪些条件？"}] * 3}, runs[3]),
        ]
        try:
            with patch("application.services.lesson_generation.runtime.generate_json", side_effect=responses) as generate:
                payload, cards, run = generate_lesson(source)
        finally:
            runtime.selection = original_selection
            lesson_store.pop(payload["question"]["id"], None)

        self.assertEqual([stage["name"] for stage in run["stages"]], ["extraction", "solution", "verification", "tutor-script"])
        # 四个阶段都必须是真实模型调用产出，不能有任何一个悄悄复用缓存或产物。
        self.assertTrue(all(stage["provider"] == "codex" and not stage["cacheHit"] for stage in run["stages"]))
        self.assertEqual(payload["question"]["questionNumber"], "7")
        self.assertEqual(payload["question"]["correctAnswer"], "3")
        self.assertEqual(payload["question"]["sourceProvenance"]["visualAssetIds"], ["q7.png"])
        self.assertEqual(len(cards), 3)
        for call in generate.call_args_list:
            self.assertNotIn("images/q7.png", call.args[0])
