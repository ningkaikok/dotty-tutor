"""Generate and confirm teacher assignment plans."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any, Mapping

from domain.assignment_planning import (
    PLANNER_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    aggregate_class_mastery,
    aggregate_error_reason_stats,
    build_publication_coverage,
    deterministic_goals,
    planning_input_snapshot,
    source_fingerprint,
)
from persistence.assignment_planning_store import AssignmentPlanningStore
from run_audit import RunAudit


class AssignmentPlanConflict(ValueError):
    """A plan cannot be confirmed because its immutable inputs changed."""


class AssignmentPlanningService:
    """Own planning orchestration; the model never owns facts or assignment writes."""

    def __init__(self, *, store: Any, planning_store: AssignmentPlanningStore, mistake_store: Any = None, runtime: Any = None) -> None:
        self.store = store
        self.planning_store = planning_store
        self.mistake_store = mistake_store
        self.runtime = runtime
        self.audit = RunAudit(store)

    def _facts(self, class_id: str, publication_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        classroom = self.store.get_class(class_id)
        if not classroom:
            raise LookupError("班级不存在")
        publication = self.store.load_publication(publication_id)
        if not publication or publication.get("status") != "published":
            raise LookupError("只能为已发布互动试卷生成作业计划")
        members = classroom.get("members", [])
        learner_ids = [member["learnerId"] for member in members]
        states = self.planning_store.list_mastery_states(learner_ids)
        mistakes: list[dict[str, Any]] = []
        if self.mistake_store is not None:
            for learner_id in learner_ids:
                mistakes.extend(self.mistake_store.list(learner_id))
        mastery = aggregate_class_mastery(members, states)
        errors = aggregate_error_reason_stats(members, mistakes)
        coverage = build_publication_coverage(publication.get("lessons", []))
        # A plan is about this paper. Historical evidence may come from other
        # publications, but unrelated topics must not become assignment goals.
        mastery = {key: value for key, value in mastery.items() if key in coverage}
        errors = {key: value for key, value in errors.items() if key in coverage}
        for key, item in coverage.items():
            mastery.setdefault(key, {
                "planningTopicKey": key,
                "topic": item["topic"],
                "observedStudentCount": 0,
                "notObservedStudentCount": len(members),
                "notObserved": len(members),
                "averageScore": None,
                "distribution": {"needsSupport": 0, "developing": 0, "mastered": 0},
                "evidenceCount": 0,
            })
        snapshot = planning_input_snapshot(
            class_size=len(members), publication=publication,
            mastery=mastery, errors=errors, coverage=coverage,
        )
        # These digests make a same-sized membership replacement or a changed
        # immutable question set stale without persisting or sending identity.
        snapshot["sourceIdentity"] = {
            "membershipHash": hashlib.sha256("\n".join(sorted(learner_ids)).encode()).hexdigest(),
            "publicationHash": hashlib.sha256(json.dumps({
                "publicationId": publication_id,
                "version": publication.get("version", 1),
                "lessonIds": publication.get("lessonIds") or [],
            }, sort_keys=True).encode()).hexdigest(),
        }
        return publication, snapshot, {
            "classSize": len(members),
            "mastery": mastery,
            "errors": errors,
            "coverage": coverage,
        }

    def create_plan(self, *, class_id: str, publication_id: str, now: float | None = None) -> dict[str, Any]:
        publication, snapshot, facts = self._facts(class_id, publication_id)
        fingerprint = source_fingerprint(snapshot)
        deterministic = deterministic_goals(facts["mastery"], facts["errors"], facts["coverage"])
        warnings = self._warnings(facts, deterministic)
        result = {
            "plannerVersion": PLANNER_VERSION,
            "fallback": True,
            "fallbackReason": "未启用规划模型",
            "goals": deterministic,
            "coverage": list(facts["coverage"].values()),
            "mastery": list(facts["mastery"].values()),
            "errorStats": list(facts["errors"].values()),
        }
        model_run: dict[str, Any] = {"provider": "deterministic", "model": "rules", "fallback": True}
        if self.runtime is not None:
            result, model_run = self._try_model(facts, deterministic, result)
            if result.get("fallback"):
                warnings.append({"code": "planner_fallback", "severity": "info", "message": "规划模型不可用，已使用确定性规则生成目标。"})
        started = now if now is not None else time.time()
        run = self.audit.start(
            "assignment_plan", f"class:{class_id}", publication_id=publication_id,
            config={
                "plannerVersion": PLANNER_VERSION,
                "promptVersion": PROMPT_VERSION,
                "schemaVersion": SCHEMA_VERSION,
                "inputHash": fingerprint,
                "model": {key: value for key, value in model_run.items() if key != "prompt"},
            },
        )
        plan = self.planning_store.create(
            plan_id=uuid.uuid4().hex,
            class_id=class_id,
            publication_id=publication_id,
            publication_version=int(publication.get("version") or 1),
            source_fingerprint=fingerprint,
            input_snapshot=snapshot,
            result=result,
            warnings=warnings,
            run_id=run["runId"],
            created_at=started,
        )
        self.audit.finish(run["runId"], result={
            "planId": plan["planId"], "sourceFingerprint": fingerprint,
            "fallback": bool(result.get("fallback")),
        })
        return plan

    def get_plan(self, *, class_id: str, plan_id: str) -> dict[str, Any] | None:
        plan = self.planning_store.get(plan_id)
        if plan and plan["classId"] != class_id:
            return None
        return plan

    def personalized_context(self, *, class_id: str, plan_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return only aggregate, identity-free context for lesson generation."""
        plan = self.get_plan(class_id=class_id, plan_id=plan_id)
        if not plan:
            raise LookupError("作业计划不存在或不属于当前班级")
        if plan.get("status") != "draft":
            raise ValueError("只有未确认的分析计划可以生成个性化作业")
        publication, snapshot, facts = self._facts(class_id, plan["publicationId"])
        if source_fingerprint(snapshot) != plan["sourceFingerprint"]:
            raise ValueError("作业计划已失效，请重新分析")
        result = plan.get("result") or {}
        mastery = [item for item in result.get("mastery", []) if int(item.get("evidenceCount", 0) or 0) > 0]
        errors = [item for item in result.get("errorStats", []) if int(item.get("total", 0) or 0) > 0]
        if not mastery and not errors:
            raise ValueError("当前计划没有可用于个性化生成的班级证据")
        goals = [
            {key: goal.get(key) for key in ("planningTopicKey", "topic", "objective", "evidenceRefs")}
            for goal in result.get("goals", [])
            if goal.get("planningTopicKey") in facts["coverage"]
        ]
        if not goals:
            raise ValueError("当前计划没有可生成题目的目标")
        source_examples = []
        for lesson in publication.get("lessons", []):
            question = (lesson.get("questionPayload") or {}).get("question") or {}
            source_examples.append({
                "planningTopicKey": next((key for key, value in facts["coverage"].items() if value.get("topic") == question.get("knowledgePoint")), ""),
                "prompt": str(question.get("prompt") or "")[:800],
                "questionType": str(question.get("questionType") or "")[:30],
            })
        return plan, {
            "sourcePlanId": plan["planId"],
            "sourcePublicationId": plan["publicationId"],
            "subject": self.store.get_class(class_id).get("subject", "数学"),
            "gradeBand": self.store.get_class(class_id).get("gradeBand", "初中"),
            "goals": goals,
            "mastery": mastery,
            "errors": errors,
            "sourceExamples": source_examples,
        }

    def current_fingerprint(self, *, class_id: str, publication_id: str) -> str:
        _, snapshot, _ = self._facts(class_id, publication_id)
        return source_fingerprint(snapshot)

    @staticmethod
    def _warnings(facts: Mapping[str, Any], goals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        if not facts["classSize"]:
            warnings.append({"code": "empty_class", "severity": "warning", "message": "班级还没有学生，计划缺少班级证据。"})
        observed = sum(int(item.get("observedStudentCount", 0)) for item in facts["mastery"].values())
        if not observed:
            warnings.append({"code": "no_mastery_evidence", "severity": "info", "message": "当前班级没有可用掌握度证据；未观测学生不会被当作 0 分。"})
        if not goals:
            warnings.append({"code": "no_coverage", "severity": "warning", "message": "试卷没有可识别的知识点覆盖。"})
        return warnings

    def _try_model(self, facts: Mapping[str, Any], deterministic: list[dict[str, Any]], fallback: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        model_input = {
            "classSize": facts["classSize"],
            "topics": [
                {
                    "planningTopicKey": key,
                    "topic": value.get("topic"),
                    "mastery": facts["mastery"].get(key, {"observedStudentCount": 0, "averageScore": None}),
                    "errorStats": facts["errors"].get(key, {"selfReported": {}, "aiAttributed": {}, "effective": {}}),
                    "coverage": value,
                    "evidenceRefs": [f"mastery:{key}", f"coverage:{key}"],
                }
                for key, value in facts["coverage"].items()
            ],
        }
        schema = {
            "type": "object", "required": ["goals"], "properties": {
                "goals": {"type": "array", "items": {"type": "object", "required": ["planningTopicKey", "objective", "evidenceRefs"]}},
            },
        }
        prompt = "请只对给定主题目标进行简洁表达和排序，不新增主题或证据：\n" + json.dumps(model_input, ensure_ascii=False, sort_keys=True)
        try:
            raw, run = self.runtime.generate_json(prompt, schema, max_tokens=900)
            goals_by_key = {goal["planningTopicKey"]: goal for goal in deterministic}
            allowed = set(goals_by_key)
            model_goals = raw.get("goals") if isinstance(raw, dict) else None
            if not isinstance(model_goals, list) or not model_goals:
                raise ValueError("规划模型未返回目标")
            checked: list[dict[str, Any]] = []
            for item in model_goals:
                if not isinstance(item, dict) or item.get("planningTopicKey") not in allowed:
                    raise ValueError("规划模型引用了未知主题")
                key = item["planningTopicKey"]
                refs = item.get("evidenceRefs")
                allowed_refs = set(goals_by_key[key]["evidenceRefs"])
                if not isinstance(refs, list) or not set(refs).issubset(allowed_refs):
                    raise ValueError("规划模型引用了未授权证据")
                checked.append({**goals_by_key[key], "objective": str(item.get("objective") or goals_by_key[key]["objective"]), "evidenceRefs": refs})
            for index, goal in enumerate(checked, start=1):
                goal["priority"] = index
            return {**fallback, "fallback": False, "fallbackReason": None, "goals": checked}, {**(run or {}), "fallback": False}
        except Exception as error:  # noqa: BLE001
            return {**fallback, "fallback": True, "fallbackReason": str(error)[:160]}, {"provider": "deterministic", "model": "rules", "fallback": True, "fallbackReason": str(error)[:160]}
