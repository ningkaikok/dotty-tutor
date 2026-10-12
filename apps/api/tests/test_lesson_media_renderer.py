"""Source-safe narration and early layout checks for lesson media scenes."""

from __future__ import annotations

import unittest

from application.job_worker import TerminalJobError
from application.lesson_media_renderer import LessonMediaRenderer, normalize_speech_text


class LessonMediaRendererTests(unittest.TestCase):
    def test_user_explains_supported_math_symbols_without_losing_them_in_narration(self) -> None:
        # Given a short explanation containing the symbols supported by the first renderer
        source = "解方程 y = −50，检查 × ÷ ≤ ≥ √ 和 ≠。"

        # When the text is prepared for speech
        narration = normalize_speech_text(source)

        # Then each symbol remains present or has the explicit spoken minus normalization
        self.assertEqual(narration, "解方程 y = -50，检查 × ÷ ≤ ≥ √ 和 ≠。")

    def test_user_submits_an_unsupported_formula_then_rendering_requires_a_human_rewrite(self) -> None:
        # Given a formula whose summation and subscript semantics are not supported
        formula = "$∑ x_i$"

        # When the renderer prepares the spoken explanation
        with self.assertRaises(TerminalJobError):
            normalize_speech_text(formula)

    def test_user_creates_a_storyboard_with_text_outside_the_safe_area_then_tts_is_not_started(self) -> None:
        # Given a source-linked scene whose narration cannot fit in the fixed canvas
        scene = {
            "title": "学习目标",
            "narration": "这是很长的一段讲解。" * 30,
            "sourceRefs": [{"page": 214}],
        }

        # When the storyboard is checked before the Worker reaches TTS
        with self.assertRaisesRegex(TerminalJobError, "超出画面安全区域"):
            LessonMediaRenderer.validate_storyboard(
                scenes=[scene, {**scene, "title": "讲解"}, {**scene, "title": "总结"}],
                source={"chapterTitle": "很长的课程名称", "sourceTitle": "教材", "license": "CC BY 4.0"},
            )


if __name__ == "__main__":
    unittest.main()
