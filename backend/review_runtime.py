from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

from model_runtime import Provider, runtime


TEXT_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "corrected", "needs_review"]},
        "correctedPrompt": {"type": "string", "maxLength": 4000},
        "correctedGivens": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 200}},
        "correctedOptions": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 200}},
        "correctedLessonSteps": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string", "maxLength": 40},
                    "text": {"type": "string", "maxLength": 200},
                    "speechText": {"type": "string", "maxLength": 200},
                },
                "required": ["title", "text", "speechText"],
            },
        },
        "corrections": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "field": {"type": "string", "maxLength": 40},
                    "original": {"type": "string", "maxLength": 300},
                    "corrected": {"type": "string", "maxLength": 300},
                    "reason": {"type": "string", "maxLength": 200},
                },
                "required": ["field", "original", "corrected", "reason"],
            },
        },
        "issues": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 200}},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "needsHumanReview": {"type": "boolean"},
    },
    "required": [
        "verdict", "correctedPrompt", "correctedGivens", "correctedOptions",
        "correctedLessonSteps", "corrections", "issues", "confidence", "needsHumanReview",
    ],
}

VISION_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "correctAnswer": {"type": "string", "maxLength": 100},
        "imageAssessments": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "index": {"type": "integer", "minimum": 0, "maximum": 20},
                    "belongsToQuestion": {"type": "boolean"},
                    "visualDescription": {"type": "string", "maxLength": 500},
                    "relevantFacts": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 160}},
                    "conflicts": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 160}},
                },
                "required": ["index", "belongsToQuestion", "visualDescription", "relevantFacts", "conflicts"],
            },
        },
        "issues": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 200}},
        "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "needsHumanReview": {"type": "boolean"},
    },
    "required": ["correctAnswer", "imageAssessments", "issues", "confidence", "needsHumanReview"],
}


FONT_COMMAND = re.compile(r"\\(?:mathsf|textsf|mathtt|tt|mathrm|textrm|mathfrak)\s*\{\s*([^{}]*)\s*\}")
IMAGE_MARKDOWN = re.compile(r"!\[[^\]]*\]\([^\n)]+\)")
FORMULA_ANOMALIES = (
    re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]"),
    re.compile(r"\\root\b", re.IGNORECASE),
    re.compile(r"\\from\b", re.IGNORECASE),
    re.compile(r"/\s*\\backslash\s*/"),
    re.compile(r"\\(?:mathsf|textsf|mathtt|tt)\b"),
    re.compile(r"(?<![A-Za-z])R(?![A-Za-z])"),
)


def normalize_ocr_question(text: str) -> str:
    """Remove common MinerU presentation noise without changing question semantics."""
    normalized = IMAGE_MARKDOWN.sub("", text).strip()
    for _ in range(4):
        updated = FONT_COMMAND.sub(lambda match: match.group(1).strip(), normalized)
        if updated == normalized:
            break
        normalized = updated

    def clean_math(match: re.Match[str]) -> str:
        value = match.group(1)
        value = re.sub(
            r"\\begin\s*\{?\s*array\s*\}?\s*\{?\s*([clr])\s*\}?",
            r"\\begin{array}{\1}",
            value,
        )
        value = re.sub(r"\\end\s*\{?\s*array\s*\}?", r"\\end{array}", value)
        value = re.sub(r"(?<=\d)\s+(?=\d)", "", value)
        value = re.sub(r"(?<=[A-Za-z])\s+(?=[A-Za-z])", "", value)
        value = re.sub(r"/\s*/", r"\\parallel ", value)
        value = re.sub(r"\\sqrt\s*\{\s*([^{}]+?)\s*\}", r"\\sqrt{\1}", value)
        value = re.sub(
            r"\\(?:mathrm|textrm|mathfrak)\s*\{?\s*([A-Za-z]+)\s*\}?",
            r"\1",
            value,
        )
        value = re.sub(r"(?<![A-Za-z\\])\{\s*([A-Za-z]{1,5})\s*\}", r"\1", value)
        value = re.sub(r"\{\s*(=|\\bot|\\perp|\\parallel)\s*\}", r"\1", value)
        value = re.sub(r"\s*~\s*", " ", value)
        value = re.sub(r"\s+,", ",", value)
        value = re.sub(r"[ \t]{2,}", " ", value).strip()
        return f"${value}$"

    normalized = re.sub(r"\$([^$]+)\$", clean_math, normalized)
    # MinerU occasionally emits an isolated recognition artifact between two
    # formulas, for example "$AB\\parallel DC$ R $AB=AD$".
    normalized = re.sub(r"(\$[^$]+\$)\s+R\s+(?=\$)", r"\1，", normalized)
    normalized = re.sub(r"[ \t]+([，。；：！？])", r"\1", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def formula_anomaly_score(text: str) -> int:
    return sum(len(pattern.findall(text)) for pattern in FORMULA_ANOMALIES)


class ReviewRuntime:
    def __init__(self) -> None:
        self.text_provider: Provider = os.getenv("REVIEW_PROVIDER", "ollama")  # type: ignore[assignment]
        self.text_model = os.getenv("REVIEW_MODEL", "qwen2.5:7b")
        self.vision_provider: Provider = os.getenv("VISION_PROVIDER", "codex")  # type: ignore[assignment]
        self.vision_model = os.getenv("VISION_MODEL", "default")

    def review(
        self,
        payload: dict[str, Any],
        ocr_question_block: str,
        image_paths: list[Path],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        corrected = copy.deepcopy(payload)
        draft = json.dumps(payload, ensure_ascii=False)
        normalized_source = normalize_ocr_question(ocr_question_block)
        text_prompt = f"""
你是第二审校模型。逐项对照 OCR 原题与第一模型生成结果，系统性检查 OCR 错字、数字、运算符、上下标、根号、分数、角度、单位和公式，并检查四步讲解是否与原题一致。

规则：
1. 只修正能从 OCR 上下文明确确认的内容；不确定时保留原文并标记 needsHumanReview。
2. 不得把参考答案、其他题目或自行计算的答案加入题干。
3. correctedPrompt 必须是一道完整原题；correctedLessonSteps 必须恰好四步且不能与题意冲突。
4. corrections 逐条记录修改前后和理由；即使无需修改也返回完整字段。

OCR 原题：
---
{ocr_question_block[:12000]}
---

去除 OCR 字体包装与常见排版噪声后的候选原题（优先参考其公式写法，但仍须核对语义）：
---
{normalized_source[:12000]}
---

第一模型结果：
{draft[:12000]}
""".strip()
        text_error = None
        try:
            text_review, text_run = runtime.generate_json_as(
                self.text_provider,
                self.text_model,
                text_prompt,
                TEXT_REVIEW_SCHEMA,
                max_tokens=2200,
            )
            question = corrected["question"]
            if text_review.get("correctedPrompt"):
                question["prompt"] = str(text_review["correctedPrompt"])[:4000]
            reviewed_prompt = str(question.get("prompt", ""))
            if (
                normalized_source
                and formula_anomaly_score(reviewed_prompt) > formula_anomaly_score(normalized_source)
            ):
                question["prompt"] = normalized_source[:4000]
                corrections = text_review.setdefault("corrections", [])
                corrections.append({
                    "field": "题干公式规范化",
                    "original": reviewed_prompt[:300],
                    "corrected": normalized_source[:300],
                    "reason": "第二模型输出仍含 OCR/LaTeX 异常，改用同一原题块的确定性规范化结果。",
                })
                text_review["verdict"] = "corrected"
            question["givens"] = [str(item)[:200] for item in text_review.get("correctedGivens", [])[:8]]
            question["options"] = [str(item)[:200] for item in text_review.get("correctedOptions", [])[:8]]
            reviewed_steps = text_review.get("correctedLessonSteps", [])
            if len(reviewed_steps) == 4:
                for index, step in enumerate(reviewed_steps):
                    corrected["lessonSteps"][index].update({
                        "title": str(step.get("title", corrected["lessonSteps"][index]["title"]))[:80],
                        "text": str(step.get("text", corrected["lessonSteps"][index]["text"]))[:700],
                        "speechText": str(step.get("speechText", corrected["lessonSteps"][index]["speechText"]))[:700],
                    })
        except Exception as error:
            text_error = str(error)
            text_review = {
                "verdict": "needs_review",
                "corrections": [],
                "issues": [f"文字审校模型不可用：{error}"],
                "confidence": 0,
                "needsHumanReview": True,
            }
            text_run = {
                "requestedProvider": self.text_provider,
                "provider": "none",
                "model": self.text_model,
                "fallback": True,
                "error": text_error,
            }

        vision_error = None
        vision_review: dict[str, Any]
        if image_paths:
            image_names = [path.name for path in image_paths]
            vision_prompt = f"""
你是多模态数学题图审校模型。图片按顺序编号 0..{len(image_paths) - 1}，文件名依次为：{image_names}。
请真正观察每张图片，判断它是否属于下面这道题；描述图中的点、线、坐标、表格或数值，并指出与题干/讲解的冲突。
如果题干是选择题且图片按 A、B、C、D 顺序出现，请根据图片内容填写 correctAnswer（例如“(A)”）；不是选择题则填写空字符串。不要猜测图片外的信息。

题目与讲解：
{json.dumps(corrected, ensure_ascii=False)[:12000]}
""".strip()
            try:
                vision_review, vision_run = runtime.generate_json_as(
                    self.vision_provider,
                    self.vision_model,
                    vision_prompt,
                    VISION_REVIEW_SCHEMA,
                    max_tokens=1400,
                    image_paths=image_paths,
                )
            except Exception as error:
                vision_error = str(error)
                vision_review = {
                    "correctAnswer": "",
                    "imageAssessments": [],
                    "issues": [f"视觉审校模型不可用：{error}"],
                    "confidence": 0,
                    "needsHumanReview": True,
                }
                vision_run = {
                    "requestedProvider": self.vision_provider,
                    "provider": "none",
                    "model": self.vision_model,
                    "fallback": True,
                    "error": vision_error,
                }
        else:
            vision_review = {
                "correctAnswer": "",
                "imageAssessments": [],
                "issues": [],
                "confidence": 100,
                "needsHumanReview": False,
            }
            vision_run = {
                "requestedProvider": self.vision_provider,
                "provider": "none",
                "model": self.vision_model,
                "fallback": False,
                "skipped": "题目没有图片",
            }

        assessments = vision_review.get("imageAssessments", [])
        if assessments:
            accepted_indexes = {
                int(item["index"])
                for item in assessments
                if item.get("belongsToQuestion") and isinstance(item.get("index"), int)
            }
            corrected["question"]["imageUrls"] = [
                url for index, url in enumerate(corrected["question"].get("imageUrls", []))
                if index in accepted_indexes
            ]
            corrected["question"]["visualContext"] = [
                {
                    "description": item.get("visualDescription", ""),
                    "facts": item.get("relevantFacts", []),
                    "conflicts": item.get("conflicts", []),
                }
                for item in assessments if item.get("belongsToQuestion")
            ]

        # A text-only reviewer cannot reliably tell whether a lesson step
        # matches an option image. Give it the visual facts/conflicts once the
        # image pass finishes, so the persisted lesson is corrected instead of
        # merely carrying a warning badge.
        visual_conflicts = [
            str(conflict)
            for item in assessments
            for conflict in item.get("conflicts", [])
            if conflict
        ]
        visual_issues = [str(issue) for issue in vision_review.get("issues", []) if issue]
        if image_paths and (visual_conflicts or visual_issues):
            repair_prompt = f"""
你是最终讲解修复模型。请根据题干、当前四步讲解和视觉审校事实，修复所有与图片不一致的讲解。

硬性要求：
1. correctedPrompt 必须原样保留当前完整题干，不要缩写或换题。
2. correctedLessonSteps 必须恰好四步，逐项对应当前题目和图片；选择题必须明确指出正确选项及理由。
3. 不要使用图片中不存在的点名、角平分线、三角形或计算步骤；不确定就标记 needsHumanReview。
4. 只输出 JSON，不输出 Markdown。

当前题目与讲解：
{json.dumps(corrected, ensure_ascii=False)[:12000]}

视觉审校结果：
{json.dumps(vision_review, ensure_ascii=False)[:12000]}

视觉冲突与问题：
{json.dumps(visual_conflicts + visual_issues, ensure_ascii=False)[:6000]}
""".strip()
            try:
                repaired_review, repair_run = runtime.generate_json_as(
                    self.text_provider,
                    self.text_model,
                    repair_prompt,
                    TEXT_REVIEW_SCHEMA,
                    max_tokens=2200,
                )
                repaired_prompt = str(repaired_review.get("correctedPrompt", ""))
                if repaired_prompt:
                    corrected["question"]["prompt"] = repaired_prompt[:4000]
                corrected["question"]["givens"] = [
                    str(item)[:200] for item in repaired_review.get("correctedGivens", [])[:8]
                ]
                corrected["question"]["options"] = [
                    str(item)[:200] for item in repaired_review.get("correctedOptions", [])[:8]
                ]
                repaired_steps = repaired_review.get("correctedLessonSteps", [])
                if len(repaired_steps) == 4:
                    for index, step in enumerate(repaired_steps):
                        corrected["lessonSteps"][index].update({
                            "title": str(step.get("title", corrected["lessonSteps"][index]["title"]))[:80],
                            "text": str(step.get("text", corrected["lessonSteps"][index]["text"]))[:700],
                            "speechText": str(step.get("speechText", corrected["lessonSteps"][index]["speechText"]))[:700],
                        })
                repaired_review.setdefault("issues", [])
                repaired_review["issues"] = list(repaired_review["issues"])[:12]
                repaired_review["corrections"] = list(repaired_review.get("corrections", []))[:20]
                text_review = repaired_review
                text_run = {
                    **text_run,
                    "repairPass": True,
                    "repairModel": repair_run,
                }
            except Exception as error:
                text_review.setdefault("issues", []).append(f"视觉冲突后的讲解复修失败：{error}")
                text_review["needsHumanReview"] = True

        review_run = {
            "status": "needs_review" if text_review.get("needsHumanReview") or vision_review.get("needsHumanReview") else "reviewed",
            "text": text_review,
            "vision": vision_review,
            "textModelRun": text_run,
            "visionModelRun": vision_run,
            "needsHumanReview": bool(text_review.get("needsHumanReview") or vision_review.get("needsHumanReview")),
        }
        corrected["review"] = review_run
        return corrected, review_run


runtime_reviewer = ReviewRuntime()
