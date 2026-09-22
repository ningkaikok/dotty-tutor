"""学生作答判题的符号等价兜底回归测试（roadmap T0"符号等价判题"）。

覆盖两类场景：
1. 结构化归一化判否、符号层能救回的假阴性——科学计数法、根式、代数式展开；
2. 符号层也判不了/判不一致时，原判否结果必须维持——证明这一层只救假阴性，
   绝不比现有规则更严格，也绝不会把本来判对的结果反过来推翻。
"""

from __future__ import annotations

import unittest

from answer_evaluator import EVALUATOR_VERSION, evaluate_structured_answer


def _numeric_question(expected: str, tolerance: float = 0) -> dict:
    return {
        "questionType": "numeric",
        "answerSpec": {"answerType": "numeric", "expected": expected, "tolerance": tolerance},
    }


def _fill_blank_question(answer_type: str, correct_answers: list[str]) -> dict:
    return {
        "questionType": "fill-blank",
        "blanks": [{"id": "b1", "answerType": answer_type, "correctAnswers": correct_answers}],
    }


class SymbolicFallbackRescuesFalseNegativesTests(unittest.TestCase):
    """归一化判否、符号层判 agree 才能救回——覆盖 roadmap 点名的三类形态。"""

    def test_scientific_notation_numeric_answer_is_rescued(self) -> None:
        question = _numeric_question("0.05")
        result = evaluate_structured_answer(question, "", {"numericAnswer": "5×10⁻²"})
        assert result is not None
        self.assertEqual(result["assessment"], "correct")

    def test_ocr_style_scientific_notation_with_x_is_rescued(self) -> None:
        # MinerU/pypdf 把 "×" OCR 成 "x" 是现实里的常见情形，answer_solver 已经处理过；
        # 学生作答路径复用同一层，OCR 出来的题目答案同样应该被救回。
        question = _numeric_question("1500")
        result = evaluate_structured_answer(question, "", {"numericAnswer": "1.5x10^3"})
        assert result is not None
        self.assertEqual(result["assessment"], "correct")

    def test_radical_matches_decimal_power_expression_blank(self) -> None:
        question = _fill_blank_question("expression", ["2**0.5"])
        result = evaluate_structured_answer(question, "", {"blankAnswers": {"b1": "√2"}})
        assert result is not None
        self.assertEqual(result["assessment"], "correct")

    def test_expanded_polynomial_matches_factored_form_blank(self) -> None:
        question = _fill_blank_question("expression", ["x^2+2x+1"])
        result = evaluate_structured_answer(question, "", {"blankAnswers": {"b1": "(x+1)^2"}})
        assert result is not None
        self.assertEqual(result["assessment"], "correct")
        # 版本号必须体现规则变化，供陪练计划/尝试记录解释历史证据的语义。
        self.assertEqual(result["evaluationEvidence"]["evaluatorVersion"], EVALUATOR_VERSION)


class SymbolicFallbackNeverOverridesExistingFailureTests(unittest.TestCase):
    """符号层确定冲突才判错，判不了必须回退而不是伪造 deterministic incorrect。"""

    def test_symbolic_disagreement_keeps_incorrect(self) -> None:
        # (x+1)^2 展开是 x^2+2x+1，不是 x^2+2x+2——符号层会明确判 disagree，
        # 结果必须仍然是 incorrect，不能被"升级判等"误救成 correct。
        question = _fill_blank_question("expression", ["x^2+2x+2"])
        result = evaluate_structured_answer(question, "", {"blankAnswers": {"b1": "(x+1)^2"}})
        assert result is not None
        self.assertEqual(result["assessment"], "incorrect")

    def test_unparseable_open_ended_text_abstains(self) -> None:
        # 两句不同的开放题措辞：符号层解析失败/判不了（undecidable），不能维持
        # 归一化给出的判否结果，否则开放文本会被伪装成 deterministic incorrect。
        question = _fill_blank_question("text", ["见解析"])
        result = evaluate_structured_answer(question, "", {"blankAnswers": {"b1": "证明过程如上"}})
        self.assertIsNone(result)

    def test_candidate_set_agrees_with_reordered_reference_set(self) -> None:
        question = _numeric_question("x=1 或 x=2")
        result = evaluate_structured_answer(question, "", {"numericAnswer": "x=2；x=1"})
        assert result is not None
        self.assertEqual(result["assessment"], "correct")

    def test_mixed_candidate_aggregation_prefers_agreement_over_undecidable(self) -> None:
        question = _fill_blank_question("expression", ["x=1 或 x=2", "x=3"])
        result = evaluate_structured_answer(
            question,
            "",
            {"blankAnswers": {"b1": "x=2,x=1"}},
        )
        assert result is not None
        self.assertEqual(result["assessment"], "correct")

    def test_plain_wrong_number_keeps_incorrect(self) -> None:
        question = _numeric_question("3")
        result = evaluate_structured_answer(question, "", {"numericAnswer": "4"})
        assert result is not None
        self.assertEqual(result["assessment"], "incorrect")

    def test_empty_submission_skips_fallback_and_stays_incorrect(self) -> None:
        # 空提交不应该触发符号层（也没有内容可判），仍然走原有的"回条件提取"路径。
        question = _numeric_question("3")
        result = evaluate_structured_answer(question, "", {"numericAnswer": ""})
        assert result is not None
        self.assertEqual(result["assessment"], "incorrect")
        self.assertEqual(result["evaluationEvidence"]["submittedRaw"], "")


if __name__ == "__main__":
    unittest.main()
