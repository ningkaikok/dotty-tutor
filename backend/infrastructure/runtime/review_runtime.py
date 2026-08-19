"""题目文字与视觉审核编排。

生成模型的职责是“产出候选题”，审核模型的职责是“对照 OCR 来源纠错”。两者故意使用
独立选择，避免小型本地生成模型同时充当自己的裁判；同一个审核模型会同时检查文字和题图，
避免文本审核与视觉审核给出互相矛盾的结论。模型审核之后仍会进入确定性质量门禁；
审核分数和 ``needsHumanReview`` 只是证据，不是允许发布的最终条件。
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

from infrastructure.runtime.model_runtime import Provider, runtime
from infrastructure.runtime.contracts import RuntimeConfigSnapshot, attach_runtime_config


def _normalize_review_math(value: str) -> str:
    """延迟调用题目规范化，避免 review_runtime 与 question_pipeline 循环导入。"""
    from domain.questions.pipeline import normalize_model_math_text

    return normalize_model_math_text(value)


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
    """移除 MinerU 常见排版噪声，但不改写题意和数值。"""
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
    # MinerU 偶尔在两个公式之间产生孤立字母，例如 "$AB\\parallel DC$ R $AB=AD$"；
    # 仅修复这一种有明确上下文的噪声，避免对普通英文题干做激进替换。
    normalized = re.sub(r"(\$[^$]+\$)\s+R\s+(?=\$)", r"\1，", normalized)
    normalized = re.sub(r"[ \t]+([，。；：！？])", r"\1", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def formula_anomaly_score(text: str) -> int:
    return sum(len(pattern.findall(text)) for pattern in FORMULA_ANOMALIES)


class ReviewRuntime:
    """协调文本审核、图片审核及视觉冲突后的二次修复。"""

    def __init__(self) -> None:
        # 文字和题图审核使用同一个裁判模型，避免一题得到两套相互矛盾的审核结论。
        self.text_provider: Provider = os.getenv("REVIEW_PROVIDER", "codex")  # type: ignore[assignment]
        self.text_model = os.getenv("REVIEW_MODEL", "gpt-5.6-sol")

    def _audit_run(
        self,
        run: dict[str, Any],
        *,
        schema: dict[str, Any],
        prompt: str,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Normalize successful and fallback model runs to the review runtime."""
        existing = run.get("config") if isinstance(run, dict) else None
        if isinstance(existing, dict):
            snapshot = RuntimeConfigSnapshot.from_mapping({**existing, "runtime": "review"})
        else:
            snapshot = RuntimeConfigSnapshot.for_model(
                provider or self.text_provider,
                model or self.text_model,
                schema=schema,
                prompt=prompt,
                runtime="review",
                timeout=240.0,
            )
        return attach_runtime_config(run, snapshot)

    def catalog(self) -> dict[str, Any]:
        """返回统一审核模型目录，不改变题目生成模型。"""
        return {
            "selected": {
                "provider": self.text_provider,
                "model": self.text_model,
            },
            "providers": runtime.providers(),
        }

    def select_text(self, provider: Provider, model: str) -> dict[str, Any]:
        """切换统一审核模型，并拒绝当前环境中不可用的选项。"""
        provider_info = next(
            (item for item in runtime.providers() if item["id"] == provider),
            None,
        )
        if not provider_info or not provider_info["available"]:
            raise ValueError(f"{provider} 当前不可用于审核")
        if model not in provider_info["models"]:
            raise ValueError(f"{provider} 中没有审核模型 {model}")
        self.text_provider = provider
        self.text_model = model
        return self.catalog()

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
            self._audit_run(text_run, schema=TEXT_REVIEW_SCHEMA, prompt=text_prompt)
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
                        "text": _normalize_review_math(str(step.get("text", corrected["lessonSteps"][index]["text"]))[:700]),
                        "speechText": _normalize_review_math(str(step.get("speechText", corrected["lessonSteps"][index]["speechText"]))[:700]),
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
            self._audit_run(text_run, schema=TEXT_REVIEW_SCHEMA, prompt=text_prompt)

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
                    self.text_provider,
                    self.text_model,
                    vision_prompt,
                    VISION_REVIEW_SCHEMA,
                    max_tokens=1400,
                    image_paths=image_paths,
                )
                self._audit_run(vision_run, schema=VISION_REVIEW_SCHEMA, prompt=vision_prompt)
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
                    "requestedProvider": self.text_provider,
                    "provider": "none",
                    "model": self.text_model,
                    "fallback": True,
                    "error": vision_error,
                }
                self._audit_run(vision_run, schema=VISION_REVIEW_SCHEMA, prompt=vision_prompt)
        else:
            vision_review = {
                "correctAnswer": "",
                "imageAssessments": [],
                "issues": [],
                "confidence": 100,
                "needsHumanReview": False,
            }
            vision_run = {
                "requestedProvider": self.text_provider,
                "provider": "none",
                "model": self.text_model,
                "fallback": False,
                "skipped": "题目没有图片",
            }
            self._audit_run(vision_run, schema=VISION_REVIEW_SCHEMA, prompt="no-image-review")

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

        # 纯文字模型无法可靠判断讲解是否与题图一致。视觉审核完成后，把提取出的事实与冲突
        # 再交给文字模型修复讲解；这样持久化的是修正结果，而不是只有一个“疑似错误”标签。
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
                self._audit_run(repair_run, schema=TEXT_REVIEW_SCHEMA, prompt=repair_prompt)
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
                        "text": _normalize_review_math(str(step.get("text", corrected["lessonSteps"][index]["text"]))[:700]),
                        "speechText": _normalize_review_math(str(step.get("speechText", corrected["lessonSteps"][index]["speechText"]))[:700]),
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
        self._audit_run(
            review_run,
            schema=TEXT_REVIEW_SCHEMA,
            prompt="review-orchestration",
            provider=self.text_provider,
            model=self.text_model,
        )
        corrected["review"] = review_run
        return corrected, review_run


runtime_reviewer = ReviewRuntime()
