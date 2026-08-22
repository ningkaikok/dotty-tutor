"""金标准语料定义。

种子策略：直接从单测模块导入真实坏样本常量，保证"单测夹具 = 评测语料"单一事实
来源，避免两份拷贝漂移。等语料规模超过测试夹具后，再迁移到独立的 JSONL 数据文件。

条目字段约定：
- ``documenting_bug``：非空时表示该条目固化的是一个**已知未修复缺陷**的当前行为
  （特征化测试）。它通过不代表没问题——恰恰相反：一旦某次改动把它"修好"，重放器
  会报错并要求把该条目转正为正常期望，防止缺陷被无声地改变。
- ``expect`` 支持的检查键见 replay.py 的 ``_evaluate_entry``。
"""

from __future__ import annotations

from tests.test_question_processing import _REAL_DUPLICATE_NUMBER_OCR_EXCERPT
from tests.test_question_segmentation import (
    _REAL_CAPTION_ATTRIBUTION_OCR_EXCERPT,
    _REAL_CONTENT_LIST_JSON_EXCERPT,
    _REAL_LINE_BREAK_RECONSTRUCTION_PAYLOAD,
    _REAL_SUBPROBLEM_A_PAYLOAD,
)

# 语料结构版本：期望字段的语义变化时递增，报告里记录以便对比历史结果。
CORPUS_VERSION = "1"

SUBPROBLEM_A_SOURCE = (
    "8.（3分）一个不透明的袋中有四张完全相同的卡片，把它们分别标上数字1、2、"
    "\n\n"
    + _REAL_SUBPROBLEM_A_PAYLOAD["content_list"][0]["text"]
    + "\nA. 1/4 B. 1/2 C. 3/4 D. 5/6\n\n9. （3分）下一道真正的第9题。"
)

CORPUS: list[dict] = [
    {
        "id": "caption-attribution-real-excerpt",
        "description": (
            "真实教材图注归属坏样本：第9/10题因题号粘连未被切成独立块（子问题 B 的"
            "历史现场），结构化 content_list.json 里明确写了'第9题图''第10题图'，"
            "但没有安全落点，必须和纯正则路径一样移除这两张图。"
        ),
        "tags": ["caption-attribution", "question-number"],
        "ocr_markdown": _REAL_CAPTION_ATTRIBUTION_OCR_EXCERPT,
        "structured_payload": {
            "content_list": _REAL_CONTENT_LIST_JSON_EXCERPT,
        },
        "expect": {
            # 原文包含第 1-17 题，这里只固化与图注归属相关的关键题号，
            # 不对整段文本的切分结果做全量锁定。
            "question_numbers_present": ["5", "6", "8", "11", "16"],
            "absent_question_numbers": ["9", "10"],
            "images_by_number": {
                "5": ["images/547a74e3345f6b60c9a10d2801e2a69d036e7f200357915dfd4b4818a7871bbe.jpg"],
                "6": ["images/b90c7684d2d22c333b87981d53745f28d4c5dbd8f899553bc67979e72ad9d5dc.jpg"],
                "8": [],
                "11": ["images/7a8d6eb93bdb7c0ffe43f4d3fe5d58e0969fbe854259664aef6d64dbb386af81.jpg"],
                "16": ["images/765e1ec9e5d47ebc51c073517fd8207820821b959527457f6eac454c91ed991c.jpg"],
            },
        },
    },
    {
        "id": "line-break-reconstruction-recovers-9-and-10",
        "description": (
            "题号 9、10 紧跟上一题末尾没有换行（MinerU 扁平化吃掉的）。带 middle.json "
            "时行级重建应恢复独立题块；这是 v0.21.x 修复的回归证据。"
        ),
        "tags": ["line-break", "question-number"],
        "ocr_markdown": _REAL_LINE_BREAK_RECONSTRUCTION_PAYLOAD["content_list"][0]["text"],
        "structured_payload": dict(_REAL_LINE_BREAK_RECONSTRUCTION_PAYLOAD),
        "expect": {
            "question_numbers": ["5", "6", "7", "8", "9", "10"],
        },
    },
    {
        "id": "line-break-flat-fallback-behavior",
        "description": (
            "同一段文本在没有 middle.json 时必须完全走扁平路径：9、10 保持丢失。"
            "这不是期望的最终行为，而是回退语义的固化——回退路径的行为变化必须显式可见。"
        ),
        "tags": ["line-break", "fallback"],
        "ocr_markdown": _REAL_LINE_BREAK_RECONSTRUCTION_PAYLOAD["content_list"][0]["text"],
        "structured_payload": None,
        "expect": {
            "absent_question_numbers": ["9", "10"],
            "question_numbers_present": ["8"],
        },
    },
    {
        "id": "duplicate-question-number-safety-net",
        "description": (
            "真实教材 batch-001 第 3-9 题：句子中间的段落断点让 '3、' 被误判成新题号，"
            "与真正的第 3 题共享 key。当前安全网把重复项隔离而不是静默覆盖。"
        ),
        "tags": ["question-number", "duplicate-key"],
        "ocr_markdown": _REAL_DUPLICATE_NUMBER_OCR_EXCERPT,
        "structured_payload": None,
        "expect": {
            # 过滤出 {3, 8} 后的原始序列必须是 3, 8, 3：伪 '3' 与真 '3' 并存，
            # 这是安全网生效的前提条件。
            "filtered_number_sequence": {"numbers": ["3", "8"], "sequence": ["3", "8", "3"]},
        },
    },
    {
        "id": "subproblem-a-phantom-number-known-bug",
        "description": (
            "roadmap T0 子问题 A（续举例编号被误判为新题）的特征化条目：'3、' 伪题块"
            "目前依然存在，且结构化重放与扁平路径结果完全一致。修复该缺陷后本条目会"
            "失败，届时应把它转正为 absent_question_numbers 期望。"
        ),
        "tags": ["question-number", "known-bug"],
        "documenting_bug": "T0/question-number-subproblem-A",
        "ocr_markdown": SUBPROBLEM_A_SOURCE,
        "structured_payload": dict(_REAL_SUBPROBLEM_A_PAYLOAD),
        "expect": {
            "phantom_numbers_present": ["3"],
            "stable_across_structured_replay": True,
        },
    },
]
