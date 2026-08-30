"""把 OCR Markdown 有界切分为题目源的纯函数模块。

"有界切分"是本模块的核心约束：只有能被证明是新题号的数字才允许开启新题。
小问 ``(1)`` 属于当前题、章节编号 ``1.1`` 不是可独立出题的题号、句子中间
意外断行产生的行首数字更不是——宽泛的数字正则会把一道题切成三道。

切分规则版本（``QUESTION_SEGMENTATION_VERSION``）参与运行审计：规则一旦变更，
历史生成结果仍可通过版本号追溯是用哪套规则切出来的，不会被静默覆盖。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ocr_pipeline import PAGE_MARKER, has_visual_hint

# 只把版心左侧的题号视为新题。小问 ``(1)``、章节编号 ``1.1`` 都可能以数字
# 开头，但前者属于当前题、后者通常不是可独立出题的题号，不能用宽泛的数字正则切开。
# 分隔符单独命名（``sep``）：续举例编号误判（roadmap 子问题 A）只发生在顿号/逗号
# 分隔的候选上，修复逻辑需要区分对待，见 ``_is_enumeration_continuation``。
QUESTION_START_PATTERN = re.compile(
    r"(?m)^\s*(?:[【\[]\s*)?(?:第\s*)?(?P<number>\d{1,3})"
    r"(?P<sep>(?:\s*题\s*(?:[:：]|\s))|[.．、，,]|[】\]])\s*"
)
# 枚举标记：一个顿号/逗号候选题号只有在前文以这些字符收尾时才被判为续行举例。
# 不采用更宽的"非句末标点即续行"方案：真实语料里既有以公式（$…$）或数字收尾的
# 合法题目，也存在合法的"、"风格题号（见 test_stops_a_question_at_inline_answer_…
# 的 "7、/8、" 用例），宽规则会把它们错并进上一题。
ENUMERATION_MARKS = frozenset("、，,")
# 试卷通常在真正题目之前包含“注意事项”或考试信息。这里只把明确的题型章节
# 作为题目区起点，避免把注意事项中的“1.”、“2.”误判成题号。OCR 可能在汉字
# 之间插入空格或换行，因此章节标题的每个字之间都允许空白；这是版面 OCR 中
# 比“精确匹配整行标题”更稳定的做法。
QUESTION_SECTION_PATTERN = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:(?:第\s*[一二三四五六七八九十百\d]+\s*(?:大题|部分|节))|"
    r"(?:[一二三四五六七八九十百\d]+\s*[、，,.．]))\s*"
    r"(?:选\s*择\s*题|填\s*空\s*题|判\s*断\s*题|解\s*答\s*题|计\s*算\s*题|"
    r"应\s*用\s*题|作\s*图\s*题|证\s*明\s*题|实\s*验\s*题|综\s*合\s*题|"
    r"单\s*项\s*选\s*择\s*题|多\s*项\s*选\s*择\s*题|非\s*选\s*择\s*题)"
)
# Keep malformed OCR input linear-time; these spans never need to backtrack.
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*+\]\(([^)]++)\)")
# 只匹配"图片引用后紧跟第N题图/第N题"这种明确格式，中间只允许空白，不允许跨越其他
# 内容——这是比文本位置更可靠的归属信号，但格式必须足够窄才不会误伤正常题干。
IMAGE_CAPTION_PATTERN = re.compile(
    r"!\[[^\]]*\]\((?P<path>[^)]+)\)\s*第\s*(?P<number>\d{1,3})\s*题(?:图)?"
)
# 用于从 content_list.json 的 image_caption/chart_caption/table_caption 字段（纯文本，
# 不含 Markdown 图片语法）里提取题号，格式和 IMAGE_CAPTION_PATTERN 里"第N题图/第N题"
# 的题号部分一致，只是不需要再匹配前面的 ``![]()``。
STRUCTURED_CAPTION_NUMBER_PATTERN = re.compile(r"第\s*(?P<number>\d{1,3})\s*题")
# MinerU 结构化 JSON 文件名，落盘方式见 infrastructure/runtime/ocr_runtime.py 的
# ``_persist_structured_output``；两个文件都是可选的，不存在时完全回退到纯正则逻辑。
STRUCTURED_CONTENT_LIST_FILENAME = "source.content_list.json"
STRUCTURED_MIDDLE_FILENAME = "source.middle.json"
# content_list.json 里这些类型是页眉/页脚/页码，middle.json 的 para_blocks 不包含
# 它们（被分到 discarded_blocks），按页对应块顺序时必须先排除，否则两份文件同一页
# 的块数量会对不上。已用本机真实解析的多本教材验证过这个对应关系。
STRUCTURED_DISCARDED_CONTENT_TYPES = frozenset({"page_number", "header", "footer"})
MAX_QUESTIONS_PER_BATCH = 5
MAX_FULL_PAPER_QUESTIONS_PER_BATCH = 20
# 题目切分规则会影响送给模型的题源，因此必须像 OCR Provider 一样有版本号；
# 不同规则版本的产物不能静默混用，重新 OCR/生成时会写入新的版本证据。
# v4：兼容 OCR 常见的全角逗号题号（如“1，”“17，”），并继续隔离顿号/逗号枚举续行。
QUESTION_SEGMENTATION_VERSION = "question-segmentation-v4"

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
    r"答卷",
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
    r"本小题",
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


def _load_structured_content_list(asset_dir: Path | None) -> list[Any] | None:
    """读取 PR A 落盘的 ``source.content_list.json``。

    文件不存在、不是这次 OCR 产出（比如 pypdf 回退、手动粘贴文本）或解析失败时返回
    ``None``；调用方据此完全回退到现有的纯正则逻辑，不当作错误处理。
    """
    if asset_dir is None:
        return None
    path = Path(asset_dir) / STRUCTURED_CONTENT_LIST_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, list) else None


def _structured_caption_attributions(asset_dir: Path | None) -> dict[str, str]:
    """从 content_list.json 的 image/chart/table 块直接读取"第N题图/第N题"归属。

    这是比在扁平文本里用正则找"图片后紧跟第N题图"更可靠的信号：MinerU 已经把这类
    标注解析进 ``image_caption``/``chart_caption``/``table_caption`` 字段，不需要
    再从可能已经丢失换行、粘连的扁平文本里重新猜。没有对应结构化数据时返回空字典，
    调用方会完全回退到现有的正则逻辑。
    """
    content_list = _load_structured_content_list(asset_dir)
    if not content_list:
        return {}
    attributions: dict[str, str] = {}
    for item in content_list:
        if not isinstance(item, dict) or item.get("type") not in ("image", "chart", "table"):
            continue
        img_path = item.get("img_path")
        if not isinstance(img_path, str) or not img_path:
            continue
        captions = (
            item.get("image_caption") or item.get("chart_caption") or item.get("table_caption") or []
        )
        if not isinstance(captions, list) or not captions or not isinstance(captions[0], str):
            continue
        match = STRUCTURED_CAPTION_NUMBER_PATTERN.search(captions[0])
        if match:
            attributions[img_path] = match.group("number")
    return attributions


def _load_structured_middle_document(asset_dir: Path | None) -> dict[str, Any] | None:
    """读取 PR A 落盘的 ``source.middle.json``。语义和 ``_load_structured_content_list``
    一致：文件不存在或解析失败都返回 ``None``，调用方回退到现有的扁平文本路径。
    """
    if asset_dir is None:
        return None
    path = Path(asset_dir) / STRUCTURED_MIDDLE_FILENAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _rebuild_text_block_line_breaks(original_text: str, lines: list[Any]) -> str:
    """只在确认丢失的行界处插入换行，不改写任何已有字符。

    根因（已用真实 MinerU 输出核对过）：生成扁平文本时，个别行与行之间的换行会被
    吃掉——最典型的是题号紧跟在上一行末尾，中间没有任何分隔符，导致
    ``QUESTION_START_PATTERN`` 永远匹配不到行首的题号。``middle.json`` 的
    ``lines[]`` 保留了完整的行级顺序，可以用来定位这些丢失的边界。

    但真实数据显示，"这一行和上一行之间当前有没有分隔符"并不能单独作为"要不要插入
    换行"的判据：教材里大量普通换行只是 PDF 排版换行导致的词内/句中断行（例如把
    "两数"从中间断成"两"/"数"两行），本身在扁平文本里就没有分隔符，也不应该有——
    插入换行会把这类正常续行错误地打断。真正需要修的场景，签名很窄：这一行的开头
    看起来像一个新题号（能匹配现有的 ``QUESTION_START_PATTERN``），但却直接贴着
    上一行的结尾，没有任何换行。只在这个窄条件下插入换行，凡是行首不像题号的，
    一律不碰，保持原文本字符不变。

    只处理某一行的首个 span 是纯文本（``type == "text"``）的情况——公式片段
    （``inline_equation``）不会是题号，也没有必要处理；找不到该行文本在剩余原文本
    里的位置时（理论上不应发生，除非行内容本身在原文本里被截断过）跳过这一行，
    不强行插入。
    """
    cursor = 0
    is_first_line = True
    for line in lines:
        spans = line.get("spans") if isinstance(line, dict) else None
        if not isinstance(spans, list) or not spans:
            continue
        first_span = spans[0]
        if not isinstance(first_span, dict) or first_span.get("type") != "text":
            continue
        signature = str(first_span.get("content", "")).strip()
        if len(signature) < 2:
            continue
        idx = original_text.find(signature, cursor)
        if idx == -1:
            continue
        if (
            not is_first_line
            and idx > 0
            and original_text[idx - 1] != "\n"
            and QUESTION_START_PATTERN.match(signature)
        ):
            original_text = original_text[:idx] + "\n" + original_text[idx:]
            idx += 1
        cursor = idx + len(signature)
        is_first_line = False
    return original_text


def _reconstruct_question_line_breaks(source: str, asset_dir: Path | None) -> str:
    """用 middle.json 的行级坐标重建被扁平 Markdown 吃掉的换行。

    只在 content_list.json 和 middle.json 都存在、且能按页把两者的块一一对应时才
    生效；任何不确定的情况（文件缺失、解析失败、某一页块数量对不上）都让那一页
    （或整份文档）原样返回，完全回退到现有的扁平文本路径，不做任何猜测性替换。
    只处理 ``type == "text"`` 的块，图片/图表/表格块不受影响。
    """
    content_list = _load_structured_content_list(asset_dir)
    middle = _load_structured_middle_document(asset_dir)
    if not content_list or not middle:
        return source
    pdf_info = middle.get("pdf_info")
    if not isinstance(pdf_info, list):
        return source

    rebuilt_source = source
    for page_idx, page in enumerate(pdf_info):
        para_blocks = page.get("para_blocks") if isinstance(page, dict) else None
        if not isinstance(para_blocks, list):
            continue
        page_items = [
            item
            for item in content_list
            if isinstance(item, dict)
            and item.get("page_idx") == page_idx
            and item.get("type") not in STRUCTURED_DISCARDED_CONTENT_TYPES
        ]
        if len(page_items) != len(para_blocks):
            # 块数量对不上说明这一页的对应关系不可信（比如两份文件来自不同的 OCR
            # 运行），整页跳过，不做任何猜测替换。
            continue
        for content_item, para_block in zip(page_items, para_blocks):
            if content_item.get("type") != "text":
                continue
            original_text = content_item.get("text")
            if not isinstance(original_text, str) or not original_text.strip():
                continue
            lines = para_block.get("lines") if isinstance(para_block, dict) else None
            if not isinstance(lines, list) or len(lines) < 2:
                continue
            if rebuilt_source.count(original_text) != 1:
                # 这段文本在当前来源里找不到、或出现了不止一次（有歧义），不做替换。
                continue
            new_text = _rebuild_text_block_line_breaks(original_text, lines)
            if new_text != original_text:
                rebuilt_source = rebuilt_source.replace(original_text, new_text, 1)
    return rebuilt_source


def _is_enumeration_continuation(question_area: str, match: re.Match) -> bool:
    """判断一个候选题号是否其实是上一题句子中间的续举例编号（子问题 A）。

    真实坏样本："…把它们分别标上数字1、2、\\n\\n3、4. 随机抽取…"——OCR 在句子
    中间插入段落断点后，行首的 "3、" 被 ``QUESTION_START_PATTERN`` 判成新题，
    和真正的第 3 题共享 key。它的签名很窄：前文恰好停在枚举标记上。

    修复规则因此刻意收窄：只约束 **"、"分隔** 且前文最后非空白字符是枚举标记
    （、，,）的候选。已对本地全部真实教材 OCR 验证——合法题号从不使用"、"，
    唯一受影响的行就是坏样本本身；roadmap 曾考虑过更宽的"要求句末标点前置"
    方案，但真实语料里合法题目常以公式、数字等非句末标点收尾，宽规则误伤面大。
    """
    if match.group("sep") not in {"、", "，", ","}:
        return False
    preceding = question_area[: match.start()].rstrip()
    if not preceding:
        return False
    return preceding[-1] in ENUMERATION_MARKS


def split_question_sources(
    source: str,
    asset_dir: Path | None = None,
    attribution_audit: list[dict[str, Any]] | None = None,
) -> list[tuple[str, str, list[str]]]:
    """Split OCR Markdown into bounded questions without answer-key leakage.

    OCR 页之间可能重复打印同一个题号；相邻的同号块按续题合并，既能保住跨页小问，也不把
    下一题吸进来。图片始终随其所在题块保留，顺序与 OCR Markdown 一致。

    ``asset_dir`` 是这次 OCR 的资源目录（含 PR A 落盘的 ``source.content_list.json``/
    ``source.middle.json``），可选；传入时会优先用其中的结构化图注字段做图片归属，
    并用行级坐标重建被扁平化吃掉的换行（题号紧跟上一题末尾、没有换行导致题号识别
    失败），没有结构化数据时行为和之前完全一致。
    """
    source = _reconstruct_question_line_breaks(source, asset_dir)
    question_area = _question_area(source)
    matches = list(QUESTION_START_PATTERN.finditer(question_area))
    # 分页 OCR 可能把下一卷的注意事项带进当前批次；这些编号不是题目边界。
    # 只按候选块首行做语义过滤，避免说明后的真正题干被误删。
    filtered_matches: list[re.Match[str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(question_area)
        candidate = question_area[match.start():end]
        next_heading = QUESTION_SECTION_PATTERN.search(candidate, 1)
        if next_heading:
            candidate = candidate[:next_heading.start()]
        first_line = next((line.strip() for line in candidate.splitlines() if line.strip()), candidate)
        if not is_likely_exam_instruction(first_line):
            filtered_matches.append(match)
    matches = filtered_matches
    # 子问题 A 修复：先把"其实是续行枚举"的候选从边界集合里剔除，再按剩余边界
    # 切块。剔除的候选文本自然并入上一题（它的 end 由下一个被接受的边界决定）。
    boundaries = [
        match
        for index, match in enumerate(matches)
        if index == 0 or not _is_enumeration_continuation(question_area, match)
    ]
    page_markers = list(PAGE_MARKER.finditer(question_area))
    blocks: list[tuple[str, str, list[str]]] = []
    for index, match in enumerate(boundaries):
        end = boundaries[index + 1].start() if index + 1 < len(boundaries) else len(question_area)
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
    structured_captions = _structured_caption_attributions(asset_dir)
    # Apply the positional fallback per image. Explicit inline/caption evidence
    # is reconciled afterwards and wins for that image only; it must not suppress
    # bbox attribution for unrelated images in the same batch.
    blocks = _apply_bbox_image_attribution(blocks, asset_dir, attribution_audit)
    return _apply_caption_image_attribution(
        blocks,
        question_area,
        structured_captions=structured_captions,
    )


def _bbox(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        left, top, right, bottom = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _overlap_ratio(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    overlap = max(0.0, right - left) * max(0.0, bottom - top)
    area = max(1.0, min((first[2] - first[0]) * (first[3] - first[1]), (second[2] - second[0]) * (second[3] - second[1])))
    return overlap / area


def _apply_bbox_image_attribution(
    blocks: list[tuple[str, str, list[str]]],
    asset_dir: Path | None,
    audit: list[dict[str, Any]] | None,
) -> list[tuple[str, str, list[str]]]:
    """Assign uncaptioned images from page layout evidence, or explicitly abstain.

    The content-list text blocks provide question-start coordinates and the image
    blocks provide page/bbox coordinates.  A candidate must be on the same page,
    inside the vertical interval before the next question, and overlap the question
    column.  Low scores or near ties are left unassigned and recorded for review.
    """
    content_list = _load_structured_content_list(asset_dir)
    if not content_list:
        return blocks
    text_items = [item for item in content_list if isinstance(item, dict) and item.get("type") == "text"]
    image_items = [item for item in content_list if isinstance(item, dict) and item.get("type") in {"image", "chart"}]
    starts: list[dict[str, Any]] = []
    used_start_indexes: set[int] = set()
    for number, _block, _images in blocks:
        pattern = re.compile(rf"^\s*(?:第\s*)?{re.escape(number)}\s*[.．、，,)]")
        match_index = next((index for index, item in enumerate(text_items) if index not in used_start_indexes and pattern.search(str(item.get("text", "")))), None)
        if match_index is None:
            continue
        item = text_items[match_index]
        item_bbox = _bbox(item.get("bbox"))
        if item_bbox is None or not isinstance(item.get("page_idx"), int):
            continue
        used_start_indexes.add(match_index)
        starts.append({"number": number, "page": item["page_idx"], "bbox": item_bbox})
    if not starts:
        return blocks

    mutable = [list(images) for _number, _block, images in blocks]
    index_by_number = {number: index for index, (number, _block, _images) in enumerate(blocks)}
    ordered_starts = sorted(starts, key=lambda item: (item["page"], item["bbox"][1], item["bbox"][0]))
    assigned_basenames = {Path(path).name for _number, _block, images in blocks for path in images}
    seen_paths: set[str] = set()
    for image in image_items:
        path = image.get("img_path")
        image_bbox = _bbox(image.get("bbox"))
        page = image.get("page_idx")
        if not isinstance(path, str) or not path:
            continue
        if Path(path).name in assigned_basenames:
            continue
        if path in seen_paths:
            if audit is not None:
                audit.append({"image": path, "status": "needs_review", "reason": "duplicate image record"})
            continue
        seen_paths.add(path)
        if image_bbox is None or not isinstance(page, int):
            if audit is not None:
                audit.append({"image": path, "status": "needs_review", "reason": "invalid bbox or page"})
            continue
        image_center = ((image_bbox[0] + image_bbox[2]) / 2, (image_bbox[1] + image_bbox[3]) / 2)
        candidates: list[dict[str, Any]] = []
        page_starts = [item for item in ordered_starts if item["page"] == page]
        for start in page_starts:
            start_bbox = start["bbox"]
            start_center_x = (start_bbox[0] + start_bbox[2]) / 2
            start_width = start_bbox[2] - start_bbox[0]
            same_column = [
                other for other in page_starts
                if other["bbox"][1] > start_bbox[1]
                and abs((other["bbox"][0] + other["bbox"][2]) / 2 - start_center_x)
                <= max(start_width, other["bbox"][2] - other["bbox"][0]) * 1.5
            ]
            next_start = min(same_column, key=lambda item: item["bbox"][1], default=None)
            if image_center[1] < start["bbox"][1] or (next_start and image_center[1] >= next_start["bbox"][1]):
                continue
            column_overlap = _overlap_ratio(image_bbox, start["bbox"])
            # A start line can be short; use its horizontal center as a column hint
            # when the image does not literally overlap the line's bbox.
            in_column = start["bbox"][0] - 40 <= image_center[0] <= start["bbox"][2] + 40
            if not in_column and column_overlap == 0:
                continue
            vertical_span = (next_start["bbox"][1] - start["bbox"][1]) if next_start else 1000.0
            if next_start:
                position = (image_center[1] - start["bbox"][1]) / max(vertical_span, 1.0)
                # Being inside the interval is strong evidence; proximity to a
                # boundary lowers confidence but does not make the first candidate
                # win merely because it starts earlier on the page.
                vertical_score = 0.75 + 0.25 * max(0.0, 1.0 - abs(position - 0.5) * 2)
            else:
                vertical_score = 1.0
            score = 0.65 * vertical_score + 0.35 * max(column_overlap, 0.5 if in_column else 0.0)
            candidates.append({"number": start["number"], "score": round(score, 3), "page": page, "imageBbox": list(image_bbox), "questionBbox": list(start["bbox"])})
        candidates.sort(key=lambda item: item["score"], reverse=True)
        best = candidates[0] if candidates else None
        second_score = candidates[1]["score"] if len(candidates) > 1 else 0.0
        confident = bool(best and best["score"] >= 0.72 and best["score"] - second_score >= 0.12)
        entry = {
            "image": path,
            "status": "assigned" if confident else "needs_review",
            "candidates": candidates,
            "selectedQuestionNumber": best["number"] if best is not None and confident else None,
        }
        if audit is not None:
            audit.append(entry)
        if not confident or best is None:
            continue
        target = index_by_number.get(best["number"])
        if target is not None and path not in mutable[target]:
            mutable[target].append(path)
    return [(number, block, mutable[index]) for index, (number, block, _images) in enumerate(blocks)]


def _apply_caption_image_attribution(
    blocks: list[tuple[str, str, list[str]]],
    question_area: str,
    *,
    structured_captions: dict[str, str] | None = None,
) -> list[tuple[str, str, list[str]]]:
    """让图片后紧跟的"第N题图/第N题"标注优先于纯文本位置归属。

    普通归属逻辑按图片在 OCR 文本里的位置分配给"当前正在切分的题块"，题号识别失败
    （题号粘连在上一题末尾、没有换行）时会把明显不相关的图片一起分给错误的题目——
    例如一道题顶着四张分别标注"第5题图""第6题图""第9题图""第10题图"的图片。
    显式图注是比文本位置更可靠的信号，这里只处理"图片引用后紧跟第N题图/第N题"这种
    高置信度、格式收紧的命中；没有命中这个格式的图片，行为完全不变。

    ``structured_captions`` 来自 content_list.json 的 image/chart/table 块（见
    ``_structured_caption_attributions``），键是 MinerU 记录的 ``img_path``。这份数据
    不依赖扁平文本里题号是否粘连，比正则更可靠，命中时优先生效；和正则命中冲突时以
    结构化数据为准，互不冲突时结果不变。两者的路径字符串格式可能不完全一致（比如
    页面路由把图片重新拼接过），按文件名（``Path(...).name``）对齐，不假设完全相同。

    目标题号在 blocks 里不存在时（比如本身因为同样的题号识别失败而完全没被识别成
    独立块），不尝试推断它到底该属于哪个现有题——那是题号识别失败的根因，本函数
    不修；这里只做"移除"，让错误的题目不再顶着一张明确写着不属于自己的图，即使
    暂时没有更好的归位方案。
    """
    captions_by_basename: dict[str, str] = {}
    for match in IMAGE_CAPTION_PATTERN.finditer(question_area):
        captions_by_basename[Path(match.group("path")).name] = match.group("number")
    if structured_captions:
        for img_path, number in structured_captions.items():
            # 结构化命中优先生效，可能覆盖同一图片的正则判断；两者不冲突时结果不变。
            captions_by_basename[Path(img_path).name] = number
    if not captions_by_basename:
        return blocks
    block_index_by_number: dict[str, int] = {}
    for index, (number, _block, _images) in enumerate(blocks):
        block_index_by_number.setdefault(number, index)
    mutable_images = [list(images) for _number, _block, images in blocks]
    # 按文件名索引题块里实际出现过的图片路径，用来把 content_list.json 的 img_path
    # 对齐到当前文本里真正使用的那个路径字符串；找不到对应文件名时说明这张图从未
    # 出现在任何题块里（比如表格快照从未被内嵌成 Markdown 图片引用），没有安全的
    # 落点，直接跳过，不凭空塞进一个题块从未引用过的图片。
    basename_to_path: dict[str, str] = {}
    for images in mutable_images:
        for path in images:
            basename_to_path.setdefault(Path(path).name, path)
    for basename, declared_number in captions_by_basename.items():
        path = basename_to_path.get(basename)
        if path is None:
            continue
        for images in mutable_images:
            if path in images:
                images.remove(path)
        target_index = block_index_by_number.get(declared_number)
        if target_index is None:
            continue
        if path not in mutable_images[target_index]:
            mutable_images[target_index].append(path)
    return [
        (number, block, mutable_images[index])
        for index, (number, block, _images) in enumerate(blocks)
    ]


def limited_question_sources(
    source: str,
    limit: int = MAX_QUESTIONS_PER_BATCH,
    asset_dir: Path | None = None,
) -> list[tuple[str, str, list[str]]]:
    """Bound one batch's model cost while retaining a no-number fallback.

    快速预览调用保持 5 题；整卷任务可以提高到 20 题/批，但仍由代码上限约束，
    避免错误 OCR 把页眉、说明等碎片无限送入模型。
    """
    safe_limit = max(1, min(int(limit), MAX_FULL_PAPER_QUESTIONS_PER_BATCH))
    blocks = split_question_sources(source, asset_dir)[:safe_limit]
    return blocks or [("", source, MARKDOWN_IMAGE_PATTERN.findall(source))]


def select_complete_question_source(
    source: str,
    asset_dir: Path | None = None,
) -> tuple[str, str, list[str]]:
    """Select one intact question, preferring an illustrated example."""
    blocks = split_question_sources(source, asset_dir)
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
