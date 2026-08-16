"""OCR 题块边界与公式保守修复的回归测试。"""

from __future__ import annotations

import unittest

from domain.questions.pipeline import apply_question_quality_gate, normalize_model_math_text
from domain.questions.source import split_question_sources


class QuestionSegmentationTests(unittest.TestCase):
    def test_ignores_numbered_exam_instructions_before_question_section(self) -> None:
        source = """
        注意事项：
        1. 本试卷共 6 页，满分 120 分。
        2. 请认真核对姓名和准考证号。
        3. 答选择题必须用铅笔涂黑。
        一、选择题（本大题共 6 小题）
        1. 根号下 9/4 的值等于（ ）
        A. 3/2 B. -3/2 C. ±3/2 D. 81/16
        2. 计算 a^3·(a^3)^2 的结果是（ ）
        A. a^8 B. a^9 C. a^12 D. a^18
        """
        blocks = split_question_sources(source)
        self.assertEqual([number for number, _, _ in blocks], ["1", "2"])
        self.assertNotIn("注意事项", blocks[0][1])
        self.assertNotIn("准考证", blocks[0][1])

    def test_keeps_nested_questions_and_merges_repeated_cross_page_number(self) -> None:
        source = """
第 12 题 如图完成下列问题。
(1) 求 $x$ 的值。
![](images/p12-a.png)
<!-- page 2 -->
12．(2) 说明理由。
![](images/p12-b.png)
【13】计算 $3+4$。
# 参考答案与解析
12．(1) 略；(2) 略。
"""
        blocks = split_question_sources(source)
        self.assertEqual([number for number, _, _ in blocks], ["12", "13"])
        self.assertIn("(1) 求", blocks[0][1])
        self.assertIn("(2) 说明", blocks[0][1])
        self.assertEqual(blocks[0][2], ["images/p12-a.png", "images/p12-b.png"])
        self.assertNotIn("参考答案", "\n".join(block for _, block, _ in blocks))

    def test_stops_a_question_at_inline_answer_or_analysis_line(self) -> None:
        source = """7、解方程 $x+1=2$。
答案：$x=1$
8、下一题。"""
        blocks = split_question_sources(source)
        self.assertEqual([number for number, _, _ in blocks], ["7", "8"])
        self.assertNotIn("答案", blocks[0][1])

    def test_does_not_attach_all_page_images_to_an_ambiguous_visual_question(self) -> None:
        source = "<!-- page 1 -->\n![](images/figure-a.png)\n![](images/figure-b.png)\n3. 如图判断正误。\n4. 下一题。"
        blocks = split_question_sources(source)
        self.assertEqual(blocks[0][2], [])

    def test_normalizes_known_formula_damage_without_changing_numbers(self) -> None:
        normalized = normalize_model_math_text(
            r"$25℃＋3×4＝50％，\begin array {cc}a&b\end array$"
        )
        self.assertIn(r"25^{\circ}\mathrm{C}+3\times 4=50%", normalized)
        self.assertIn(r"\begin{array}{cc}a&b\end{array}", normalized)

    def test_quality_error_contains_formula_evidence(self) -> None:
        payload = {"question": {
            "questionNumber": "9",
            "prompt": r"计算 $\begin array x$。",
            "options": [],
            "imageUrls": [],
        }}
        quality = apply_question_quality_gate(payload, r"9、计算。", [])
        self.assertEqual(quality["status"], "needs_review")
        self.assertTrue(any("环境不完整" in error and "begin=" in error for error in quality["errors"]))


if __name__ == "__main__":
    unittest.main()
