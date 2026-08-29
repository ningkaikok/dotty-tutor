"""Regression tests for keeping stem images at their position in the question text.

真实坏样本：「初中数学湖北中考」第 7 题有主视图、俯视图两张图，OCR 原文里
`![](images/x.jpg)` 就在各自图注之前，位置本身是题意的一部分。此前清理题干时只删
引用、不记位置，前端只能把两张图整批贴在全部文字之后，页面上先出现两行图注、再出现
两张图，读者无法判断哪张是主视图。
"""

from __future__ import annotations

import unittest

from domain.questions.pipeline import (
    apply_question_quality_gate,
    audit_image_placeholders,
    build_lesson_prompt,
    extract_image_placements,
    protect_image_references,
    restore_image_placeholders,
)

STEM = (
    "7.（3分）一个几何体由若干个相同的正方体组成，其主视图和俯视图如图所示，"
    "则这个几何体中正方体的个数最多是（ ）\n\n"
    "![](images/206a.jpg)\n主视图\n\n"
    "![](images/e00fb.jpg)\n俯视图"
)
SOURCE = STEM + "\n\nA. 3 B. 4 C. 5 D. 6"
REFERENCES = ["images/206a.jpg", "images/e00fb.jpg"]
URLS = ["/api/uploads/u/assets/b/206a.jpg", "/api/uploads/u/assets/b/e00fb.jpg"]


def _payload(prompt: str, image_urls: list[str]) -> dict:
    return {"question": {
        "questionNumber": "7", "questionType": "choice", "prompt": prompt,
        "options": ["3", "4", "5", "6"], "imageUrls": list(image_urls),
    }}


class StemImagePlacementTests(unittest.TestCase):
    def test_lesson_prompt_never_exposes_markdown_image_paths(self) -> None:
        prompt = build_lesson_prompt("题干 ![](images/front.jpg)\n主视图")

        self.assertNotIn("images/front.jpg", prompt)
        self.assertIn("⟦IMG_1⟧", prompt)

    def test_image_references_round_trip_through_placeholders(self) -> None:
        source = " ".join(
            f"图{index} ![](images/image-{index}.jpg)"
            for index in range(1, 11)
        )
        protected, context = protect_image_references(source)

        self.assertNotIn("images/", protected)
        self.assertEqual(protected.count("⟦IMG_"), 10)
        self.assertEqual(restore_image_placeholders(protected, context), source)

        protected, context = protect_image_references(
            "主视图\n![](images/206a.jpg)\n俯视图\n![](images/e00fb.jpg)"
        )

        self.assertNotIn("images/", protected)
        self.assertEqual(protected.count("⟦IMG_"), 2)
        self.assertEqual(
            restore_image_placeholders(protected, context),
            "主视图\n![](images/206a.jpg)\n俯视图\n![](images/e00fb.jpg)",
        )
        self.assertEqual(
            audit_image_placeholders("主视图 ⟦IMG_1⟧ 俯视图 ⟦IMG_2⟧", context)["status"],
            "ready",
        )

    def test_malformed_image_markers_do_not_trigger_regex_backtracking(self) -> None:
        malformed = "![" * 2_000

        protected, context = protect_image_references(malformed)

        self.assertEqual(protected, malformed)
        self.assertEqual(context.originals, ())

    def test_placeholder_audit_flags_dropped_or_reordered_images(self) -> None:
        _protected, context = protect_image_references(
            "主视图 ![](images/206a.jpg) 俯视图 ![](images/e00fb.jpg)"
        )

        dropped = audit_image_placeholders("主视图 ⟦IMG_1⟧", context)
        reordered = audit_image_placeholders("俯视图 ⟦IMG_2⟧ 主视图 ⟦IMG_1⟧", context)

        self.assertEqual(dropped["status"], "needs_review")
        self.assertIn("数量不守恒", dropped["errors"][0])
        self.assertEqual(reordered["status"], "needs_review")
        self.assertIn("顺序不守恒", reordered["errors"][0])

    def test_quality_gate_rejects_placeholder_conservation_failure(self) -> None:
        payload = _payload("7. 题干", URLS)
        payload["_imagePlaceholderAudits"] = [{
            "status": "needs_review",
            "errors": ["图片占位符数量不守恒：期望 2 个，实际 1 个"],
        }]

        quality = apply_question_quality_gate(payload, SOURCE, REFERENCES)

        self.assertEqual(quality["status"], "needs_review")
        self.assertTrue(any("图片占位符校验失败" in error for error in quality["errors"]))
        self.assertNotIn("_imagePlaceholderAudits", payload)

    def test_records_placement_offsets_while_cleaning(self) -> None:
        """清理引用的同时必须记录位置；中英文方括号注释也要覆盖。"""
        cleaned, placements = extract_image_placements(
            "题干\n\n[主视图图片：images/a.jpg]\n主视图\n\n![](images/b.jpg)\n俯视图"
        )
        self.assertNotIn("images/", cleaned)
        self.assertEqual([reference for _offset, reference in placements], ["images/a.jpg", "images/b.jpg"])
        self.assertTrue(all(0 <= offset <= len(cleaned) for offset, _ in placements))

    def test_stem_images_render_at_their_position(self) -> None:
        """两张图必须各自落在对应图注旁，而不是整批贴在文字之后。"""
        payload = _payload(STEM, URLS)
        quality = apply_question_quality_gate(payload, SOURCE, REFERENCES)

        self.assertEqual(quality["status"], "ready", quality["errors"])
        kinds = [block["type"] for block in payload["question"]["contentBlocks"]]
        self.assertEqual(kinds, ["text", "image", "text", "image", "text", "options"])
        texts = [block for block in payload["question"]["contentBlocks"] if block["type"] == "text"]
        self.assertIn("主视图", texts[1]["text"])
        self.assertIn("俯视图", texts[2]["text"])
        # 临时的位置记录不能进入对外契约。
        self.assertNotIn("stemImagePlacements", payload["question"])

    def test_falls_back_to_appending_when_placements_are_incomplete(self) -> None:
        """题干只引用了部分图片时回退到既有行为，避免把图放到错误位置。"""
        prompt = "7. 题干（ ）\n\n![](images/206a.jpg)\n主视图"
        payload = _payload(prompt, URLS)
        quality = apply_question_quality_gate(payload, SOURCE, REFERENCES)

        kinds = [block["type"] for block in payload["question"]["contentBlocks"]]
        self.assertEqual(kinds.count("image"), 2, kinds)
        # 回退路径把图片整批放在文字之后。
        self.assertEqual(kinds[:2], ["text", "image"])
        self.assertEqual(quality["status"], "ready", quality["errors"])

    def test_prompt_without_references_keeps_previous_behaviour(self) -> None:
        """模型没有保留引用时不能崩，也不能丢图。"""
        prompt = "7. 题干（ ）\n\n主视图：\n\n俯视图："
        payload = _payload(prompt, URLS)
        quality = apply_question_quality_gate(payload, SOURCE, REFERENCES)

        kinds = [block["type"] for block in payload["question"]["contentBlocks"]]
        self.assertEqual(kinds.count("image"), 2, kinds)
        self.assertEqual(quality["status"], "ready", quality["errors"])


if __name__ == "__main__":
    unittest.main()
