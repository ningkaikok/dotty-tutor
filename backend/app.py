from __future__ import annotations

import hashlib
import html
import math
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from pypdf import PdfReader

from model_runtime import Provider, runtime
from ocr_runtime import OcrProvider, runtime as ocr_runtime
from review_runtime import formula_anomaly_score, normalize_ocr_question, runtime_reviewer
from storage import store


app = FastAPI(title="Dotty Tutor", version="0.1.0")


def csv_env(name: str, default: str) -> list[str]:
    """Read a comma-separated environment variable without empty entries."""
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


cors_origins = csv_env(
    "CORS_ORIGINS",
    "http://localhost:5174,http://127.0.0.1:5174",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
)

trusted_hosts = csv_env("TRUSTED_HOSTS", "")
if trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add low-risk browser hardening and a request correlation header."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "microphone=(self)")
    return response


QUESTION = {
    "id": "geometry-perpendicular-bisector",
    "questionType": "short-answer",
    "correctAnswer": "",
    "interaction": {"type": "none", "instruction": "", "points": [], "requiredConnections": []},
    "chapter": "动点轨迹",
    "knowledgePoint": "到两个定点距离相等的点",
    "prompt": "已知 A、B 是两个定点，点 P 满足 PA = PB。点 P 的运动轨迹是什么？",
    "givens": ["PA = PB", "M 是 AB 的中点"],
}


LESSON_STEPS = [
    {
        "id": "points",
        "title": "建立已知条件",
        "text": "固定点 A、B，M 是 AB 的中点。",
        "speechText": "先画出两个固定点 A 和 B，并标出线段 AB 的中点 M。",
        "action": "show-base",
    },
    {
        "id": "equal-distance",
        "title": "加入动点 P",
        "text": "取满足 PA = PB 的点 P，并连接 PA、PB。",
        "speechText": "现在取一点 P，使它到 A、B 两点的距离相等。",
        "action": "show-point-p",
    },
    {
        "id": "triangles",
        "title": "比较两个三角形",
        "text": "PA = PB，AM = BM，PM 为公共边。",
        "speechText": "比较三角形 PAM 和 PBM，它们有三组对应边相等。",
        "action": "show-triangles",
    },
    {
        "id": "conclusion",
        "title": "得到轨迹",
        "text": "PM 垂直 AB；所有这样的 P 都在 AB 的垂直平分线上。",
        "speechText": "因此 PM 垂直于 AB，点 P 的轨迹就是线段 AB 的垂直平分线。",
        "action": "show-bisector",
    },
]


# 生产环境中，这些内容通常在教材数字化阶段生成并存库。
GUIDE_CARDS = [
    {
        "level": 0,
        "stuckAt": "还没有把“到两点距离相等”转化为可以证明的几何关系。",
        "knowledge": ["等距", "中点", "全等三角形"],
        "hint": "先连接 PA、PB，再利用 M 是 AB 的中点。",
        "question": "比较三角形 PAM 和 PBM，你能找到哪三组相等的边？",
        "canvasAction": "show-triangles",
    },
    {
        "level": 1,
        "stuckAt": "已经找到相等的边，但还没有使用全等三角形。",
        "knowledge": ["SSS 全等", "对应角相等"],
        "hint": "PA = PB、AM = BM，另外 PM 是两个三角形的公共边。",
        "question": "两个三角形全等后，∠PMA 和 ∠PMB 有什么关系？",
        "canvasAction": "show-triangles",
    },
    {
        "level": 2,
        "stuckAt": "已经证明两个邻角相等，还差最后的垂直关系。",
        "knowledge": ["邻补角", "垂直", "垂直平分线"],
        "hint": "∠PMA 与 ∠PMB 相等，并且它们组成一个平角。",
        "question": "两个相等的邻补角分别是多少度？这说明 PM 与 AB 有什么关系？",
        "canvasAction": "show-bisector",
    },
]

CANVAS_ACTIONS = ["show-base", "show-point-p", "show-triangles", "show-bisector"]
LESSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "chapter": {"type": "string", "maxLength": 30},
        "knowledgePoint": {"type": "string", "maxLength": 50},
        "questionNumber": {"type": "string", "maxLength": 20},
        "questionType": {"type": "string", "enum": ["choice", "true-false", "short-answer", "draw-line"]},
        "prompt": {"type": "string", "maxLength": 800},
        "correctAnswer": {"type": "string", "maxLength": 120},
        "interaction": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "type": {"type": "string", "enum": ["none", "draw-line"]},
                "instruction": {"type": "string", "maxLength": 160},
                "points": {
                    "type": "array",
                    "maxItems": 12,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "id": {"type": "string", "maxLength": 8},
                            "label": {"type": "string", "maxLength": 12},
                            "x": {"type": "number", "minimum": 0, "maximum": 1},
                            "y": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": ["id", "label", "x", "y"],
                    },
                },
                "requiredConnections": {
                    "type": "array",
                    "maxItems": 12,
                    "items": {"type": "array", "minItems": 2, "maxItems": 2, "items": {"type": "string", "maxLength": 8}},
                },
            },
            "required": ["type", "instruction", "points", "requiredConnections"],
        },
        "givens": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 80}},
        "options": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 120}},
        "imageReferences": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 160}},
        "lessonSteps": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "title": {"type": "string", "maxLength": 20},
                    "text": {"type": "string", "maxLength": 120},
                    "speechText": {"type": "string", "maxLength": 120},
                },
                "required": ["title", "text", "speechText"],
            },
        },
        "guideCards": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "stuckAt": {"type": "string", "maxLength": 80},
                    "knowledge": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 30}},
                    "hint": {"type": "string", "maxLength": 100},
                    "question": {"type": "string", "maxLength": 100},
                },
                "required": ["stuckAt", "knowledge", "hint", "question"],
            },
        },
    },
    "required": ["chapter", "knowledgePoint", "questionNumber", "questionType", "prompt", "correctAnswer", "interaction", "givens", "options", "imageReferences", "lessonSteps", "guideCards"],
}
HELP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "assessment": {"type": "string", "enum": ["correct", "partial", "incorrect"]},
        "reply": {"type": "string", "maxLength": 220},
        "stuckAt": {"type": "string", "maxLength": 100},
        "knowledge": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 30}},
        "hint": {"type": "string", "maxLength": 120},
        "question": {"type": "string", "maxLength": 120},
        "canvasAction": {"type": "string", "enum": CANVAS_ACTIONS},
    },
    "required": ["assessment", "reply", "stuckAt", "knowledge", "hint", "question", "canvasAction"],
}

ALLOWED_UPLOAD_SUFFIXES = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif",
    ".gif", ".bmp", ".tif", ".tiff", ".pdf",
}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
PDF_MAX_UPLOAD_BYTES = 500 * 1024 * 1024
PDF_CHUNK_BYTES = 5 * 1024 * 1024
PDF_BATCH_PAGES = 5
PDF_TAIL_CHECK_BYTES = 64 * 1024
UPLOAD_ROOT = store.upload_root
pdf_uploads: dict[str, dict[str, Any]] = {}
lesson_store: dict[str, dict[str, Any]] = {}
QWEN_TTS_URL = os.getenv("QWEN_TTS_URL", "http://127.0.0.1:8020")
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "auto").lower()
AZURE_SPEECH_KEY = os.getenv("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.getenv("AZURE_SPEECH_REGION", "")
AZURE_SPEECH_VOICE = os.getenv("AZURE_SPEECH_VOICE", "zh-CN-XiaoxiaoNeural")


class HelpRequest(BaseModel):
    questionId: str
    studentInput: str = Field(default="", max_length=1_000)
    hintLevel: int = Field(default=0, ge=0, le=3)
    language: Literal["zh", "en"] = "zh"
    mode: Literal["answer", "help"] = "help"
    interactionResult: dict[str, Any] = Field(default_factory=dict)


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2_000)
    speaker: str = Field(default="Serena", max_length=40)
    instruct: str = Field(
        default="用耐心、清晰、自然的中文老师语气朗读，语速稍慢，重点处有轻微停顿。",
        max_length=200,
    )


class TutorReply(BaseModel):
    reply: str
    guideContext: dict
    nextHintLevel: int
    canvasAction: str
    source: Literal["stored-guide-card", "answer-check", "model-generated"]
    modelRun: dict[str, Any] = Field(default_factory=dict)


class ModelSelectionRequest(BaseModel):
    provider: Provider
    model: str = Field(min_length=1, max_length=100)


class OcrSelectionRequest(BaseModel):
    provider: OcrProvider


class PdfUploadInitRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0, le=PDF_MAX_UPLOAD_BYTES)
    contentType: str = "application/pdf"
    chunkSize: int = Field(default=PDF_CHUNK_BYTES, ge=1024, le=PDF_CHUNK_BYTES)
    totalChunks: int = Field(gt=0, le=200)
    sourceText: str = Field(default="", max_length=20_000)


def mock_model_run(requested_provider: str = "mock", error: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "requestedProvider": requested_provider,
        "provider": "mock",
        "model": "static-demo",
        "fallback": requested_provider != "mock",
    }
    if error:
        result["error"] = error
    return result


def build_reply(
    request: HelpRequest,
    guide_cards: list[dict[str, Any]] | None = None,
    model_run: dict[str, Any] | None = None,
) -> TutorReply:
    normalized = request.studentInput.strip().lower()
    correct_markers = ("垂直平分线", "perpendicular bisector")
    cards = guide_cards or GUIDE_CARDS
    run = model_run or mock_model_run()

    if any(marker in normalized for marker in correct_markers):
        guide_context = {
            "assessment": "correct",
            "stuckAt": "学生已经提出正确猜想，需要补全证明。",
            "knowledge": ["全等三角形", "垂直平分线"],
            "hint": "不要停在结论；用 PA = PB、AM = BM 和公共边 PM 说明理由。",
            "question": "你能用 SSS 全等把这个结论证明完整吗？",
        }
        return TutorReply(
            reply="这个猜想是对的。先别急着结束：请说明三角形 PAM 与 PBM 为什么全等。",
            guideContext=guide_context,
            nextHintLevel=min(request.hintLevel + 1, 3),
            canvasAction="show-triangles",
            source="answer-check",
            modelRun=run,
        )

    card = cards[min(request.hintLevel, len(cards) - 1)]
    stuck_markers = ("不知道", "不会", "没思路", "卡住", "don't know", "stuck")
    has_attempt = bool(normalized) and not any(marker in normalized for marker in stuck_markers)
    if request.mode == "answer" and has_attempt:
        prefix = "我会先核对你写的这一步。"
    else:
        prefix = "我看到你已经写了一些思路。" if has_attempt else "没关系，我们只往前走一步。"
    reply = f"{prefix}{card['hint']}\n\n{card['question']}"
    return TutorReply(
        reply=reply,
        guideContext={
            "assessment": "partial" if has_attempt else "incorrect",
            "stuckAt": card["stuckAt"],
            "knowledge": card["knowledge"],
            "hint": card["hint"],
            "question": card["question"],
        },
        nextHintLevel=min(request.hintLevel + 1, 3),
        canvasAction=card["canvasAction"],
        source="stored-guide-card",
        modelRun=run,
    )


def question_payload(
    question: dict[str, Any] | None = None,
    lesson_steps: list[dict[str, Any]] | None = None,
    model_run: dict[str, Any] | None = None,
) -> dict:
    return {
        "question": question or QUESTION,
        "lessonSteps": lesson_steps or LESSON_STEPS,
        "architecture": {
            "input": "scanned textbook page",
            "stored": "question + answer + guide cards",
            "runtime": "student input + selected guide_context",
        },
        "modelRun": model_run or mock_model_run(),
    }


def safe_text(value: Any, fallback: str, limit: int = 600) -> str:
    text = str(value or "").strip()
    return (text or fallback)[:limit]


def safe_string_list(value: Any, fallback: list[str], limit: int = 8) -> list[str]:
    if not isinstance(value, list):
        return fallback
    items = [safe_text(item, "", 160) for item in value]
    items = [item for item in items if item]
    return items[:limit] or fallback


def extract_pdf_text(reader: PdfReader, max_pages: int = 10, max_chars: int = 16_000) -> str:
    pages: list[str] = []
    for page in reader.pages[:max_pages]:
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if text:
            pages.append(text)
        if sum(len(item) for item in pages) >= max_chars:
            break
    return "\n\n".join(pages)[:max_chars]


def resolve_ocr_text(
    source_text: str,
    extracted_text: str = "",
    source_path: Path | None = None,
    start_page: int = 0,
    end_page: int | None = None,
    asset_dir: Path | None = None,
    asset_url_prefix: str = "",
) -> tuple[str, dict[str, Any]]:
    if source_text.strip():
        return source_text.strip(), {
            "requestedProvider": "manual",
            "provider": "manual",
            "mode": "pasted-text",
            "fallback": False,
            "output": "text",
        }

    requested = ocr_runtime.selection.provider
    if source_path and ocr_runtime.should_use_mineru():
        try:
            return ocr_runtime.parse(
                source_path,
                start_page,
                end_page,
                asset_dir,
                asset_url_prefix,
            )
        except Exception as error:
            return extracted_text, {
                "requestedProvider": requested,
                "provider": "pypdf" if extracted_text else "none",
                "mode": "text-layer-fallback" if extracted_text else "ocr-failed",
                "fallback": True,
                "error": str(error),
                "output": "text",
            }

    return extracted_text, {
        "requestedProvider": requested,
        "provider": "pypdf" if extracted_text else "none",
        "mode": "text-layer" if extracted_text else "no-text-layer",
        "fallback": False,
        "output": "text",
    }


EQUATION_PATTERN = re.compile(
    r"(?<![0-9A-Za-z])([0-9A-Za-z]+(?:\s*[+\-*/]\s*[0-9A-Za-z]+)*\s*=\s*"
    r"-?\s*[0-9A-Za-z]+(?:\s*[+\-*/]\s*[0-9A-Za-z]+)*)(?![0-9A-Za-z])"
)


def linear_equation_form(equation: str) -> tuple[float, float] | None:
    if equation.count("=") != 1:
        return None

    def expression_form(expression: str) -> tuple[float, float] | None:
        normalized = expression.replace(" ", "").replace("*", "")
        terms = re.findall(r"[+-]?[^+-]+", normalized)
        coefficient = 0.0
        constant = 0.0
        try:
            for term in terms:
                if term.endswith("x"):
                    shown = term[:-1]
                    coefficient += 1.0 if shown in ("", "+") else -1.0 if shown == "-" else float(shown)
                elif "x" in term or not term:
                    return None
                else:
                    constant += float(term)
        except ValueError:
            return None
        return coefficient, constant

    left, right = equation.split("=", 1)
    left_form = expression_form(left)
    right_form = expression_form(right)
    if not left_form or not right_form:
        return None
    return left_form[0] - right_form[0], left_form[1] - right_form[1]


def equivalent_linear_equations(first: str, second: str) -> bool | None:
    first_form = linear_equation_form(first)
    second_form = linear_equation_form(second)
    if not first_form or not second_form:
        return None
    first_a, first_b = first_form
    second_a, second_b = second_form
    if abs(first_a) < 1e-9 or abs(second_a) < 1e-9:
        return None
    return abs(first_a * second_b - second_a * first_b) < 1e-7


def equation_conflict(
    student_input: str,
    lesson_steps: list[dict[str, Any]],
    question_prompt: str = "",
) -> tuple[str, str] | None:
    student_equations = [re.sub(r"\s+", "", item) for item in EQUATION_PATTERN.findall(student_input)]
    question_equations = [re.sub(r"\s+", "", item) for item in EQUATION_PATTERN.findall(question_prompt)]
    for student_equation in student_equations:
        for question_equation in question_equations:
            equivalent = equivalent_linear_equations(student_equation, question_equation)
            if equivalent is False:
                return student_equation, question_equation

    reference_text = "\n".join(
        f"{step.get('text', '')} {step.get('speechText', '')}" for step in lesson_steps
    )
    reference_equations = [re.sub(r"\s+", "", item) for item in EQUATION_PATTERN.findall(reference_text)]
    for student_equation in student_equations:
        student_left = student_equation.split("=", 1)[0]
        for reference_equation in reference_equations:
            if reference_equation.split("=", 1)[0] == student_left and reference_equation != student_equation:
                return student_equation, reference_equation
    return None


QUESTION_START_PATTERN = re.compile(r"(?m)^\s*(?P<number>\d{1,3})[.．、]\s*")
MARKDOWN_IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
MAX_QUESTIONS_PER_BATCH = 5


def split_question_sources(source: str) -> list[tuple[str, str, list[str]]]:
    """Split OCR Markdown into complete numbered questions before answers."""
    question_area = re.split(r"(?m)^\s*#*\s*(?:参考答案|答案|解析)\s*$", source, maxsplit=1)[0]
    matches = list(QUESTION_START_PATTERN.finditer(question_area))
    blocks: list[tuple[str, str, list[str]]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(question_area)
        block = question_area[match.start():end].strip()
        if len(block) < 4:
            continue
        images = MARKDOWN_IMAGE_PATTERN.findall(block)
        blocks.append((match.group("number"), block, images))
    return blocks


def limited_question_sources(source: str) -> list[tuple[str, str, list[str]]]:
    blocks = split_question_sources(source)[:MAX_QUESTIONS_PER_BATCH]
    return blocks or [("", source, MARKDOWN_IMAGE_PATTERN.findall(source))]


def select_complete_question_source(source: str) -> tuple[str, str, list[str]]:
    """Select one intact illustrated question, kept for compatibility."""
    blocks = split_question_sources(source)
    if not blocks:
        return "", source.strip(), MARKDOWN_IMAGE_PATTERN.findall(source)

    # A diagram-dependent problem proves the end-to-end image path and is
    # usually a better interactive tutoring example. Otherwise use the first
    # complete numbered problem in the batch.
    return next((block for block in blocks if block[2]), blocks[0])


def question_image_paths(asset_dir: Path, references: list[str]) -> list[Path]:
    """Resolve only the images referenced by one question, in source order."""
    available = {
        path.name: path
        for path in asset_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    } if asset_dir.is_dir() else {}
    return [available[Path(reference).name] for reference in references if Path(reference).name in available]


def question_key(batch_id: str, number: str, index: int) -> str:
    normalized = re.sub(r"[^0-9A-Za-z_-]+", "", number) or f"index-{index + 1:03d}"
    return f"{batch_id}-q-{normalized}"


def process_question_sources(
    question_sources: list[tuple[str, str, list[str]]],
    batch: dict[str, Any],
    ocr_run: dict[str, Any],
    asset_dir: Path,
    job: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate, attach, review and return every question in one OCR batch."""
    payloads: list[dict[str, Any]] = []
    guide_cards_list: list[list[dict[str, Any]]] = []
    model_runs: list[dict[str, Any]] = []
    review_runs: list[dict[str, Any]] = []
    total = max(1, len(question_sources))
    for index, (number, block, images) in enumerate(question_sources):
        payload, guide_cards, model_run = generate_lesson(block)
        attach_question_source(payload, batch, ocr_run, images)
        if number:
            payload["question"]["questionNumber"] = number
        payload["question"]["sourceQuestionKey"] = question_key(batch["id"], number, index)
        payload, review_run = review_lesson_payload(
            payload,
            block,
            question_image_paths(asset_dir, images),
            guide_cards,
        )
        normalize_stacked_equation_choices(payload, block)
        normalize_text_choices_from_source(
            payload, str(payload["question"].get("prompt", ""))
        )
        normalize_text_choices_from_source(payload, block)
        reviewed_options = normalize_text_choice_labels(
            safe_string_list(payload["question"].get("options"), [], 6)
        )
        reviewed_options = [normalize_model_math_text(option) for option in reviewed_options]
        payload["question"]["options"] = reviewed_options
        payload["question"]["prompt"] = normalize_model_math_text(
            strip_choice_text_from_prompt(
                str(payload["question"].get("prompt", "")), reviewed_options
            )
        )
        normalize_image_choice_question(payload, block, images)
        apply_question_quality_gate(payload, block, images)
        payloads.append(payload)
        guide_cards_list.append(guide_cards)
        model_runs.append(model_run)
        review_runs.append(review_run)
        if job is not None:
            update_upload_job(
                job,
                "generating",
                min(94, 88 + round(((index + 1) / total) * 6)),
                f"正在处理第 {index + 1}/{len(question_sources)} 道题",
            )
    return payloads, guide_cards_list, model_runs, review_runs


def build_lesson_prompt(source: str) -> str:
    return f"""
请从下面的教材文字中结构化一道中文数学题，并生成互动辅导脚本。
要求：
1. 只依据教材文字，不确定的条件不要补充。
2. lessonSteps 恰好输出 4 步；每个 text 和 speechText 都不超过 60 个汉字。
3. guideCards 恰好输出 3 张，每个字段不超过 50 个汉字；依次从轻提示到强提示，只引导下一步。
4. 输入只包含一道按题号切出的完整题，不得换题、合并其他题或遗漏小问。
5. questionNumber 原样填写题号；prompt 保留完整题干，但不要重复独占一行的 (A)(B)(C)(D) 图片标签。
6. questionType 只能是 choice、true-false、short-answer、draw-line 之一：有 A/B/C/D 等选项用 choice；要求判断正误用 true-false；要求作图、连线或画辅助线用 draw-line；其余用 short-answer。
7. true-false 题的 correctAnswer 必须是“正确”或“错误”；其他题型如果教材没有明确答案则返回空字符串。
8. draw-line 题必须生成 interaction：type 为 draw-line，points 中给出需要显示的点及 0 到 1 的归一化坐标，requiredConnections 给出必须连接的点对；其他题型 interaction.type 为 none。
9. 选择题的文字选项逐项放入 options。若 A/B/C/D 是四张图片，则 options 保留四个标签，并把四个图片文件名按 A、B、C、D 顺序放入 imageReferences。
10. givens 只拆出题目明确给出的条件，不要用整段教材或图片 Markdown 代替。
11. 不依赖图片则 imageReferences 返回空数组。

教材文字：
---
{source}
---
""".strip()


def write_model_prompt_artifact(
    asset_dir: Path,
    question_sources: list[tuple[str, str, list[str]]],
) -> Path:
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
    """Keep generated drawing metadata safe and predictable for the UI."""
    if question_type != "draw-line" or not isinstance(raw, dict):
        return {"type": "none", "instruction": "", "points": [], "requiredConnections": []}

    points: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw_points = raw.get("points") if isinstance(raw.get("points"), list) else []
    for item in raw_points[:12]:
        if not isinstance(item, dict):
            continue
        point_id = safe_text(item.get("id"), "", 8).strip()
        if not point_id or point_id in seen:
            continue
        try:
            x = max(0.04, min(float(item.get("x", 0.5)), 0.96))
            y = max(0.08, min(float(item.get("y", 0.5)), 0.92))
        except (TypeError, ValueError):
            continue
        seen.add(point_id)
        points.append({
            "id": point_id,
            "label": safe_text(item.get("label"), point_id, 12),
            "x": round(x, 4),
            "y": round(y, 4),
        })

    raw_connections = raw.get("requiredConnections") if isinstance(raw.get("requiredConnections"), list) else []
    required_connections: list[list[str]] = []
    for item in raw_connections[:12]:
        if not isinstance(item, list) or len(item) != 2:
            continue
        first, second = (safe_text(value, "", 8).strip() for value in item)
        if first and second and first != second and first in seen and second in seen:
            pair = sorted([first, second])
            if pair not in required_connections:
                required_connections.append(pair)

    if len(points) < 2:
        points = [
            {"id": "A", "label": "A", "x": 0.25, "y": 0.5},
            {"id": "B", "label": "B", "x": 0.75, "y": 0.5},
        ]
        required_connections = [["A", "B"]]

    return {
        "type": "draw-line",
        "instruction": safe_text(raw.get("instruction"), "请连接 A 和 B。", 160),
        "points": points,
        "requiredConnections": required_connections,
    }


def normalize_image_choice_question(
    payload: dict[str, Any],
    source_block: str,
    source_images: list[str],
) -> None:
    """Deterministically bind four source images to A/B/C/D after review."""
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
    """Keep the stem in ``prompt``; choices are rendered from ``options``."""
    if not options:
        return prompt.strip()
    match = re.search(r"\(A\)", prompt)
    if match:
        return prompt[: match.start()].rstrip(" \n\t:：")
    return prompt.strip()


def normalize_text_choice_labels(options: list[str]) -> list[str]:
    """Remove duplicated A/B/C/D prefixes; the UI owns option labels."""
    normalized: list[str] = []
    for option in options:
        value = option.strip()
        stripped = re.sub(r"^(?:\([A-H]\)|[A-H][.:：、])\s*", "", value)
        normalized.append(stripped if stripped else value)
    return normalized


def split_concatenated_text_choices(options: list[str]) -> list[str]:
    """Split a model response that flattened A-D choices into one string.

    Small/local models occasionally return choices as
    ``["(A) ... (B) ... (C) ... (D) ..."]`` instead of a four-item array.
    Keep the original value unless we can prove that all four labels occur in
    order, so ordinary one-item answers are not changed accidentally.
    """
    if len(options) != 1:
        return options
    value = options[0].strip()
    if not value:
        return options

    matches = list(
        re.finditer(
            r"(?<![A-Za-z0-9])(?:\(([A-D])\)|([A-D])[.．:：、])\s*",
            value,
        )
    )
    labels = [match.group(1) or match.group(2) for match in matches]
    if labels != ["A", "B", "C", "D"]:
        return options

    values = [
        value[match.start() : (matches[index + 1].start() if index + 1 < len(matches) else len(value))].strip()
        for index, match in enumerate(matches)
    ]
    return values if all(values) else options


def normalize_model_math_text(value: str) -> str:
    """Repair LaTeX commands accidentally decoded as JSON control escapes."""
    replacements = {
        "\x08egin": r"\begin",
        "\text": r"\text",
        "\times": r"\times",
        "\x0crac": r"\frac",
    }
    for broken, corrected in replacements.items():
        value = value.replace(broken, corrected)
    value = re.sub(
        r"\\begin\s*\{?\s*array\s*\}?\s*\{?\s*([clr])\s*\}?",
        r"\\begin{array}{\1}",
        value,
    )
    return re.sub(r"\\end\s*\{?\s*array\s*\}?", r"\\end{array}", value)


def normalize_text_choices_from_source(payload: dict[str, Any], source_block: str) -> None:
    """Restore textual A-D values when a reviewer returns labels only."""
    current = [str(item).strip() for item in payload["question"].get("options", [])]
    split_options = split_concatenated_text_choices(current)
    if split_options != current:
        payload["question"]["options"] = split_options
        return
    labels_only = len(current) == 4 and all(
        re.fullmatch(r"(?:\([A-D]\)|[A-D][.:：、]?)", item) for item in current
    )
    if not labels_only and len(current) != 1:
        return
    normalized_source = normalize_ocr_question(normalize_model_math_text(source_block))
    matches = re.findall(
        r"\(([A-D])\)\s*(.*?)(?=\s*\([A-D]\)|$)",
        normalized_source,
        flags=re.DOTALL,
    )
    if [label for label, _value in matches] != ["A", "B", "C", "D"]:
        return
    values = [value.strip() for _label, value in matches]
    values = [
        re.sub(r"^(\d+(?:\.\d+)?)\s+(?=\$10\b)", r"\1 × ", value)
        for value in values
    ]
    if not all(values):
        return
    payload["question"]["options"] = values


def normalize_stacked_equation_choices(payload: dict[str, Any], source_block: str) -> None:
    """Recover equation-system choices that MinerU flattens into two rows.

    A common exam layout puts all x-values on one row and all y-values on the
    next. MinerU preserves the values but serializes them as x1..x4, labels,
    then y1..y4. Pairing by column is deterministic and avoids asking a small
    language model to infer the original table layout.
    """
    normalized_source = normalize_ocr_question(normalize_model_math_text(source_block))
    if "方程" not in normalized_source or "的解为" not in normalized_source or "(A)" not in normalized_source:
        return
    solution_text = normalized_source.split("的解为", 1)[1]
    x_section, y_section = solution_text.split("(A)", 1)
    x_values = re.findall(r"x\s*=\s*(-?\s*\d+)", x_section, flags=re.IGNORECASE)
    y_values = re.findall(r"y\s*=\s*(-?\s*\d+)", y_section, flags=re.IGNORECASE)
    if len(x_values) < 4 or len(y_values) < 4:
        return

    def compact_number(value: str) -> str:
        return re.sub(r"\s+", "", value)

    question = payload["question"]
    question["options"] = [
        f"$x={compact_number(x_value)},\\;y={compact_number(y_value)}$"
        for x_value, y_value in zip(x_values[:4], y_values[:4])
    ]
    stem = normalized_source.split("的解为", 1)[0] + "的解为"
    stem = QUESTION_START_PATTERN.sub("", stem, count=1)
    question["prompt"] = stem.strip()


MATH_FRAGMENT_PATTERN = re.compile(r"(\$\$[\s\S]+?\$\$|\$[^$]+?\$)")


def rich_text_blocks(text: str, id_prefix: str) -> list[dict[str, Any]]:
    """Split corrected text into ordered text/math blocks for stable rendering."""
    blocks: list[dict[str, Any]] = []
    for fragment in MATH_FRAGMENT_PATTERN.split(text):
        if not fragment:
            continue
        display = fragment.startswith("$$") and fragment.endswith("$$")
        inline = fragment.startswith("$") and fragment.endswith("$")
        if display or inline:
            latex = fragment[2:-2] if display else fragment[1:-1]
            blocks.append({
                "id": f"{id_prefix}-{len(blocks) + 1}",
                "type": "math",
                "latex": latex.strip(),
                "display": display,
            })
        elif fragment.strip():
            blocks.append({
                "id": f"{id_prefix}-{len(blocks) + 1}",
                "type": "text",
                "text": fragment,
            })
    return blocks


def build_question_content_blocks(
    payload: dict[str, Any],
    source_block: str,
    source_images: list[str],
) -> list[dict[str, Any]]:
    """Build the canonical ordered representation used by persistence and UI."""
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
        image_blocks.append((
            source_block.find(reference),
            {
                "id": f"stem-image-{index + 1}",
                "type": "image",
                "url": url,
                "assetId": Path(url).stem,
                "sourceReference": reference,
                "role": "stem",
            },
        ))

    option_items = []
    for index, option in enumerate(options):
        label = f"({chr(65 + index)})"
        clean_option = re.sub(rf"^(?:\({chr(65 + index)}\)|{chr(65 + index)}[.:：、])\s*", "", option).strip()
        item: dict[str, Any] = {
            "label": label,
            "contentBlocks": rich_text_blocks(clean_option, f"option-{index + 1}"),
        }
        if index < len(option_image_urls):
            item["imageUrl"] = option_image_urls[index]
            item["assetId"] = Path(option_image_urls[index]).stem
        option_items.append(item)

    options_block = {
        "id": "options",
        "type": "options",
        "items": option_items,
    } if option_items else None
    first_option_position = source_block.find("(A)")
    before_options = [block for position, block in image_blocks if first_option_position < 0 or position < first_option_position]
    after_options = [block for position, block in image_blocks if first_option_position >= 0 and position >= first_option_position]
    blocks.extend(before_options)
    if options_block:
        blocks.append(options_block)
    blocks.extend(after_options)
    for source_order, block in enumerate(blocks):
        block["sourceOrder"] = source_order
    return blocks


def validate_question_payload(
    payload: dict[str, Any],
    source_block: str,
    source_images: list[str],
) -> dict[str, Any]:
    """Apply deterministic structural invariants before a question is published."""
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
    source_labels = re.findall(r"\(([A-D])\)", source_block)
    has_four_choices = all(label in source_labels for label in ("A", "B", "C", "D"))
    if has_four_choices and len(options) != 4:
        errors.append(f"原题包含 A-D，但结构化选项数为 {len(options)}")
    for index, option in enumerate(options):
        label_only = bool(re.fullmatch(r"(?:\([A-H]\)|[A-H][.:：、]?)", option))
        if (not option or label_only) and index >= len(option_images):
            errors.append(f"选项 {chr(65 + index)} 缺少内容或图片")
    if options and re.search(r"\(A\)", str(question.get("prompt", ""))):
        errors.append("题干中重复包含结构化选项")

    content_blocks = question.get("contentBlocks", [])
    if not content_blocks:
        errors.append("缺少 contentBlocks")
    block_images = [
        Path(str(block.get("url", ""))).name
        for block in content_blocks
        if block.get("type") == "image"
    ]
    block_option_images = [
        Path(str(item.get("imageUrl", ""))).name
        for block in content_blocks if block.get("type") == "options"
        for item in block.get("items", []) if item.get("imageUrl")
    ]
    if block_images + block_option_images != actual_images:
        errors.append("contentBlocks 中的图片顺序与题目图片不一致")

    math_blocks = [
        str(block.get("latex", ""))
        for block in content_blocks if block.get("type") == "math"
    ] + [
        str(inner.get("latex", ""))
        for block in content_blocks if block.get("type") == "options"
        for item in block.get("items", [])
        for inner in item.get("contentBlocks", []) if inner.get("type") == "math"
    ]
    for index, latex in enumerate(math_blocks, start=1):
        if not latex:
            errors.append(f"第 {index} 个公式为空")
            continue
        if formula_anomaly_score(f"${latex}$"):
            errors.append(f"第 {index} 个公式仍含 OCR/控制字符异常")
        if latex.count("{") != latex.count("}"):
            errors.append(f"第 {index} 个公式花括号不平衡")
        begins = re.findall(r"\\begin\{([^}]+)\}", latex)
        ends = re.findall(r"\\end\{([^}]+)\}", latex)
        if begins != ends or re.search(r"\\(?:begin|end)(?!\{)", latex):
            errors.append(f"第 {index} 个公式环境不完整")

    if not str(question.get("prompt", "")).strip():
        errors.append("题干为空")
    if not source_block.strip():
        warnings.append("缺少 OCR 原始题块，无法进行来源覆盖校验")
    return {
        "status": "ready" if not errors else "needs_review",
        "errors": errors,
        "warnings": warnings,
        "validatorVersion": "p0-v1",
        "validatedAt": time.time(),
    }


def apply_question_quality_gate(
    payload: dict[str, Any],
    source_block: str,
    source_images: list[str],
) -> dict[str, Any]:
    question = payload["question"]
    question["contentBlocks"] = build_question_content_blocks(payload, source_block, source_images)
    question["sourceEvidence"] = {
        "questionNumber": question.get("questionNumber", ""),
        "sourceHash": hashlib.sha256(source_block.encode("utf-8")).hexdigest(),
        "imageReferences": list(source_images),
    }
    quality = validate_question_payload(payload, source_block, source_images)
    payload["quality"] = quality
    question["publicationStatus"] = quality["status"]
    if quality["errors"] and payload.get("review"):
        payload["review"]["status"] = "needs_review"
        payload["review"]["needsHumanReview"] = True
        payload["review"].setdefault("text", {}).setdefault("issues", []).extend(
            f"结构校验：{error}" for error in quality["errors"]
        )
    return quality


def generate_lesson(source_text: str) -> tuple[dict, list[dict[str, Any]], dict[str, Any]]:
    selection = runtime.selection
    source = source_text.strip()[:16_000]
    if not source:
        source = f"{QUESTION['prompt']}\n已知条件：{'；'.join(QUESTION['givens'])}"
    selected_number, selected_source, selected_images = select_complete_question_source(source)
    if selected_number:
        source = selected_source

    prompt = build_lesson_prompt(source)

    if selection.provider == "mock":
        run = mock_model_run()
        payload = question_payload(model_run=run)
        lesson_store[QUESTION["id"]] = {"payload": payload, "guideCards": GUIDE_CARDS}
        return payload, GUIDE_CARDS, run

    try:
        generated, run = runtime.generate_json(prompt, LESSON_SCHEMA, max_tokens=1600)
    except Exception as error:
        run = mock_model_run(selection.provider, str(error))
        payload = question_payload(model_run=run)
        lesson_store[QUESTION["id"]] = {"payload": payload, "guideCards": GUIDE_CARDS}
        return payload, GUIDE_CARDS, run

    question_id = f"generated-{hashlib.sha256(source.encode('utf-8')).hexdigest()[:12]}"
    question_type = safe_text(generated.get("questionType"), "short-answer", 30)
    if question_type not in {"choice", "true-false", "short-answer", "draw-line"}:
        question_type = "short-answer"
    generated_givens = safe_string_list(generated.get("givens"), [], 5)
    generated_givens = [
        given for given in generated_givens
        if "images/" not in given and "![" not in given
    ]
    generated_options = safe_string_list(generated.get("options"), [], 6) if generated.get("options") else []
    question_prompt = clean_question_stem(selected_number, selected_source) if selected_number else safe_text(generated.get("prompt"), QUESTION["prompt"], 4000)
    question_prompt = strip_choice_text_from_prompt(question_prompt, generated_options)
    question = {
        "id": question_id,
        "questionType": question_type,
        "chapter": safe_text(generated.get("chapter"), "教材练习", 80),
        "knowledgePoint": safe_text(generated.get("knowledgePoint"), "分步推理", 120),
        "questionNumber": selected_number or safe_text(generated.get("questionNumber"), "", 30),
        "prompt": question_prompt,
        "correctAnswer": safe_text(generated.get("correctAnswer"), "", 120),
        "interaction": normalize_question_interaction(generated.get("interaction"), question_type),
        "givens": generated_givens,
        "options": generated_options,
        "imageReferences": selected_images or (safe_string_list(generated.get("imageReferences"), [], 4) if generated.get("imageReferences") else []),
    }
    raw_steps = generated.get("lessonSteps") if isinstance(generated.get("lessonSteps"), list) else []
    lesson_steps = []
    for index in range(4):
        raw = raw_steps[index] if index < len(raw_steps) and isinstance(raw_steps[index], dict) else {}
        lesson_steps.append(
            {
                "id": f"model-step-{index + 1}",
                "title": safe_text(raw.get("title"), f"第 {index + 1} 步", 80),
                "text": safe_text(raw.get("text"), "根据题目条件继续推理。", 700),
                "speechText": safe_text(raw.get("speechText"), "我们继续看下一步。", 700),
                "action": CANVAS_ACTIONS[index],
            }
        )

    raw_cards = generated.get("guideCards") if isinstance(generated.get("guideCards"), list) else []
    guide_cards = []
    for index in range(3):
        fallback = {
            "stuckAt": "需要把题目条件转化为下一步操作。",
            "knowledge": [question["knowledgePoint"]],
            "hint": "先圈出题目明确给出的量，再判断当前能进行哪一步。",
            "question": "根据已知条件，你现在可以先写出什么关系？",
        }
        raw = raw_cards[index] if index < len(raw_cards) and isinstance(raw_cards[index], dict) else {}
        guide_cards.append(
            {
                "level": index,
                "stuckAt": safe_text(raw.get("stuckAt"), fallback["stuckAt"], 300),
                "knowledge": safe_string_list(raw.get("knowledge"), fallback["knowledge"]),
                "hint": safe_text(raw.get("hint"), fallback["hint"], 500),
                "question": safe_text(raw.get("question"), fallback["question"], 500),
                "canvasAction": CANVAS_ACTIONS[min(index + 1, 3)],
            }
        )

    payload = question_payload(question, lesson_steps, run)
    lesson_store[question_id] = {"payload": payload, "guideCards": guide_cards}
    return payload, guide_cards, run


def attach_question_source(
    payload: dict[str, Any],
    batch: dict[str, Any],
    ocr_run: dict[str, Any],
    source_image_references: list[str] | None = None,
) -> None:
    question = payload["question"]
    question["sourceBatchId"] = batch["id"]
    question["sourcePages"] = {
        "start": ocr_run.get("startPage", batch["startPage"]),
        "end": ocr_run.get("endPage", batch["endPage"]),
    }
    question["sourceArtifactUrl"] = ocr_run.get("sourceArtifactUrl")
    question["promptArtifactUrl"] = ocr_run.get("promptArtifactUrl")
    available_images = [
        url for url in ocr_run.get("imageUrls", [])
        if isinstance(url, str) and url.startswith("/api/uploads/")
    ]
    references = source_image_references or question.pop("imageReferences", [])
    available_by_name = {Path(url).name: url for url in available_images}
    # Preserve the order in which MinerU references images in the question.
    # Sorting asset filenames changes A/B/C/D into an unrelated visual order.
    matched_images = [
        available_by_name[Path(reference).name]
        for reference in references
        if Path(reference).name in available_by_name
    ]
    # A question without an image reference must remain image-free. Falling
    # back to the first batch assets leaks images from earlier questions.
    question["imageUrls"] = matched_images


def review_lesson_payload(
    payload: dict[str, Any],
    lesson_source: str,
    asset_dir: Path | list[Path],
    guide_cards: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    _number, question_block, _images = select_complete_question_source(lesson_source)
    if isinstance(asset_dir, list):
        image_paths = asset_dir
    else:
        image_paths = [
            asset_dir / Path(url).name
            for url in payload["question"].get("imageUrls", [])
            if (asset_dir / Path(url).name).is_file()
        ]
    reviewed_payload, review_run = runtime_reviewer.review(
        payload,
        question_block,
        image_paths,
    )
    lesson_store[reviewed_payload["question"]["id"]] = {
        "payload": reviewed_payload,
        "guideCards": guide_cards,
    }
    return reviewed_payload, review_run


def generate_model_reply(request: HelpRequest) -> TutorReply:
    stored = lesson_store.get(request.questionId)
    if stored and request.mode == "answer":
        question = stored["payload"].get("question", {})
        if question.get("questionType") == "true-false" and question.get("correctAnswer"):
            expected = str(question["correctAnswer"]).strip().lower()
            submitted = request.studentInput.strip().lower()
            selected = "正确" if "正确" in submitted or "true" in submitted else "错误" if "错误" in submitted or "false" in submitted else ""
            if selected:
                is_correct = selected.lower() == expected
                assessment = "correct" if is_correct else "incorrect"
                reply = (
                    f"回答正确，答案是“{question['correctAnswer']}”。请再说说题干中的哪个条件支持这个判断。"
                    if is_correct else
                    f"这次选择不对，正确答案是“{question['correctAnswer']}”。请回到题干，找出能验证这句话的条件。"
                )
                return TutorReply(
                    reply=reply,
                    guideContext={
                        "assessment": assessment,
                        "stuckAt": "需要根据题干条件判断命题真伪。",
                        "knowledge": [question.get("knowledgePoint", "概念判断")],
                        "hint": "圈出题干中的关键条件，再逐项核对命题。",
                        "question": "题干中的哪条条件能支持你的判断？",
                    },
                    nextHintLevel=min(request.hintLevel + 1, 3),
                    canvasAction="show-base",
                    source="answer-check",
                    modelRun=mock_model_run(),
                )
    if stored and request.mode == "answer":
        question = stored["payload"].get("question", {})
        if question.get("questionType") == "draw-line":
            interaction = question.get("interaction") or {}
            required = {
                tuple(sorted(pair))
                for pair in interaction.get("requiredConnections", [])
                if isinstance(pair, list) and len(pair) == 2
            }
            submitted = {
                tuple(sorted(pair))
                for pair in request.interactionResult.get("connections", [])
                if isinstance(pair, list) and len(pair) == 2
            }
            if required:
                is_correct = required.issubset(submitted)
                assessment = "correct" if is_correct else "partial" if submitted else "incorrect"
                reply = (
                    "连接正确。请说明这条线段为什么满足题目要求。"
                    if is_correct else
                    "还差一点：检查是否连接了题目要求的两个端点，再试一次。"
                )
                return TutorReply(
                    reply=reply,
                    guideContext={
                        "assessment": assessment,
                        "stuckAt": "需要把题目中的几何关系落实为图上的连线。",
                        "knowledge": [question.get("knowledgePoint", "几何作图")],
                        "hint": interaction.get("instruction", "先找出题目要求连接的两个点。"),
                        "question": "你连接的线段对应题目中的哪条几何关系？",
                    },
                    nextHintLevel=min(request.hintLevel + 1, 3),
                    canvasAction="show-triangles",
                    source="answer-check",
                    modelRun=mock_model_run(),
                )
    if not stored or runtime.selection.provider == "mock":
        cards = stored["guideCards"] if stored else GUIDE_CARDS
        return build_reply(request, cards)

    payload = stored["payload"]
    cards = stored["guideCards"]
    current_card = cards[min(request.hintLevel, len(cards) - 1)]
    conflict = equation_conflict(
        request.studentInput,
        payload["lessonSteps"],
        payload["question"]["prompt"],
    )
    conflict_instruction = ""
    if conflict:
        conflict_instruction = (
            f"系统校验发现学生写的 {conflict[0]} 与标准步骤 {conflict[1]} 冲突。"
            "assessment 必须为 incorrect，绝对不能说这一步正确；只提示学生回查符号和算术。"
        )
    prompt = f"""
你正在辅导下面这道题。先用标准讲解脚本独立核对学生的每一步计算，再判断卡点。

题目：{payload['question']['prompt']}
已知条件：{'；'.join(payload['question']['givens'])}
标准讲解脚本：{json_dumps(payload['lessonSteps'])}
当前提示层级：{request.hintLevel}
候选引导卡：{json_dumps(current_card)}
学生输入：{request.studentInput.strip() or '学生没有输入内容'}
学生交互作答结果：{json_dumps(request.interactionResult) if request.interactionResult else '无'}
用户操作：{'提交回答并请求判题' if request.mode == 'answer' else '请求下一步提示'}
系统确定性校验：{conflict_instruction or '未发现同左边等式冲突，仍需自行核对。'}

要求：
1. assessment 必须是 correct、partial 或 incorrect。
2. 特别核对移项符号、算术和单位；只有确实正确时才能说“对”或表扬该步骤。
3. 如果错误，温和但明确指出哪一步不成立，然后给一个不泄露最终答案的提示。
4. 如果用户是请求提示，只引导下一步，不给最终答案；如果是提交回答，先明确判断再引导修改或继续。
5. reply 应像真人老师一样简短，最后提一个学生可以继续回答的问题。
""".strip()
    selection = runtime.selection
    try:
        generated, run = runtime.generate_json(prompt, HELP_SCHEMA, max_tokens=450)
    except Exception as error:
        return build_reply(
            request,
            cards,
            mock_model_run(selection.provider, str(error)),
        )

    action = generated.get("canvasAction")
    if action not in CANVAS_ACTIONS:
        action = current_card["canvasAction"]
    assessment = generated.get("assessment", "partial")
    reply_text = safe_text(generated.get("reply"), current_card["hint"], 1000)
    if conflict:
        assessment = "incorrect"
        reply_text = (
            f"这里需要再核对一下：你写的 {conflict[0]} 与前一步推导不一致。"
            "先别继续除法，请重新检查移项后的符号和右边的计算，你能重算这一行吗？"
        )
        action = current_card["canvasAction"]
    return TutorReply(
        reply=reply_text,
        guideContext={
            "assessment": assessment,
            "stuckAt": safe_text(generated.get("stuckAt"), current_card["stuckAt"], 300),
            "knowledge": safe_string_list(generated.get("knowledge"), current_card["knowledge"]),
            "hint": safe_text(generated.get("hint"), current_card["hint"], 500),
            "question": safe_text(generated.get("question"), current_card["question"], 500),
        },
        nextHintLevel=min(request.hintLevel + 1, 3),
        canvasAction=action,
        source="model-generated",
        modelRun=run,
    )


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def upload_job(upload_id: str) -> dict[str, Any]:
    job = pdf_uploads.get(upload_id)
    if not job:
        job = store.load_job(upload_id)
        if job:
            pdf_uploads[upload_id] = job
            for batch_id, payload in job.get("batchPayloads", {}).items():
                lesson_store[payload["question"]["id"]] = {
                    "payload": payload,
                    "guideCards": job.get("batchGuideCards", {}).get(batch_id) or GUIDE_CARDS,
                }
    if not job:
        raise HTTPException(status_code=404, detail="上传任务不存在")
    return job


def upload_status(job: dict[str, Any]) -> dict:
    uploaded = sorted(
        int(path.stem.split("-")[-1])
        for path in job["directory"].glob("chunk-*.part")
    )
    result = {
        "uploadId": job["uploadId"],
        "filename": job["filename"],
        "size": job["size"],
        "chunkSize": job["chunkSize"],
        "totalChunks": job["totalChunks"],
        "uploadedChunks": uploaded,
        "status": job["status"],
        "progress": job.get("progress", 0),
        "message": job.get("message", ""),
        "elapsedSeconds": round(
            ((job.get("completedAt") or time.time()) - job["startedAt"]),
            1,
        ),
    }
    if job.get("result"):
        result["result"] = job["result"]
    return result


def update_upload_job(
    job: dict[str, Any],
    status: str,
    progress: int,
    message: str,
) -> None:
    job["status"] = status
    job["progress"] = max(0, min(progress, 100))
    job["message"] = message
    job["updatedAt"] = time.time()
    store.save_job(job)


def validate_pdf_envelope(path: Path) -> None:
    """Reject truncated/non-PDF files before handing them to a PDF parser."""
    with path.open("rb") as pdf_file:
        header = pdf_file.read(8)
        pdf_file.seek(max(0, path.stat().st_size - PDF_TAIL_CHECK_BYTES))
        tail = pdf_file.read()

    if not header.startswith(b"%PDF-"):
        raise ValueError("文件头不是有效的 PDF（缺少 %PDF- 标记）")
    if b"%%EOF" not in tail:
        size_mb = path.stat().st_size / 1024 / 1024
        raise ValueError(
            "文件缺少 PDF 结束标记（%%EOF）。"
            f"分块已完整合并为 {size_mb:.1f} MB，因此原 PDF 很可能下载不完整或导出中断；"
            "请重新下载，或用系统的“打印 → 存储为 PDF”生成新文件后重试"
        )


@app.get("/api/health")
def health() -> dict[str, str]:
    if not store.ping():
        raise HTTPException(status_code=503, detail="数据库不可用")
    return {"status": "ok", "database": store.backend}


@app.get("/api/tts/status")
def tts_status() -> dict[str, Any]:
    """Report whether the optional local Qwen3-TTS service is reachable."""
    if TTS_PROVIDER in {"auto", "azure"} and AZURE_SPEECH_KEY and AZURE_SPEECH_REGION:
        return {
            "provider": "azure-speech-neural",
            "available": True,
            "voice": AZURE_SPEECH_VOICE,
            "detail": "Azure Speech Neural 已配置",
        }
    if TTS_PROVIDER == "azure":
        return {
            "provider": "browser",
            "available": False,
            "detail": "缺少 AZURE_SPEECH_KEY 或 AZURE_SPEECH_REGION",
        }
    try:
        with urllib.request.urlopen(f"{QWEN_TTS_URL}/health", timeout=1) as response:
            data = response.read().decode("utf-8")
        return {"provider": "qwen3-tts", "available": True, "detail": data}
    except (OSError, urllib.error.URLError):
        return {"provider": "browser", "available": False, "detail": "Qwen3-TTS 服务未启动，前端将回退到浏览器语音"}


def synthesize_azure_tts(request: TtsRequest) -> Response:
    ssml = (
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="zh-CN">'
        f'<voice name="{html.escape(AZURE_SPEECH_VOICE, quote=True)}">'
        f'<prosody rate="-5%">{html.escape(request.text)}</prosody>'
        "</voice></speak>"
    ).encode("utf-8")
    azure_request = urllib.request.Request(
        f"https://{AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml,
        headers={
            "Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
            "User-Agent": "dotty-tutor",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(azure_request, timeout=30) as response:
            audio = response.read()
    except (OSError, urllib.error.URLError) as error:
        raise HTTPException(status_code=503, detail=f"Azure Speech 调用失败：{error}") from error
    return Response(content=audio, media_type="audio/mpeg")


@app.post("/api/tts")
def synthesize_tts(request: TtsRequest) -> Response:
    """Proxy local Qwen3-TTS audio while keeping the browser on the same origin."""
    if TTS_PROVIDER in {"auto", "azure"} and AZURE_SPEECH_KEY and AZURE_SPEECH_REGION:
        return synthesize_azure_tts(request)
    if TTS_PROVIDER == "azure":
        raise HTTPException(status_code=503, detail="Azure Speech 未配置，请设置 AZURE_SPEECH_KEY 和 AZURE_SPEECH_REGION")
    body = json_dumps({
        "text": request.text,
        "speaker": request.speaker,
        "instruct": request.instruct,
    }).encode("utf-8")
    proxy_request = urllib.request.Request(
        f"{QWEN_TTS_URL}/tts",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(proxy_request, timeout=180) as response:
            audio = response.read()
    except (OSError, urllib.error.URLError) as error:
        raise HTTPException(status_code=503, detail=f"Qwen3-TTS 暂不可用：{error}") from error
    return Response(content=audio, media_type="audio/wav")


@app.get("/api/models")
def get_models() -> dict[str, Any]:
    return runtime.catalog()


@app.post("/api/models/select")
def select_model(request: ModelSelectionRequest) -> dict[str, Any]:
    try:
        return runtime.select(request.provider, request.model)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/ocr")
def get_ocr_providers() -> dict[str, Any]:
    return ocr_runtime.catalog()


@app.post("/api/ocr/select")
def select_ocr_provider(request: OcrSelectionRequest) -> dict[str, Any]:
    try:
        return ocr_runtime.select(request.provider)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.get("/api/question")
def get_question() -> dict:
    return question_payload()


@app.post("/api/textbook/import")
async def import_textbook(
    file: UploadFile = File(...),
    sourceText: str = Form(default="", max_length=20_000),
) -> dict:
    """读取一页教材并返回结构化结果；轻量导入路径不持久化原文件。"""
    filename = Path(file.filename or "textbook-page").name
    suffix = Path(filename).suffix.lower()
    content_type = (file.content_type or "").lower()
    is_supported_mime = content_type.startswith("image/") or content_type == "application/pdf"
    is_supported_suffix = suffix in ALLOWED_UPLOAD_SUFFIXES
    if not is_supported_mime and not is_supported_suffix:
        shown_type = content_type or "未提供 MIME"
        raise HTTPException(
            status_code=415,
            detail=f"不支持这个文件：{filename}（{shown_type}）。请上传图片或 PDF。",
        )

    content = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件不能为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="教材页不能超过 10 MB")

    extracted_text = ""
    if suffix == ".pdf" or content_type == "application/pdf":
        try:
            reader = PdfReader(BytesIO(content))
            extracted_text = extract_pdf_text(reader)
        except Exception as error:
            raise HTTPException(status_code=422, detail=f"PDF 无法解析：{error}") from error

    if not sourceText.strip() and ocr_runtime.should_use_mineru():
        with tempfile.TemporaryDirectory(prefix="dotty-ocr-input-") as input_dir:
            source_path = Path(input_dir) / filename
            source_path.write_bytes(content)
            lesson_source, ocr_run = resolve_ocr_text(sourceText, extracted_text, source_path)
    else:
        lesson_source, ocr_run = resolve_ocr_text(sourceText, extracted_text)
    payload, guide_cards, model_run = generate_lesson(lesson_source)
    digest = hashlib.sha256(content).hexdigest()
    return {
        "importId": f"page-{digest[:12]}",
        "filename": filename,
        "contentType": content_type or "application/octet-stream",
        "size": len(content),
        "stored": False,
        "modelRun": model_run,
        "ocrRun": ocr_run,
        "stages": [
            {"id": "upload", "label": "文件校验", "status": "done"},
            {"id": "ocr", "label": "MinerU OCR / 文字层解析", "status": "done"},
            {"id": "structure", "label": "题目结构化", "status": "done"},
            {"id": "guides", "label": "引导卡生成", "status": "done"},
        ],
        "extraction": {
            "chapter": payload["question"]["chapter"],
            "knowledgePoint": payload["question"]["knowledgePoint"],
            "questionCount": 1,
            "formulaCount": 2,
            "guideCardCount": len(guide_cards),
            "confidence": 0.96,
            "mode": f"model-from-{ocr_run['provider']}" if lesson_source else "demo-seed-no-ocr",
        },
        "questionPayload": payload,
    }


@app.post("/api/uploads/init")
def init_pdf_upload(request: PdfUploadInitRequest) -> dict:
    filename = Path(request.filename).name
    if Path(filename).suffix.lower() != ".pdf" and request.contentType != "application/pdf":
        raise HTTPException(status_code=415, detail="分块上传目前只用于 PDF")
    expected_chunks = math.ceil(request.size / request.chunkSize)
    if expected_chunks != request.totalChunks:
        raise HTTPException(status_code=400, detail="文件大小与分块数量不一致")

    upload_id = uuid.uuid4().hex
    directory = UPLOAD_ROOT / upload_id
    directory.mkdir(parents=True, exist_ok=False)
    job: dict[str, Any] = {
        "uploadId": upload_id,
        "filename": filename,
        "contentType": request.contentType,
        "size": request.size,
        "chunkSize": request.chunkSize,
        "totalChunks": request.totalChunks,
        "sourceText": request.sourceText,
        "directory": directory,
        "status": "uploading",
        "progress": 0,
        "message": "等待上传 PDF 分块",
        "startedAt": time.time(),
        "updatedAt": time.time(),
        "result": None,
    }
    pdf_uploads[upload_id] = job
    store.save_job(job)
    return upload_status(job)


@app.put("/api/uploads/{upload_id}/chunks/{index}")
async def upload_pdf_chunk(upload_id: str, index: int, request: Request) -> dict:
    job = upload_job(upload_id)
    if job["status"] == "complete":
        raise HTTPException(status_code=409, detail="这个上传任务已经完成")
    if index < 0 or index >= job["totalChunks"]:
        raise HTTPException(status_code=400, detail="分块序号超出范围")

    content = await request.body()
    expected_size = job["chunkSize"]
    if index == job["totalChunks"] - 1:
        expected_size = job["size"] - job["chunkSize"] * index
    if len(content) != expected_size:
        raise HTTPException(
            status_code=400,
            detail=f"第 {index + 1} 块大小不正确：收到 {len(content)}，应为 {expected_size}",
        )

    chunk_path = job["directory"] / f"chunk-{index:06d}.part"
    chunk_path.write_bytes(content)
    uploaded_count = len(list(job["directory"].glob("chunk-*.part")))
    update_upload_job(
        job,
        "uploading",
        round(uploaded_count / job["totalChunks"] * 20),
        f"已上传 {uploaded_count}/{job['totalChunks']} 个分块",
    )
    return {
        "uploadId": upload_id,
        "index": index,
        "received": len(content),
        "uploadedCount": uploaded_count,
    }


@app.get("/api/uploads/{upload_id}/status")
def get_pdf_upload_status(upload_id: str) -> dict:
    return upload_status(upload_job(upload_id))


@app.post("/api/uploads/{upload_id}/complete")
def complete_pdf_upload(upload_id: str) -> dict:
    job = upload_job(upload_id)
    if job["status"] == "complete" and job.get("result"):
        return job["result"]

    chunk_paths = [
        job["directory"] / f"chunk-{index:06d}.part"
        for index in range(job["totalChunks"])
    ]
    missing = [index for index, path in enumerate(chunk_paths) if not path.exists()]
    if missing:
        raise HTTPException(status_code=409, detail=f"仍缺少 {len(missing)} 个分块")

    update_upload_job(job, "merging", 22, "正在合并分块并计算文件校验值")
    source_path = job["directory"] / "source.pdf"
    digest = hashlib.sha256()
    written = 0
    with source_path.open("wb") as merged:
        for chunk_path in chunk_paths:
            with chunk_path.open("rb") as chunk:
                while block := chunk.read(1024 * 1024):
                    merged.write(block)
                    digest.update(block)
                    written += len(block)
    if written != job["size"]:
        update_upload_job(job, "failed", 22, "合并后的 PDF 大小校验失败")
        raise HTTPException(status_code=400, detail="合并后的 PDF 大小校验失败")

    update_upload_job(job, "validating", 28, "文件合并完成，正在读取 PDF 页数")
    try:
        validate_pdf_envelope(source_path)
        try:
            page_count = ocr_runtime.page_count(source_path)
        except Exception:
            page_count = len(PdfReader(str(source_path)).pages)
        if page_count == 0:
            raise ValueError("PDF 没有页面")
    except Exception as error:
        update_upload_job(job, "failed", 28, f"PDF 校验失败：{error}")
        raise HTTPException(status_code=422, detail=f"PDF 无法解析：{error}") from error

    update_upload_job(job, "splitting", 35, f"校验完成，共 {page_count} 页；正在规划处理批次")
    batches = []
    for start in range(0, page_count, PDF_BATCH_PAGES):
        end = min(start + PDF_BATCH_PAGES, page_count)
        batch_id = len(batches) + 1
        batches.append(
            {
                "id": f"batch-{batch_id:03d}",
                "startPage": start + 1,
                "endPage": end,
                "pageCount": end - start,
                "status": "processed" if batch_id == 1 else "queued",
            }
        )

    update_upload_job(
        job,
        "splitting",
        50,
        f"已规划 {len(batches)} 个批次；无需复制整本 PDF",
    )

    for chunk_path in chunk_paths:
        chunk_path.unlink(missing_ok=True)

    # MinerU 直接通过 --start/--end 读取原 PDF 的前五页。其余批次只保存页码范围，
    # 不再用 pypdf 打开整本教材或复制页面，避免图片型 PDF 在分页阶段等待一分钟。
    first_batch = batches[0]
    extracted_text = ""
    if not job.get("sourceText", "").strip() and not ocr_runtime.should_use_mineru():
        try:
            extracted_text = extract_pdf_text(PdfReader(str(source_path)), max_pages=PDF_BATCH_PAGES)
        except Exception:
            extracted_text = ""

    preview_pages = first_batch["pageCount"]
    update_upload_job(
        job,
        "ocr",
        55,
        f"MinerU 正在识别首批 {preview_pages} 页；整本 {page_count} 页无需等待",
    )
    lesson_source, ocr_run = resolve_ocr_text(
        job.get("sourceText", ""),
        extracted_text,
        source_path,
        0,
        preview_pages - 1,
        job["directory"] / "assets" / first_batch["id"],
        f"/api/uploads/{upload_id}/assets/{first_batch['id']}",
    )
    update_upload_job(job, "generating", 88, "首批内容已提取，正在按题号拆分并生成课程")
    question_sources = limited_question_sources(lesson_source)
    asset_dir = job["directory"] / "assets" / first_batch["id"]
    write_model_prompt_artifact(asset_dir, question_sources)
    ocr_run["sourceArtifactUrl"] = f"/api/uploads/{upload_id}/artifacts/{first_batch['id']}/source.md"
    ocr_run["promptArtifactUrl"] = f"/api/uploads/{upload_id}/artifacts/{first_batch['id']}/model-prompt.md"
    payloads, guide_cards_list, model_runs, review_runs = process_question_sources(
        question_sources,
        first_batch,
        ocr_run,
        asset_dir,
        job,
    )
    payload = payloads[0]
    guide_cards = guide_cards_list[0]
    model_run = model_runs[0]
    review_run = review_runs[0]
    question_keys = [payload["question"]["sourceQuestionKey"] for payload in payloads]
    result = {
        "uploadId": upload_id,
        "importId": f"pdf-{digest.hexdigest()[:12]}",
        "filename": job["filename"],
        "contentType": "application/pdf",
        "size": job["size"],
        "stored": True,
        "temporary": True,
        "modelRun": model_run,
        "modelRuns": model_runs,
        "ocrRun": ocr_run,
        "reviewRun": review_run,
        "reviewRuns": review_runs,
        "stages": [
            {"id": "upload", "label": "分块上传", "status": "done"},
            {"id": "merge", "label": "PDF 合并与校验", "status": "done"},
            {"id": "split", "label": "按页规划批次", "status": "done"},
            {"id": "ocr", "label": f"首批 {preview_pages} 页 MinerU OCR", "status": "done"},
            {"id": "guides", "label": "结构化与引导卡", "status": "done"},
        ],
        "extraction": {
            "chapter": payload["question"]["chapter"],
            "knowledgePoint": payload["question"]["knowledgePoint"],
            "questionCount": len(payloads),
            "questionLimit": MAX_QUESTIONS_PER_BATCH,
            "formulaCount": sum(block.count("$") // 2 for _, block, _ in question_sources),
            "guideCardCount": sum(len(cards) for cards in guide_cards_list),
            "pageCount": page_count,
            "batchCount": len(batches),
            "confidence": 0.96,
            "mode": f"model-from-{ocr_run['provider']}" if lesson_source else "demo-seed-no-ocr",
        },
        "batches": batches,
        "questionPayload": payload,
        "questionPayloads": payloads,
        "batchQuestionKeys": {first_batch["id"]: question_keys},
    }
    job["batchPayloads"] = dict(zip(question_keys, payloads))
    job["batchGuideCards"] = dict(zip(question_keys, guide_cards_list))
    job["batchQuestionKeys"] = {first_batch["id"]: question_keys}
    job["result"] = result
    job["completedAt"] = time.time()
    store.save_questions(
        upload_id,
        list(zip(question_keys, payloads, guide_cards_list)),
    )
    update_upload_job(job, "complete", 100, f"首批 {preview_pages} 页已拆分为 {len(payloads)} 道题，其余批次可按需处理")
    return result


@app.post("/api/help", response_model=TutorReply)
def get_help(request: HelpRequest) -> TutorReply:
    return generate_model_reply(request)


@app.post("/api/uploads/{upload_id}/batches/{batch_id}/process")
def process_pdf_batch(upload_id: str, batch_id: str, force: bool = False) -> dict:
    """OCR one queued five-page range and add its generated exercise to the bank."""
    job = upload_job(upload_id)
    result = job.get("result")
    if job.get("status") != "complete" or not result:
        raise HTTPException(status_code=409, detail="请先完成教材首批处理")

    batch = next((item for item in result.get("batches", []) if item["id"] == batch_id), None)
    if not batch:
        raise HTTPException(status_code=404, detail="没有找到这个教材批次")

    batch_question_keys = job.setdefault("batchQuestionKeys", {}).get(batch_id, [])
    stored_payloads = [job.setdefault("batchPayloads", {})[key] for key in batch_question_keys if key in job["batchPayloads"]]
    stored_payload = stored_payloads[0] if stored_payloads else job.setdefault("batchPayloads", {}).get(batch_id)
    if stored_payload and not force:
        return {
            "batch": batch,
            "questionPayload": stored_payload,
            "questionPayloads": stored_payloads or [stored_payload],
            "ocrRun": result["ocrRun"],
            "modelRun": stored_payload["modelRun"],
        }

    processing = job.setdefault("processingBatches", set())
    if batch_id in processing:
        raise HTTPException(status_code=409, detail="这个批次正在处理中")
    processing.add(batch_id)
    try:
        source_path = job["directory"] / "source.pdf"
        start_page = batch["startPage"] - 1
        end_page = batch["endPage"] - 1
        ocr_start_page = max(0, start_page - 1)
        asset_dir = job["directory"] / "assets" / batch_id
        cached_markdown = asset_dir / "source.md"
        extracted_text = ""
        if not ocr_runtime.should_use_mineru():
            reader = PdfReader(str(source_path))
            pages = []
            for page_index in range(ocr_start_page, end_page + 1):
                text = (reader.pages[page_index].extract_text() or "").strip()
                if text:
                    pages.append(text)
            extracted_text = "\n\n".join(pages)[:16_000]

        if cached_markdown.is_file():
            lesson_source = cached_markdown.read_text(encoding="utf-8", errors="replace")[:40_000]
            image_urls = [
                f"/api/uploads/{upload_id}/assets/{batch_id}/{path.name}"
                for path in sorted(asset_dir.iterdir())
                if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
            ]
            ocr_run = {
                "requestedProvider": ocr_runtime.selection.provider,
                "provider": "mineru",
                "mode": "persisted-markdown",
                "fallback": False,
                "output": "markdown",
                "startPage": ocr_start_page + 1,
                "endPage": end_page + 1,
                "imageUrls": image_urls,
            }
        else:
            lesson_source, ocr_run = resolve_ocr_text(
                "",
                extracted_text,
                source_path,
                ocr_start_page,
                end_page,
                asset_dir,
                f"/api/uploads/{upload_id}/assets/{batch_id}",
            )
        context_note = (
            f"\n\n[页码说明：识别内容来自第 {ocr_start_page + 1}-{end_page + 1} 页；"
            f"目标批次为第 {start_page + 1}-{end_page + 1} 页。前一页只用于补齐跨页题干。]\n"
        )
        question_sources = limited_question_sources(lesson_source)
        if not split_question_sources(lesson_source):
            question_sources = [("", context_note + lesson_source, MARKDOWN_IMAGE_PATTERN.findall(lesson_source))]
        write_model_prompt_artifact(asset_dir, question_sources)
        ocr_run["sourceArtifactUrl"] = f"/api/uploads/{upload_id}/artifacts/{batch_id}/source.md"
        ocr_run["promptArtifactUrl"] = f"/api/uploads/{upload_id}/artifacts/{batch_id}/model-prompt.md"
        payloads, guide_cards_list, model_runs, review_runs = process_question_sources(
            question_sources,
            batch,
            ocr_run,
            asset_dir,
            job,
        )
        payload = payloads[0]
        guide_cards = guide_cards_list[0]
        model_run = model_runs[0]
        review_run = review_runs[0]
        question_keys = [item["question"]["sourceQuestionKey"] for item in payloads]
        batch["status"] = "processed"
        store.save_questions(
            upload_id,
            list(zip(question_keys, payloads, guide_cards_list)),
        )
        for key, item, cards in zip(question_keys, payloads, guide_cards_list):
            job["batchPayloads"][key] = item
            job.setdefault("batchGuideCards", {})[key] = cards
        job.setdefault("batchQuestionKeys", {})[batch_id] = question_keys
        result.setdefault("batchQuestionKeys", {})[batch_id] = question_keys
        result.setdefault("questionPayloads", [])
        result["questionPayloads"] = [
            item for key in sorted(job["batchPayloads"]) for item in [job["batchPayloads"][key]]
        ]
        result["questionPayload"] = result["questionPayloads"][0]
        result["extraction"]["questionCount"] = len(result["questionPayloads"])
        result["extraction"]["guideCardCount"] = sum(
            len(cards) for cards in job.get("batchGuideCards", {}).values()
        )
        update_upload_job(
            job,
            "complete",
            100,
            f"批次 {batch['startPage']}-{batch['endPage']} 页已更新 {len(payloads)} 道题",
        )
        return {
            "batch": batch,
            "questionPayload": payload,
            "questionPayloads": payloads,
            "ocrRun": ocr_run,
            "modelRun": model_run,
            "modelRuns": model_runs,
            "reviewRun": review_run,
            "reviewRuns": review_runs,
        }
    except HTTPException as error:
        batch["status"] = "failed"
        batch["error"] = str(error.detail)
        update_upload_job(job, "complete", 100, f"批次处理失败，已保留原题：{error.detail}")
        raise
    except Exception as error:
        batch["status"] = "failed"
        batch["error"] = str(error)
        update_upload_job(job, "complete", 100, f"批次处理失败，已保留原题：{error}")
        raise HTTPException(status_code=422, detail=f"批次处理失败：{error}") from error
    finally:
        processing.discard(batch_id)


@app.get("/api/uploads/{upload_id}/assets/{batch_id}/{filename}")
def get_pdf_asset(upload_id: str, batch_id: str, filename: str) -> FileResponse:
    job = upload_job(upload_id)
    asset_root = (job["directory"] / "assets" / batch_id).resolve()
    asset_path = (asset_root / Path(filename).name).resolve()
    if asset_path.parent != asset_root or not asset_path.is_file():
        raise HTTPException(status_code=404, detail="题目图片不存在")
    if asset_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(status_code=415, detail="不支持的题目资源类型")
    return FileResponse(asset_path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.get("/api/uploads/{upload_id}/artifacts/{batch_id}/{filename}")
def get_pdf_artifact(upload_id: str, batch_id: str, filename: str) -> FileResponse:
    job = upload_job(upload_id)
    artifact_root = (job["directory"] / "assets" / batch_id).resolve()
    safe_name = Path(filename).name
    if safe_name not in {"source.md", "model-prompt.md"}:
        raise HTTPException(status_code=404, detail="中间产物不存在")
    artifact_path = (artifact_root / safe_name).resolve()
    if artifact_path.parent != artifact_root or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="中间产物不存在")
    return FileResponse(
        artifact_path,
        media_type="text/markdown; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/library")
def list_textbook_library() -> dict:
    return {"items": store.list_imports()}


@app.get("/api/library/{upload_id}")
def get_textbook_library_item(upload_id: str) -> dict:
    job = upload_job(upload_id)
    result = job.get("result")
    if job.get("status") != "complete" or not result:
        raise HTTPException(status_code=409, detail="这本教材尚未处理完成")
    restored = dict(result)
    def question_sort_key(item: dict[str, Any]) -> tuple[int, str]:
        number = str(item.get("question", {}).get("questionNumber", ""))
        match = re.search(r"\d+", number)
        return (int(match.group()) if match else 10**9, number)

    payloads = sorted(job.get("batchPayloads", {}).values(), key=question_sort_key)
    restored["questionPayloads"] = payloads or [result["questionPayload"]]
    restored["questionPayload"] = restored["questionPayloads"][0]
    return restored
