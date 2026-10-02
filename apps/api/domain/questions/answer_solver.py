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

import ast
import math
import re
from typing import Any, Literal

from answer_evaluator import convert_latex_fraction, normalize_text, parse_number

# 判等语义变化时递增：v4 用受限 AST 替代对不可信文本的动态解析。
SOLVER_VERSION = "answer-solver-v5"

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
_THOUSANDS_GROUP = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d{1,3}(?:,\d{3})+(?!\d)")
_PERCENT_SUFFIX = re.compile(r"[%％]\s*$")
# MinerU/pypdf 把科学计数法里的 "×" OCR 成拉丁字母 "x"/"X" 是这个仓库里非常现实的情形
# （"5×10⁻²" 变成 "5x10^-2"）。只在数字夹住 "x"/"X" 时才当乘号转换——边界卡在
# "两侧都紧邻数字"，不含 "x+1" 这类合法变量用法，避免把普通代数式的 x 悄悄吃掉。
_SCI_NOTATION_X_AS_TIMES = re.compile(r"(?<=[0-9])\s*[xX]\s*(?=[0-9])")
_ASSIGNMENT_PATTERN = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$"
)
_MAX_SYMBOLIC_LENGTH = 512
_MAX_SYMBOLIC_NODES = 64
_MAX_SYMBOLIC_DEPTH = 16
_MAX_SYMBOLIC_EXPONENT = 100
_MAX_SYMBOLIC_INTEGER_BITS = 4096
_MAX_SYMBOLIC_TERMS = 128
_MAX_SYMBOLIC_DEGREE = 64


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
    text = _THOUSANDS_GROUP.sub(lambda match: match.group(0).replace(",", ""), text)
    text = text.replace("，", "")
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
    """用受限 AST 构造 SymPy 表达式，不执行或动态解析输入文本。"""
    prepared = _prepare_for_symbolic_parse(value)
    if not prepared or len(prepared) > _MAX_SYMBOLIC_LENGTH:
        return None
    if not _SYMBOLIC_SAFE_CHARS.fullmatch(prepared):
        return None
    # 补上教材常见的 2x 与 2(x+1) 隐式乘法；其余 Python 表达式语法仍会在白名单处拒绝。
    prepared = re.sub(r"(?<=[0-9])(?=[A-Za-z(])", "*", prepared)
    prepared = re.sub(r"(?<=[)])(?=[0-9A-Za-z(])", "*", prepared)
    paren_depth = 0
    for char in prepared:
        if char == "(":
            paren_depth += 1
            if paren_depth > _MAX_SYMBOLIC_DEPTH:
                return None
        elif char == ")":
            paren_depth -= 1
            if paren_depth < 0:
                return None
    if paren_depth:
        return None
    try:
        import sympy as sp
    except ImportError:
        return None
    try:
        tree = ast.parse(prepared.replace("^", "**"), mode="eval")
    except (SyntaxError, ValueError, RecursionError):
        return None

    def within_budget(node: ast.AST) -> tuple[bool, int, int, int]:
        """Conservatively bound exact-number growth and polynomial expansion cost.

        Return (contains_variable, terms, degree, coefficient_bits). This pass
        runs before constructing SymPy values: expressions whose intermediate
        integers or expanded symbolic form could grow too large are undecidable.
        """
        if isinstance(node, ast.Expression):
            return within_budget(node.body)
        if isinstance(node, ast.Constant) and type(node.value) is int:
            return False, 1, 0, max(1, abs(node.value).bit_length())
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            return False, 1, 0, 53
        if isinstance(node, ast.Name):
            return (node.id not in {"pi", "e"}, 1, 1 if node.id not in {"pi", "e"} else 0, 0)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return within_budget(node.operand)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sqrt" and len(node.args) == 1:
            return within_budget(node.args[0])
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            left = within_budget(node.left)
            right = within_budget(node.right)
            symbolic = left[0] or right[0]
            if isinstance(node.op, ast.Add) or isinstance(node.op, ast.Sub):
                terms = left[1] + right[1]
                degree = max(left[2], right[2])
                bits = max(left[3], right[3]) + 1
            elif isinstance(node.op, ast.Mult):
                terms = left[1] * right[1]
                degree = left[2] + right[2]
                bits = left[3] + right[3]
            elif isinstance(node.op, ast.Div):
                # Division by a symbolic expression can force expensive rational
                # normalization in equals(); fail closed instead of guessing its cost.
                if right[0]:
                    return True, _MAX_SYMBOLIC_TERMS + 1, _MAX_SYMBOLIC_DEGREE + 1, _MAX_SYMBOLIC_INTEGER_BITS + 1
                terms, degree, bits = left[1], left[2], left[3] + right[3]
            else:
                exponent_node = node.right
                sign = 1
                if isinstance(exponent_node, ast.UnaryOp) and isinstance(exponent_node.op, (ast.UAdd, ast.USub)):
                    sign = -1 if isinstance(exponent_node.op, ast.USub) else 1
                    exponent_node = exponent_node.operand
                exponent: int | float | None = None
                if isinstance(exponent_node, ast.Constant):
                    if type(exponent_node.value) is int:
                        exponent = sign * int(exponent_node.value)
                    elif type(exponent_node.value) is float:
                        exponent = sign * float(exponent_node.value)
                if exponent is None or not math.isfinite(float(exponent)) or abs(exponent) > _MAX_SYMBOLIC_EXPONENT:
                    return True, _MAX_SYMBOLIC_TERMS + 1, _MAX_SYMBOLIC_DEGREE + 1, _MAX_SYMBOLIC_INTEGER_BITS + 1
                if symbolic and (exponent < 0 or not float(exponent).is_integer()):
                    return True, _MAX_SYMBOLIC_TERMS + 1, _MAX_SYMBOLIC_DEGREE + 1, _MAX_SYMBOLIC_INTEGER_BITS + 1
                if not float(exponent).is_integer() and not symbolic:
                    terms, degree, bits = 1, 0, 53
                else:
                    integer_exponent = int(exponent)
                    terms = left[1] ** max(0, integer_exponent)
                    degree = left[2] * max(0, integer_exponent)
                    bits = left[3] * abs(integer_exponent) + 1
            # Every intermediate must fit: a later power of zero cannot make an
            # expensive child safe to construct before that final simplification.
            if (
                bits > _MAX_SYMBOLIC_INTEGER_BITS
                or terms > _MAX_SYMBOLIC_TERMS
                or (symbolic and degree > _MAX_SYMBOLIC_DEGREE)
            ):
                raise ValueError("symbolic intermediate is outside the supported budget")
            return symbolic, terms, degree, bits
        return True, _MAX_SYMBOLIC_TERMS + 1, _MAX_SYMBOLIC_DEGREE + 1, _MAX_SYMBOLIC_INTEGER_BITS + 1

    try:
        contains_variable, estimated_terms, estimated_degree, estimated_bits = within_budget(tree)
    except (ValueError, OverflowError, RecursionError):
        return None
    if (
        estimated_bits > _MAX_SYMBOLIC_INTEGER_BITS
        or estimated_terms > _MAX_SYMBOLIC_TERMS
        or (contains_variable and estimated_degree > _MAX_SYMBOLIC_DEGREE)
    ):
        return None

    node_count = 0

    def build(node: ast.AST, depth: int = 0) -> Any:
        nonlocal node_count
        node_count += 1
        if node_count > _MAX_SYMBOLIC_NODES or depth > _MAX_SYMBOLIC_DEPTH:
            raise ValueError("symbolic expression is too large")
        if isinstance(node, ast.Expression):
            return build(node.body, depth + 1)
        if isinstance(node, ast.Constant) and type(node.value) is int:
            if abs(node.value) > 1e12:
                raise ValueError("numeric literal is outside the supported range")
            return sp.Integer(node.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, float):
            if not math.isfinite(node.value) or abs(node.value) > 1e12:
                raise ValueError("numeric literal is outside the supported range")
            return sp.Float(node.value)
        if isinstance(node, ast.Name):
            if node.id == "pi":
                return sp.pi
            if node.id == "e":
                return sp.E
            if len(node.id) == 1 and node.id.isascii() and node.id.isalpha():
                return sp.Symbol(node.id)
            raise ValueError("unsupported identifier")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = build(node.operand, depth + 1)
            return operand if isinstance(node.op, ast.UAdd) else -operand
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            left = build(node.left, depth + 1)
            right = build(node.right, depth + 1)
            if isinstance(node.op, ast.Pow):
                if right.is_number is not True or abs(float(right)) > _MAX_SYMBOLIC_EXPONENT:
                    raise ValueError("unsupported exponent")
                if left.is_number is not True and not float(right).is_integer():
                    raise ValueError("symbolic powers must use integer exponents")
                return left ** right
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            return left / right
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "sqrt"
            and len(node.args) == 1
            and not node.keywords
        ):
            return sp.sqrt(build(node.args[0], depth + 1))
        raise ValueError("unsupported expression syntax")

    try:
        return build(tree)
    except (ValueError, TypeError, OverflowError, RecursionError):
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


def _split_top_level(value: str) -> tuple[list[str], set[str]]:
    """Split solution-set separators without touching nested punctuation.

    Commas are deliberately reported separately from ``or``/semicolon.  A comma
    outside braces is only a solution-set separator when the caller confirms that
    every part repeats the same variable assignment.  This keeps ``(x, y)``,
    ``[a, b]`` and ``f(x, y)`` intact while still accepting ``x=1, x=2``.
    """
    parts: list[str] = []
    separators: set[str] = set()
    start = 0
    depth = 0
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\":
            # Escaped braces (``\\{...\\}``) are set notation, not a nested
            # LaTeX group for this small scanner.  Skip the escaped character.
            index += 2
            continue
        if char in "([{":
            depth += 1
            index += 1
            continue
        if char in ")]}":
            depth = max(0, depth - 1)
            index += 1
            continue
        if depth == 0:
            if char in {";", "；"}:
                parts.append(value[start:index].strip())
                separators.add("semicolon")
                start = index + 1
                index += 1
                continue
            if value.startswith("或", index):
                parts.append(value[start:index].strip())
                separators.add("or")
                start = index + 1
                index += 1
                continue
            if value[index:index + 2].casefold() == "or":
                before = value[index - 1] if index else " "
                after = value[index + 2] if index + 2 < len(value) else " "
                if not (before.isalnum() or before == "_") and not (after.isalnum() or after == "_"):
                    parts.append(value[start:index].strip())
                    separators.add("or")
                    start = index + 2
                    index += 2
                    continue
            if char == ",":
                parts.append(value[start:index].strip())
                separators.add("comma")
                start = index + 1
                index += 1
                continue
        index += 1
    parts.append(value[start:].strip())
    return parts, separators


def _unwrap_solution_set(value: str) -> tuple[str, bool]:
    """Return the body and whether a full outer pair denotes a set."""
    text = value.strip()
    escaped = text.startswith(r"\{") and text.endswith(r"\}")
    if escaped:
        return text[2:-2].strip(), True
    if not (text.startswith("{") and text.endswith("}")):
        return text, False
    depth = 0
    for index, char in enumerate(text):
        if char == "\\":
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0 and index != len(text) - 1:
                return text, False
            if depth < 0:
                return text, False
    return (text[1:-1].strip(), depth == 0)


def _parse_solution_set(value: Any) -> tuple[list[str] | None, bool]:
    """Parse only an explicit, conservative solution-set spelling.

    The boolean distinguishes "not a set" from "set syntax was present but is
    malformed".  The latter must remain ``undecidable`` rather than falling back
    to scalar parsing and accidentally treating punctuation as mathematics.
    """
    text = str(value or "").strip()
    body, braced = _unwrap_solution_set(text)
    parts, separators = _split_top_level(body)
    if not braced and not separators:
        return None, False
    if not braced and separators == {"comma"}:
        assignments = [_ASSIGNMENT_PATTERN.fullmatch(part) for part in parts]
        names = {match.group(1).casefold() for match in assignments if match is not None}
        if len(names) != 1 or len(assignments) != len(parts):
            return None, False
    if not parts or any(not part for part in parts):
        return [], True
    return parts, True


def _assignment_difference(value: str) -> Any | None:
    match = _ASSIGNMENT_PATTERN.fullmatch(value)
    if match is None:
        return None
    left = _parse_symbolic(match.group(1))
    right = _parse_symbolic(match.group(2))
    if left is None or right is None:
        return None
    try:
        return left - right
    except Exception:
        return None


def _symbolic_equal(first: Any, second: Any) -> bool | None:
    """Compare already-parsed expressions using the solver's three-state rule."""
    try:
        diff = first - second
        if diff.free_symbols:
            return diff.equals(0)
        return abs(complex(diff.evalf(30))) <= _NUMERIC_TOLERANCE
    except Exception:
        return None


def _check_scalar_agreement(candidate_text: str, reference_text: str) -> tuple[AgreementStatus, str, str]:
    """Return scalar status without recursively attempting solution-set parsing."""
    if normalize_text(candidate_text) and normalize_text(candidate_text) == normalize_text(reference_text):
        return "agree", "text-normalized-match", ""

    candidate_number = _parse_numeric_with_percent(candidate_text)
    reference_number = _parse_numeric_with_percent(reference_text)
    if candidate_number is not None and reference_number is not None:
        agree = abs(candidate_number - reference_number) <= _NUMERIC_TOLERANCE
        return "agree" if agree else "disagree", "numeric-tolerance", ""

    candidate_assignment = _assignment_difference(candidate_text)
    reference_assignment = _assignment_difference(reference_text)
    if candidate_assignment is not None or reference_assignment is not None:
        if candidate_assignment is None or reference_assignment is None:
            return "undecidable", "unparseable", "无法解析为可判等的方程式"
        same_orientation = _symbolic_equal(candidate_assignment, reference_assignment)
        opposite_orientation = _symbolic_equal(candidate_assignment, -reference_assignment)
        if same_orientation is True or opposite_orientation is True:
            return "agree", "symbolic-equivalence", ""
        if same_orientation is False and opposite_orientation is False:
            return "disagree", "symbolic-equivalence", ""
        return "undecidable", "symbolic-undetermined", "方程式符号化简无法确定等价性"

    candidate_expr = _parse_symbolic(candidate_text)
    reference_expr = _parse_symbolic(reference_text)
    if candidate_expr is None or reference_expr is None:
        return "undecidable", "unparseable", "无法解析为可判等的数学表达式，可能是文字/证明类开放答案"
    decision = _symbolic_equal(candidate_expr, reference_expr)
    if decision is True:
        return "agree", "symbolic-equivalence", ""
    if decision is False:
        return "disagree", "symbolic-equivalence", ""
    return "undecidable", "symbolic-undetermined", "符号化简无法确定等价性"


def _deduplicate_solution_elements(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        if any(_check_scalar_agreement(value, existing)[0] == "agree" for existing in unique):
            continue
        unique.append(value)
    return unique


def _solution_sets_agree(candidate: list[str], reference: list[str]) -> tuple[AgreementStatus, str]:
    """Compare sets with one-to-one matching and conservative unknown handling."""
    candidate = _deduplicate_solution_elements(candidate)
    reference = _deduplicate_solution_elements(reference)
    matrix = [
        [_check_scalar_agreement(item, expected)[0] for expected in reference]
        for item in candidate
    ]
    if len(candidate) != len(reference):
        if any(status == "undecidable" for row in matrix for status in row):
            return "undecidable", "solution-set-undetermined"
        return "disagree", "solution-set"
    if not candidate:
        return "agree", "solution-set"

    has_unknown_matching = False
    found_agreement = False

    def visit(index: int, used: set[int], saw_unknown: bool, saw_disagree: bool) -> None:
        nonlocal found_agreement, has_unknown_matching
        if found_agreement:
            return
        if index == len(candidate):
            if not saw_disagree:
                if not saw_unknown:
                    found_agreement = True
                else:
                    has_unknown_matching = True
            return
        for reference_index, status in enumerate(matrix[index]):
            if reference_index in used:
                continue
            visit(
                index + 1,
                used | {reference_index},
                saw_unknown or status == "undecidable",
                saw_disagree or status == "disagree",
            )

    visit(0, set(), False, False)
    if found_agreement:
        return "agree", "solution-set"
    if has_unknown_matching:
        return "undecidable", "solution-set-undetermined"
    return "disagree", "solution-set"


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

    # 集合语义只在明确出现花括号、顶层“或/or/分号”，或同一变量重复赋值的
    # 顶层逗号时启用。普通的坐标、区间和函数参数逗号不会被拆开。
    candidate_set, candidate_is_set = _parse_solution_set(candidate_text)
    reference_set, reference_is_set = _parse_solution_set(reference_text)
    if candidate_is_set or reference_is_set:
        if not candidate_is_set or not reference_is_set or not candidate_set or not reference_set:
            return _agreement_result(
                "undecidable", "solution-set-undetermined", candidate_text, reference_text,
                reason="解集写法不完整或两侧集合语义不一致，无法安全一一匹配",
            )
        status, method = _solution_sets_agree(candidate_set, reference_set)
        return _agreement_result(
            status, method, candidate_text, reference_text,
            reason="解集元素存在无法确定的比较" if status == "undecidable" else "",
        )

    scalar_status, scalar_method, scalar_reason = _check_scalar_agreement(candidate_text, reference_text)
    return _agreement_result(
        scalar_status, scalar_method, candidate_text, reference_text, reason=scalar_reason,
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
