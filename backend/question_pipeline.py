"""题目规范化、内容块构建和发布质量门禁的纯函数集合。

这里不调用模型、不访问数据库。模型输出和 OCR 原文进入本模块后，会被转换为前端稳定契约，
并用可重复的规则判断是否允许发布。把规则保持为纯函数，便于用历史坏题做回归测试。
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

from review_runtime import formula_anomaly_score, normalize_ocr_question

QUESTION_START_PATTERN = re.compile(r"(?m)^\s*(?P<number>\d{1,3})[.．、]\s*")
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
MATH_FRAGMENT_PATTERN = re.compile(r"(\$\$[\s\S]+?\$\$|\$[^$]+?\$)")
CHOICE_MARKER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:\(([A-D])\)|([A-D])[.．:：、])\s*"
)


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
    """仅在 OCR 明确给出 A-D 四图时，把图片提升为结构化选项。"""
    labels = re.findall(r"(?m)^\s*\(([A-D])\)\s*$", source_block)
    if len(source_images) != 4 or labels[:4] != ["A", "B", "C", "D"]:
        return
    question = payload["question"]
    image_urls = list(question.get("imageUrls", []))[:4]
    if len(image_urls) != 4:
        return
    question["optionImageUrls"] = image_urls
    question["options"] = ["(A)", "(B)", "(C)", "(D)"]
    prompt = str(question.get("prompt", ""))
    prompt = re.sub(r"(?m)^\s*\([A-D]\)\s*$", "", prompt)
    question["prompt"] = re.sub(r"\n{3,}", "\n\n", prompt).strip()


def clean_question_stem(number: str, block: str) -> str:
    stem = QUESTION_START_PATTERN.sub("", block, count=1)
    stem = MARKDOWN_IMAGE_PATTERN.sub("", stem)
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
    replacements = {"\x08egin": r"\begin", "\text": r"\text", "\times": r"\times", "\x0crac": r"\frac"}
    for broken, corrected in replacements.items():
        value = value.replace(broken, corrected)
    # 审核模型有时会输出“反斜杠”这个字面命令，而不是目标 LaTeX。这里只修复已知的
    # 百分号和摄氏度形式，避免宽泛正则误改题目中的真实数学表达式。
    value = re.sub(
        r"\\textbackslash\s*\\text\s*\{\s*%\s*\}",
        r"\\%",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"\\textbackslash\s*%", r"\\%", value, flags=re.IGNORECASE)
    value = re.sub(
        r"\\(?:textdegree|textbar)\s*C\b",
        r"^{\\circ}\\mathrm{C}",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(r"(?<![A-Za-z])(?:°\s*C|℃)", "℃", value)
    value = re.sub(r"\\begin\s*\{?\s*array\s*\}?\s*\{?\s*([clr])\s*\}?", r"\\begin{array}{\1}", value)
    return re.sub(r"\\end\s*\{?\s*array\s*\}?", r"\\end{array}", value)


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


def build_question_content_blocks(payload: dict[str, Any], source_block: str, source_images: list[str]) -> list[dict[str, Any]]:
    question = payload["question"]
    blocks = rich_text_blocks(str(question.get("prompt", "")), "stem")
    image_urls = [str(url) for url in question.get("imageUrls", [])]
    option_image_urls = [str(url) for url in question.get("optionImageUrls", [])]
    options = [str(option) for option in question.get("options", [])]
    by_name = {Path(url).name: url for url in image_urls}
    image_blocks: list[tuple[int, dict[str, Any]]] = []
    for index, reference in enumerate(source_images):
        url = by_name.get(Path(reference).name)
        if not url or url in option_image_urls:
            continue
        image_blocks.append((source_block.find(reference), {
            "id": f"stem-image-{index + 1}", "type": "image", "url": url,
            "assetId": Path(url).stem, "sourceReference": reference, "role": "stem",
        }))
    option_items: list[dict[str, Any]] = []
    for index, option in enumerate(options):
        label = f"({chr(65 + index)})"
        clean_option = re.sub(rf"^(?:\({chr(65 + index)}\)|{chr(65 + index)}[.:：、])\s*", "", option).strip()
        item: dict[str, Any] = {"label": label, "contentBlocks": rich_text_blocks(clean_option, f"option-{index + 1}")}
        if index < len(option_image_urls):
            item["imageUrl"] = option_image_urls[index]
            item["assetId"] = Path(option_image_urls[index]).stem
        option_items.append(item)
    options_block = {"id": "options", "type": "options", "items": option_items} if option_items else None
    source_choice_matches = list(CHOICE_MARKER_PATTERN.finditer(source_block))
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
    if option_images and (len(option_images) != 4 or option_images != actual_images):
        errors.append("图片选择题必须按 A、B、C、D 绑定四张当前题图片")
    options = [str(item).strip() for item in question.get("options", [])]
    source_labels = [
        match.group(1) or match.group(2)
        for match in CHOICE_MARKER_PATTERN.finditer(source_block)
    ]
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
    if options and prompt_choice_labels[:4] == ["A", "B", "C", "D"]:
        errors.append("题干中重复包含结构化选项")
    content_blocks = question.get("contentBlocks", [])
    if not content_blocks:
        errors.append("缺少 contentBlocks")
    block_images = [Path(str(block.get("url", ""))).name for block in content_blocks if block.get("type") == "image"]
    block_option_images = [Path(str(item.get("imageUrl", ""))).name for block in content_blocks if block.get("type") == "options" for item in block.get("items", []) if item.get("imageUrl")]
    if block_images + block_option_images != actual_images:
        errors.append("contentBlocks 中的图片顺序与题目图片不一致")
    math_blocks = [str(block.get("latex", "")) for block in content_blocks if block.get("type") == "math"] + [str(inner.get("latex", "")) for block in content_blocks if block.get("type") == "options" for item in block.get("items", []) for inner in item.get("contentBlocks", []) if inner.get("type") == "math"]
    for index, latex in enumerate(math_blocks, start=1):
        if not latex:
            errors.append(f"第 {index} 个公式为空")
            continue
        if formula_anomaly_score(f"${latex}$"):
            errors.append(f"第 {index} 个公式仍含 OCR/控制字符异常")
        if re.search(r"\\(?:textbackslash|textdegree|textbar)\b", latex, flags=re.IGNORECASE):
            errors.append(f"第 {index} 个公式包含不受支持的单位或转义命令")
        if latex.count("{") != latex.count("}"):
            errors.append(f"第 {index} 个公式花括号不平衡")
        begins = re.findall(r"\\begin\{([^}]+)\}", latex)
        ends = re.findall(r"\\end\{([^}]+)\}", latex)
        if begins != ends or re.search(r"\\(?:begin|end)(?!\{)", latex):
            errors.append(f"第 {index} 个公式环境不完整")
    if not str(question.get("prompt", "")).strip():
        errors.append("题干为空")
    prompt = str(question.get("prompt", ""))
    prompt_has_percent = bool(re.search(r"(?:%|\\%)", prompt))
    temperature_options = options and all(
        "℃" in option or bool(re.search(r"\\circ\}?\\mathrm\{C\}", option))
        for option in options
    )
    if "温度" in prompt and prompt_has_percent and temperature_options:
        errors.append("题干使用百分比，但选项均为温度值，单位语义冲突")
    if not source_block.strip():
        warnings.append("缺少 OCR 原始题块，无法进行来源覆盖校验")
    return {"status": "ready" if not errors else "needs_review", "errors": errors, "warnings": warnings, "validatorVersion": "p0-v2", "validatedAt": time.time()}


def apply_question_quality_gate(payload: dict[str, Any], source_block: str, source_images: list[str]) -> dict[str, Any]:
    """重建内容块、附加来源指纹，并把质量状态写回题目。"""
    question = payload["question"]
    question["contentBlocks"] = build_question_content_blocks(payload, source_block, source_images)
    question["sourceEvidence"] = {"questionNumber": question.get("questionNumber", ""), "sourceHash": hashlib.sha256(source_block.encode("utf-8")).hexdigest(), "imageReferences": list(source_images)}
    quality = validate_question_payload(payload, source_block, source_images)
    payload["quality"] = quality
    question["publicationStatus"] = quality["status"]
    if quality["errors"] and payload.get("review"):
        payload["review"]["status"] = "needs_review"
        payload["review"]["needsHumanReview"] = True
        payload["review"].setdefault("text", {}).setdefault("issues", []).extend(f"结构校验：{error}" for error in quality["errors"])
    return quality
