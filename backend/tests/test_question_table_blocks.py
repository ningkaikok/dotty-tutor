"""Regression tests for HTML statistics tables as structured content blocks.

MinerU emits statistics tables (bar charts with a frequency table, etc.) as raw
``<table>`` HTML inside the OCR text. Before this fix that markup fell through
the catch-all ``rich_text_blocks()`` branch and was rendered to the student as
literal ``<table><tr>...`` source text. These tests pin the real fixture shape
(unquoted ``rowspan``/``colspan`` attributes, as MinerU produces them) to a
structured ``table`` content block, and pin the safety-net check that flags any
leftover raw tag.
"""

from __future__ import annotations

import unittest

from domain.questions.pipeline import (
    apply_question_quality_gate,
    replace_question_prompt,
    validate_question_payload,
)

# Real MinerU shape: unquoted attributes, no whitespace between rows/cells.
STATS_TABLE_HTML = (
    "<table><tr><td rowspan=1 colspan=1>阅读量/本</td><td rowspan=1 colspan=1>0</td>"
    "<td rowspan=1 colspan=1>1</td><td rowspan=1 colspan=1>2</td></tr>"
    "<tr><td rowspan=1 colspan=1>人数</td><td rowspan=1 colspan=1>2</td>"
    "<td rowspan=1 colspan=1>5</td><td rowspan=1 colspan=1>3</td></tr></table>"
)


class QuestionTableBlockTests(unittest.TestCase):
    def test_html_table_becomes_structured_table_block(self) -> None:
        """真实统计表 fixture：表格必须变成结构化 table 块，不能原样留在文字里。"""
        payload = {
            "question": {
                "questionNumber": "7",
                "questionType": "short-answer",
                "prompt": f"某班学生一周阅读量统计如下表，求平均数。{STATS_TABLE_HTML}",
                "options": [],
                "givens": [],
                "imageUrls": [],
            },
        }
        quality = apply_question_quality_gate(payload, "7. 某班学生一周阅读量统计如下表，求平均数。", [])

        content_blocks = payload["question"]["contentBlocks"]
        table_blocks = [block for block in content_blocks if block.get("type") == "table"]
        self.assertEqual(len(table_blocks), 1, content_blocks)
        rows = table_blocks[0]["rows"]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(len(row["cells"]), 4)
        first_cell_text = "".join(
            block["text"] for block in rows[0]["cells"][0]["contentBlocks"] if block["type"] == "text"
        )
        self.assertIn("阅读量", first_cell_text)

        text_blocks = [block for block in content_blocks if block.get("type") == "text"]
        for block in text_blocks:
            self.assertNotIn("<table", block["text"])

        self.assertFalse(
            any("表格标记" in error for error in quality["errors"]),
            quality["errors"],
        )

    def test_residue_check_flags_leftover_table_tag_in_text_block(self) -> None:
        """安全网本身要能生效：残留的原始表格标签必须让题目进入 needs_review。"""
        payload = {
            "question": {
                "questionNumber": "8",
                "prompt": "某班统计如下表，求平均数。",
                "options": [],
                "imageUrls": [],
                "contentBlocks": [
                    {"id": "stem-1", "type": "text", "text": f"某班统计如下表，求平均数。{STATS_TABLE_HTML}"},
                ],
            },
        }
        quality = validate_question_payload(payload, "8. 某班统计如下表，求平均数。", [])

        self.assertEqual(quality["status"], "needs_review")
        self.assertTrue(any("表格标记" in error for error in quality["errors"]), quality["errors"])

    def test_prompt_replacement_uses_the_same_table_parsing(self) -> None:
        """错题确认改写题干时必须走同一个解析入口。

        这条路径（mistake_store.confirm）此前仍用 rich_text_blocks，表格会退化成
        文字块；同时旧表格块被当作 trailing 块保留下来，导致同一张表重复出现。
        两条路径各用一套解析规则，正是这类缺陷反复复发的原因。
        """
        question = {
            "prompt": f"旧题干。{STATS_TABLE_HTML}",
            "contentBlocks": [
                {"id": "stem-1", "type": "text", "text": "旧题干。", "sourceOrder": 0},
                {"id": "stem-table-1", "type": "table", "rows": [], "sourceOrder": 1},
                {"id": "stem-image-1", "type": "image", "url": "/api/uploads/a.png", "sourceOrder": 2},
            ],
        }
        replace_question_prompt(question, f"确认后的题干。{STATS_TABLE_HTML}")

        blocks = question["contentBlocks"]
        table_blocks = [block for block in blocks if block.get("type") == "table"]
        self.assertEqual(len(table_blocks), 1, f"表格既不能丢失也不能重复：{blocks}")
        self.assertEqual(len(table_blocks[0]["rows"]), 2, "表格必须由新题干重新解析，而不是沿用旧块")
        for block in blocks:
            if block.get("type") == "text":
                self.assertNotIn("<table", block["text"])
        # 图片和选项来自独立字段，与 prompt 无关，必须保留。
        self.assertEqual(len([block for block in blocks if block.get("type") == "image"]), 1)
        self.assertEqual([block["sourceOrder"] for block in blocks], list(range(len(blocks))))


if __name__ == "__main__":
    unittest.main()
