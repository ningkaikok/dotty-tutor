"""HTTP boundary for creating tutor threads and appending turns."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request

from auth_context import learner_for_request, require_owner
from domain.contracts.tutoring import TutorMessageRequest
from domain.questions.student_view import (
    student_tutor_action,
    student_tutor_reply,
    student_tutor_thread,
)
from domain.tutoring.tools import TOOL_POLICY_VERSION, validate_tool_proposal
from domain.tutoring.turn_plan import ERROR_STRATEGIES
from observability import log_event
from persistence.tutoring_store import ConcurrentTurnError
from run_audit import build_tutor_run_config


def has_meaningful_answer(content: str, interaction_result: dict[str, Any]) -> bool:
    """Return whether a turn contains an answer a learner could have entered.

    Checking only whether ``interaction_result`` is non-empty is insufficient:
    an untouched choice control serializes as ``{"selectedOptions": []}``.
    Keeping the rule at the HTTP boundary gives every client the same validation
    while the tutor can assume that an ``answer`` turn contains useful input.
    """
    if content.strip():
        return True
    for value in interaction_result.values():
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, list) and value:
            return True
        if isinstance(value, dict) and has_meaningful_answer("", value):
            return True
    return False


def _evidence_registry(
    *, thread: dict[str, Any], mistake: dict[str, Any], input_item: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Build only references the server can prove belong to this learner.

    Tutor proposals are model output, so a non-empty string is not evidence.
    The registry deliberately contains stable IDs from persisted records only;
    unknown references remain denied even while execution is shadow-only.
    """
    owner = thread.get("learnerId")
    registry: dict[str, dict[str, Any]] = {}
    mistake_id = mistake.get("mistakeId")
    if isinstance(mistake_id, str) and mistake_id:
        record = {"learnerId": owner, "kind": "mistake"}
        registry[mistake_id] = record
        registry[f"mistake:{mistake_id}"] = record
    if not input_item:
        return registry
    input_id = input_item.get("inputId") or input_item.get("input_id")
    if isinstance(input_id, str) and input_id:
        record = {"learnerId": owner, "kind": "tutor-input"}
        registry[input_id] = record
        registry[f"input:{input_id}"] = record
    for artifact in input_item.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        artifact_id = artifact.get("artifactId") or artifact.get("artifact_id")
        if isinstance(artifact_id, str) and artifact_id:
            registry[artifact_id] = {"learnerId": owner, "kind": "artifact"}
            registry[f"artifact:{artifact_id}"] = registry[artifact_id]
    return registry


def _persist_tutor_turn(
    *,
    thread_id: str,
    request: TutorMessageRequest,
    thread: dict[str, Any],
    mistake: dict[str, Any],
    input_item: dict[str, Any] | None,
    mistake_store: Any,
    tutoring_store: Any,
    tutor: Any,
    run_id: str,
    request_key: str | None,
    request_hash: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute and persist one turn, returning a content-free audit summary."""
    result = tutor.reply(
        mistake=mistake,
        thread=thread,
        recent_messages=tutoring_store.recent_messages(thread_id),
        request=request,
    )
    proposals = result["reply"].toolProposals
    policy_decisions: list[dict[str, Any]] = []
    evidence_registry = _evidence_registry(
        thread=thread,
        mistake=mistake,
        input_item=input_item,
    )
    for proposal in proposals:
        decision = validate_tool_proposal(
            proposal,
            stage=thread["stage"],
            input_item=input_item,
            action=result["action"],
            evidence_registry=evidence_registry,
            evidence_owner=thread["learnerId"],
        )
        policy_decisions.append(decision.model_dump())
        key_source = json.dumps(
            {"thread": thread_id, "input": request.inputId, "proposal": proposal, "stage": thread["stage"]},
            ensure_ascii=False,
            sort_keys=True,
        )
        tutoring_store.append_tool_event(
            thread_id=thread_id,
            input_id=request.inputId,
            learner_id=thread["learnerId"],
            tool_name=str(proposal.get("name") or "unknown"),
            proposal=proposal,
            policy_version=TOOL_POLICY_VERSION,
            decision=decision.decision,
            reason=decision.reason,
            execution_status="shadow",
            idempotency_key=hashlib.sha256(key_source.encode("utf-8")).hexdigest(),
        )
        log_event(
            "tutor.tool.policy",
            run_id=run_id,
            thread_id=thread_id,
            tool_name=decision.name,
            decision=decision.decision,
            policy_version=decision.policyVersion,
            execution_status="shadow",
        )
    result["action"]["toolPolicy"] = policy_decisions
    result["action"]["runId"] = run_id
    plan = result["action"].get("tutorTurnPlan")
    diagnosis = plan.get("misconception") if isinstance(plan, dict) else None
    # AI attribution is written only after the same evidence/confidence gate
    # used by the tutor plan. An unconfirmed hypothesis must never overwrite
    # the latest trusted value, and this boundary keeps StatefulTutor store-free.
    category = diagnosis.get("category") if isinstance(diagnosis, dict) else None
    confidence = diagnosis.get("confidence") if isinstance(diagnosis, dict) else None
    diagnosis_update = None
    if (
        isinstance(diagnosis, dict)
        and diagnosis.get("needsConfirmation") is False
        and category in ERROR_STRATEGIES
        and category != "unknown"
        and confidence is not None
    ):
        diagnosis_update = {
            "mistake_id": thread["mistakeId"],
            "category": category,
            "confidence": confidence,
            "evidence": {
                "text": diagnosis.get("evidence", ""),
                "matched": diagnosis.get("evidenceMatched", False),
            },
            "model_version": (
                result["reply"].modelRun.get("model")
                if isinstance(result["reply"].modelRun, dict)
                else None
            ),
        }
    saved = tutoring_store.append_turn(
        thread_id,
        student_content=request.content.strip() or "请求下一步提示",
        input_mode=result["inputMode"],
        assistant_content=result["reply"].reply,
        assessment=result["action"]["assessment"],
        action=result["action"],
        model_run=result["reply"].modelRun,
        stage=result["stage"],
        hint_level=result["reply"].nextHintLevel,
        summary=result["summary"],
        input_id=request.inputId,
        expected_message_count=int(thread.get("messageCount", 0)),
        request_key=request_key,
        request_hash=request_hash if request_key else None,
        replay_response={
            "reply": result["reply"].model_dump(),
            "action": result["action"],
        } if request_key else None,
    )
    if saved is None:
        raise HTTPException(status_code=404, detail="辅导线程不存在")
    if diagnosis_update:
        mistake_store.update_ai_error_reason(**diagnosis_update)
    model_run = result["reply"].modelRun if isinstance(result["reply"].modelRun, dict) else {}
    decision_counts = {
        decision: sum(item.get("decision") == decision for item in policy_decisions)
        for decision in ("allow", "deny", "confirm")
    }
    audit_summary = {
        "threadId": thread_id,
        "mistakeId": thread["mistakeId"],
        "inputId": request.inputId,
        "previousStage": thread["stage"],
        "nextStage": result["stage"],
        "assessment": result["action"]["assessment"],
        "inputMode": result["inputMode"],
        "source": result["reply"].source,
        "provider": model_run.get("provider"),
        "model": model_run.get("model"),
        "fallback": bool(model_run.get("fallback", False)),
        "toolProposalCount": len(proposals),
        "toolDecisions": decision_counts,
        "deduplication": {
            key: result["action"].get("deduplication", {}).get(key)
            for key in ("status", "retryCount", "fallbackUsed")
            if key in result["action"].get("deduplication", {})
        },
    }
    response = {
        "thread": student_tutor_thread(saved),
        "reply": student_tutor_reply(result["reply"].model_dump()),
        "action": student_tutor_action(result["action"]),
    }
    return response, audit_summary


def build_tutoring_router(
    *, mistake_store: Any, tutoring_store: Any, tutor: Any, run_audit: Any | None = None,
) -> APIRouter:
    """Build the tutoring HTTP adapter from replaceable domain dependencies.

    The demo uses ``local-demo`` as its single learner identity.  This ownership
    check prevents accidental cross-record access during local testing, but it
    is not authentication.  A public deployment must derive the learner from a
    trusted login session instead.
    """
    router = APIRouter(tags=["tutoring"])

    @router.post("/api/mistakes/{mistake_id}/thread")
    def create_thread(
        request: Request, mistake_id: str, learnerId: str | None = None
    ) -> dict[str, Any]:
        learnerId = learner_for_request(request, learnerId)
        """Create or restore the single tutoring thread for one confirmed mistake."""
        mistake = mistake_store.get(mistake_id)
        if not mistake:
            raise HTTPException(status_code=404, detail="错题不存在")
        if mistake["learnerId"] != learnerId:
            raise HTTPException(status_code=403, detail="不能访问其他学生的错题")
        if mistake["status"] == "pending_confirmation":
            raise HTTPException(status_code=409, detail="请先确认题目和错误原因，再开始陪练")
        if mistake["status"] == "archived":
            raise HTTPException(status_code=409, detail="归档错题不能开始陪练")
        thread = tutoring_store.create_or_get(mistake_id, learnerId)
        log_event("tutor.thread.ready", thread_id=thread["threadId"], mistake_id=mistake_id)
        # create_or_get 可能返回不含消息的轻量记录；统一补载消息，让创建与恢复拥有相同响应结构。
        return student_tutor_thread(tutoring_store.get(thread["threadId"]) or thread)

    @router.get("/api/tutor/threads/{thread_id}")
    def get_thread(request: Request, thread_id: str) -> dict[str, Any]:
        """Return bounded persisted messages and the current tutoring state."""
        thread = tutoring_store.get(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="辅导线程不存在")
        require_owner(request, thread["learnerId"])
        return student_tutor_thread(thread)

    @router.post("/api/tutor/threads/{thread_id}/messages")
    def append_message(
        http_request: Request,
        thread_id: str,
        request: TutorMessageRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        """Evaluate one learner turn, generate guidance and persist both messages."""
        thread = tutoring_store.get(thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="辅导线程不存在")
        require_owner(http_request, thread["learnerId"])
        request_hash = hashlib.sha256(json.dumps(
            request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")).hexdigest()
        if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 128):
            raise HTTPException(status_code=422, detail="Idempotency-Key 长度须为 1 到 128 字符")
        request_key = hashlib.sha256(f"{thread_id}:{idempotency_key}".encode("utf-8")).hexdigest() if idempotency_key else None

        def replay_existing() -> dict[str, Any] | None:
            if request_key is None:
                return None
            existing = tutoring_store.get_turn_request(thread_id, request_key)
            if not existing:
                return None
            if existing["requestHash"] != request_hash:
                raise HTTPException(status_code=409, detail="Idempotency-Key 已用于不同的辅导输入")
            saved_response = existing["response"]
            current_thread = tutoring_store.get(thread_id)
            if not current_thread:
                raise HTTPException(status_code=404, detail="辅导线程不存在")
            return {
                "thread": student_tutor_thread(current_thread),
                "reply": student_tutor_reply(saved_response["reply"]),
                "action": student_tutor_action(saved_response["action"]),
            }

        replay = replay_existing()
        if replay:
            return replay
        mistake = mistake_store.get(thread["mistakeId"])
        if not mistake:
            raise HTTPException(status_code=404, detail="原错题不存在")
        require_owner(http_request, mistake["learnerId"])
        input_item: dict[str, Any] | None = None
        if request.inputId:
            input_item = tutoring_store.get_input(request.inputId)
            if not input_item or input_item["threadId"] != thread_id:
                raise HTTPException(status_code=404, detail="TutorInput 不存在")
            if input_item["status"] != "confirmed":
                raise HTTPException(status_code=409, detail="请先确认解题步骤识别结果")
            incoming_formulas = [item.model_dump() for item in request.formulaRecognitions]
            stored_formulas = input_item.get("formulaRecognitions", [])
            incoming_canvas = request.canvasState.model_dump() if request.canvasState else None
            if request.content and request.content != input_item["content"]:
                raise HTTPException(status_code=409, detail="已确认的输入内容不可修改，请创建新的 TutorInput")
            if request.interactionResult and request.interactionResult != input_item["interactionResult"]:
                raise HTTPException(status_code=409, detail="已确认的结构化答案不可修改，请创建新的 TutorInput")
            if incoming_formulas and incoming_formulas != stored_formulas:
                raise HTTPException(status_code=409, detail="已确认的公式识别不可修改，请创建新的 TutorInput")
            if incoming_canvas is not None and incoming_canvas != input_item.get("canvasState"):
                raise HTTPException(status_code=409, detail="已确认的画布状态不可修改，请创建新的 TutorInput")
            # The confirmed server snapshot is the sole evidence source, including
            # when a legacy client sends conflicting or partial fields.
            request = request.model_copy(update={
                "content": input_item["content"],
                "interactionResult": input_item["interactionResult"],
                "formulaRecognitions": stored_formulas,
                "canvasState": input_item.get("canvasState"),
            })
        if request.mode == "answer" and not has_meaningful_answer(
            request.content,
            request.interactionResult,
        ):
            raise HTTPException(status_code=422, detail="请先输入或选择答案")

        run_id = uuid.uuid4().hex
        started = time.perf_counter()
        audit_started = False
        try:
            if run_audit is not None:
                run_audit.start(
                    "tutor_turn",
                    "tutor",
                    run_id=run_id,
                    config=build_tutor_run_config(
                        runtime=getattr(tutor, "runtime", None),
                        operation_details={
                            "stage": thread["stage"],
                            "mode": request.mode,
                            "hintLevel": request.hintLevel,
                            "hasInputEnvelope": request.inputId is not None,
                        },
                    ),
                )
                audit_started = True
            log_event(
                "tutor.turn.started",
                run_id=run_id,
                thread_id=thread_id,
                mistake_id=thread["mistakeId"],
                stage=thread["stage"],
                mode=request.mode,
                status="running",
            )
            response, audit_summary = _persist_tutor_turn(
                thread_id=thread_id,
                request=request,
                thread=thread,
                mistake=mistake,
                input_item=input_item,
                mistake_store=mistake_store,
                tutoring_store=tutoring_store,
                tutor=tutor,
                run_id=run_id,
                request_key=request_key,
                request_hash=request_hash,
            )
            if run_audit is not None:
                try:
                    run_audit.finish(run_id, result=audit_summary)
                except Exception as audit_error:
                    # The learner turn is already durable.  Audit storage is an
                    # observability boundary and must not turn that success into
                    # a retryable 500 that could duplicate the learning turn.
                    log_event(
                        "tutor.audit.failed",
                        level=40,
                        run_id=run_id,
                        thread_id=thread_id,
                        error_type=type(audit_error).__name__,
                    )
        except Exception as error:
            if run_audit is not None and audit_started:
                try:
                    run_audit.fail(
                        run_id,
                        RuntimeError(type(error).__name__),
                        stage="tutor-turn",
                    )
                except Exception as audit_error:
                    log_event(
                        "tutor.audit.failed",
                        level=40,
                        run_id=run_id,
                        thread_id=thread_id,
                        error_type=type(audit_error).__name__,
                    )
            log_event(
                "tutor.turn.failed",
                level=40,
                run_id=run_id,
                thread_id=thread_id,
                mistake_id=thread["mistakeId"],
                stage=thread["stage"],
                status="failed",
                error_type=type(error).__name__,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
            )
            if isinstance(error, ConcurrentTurnError):
                replay = replay_existing()
                if replay:
                    return replay
                raise HTTPException(status_code=409, detail=str(error)) from error
            raise
        log_event(
            "tutor.turn.completed",
            run_id=run_id,
            thread_id=thread_id,
            mistake_id=thread["mistakeId"],
            stage=audit_summary["nextStage"],
            assessment=audit_summary["assessment"],
            source=audit_summary["source"],
            provider=audit_summary["provider"],
            model=audit_summary["model"],
            fallback=audit_summary["fallback"],
            tool_proposal_count=audit_summary["toolProposalCount"],
            status="succeeded",
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        return response

    @router.get("/api/tutor/threads/{thread_id}/tool-events")
    def list_tool_events(request: Request, thread_id: str) -> list[dict[str, Any]]:
        """Return the server-side shadow audit for a tutor thread."""
        thread = tutoring_store.get(thread_id, message_limit=1)
        if not thread:
            raise HTTPException(status_code=404, detail="辅导线程不存在")
        require_owner(request, thread["learnerId"])
        return tutoring_store.list_tool_events(thread_id)

    return router
