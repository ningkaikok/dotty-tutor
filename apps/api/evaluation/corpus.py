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

import hashlib
import json
from typing import Any

from tests.test_question_segmentation import (
    _REAL_CAPTION_ATTRIBUTION_OCR_EXCERPT,
    _REAL_CONTENT_LIST_JSON_EXCERPT,
    _REAL_LINE_BREAK_RECONSTRUCTION_PAYLOAD,
    _REAL_SUBPROBLEM_A_PAYLOAD,
)

# 语料结构版本：期望字段的语义变化时递增，报告里记录以便对比历史结果。
CORPUS_VERSION = "1"

# 讲解 Judge 语料独立版本化。题目切分语料的版本变化不应让讲解评分报告
# 在没有实际变更讲解样本时失去可比性。
EXPLANATION_CORPUS_VERSION = "explanation-samples-v1"

EXPLANATION_SAMPLES: list[dict[str, str]] = [
    {
        "id": "guide-cards-perpendicular-bisector",
        "questionContext": "PA=PB，M 是 AB 中点，求证 PM 垂直 AB。",
        "explanation": (
            "还没有把“到两点距离相等”转化为可以证明的几何关系。"
            "先连接 PA、PB，再利用 M 是 AB 的中点。"
            "比较三角形 PAM 和 PBM，你能找到哪三组相等的边？"
        ),
    },
    {
        "id": "guide-cards-ssr-congruence",
        "questionContext": "接上题，已证 PA=PB、AM=BM，继续求证 PM⊥AB。",
        "explanation": (
            "已经找到相等的边，但还没有使用全等三角形。"
            "PA = PB、AM = BM，另外 PM 是两个三角形的公共边。"
            "两个三角形全等后，∠PMA 和 ∠PMB 有什么关系？"
        ),
    },
    {
        "id": "guide-cards-adjacent-supplementary",
        "questionContext": "接上题，已证 ∠PMA=∠PMB，求证 PM⊥AB。",
        "explanation": (
            "已经证明两个邻角相等，还差最后的垂直关系。"
            "∠PMA 与 ∠PMB 相等，并且它们组成一个平角。"
            "两个相等的邻补角分别是多少度？这说明 PM 与 AB 有什么关系？"
        ),
    },
]


def sample_set_hash(samples: list[dict[str, Any]]) -> str:
    """为评测样本的身份和内容生成稳定哈希，不把样本原文写入运行报告。"""
    canonical = json.dumps(
        samples, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()

# 真实 OCR 原文摘录（教材 56503d0642c54d728a7672e9cb77dd57，batch-001，第 3-9 题），
# 原存于 test_question_processing.py；question-segmentation-v3 修复后该测试改用合成
# 样本测安全网，这段真实文本移入语料，作为"伪题号已不再出现"的回归证据。
_REAL_DUPLICATE_NUMBER_OCR_EXCERPT = """3. （3分）计算 $3 x ^ { 2 } - x ^ { 2 }$ 的结果是（ )A. 2 B. $2 { \\tt x } ^ { 2 }$ C. 2x D. $4 \\mathsf { x } ^ { 2 }$

4.（3分）五名女生的体重（单位：kg）分别为：37、40、38、42、42，这组数
据的众数和中位数分别是（）
A. 2、40 B. 42、38 C. 40、42 D. 42、40

5. （3分）计算（a-2）（a+3）的结果是（）A. ${ \\mathsf { a } } ^ { 2 } - 6$ B. $\\mathsf { a } ^ { 2 } { + } \\mathsf { a } - 6$

6. （3分）点 A（2，-5）关于x轴对称的点的坐标是（）A. (2, 5) B. (- 2, 5) C. ( - 2, - 5) D. ( - 5, 2)

7.（3分）一个几何体由若干个相同的正方体组成，其主视图和俯视图如图所示，则这个几何体中正方体的个数最多是（）

A. 3 B. 4 C. 5 D. 6

8.（3分）一个不透明的袋中有四张完全相同的卡片，把它们分别标上数字1、2、

3、4. 随机抽取一张卡片，然后放回，再随机抽取一张卡片，则两次抽取的卡片上数字之积为偶数的概率是（）
A. $\\textstyle { \\frac { 1 } { 4 } }$ B. $\\textstyle { \\frac { 1 } { 2 } }$ C. $\\frac { 3 } { 4 }$ D. $\\frac { 5 } { 6 }$

9. （3分）将正整数1至2018按一定规律排列如下表：
"""

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
        "tags": ["image-misattribution", "question-number-boundary"],
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
        "tags": ["question-number-boundary"],
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
        "tags": ["fallback"],
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
            "真实教材 batch-001 第 3-9 题：句子中间的段落断点曾让 '3、' 被误判成新题号，"
            "与真正的第 3 题共享 key（v2 切分下序列为 3,8,3）。question-segmentation-v3 "
            "修复根因后序列恢复为干净的 [3, 8]；本条目固化为回归证据，防止误判回归。"
            "安全网本身的单元覆盖见 test_question_processing 的合成重复样本。"
        ),
        "tags": ["question-number-boundary", "duplicate-key"],
        "ocr_markdown": _REAL_DUPLICATE_NUMBER_OCR_EXCERPT,
        "structured_payload": None,
        "expect": {
            # v3 修复后伪 '3' 不再是边界：真实文本的题号序列必须保持干净。
            "filtered_number_sequence": {"numbers": ["3", "8"], "sequence": ["3", "8"]},
        },
    },
    {
        "id": "subproblem-a-enumeration-not-a-boundary",
        "description": (
            "roadmap T0 子问题 A 的修复回归证据：句子中间断行产生的 '3、4. 随机抽取…' "
            "不是新题号——'、'分隔且前文未以句末标点收尾时，必须并入上一题续行，"
            "真正的第 8、9 题边界不受影响；结构化重放与扁平路径结果保持一致。"
        ),
        "tags": ["question-number-boundary"],
        "ocr_markdown": SUBPROBLEM_A_SOURCE,
        "structured_payload": dict(_REAL_SUBPROBLEM_A_PAYLOAD),
        "expect": {
            "absent_question_numbers": ["3"],
            "question_numbers_present": ["8", "9"],
            "stable_across_structured_replay": True,
        },
    },
    {
        "id": "formula-normalize-latex-escapes",
        "description": (
            "公式规范化回归集：模型常见 LaTeX 转义损坏（textbackslash 百分号、"
            "degree 温度、textcirc、控制字符 frac/begin/end）的确定性修复，"
            "以及已正确输入必须原样保留（不进行开放式数学改写）。"
        ),
        "tags": ["formula-damage"],
        "kind": "formula-normalize",
        "cases": [
            {"raw": r"$7\textbackslash\text{%}$", "expected": r"$7\%$"},
            {"raw": r"$-3 \textdegree C$", "expected": r"$-3 ^{\circ}\mathrm{C}$"},
            {"raw": r"$7\textbackslash \textcirc C$", "expected": r"$7^{\circ}\mathrm{C}$"},
            {"raw": "$60^\\text{°}$", "expected": "$60^\\text{°}$"},
            {"raw": r"$60^\text{°}$", "expected": r"$60^\text{°}$"},
        ],
    },
    {
        "id": "quality-gate-unit-conflict",
        "description": (
            "审核维度：题干使用百分比但选项均为温度值——单位语义冲突必须被"
            "确定性门禁拦截为 needs_review，而不是静默发布。"
        ),
        "tags": ["answer-wrong"],
        "kind": "quality-gate",
        "payload": {
            "question": {
                "prompt": r"温度由 -4℃ 上升 $7\%$ 是（ ）",
                "options": ["3℃", "-3℃", "11℃", "-11℃"],
                "imageUrls": [],
            }
        },
        "sourceBlock": r"1. 温度由 -4℃ 上升 $7\%$ 是（ ）A. 3℃ B. -3℃ C. 11℃ D. -11℃",
        "expect": {"status": "needs_review", "errorContains": "单位语义冲突"},
    },
    {
        "id": "quality-gate-clean-choice-ready",
        "description": (
            "审核维度对照样本：题干与选项一致的健康选择题必须通过门禁（ready），"
            "防止误判修复把门禁变成全面拦截。"
        ),
        "tags": [],
        "kind": "quality-gate",
        "payload": {
            "question": {
                "prompt": "（3分）计算 $3 x^{2} - x^{2}$ 的结果是（ ）",
                "options": ["2", "$2x^{2}$", "2x", "$4x^{2}$"],
                "imageUrls": [],
            }
        },
        "sourceBlock": r"3. （3分）计算 $3 x ^ { 2 } - x ^ { 2 }$ 的结果是（ )A. 2 B. $2 { \\tt x } ^ { 2 }$ C. 2x D. $4 \\mathsf { x } ^ { 2 }$",
        "expect": {"status": "ready"},
    },
    {
        "id": "tutor-intent-taxonomy-stable",
        "description": (
            "陪练维度：八类学生意图 + 结构化作答优先级的意图识别回归。这些中文短语是"
            "陪练界面的实际按钮与高频口语，识别漂移会直接改变教学动作选择。"
        ),
        "tags": ["stage-overreach"],
        "kind": "turn-plan-intent",
        "cases": [
            {"mode": "answer", "content": "我选 A", "interactionResult": {}, "intentId": "submit-answer"},
            {"mode": "answer", "content": "准备好了", "interactionResult": {}, "intentId": "confirm-ready"},
            {"mode": "help", "content": "给我一点提示", "interactionResult": {}, "intentId": "request-hint"},
            {"mode": "help", "content": "为什么要这样做", "interactionResult": {}, "intentId": "request-explanation"},
            {"mode": "answer", "content": "帮我检查这一步", "interactionResult": {}, "intentId": "check-step"},
            {"mode": "help", "content": "我不认同标准答案", "interactionResult": {}, "intentId": "challenge-answer"},
            {"mode": "help", "content": "能举个例子吗", "interactionResult": {}, "intentId": "request-example"},
            {"mode": "help", "content": "我完全看不懂", "interactionResult": {}, "intentId": "express-confusion"},
            {"mode": "help", "content": "今天天气怎么样", "interactionResult": {}, "intentId": "off-topic"},
            {"mode": "help", "content": "标准答案是不是错了", "interactionResult": {"selectedOptions": ["B"]}, "intentId": "submit-answer"},
        ],
    },
]
