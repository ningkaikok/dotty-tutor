"""Regression tests for unstructured content residue in question stems.

This bug has recurred multiple times (see commits 74118ed, c363620, 5f52ed8,
947ed01): a cleanup rule gets written inside one special-case branch, a
different question shape slips past it, and the raw markdown/image markup
ends up rendered as literal text to the student. These tests pin down the
two structural fixes: the cleanup now runs on every question, and a
deterministic validator check catches any future regression instead of
relying on someone spotting it in the UI.
"""

from __future__ import annotations

import unittest

from domain.questions.pipeline import apply_question_quality_gate, validate_question_payload


class QuestionContentResidueTests(unittest.TestCase):
    def test_strips_image_reference_from_ordinary_question_prompt(self) -> None:
        """普通题（非 A-D 图片选择题）模型误把图片路径写回 prompt 时，仍需被清理。

        清理逻辑此前只写在 A-D 图片选择题分支里，其它题型（例如这里的统计表
        + 单张图题）会原样保留 `![](images/xxx.jpg)`，一路流进 contentBlocks
        并展示给学生。
        """
        leaked_reference = "![](images/49ded691c9294df2b581302eca963010653822afc68bf9d444aaba5999b3cbb1.jpg)"
        payload = {
            "question": {
                "questionNumber": "5",
                "questionType": "short-answer",
                "prompt": f"某班统计阅读量如下表所示，求平均数。{leaked_reference}",
                "options": [],
                "givens": [],
                "imageUrls": [],
            },
        }
        quality = apply_question_quality_gate(payload, "5. 某班统计阅读量如下表所示，求平均数。", [])

        prompt = payload["question"]["prompt"]
        self.assertNotIn("![", prompt)
        self.assertNotIn("images/", prompt)

        text_blocks = [block for block in payload["question"]["contentBlocks"] if block.get("type") == "text"]
        self.assertTrue(text_blocks, "cleaned prompt should still produce a text content block")
        for block in text_blocks:
            self.assertNotIn("images/", block["text"])

        self.assertFalse(
            any("残留未结构化的图片引用" in error for error in quality["errors"]),
            quality["errors"],
        )

    def test_residue_check_flags_leftover_image_reference_in_text_block(self) -> None:
        """安全网本身要能生效：即便清理逻辑被绕过，残留也必须让题目进入 needs_review。"""
        payload = {
            "question": {
                "questionNumber": "6",
                "prompt": "某班统计如下表，求平均数。",
                "options": [],
                "imageUrls": [],
                "contentBlocks": [
                    {"id": "stem-1", "type": "text", "text": "某班统计如下表，求平均数。![](images/leaked.jpg)"},
                ],
            },
        }
        quality = validate_question_payload(payload, "6. 某班统计如下表，求平均数。", [])

        self.assertEqual(quality["status"], "needs_review")
        self.assertTrue(
            any("题干残留未结构化的图片引用" in error for error in quality["errors"]),
            quality["errors"],
        )

    def test_strips_bracketed_image_annotation_as_a_whole(self) -> None:
        """真实坏样本：模型把图片写成带说明的方括号注释，必须整段删除。

        取自「初中数学湖北中考」第 7 题。只删路径会留下 `[主视图图片：` 这样的
        残缺前缀——右方括号会被裸路径规则一起吃掉，读起来比原文更糟。
        """
        prompt = (
            "7.（3分）一个几何体由若干个相同的正方体组成，其主视图和俯视图如图所示，"
            "则这个几何体中正方体的个数最多是（ ）\n\n"
            "[主视图图片：images/206a06049decfb0cc63fdedbae40b8122ee534991823b76ce915ef1f36d3778a.jpg]\n"
            "主视图\n\n"
            "[俯视图图片：images/e00fb9b69c89d76e11eb9b4c3be33c0ee5f33785a5bc134d41c454fc4dbccf7c.jpg]\n"
            "俯视图"
        )
        payload = {
            "question": {
                "questionNumber": "7",
                "prompt": prompt,
                "options": [],
                "givens": [],
                "imageUrls": [],
            },
        }
        quality = apply_question_quality_gate(payload, prompt, [])

        cleaned = payload["question"]["prompt"]
        self.assertNotIn("images/", cleaned)
        # 关键：不能留下无法配对的残缺方括号前缀。
        self.assertNotIn("[主视图图片", cleaned)
        self.assertNotIn("[俯视图图片", cleaned)
        # 图注文字属于题意，必须保留。
        self.assertIn("主视图", cleaned)
        self.assertIn("俯视图", cleaned)
        self.assertEqual(quality["status"], "ready", quality["errors"])

    def test_duplicate_options_are_flagged_for_three_choice_questions(self) -> None:
        """真实坏样本：三选项题的题干与结构化选项重复，门禁必须拦下。

        取自「2018南京中考数学试卷」第 18 题。原检查写成 `prompt_choice_labels[:4]
        == ["A","B","C","D"]`，只认恰好四项，三选项题永远比不中，题干和选项按钮
        会把同样的内容显示两遍。
        """
        prompt = (
            "18.（7分）如图，在数轴上，点A、B分别表示数1、−2x+3。\n\n"
            "（1）求x的取值范围。\n\n"
            "（2）数轴上表示数−x+2的点应落在（▲）。\n"
            "A. 点A的左边\nB. 线段AB上\nC. 点B的右边"
        )
        payload = {
            "question": {
                "questionNumber": "18",
                "prompt": prompt,
                "options": ["点A的左边", "线段AB上", "点B的右边"],
                "imageUrls": [],
                "contentBlocks": [{"id": "stem-1", "type": "text", "text": prompt}],
            },
        }
        quality = validate_question_payload(payload, prompt, [])

        self.assertEqual(quality["status"], "needs_review")
        self.assertTrue(
            any("题干中重复包含结构化选项" in error for error in quality["errors"]),
            quality["errors"],
        )

    def test_four_option_duplicate_check_still_applies(self) -> None:
        """放宽到按实际选项数比对后，原有的四选项场景不能失效。"""
        prompt = "1. 选出正确的一项（ ）\nA. 甲\nB. 乙\nC. 丙\nD. 丁"
        payload = {
            "question": {
                "questionNumber": "1",
                "prompt": prompt,
                "options": ["甲", "乙", "丙", "丁"],
                "imageUrls": [],
                "contentBlocks": [{"id": "stem-1", "type": "text", "text": prompt}],
            },
        }
        quality = validate_question_payload(payload, prompt, [])
        self.assertTrue(
            any("题干中重复包含结构化选项" in error for error in quality["errors"]),
            quality["errors"],
        )

    def test_prompt_without_duplicated_options_stays_ready(self) -> None:
        """题干没有重复列选项时不能误报。"""
        prompt = "1. 选出正确的一项（ ）"
        payload = {
            "question": {
                "questionNumber": "1",
                "prompt": prompt,
                "options": ["甲", "乙", "丙"],
                "imageUrls": [],
                "contentBlocks": [{"id": "stem-1", "type": "text", "text": prompt}],
            },
        }
        quality = validate_question_payload(payload, prompt, [])
        self.assertFalse(
            any("题干中重复包含结构化选项" in error for error in quality["errors"]),
            quality["errors"],
        )

    def test_image_choice_questions_are_not_flagged_as_duplicate(self) -> None:
        """真实坏样本：图片选择题的裸标签选项不能被误判成"题干重复包含结构化选项"。

        图片选择题清理干净后，题干里一行选项文字都没有（`prompt_option_lines` 是空
        字典），而结构化选项 `"(A)"` 剥掉标记后正文也是空字符串。两边都拿到默认值 ""
        时会凑巧相等，导致这条检查对**所有**图片选择题恒为真——这个 bug 在选项文字还
        残留在题干里时（修复 #128 之前）被意外掩盖了，残留文字一清理干净就暴露出来。
        """
        prompt = "不等式 4-2x>0 的解集在数轴上表示为（ ）"
        payload = {
            "question": {
                "questionNumber": "2",
                "prompt": prompt,
                "options": ["(A)", "(B)", "(C)", "(D)"],
                "optionImageUrls": ["/a.jpg", "/b.jpg", "/c.jpg", "/d.jpg"],
                "imageUrls": ["/a.jpg", "/b.jpg", "/c.jpg", "/d.jpg"],
                "contentBlocks": [
                    {"id": "stem-1", "type": "text", "text": prompt},
                    {
                        "id": "options",
                        "type": "options",
                        "items": [
                            {"label": "(A)", "imageUrl": "/a.jpg"},
                            {"label": "(B)", "imageUrl": "/b.jpg"},
                            {"label": "(C)", "imageUrl": "/c.jpg"},
                            {"label": "(D)", "imageUrl": "/d.jpg"},
                        ],
                    },
                ],
            },
        }
        quality = validate_question_payload(payload, "2. " + prompt, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"])
        self.assertFalse(
            any("题干中重复包含结构化选项" in error for error in quality["errors"]),
            quality["errors"],
        )


if __name__ == "__main__":
    unittest.main()
