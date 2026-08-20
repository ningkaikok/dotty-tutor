"""Pure helpers for turning OCR Markdown into bounded question sources."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ocr_pipeline import PAGE_MARKER, has_visual_hint

# 只把版心左侧的题号视为新题。小问 ``(1)``、章节编号 ``1.1`` 都可能以数字
# 开头，但前者属于当前题、后者通常不是可独立出题的题号，不能用宽泛的数字正则切开。
QUESTION_START_PATTERN = re.compile(
    r"(?m)^\s*(?:[【\[]\s*)?(?:第\s*)?(?P<number>\d{1,3})(?:(?:\s*题\s*(?:[:：]|\s))|[.．、]|[】\]])\s*"
)
# 试卷通常在真正题目之前包含“注意事项”或考试信息。这里只把明确的题型章节
# 作为题目区起点，避免把注意事项中的“1.”、“2.”误判成题号。OCR 可能在汉字
# 之间插入空格或换行，因此章节标题的每个字之间都允许空白；这是版面 OCR 中
# 比“精确匹配整行标题”更稳定的做法。
QUESTION_SECTION_PATTERN = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:(?:第\s*[一二三四五六七八九十百\d]+\s*(?:大题|部分|节))|"
    r"(?:[一二三四五六七八九十百\d]+\s*[、.．]))\s*"
    r"(?:选\s*择\s*题|填\s*空\s*题|判\s*断\s*题|解\s*答\s*题|计\s*算\s*题|"
    r"应\s*用\s*题|作\s*图\s*题|证\s*明\s*题|实\s*验\s*题|综\s*合\s*题|"
    r"单\s*项\s*选\s*择\s*题|多\s*项\s*选\s*择\s*题|非\s*选\s*择\s*题)"
)
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
MAX_QUESTIONS_PER_BATCH = 5
MAX_FULL_PAPER_QUESTIONS_PER_BATCH = 20
# 题目切分规则会影响送给模型的题源，因此必须像 OCR Provider 一样有版本号；
# 不同规则版本的产物不能静默混用，重新 OCR/生成时会写入新的版本证据。
QUESTION_SEGMENTATION_VERSION = "question-segmentation-v2"

ANSWER_SECTION_PATTERN = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:参考答案与解析|答案与解析|参考答案|答案|解答|解析)\s*$"
)
INLINE_ANSWER_PATTERN = re.compile(
    r"(?im)^\s*(?:【\s*)?(?:参考)?(?:答案|解析)\s*(?:】\s*)?"
)

# 当题型章节标题因分页、OCR 断行或版式丢失而无法匹配时，使用一组高精度的
# 文档语义信号识别考试前置说明。这里不把“请”或“选择”这种普通词作为信号，
# 避免误伤真正的题干；只有和答题卡、准考证、考试时间等强上下文一起出现时才
# 过滤。该规则对应业界常见的“文档区域分类 + 题块白名单”两阶段切分。
EXAM_INSTRUCTION_MARKERS = (
    r"注意事项",
    r"本试卷",
    r"考试时间",
    r"满分\s*\d+",
    r"准考证",
    r"条形码",
    r"答题卡",
    r"监考",
    r"签字笔",
    r"涂黑",
    r"填涂",
    r"作图必须",
    r"答题.{0,12}(?:无效|位置)",
    r"姓名.{0,12}(?:考试|准考证)",
)
QUESTION_EVIDENCE_MARKERS = (
    r"下列",
    r"计算",
    r"求(?:出|得|值|证)?",
    r"解(?:方程|不等式|集)?",
    r"判断",
    r"等于",
    r"的是",
    r"已知",
    r"若",
    r"如图",
    r"证明",
    r"化简",
    r"取值",
    r"结果",
    r"填空",
    r"方程",
    r"不等式",
    r"平均数|中位数|概率",
)


def _has_any_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(re.search(marker, text, flags=re.IGNORECASE) for marker in markers)


def is_likely_exam_instruction(text: str) -> bool:
    """识别不可直接生成题目的考试说明/作答要求块。

    这是来源级安全边界，不是题型判断器：说明块即使被模型“补全”为合法 JSON，
    也必须在发布前被拦截。题干同时包含明确数学任务时不判为说明，避免把“请计算”
    之类的正常题目误杀。
    """
    source = str(text or "").strip()
    return bool(source) and _has_any_marker(source, EXAM_INSTRUCTION_MARKERS) and not _has_any_marker(
        source, QUESTION_EVIDENCE_MARKERS
    )


def _trim_leading_exam_instructions(source: str) -> str:
    """在章节标题缺失时，跳过开头的编号说明并保留第一个题块。

    题号可能重复（说明中的 ``1.`` 后面再次出现真正的第 1 题），所以不能按题号去重；
    必须先对每个候选块做语义分类，再从第一个非说明块开始切分。
    """
    matches = list(QUESTION_START_PATTERN.finditer(source))
    if not matches:
        return source
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        candidate = source[match.start():end]
        if not is_likely_exam_instruction(candidate):
            return source[match.start():]
    # 全部都是说明时返回空白；调用方会把它标记为无可发布题源，而不是交给模型猜题。
    return ""


def _question_area(source: str) -> str:
    """Return the actual exercise section when an exam heading is present.

    OCR often preserves numbered instructions before the first section.  Slicing at a
    recognizable heading is safer than trying to blacklist every possible instruction
    sentence, and leaves non-exam textbook snippets unchanged.
    """
    area = ANSWER_SECTION_PATTERN.split(source, maxsplit=1)[0]
    heading = QUESTION_SECTION_PATTERN.search(area)
    if heading:
        return area[heading.start():]
    # 章节标题可能被 OCR 拆散或落在上一页，使用语义分类作为保底；普通教材没有
    # 考试说明时保持原行为，不改变已有的无章节题号切分。
    return _trim_leading_exam_instructions(area) if _has_any_marker(area, EXAM_INSTRUCTION_MARKERS) else area


def safe_text(value: Any, fallback: str, limit: int = 600) -> str:
    """Normalize untrusted model output and apply a hard storage limit."""
    text = str(value or "").strip()
    return (text or fallback)[:limit]


def safe_string_list(value: Any, fallback: list[str], limit: int = 8) -> list[str]:
    """Normalize a model-produced list without accepting nested structures."""
    if not isinstance(value, list):
        return fallback
    items = [safe_text(item, "", 160) for item in value]
    items = [item for item in items if item]
    return items[:limit] or fallback


def split_question_sources(source: str) -> list[tuple[str, str, list[str]]]:
    """Split OCR Markdown into bounded questions without answer-key leakage.

    OCR 页之间可能重复打印同一个题号；相邻的同号块按续题合并，既能保住跨页小问，也不把
    下一题吸进来。图片始终随其所在题块保留，顺序与 OCR Markdown 一致。
    """
    question_area = _question_area(source)
    matches = list(QUESTION_START_PATTERN.finditer(question_area))
    page_markers = list(PAGE_MARKER.finditer(question_area))
    blocks: list[tuple[str, str, list[str]]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(question_area)
        block = INLINE_ANSWER_PATTERN.split(question_area[match.start():end], maxsplit=1)[0].strip()
        if len(block) < 4:
            continue
        images = MARKDOWN_IMAGE_PATTERN.findall(block)
        # 矢量 PDF 图形不会出现在 Markdown 图片列表中。OCR 编排器会在对应页段
        # 注入页面渲染图。题目跨页时仍以题号所在页为准；否则会把下一页的整页图
        # 错绑给上一页末尾的题目。
        if not images and has_visual_hint(block) and page_markers:
            page_marker = next(
                (marker for marker in reversed(page_markers) if marker.start() < match.start()),
                None,
            )
            if page_marker:
                section_end = next(
                    (marker.start() for marker in page_markers if marker.start() > page_marker.start()),
                    len(question_area),
                )
                # A page can contain several unrelated figures.  Guessing all of them
                # creates a valid-looking but wrong question.  Only infer a rendered
                # page image when it is the sole image candidate on that page; otherwise
                # the quality gate quarantines the question for an explicit repair.
                page_images = MARKDOWN_IMAGE_PATTERN.findall(question_area[page_marker.end():section_end])
                images = page_images if len(page_images) == 1 else []
        number = match.group("number")
        if blocks and blocks[-1][0] == number:
            # 同号重复通常来自跨页页眉或 OCR 将续题重新识别为题首。合并而非新建题，
            # 并按出现顺序去重图片，避免同一资源被审核成两次归属。
            previous_number, previous_block, previous_images = blocks[-1]
            merged_images = list(dict.fromkeys([*previous_images, *images]))
            blocks[-1] = (previous_number, f"{previous_block}\n{block}", merged_images)
        else:
            blocks.append((number, block, images))
    return blocks


def limited_question_sources(
    source: str,
    limit: int = MAX_QUESTIONS_PER_BATCH,
) -> list[tuple[str, str, list[str]]]:
    """Bound one batch's model cost while retaining a no-number fallback.

    快速预览保持 5 题；显式整卷任务可以提高到 20 题/批，但仍由代码上限约束，
    避免错误 OCR 把页眉、说明等碎片无限送入模型。
    """
    safe_limit = max(1, min(int(limit), MAX_FULL_PAPER_QUESTIONS_PER_BATCH))
    blocks = split_question_sources(source)[:safe_limit]
    return blocks or [("", source, MARKDOWN_IMAGE_PATTERN.findall(source))]


def select_complete_question_source(source: str) -> tuple[str, str, list[str]]:
    """Select one intact question, preferring an illustrated example."""
    blocks = split_question_sources(source)
    if not blocks:
        return "", source.strip(), MARKDOWN_IMAGE_PATTERN.findall(source)
    return next((block for block in blocks if block[2]), blocks[0])


def question_image_paths(asset_dir: Path, references: list[str]) -> list[Path]:
    """Resolve only safe image basenames referenced by one question."""
    available = {
        path.name: path
        for path in asset_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    } if asset_dir.is_dir() else {}
    return [available[Path(reference).name] for reference in references if Path(reference).name in available]


def question_key(batch_id: str, number: str, index: int) -> str:
    """Build a stable key from a batch and OCR-visible question number."""
    normalized = re.sub(r"[^0-9A-Za-z_-]+", "", number) or f"index-{index + 1:03d}"
    return f"{batch_id}-q-{normalized}"
