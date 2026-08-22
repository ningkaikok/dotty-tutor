"""Badcase 统一标签体系。

roadmap T1/Q2 要求所有坏样本用同一套标签描述失败模式，这样报告才能按类别聚合
（例如"本轮变更导致 2 个 image-misattribution 回归"），而不是靠人读标题。

约定：
- 标签是英文 kebab-case 常量，语义解释用中文；新增标签必须在这里注册，
  语料和登记簿不允许出现未注册标签（由单元测试强制）。
- 一个坏样本可以带多个标签，但应选一个最主要的放在 ``label`` 字段。
"""

from __future__ import annotations

BADCASE_LABELS: dict[str, str] = {
    "ocr-text-loss": "OCR 丢字或整行丢失，导致题目内容不完整",
    "formula-damage": "公式被 OCR 或规范化损坏，无法渲染或语义改变",
    "image-misattribution": "题图被错误归属给相邻题目或丢失",
    "question-number-boundary": "题号识别边界错误：漏切、多切或粘连",
    "duplicate-key": "同批次内多个题块共享存储 key，发生覆盖或冲突",
    "answer-wrong": "生成内容的答案本身计算或判断错误",
    "explanation-mismatch": "讲解内容与题目、答案或学生误区不符",
    "repeated-hint": "提示层级重复或无效，没有随学生状态升级",
    "stage-overreach": "陪练阶段越权：跳过阶段或执行了当前阶段不允许的动作",
    "timeout": "模型调用或任务超时",
    "wrong-fallback": "回退路径行为不符合预期（该回退没回退、不该回退却回退）",
}

# 允许出现在语料/登记簿里的流程性标记（不是失败模式本身，而是管理状态）。
PROCESS_TAGS: dict[str, str] = {
    "known-bug": "特征化条目：固化已知未修复缺陷的当前行为",
    "fallback": "固化回退路径的既有行为，防止无感漂移",
}


def validate_tags(tags: list[str], context: str) -> list[str]:
    """返回标签列表中的未知标签；空列表表示全部合法。"""
    known = set(BADCASE_LABELS) | set(PROCESS_TAGS)
    return [tag for tag in tags if tag not in known]
