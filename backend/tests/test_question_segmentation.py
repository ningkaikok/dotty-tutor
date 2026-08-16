"""OCR 题块边界与公式保守修复的回归测试。"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from domain.questions.pipeline import (
    apply_question_quality_gate,
    normalize_model_math_text,
    write_model_prompt_artifact,
)
from domain.questions.source import (
    QUESTION_SEGMENTATION_VERSION,
    is_likely_exam_instruction,
    split_question_sources,
)


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

    def test_handles_ocr_whitespace_and_line_breaks_in_section_heading(self) -> None:
        source = """
        注 意 事 项：本试卷共 6 页，考试时间 120 分钟。
        一 、 选
        择 题（每题只有一个正确答案）
        1．下列各数中比 1 大的是（ ）
        A．2 B．0 C．1 D．3
        """
        blocks = split_question_sources(source)
        self.assertEqual([number for number, _, _ in blocks], ["1"])
        self.assertNotIn("考试时间", blocks[0][1])
        self.assertIn("下列各数", blocks[0][1])

    def test_accepts_markdown_heading_prefix_from_ocr_export(self) -> None:
        source = """
        # 一、选择题（每题 2 分）
        1. 下列各数中比 1 大的是（ ）
        A. 2 B. 0 C. 1 D. 3
        """
        blocks = split_question_sources(source)
        self.assertEqual([number for number, _, _ in blocks], ["1"])
        self.assertNotIn("选择题", blocks[0][1])

    def test_skips_numbered_exam_instructions_when_section_heading_is_missing(self) -> None:
        # 分页 OCR 可能把“一、选择题”留在上一页。题号会重复出现，不能按数字去重，
        # 必须先做说明块语义分类，再从第一个真实题块开始切分。
        source = """
        注意事项：
        1. 本试卷共 6 页，满分 120 分，考试时间 120 分钟。
        2. 请认真核对监考教师在答题卡上所粘贴条形码的姓名、考试证号。
        3. 答选择题必须用 2B 铅笔将答题卡上的答案标号涂黑。
        4. 作图必须用 2B 铅笔作答，并请加黑加粗，描写清楚。
        1. 根号下 9/4 的值等于（ ）
        A. 3/2 B. -3/2 C. ±3/2 D. 81/16
        2. 计算 a^3·(a^3)^2 的结果是（ ）
        A. a^8 B. a^9 C. a^12 D. a^18
        """
        blocks = split_question_sources(source)
        self.assertEqual([number for number, _, _ in blocks], ["1", "2"])
        self.assertNotIn("注意事项", blocks[0][1])
        self.assertNotIn("条形码", blocks[0][1])

    def test_classifies_instruction_without_question_evidence(self) -> None:
        self.assertTrue(is_likely_exam_instruction("注意事项：请核对准考证号，答题卡涂黑后答案方可有效。"))
        self.assertFalse(is_likely_exam_instruction("1. 计算 3+4 的结果是（ ）。"))

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

    def test_quality_gate_rejects_exam_instruction_source(self) -> None:
        payload = {"question": {
            "questionNumber": "1",
            "prompt": "请核对准考证号。",
            "options": [],
            "imageUrls": [],
            "contentBlocks": [{"type": "text", "text": "请核对准考证号。"}],
        }}
        quality = apply_question_quality_gate(
            payload,
            "1. 本试卷共 6 页，考试时间 120 分钟，请核对准考证号。",
            [],
        )
        self.assertEqual(quality["status"], "needs_review")
        self.assertTrue(any("考试说明" in error for error in quality["errors"]))
        self.assertEqual(quality["validatorVersion"], "p0-v4")

    def test_real_exam_notice_never_enters_prompt_artifact(self) -> None:
        """回放真实 OCR 形态，防止修复只停留在切分函数而再次污染模型提示词。"""
        source = """
# 南京市2018年初中毕业生学业考试
## 注意事项：
1. 本试卷共6页.全卷满分 120分.考试时间为120分钟.考生答题全部答在答题卡上，答在本试卷上无效.
2. 请认真核对监考教师在答题卡上所粘贴条形码的姓名、考试证号是否与本人相符合，再将自己的姓名、考试证号用0.5毫米黑色墨水签字笔填写在答题卡及本试卷上.
3. 答选择题必须用2B铅笔将答题卡上对应的答案标号涂黑.
## 一、选择题（本大题共6小题）
1. $\\sqrt{9/4}$ 的值等于 A. $3/2$ B. $-3/2$ C. $\\pm3/2$ D. $81/16$
"""
        blocks = split_question_sources(source)
        self.assertEqual([number for number, _, _ in blocks], ["1"])
        self.assertNotIn("监考教师", blocks[0][1])
        with TemporaryDirectory() as directory:
            artifact = write_model_prompt_artifact(Path(directory), blocks)
            prompt = artifact.read_text(encoding="utf-8")
            self.assertIn(QUESTION_SEGMENTATION_VERSION, prompt)
            self.assertNotIn("请认真核对监考教师", prompt)


if __name__ == "__main__":
    unittest.main()
