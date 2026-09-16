"""答案核验阶段的确定性符号等价判等器。

为什么需要这个模块——`verification` 阶段过去让模型自己给出一个布尔字段
`solverAgreement`，字段名暗示"跑过求解器核对过"，但仓库里从未引入过 CAS/solver：
那只是模型的自我断言。这违反了 `docs/product-roadmap.md` 记录的、三次独立外部评审
一致认可的结论——"数学判对错交给确定性程序而非模型"。`answer_evaluator.py` 对学生
作答就是这么做的，但核验阶段的 `solverAgreement` 一直是个例外，本模块补上这个缺口，
把 `solverAgreement` 的裁决权收回到代码手里。

设计边界（照抄 `docs/engineering-roadmap.md` "符号等价判题" 条目，不是新发明）：

1. 只做等价性判断（parse + `simplify(a - b) == 0`，这里用等价的 `sympy.Expr.equals`
   实现），绝不解方程、绝不生成解题步骤——两侧输入本来就已经"声称"是答案，
   本模块只负责验证这两个声称是否指向同一个数学对象。
2. 三态结果（agree / disagree / undecidable），不是二态。核验阶段面对的多数是几何证明、
   开放式短答题——CAS 根本判不了，必须能把"判不了"和"判定不一致"分开，否则会把
   大量正常的"无法核验"题目误报成冲突，重蹈"把措辞不准变成学习记录写错"的覆辙
   （见 `answer_evaluator.normalize_true_false` 的教训）。
3. 解析失败、输入缺失、`sympy.Expr.equals` 返回 `None`（真正判不出）都归为
   undecidable，绝不当作 agree 或 disagree 的猜测。

数值优先复用 `answer_evaluator.parse_number`（教材分数/百分号/千分位格式已经在那里
验证过），只有数值解析失败时才升级到符号层；这是"结构化归一化优先，符号层兜底"的
既定分层，不重复实现数值解析。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from answer_evaluator import convert_latex_fraction, normalize_text, parse_number

# 判等逻辑的版本号，约定与 EVALUATOR_VERSION/QUESTION_SEGMENTATION_VERSION 等一致：
# 判等规则（哪怕只是新增一种符号预处理）变化时必须递增，消费方（核验证据、审校面板）
# 才能正确解释历史判定的语义。v2：新增"数字夹住的 x/X 视为科学计数法乘号"预处理，
# 修复 "5x10^-2" 这类 OCR 常见写法被误判 disagree 的问题。
SOLVER_VERSION = "answer-solver-v2"

AgreementStatus = Literal["agree", "disagree", "undecidable"]

_NUMERIC_TOLERANCE = 1e-9

_SUPERSCRIPT_MAP = str.maketrans({
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
    "⁻": "-", "⁺": "+",
})
# 上标数字紧跟在数字/字母/右括号之后才转换为幂运算，避免误伤普通文本里偶然出现的
# 上标字符（教材 OCR 里几乎不会孤立出现上标）。
_SUPERSCRIPT_RUN = re.compile(r"([0-9a-zA-Z)])([⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+)")
_SQRT_GROUP = re.compile(r"√\s*\(([^()]+)\)")
_SQRT_BARE = re.compile(r"√\s*([0-9a-zA-Z]+(?:\.[0-9]+)?)")
# sympy 会把任意 Unicode 字母串当成合法符号名（包括中文），"见解析"这类开放题答案
# 会被悄悄解析成两个不同的 Symbol，相减必然非零，从而被误判成 disagree——这正是本
# 模块最不能出的错。因此符号解析前先做白名单校验：预处理后的文本只允许出现数学表达式
# 会用到的 ASCII 字符；出现任何白名单外的字符（中文、单位汉字等）一律当作解析失败，
# 交回 undecidable，而不是让 sympy 把它们造成假符号。
_SYMBOLIC_SAFE_CHARS = re.compile(r"^[0-9A-Za-z_+\-*/^().,=<>\s]+$")
_PERCENT_SUFFIX = re.compile(r"[%％]\s*$")
# MinerU/pypdf 把科学计数法里的 "×" OCR 成拉丁字母 "x"/"X" 是这个仓库里非常现实的情形
# （"5×10⁻²" 变成 "5x10^-2"）。只在数字夹住 "x"/"X" 时才当乘号转换——边界卡在
# "两侧都紧邻数字"，不含 "x+1" 这类合法变量用法，避免把普通代数式的 x 悄悄吃掉。
_SCI_NOTATION_X_AS_TIMES = re.compile(r"(?<=[0-9])\s*[xX]\s*(?=[0-9])")


def _expand_superscripts(text: str) -> str:
    """把 `10⁻²`、`x²` 这类上标写法转成 `10**(-2)`、`x**(2)`，供 sympy 解析。"""

    def _replace(match: "re.Match[str]") -> str:
        base, exponent = match.group(1), match.group(2).translate(_SUPERSCRIPT_MAP)
        return f"{base}**({exponent})"

    return _SUPERSCRIPT_RUN.sub(_replace, text)


def _expand_roots(text: str) -> str:
    """把 `√(...)`/`√2` 转成 sympy 能解析的 `sqrt(...)`。"""
    text = _SQRT_GROUP.sub(r"sqrt(\1)", text)
    return _SQRT_BARE.sub(r"sqrt(\1)", text)


def _prepare_for_symbolic_parse(value: Any) -> str:
    """符号解析前的书写形式转换——只转写法，不做任何数值判断或求解。"""
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace(",", "").replace("，", "")
    text = convert_latex_fraction(text)
    text = text.replace("×", "*").replace("π", "pi").replace("％", "%")
    text = _SCI_NOTATION_X_AS_TIMES.sub("*", text)
    text = _expand_roots(text)
    text = _expand_superscripts(text)
    # 度数/百分号不是符号层要判等的对象；只在剥离后仍非空时才剥离，
    # 避免把"90%"这类纯单位答案吃成空字符串导致误判 undecidable。
    stripped = re.sub(r"[°%]+\s*$", "", text).strip()
    return stripped or text


def _parse_symbolic(value: Any) -> Any | None:
    """把答案文本解析成 sympy 表达式；解析失败或含非法字符返回 None，绝不抛错给调用方。"""
    prepared = _prepare_for_symbolic_parse(value)
    if not prepared or not _SYMBOLIC_SAFE_CHARS.fullmatch(prepared):
        return None
    # sympy 是重量级导入（约 100ms+），且只有数值判等失败时才需要它。延迟到真正
    # 解析符号表达式时才导入，避免拖慢模块导入方（包含只需要 SOLVER_VERSION/类型
    # 的调用方）和学生作答判题这类延迟敏感路径——尽管后者目前并不导入本模块。
    try:
        from sympy.parsing.sympy_parser import (
            convert_xor,
            implicit_multiplication_application,
            parse_expr,
            standard_transformations,
        )
    except ImportError:
        return None
    transformations = standard_transformations + (implicit_multiplication_application, convert_xor)
    try:
        return parse_expr(prepared, transformations=transformations, evaluate=True)
    except Exception:
        return None


def _parse_numeric_with_percent(text: str) -> float | None:
    """在 ``parse_number`` 之上再补一步百分号语义转换。

    ``answer_evaluator.parse_number`` 只负责把 "90%" 里的 "%" 当无关后缀剥掉、返回
    90.0——这对同一题里学生答案与标准答案同一书写习惯的比较是安全的（两边同样带
    "%"，比大小结论不变）。但这里比较的是解答与来源答案两段可能来自不同书写习惯的
    文本（比如一边写 "90%"、一边写 "0.9"），必须先把百分号换算成真实数值再比较，
    否则会把等价答案误判成 disagree。
    """
    value = parse_number(text)
    if value is None:
        return None
    return value / 100 if _PERCENT_SUFFIX.search(text.strip()) else value


def _agreement_result(
    status: AgreementStatus,
    method: str,
    candidate_text: str,
    reference_text: str,
    *,
    reason: str = "",
) -> dict[str, Any]:
    return {
        "status": status,
        "method": method,
        "solverVersion": SOLVER_VERSION,
        "candidate": candidate_text[:160],
        "reference": reference_text[:160],
        "reason": reason,
    }


def check_answer_agreement(candidate: Any, reference: Any) -> dict[str, Any]:
    """比较候选答案与参照答案是否等价；只判等价，不解题、不生成推导步骤。

    返回结构包含 ``status``（agree/disagree/undecidable）、``method``（走的是哪条
    判等路径）、``solverVersion`` 和截断后的原始输入，供审校面板解释"为什么"。
    """
    candidate_text = str(candidate or "").strip()
    reference_text = str(reference or "").strip()
    if not candidate_text or not reference_text:
        return _agreement_result(
            "undecidable", "missing-input", candidate_text, reference_text,
            reason="候选答案或来源答案为空，无法比较",
        )

    # 第零层：两段文本归一化后逐字相同——多数是"解答直接抄了来源答案原文"，
    # 或者两边都是同一句无法数学化判等的开放式表述（如证明题的“见解析”）。
    # 无论哪种，字面完全一致都足以判定 agree，不需要（也没法）先解析成表达式。
    if normalize_text(candidate_text) and normalize_text(candidate_text) == normalize_text(reference_text):
        return _agreement_result("agree", "text-normalized-match", candidate_text, reference_text)

    # 第一层：复用既有的确定性数值归一化，覆盖分数/百分号/千分位等常见格式，
    # 再补一步百分号语义换算（见 `_parse_numeric_with_percent` 的注释）。
    candidate_number = _parse_numeric_with_percent(candidate_text)
    reference_number = _parse_numeric_with_percent(reference_text)
    if candidate_number is not None and reference_number is not None:
        agree = abs(candidate_number - reference_number) <= _NUMERIC_TOLERANCE
        return _agreement_result(
            "agree" if agree else "disagree", "numeric-tolerance", candidate_text, reference_text,
        )

    # 第二层：数值归一化判不了（含符号/代数式/根式/科学计数法等），升级到符号等价。
    candidate_expr = _parse_symbolic(candidate_text)
    reference_expr = _parse_symbolic(reference_text)
    if candidate_expr is None or reference_expr is None:
        return _agreement_result(
            "undecidable", "unparseable", candidate_text, reference_text,
            reason="无法解析为可判等的数学表达式，可能是文字/证明类开放答案",
        )
    try:
        diff = (candidate_expr - reference_expr)
        if diff.free_symbols:
            # 含未消去的变量：走结构化判等。Expr.equals 语义正好是三态——
            # True/False/无法判定时返回 None，与 agree/disagree/undecidable 一一对应。
            decision = diff.equals(0)
        else:
            # 纯数值（可能是根式、π 等精确无理数，也可能是十进制近似）：
            # `equals` 对混入 Float 的表达式过于保守，容易把"数值上相等"误判成
            # 不相等（如 sqrt(2) 与 2**0.5），因此改用高精度数值求值 + 容差判断，
            # 容差同样只用于判等价，不影响任何求解逻辑。
            decision = abs(complex(diff.evalf(30))) <= 1e-9
    except Exception:
        decision = None
    if decision is True:
        return _agreement_result("agree", "symbolic-equivalence", candidate_text, reference_text)
    if decision is False:
        return _agreement_result("disagree", "symbolic-equivalence", candidate_text, reference_text)
    return _agreement_result(
        "undecidable", "symbolic-undetermined", candidate_text, reference_text,
        reason="符号化简无法确定等价性",
    )


def extract_solution_answer_text(solution: dict[str, Any] | None) -> str:
    """从 SolutionIR 风格的结构里取出唯一、可比较的答案文本。

    只在能确定"这就是唯一答案"时才返回非空值：多空（``blanks``）、多选、或者
    ``correctAnswers`` 里有一个以上取值时，没有单一可比较对象，交回空字符串让
    调用方走 undecidable，而不是拼接/挑选其中一个去冒充"标准答案"。
    """
    if not isinstance(solution, dict):
        return ""
    spec = solution.get("answerSpec")
    if isinstance(spec, dict):
        expected = str(spec.get("expected") or "").strip()
        if expected:
            return expected
    correct_answer = solution.get("correctAnswer")
    if isinstance(correct_answer, str) and correct_answer.strip():
        return correct_answer.strip()
    values = solution.get("correctAnswers")
    if isinstance(values, list) and len(values) == 1 and str(values[0]).strip():
        return str(values[0]).strip()
    return ""


__all__ = [
    "SOLVER_VERSION",
    "AgreementStatus",
    "check_answer_agreement",
    "extract_solution_answer_text",
]
