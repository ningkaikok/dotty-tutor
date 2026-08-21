"""题目规范化、内容块构建和发布质量门禁的纯函数集合。

这里不调用模型、不访问数据库。模型输出和 OCR 原文进入本模块后，会被转换为前端稳定契约，
并用可重复的规则判断是否允许发布。把规则保持为纯函数，便于用历史坏题做回归测试。
"""

from __future__ import annotations

import hashlib
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from domain.questions.source import (
    QUESTION_SEGMENTATION_VERSION,
    is_likely_exam_instruction,
)
from infrastructure.runtime.review_runtime import formula_anomaly_score, normalize_ocr_question

QUESTION_START_PATTERN = re.compile(r"(?m)^\s*(?P<number>\d{1,3})[.．、]\s*")
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
# 右方括号不计入路径：模型会把图片写成 ``[主视图图片：images/x.jpg]``，若把 ``]`` 当作
# 路径的一部分吃掉，剩下的左方括号就会变成无法配对的残缺文本。
BARE_IMAGE_REFERENCE_PATTERN = re.compile(r"(?<![A-Za-z0-9_.-])(?:images/|/api/uploads/)[^\s)\]<>]+")
# 整段删除“带说明的方括号图片注释”。中英文方括号都覆盖，内部不允许再嵌套方括号，
# 避免把一大段正常题干误删。
BRACKETED_IMAGE_ANNOTATION_PATTERN = re.compile(
    r"[\[【][^\[\]【】]*(?:images/|/api/uploads/)[^\[\]【】]*[\]】]"
)
# 三种图片引用形态的统一入口，用于同时删除引用并记录它在题干中的位置。
IMAGE_REFERENCE_PATTERN = re.compile(
    r"!\[[^\]]*\]\((?P<markdown_path>[^)]+)\)"
    r"|[\[【][^\[\]【】]*(?:images/|/api/uploads/)[^\[\]【】]*[\]】]"
    r"|(?<![A-Za-z0-9_.-])(?:images/|/api/uploads/)[^\s)\]<>]+"
)
# 只有这些题型会进入 answer_evaluator 的确定性判题分支；其余题型的 answerSpec
# 永远读不到，属于会误导后续改动的死数据。
DETERMINISTIC_ANSWER_TYPES = frozenset({"choice", "multi-select", "true-false", "fill-blank", "numeric"})
# 成行出现的小问编号，例如 ``（1）`` 或 ``(2)``。
SUB_QUESTION_PATTERN = re.compile(r"[（(]\s*([1-9])\s*[）)]")
# 只匹配“独占一行的选项”，形如 ``A. 点A的左边``；正文中顺带出现的字母不会成行，
# 因此不会被误当成选项。
PROMPT_OPTION_LINE_PATTERN = re.compile(
    r"(?m)^\s*(?:\(([A-H])\)|([A-H]))[.．:：、]\s*(\S.*?)\s*$"
)
MATH_FRAGMENT_PATTERN = re.compile(r"(\$\$[\s\S]+?\$\$|\$[^$]+?\$)")
CHOICE_MARKER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:\(([A-D])\)|([A-D])[.．:：、])\s*"
)
# Image-option OCR commonly emits a bare label on its own line (``A`` then image)
# instead of ``A.``.  It is only used when the source already contains four/five
# images, so ordinary prose containing the letter A is not treated as an option.
STANDALONE_CHOICE_MARKER_PATTERN = re.compile(r"(?im)^\s*(?:\(([A-D])\)|([A-D]))\s*$")
ANSWER_LEAK_PATTERN = re.compile(r"(?im)^\s*(?:【\s*)?(?:参考)?(?:答案|解析)\s*(?:】\s*)?")
# MinerU 把统计表输出成原始 HTML，属性经常不带引号（``<td rowspan=1 colspan=1>``），
# 这里只用正则定位 <table>...</table> 片段的边界，内部结构交给 html.parser 解析，
# 不用正则猜测标签和属性。
TABLE_BLOCK_PATTERN = re.compile(r"(<table\b[^>]*>.*?</table>)", re.IGNORECASE | re.DOTALL)
TABLE_TAG_PATTERN = re.compile(r"</?table\b|</?t[dr]\b", re.IGNORECASE)


def _safe_text(value: Any, fallback: str, limit: int = 600) -> str:
    text = str(value or "").strip()
    return (text or fallback)[:limit]


def build_lesson_prompt(source: str, repair_errors: list[str] | None = None) -> str:
    """构造结构化出题提示；重试时只附加上一轮的确定性错误。"""
    repair_instruction = ""
    if repair_errors:
        bounded_errors = "\n".join(f"- {str(error)[:180]}" for error in repair_errors[:8])
        repair_instruction = f"""

上一次结构化结果未通过确定性校验。本次必须修复以下问题，不要改变原题语义：
{bounded_errors}
"""
    return f"""
请从下面的教材文字中结构化一道中文数学题，并生成互动辅导脚本。
要求：
1. 只依据教材文字，不确定的条件不要补充。
2. lessonSteps 恰好输出 4 步；每个 text 和 speechText 都不超过 60 个汉字。
3. guideCards 恰好输出 3 张，每个字段不超过 50 个汉字；依次从轻提示到强提示，只引导下一步。
4. 输入只包含一道按题号切出的完整题，不得换题、合并其他题或遗漏小问。
5. questionNumber 原样填写题号；prompt 保留完整题干，但不要重复独占一行的 (A)(B)(C)(D) 图片标签。
6. questionType 只能是 choice、multi-select、true-false、short-answer、fill-blank、numeric、draw-line 之一：有单选项用 choice；有多个正确选项用 multi-select；要求判断正误用 true-false；题干有空格待填写用 fill-blank；要求填写数值或公式结果用 numeric；要求作图、连线或画辅助线用 draw-line；其余用 short-answer。
7. true-false 的 correctAnswer 必须是“正确”或“错误”；multi-select 应填写 correctAnswers；其他题型如果教材没有明确答案则不要猜测。
8. fill-blank 必须为每个空生成 blanks，包含稳定 id、label、answerType、correctAnswers、tolerance 和 unit；numeric 必须生成 answerSpec，包含 answerType、expected、accepted、tolerance 和 unit。
9. draw-line 题必须生成 interaction：type 为 draw-line，points 中给出需要显示的点及 0 到 1 的归一化坐标，requiredConnections 给出必须连接的点对；其他题型 interaction.type 为 none。
10. 选择题的文字选项逐项放入 options。若 A/B/C/D 是四张图片，则 options 保留四个标签，并把四个图片文件名按 A、B、C、D 顺序放入 imageReferences。
11. givens 只拆出题目明确给出的条件，不要用整段教材或图片 Markdown 代替。
11.1 题干中图片出现的位置必须原样保留教材文字里的 `![](images/xxx.jpg)` 引用，不要删除、移动或改写成“[主视图图片：...]”这类文字描述。图注文字（如“主视图”“图1”）照常保留。系统据此把图片放回题干中的正确位置。
12. 不依赖图片则 imageReferences 返回空数组。

教材文字：
---
{source}
---
{repair_instruction}
""".strip()


def write_model_prompt_artifact(asset_dir: Path, question_sources: list[tuple[str, str, list[str]]]) -> Path:
    asset_dir.mkdir(parents=True, exist_ok=True)
    sections = [
        "# OCR 后结构化模型提示词\n",
        "> MinerU 不使用自然语言提示词；下列内容是 OCR 完成后实际交给结构化模型的提示词。\n",
        f"> 题目切分版本：`{QUESTION_SEGMENTATION_VERSION}`。不同版本提示词不会被视为本次切分结果。\n",
    ]
    for index, (number, block, _images) in enumerate(question_sources, start=1):
        sections.append(f"\n## 第 {number or index} 题\n\n```text\n{build_lesson_prompt(block)}\n```\n")
    path = asset_dir / "model-prompt.md"
    path.write_text("\n".join(sections), encoding="utf-8")
    return path


def normalize_question_interaction(raw: Any, question_type: str) -> dict[str, Any]:
    """把画线题交互限制在安全、可渲染的点与连线集合内。"""
    if question_type != "draw-line" or not isinstance(raw, dict):
        return {"type": "none", "instruction": "", "points": [], "requiredConnections": []}
    points: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_points = raw.get("points") if isinstance(raw.get("points"), list) else []
    for item in raw_points[:12]:
        if not isinstance(item, dict):
            continue
        point_id = _safe_text(item.get("id"), "", 8).strip()
        if not point_id or point_id in seen:
            continue
        try:
            x = max(0.04, min(float(item.get("x", 0.5)), 0.96))
            y = max(0.08, min(float(item.get("y", 0.5)), 0.92))
        except (TypeError, ValueError):
            continue
        seen.add(point_id)
        points.append({"id": point_id, "label": _safe_text(item.get("label"), point_id, 12), "x": round(x, 4), "y": round(y, 4)})
    raw_connections = raw.get("requiredConnections") if isinstance(raw.get("requiredConnections"), list) else []
    required_connections: list[list[str]] = []
    for item in raw_connections[:12]:
        if not isinstance(item, list) or len(item) != 2:
            continue
        first, second = (_safe_text(value, "", 8).strip() for value in item)
        if first and second and first != second and first in seen and second in seen:
            pair = sorted([first, second])
            if pair not in required_connections:
                required_connections.append(pair)
    if len(points) < 2:
        points = [{"id": "A", "label": "A", "x": 0.25, "y": 0.5}, {"id": "B", "label": "B", "x": 0.75, "y": 0.5}]
        required_connections = [["A", "B"]]
    return {
        "type": "draw-line",
        "instruction": _safe_text(raw.get("instruction"), "请连接 A 和 B。", 160),
        "points": points,
        "requiredConnections": required_connections,
    }


def normalize_image_choice_question(payload: dict[str, Any], source_block: str, source_images: list[str]) -> None:
    """根据 OCR 的版面顺序恢复图片选择题。

    OCR 通常会输出“题干图 + A-D 四张选项图”五张图片；审核模型可能把文件名
    当作普通文字写回 JSON，因此这里不信任模型的图片字段，而是以 OCR 来源顺序
    重新绑定；题干图缺失时也支持四张选项图的当前版式。
    """
    labels = _image_choice_labels(source_block, len(source_images))
    if labels[:4] != ["A", "B", "C", "D"] or len(source_images) not in {4, 5}:
        return
    question = payload["question"]
    image_urls = [str(url) for url in question.get("imageUrls", [])]
    if len(image_urls) != len(source_images):
        return
    option_start = 1 if len(image_urls) == 5 else 0
    question["optionImageUrls"] = image_urls[option_start:option_start + 4]
    question["options"] = ["(A)", "(B)", "(C)", "(D)"]
    question["imageManifest"] = [
        {
            "order": index,
            "role": "stem" if option_start == 1 and index == 0 else "option",
            "optionLabel": None if option_start == 1 and index == 0 else chr(65 + index - option_start),
            "sourceReference": source_images[index],
            "url": image_urls[index],
        }
        for index in range(len(image_urls))
    ]
    prompt = strip_image_references(str(question.get("prompt", "")))
    # 只删"整行只有标记本身"的行删不掉模型偶尔写出的"A（数轴图）"这类占位文字行——
    # 标记后面跟了实际内容时，`$` 锚定行尾会让正则失配，占位文字和下面真正的图片
    # 选择题结构重复展示给学生。这里只在已确认来源是 4/5 张图、且 OCR 原文里能找到
    # A-D 标记的前提下才会执行（见本函数开头的 `labels[:4] != ["A", "B", "C", "D"]`
    # 判断），因此任何整行以 A-D 标记开头的内容都是选项占位文字，不是题目正文，
    # 整行删除是安全的。
    prompt = re.sub(r"(?m)^\s*(?:\([A-D]\)|[A-D][.．:：、]?)\s*.*$", "", prompt)
    question["prompt"] = re.sub(r"\n{3,}", "\n\n", prompt).strip()


def _image_choice_labels(source_block: str, image_count: int) -> list[str]:
    """Extract A-D markers while preserving the OCR source order."""
    labels = [match.group(1) or match.group(2) for match in CHOICE_MARKER_PATTERN.finditer(source_block)]
    if labels[:4] == ["A", "B", "C", "D"]:
        return labels
    if image_count not in {4, 5}:
        return labels
    return [match.group(1) or match.group(2) for match in STANDALONE_CHOICE_MARKER_PATTERN.finditer(source_block)]


def extract_image_placements(text: str) -> tuple[str, list[tuple[int, str]]]:
    """删除题干中的图片引用，同时记录每张图在清理后文本中的位置。

    模型改写题干时会把图片写成 Markdown 或带说明的方括号注释，位置本身就是题意的
    一部分（“主视图”“俯视图”“图1/图2”各自对应哪张图）。此前清理只删不记，位置信息
    被丢弃，前端只能把所有题干图整批贴在文字之后，图注和图片对不上。
    """
    placements: list[tuple[int, str]] = []
    pieces: list[str] = []
    length = 0
    cursor = 0
    for match in IMAGE_REFERENCE_PATTERN.finditer(text):
        segment = text[cursor:match.start()]
        pieces.append(segment)
        length += len(segment)
        reference = match.group("markdown_path") or ""
        if not reference:
            bare = BARE_IMAGE_REFERENCE_PATTERN.search(match.group(0))
            reference = bare.group(0) if bare else ""
        if reference:
            placements.append((length, reference))
        cursor = match.end()
    pieces.append(text[cursor:])
    cleaned = re.sub(r"[ \t]{2,}", " ", "".join(pieces))
    # 压缩空白会改变偏移，只有在没有压缩发生时才信任记录下来的位置。
    if cleaned != "".join(pieces):
        return cleaned, []
    return cleaned, placements


def strip_image_references(text: str) -> str:
    """删除模型误写入题干的 Markdown 或裸图片路径，保留题意文字。

    模型有时不用 Markdown，而是写成带说明的方括号注释（``[主视图图片：images/x.jpg]``）。
    必须整段删除：只删路径会留下 ``[主视图图片：`` 这样的残缺前缀，比原文更难读。
    这一步要在裸路径清理之前执行，否则裸路径规则会先吃掉右方括号，破坏括号配对。
    """
    text = MARKDOWN_IMAGE_PATTERN.sub("", text)
    text = BRACKETED_IMAGE_ANNOTATION_PATTERN.sub("", text)
    text = BARE_IMAGE_REFERENCE_PATTERN.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def clean_question_stem(number: str, block: str) -> str:
    stem = QUESTION_START_PATTERN.sub("", block, count=1)
    stem = strip_image_references(stem)
    return re.sub(r"\n{3,}", "\n\n", stem).strip()[:4_000]


def strip_choice_text_from_prompt(prompt: str, options: list[str]) -> str:
    if not options:
        return prompt.strip()
    matches = list(CHOICE_MARKER_PATTERN.finditer(prompt))
    labels = [match.group(1) or match.group(2) for match in matches]
    if labels[:4] != ["A", "B", "C", "D"]:
        return prompt.strip()
    stem = prompt[:matches[0].start()].rstrip(" \n\t:：")
    return re.sub(r"[（(]\s*[)）]", "（ ）", stem)


def normalize_text_choice_labels(options: list[str]) -> list[str]:
    normalized: list[str] = []
    for option in options:
        value = option.strip()
        stripped = re.sub(r"^(?:\([A-H]\)|[A-H][.:：、])\s*", "", value)
        normalized.append(stripped if stripped else value)
    return normalized


def split_concatenated_text_choices(options: list[str]) -> list[str]:
    if len(options) != 1 or not options[0].strip():
        return options
    value = options[0].strip()
    matches = list(CHOICE_MARKER_PATTERN.finditer(value))
    labels = [match.group(1) or match.group(2) for match in matches]
    if labels != ["A", "B", "C", "D"]:
        return options
    values = [value[match.start() : (matches[index + 1].start() if index + 1 < len(matches) else len(value))].strip() for index, match in enumerate(matches)]
    return values if all(values) else options


def normalize_model_math_text(value: str) -> str:
    """修复模型常见 LaTeX 转义错误，不进行开放式数学改写。"""
    replacements = {
        "\x08egin": r"\begin", "\x08end": r"\end", "\x0crac": r"\frac",
        "\text": r"\text", "\times": r"\times",
    }
    for broken, corrected in replacements.items():
        value = value.replace(broken, corrected)
    # 审核模型有时会输出“反斜杠”这个字面命令，而不是目标 LaTeX。这里只修复已知的
    # 百分号和摄氏度形式，避免宽泛正则误改题目中的真实数学表达式。
    value = re.sub(r"\\textbackslash\s*(?:\\text\s*\{\s*%\s*\}|%)", r"\\%", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\\textbackslash\s*\\textcirc\s*\{?\s*C\s*\}?",
        r"^{\\circ}\\mathrm{C}",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\\(?:textdegree|textbar|textcirc)\s*\{?\s*C\s*\}?",
        r"^{\\circ}\\mathrm{C}",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\\begin\s*\{?\s*array\s*\}?\s*\{?\s*([clr]+)\s*\}?", r"\\begin{array}{\1}", value)
    value = re.sub(r"\\end\s*\{?\s*array\s*\}?", r"\\end{array}", value)

    def normalize_math_fragment(match: re.Match[str]) -> str:
        delimiter = "$$" if match.group(0).startswith("$$") else "$"
        content = match.group(0)[len(delimiter):-len(delimiter)]
        # 全角运算符来自中文 OCR；只在数学定界符内转换，避免改写普通中文题干。
        content = content.translate(str.maketrans({"＋": "+", "－": "-", "＝": "=", "％": "%", "（": "(", "）": ")", "｛": "{", "｝": "}"}))
        content = re.sub(r"(?<!\\)×", r"\\times ", content)
        content = re.sub(r"(?:°\s*C|℃)", r"^{\\circ}\\mathrm{C}", content)
        # 只修复已知环境名。未知命令保持原样并交给质量门禁，避免猜测公式语义。
        content = re.sub(r"\\begin\s*\{?\s*(matrix|pmatrix|bmatrix|vmatrix|Vmatrix|cases|aligned)\s*\}?", r"\\begin{\1}", content)
        content = re.sub(r"\\end\s*\{?\s*(matrix|pmatrix|bmatrix|vmatrix|Vmatrix|cases|aligned)\s*\}?", r"\\end{\1}", content)
        return f"{delimiter}{content}{delimiter}"

    return MATH_FRAGMENT_PATTERN.sub(normalize_math_fragment, value)


def normalize_text_choices_from_source(payload: dict[str, Any], source_block: str) -> None:
    current = [str(item).strip() for item in payload["question"].get("options", [])]
    split_options = split_concatenated_text_choices(current)
    if split_options != current:
        payload["question"]["options"] = split_options
        return
    labels_only = len(current) == 4 and all(re.fullmatch(r"(?:\([A-D]\)|[A-D][.:：、]?)", item) for item in current)
    if not labels_only and len(current) != 1:
        return
    normalized_source = normalize_ocr_question(normalize_model_math_text(source_block))
    matches = list(CHOICE_MARKER_PATTERN.finditer(normalized_source))
    labels = [match.group(1) or match.group(2) for match in matches]
    if labels[:4] != ["A", "B", "C", "D"]:
        return
    matches = matches[:4]
    values = [
        normalized_source[
            match.end():(matches[index + 1].start() if index + 1 < len(matches) else len(normalized_source))
        ].strip()
        for index, match in enumerate(matches)
    ]
    values = [re.sub(r"^(\d+(?:\.\d+)?)\s+(?=\$10\b)", r"\1 × ", value) for value in values]
    if all(values):
        payload["question"]["options"] = values


def normalize_stacked_equation_choices(payload: dict[str, Any], source_block: str) -> None:
    normalized_source = normalize_ocr_question(normalize_model_math_text(source_block))
    if "方程" not in normalized_source or "的解为" not in normalized_source or "(A)" not in normalized_source:
        return
    solution_text = normalized_source.split("的解为", 1)[1]
    x_section, y_section = solution_text.split("(A)", 1)
    x_values = re.findall(r"x\s*=\s*(-?\s*\d+)", x_section, flags=re.IGNORECASE)
    y_values = re.findall(r"y\s*=\s*(-?\s*\d+)", y_section, flags=re.IGNORECASE)
    if len(x_values) < 4 or len(y_values) < 4:
        return
    compact = lambda value: re.sub(r"\s+", "", value)
    question = payload["question"]
    question["options"] = [f"$x={compact(x)},\\;y={compact(y)}$" for x, y in zip(x_values[:4], y_values[:4])]
    stem = QUESTION_START_PATTERN.sub("", normalized_source.split("的解为", 1)[0] + "的解为", count=1)
    question["prompt"] = stem.strip()


def rich_text_blocks(text: str, id_prefix: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for fragment in MATH_FRAGMENT_PATTERN.split(text):
        if not fragment:
            continue
        display = fragment.startswith("$$") and fragment.endswith("$$")
        inline = fragment.startswith("$") and fragment.endswith("$")
        if display or inline:
            latex = fragment[2:-2] if display else fragment[1:-1]
            blocks.append({"id": f"{id_prefix}-{len(blocks) + 1}", "type": "math", "latex": latex.strip(), "display": display})
        elif fragment.strip():
            blocks.append({"id": f"{id_prefix}-{len(blocks) + 1}", "type": "text", "text": fragment})
    return blocks


class _TableHTMLParser(HTMLParser):
    """收集 ``<table>`` 的行、列文本，不还原 rowspan/colspan 合并后的视觉网格。

    MinerU 表格通常只是简单的 tr/td 网格；发布内容只需要按来源行列展示数据，
    不需要重建合并单元格的渲染语义，所以这里保持解析逻辑简单。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th"):
            self._current_cell = []
        elif tag == "br" and self._current_cell is not None:
            self._current_cell.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self._current_row is not None:
            self.rows.append(self._current_row)
            self._current_row = None
        elif tag in ("td", "th") and self._current_cell is not None:
            if self._current_row is not None:
                self._current_row.append("".join(self._current_cell).strip())
            self._current_cell = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)


def _parse_table_block(html_fragment: str, block_id: str) -> dict[str, Any] | None:
    """Parse one ``<table>...</table>`` fragment into a structured table block."""
    parser = _TableHTMLParser()
    try:
        parser.feed(html_fragment)
        parser.close()
    except Exception:
        return None
    if not parser.rows:
        return None
    rows = [
        {
            "cells": [
                {"contentBlocks": rich_text_blocks(cell_text, f"{block_id}-r{row_index + 1}-c{cell_index + 1}")}
                for cell_index, cell_text in enumerate(row)
            ]
        }
        for row_index, row in enumerate(parser.rows)
    ]
    return {"id": block_id, "type": "table", "rows": rows}


def _text_and_table_blocks(text: str, id_prefix: str, counters: dict[str, int]) -> list[dict[str, Any]]:
    """把一段文字拆成文字/公式/表格块，表格保留在原始位置而不是贴到末尾。"""
    blocks: list[dict[str, Any]] = []
    for fragment in TABLE_BLOCK_PATTERN.split(text):
        if not fragment:
            continue
        if TABLE_BLOCK_PATTERN.fullmatch(fragment):
            counters["table"] += 1
            table_block = _parse_table_block(fragment, f"{id_prefix}-table-{counters['table']}")
            if table_block is not None:
                blocks.append(table_block)
                continue
            # 解析失败（异常畸形标签）：退回文字块，让下面的残留检查能捕捉到它，
            # 而不是静默丢弃这段内容。
        for block in rich_text_blocks(fragment, id_prefix):
            counters["text"] += 1
            block["id"] = f"{id_prefix}-{counters['text']}"
            blocks.append(block)
    return blocks


def _prompt_content_blocks(
    text: str,
    id_prefix: str,
    image_placements: list[tuple[int, str]] | None = None,
    image_by_name: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """把 prompt 拆成有序内容块；有位置记录时，题干图就地插入而不是整批贴到末尾。"""
    counters = {"text": 0, "table": 0, "image": 0}
    placements = sorted(image_placements or [])
    if not placements:
        return _text_and_table_blocks(text, id_prefix, counters)
    blocks: list[dict[str, Any]] = []
    cursor = 0
    for offset, reference in placements:
        blocks.extend(_text_and_table_blocks(text[cursor:offset], id_prefix, counters))
        cursor = max(cursor, offset)
        url = (image_by_name or {}).get(Path(reference).name)
        if not url:
            continue
        counters["image"] += 1
        blocks.append({
            "id": f"{id_prefix}-image-{counters['image']}", "type": "image", "url": url,
            "assetId": Path(url).stem, "sourceReference": reference, "role": "stem",
        })
    blocks.extend(_text_and_table_blocks(text[cursor:], id_prefix, counters))
    return blocks


def replace_question_prompt(question: dict[str, Any], prompt: str) -> None:
    """Replace editable prompt blocks without disturbing current image/options blocks.

    表格块和文字、公式一样由 prompt 文本派生，因此必须随新 prompt 一起重建；
    图片和选项来自 imageUrls/options 字段，与 prompt 无关，保留为 trailing 块。
    这里必须和 build_question_content_blocks() 使用同一个解析入口：两条路径各用
    一套解析规则，正是表格和图片残留反复复发的原因。
    """
    trailing_blocks = [
        block
        for block in question["contentBlocks"]
        if block.get("type") not in {"text", "math", "table"}
    ]
    question["prompt"] = prompt
    question["contentBlocks"] = [*_prompt_content_blocks(prompt, "stem"), *trailing_blocks]
    for source_order, block in enumerate(question["contentBlocks"]):
        block["sourceOrder"] = source_order


def _option_body(option: str, index: int) -> str:
    """去掉选项自带的 ``(A)``/``A.`` 前缀，只保留正文。"""
    return re.sub(rf"^(?:\({chr(65 + index)}\)|{chr(65 + index)}[.．:：、])\s*", "", str(option)).strip()


def _option_items(options: list[str], option_image_urls: list[str]) -> list[dict[str, Any]]:
    """构建结构化选项项；两条内容块路径共用，避免选项渲染规则出现两份实现。"""
    items: list[dict[str, Any]] = []
    for index, option in enumerate(options):
        item: dict[str, Any] = {
            "label": f"({chr(65 + index)})",
            "contentBlocks": rich_text_blocks(_option_body(option, index), f"option-{index + 1}"),
        }
        if index < len(option_image_urls):
            item["imageUrl"] = option_image_urls[index]
            item["assetId"] = Path(option_image_urls[index]).stem
        items.append(item)
    return items


def build_question_content_blocks(payload: dict[str, Any], source_block: str, source_images: list[str]) -> list[dict[str, Any]]:
    question = payload["question"]
    image_urls = [str(url) for url in question.get("imageUrls", [])]
    option_image_urls = [str(url) for url in question.get("optionImageUrls", [])]
    options = [str(option) for option in question.get("options", [])]
    by_name = {Path(url).name: url for url in image_urls}
    # 题干图就地放置：只有当记录到的位置能覆盖全部题干图、且顺序与 OCR 来源一致时才启用。
    # 任何不一致都回退到既有的“整批追加”行为——宁可版式不理想，也不要把图放到错误的
    # 位置或让下面的图片顺序门禁误报。
    stem_references = [
        reference for reference in source_images
        if by_name.get(Path(reference).name) and by_name[Path(reference).name] not in option_image_urls
    ]
    placements = [
        (offset, reference)
        for offset, reference in (question.get("stemImagePlacements") or [])
        if by_name.get(Path(reference).name)
    ]
    inline_placement = (
        bool(stem_references)
        and [Path(reference).name for _offset, reference in placements] == [Path(reference).name for reference in stem_references]
    )
    blocks = _prompt_content_blocks(
        str(question.get("prompt", "")), "stem",
        placements if inline_placement else None,
        by_name if inline_placement else None,
    )
    if inline_placement:
        option_items = _option_items(options, option_image_urls)
        if option_items:
            blocks.append({"id": "options", "type": "options", "items": option_items})
        for source_order, block in enumerate(blocks):
            block["sourceOrder"] = source_order
        return blocks
    image_blocks: list[tuple[int, dict[str, Any]]] = []
    for index, reference in enumerate(source_images):
        url = by_name.get(Path(reference).name)
        if not url or url in option_image_urls:
            continue
        image_blocks.append((source_block.find(reference), {
            "id": f"stem-image-{index + 1}", "type": "image", "url": url,
            "assetId": Path(url).stem, "sourceReference": reference, "role": "stem",
        }))
    option_items = _option_items(options, option_image_urls)
    options_block = {"id": "options", "type": "options", "items": option_items} if option_items else None
    source_choice_matches = list(CHOICE_MARKER_PATTERN.finditer(source_block))
    if not source_choice_matches and len(source_images) in {4, 5}:
        source_choice_matches = list(STANDALONE_CHOICE_MARKER_PATTERN.finditer(source_block))
    first_option_position = source_choice_matches[0].start() if source_choice_matches else -1
    blocks.extend(block for position, block in image_blocks if first_option_position < 0 or position < first_option_position)
    if options_block:
        blocks.append(options_block)
    blocks.extend(block for position, block in image_blocks if first_option_position >= 0 and position >= first_option_position)
    for source_order, block in enumerate(blocks):
        block["sourceOrder"] = source_order
    return blocks


def validate_question_payload(payload: dict[str, Any], source_block: str, source_images: list[str]) -> dict[str, Any]:
    """执行确定性发布检查，返回错误证据但不直接抛异常。"""
    question = payload["question"]
    errors: list[str] = []
    warnings: list[str] = []
    expected_images = [Path(reference).name for reference in source_images]
    actual_images = [Path(str(url)).name for url in question.get("imageUrls", [])]
    if actual_images != expected_images:
        errors.append(f"图片归属不一致：OCR={expected_images}，结构化结果={actual_images}")
    if len(actual_images) != len(set(actual_images)):
        errors.append("同一道题包含重复图片")
    option_images = [Path(str(url)).name for url in question.get("optionImageUrls", [])]
    expected_option_images = actual_images[1:] if len(actual_images) == 5 else actual_images
    if option_images and (len(option_images) != 4 or option_images != expected_option_images):
        errors.append("图片选择题必须按 A、B、C、D 绑定四张当前题图片")
    options = [str(item).strip() for item in question.get("options", [])]
    prompt = str(question.get("prompt", ""))
    # 题源边界优先于模型结构化结果：考试说明即使被模型包装成合法 JSON，也不能
    # 作为学生可作答题发布。把它放在统一门禁而不是提示词里，避免不同模型绕过边界。
    if is_likely_exam_instruction(source_block):
        evidence = re.sub(r"\s+", " ", source_block).strip()[:120]
        errors.append(f"题源疑似考试说明/作答要求，禁止生成题目：{evidence}")
    source_number = QUESTION_START_PATTERN.match(source_block)
    if source_number and str(question.get("questionNumber", "")).strip() not in {"", source_number.group("number")}:
        errors.append(
            f"题号来源不一致：OCR={source_number.group('number')}，结构化结果={question.get('questionNumber')}"
        )
    leaked_answer = ANSWER_LEAK_PATTERN.search(prompt)
    if leaked_answer:
        evidence = prompt[leaked_answer.start():leaked_answer.start() + 36].replace("\n", " ")
        errors.append(f"题干混入答案/解析证据：{evidence}")
    source_labels = _image_choice_labels(source_block, len(source_images))
    if all(label in source_labels for label in ("A", "B", "C", "D")) and len(options) != 4:
        errors.append(f"原题包含 A-D，但结构化选项数为 {len(options)}")
    for index, option in enumerate(options):
        label_only = bool(re.fullmatch(r"(?:\([A-H]\)|[A-H][.:：、]?)", option))
        if (not option or label_only) and index >= len(option_images):
            errors.append(f"选项 {chr(65 + index)} 缺少内容或图片")
    prompt_choice_labels = [
        match.group(1) or match.group(2)
        for match in CHOICE_MARKER_PATTERN.finditer(str(question.get("prompt", "")))
    ]
    # 比对“题干里成行列出的选项正文”与结构化选项，而不是比对标签序列。
    # 标签序列不可靠：题干正文里的 “点A、B分别表示…” 也会被 CHOICE_MARKER_PATTERN
    # 匹配成一个 A，于是三选项题的标签序列变成 ["A","A","B","C"]，旧的
    # `[:4] == ["A","B","C","D"]` 比对永远落空，题干和选项按钮会重复显示同样内容。
    # 要求逐项全部命中，宁可漏报也不误伤正常题目。
    if len(options) >= 2:
        prompt_option_lines = {
            (match.group(1) or match.group(2)): match.group(3).strip()
            for match in PROMPT_OPTION_LINE_PATTERN.finditer(prompt)
        }
        duplicated = all(
            prompt_option_lines.get(chr(65 + index), "") == _option_body(option, index)
            for index, option in enumerate(options)
        )
        if duplicated:
            errors.append("题干中重复包含结构化选项")
    content_blocks = question.get("contentBlocks", [])
    if not content_blocks:
        errors.append("缺少 contentBlocks")
    # 安全网：正常化之后不应再有未结构化的图片引用残留在任何文本片段里。这条检查
    # 本身不修复问题，只是让清理逻辑的漏洞（例如新出现的题型没有覆盖到）在发布前
    # 就变成一个确定性错误，而不是被学生在页面上看到源码字符。
    text_fragments = [prompt, *options]
    for block in content_blocks:
        if block.get("type") == "text":
            text_fragments.append(str(block.get("text", "")))
        elif block.get("type") == "options":
            for item in block.get("items", []):
                for inner in item.get("contentBlocks", []):
                    if inner.get("type") == "text":
                        text_fragments.append(str(inner.get("text", "")))
    for fragment in text_fragments:
        residue = MARKDOWN_IMAGE_PATTERN.search(fragment) or BARE_IMAGE_REFERENCE_PATTERN.search(fragment)
        if residue:
            evidence = residue.group(0)[:80]
            errors.append(f"题干残留未结构化的图片引用：{evidence}")
    block_images = [Path(str(block.get("url", ""))).name for block in content_blocks if block.get("type") == "image"]
    block_option_images = [Path(str(item.get("imageUrl", ""))).name for block in content_blocks if block.get("type") == "options" for item in block.get("items", []) if item.get("imageUrl")]
    if block_images + block_option_images != actual_images:
        errors.append("contentBlocks 中的图片顺序与题目图片不一致")
    # 安全网：表格必须已被解析成结构化 table 块。`prompt` 字段本身仍保留原始 OCR
    # 文本（供模型上下文和来源审计使用），不会被渲染给学生，所以这里只检查真正
    # 渲染给学生的 contentBlocks text 片段；任何原始 <table>/<tr>/<td> 标签残留在
    # 里面，说明表格解析逻辑存在遗漏，此时应显式进入人工复核，而不是把 HTML 标签
    # 当文字展示给学生。
    table_residue_fragments: list[str] = []
    for block in content_blocks:
        if block.get("type") == "text":
            table_residue_fragments.append(str(block.get("text", "")))
        elif block.get("type") == "options":
            for item in block.get("items", []):
                for inner in item.get("contentBlocks", []):
                    if inner.get("type") == "text":
                        table_residue_fragments.append(str(inner.get("text", "")))
    for fragment in table_residue_fragments:
        residue = TABLE_TAG_PATTERN.search(fragment)
        if residue:
            start = max(0, residue.start() - 10)
            evidence = fragment[start:residue.start() + 40].replace("\n", " ")
            errors.append(f"题干残留未结构化的表格标记：{evidence}")
    math_blocks = [str(block.get("latex", "")) for block in content_blocks if block.get("type") == "math"] + [str(inner.get("latex", "")) for block in content_blocks if block.get("type") == "options" for item in block.get("items", []) for inner in item.get("contentBlocks", []) if inner.get("type") == "math"]
    for index, latex in enumerate(math_blocks, start=1):
        evidence = latex[:80].replace("\n", " ")
        if not latex:
            errors.append(f"第 {index} 个公式为空")
            continue
        if formula_anomaly_score(f"${latex}$"):
            errors.append(f"第 {index} 个公式仍含 OCR/控制字符异常：{evidence}")
        if re.search(r"\\(?:textbackslash|textdegree|textbar)\b", latex, flags=re.IGNORECASE):
            errors.append(f"第 {index} 个公式包含不受支持的单位或转义命令：{evidence}")
        if latex.count("{") != latex.count("}"):
            errors.append(f"第 {index} 个公式花括号不平衡：左={latex.count('{')}，右={latex.count('}')}，{evidence}")
        begins = re.findall(r"\\begin\{([^}]+)\}", latex)
        ends = re.findall(r"\\end\{([^}]+)\}", latex)
        if begins != ends or re.search(r"\\(?:begin|end)(?!\{)", latex):
            errors.append(f"第 {index} 个公式环境不完整：begin={begins}，end={ends}，{evidence}")
    if not prompt.strip():
        errors.append("题干为空")
    # 多小问题目当前没有结构化表示：questionType、correctAnswer 和 answerSpec 都是
    # 单答案模型。实测的坏样本是一道 short-answer 证明题带着 `answerType: numeric`、
    # `expected: "5/2"`——那其实只是第 (2) 问的答案。今天 short-answer 不走确定性
    # 判题，这条错配数据读不到所以无害；一旦有人把题型改成 numeric，答对第 (1) 问
    # 的学生就会被判错。在 subQuestions 结构落地前（见 docs/engineering-roadmap.md），
    # 先把这种沉默的错配变成显式错误。
    question_type = str(question.get("questionType", ""))
    answer_spec = question.get("answerSpec")
    if question_type not in DETERMINISTIC_ANSWER_TYPES and isinstance(answer_spec, dict) and answer_spec.get("expected"):
        errors.append(
            f"题型 {question_type} 不参与确定性判题，却携带 answerSpec："
            f"{str(answer_spec.get('expected'))[:40]}"
        )
    if SUB_QUESTION_PATTERN.search(prompt) and len(SUB_QUESTION_PATTERN.findall(prompt)) >= 2:
        warnings.append("题干包含多个小问，当前数据模型只保存一个答案；发布前请人工确认判题范围")
    prompt_has_percent = bool(re.search(r"(?:%|\\%)", prompt))
    temperature_options = options and all(
        "℃" in option or bool(re.search(r"\\circ\}?\\mathrm\{C\}", option))
        for option in options
    )
    if "温度" in prompt and prompt_has_percent and temperature_options:
        errors.append("题干使用百分比，但选项均为温度值，单位语义冲突")
    if not source_block.strip():
        warnings.append("缺少 OCR 原始题块，无法进行来源覆盖校验")
    return {"status": "ready" if not errors else "needs_review", "errors": errors, "warnings": warnings, "validatorVersion": "p0-v5", "validatedAt": time.time()}


def apply_question_quality_gate(payload: dict[str, Any], source_block: str, source_images: list[str]) -> dict[str, Any]:
    """重建内容块、附加来源指纹，并把质量状态写回题目。"""
    question = payload["question"]
    # 模型偶尔会把图片文件名原样写回 prompt（`![](images/xxx.jpg)` 或裸路径）。
    # 之前这条清理只发生在 A-D 图片选择题分支里，普通题目不受影响，脏文本会
    # 一路流进 contentBlocks 并原样展示给学生。这里提升到每道题都会经过的
    # 通用路径，且必须在 build_question_content_blocks() 之前执行。
    cleaned_prompt, stem_image_placements = extract_image_placements(str(question.get("prompt", "")))
    question["prompt"] = cleaned_prompt
    # 位置只用于本次内容块构建，不进入对外契约，避免下游把它当成稳定字段。
    question["stemImagePlacements"] = stem_image_placements
    # 模型偶尔会把题目说明或下一题的选项也当成 E 项。对于 OCR 已明确是
    # A-D 的题，先做一个可逆的展示修复：只保留来源支持的四项，同时记录门禁错误，
    # 这样内容工作台不会继续展示一个可作答的假选项，发布边界也不会被误放行。
    source_labels = _image_choice_labels(source_block, len(source_images))
    options = question.get("options") if isinstance(question.get("options"), list) else []
    if len(options) > 4 and (
        all(label in source_labels for label in ("A", "B", "C", "D"))
        or len(source_images) == 5
    ):
        question["options"] = list(options[:4])
        option_images = question.get("optionImageUrls")
        if isinstance(option_images, list):
            question["optionImageUrls"] = list(option_images[:4])
        question["qualityRepairNotes"] = [
            *(question.get("qualityRepairNotes") or []),
            "来源仅支持 A-D，已暂时隐藏结构化结果中的多余选项；请重新生成或人工确认。",
        ]
    question["contentBlocks"] = build_question_content_blocks(payload, source_block, source_images)
    question.pop("stemImagePlacements", None)
    question["sourceEvidence"] = {"questionNumber": question.get("questionNumber", ""), "sourceHash": hashlib.sha256(source_block.encode("utf-8")).hexdigest(), "imageReferences": list(source_images)}
    quality = validate_question_payload(payload, source_block, source_images)
    if question.get("qualityRepairNotes"):
        quality["errors"].extend(str(note) for note in question["qualityRepairNotes"])
        quality["status"] = "needs_review"
    payload["quality"] = quality
    question["publicationStatus"] = quality["status"]
    if quality["errors"] and payload.get("review"):
        payload["review"]["status"] = "needs_review"
        payload["review"]["needsHumanReview"] = True
        payload["review"].setdefault("text", {}).setdefault("issues", []).extend(f"结构校验：{error}" for error in quality["errors"])
    return quality
