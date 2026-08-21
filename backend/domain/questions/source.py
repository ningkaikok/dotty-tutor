"""Pure helpers for turning OCR Markdown into bounded question sources."""

from __future__ import annotations

import json
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


def split_question_sources(
    source: str,
    asset_dir: Path | None = None,
) -> list[tuple[str, str, list[str]]]:
    """Split OCR Markdown into bounded questions without answer-key leakage.

    OCR 页之间可能重复打印同一个题号；相邻的同号块按续题合并，既能保住跨页小问，也不把
    下一题吸进来。图片始终随其所在题块保留，顺序与 OCR Markdown 一致。

    ``asset_dir`` 是这次 OCR 的资源目录（含 PR A 落盘的 ``source.content_list.json``），
    可选；传入时会优先用其中的结构化图注字段做图片归属，没有时行为和之前完全一致。
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
    return _apply_caption_image_attribution(
        blocks, question_area, structured_captions=_structured_caption_attributions(asset_dir)
    )


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
