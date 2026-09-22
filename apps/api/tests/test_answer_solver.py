"""``domain/questions/answer_solver.py`` 的确定性判等回归测试。

覆盖三条既定分层（文本归一化 → 数值容差 → 符号等价）以及最重要的 undecidable 路径：
CAS 判不了的答案（无法解析、开放题/证明题的文字表述）绝不能被误报成 disagree。
"""

from __future__ import annotations

import unittest

from domain.questions.answer_solver import (
    SOLVER_VERSION,
    check_answer_agreement,
    extract_solution_answer_text,
)


class NumericAgreementTests(unittest.TestCase):
    def test_equivalent_fraction_and_decimal_agree(self) -> None:
        result = check_answer_agreement("0.5", "1/2")
        self.assertEqual(result["status"], "agree")
        self.assertEqual(result["method"], "numeric-tolerance")
        self.assertEqual(result["solverVersion"], SOLVER_VERSION)

    def test_different_numbers_disagree(self) -> None:
        result = check_answer_agreement("3", "4")
        self.assertEqual(result["status"], "disagree")
        self.assertEqual(result["method"], "numeric-tolerance")

    def test_percent_and_decimal_are_the_same_value(self) -> None:
        # 与 answer_evaluator.parse_number 不同：answer_solver 明确知道两边可能来自
        # 不同书写习惯，因此会把 "90%" 换算成 0.9 再比较，而不是直接剥掉百分号比大小。
        result = check_answer_agreement("90%", "0.9")
        self.assertEqual(result["status"], "agree")

    def test_thousands_separator_and_latex_fraction_still_agree(self) -> None:
        result = check_answer_agreement("\\frac{1}{4}", "0.25")
        self.assertEqual(result["status"], "agree")
        self.assertEqual(result["method"], "numeric-tolerance")


class SymbolicAgreementTests(unittest.TestCase):
    def test_expanded_polynomial_matches_factored_form(self) -> None:
        result = check_answer_agreement("(x+1)^2", "x^2+2x+1")
        self.assertEqual(result["status"], "agree")
        self.assertEqual(result["method"], "symbolic-equivalence")

    def test_polynomial_with_wrong_constant_disagrees(self) -> None:
        result = check_answer_agreement("(x+1)^2", "x^2+2x+2")
        self.assertEqual(result["status"], "disagree")
        self.assertEqual(result["method"], "symbolic-equivalence")

    def test_scientific_notation_matches_decimal(self) -> None:
        result = check_answer_agreement("5×10⁻²", "0.05")
        self.assertEqual(result["status"], "agree")
        self.assertEqual(result["method"], "symbolic-equivalence")

    def test_radical_matches_fractional_power(self) -> None:
        result = check_answer_agreement("√2", "2**0.5")
        self.assertEqual(result["status"], "agree")
        self.assertEqual(result["method"], "symbolic-equivalence")

    def test_different_variables_that_cannot_cancel_disagree(self) -> None:
        result = check_answer_agreement("x+y", "y+z")
        self.assertEqual(result["status"], "disagree")

    def test_ocr_style_scientific_notation_with_lowercase_x_agrees(self) -> None:
        # MinerU/pypdf 经常把科学计数法里的 "×" OCR 成拉丁字母 "x"；这是
        # engineering-roadmap.md 点名的第一个科学计数法例子（5×10⁻² vs 0.05），
        # 误判成 disagree 会把答对的题挡成 conflict，比 undecidable 更伤信任。
        result = check_answer_agreement("5x10^-2", "0.05")
        self.assertEqual(result["status"], "agree")
        self.assertEqual(result["method"], "symbolic-equivalence")

    def test_ocr_style_scientific_notation_with_uppercase_x_agrees(self) -> None:
        result = check_answer_agreement("5X10^-2", "0.05")
        self.assertEqual(result["status"], "agree")

    def test_decimal_scientific_notation_with_x_agrees(self) -> None:
        result = check_answer_agreement("1.5x10^3", "1500")
        self.assertEqual(result["status"], "agree")

    def test_bare_variable_x_is_not_treated_as_multiplication(self) -> None:
        # 边界回归：只有两侧都紧邻数字的 x/X 才当乘号，普通代数变量 x 不能被吃掉，
        # 否则 "x+1" 和 "x+2" 会被错误地判成一致。
        result = check_answer_agreement("x+1", "x+2")
        self.assertEqual(result["status"], "disagree")


class SolutionSetAgreementTests(unittest.TestCase):
    """解集只接受明确分隔符，并按集合语义做保守一一匹配。"""

    def test_reordered_or_solution_set_agrees(self) -> None:
        result = check_answer_agreement("x=1 或 x=2", "x=2 or x=1")
        self.assertEqual(result["status"], "agree")
        self.assertEqual(result["method"], "solution-set")

    def test_braces_and_semicolon_solution_set_agree(self) -> None:
        result = check_answer_agreement("{x=1；x=2}", r"\{x=2;x=1\}")
        self.assertEqual(result["status"], "agree")

    def test_radical_and_fraction_elements_agree(self) -> None:
        result = check_answer_agreement("{√2, 1/2}", "{2**0.5, 0.5}")
        self.assertEqual(result["status"], "agree")

    def test_duplicate_elements_have_set_semantics(self) -> None:
        result = check_answer_agreement("{x=1, x=1, x=2}", "{x=2,x=1}")
        self.assertEqual(result["status"], "agree")

    def test_missing_or_extra_solution_disagrees_when_all_pairs_are_known(self) -> None:
        fewer = check_answer_agreement("{x=1}", "{x=1,x=2}")
        more = check_answer_agreement("{x=1,x=2,x=3}", "{x=1,x=2}")
        self.assertEqual(fewer["status"], "disagree")
        self.assertEqual(more["status"], "disagree")

    def test_different_variables_are_not_silently_matched(self) -> None:
        result = check_answer_agreement("{x=1,x=2}", "{y=1,y=2}")
        self.assertEqual(result["status"], "disagree")

    def test_nested_coordinate_and_function_commas_are_not_solution_separators(self) -> None:
        coordinate = check_answer_agreement("(1,2)", "(1,3)")
        function = check_answer_agreement("f(1,2)", "f(1,3)")
        self.assertNotEqual(coordinate["method"], "solution-set")
        self.assertNotEqual(function["method"], "solution-set")
        self.assertEqual(coordinate["status"], "undecidable")
        self.assertEqual(function["status"], "undecidable")


class TextFallbackAgreementTests(unittest.TestCase):
    def test_identical_prose_answers_agree_without_math_parsing(self) -> None:
        result = check_answer_agreement("见解析", "见解析")
        self.assertEqual(result["status"], "agree")
        self.assertEqual(result["method"], "text-normalized-match")

    def test_whitespace_and_case_insensitive_text_match(self) -> None:
        result = check_answer_agreement("AB = 3cm", "ab=3cm")
        self.assertEqual(result["status"], "agree")
        self.assertEqual(result["method"], "text-normalized-match")


class UndecidableAgreementTests(unittest.TestCase):
    """最重要的一组用例：CAS 判不了时必须落在 undecidable，绝不能猜成 disagree。"""

    def test_missing_reference_is_undecidable(self) -> None:
        result = check_answer_agreement("3", "")
        self.assertEqual(result["status"], "undecidable")
        self.assertEqual(result["method"], "missing-input")

    def test_both_inputs_missing_is_undecidable(self) -> None:
        result = check_answer_agreement("", "")
        self.assertEqual(result["status"], "undecidable")

    def test_different_prose_open_answers_are_undecidable_not_disagree(self) -> None:
        # 两句不同的证明题/开放题措辞：sympy 会把中文字符当成合法 Symbol 名，
        # 如果不做白名单拦截，相减必然非零，会被误判成 disagree——这是本模块
        # 最容易踩的坑，必须回归覆盖。
        result = check_answer_agreement("见解析", "略")
        self.assertEqual(result["status"], "undecidable")
        self.assertEqual(result["method"], "unparseable")

    def test_geometry_proof_style_answer_is_undecidable(self) -> None:
        result = check_answer_agreement("略（证明过程见解析）", "详见解析过程")
        self.assertEqual(result["status"], "undecidable")

    def test_unparseable_mixed_text_is_undecidable(self) -> None:
        result = check_answer_agreement("因为对顶角相等，所以∠A=∠B", "0.05")
        self.assertEqual(result["status"], "undecidable")


class ExtractSolutionAnswerTextTests(unittest.TestCase):
    def test_prefers_answer_spec_expected(self) -> None:
        solution = {"answerSpec": {"expected": "3"}, "correctAnswer": "4"}
        self.assertEqual(extract_solution_answer_text(solution), "3")

    def test_falls_back_to_single_correct_answer(self) -> None:
        solution = {"correctAnswer": "正确"}
        self.assertEqual(extract_solution_answer_text(solution), "正确")

    def test_single_correct_answers_entry_is_usable(self) -> None:
        solution = {"correctAnswers": ["A"]}
        self.assertEqual(extract_solution_answer_text(solution), "A")

    def test_multiple_correct_answers_have_no_single_comparable_text(self) -> None:
        # 多选/多空没有单一"标准答案"可比较，交回空字符串让调用方走 undecidable，
        # 而不是拼接猜一个字符串出来冒充"标准答案"。
        solution = {"correctAnswers": ["A", "C"]}
        self.assertEqual(extract_solution_answer_text(solution), "")

    def test_missing_solution_returns_empty(self) -> None:
        self.assertEqual(extract_solution_answer_text(None), "")
        self.assertEqual(extract_solution_answer_text({}), "")


if __name__ == "__main__":
    unittest.main()
