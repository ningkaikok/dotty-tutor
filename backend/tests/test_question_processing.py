"""Tests for bounded automatic recovery in the question pipeline."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from application.services.question_processing import _generate_validated_question, process_question_sources
from domain.questions.source import split_question_sources


def _candidate() -> tuple[dict, list[dict], dict]:
    payload = {
        "question": {
            "id": "question-1",
            "questionType": "short-answer",
            "prompt": "计算 1+1。",
            "options": [],
            "givens": [],
        },
        "lessonSteps": [],
        "architecture": {},
        "modelRun": {"provider": "test", "model": "test", "fallback": False},
    }
    return payload, [], payload["modelRun"]


class QuestionQualityRecoveryTests(unittest.TestCase):
    def test_retries_only_the_failed_question_until_quality_is_ready(self) -> None:
        quality_attempts = 0

        def apply_gate(payload: dict, _source: str, _images: list[str]) -> dict:
            nonlocal quality_attempts
            quality_attempts += 1
            status = "ready" if quality_attempts == 2 else "needs_review"
            quality = {
                "status": status,
                "errors": [] if status == "ready" else ["题型结构不完整"],
                "warnings": [],
                "validatorVersion": "test-v1",
            }
            payload["quality"] = quality
            payload["question"]["publicationStatus"] = status
            return quality

        with TemporaryDirectory() as directory:
            with (
                patch(
                    "application.services.question_processing.generate_lesson",
                    side_effect=lambda _source, **_kwargs: _candidate(),
                ) as generate,
                patch(
                    "application.services.question_processing.review_lesson_payload",
                    side_effect=lambda payload, _source, _images, _cards: (
                        payload,
                        {"provider": "test"},
                    ),
                ),
                patch("application.services.question_processing.apply_question_quality_gate", side_effect=apply_gate),
            ):
                payload, _cards, _model_run, _review_run = _generate_validated_question(
                    number="2",
                    block="2. 计算 1+1。",
                    images=[],
                    index=0,
                    batch={"id": "batch-1", "startPage": 1, "endPage": 1},
                    ocr_run={},
                    asset_dir=Path(directory),
                )

        self.assertEqual(generate.call_count, 2)
        self.assertIsNone(generate.call_args_list[0].kwargs["repair_errors"])
        self.assertEqual(generate.call_args_list[1].kwargs["repair_errors"], ["题型结构不完整"])
        self.assertEqual(payload["quality"]["status"], "ready")
        self.assertEqual(payload["qualityRecovery"], {
            "attempts": 2,
            "recovered": True,
            "quarantined": False,
        })

    def test_runtime_fallback_is_quarantined_without_repeating_the_outage(self) -> None:
        fallback = _candidate()
        fallback[0]["modelRun"] = {
            "provider": "mock",
            "model": "fallback",
            "fallback": True,
        }
        fallback = fallback[0], fallback[1], fallback[0]["modelRun"]

        def failed_gate(payload: dict, _source: str, _images: list[str]) -> dict:
            quality = {
                "status": "needs_review",
                "errors": ["题型结构不完整"],
                "warnings": [],
                "validatorVersion": "test-v1",
            }
            payload["quality"] = quality
            return quality

        with TemporaryDirectory() as directory:
            with (
                patch("application.services.question_processing.generate_lesson", return_value=fallback) as generate,
                patch(
                    "application.services.question_processing.review_lesson_payload",
                    side_effect=lambda payload, _source, _images, _cards: (
                        payload,
                        {"provider": "test"},
                    ),
                ),
                patch("application.services.question_processing.apply_question_quality_gate", side_effect=failed_gate),
            ):
                payload, _cards, _model_run, _review_run = _generate_validated_question(
                    number="3",
                    block="3. 计算 2+2。",
                    images=[],
                    index=0,
                    batch={"id": "batch-1", "startPage": 1, "endPage": 1},
                    ocr_run={},
                    asset_dir=Path(directory),
                )

        self.assertEqual(generate.call_count, 1)
        self.assertEqual(payload["qualityRecovery"], {
            "attempts": 1,
            "recovered": False,
            "quarantined": True,
        })


# 真实 OCR 原文摘录（教材 56503d0642c54d728a7672e9cb77dd57，batch-001，第 3-9 题）。
# "数字1、2、" 和 "3、4. 随机抽取" 之间的空行是 MinerU 在句子中间插入的段落断点，不是
# 真正的题目边界，但 QUESTION_START_PATTERN 只看行首，会把 "3、" 误判为新题号。
_REAL_DUPLICATE_NUMBER_OCR_EXCERPT = """3. （3分）计算 $3 x ^ { 2 } - x ^ { 2 }$ 的结果是（ )A. 2 B. $2 { \\tt x } ^ { 2 }$ C. 2x D. $4 \\mathsf { x } ^ { 2 }$

4.（3分）五名女生的体重（单位：kg）分别为：37、40、38、42、42，这组数
据的众数和中位数分别是（）
A. 2、40 B. 42、38 C. 40、42 D. 42、40

5. （3分）计算（a-2）（a+3）的结果是（）A. ${ \\mathsf { a } } ^ { 2 } - 6$ B. $\\mathsf { a } ^ { 2 } { + } \\mathsf { a } - 6$

6. （3分）点 A（2，-5）关于x轴对称的点的坐标是（）A. (2, 5) B. (- 2, 5) C. ( - 2, - 5) D. ( - 5, 2)

7.（3分）一个几何体由若干个相同的正方体组成，其主视图和俯视图如图所示，则这个几何体中正方体的个数最多是（）

A. 3 B. 4 C. 5 D. 6

8.（3分）一个不透明的袋中有四张完全相同的卡片，把它们分别标上数字1、2、

3、4. 随机抽取一张卡片，然后放回，再随机抽取一张卡片，则两次抽取的卡片上数字之积为偶数的概率是（）
A. $\\textstyle { \\frac { 1 } { 4 } }$ B. $\\textstyle { \\frac { 1 } { 2 } }$ C. $\\frac { 3 } { 4 }$ D. $\\frac { 5 } { 6 }$

9. （3分）将正整数1至2018按一定规律排列如下表：
"""


class DuplicateQuestionNumberSafetyNetTests(unittest.TestCase):
    """回归证据 A：一道题曾经被同批次内另一道题静默覆盖。"""

    def test_process_question_sources_isolates_duplicate_number_within_one_batch(self) -> None:
        blocks = split_question_sources(_REAL_DUPLICATE_NUMBER_OCR_EXCERPT)
        question_sources = [block for block in blocks if block[0] in {"3", "8"}]
        # 先确认真实文本确实重现了坏样本：真正的第 3 题、第 8 题，以及被误判成
        # "新的第 3 题" 的第 8 题尾巴，三者的题号序列是 3, 8, 3。
        self.assertEqual([number for number, _, _ in question_sources], ["3", "8", "3"])

        def fake_generate_lesson(source_text: str, *, repair_errors=None):
            payload = {
                "question": {
                    "id": f"question-{abs(hash(source_text))}",
                    "questionType": "choice",
                    "prompt": source_text,
                    "options": [],
                    "givens": [],
                },
                "lessonSteps": [],
                "architecture": {},
                "modelRun": {"provider": "test", "model": "test", "fallback": False},
            }
            return payload, [], payload["modelRun"]

        def fake_quality_gate(payload: dict, _source: str, _images: list[str]) -> dict:
            quality = {"status": "ready", "errors": [], "warnings": [], "validatorVersion": "test-v1"}
            payload["quality"] = quality
            payload["question"]["publicationStatus"] = "ready"
            return quality

        with TemporaryDirectory() as directory:
            with (
                patch(
                    "application.services.question_processing.generate_lesson",
                    side_effect=fake_generate_lesson,
                ),
                patch(
                    "application.services.question_processing.review_lesson_payload",
                    side_effect=lambda payload, _source, _images, _cards: (payload, {"provider": "test"}),
                ),
                patch(
                    "application.services.question_processing.apply_question_quality_gate",
                    side_effect=fake_quality_gate,
                ),
            ):
                payloads, _guide_cards, _model_runs, _review_runs = process_question_sources(
                    question_sources,
                    batch={"id": "batch-dup", "startPage": 1, "endPage": 1},
                    ocr_run={},
                    asset_dir=Path(directory),
                )

        keys = [payload["question"]["sourceQuestionKey"] for payload in payloads]
        self.assertEqual(keys[0], "batch-dup-q-3")
        self.assertEqual(keys[1], "batch-dup-q-8")
        # 被误判的重复 "3" 必须拿到一个和批次内任何其他题目都不同的 key；否则同批次
        # upsert 持久化时它会直接覆盖真正的第 3 题，且没有任何报错。
        self.assertNotIn(keys[2], {keys[0], keys[1]})

        # 用一个内存字典模拟同批次内按 sourceQuestionKey upsert 持久化：如果两条记录
        # 共享同一个 key，这里就只会剩两条而不是三条，从而暴露"静默覆盖"问题。
        store: dict[str, dict] = {}
        for payload in payloads:
            store[payload["question"]["sourceQuestionKey"]] = payload
        self.assertEqual(len(store), 3)
        self.assertIn("计算 $3 x", store[keys[0]]["question"]["prompt"])
        self.assertIn("随机抽取", store[keys[2]]["question"]["prompt"])

        # 真正的第 3 题、第 8 题不受影响，质量保持 ready。
        self.assertEqual(payloads[0]["quality"]["status"], "ready")
        self.assertEqual(payloads[1]["quality"]["status"], "ready")
        # 重复候选必须被拦截为 needs_review，并在 errors 里留下可追溯的证据，
        # 而不是走 warnings 静默通过。
        self.assertEqual(payloads[2]["quality"]["status"], "needs_review")
        self.assertTrue(any("重复" in error for error in payloads[2]["quality"]["errors"]))
        self.assertEqual(payloads[2]["question"]["publicationStatus"], "needs_review")
        # 原始题号仍然保留在 questionNumber 上，供人工核对，不隐藏它。
        self.assertEqual(payloads[2]["question"]["questionNumber"], "3")


if __name__ == "__main__":
    unittest.main()
