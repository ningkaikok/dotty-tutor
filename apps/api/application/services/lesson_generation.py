"""独立于 HTTP 和上传路由的模型课程生成能力。

函数只接收文本/字典并返回可序列化数据。与 ``app.py`` 分离后，模型行为无需构造分块上传请求即可测试，
也能被未来的后台 Worker 直接复用。
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from application.services.legacy_question_adapter import project_question_ir
from application.services.stage_artifact_cache import StageArtifactCache
from application.services.staged_question_generation import normalize_stage
from application.services.tutor_engine import TutorEngine
from domain.questions.answer_solver import (
    check_answer_agreement,
    extract_solution_answer_text,
)
from domain.questions.contracts import (
    CANVAS_ACTIONS,
    GUIDE_CARDS,
    LESSON_STEPS,
    PERSONALIZED_ASSIGNMENT_PROMPT_VERSION,
    PERSONALIZED_ASSIGNMENT_SCHEMA,
    PERSONALIZED_ASSIGNMENT_SCHEMA_VERSION,
    QUESTION,
    HelpRequest,
    TutorReply,
)
from domain.questions.exam_ir import first_question_ir
from domain.questions.ir import bounded_confidence, stage_cache_key
from domain.questions.pipeline import (
    apply_question_quality_gate,
    audit_image_placeholders,
    build_personalized_assignment_prompt,
    clean_question_stem,
    normalize_model_math_text,
    normalize_question_interaction,
    normalize_text_choices_from_source,
    protect_image_references,
    strip_choice_text_from_prompt,
)
from domain.questions.source import (
    safe_string_list,
    safe_text,
    select_complete_question_source,
)
from domain.questions.staged_contracts import (
    QUESTION_EXTRACTION_SCHEMA,
    SOLUTION_SCHEMA,
    TUTOR_SCRIPT_SCHEMA,
    VERIFICATION_SCHEMA,
)
from domain.tutoring.checks import (
    generic_guide_cards,
    is_geometry_question,
    mock_model_run,
    normalize_guide_cards,
)
from infrastructure.runtime.model_runtime import runtime
from infrastructure.runtime.review_runtime import runtime_reviewer
from observability import log_event

# 该缓存仅加速单进程 Demo，PostgreSQL 才是持久化课程的真相来源。
# 多 Worker 部署应改用共享缓存或 Store，不能尝试在进程间同步这个字典。
lesson_store: dict[str, dict[str, Any]] = {}

STAGE_VERSIONS = {
    "extraction": ("question-extraction-v1", "question-ir-v1"),
    "solution": ("math-solver-v1", "solution-ir-v1"),
    "verification": ("answer-verifier-v1", "verification-ir-v1"),
    "tutor-script": ("tutor-script-v1", "tutor-script-v1"),
}
STAGE_CACHE_LIMIT = 128
_stage_artifact_cache: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}


def new_question_id(prefix: str, source: str) -> str:
    """为每次生成分配新的修订 ID。

    来源哈希仍用于来源证据和 ``sourceQuestionKey``，但不能充当题目主键；否则
    ``force=true`` 重新调用模型后会覆盖旧题并让前端误以为没有生成新版本。
    """
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{source_hash}-{uuid.uuid4().hex[:8]}"


def question_payload(
    question: dict[str, Any] | None = None,
    lesson_steps: list[dict[str, Any]] | None = None,
    model_run: dict[str, Any] | None = None,
) -> dict:
    """Build the stable frontend envelope used by live and Mock generation."""
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


def _normalized_blanks(generated: dict[str, Any]) -> list[dict[str, Any]]:
    blanks: list[dict[str, Any]] = []
    if not isinstance(generated.get("blanks"), list):
        return blanks
    for index, raw_blank in enumerate(generated["blanks"][:8], start=1):
        if not isinstance(raw_blank, dict):
            continue
        try:
            tolerance = max(0.0, float(raw_blank.get("tolerance", 0) or 0))
        except (TypeError, ValueError):
            tolerance = 0.0
        blanks.append({
            "id": safe_text(raw_blank.get("id"), f"blank-{index}", 24),
            "label": safe_text(raw_blank.get("label"), f"第 {index} 空", 30),
            "answerType": safe_text(raw_blank.get("answerType"), "text", 20),
            "correctAnswers": safe_string_list(raw_blank.get("correctAnswers"), [], 6),
            "tolerance": tolerance,
            "unit": safe_text(raw_blank.get("unit"), "", 20),
        })
    return blanks


def _normalized_answer_spec(generated: dict[str, Any]) -> dict[str, Any] | None:
    raw = generated.get("answerSpec")
    if not isinstance(raw, dict):
        return None
    try:
        tolerance = max(0.0, float(raw.get("tolerance", 0) or 0))
    except (TypeError, ValueError):
        tolerance = 0.0
    return {
        "answerType": safe_text(raw.get("answerType"), "numeric", 20),
        "expected": safe_text(raw.get("expected"), "", 120),
        "accepted": safe_string_list(raw.get("accepted"), [], 6),
        "tolerance": tolerance,
        "unit": safe_text(raw.get("unit"), "", 20),
    }


def _normalized_sub_questions(generated: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize optional multi-part questions without inventing answer keys.

    Each part keeps its own answer contract.  Tutor-only parts deliberately have no
    deterministic answer fields so the evaluator and mastery projection can abstain.
    """
    raw_parts = generated.get("subQuestions")
    if not isinstance(raw_parts, list):
        return []
    parts: list[dict[str, Any]] = []
    allowed_types = {"choice", "multi-select", "true-false", "short-answer", "fill-blank", "numeric", "draw-line"}
    for index, raw in enumerate(raw_parts[:12], start=1):
        if not isinstance(raw, dict):
            continue
        raw_evaluation = raw.get("evaluation")
        evaluation: dict[str, Any] = raw_evaluation if isinstance(raw_evaluation, dict) else {}
        mode_value = evaluation.get("mode")
        mode = mode_value if mode_value in {"deterministic", "tutor"} else "tutor"
        question_type = safe_text(raw.get("questionType"), "short-answer", 30)
        if question_type not in allowed_types:
            question_type = "short-answer"
        part: dict[str, Any] = {
            "id": safe_text(raw.get("id"), f"sub-question-{index}", 40),
            "label": safe_text(raw.get("label"), f"（{index}）", 20),
            "prompt": normalize_model_math_text(safe_text(raw.get("prompt"), "请完成这一小问。", 800)),
            "questionType": question_type,
            "evaluation": {
                "mode": mode,
                "reason": safe_text(evaluation.get("reason"), "", 160) or None,
            },
            "options": safe_string_list(raw.get("options"), [], 6),
            "correctAnswer": None,
            "correctAnswers": None,
            "blanks": None,
            "answerSpec": None,
            "interaction": raw.get("interaction") if isinstance(raw.get("interaction"), dict) else None,
            "contentBlocks": raw.get("contentBlocks") if isinstance(raw.get("contentBlocks"), list) else [],
        }
        if mode == "deterministic":
            part["correctAnswer"] = safe_text(raw.get("correctAnswer"), "", 120) or None
            part["correctAnswers"] = safe_string_list(raw.get("correctAnswers"), [], 6) or None
            part["blanks"] = _normalized_blanks(raw) or None
            part["answerSpec"] = _normalized_answer_spec(raw)
        parts.append(part)
    return parts


def _normalized_steps(generated: dict[str, Any], question: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    raw_steps_value = generated.get("lessonSteps")
    raw_steps: list[Any] = raw_steps_value if isinstance(raw_steps_value, list) else []
    steps: list[dict[str, Any]] = []
    for index in range(4):
        raw = raw_steps[index] if index < len(raw_steps) and isinstance(raw_steps[index], dict) else {}
        steps.append({
            "id": f"model-step-{index + 1}",
            "title": safe_text(raw.get("title"), f"第 {index + 1} 步", 80),
            # 题干和选项会经过统一的公式规范化；讲解步骤也必须走同一条路径，
            # 否则生产端会出现“题目能渲染、讲解仍显示 $...$”的分裂行为。
            "text": normalize_model_math_text(safe_text(raw.get("text"), "根据题目条件继续推理。", 700)),
            "speechText": normalize_model_math_text(safe_text(raw.get("speechText"), "我们继续看下一步。", 700)),
            # 目前只有几何题有具体画布动作；其他题保留统一的基础画布，
            # 避免历史几何样例污染普通数学题的讲解状态。
            "action": CANVAS_ACTIONS[index] if is_geometry_question(question) else "show-base",
        })
    return steps


def _normalized_guide_cards(
    generated: dict[str, Any],
    knowledge_point: str,
    question: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    raw_cards_value = generated.get("guideCards")
    raw_cards: list[Any] = raw_cards_value if isinstance(raw_cards_value, list) else []
    cards: list[dict[str, Any]] = []
    for index in range(3):
        fallback = {
            "stuckAt": "需要把题目条件转化为下一步操作。",
            "knowledge": [knowledge_point],
            "hint": "先圈出题目明确给出的量，再判断当前能进行哪一步。",
            "question": "根据已知条件，你现在可以先写出什么关系？",
        }
        raw = raw_cards[index] if index < len(raw_cards) and isinstance(raw_cards[index], dict) else {}
        cards.append({
            "level": index,
            "stuckAt": safe_text(raw.get("stuckAt"), fallback["stuckAt"], 300),
            "knowledge": safe_string_list(raw.get("knowledge"), fallback["knowledge"]),
            "hint": safe_text(raw.get("hint"), fallback["hint"], 500),
            "question": safe_text(raw.get("question"), fallback["question"], 500),
            "canvasAction": CANVAS_ACTIONS[min(index + 1, 3)] if is_geometry_question(question) else "show-base",
        })
    return normalize_guide_cards(cards, question)


def _fallback_lesson(
    source: str,
    selected_number: str,
    selected_source: str,
    selected_images: list[str],
    model_run: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """在模型不可用时保留 OCR 原题，而不是返回内置几何演示题。"""
    prompt = clean_question_stem(selected_number, selected_source) if selected_number else source[:4_000]
    question = {
        "id": new_question_id("fallback", source),
        "questionType": "short-answer",
        "chapter": "教材练习",
        "knowledgePoint": "待确认知识点",
        "questionNumber": selected_number,
        "prompt": normalize_model_math_text(prompt or "暂时无法生成题目，请重新尝试。"),
        "correctAnswer": "",
        "correctAnswers": [],
        "selectionMode": "single",
        "blanks": [],
        "answerSpec": None,
        "interaction": {"type": "none", "instruction": "", "points": [], "requiredConnections": []},
        "givens": [],
        # 先放一个占位，让已有 OCR 选项拆分器可以识别连续的 A-D 选项。
        "options": [""],
        "imageReferences": selected_images,
    }
    payload = question_payload(
        question,
        _normalized_steps({}, question),
        model_run,
    )
    # 复用已有的 OCR 选项拆分规则；即使没有完整结构化结果，也不要把 A-D 丢掉。
    normalize_text_choices_from_source(payload, selected_source or source)
    payload["question"]["prompt"] = normalize_model_math_text(strip_choice_text_from_prompt(
        payload["question"]["prompt"],
        payload["question"].get("options", []),
    ))
    if len(payload["question"].get("options", [])) >= 2:
        payload["question"]["questionType"] = "choice"
    cards = generic_guide_cards(payload["question"])
    return payload, cards


def _stage_prompt(stage: str, question_ir: dict[str, Any], *, repair_errors: list[str] | None = None) -> str:
    """为每个模型阶段构造独立提示词，只暴露该阶段需要的上下文。"""
    # provenance 中的 block 原文可能包含本地 images/... 路径；模型只需要稳定的
    # asset id，不能看到宿主路径后自行拼接或把路径写回题干。
    prompt_ir = dict(question_ir)
    prompt_ir.pop("sourceBlocks", None)
    source = str(question_ir.get("sourceText") or question_ir.get("stem") or "").strip()
    repair = ""
    if repair_errors:
        repair = "\n上一轮确定性校验发现的问题：\n" + "\n".join(
            f"- {str(error)[:180]}" for error in repair_errors[:8]
        )
    if stage == "extraction":
        return f"""你是试卷结构抽取器。只从下面这一道已切出的原题中提取结构，不求解，不解释，不补写缺失内容。
必须保留题干原文、题号、小问顺序、选项顺序和图片占位符；图片只记录来源中已有的引用。
题目来源证据：{question_ir.get('sourceBlockIds', [])}
图片证据：{question_ir.get('visualAssetIds', [])}

原题：
---
{source}
---{repair}""".strip()
    if stage == "solution":
        return f"""你是独立的数学题求解器。只根据已确认的 QuestionIR 求解，不改写题干，不生成新题。
如果来源不足以确定答案，返回空答案并保持题型；不要猜测。输出只包含答案契约和知识点。

QuestionIR：
---
{json.dumps(prompt_ir, ensure_ascii=False)}
---{repair}""".strip()
    if stage == "verification":
        return f"""你是独立答案核验器。对照原题来源和 SolutionIR 检查题干完整性、选项对齐、单位、公式和答案。
不要改写题目或答案。求解结论是否与来源答案等价由确定性程序另行核对，不是你的职责——
sourceAnswer 只需要如实抄录来源中出现的原始答案文字；来源没有印出答案就返回空字符串，
不要自己计算、推断或编造。题干不完整、选项对不上、单位/公式有问题、或你看到的来源答案
与解答明显矛盾时，必须返回 conflict 或 needs_review。

QuestionIR 与 SolutionIR：
---
{json.dumps(prompt_ir, ensure_ascii=False)}
---{repair}""".strip()
    return f"""你是教学脚本编排器。根据已确认的原题和独立求解结果，生成恰好 4 步讲解和 3 张递进提示卡。
不得改变题干、选项或标准答案；开场步骤不得提前泄露答案，提示卡只引导下一步。

QuestionIR 与 SolutionIR：
---
{json.dumps(prompt_ir, ensure_ascii=False)}
---{repair}""".strip()


def _build_verification(raw_verification: dict[str, Any], raw_solution: dict[str, Any]) -> dict[str, Any]:
    """把核验阶段的模型输出与确定性符号核对结果合并成持久化的 verification 结构。

    ``solverAgreement`` 不再是模型的自我断言，而是 ``answer_solver.check_answer_agreement``
    的判定结果：
    - disagree 会把 status 强制改成 conflict 并要求人工复核——这是确定性证据的否决权，
      模型说"一致"也压不住代码算出来的"不一致"；
    - agree/undecidable 都不会把模型给出的 needs_review 提升为 verified。"确定性核对
      没发现冲突"只覆盖了答案等价这一项，核验阶段其余检查（题干完整性、选项对齐、
      单位、公式）仍然只能靠模型或人工，代码没有证据去替它们背书，因此只做否决、
      不做提升——这是本仓库"确定性程序把关，模型提议"原则里更保守的那一半。
    """
    solver_check = check_answer_agreement(
        extract_solution_answer_text(raw_solution),
        raw_verification.get("sourceAnswer"),
    )
    status = safe_text(raw_verification.get("status"), "needs_review", 20)
    conflicts = safe_string_list(raw_verification.get("conflicts"), [], 12)
    needs_human_review = bool(raw_verification.get("needsHumanReview", True))
    if solver_check["status"] == "disagree":
        status = "conflict"
        needs_human_review = True
        conflicts = (conflicts + [
            f"确定性符号核验：解答“{solver_check['candidate']}”与来源答案"
            f"“{solver_check['reference']}”不一致"
        ])[:12]
    return {
        "status": status,
        # undecidable（多数几何证明/开放题）一律记为 False，绝不能被当成"核验通过"。
        "solverAgreement": solver_check["status"] == "agree",
        "solverCheck": solver_check,
        "sourceAnswer": safe_text(raw_verification.get("sourceAnswer"), "", 160),
        "conflicts": conflicts,
        "checks": safe_string_list(raw_verification.get("checks"), [], 12),
        "confidence": bounded_confidence(raw_verification.get("confidence")),
        "needsHumanReview": needs_human_review,
    }


def _merge_stage_runs(stage_runs: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    """把三次调用汇总成兼容旧前端的 modelRun，同时保留阶段审计。"""
    last = dict(stage_runs[-1][1]) if stage_runs else mock_model_run()
    # tutor-script 可能因 verifier 门禁被跳过；顶层旧 modelRun 仍应代表实际模型调用，
    # 否则历史客户端会把一次成功的抽取/求解误显示成 provider=gate。
    for _name, candidate in reversed(stage_runs):
        if not candidate.get("skipped") and candidate.get("provider") != "gate":
            last = dict(candidate)
            break
    last["stages"] = [
        {
            "name": name,
            "provider": run.get("provider"),
            "model": run.get("model"),
            "fallback": bool(run.get("fallback")),
            "promptVersion": STAGE_VERSIONS[name][0],
            "schemaVersion": STAGE_VERSIONS[name][1],
            "cacheKey": run.get("cacheKey"),
            "cacheHit": bool(run.get("cacheHit")),
        }
        for name, run in stage_runs
    ]
    last["fallback"] = any(bool(run.get("fallback")) for _, run in stage_runs)
    return last


def _staged_lesson(
    source: str,
    *,
    repair_errors: list[str] | None = None,
    asset_dir: Path | None = None,
    target_stage: str | None = None,
    prior_stage_artifacts: dict[str, dict[str, Any]] | None = None,
    question_ir: dict[str, Any] | None = None,
    rerun_token: str | None = None,
) -> tuple[dict, list[dict[str, Any]], dict[str, Any]]:
    """执行“原题抽取 → 独立求解 → 教学脚本”三阶段生成。"""
    selection = runtime.selection
    selected_number, selected_source, selected_images = select_complete_question_source(source)
    question_ir = copy.deepcopy(question_ir) if question_ir is not None else first_question_ir(
        selected_source or source, asset_dir=asset_dir,
    )
    # 来源图片是确定性事实，不信任模型在 extraction 阶段返回的路径。
    question_ir["number"] = selected_number or question_ir.get("number", "")
    question_ir["visualAssetIds"] = [Path(image).name for image in selected_images]
    question_ir["sourceText"] = question_ir.get("sourceText") or selected_source or source
    protected_source, placeholder_context = protect_image_references(question_ir["sourceText"])
    question_ir["sourceText"] = protected_source

    stage_runs: list[tuple[str, dict[str, Any]]] = []
    raw_extraction: dict[str, Any] = {}
    raw_solution: dict[str, Any] = {}
    raw_script: dict[str, Any] = {}
    raw_verification: dict[str, Any] = {}
    stage_artifacts: dict[str, dict[str, Any]] = {}
    target_index = -1 if target_stage is None else list(STAGE_VERSIONS).index(normalize_stage(target_stage))
    effective_rerun_token = rerun_token or (uuid.uuid4().hex if target_index >= 0 else "")
    disk_cache = StageArtifactCache(asset_dir) if asset_dir is not None else None
    source_stable_id = str(question_ir.get("sourceStableId") or question_ir.get("sourceQuestionKey") or "source")
    for name, schema, max_tokens in (
        ("extraction", QUESTION_EXTRACTION_SCHEMA, 1100),
        ("solution", SOLUTION_SCHEMA, 1300),
        ("verification", VERIFICATION_SCHEMA, 900),
        ("tutor-script", TUTOR_SCRIPT_SCHEMA, 1500),
    ):
        prompt_ir = dict(question_ir)
        if name == "extraction":
            prompt_ir["sourceText"] = protected_source
        elif name == "solution":
            prompt_ir["extraction"] = raw_extraction
        elif name == "verification":
            prompt_ir["extraction"] = raw_extraction
            prompt_ir["solution"] = raw_solution
        else:
            prompt_ir["solution"] = raw_solution
            prompt_ir["extraction"] = raw_extraction
            prompt_ir["verification"] = raw_verification
        stage_prompt = _stage_prompt(name, prompt_ir, repair_errors=repair_errors)
        stage_index = list(STAGE_VERSIONS).index(name)
        cache_key = stage_cache_key(
            name,
            stage_prompt,
            provider=selection.provider,
            model=selection.model,
            prompt_version=STAGE_VERSIONS[name][0],
            schema_version=STAGE_VERSIONS[name][1],
            rerun_token=effective_rerun_token if target_index >= 0 and stage_index >= target_index else "",
        )
        if target_index >= 0 and stage_index < target_index:
            cached_prior = (prior_stage_artifacts or {}).get(name)
            if not cached_prior or not isinstance(cached_prior.get("raw"), dict):
                raise ValueError(f"缺少 {name} 阶段产物，无法只重跑 {target_stage}")
            raw = copy.deepcopy(cached_prior["raw"])
            run = {"provider": "artifact", "model": "stored", "fallback": False, "cacheHit": True, "stageReused": True}
        else:
            forced = target_index >= 0 and stage_index >= target_index
            cached = _stage_artifact_cache.get(cache_key) if not repair_errors and not forced else None
            if cached is None and disk_cache is not None and not repair_errors and not forced:
                disk_value = disk_cache.load(source_stable_id, name, cache_key)
                if disk_value and isinstance(disk_value.get("raw"), dict):
                    cached = (disk_value["raw"], disk_value.get("run") or {})
            if cached is not None:
                raw, run = copy.deepcopy(cached[0]), copy.deepcopy(cached[1])
                run["cacheHit"] = True
            elif name == "tutor-script" and raw_verification.get("status") != "verified":
                raw = {"lessonSteps": [], "guideCards": [], "schemaVersion": "tutor-script-v1"}
                run = {"provider": "gate", "model": "verification", "fallback": False, "skipped": True, "blockedBy": "verification"}
            else:
                raw, run = runtime.generate_json(
                    stage_prompt,
                    schema,
                    max_tokens=max_tokens,
                )
                if not run.get("fallback") and not repair_errors:
                    _stage_artifact_cache[cache_key] = (copy.deepcopy(raw), copy.deepcopy(run))
                    if disk_cache is not None:
                        disk_cache.save(
                            source_stable_id,
                            name,
                            cache_key,
                            {"raw": copy.deepcopy(raw), "run": copy.deepcopy(run)},
                        )
                    if len(_stage_artifact_cache) > STAGE_CACHE_LIMIT:
                        del _stage_artifact_cache[next(iter(_stage_artifact_cache))]
        run = dict(run)
        run["cacheKey"] = cache_key
        stage_runs.append((name, run))
        stage_artifacts[name] = {"raw": copy.deepcopy(raw), "run": copy.deepcopy(run), "cacheKey": cache_key}
        if name == "extraction":
            raw_extraction = raw
            # Older adapters return the original one-shot lesson schema. Keep that
            # compatibility path single-call so existing clients/tests do not turn a
            # legacy response into three meaningless downstream calls.
            if isinstance(raw, dict) and ("lessonSteps" in raw or ("prompt" in raw and "stem" not in raw)):
                legacy_question = {
                    "id": new_question_id("generated", selected_source or source),
                    "questionType": safe_text(raw.get("questionType"), "short-answer", 30),
                    "chapter": safe_text(raw.get("chapter"), "教材练习", 80),
                    "knowledgePoint": safe_text(raw.get("knowledgePoint"), "分步推理", 120),
                    "questionNumber": selected_number,
                    "prompt": normalize_model_math_text(safe_text(raw.get("prompt"), question_ir.get("stem") or source, 4_000)),
                    "correctAnswer": safe_text(raw.get("correctAnswer"), "", 120),
                    "correctAnswers": safe_string_list(raw.get("correctAnswers"), [], 8),
                    "selectionMode": "single",
                    "blanks": _normalized_blanks(raw),
                    "answerSpec": _normalized_answer_spec(raw),
                    "interaction": normalize_question_interaction(raw.get("interaction"), safe_text(raw.get("questionType"), "short-answer", 30)),
                    "givens": safe_string_list(raw.get("givens"), [], 8),
                    "options": safe_string_list(raw.get("options"), [], 8),
                    "subQuestions": _normalized_sub_questions(raw),
                    "imageReferences": selected_images,
                    "sourceProvenance": question_ir.get("sourceProvenance", {}),
                }
                legacy_run = dict(run)
                legacy_run["stages"] = [{"name": "legacy", "provider": run.get("provider"), "model": run.get("model"), "fallback": bool(run.get("fallback"))}]
                payload = question_payload(legacy_question, _normalized_steps(raw, legacy_question), legacy_run)
                payload["stageArtifacts"] = {"extraction": {"raw": copy.deepcopy(raw), "run": copy.deepcopy(run)}}
                payload["modelRun"]["imagePlaceholderAudit"] = audit_image_placeholders(
                    str(raw.get("prompt") or ""), placeholder_context,
                )
                cards = _normalized_guide_cards(raw, legacy_question["knowledgePoint"], legacy_question)
                lesson_store[legacy_question["id"]] = {"payload": payload, "guideCards": cards}
                return payload, cards, payload["modelRun"]
        elif name == "solution":
            raw_solution = raw
        elif name == "verification":
            raw_verification = raw
        else:
            raw_script = raw

    question_type = safe_text(raw_solution.get("questionType"), safe_text(raw_extraction.get("questionType"), "short-answer", 30), 30)
    if question_type not in {"choice", "multi-select", "true-false", "short-answer", "fill-blank", "numeric", "draw-line"}:
        question_type = "short-answer"
    options = safe_string_list(raw_extraction.get("options"), [], 8)
    if not options:
        options = safe_string_list(raw_solution.get("options"), [], 8)
    question = {
        "id": new_question_id("generated", selected_source or source),
        "questionType": question_type,
        "chapter": safe_text(raw_extraction.get("chapter"), safe_text(raw_solution.get("chapter"), "教材练习", 80), 80),
        "knowledgePoint": safe_text(raw_solution.get("knowledgePoint"), safe_text(raw_extraction.get("knowledgePoint"), "分步推理", 120), 120),
        "questionNumber": selected_number or safe_text(raw_extraction.get("questionNumber"), "", 30),
        # 原题复刻以 QuestionIR 为准；模型只负责结构标注，不得改写来源文字。
        "prompt": normalize_model_math_text(strip_choice_text_from_prompt(clean_question_stem(selected_number, selected_source or source), options)),
        "correctAnswer": safe_text(raw_solution.get("correctAnswer"), "", 120),
        "correctAnswers": safe_string_list(raw_solution.get("correctAnswers"), [], 8),
        "selectionMode": "multiple" if question_type == "multi-select" else "single",
        "blanks": _normalized_blanks(raw_solution),
        "answerSpec": _normalized_answer_spec(raw_solution),
        "interaction": normalize_question_interaction(raw_solution.get("interaction"), question_type),
        "givens": safe_string_list(raw_extraction.get("givens"), [], 8),
        "options": options,
        "subQuestions": _normalized_sub_questions(raw_extraction),
        "imageReferences": selected_images,
        "sourceProvenance": {
            "sourceQuestionKey": question_ir.get("sourceQuestionKey"),
            "sourcePages": question_ir.get("sourcePages", []),
            "sourceBlockIds": question_ir.get("sourceBlockIds", []),
            "visualAssetIds": question_ir.get("visualAssetIds", []),
            "confidence": question_ir.get("confidence", 0),
            "warnings": question_ir.get("warnings", []),
            "diagnostics": question_ir.get("diagnostics", {}),
            "sourceAnswerReference": question_ir.get("sourceAnswerReference"),
            "sourceOrigin": question_ir.get("sourceOrigin", "markdown-fallback"),
            "sourceBlocks": question_ir.get("sourceBlocks", []),
            "version": "question-ir-v1",
        },
        "verification": _build_verification(raw_verification, raw_solution),
    }
    payload = question_payload(question, _normalized_steps(raw_script, question), _merge_stage_runs(stage_runs))
    payload["stageArtifacts"] = stage_artifacts
    # 只审计模型是否保持占位符；真正的图片绑定稍后由来源证据重建。
    payload["modelRun"]["imagePlaceholderAudit"] = audit_image_placeholders(
        str(raw_extraction.get("stem") or raw_extraction.get("prompt") or raw_script.get("stem") or raw_script.get("prompt") or ""),
        placeholder_context,
    )
    cards = _normalized_guide_cards(raw_script, question["knowledgePoint"], question)
    lesson_store[question["id"]] = {"payload": payload, "guideCards": cards}
    return payload, cards, payload["modelRun"]


def write_staged_prompt_artifact(asset_dir: Path, question_sources: list[tuple[str, str, list[str]]]) -> Path:
    """写入本次实际使用的四阶段提示词，避免审计文件继续展示旧单提示词。"""
    asset_dir.mkdir(parents=True, exist_ok=True)
    sections = [
        "# OCR 后分阶段模型提示词\n",
        "> OCR 不使用自然语言提示词；以下是本次题块的阶段提示词基线，后续阶段运行时会追加前一阶段结构化结果。\n",
        "> 阶段版本：" + ", ".join(f"`{name}:{STAGE_VERSIONS[name][0]}`" for name in STAGE_VERSIONS) + "\n",
    ]
    for index, (number, block, images) in enumerate(question_sources, start=1):
        question_ir = first_question_ir(block)
        question_ir["number"] = number
        question_ir["visualAssetIds"] = [Path(image).name for image in images]
        protected, _context = protect_image_references(block)
        question_ir["sourceText"] = protected
        sections.append(f"\n## 第 {number or index} 题\n")
        for stage in ("extraction", "solution", "verification", "tutor-script"):
            sections.append(f"\n### {stage}\n\n```text\n{_stage_prompt(stage, question_ir)}\n```\n")
    path = asset_dir / "model-prompt.md"
    path.write_text("\n".join(sections), encoding="utf-8")
    return path


def generate_question_from_ir(
    question_ir: dict[str, Any],
    *,
    asset_dir: Path | None = None,
    target_stage: str | None = None,
    prior_stage_artifacts: dict[str, dict[str, Any]] | None = None,
    repair_errors: list[str] | None = None,
    rerun_token: str | None = None,
) -> tuple[dict, list[dict[str, Any]], dict[str, Any]]:
    """以 QuestionIR 为唯一来源事实生成题目；旧 ``generate_lesson`` 只是文本适配器。"""
    source = str(question_ir.get("sourceText") or question_ir.get("stem") or "").strip()
    payload, cards, run = _staged_lesson(
        source,
        repair_errors=repair_errors,
        asset_dir=asset_dir,
        target_stage=target_stage,
        prior_stage_artifacts=prior_stage_artifacts,
        question_ir=question_ir,
        rerun_token=rerun_token,
    )
    payload["question"] = project_question_ir(question_ir, payload.get("question", {}))
    payload["question"]["sourceQuestionKey"] = question_ir.get("sourceQuestionKey") or payload["question"].get("sourceQuestionKey")
    lesson_store[payload["question"]["id"]] = {"payload": payload, "guideCards": cards}
    return payload, cards, run


def generate_lesson(
    source_text: str,
    *,
    repair_errors: list[str] | None = None,
    asset_dir: Path | None = None,
    target_stage: str | None = None,
    prior_stage_artifacts: dict[str, dict[str, Any]] | None = None,
    rerun_token: str | None = None,
) -> tuple[dict, list[dict[str, Any]], dict[str, Any]]:
    """Generate one validated-shape lesson, optionally repairing known errors."""
    selection = runtime.selection
    started = time.perf_counter()
    log_event("model.generation.started", provider=selection.provider, model=selection.model)
    provided_source = source_text.strip()[:16_000]
    source = provided_source
    if not source:
        source = f"{QUESTION['prompt']}\n已知条件：{'；'.join(QUESTION['givens'])}"
    selected_number, selected_source, selected_images = select_complete_question_source(source)
    if selected_number:
        source = selected_source

    if selection.provider == "mock":
        run = mock_model_run()
        if not provided_source:
            payload = question_payload(model_run=run)
            cards = GUIDE_CARDS
        else:
            payload, cards = _fallback_lesson(source, selected_number, selected_source, selected_images, run)
        lesson_store[payload["question"]["id"]] = {"payload": payload, "guideCards": cards}
        log_event("model.generation.completed", provider="mock", duration_ms=round((time.perf_counter() - started) * 1000, 1))
        return payload, cards, run

    try:
        payload, cards, run = _staged_lesson(
            source,
            repair_errors=repair_errors,
            asset_dir=asset_dir,
            target_stage=target_stage,
            prior_stage_artifacts=prior_stage_artifacts,
            rerun_token=rerun_token,
        )
    except Exception as error:
        run = mock_model_run(selection.provider, str(error))
        payload, cards = _fallback_lesson(source, selected_number, selected_source, selected_images, run)
        lesson_store[payload["question"]["id"]] = {"payload": payload, "guideCards": cards}
        log_event(
            "model.generation.failed",
            level=40,
            provider=selection.provider,
            model=selection.model,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
            fallback=True,
            error_type=type(error).__name__,
            error=str(error)[:300],
            exc_info=True,
        )
        return payload, cards, run
    log_event(
        "model.generation.completed",
        provider=run.get("provider"),
        model=run.get("model"),
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
        question_type=payload["question"].get("questionType"),
    )
    return payload, cards, run


class PersonalizedLessonGenerationError(ValueError):
    """The batch generator failed closed and produced no assignable lessons."""


def _has_deterministic_answer(question: dict[str, Any]) -> bool:
    question_type = question.get("questionType")
    if question_type in {"choice", "multi-select"}:
        return bool(question.get("correctAnswers"))
    if question_type == "true-false":
        return str(question.get("correctAnswer") or "") in {"正确", "错误", "true", "false"}
    if question_type == "fill-blank":
        blanks = question.get("blanks")
        return bool(blanks) and all(item.get("correctAnswers") for item in blanks if isinstance(item, dict))
    if question_type == "numeric":
        spec = question.get("answerSpec")
        return bool(question.get("correctAnswer") or (isinstance(spec, dict) and (spec.get("expected") or spec.get("accepted"))))
    return False


def generate_personalized_lessons(
    context: dict[str, Any],
    question_count: int,
    *,
    model_runtime: Any = runtime,
) -> list[dict[str, Any]]:
    """Generate a whole-class batch once and reject unsafe model output.

    There is intentionally no deterministic fallback here: a fallback lesson
    would look like a successful personalized assignment to the teacher.
    """
    if not 1 <= question_count <= 5:
        raise PersonalizedLessonGenerationError("题目数量必须在 1 到 5 之间")
    prompt = build_personalized_assignment_prompt(context, question_count)
    try:
        raw, run = model_runtime.generate_json(
            prompt, PERSONALIZED_ASSIGNMENT_SCHEMA, max_tokens=2_800,
        )
    except Exception as error:  # noqa: BLE001
        raise PersonalizedLessonGenerationError("个性化作业模型不可用") from error
    if not isinstance(raw, dict) or not isinstance(raw.get("questions"), list):
        raise PersonalizedLessonGenerationError("个性化作业结果结构不完整")
    if not isinstance(run, dict) or run.get("fallback") or run.get("provider") in {"mock", "deterministic"}:
        raise PersonalizedLessonGenerationError("个性化作业不接受模型回退结果")
    questions = raw["questions"]
    if len(questions) != question_count:
        raise PersonalizedLessonGenerationError("模型返回的题目数量不符合请求")
    allowed_keys = {str(item.get("planningTopicKey")) for item in context.get("goals", [])}
    source_prompts = {
        str(item.get("prompt") or "").strip().casefold()
        for item in context.get("sourceExamples", [])
    }
    results: list[dict[str, Any]] = []
    for index, item in enumerate(questions, start=1):
        if not isinstance(item, dict) or item.get("planningTopicKey") not in allowed_keys:
            raise PersonalizedLessonGenerationError("模型引用了计划之外的主题")
        lesson = item.get("lesson")
        if not isinstance(lesson, dict):
            raise PersonalizedLessonGenerationError("模型返回的 lesson 不完整")
        question_type = str(lesson.get("questionType") or "")
        prompt_text = str(lesson.get("prompt") or "").strip()
        if not prompt_text or question_type not in {"choice", "multi-select", "true-false", "fill-blank", "numeric"}:
            raise PersonalizedLessonGenerationError("个性化题目缺少可判题结构")
        if prompt_text.casefold() in source_prompts:
            raise PersonalizedLessonGenerationError("个性化题目复制了来源题")
        lesson_id = new_question_id("personalized", f"{item['planningTopicKey']}:{prompt_text}:{index}")
        question = {
            "id": lesson_id,
            "questionType": question_type,
            "chapter": safe_text(lesson.get("chapter"), "班级个性化作业", 80),
            "knowledgePoint": safe_text(lesson.get("knowledgePoint"), item["planningTopicKey"], 120),
            "questionNumber": str(lesson.get("questionNumber") or index),
            "prompt": normalize_model_math_text(prompt_text),
            "correctAnswer": safe_text(lesson.get("correctAnswer"), "", 120),
            "correctAnswers": safe_string_list(lesson.get("correctAnswers"), [], 6),
            "selectionMode": "multiple" if question_type == "multi-select" else "single",
            "blanks": _normalized_blanks(lesson),
            "answerSpec": _normalized_answer_spec(lesson),
            "interaction": normalize_question_interaction(lesson.get("interaction"), question_type),
            "givens": safe_string_list(lesson.get("givens"), [], 5),
            "options": safe_string_list(lesson.get("options"), [], 6),
            "imageReferences": [],
            "subQuestions": [],
        }
        if not _has_deterministic_answer(question):
            raise PersonalizedLessonGenerationError("个性化题目答案不完整")
        payload = question_payload(question, _normalized_steps(lesson, question), run)
        cards = _normalized_guide_cards(lesson, question["knowledgePoint"], question)
        quality = apply_question_quality_gate(payload, prompt_text, [])
        if quality.get("status") != "ready":
            raise PersonalizedLessonGenerationError("个性化题目未通过质量门禁")
        metadata = {
            "sourcePlanId": context["sourcePlanId"],
            "sourcePublicationId": context["sourcePublicationId"],
            "planningTopicKey": item["planningTopicKey"],
            "evidenceRefs": list(next(goal for goal in context["goals"] if goal["planningTopicKey"] == item["planningTopicKey"]).get("evidenceRefs", [])),
            "promptVersion": PERSONALIZED_ASSIGNMENT_PROMPT_VERSION,
            "schemaVersion": PERSONALIZED_ASSIGNMENT_SCHEMA_VERSION,
            "difficulty": str(lesson.get("difficulty") or "按班级证据调整"),
        }
        question["personalization"] = metadata
        payload["personalization"] = metadata
        results.append({
            "lessonId": lesson_id,
            "title": f"个性化练习 · {question['knowledgePoint']}",
            "questionPayload": payload,
            "guideCards": cards,
            "metadata": metadata,
        })
    return results


def attach_question_source(
    payload: dict[str, Any],
    batch: dict[str, Any],
    ocr_run: dict[str, Any],
    source_image_references: list[str],
) -> None:
    """Attach source lineage without leaking images from adjacent questions."""
    question = payload["question"]
    question["sourceBatchId"] = batch["id"]
    provenance = question.get("sourceProvenance")
    provenance_pages = provenance.get("sourcePages") if isinstance(provenance, dict) else None
    if isinstance(provenance_pages, list) and provenance_pages:
        question["sourcePages"] = {
            "start": min(int(page) for page in provenance_pages),
            "end": max(int(page) for page in provenance_pages),
        }
    else:
        question["sourcePages"] = {
            "start": ocr_run.get("startPage", batch["startPage"]),
            "end": ocr_run.get("endPage", batch["endPage"]),
        }
    if isinstance(provenance, dict):
        question["sourceBlockIds"] = list(provenance.get("sourceBlockIds") or [])
        question["visualAssetIds"] = list(provenance.get("visualAssetIds") or [])
    question["sourceArtifactUrl"] = ocr_run.get("sourceArtifactUrl")
    question["promptArtifactUrl"] = ocr_run.get("promptArtifactUrl")
    available_images = [
        url for url in ocr_run.get("imageUrls", [])
        if isinstance(url, str) and url.startswith("/api/uploads/")
    ]
    # ``[]`` 是一个有意义的结果：OCR 已明确判断本题没有图片。
    # 不能用 ``or`` 回退到模型返回的 imageReferences，否则模型可能把同一批次
    # 其他题目的图片重新带进来，造成题干图/选项图串题。
    references = list(source_image_references)
    question.pop("imageReferences", None)
    available_by_name = {Path(url).name: url for url in available_images}
    question["imageUrls"] = [
        available_by_name[Path(reference).name]
        for reference in references
        if Path(reference).name in available_by_name
    ]


def review_lesson_payload(
    payload: dict[str, Any],
    lesson_source: str,
    asset_dir: Path | list[Path],
    guide_cards: list[dict[str, Any]],
    question_ir: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the review adapter and refresh the in-process lesson cache."""
    _number, question_block, _images = select_complete_question_source(lesson_source)
    image_paths = asset_dir if isinstance(asset_dir, list) else [
        asset_dir / Path(url).name
        for url in payload["question"].get("imageUrls", [])
        if (asset_dir / Path(url).name).is_file()
    ]
    reviewed_payload, review_run = runtime_reviewer.review(payload, question_block, image_paths)
    if question_ir is not None:
        # Review is advisory. Source facts are re-projected immediately so a reviewer
        # cannot silently rewrite the original stem, numbering, choices or subquestions.
        reviewed_payload["question"] = project_question_ir(
            question_ir, reviewed_payload.get("question", {})
        )
    lesson_store[reviewed_payload["question"]["id"]] = {
        "payload": reviewed_payload,
        "guideCards": guide_cards,
    }
    return reviewed_payload, review_run


tutor_engine = TutorEngine(lesson_store=lesson_store, runtime=runtime, guide_cards=GUIDE_CARDS)


def generate_model_reply(request: HelpRequest) -> TutorReply:
    """Generate one tutoring turn from cached lesson context."""
    return tutor_engine.reply(request)
