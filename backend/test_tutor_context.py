from __future__ import annotations

import unittest
from unittest.mock import patch

from lesson_generation import generate_lesson, lesson_store
from model_runtime import ModelSelection, runtime
from tutor_checks import normalize_guide_cards, safe_canvas_action


class TutorContextTests(unittest.TestCase):
    def test_non_geometry_question_cannot_reuse_geometry_guide_cards(self) -> None:
        question = {
            "questionType": "choice",
            "chapter": "统计",
            "knowledgePoint": "平均数",
            "prompt": "五名女生的体重分别是多少？",
            "givens": [],
        }
        stale = [{
            "hint": "比较三角形 PAM 和 PBM",
            "question": "哪三组边相等？",
            "knowledge": ["全等三角形"],
            "canvasAction": "show-triangles",
        }]
        cards = normalize_guide_cards(stale, question)
        self.assertEqual([card["canvasAction"] for card in cards], ["show-base"] * 3)
        self.assertEqual(safe_canvas_action(question, "show-triangles"), "show-base")
        self.assertNotIn("三角形", " ".join(card["hint"] for card in cards))

    def test_model_failure_keeps_ocr_question_instead_of_geometry_sample(self) -> None:
        previous = runtime.selection
        runtime.selection = ModelSelection("mock", "static-demo")
        try:
            payload, cards, _run = generate_lesson(
                "2. 五名女生的体重分别为37、40、38、42、42 kg，平均体重是（ ）\n(A) 38 (B) 40 (C) 42 (D) 41"
            )
        finally:
            runtime.selection = previous
            lesson_store.pop(payload["question"]["id"], None)

        self.assertIn("五名女生", payload["question"]["prompt"])
        self.assertNotEqual(payload["question"]["id"], "geometry-perpendicular-bisector")
        self.assertEqual([step["action"] for step in payload["lessonSteps"]], ["show-base"] * 4)
        self.assertEqual([card["canvasAction"] for card in cards], ["show-base"] * 3)


if __name__ == "__main__":
    unittest.main()
