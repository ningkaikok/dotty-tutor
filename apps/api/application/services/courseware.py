"""Teacher-reviewed, source-grounded courseware orchestration.

This service owns state transitions and authorization-independent business rules;
HTTP routes derive actor identity and Worker handlers provide fenced executions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
import uuid
from typing import Any

from application.job_worker import JobCancelled, JobExecutionContext, TerminalJobError
from domain.contracts.courseware import (
    CoursewareActivityBlock,
    CoursewareAttemptRequest,
    CoursewareDocument,
    CoursewareImageBlock,
    CoursewarePageContent,
    CoursewarePageDraftOutput,
    CoursewarePlan,
    CoursewarePlanCreate,
    CoursewarePlanDraftOutput,
    CoursewarePlanEdit,
    CoursewarePublicActivityBlock,
    CoursewarePublicDocument,
    CoursewarePublicPage,
    CoursewareSourceRef,
)
from domain.courseware.assessment import assess_activity
from domain.courseware.model_output import generation_schema
from domain.courseware.profiles import CoursewareProfileRegistry, courseware_profiles
from domain.courseware.quality import document_page_issues, source_readiness_issues
from infrastructure.runtime.job_snapshot import (
    current_job_runtime_snapshot,
    use_job_runtime_snapshot,
)
from infrastructure.runtime.model_runtime import ModelRuntime, ModelSelection
from infrastructure.runtime.model_runtime import runtime as generation_runtime
from persistence.courseware_store import CoursewareStore
from prompts import CATALOG, PromptTemplate


class CoursewareService:
    """Coordinate planning, per-page authoring, review, publication, and playback."""

    def __init__(
        self,
        *,
        store: CoursewareStore,
        chapters: Any,
        jobs: Any,
        runtime: ModelRuntime | None = None,
        prompt_store: Any | None = None,
        profiles: CoursewareProfileRegistry = courseware_profiles,
    ) -> None:
        self.store = store
        self.chapters = chapters
        self.jobs = jobs
        self.runtime = runtime or generation_runtime
        self.prompt_store = prompt_store
        self.profiles = profiles

    def list_profiles(self) -> dict[str, Any]:
        return {"items": [
            {"subjectId": item.subject_id, "label": item.label}
            for item in self.profiles.list_profiles()
        ]}

    @staticmethod
    def _source(chapter: dict[str, Any]) -> dict[str, Any]:
        revisions = chapter.get("sourceRevisions") or []
        if not revisions:
            raise ValueError("教材章节没有已保存的来源修订")
        return revisions[-1]

    def _chapter(
        self, chapter_id: str, *, subject: str | None = None, lock: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        chapter = self.store.load_chapter(chapter_id, lock=lock)
        if not chapter or chapter.get("deletedAt"):
            raise LookupError("教材章节不存在")
        if chapter.get("teachingMode") != "tutorial":
            raise ValueError("试卷和练习章节不能创建教材讲解课件")
        self.profiles.get(str(chapter.get("subject") or ""))
        if subject is not None and subject != chapter.get("subject"):
            raise ValueError("课件学科必须与教材章节一致")
        source = self._source(chapter)
        upload_id = source.get("uploadId")
        if upload_id:
            upload = self.chapters.store.load_job(str(upload_id))
            if not upload or upload.get("status") not in {"complete", "completed", "ready"}:
                raise ValueError("教材来源尚未完成识别")
            # Keep the immutable chapter revision as the source of truth while still
            # detecting an upload whose original file was replaced on disk.
            if source.get("sourceFileSha256"):
                from application.services.chapter_source_preview import (
                    file_hash,
                    source_file,
                )

                path = source_file(self.chapters.store, upload)
                if path is None or file_hash(path) != source["sourceFileSha256"]:
                    raise ValueError("教材原文件已变化，请重新准备来源")
        return chapter, source

    @staticmethod
    def _selected_pages(
        source: dict[str, Any], page_start: int | None = None, page_end: int | None = None,
    ) -> list[dict[str, Any]]:
        start = int(page_start if page_start is not None else source.get("pageStart", 1))
        end = int(page_end if page_end is not None else source.get("pageEnd", start))
        if end < start or end - start + 1 > 80:
            raise ValueError("课件来源范围必须在 1 到 80 页之间")
        by_page = {int(page["page"]): page for page in source.get("pages", [])}
        missing = [number for number in range(start, end + 1) if number not in by_page]
        if missing:
            raise ValueError("课件范围包含缺失来源页：" + ", ".join(map(str, missing[:12])))
        pages = [by_page[number] for number in range(start, end + 1)]
        issues = source_readiness_issues(pages)
        if issues:
            raise ValueError("来源未就绪，不能确认或发布课件：" + issues[0]["message"])
        return pages

    @staticmethod
    def _source_assets(source: dict[str, Any], selected_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from application.services.courseware_assets import source_assets

        return source_assets(source, selected_pages)

    @staticmethod
    def _assets_for_page(assets: list[dict[str, Any]], source_page_numbers: set[int]) -> list[dict[str, Any]]:
        """Expose only source assets whose frozen physical page is cited by this page."""
        return [
            copy.deepcopy(asset) for asset in assets
            if asset.get("status") == "available"
            and any(
                int(ref.get("page", 0)) in source_page_numbers
                for ref in asset.get("sourceRefs", [])
                if isinstance(ref, dict)
            )
        ]

    @staticmethod
    def _source_view(source: dict[str, Any], pages: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "sourceRevisionId": source["sourceRevisionId"],
            "pages": [{
                "page": item["page"],
                "text": item["text"],
                "sentenceIds": [sentence["sentenceId"] for sentence in item.get("sentences", [])],
                "sentences": item.get("sentences", []),
                "regions": [{
                    "regionId": region.get("regionId"),
                    "assetId": region.get("assetId"),
                    "alt": region.get("alt"),
                } for region in item.get("regions", [])],
            } for item in pages],
        }

    @classmethod
    def _validate_ref(cls, value: CoursewareSourceRef | dict[str, Any], source: dict[str, Any], pages: list[dict[str, Any]]) -> None:
        ref = value if isinstance(value, CoursewareSourceRef) else CoursewareSourceRef.model_validate(value)
        if ref.sourceRevisionId != source.get("sourceRevisionId"):
            raise ValueError("来源引用必须指向当前冻结的 sourceRevisionId")
        page = next((item for item in pages if int(item["page"]) == ref.page), None)
        if page is None:
            raise ValueError(f"来源引用第 {ref.page} 页不在当前课件范围")
        sentence_ids = {str(item.get("sentenceId")) for item in page.get("sentences", [])}
        region_by_id = {
            str(item.get("regionId")): item for item in page.get("regions", [])
            if item.get("regionId")
        }
        if any(item not in sentence_ids for item in ref.sentenceIds):
            raise ValueError("来源引用包含不存在的 sentenceId")
        if any(item not in region_by_id for item in ref.regionIds):
            raise ValueError("来源引用包含不存在的 regionId")
        if ref.assetId:
            source_page_asset = (
                source.get("uploadId") and source.get("sourceFileSha256")
                and ref.assetId == f"source-page-{str(source.get('sourceRevisionId'))[:12]}-{ref.page}"
            )
            generated_ids = {item["assetId"] for item in cls._source_assets(source, [page])}
            page_asset_ids = {
                str(region.get("assetId")) for region in (page or {}).get("regions", [])
                if region.get("assetId")
            }
            if not source_page_asset and ref.assetId not in page_asset_ids | generated_ids:
                raise ValueError("来源引用包含当前冻结来源中不存在的 assetId")

    @classmethod
    def _validate_refs(cls, value: Any, source: dict[str, Any], pages: list[dict[str, Any]]) -> None:
        if isinstance(value, CoursewareSourceRef):
            cls._validate_ref(value, source, pages)
        elif isinstance(value, dict):
            if "sourceRevisionId" in value and "page" in value:
                cls._validate_ref(value, source, pages)
            else:
                for child in value.values():
                    cls._validate_refs(child, source, pages)
        elif isinstance(value, (list, tuple)):
            for child in value:
                cls._validate_refs(child, source, pages)
        elif hasattr(value, "model_dump"):
            cls._validate_refs(value.model_dump(), source, pages)

    def _prompt(self, prompt_id: str) -> PromptTemplate:
        templates = self.prompt_store.active_templates() if self.prompt_store else CATALOG
        try:
            return templates[prompt_id]
        except KeyError as error:
            raise ValueError(f"课件提示词未登记：{prompt_id}") from error

    @staticmethod
    def _frozen_template(template: PromptTemplate) -> dict[str, Any]:
        return {"id": template.id, "version": template.version, "text": template.text,
                "variables": list(template.variables), "identity": template.identity()}

    @staticmethod
    def _template_from_payload(value: dict[str, Any]) -> PromptTemplate:
        return PromptTemplate(str(value["id"]), str(value["version"]), str(value["text"]), tuple(value["variables"]))

    @staticmethod
    def _runtime_audit(run: dict[str, Any], template: dict[str, Any]) -> dict[str, Any]:
        """Persist actual call identity and usage without prompts or credentials."""
        usage_value = run.get("usage")
        usage: dict[str, Any] = usage_value if isinstance(usage_value, dict) else {}
        fallback_value = run.get("schemaFallback")
        schema_fallback: dict[str, Any] = fallback_value if isinstance(fallback_value, dict) else {}
        identity_value = template.get("identity")
        identity: dict[str, Any] = identity_value if isinstance(identity_value, dict) else {}
        return {
            "provider": run.get("actualProvider") or run.get("provider"),
            "model": run.get("actualModel") or run.get("model"),
            "requestedProvider": run.get("requestedProvider"),
            "requestedModel": run.get("requestedModel"),
            "fallback": bool(run.get("fallback", False)),
            "durationMs": run.get("durationMs"),
            "promptChars": run.get("promptChars"),
            "maxOutputTokens": run.get("maxOutputTokens"),
            "promptTokens": usage.get("promptTokens"),
            "outputTokens": usage.get("outputTokens"),
            "providerAttempts": run.get("providerAttempts"),
            "schemaFallback": bool(schema_fallback.get("used", False)),
            "promptId": template.get("id"),
            "promptVersion": template.get("version"),
            "promptHash": identity.get("contentHash"),
        }

    @staticmethod
    def _job_summary(job: dict[str, Any] | None) -> dict[str, Any] | None:
        if not job:
            return None
        return {key: job.get(key) for key in ("jobId", "jobType", "status", "attemptCount", "progress", "message", "result")}

    def list_chapter_documents(self, chapter_id: str) -> dict[str, Any]:
        self._chapter(chapter_id)
        items = []
        plan = self.store.load_plan(chapter_id)
        for document in self.store.list_documents(chapter_id):
            if document.get("status") == "deleted":
                continue
            if plan and int(document.get("planVersion", 0)) != int(plan.get("planVersion", 0)):
                continue
            items.append({key: document.get(key) for key in (
                "coursewareId", "title", "status", "generationStatus", "recordVersion",
                "publicationId",
            )} | {"pageCount": len(document.get("pages", []))})
        return {"items": items}

    def get_coverage(self, chapter_id: str) -> dict[str, Any]:
        """Report complete-unit physical-page gaps without asserting teaching quality."""
        from application.services.courseware_coverage import coverage_report

        chapter, source = self._chapter(chapter_id)
        return coverage_report(chapter, source, self.store)

    def get_completion(self, chapter_id: str, *, max_pages_per_batch: int = 5) -> dict[str, Any]:
        """Suggest missing ranges while source and chapter identity remain guarded."""
        from application.services.courseware_completion import completion_report
        from application.services.courseware_coverage import coverage_report

        # Reuse the mutation serialization boundary only to stabilize reads. No
        # plan, job, review, or publication is created by this preparation API.
        with self.chapters.store.chapter_lock(chapter_id):
            with self.store.atomic():
                chapter, source = self._chapter(chapter_id, lock=True)
                blocked = []
                for number in range(int(source["pageStart"]), int(source["pageEnd"]) + 1):
                    try:
                        self._selected_pages(source, number, number)
                    except ValueError:
                        blocked.append(number)
                plan = self.store.load_plan(chapter_id)
                documents = [item for item in self.store.list_documents(chapter_id)
                             if not item.get("deletedAt") and item.get("status") != "deleted"]
                current_records = [item for item in ([plan] if plan else []) + documents
                                   if item.get("sourceRevisionId") == source["sourceRevisionId"]]
                active = False
                for item in current_records:
                    job = self.jobs.get_job(item["generationJobId"]) if self.jobs is not None and item.get("generationJobId") else None
                    active = active or (
                        job.get("status") in {"queued", "running"} if job
                        else item.get("generationStatus") in {"queued", "running"}
                    )
                return completion_report(
                    chapter, source, coverage_report(chapter, source, self.store),
                    plan=plan, blocked_pages=blocked, active_generation=active,
                    max_pages_per_batch=max_pages_per_batch,
                )

    def get_plan(self, chapter_id: str) -> dict[str, Any] | None:
        chapter, source = self._chapter(chapter_id)
        plan = self.store.load_plan(chapter_id)
        if plan and plan.get("sourceRevisionId") != source["sourceRevisionId"]:
            plan = {**plan, "status": "superseded"}
        return plan

    def create_plan(self, chapter_id: str, request: CoursewarePlanCreate, *, idempotency_key: str | None = None) -> dict[str, Any]:
        profile = self.profiles.get(request.subject)
        template = self._prompt(profile.plan_prompt_id)
        request_hash = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        command_key = (idempotency_key or request_hash).strip()[:128]
        job_key = f"courseware-plan:{chapter_id}:{command_key}"
        now = time.time()
        with self.chapters.store.chapter_lock(chapter_id):
            with self.store.atomic() as connection:
                # Re-read and lock inside the write transaction. A revision or
                # deletion after the optimistic request read must not be planned.
                chapter, source = self._chapter(chapter_id, subject=request.subject, lock=True)
                if int(chapter["recordVersion"]) != request.expectedRecordVersion:
                    raise ValueError("教材章节已更新，请刷新后重新规划")
                page_start = request.pageStart if request.pageStart is not None else int(source["pageStart"])
                page_end = request.pageEnd if request.pageEnd is not None else int(source["pageEnd"])
                pages = self._selected_pages(source, page_start, page_end)
                if len(pages) > 30:
                    raise ValueError("一次规划最多选择 30 个物理页；请按教学范围拆分课时")
                source_json = self._source_view(source, pages)
                serialized_source = json.dumps(source_json, ensure_ascii=False, separators=(",", ":"))
                if len(serialized_source) > 80_000:
                    raise ValueError("所选来源范围过长，请选择更小的完整教学范围")
                existing = self.store.load_plan(chapter_id, lock=True)
                existing_version = int(existing.get("recordVersion", 0)) if existing else 0
                existing_plan_version = int(existing.get("planVersion", 0)) if existing else 0
                next_plan_version = existing_plan_version + 1
                next_record_version = existing_version + 1
                runtime_snapshot = current_job_runtime_snapshot()
                job_id = uuid.uuid4().hex
                payload = {
                    "chapterId": chapter_id, "chapterVersion": int(chapter["recordVersion"]),
                    "sourceRevisionId": source["sourceRevisionId"], "pageStart": page_start,
                    "pageEnd": page_end, "sourcePages": source_json["pages"],
                    "audience": request.audience, "languagePolicy": request.languagePolicy,
                    "durationMinutes": request.durationMinutes, "requestedObjectives": request.objectives,
                    "expectedPlanRecordVersion": next_record_version, "planVersion": next_plan_version,
                    "runtimeSnapshot": runtime_snapshot,
                    "frozenTemplate": self._frozen_template(template), "requestHash": request_hash,
                }
                previous_job = self.jobs.get_by_idempotency_key(job_key, connection=connection, lock=True)
                if previous_job:
                    previous_payload = previous_job.get("payload") or {}
                    if previous_payload.get("requestHash") != request_hash:
                        raise ValueError("幂等键已用于不同课件规划请求")
                    current_plan = self.store.load_plan(chapter_id)
                    if current_plan and current_plan.get("generationJobId") == previous_job["jobId"]:
                        return {"plan": current_plan, "job": self._job_summary(previous_job)}
                    raise ValueError("规划幂等命令已被更新版本取代，请刷新")
                if existing and existing.get("sourceRevisionId") == source["sourceRevisionId"] and existing.get("requestHash") == request_hash:
                    related = self.jobs.get_job(existing.get("generationJobId")) if existing.get("generationJobId") else None
                    if not related or related.get("status") not in {"failed", "cancelled"}:
                        return {"plan": existing, "job": self._job_summary(related)}
                if existing and existing.get("generationJobId"):
                    previous = self.jobs.get_job(str(existing["generationJobId"]))
                    if previous and previous.get("status") in {"queued", "running"}:
                        self.jobs.request_cancel(previous["jobId"], connection=connection)
                plan_payload = CoursewarePlan.model_validate({
                    "chapterId": chapter_id, "sourceRevisionId": source["sourceRevisionId"],
                    "pageStart": page_start, "pageEnd": page_end, "subject": request.subject,
                    "audience": request.audience, "languagePolicy": request.languagePolicy,
                    "objectives": [], "requestedObjectives": request.objectives, "sessions": [],
                    "coverageMap": {}, "status": "draft", "planVersion": next_plan_version,
                    "recordVersion": max(1, next_record_version), "generationJobId": job_id,
                    "generationStatus": "queued", "coursewareIds": [], "requestHash": request_hash,
                    "createdAt": existing.get("createdAt", now) if existing else now, "updatedAt": now,
                }).model_dump()
                saved_plan = self.store.save_record(
                    record_id=f"plan:{chapter_id}", record_type="plan", chapter_id=chapter_id,
                    courseware_id=None, source_revision_id=source["sourceRevisionId"],
                    payload=plan_payload, status="draft", expected_version=existing_version,
                    connection=connection,
                )
                job = self.jobs.create_job(
                    "courseware.plan.generate", payload, job_id=job_id, max_attempts=2,
                    idempotency_key=job_key, connection=connection, reject_payload_mismatch=True,
                )
                return {"plan": saved_plan, "job": self._job_summary(job)}

    def run_plan_generation(
        self, payload: dict[str, Any], cancellation_check: Any, context: JobExecutionContext,
    ) -> dict[str, Any]:
        chapter, current_source = self._chapter(str(payload["chapterId"]))
        if int(chapter["recordVersion"]) != int(payload["chapterVersion"]):
            raise TerminalJobError("教材章节已变化，旧规划未写入")
        if current_source["sourceRevisionId"] != payload["sourceRevisionId"]:
            raise TerminalJobError("教材来源已变化，旧规划未写入")
        pages = self._selected_pages(current_source, int(payload["pageStart"]), int(payload["pageEnd"]))
        existing_plan = self.store.load_plan(str(payload["chapterId"]))
        if (existing_plan and existing_plan.get("generationJobId") == context.job_id
                and existing_plan.get("generationStatus") == "completed"
                and existing_plan.get("status") in {"awaiting_confirmation", "approved"}):
            return {"chapterId": payload["chapterId"], "planVersion": existing_plan["planVersion"], "status": existing_plan["status"]}
        if cancellation_check():
            raise JobCancelled()
        source_view = {"sourceRevisionId": current_source["sourceRevisionId"], "pages": payload["sourcePages"]}
        template = self._template_from_payload(payload["frozenTemplate"])
        prompt = template.render(
            chapter=json.dumps({"title": chapter["title"], "subject": chapter["subject"],
                                "pageStart": payload["pageStart"], "pageEnd": payload["pageEnd"]}, ensure_ascii=False),
            audience=payload["audience"], language_policy=payload["languagePolicy"],
            duration_minutes=payload["durationMinutes"],
            objectives=json.dumps(payload["requestedObjectives"], ensure_ascii=False),
            source=json.dumps(source_view, ensure_ascii=False),
        )
        schema = generation_schema(CoursewarePlanDraftOutput)
        selection = payload["runtimeSnapshot"]["generation"]
        with use_job_runtime_snapshot(payload):
            draft, run = self.runtime.generate_json(
                prompt, schema, max_tokens=6500, task="courseware_plan_generation",
                selection=ModelSelection(selection["provider"], selection["model"]),
            )
        parsed = CoursewarePlanDraftOutput.model_validate(draft)
        for objective in parsed.objectives:
            self._validate_refs(objective.sourceRefs, current_source, pages)
        for session in parsed.sessions:
            for section in session.sections:
                for page in section.pages:
                    if not set(page.objectiveIds).issubset({item.objectiveId for item in parsed.objectives}):
                        raise ValueError("规划页面引用了不存在的教学目标")
                    self._validate_refs(page.sourceRefs, current_source, pages)
        for refs in parsed.coverageMap.values():
            self._validate_refs(refs, current_source, pages)
        current_plan = self.store.load_plan(str(payload["chapterId"]))
        if current_plan is None:
            raise TerminalJobError("课件规划草稿不存在")
        result = {
            **current_plan,
            "objectives": [item.model_dump() for item in parsed.objectives],
            "sessions": [item.model_dump() for item in parsed.sessions],
            "coverageMap": {key: [ref.model_dump() for ref in refs] for key, refs in parsed.coverageMap.items()},
            "status": "awaiting_confirmation",
            "generationStatus": "completed",
            "planVersion": int(payload["planVersion"]),
            "generationAudit": self._runtime_audit(run, payload["frozenTemplate"]),
            "updatedAt": time.time(),
        }
        result["generationAudit"]["timingDerivedFromPages"] = True
        validated = CoursewarePlan.model_validate(result).model_dump()
        saved = self.store.checkpoint_plan(
            context=context,
            chapter_id=str(payload["chapterId"]),
            chapter_version=int(payload["chapterVersion"]),
            source_revision_id=str(payload["sourceRevisionId"]),
            expected_plan_record_version=int(payload["expectedPlanRecordVersion"]),
            plan=validated,
            now=time.time(),
        )
        return {"chapterId": payload["chapterId"], "planVersion": saved["planVersion"], "status": saved["status"]}

    def edit_plan(self, chapter_id: str, edit: CoursewarePlanEdit) -> dict[str, Any]:
        with self.chapters.store.chapter_lock(chapter_id):
            with self.store.atomic() as connection:
                chapter, source = self._chapter(chapter_id, lock=True)
                plan = self.store.load_plan(chapter_id, lock=True)
                if not plan:
                    raise LookupError("课件规划不存在")
                if int(plan["recordVersion"]) != edit.expectedRecordVersion:
                    raise ValueError("课件规划已更新，请刷新后重新编辑")
                if plan["sourceRevisionId"] != source["sourceRevisionId"]:
                    raise ValueError("教材来源已变化，请重新生成规划")
                start = edit.pageStart if edit.pageStart is not None else int(plan["pageStart"])
                end = edit.pageEnd if edit.pageEnd is not None else int(plan["pageEnd"])
                selected_pages = self._selected_pages(source, start, end)
                if len(selected_pages) > 30:
                    raise ValueError("一次规划最多选择 30 个物理页；请拆分教学范围")
                self._validate_refs(edit.objectives, source, selected_pages)
                self._validate_refs(edit.sessions, source, selected_pages)
                coverage = {
                    item.objectiveId: [ref.model_dump() for ref in item.sourceRefs]
                    for item in edit.objectives
                }
                old_jobs = []
                if plan.get("generationJobId"):
                    old_jobs.append(str(plan["generationJobId"]))
                for courseware_id in plan.get("coursewareIds", []):
                    old_doc = self.store.load_document(str(courseware_id), lock=True)
                    if old_doc and old_doc.get("generationJobId"):
                        old_jobs.append(str(old_doc["generationJobId"]))
                for job_id in old_jobs:
                    self.jobs.request_cancel(job_id, connection=connection)
                next_plan_version = int(plan["planVersion"]) + 1
                plan.update({
                    "pageStart": start, "pageEnd": end,
                    "audience": edit.audience, "languagePolicy": edit.languagePolicy,
                    "objectives": [item.model_dump() for item in edit.objectives],
                    "sessions": [item.model_dump() for item in edit.sessions],
                    "coverageMap": coverage, "status": "awaiting_confirmation",
                    "generationStatus": "completed", "generationJobId": None,
                    "approvalCommandId": None, "coursewareIds": [],
                    "planVersion": next_plan_version, "updatedAt": time.time(),
                })
                validated = CoursewarePlan.model_validate(plan).model_dump()
                return self.store.save_record(
                    record_id=f"plan:{chapter_id}", record_type="plan", chapter_id=chapter_id,
                    courseware_id=None, source_revision_id=source["sourceRevisionId"],
                    payload=validated, status="awaiting_confirmation",
                    expected_version=edit.expectedRecordVersion, connection=connection,
                )

    def approve_plan(self, chapter_id: str, *, plan_version: int, expected_record_version: int,
                     command_id: str) -> dict[str, Any]:
        with self.chapters.store.chapter_lock(chapter_id):
            with self.store.atomic() as connection:
                chapter, source = self._chapter(chapter_id, lock=True)
                plan = self.store.load_plan(chapter_id, lock=True)
                if not plan:
                    raise LookupError("课件规划不存在")
                if plan["sourceRevisionId"] != source["sourceRevisionId"]:
                    raise ValueError("教材来源已变化，请重新生成规划")
                if plan["status"] == "approved" and int(plan["planVersion"]) == plan_version:
                    if plan.get("approvalCommandId") != command_id:
                        raise ValueError("此规划版本已被其他确认命令批准")
                    docs = [item for item in self.store.list_documents(chapter_id) if item.get("coursewareId") in plan.get("coursewareIds", [])]
                    return {"plan": plan, "documents": docs, "jobs": [self._job_summary(self.jobs.get_job(item.get("generationJobId"))) for item in docs]}
                if int(plan["recordVersion"]) != expected_record_version or int(plan["planVersion"]) != plan_version:
                    raise ValueError("规划版本已变化，请刷新后确认")
                if plan["status"] != "awaiting_confirmation":
                    raise ValueError("只有待教师确认的规划可以确认")
                if not plan.get("objectives") or not plan.get("sessions"):
                    raise ValueError("确认前必须包含教学目标、课时与页面")
                if set(plan.get("coverageMap", {})) != {item["objectiveId"] for item in plan["objectives"]}:
                    raise ValueError("每个教学目标都必须有来源覆盖")
                pages = self._selected_pages(source, int(plan["pageStart"]), int(plan["pageEnd"]))
                self._validate_refs(plan["coverageMap"], source, pages)
                self._validate_refs(plan["sessions"], source, pages)
                profile = self.profiles.get(str(plan["subject"]))
                template = self._prompt(profile.page_prompt_id)
                template_data = self._frozen_template(template)
                now = time.time()
                documents: list[dict[str, Any]] = []
                jobs: list[dict[str, Any]] = []
                for session in plan["sessions"]:
                    session_page_numbers = {
                        int(ref["page"])
                        for section in session["sections"]
                        for outline in section["pages"]
                        for ref in outline["sourceRefs"]
                    }
                    assets = self._source_assets(source, [
                        page for page in pages if int(page["page"]) in session_page_numbers
                    ])
                    if len(assets) > 200:
                        raise ValueError("单个课时的来源素材超过 200 项，请拆分课时或减少来源区域后重新确认规划")
                    courseware_id = uuid.uuid4().hex
                    sections = []
                    outline_pages = []
                    for section in session["sections"]:
                        page_ids = []
                        for outline in section["pages"]:
                            page_ids.append(outline["pageId"])
                            outline_pages.append({**outline, "sectionId": section["sectionId"]})
                        sections.append({
                            "sectionId": section["sectionId"], "title": section["title"],
                            "purpose": section.get("purpose", ""),
                            "estimatedMinutes": section["estimatedMinutes"], "pageIds": page_ids,
                        })
                    doc = CoursewareDocument.model_validate({
                        "coursewareId": courseware_id,
                        "chapterId": chapter_id,
                        "planVersion": plan_version,
                        "sourceRevisionId": source["sourceRevisionId"],
                        "pageStart": int(plan["pageStart"]), "pageEnd": int(plan["pageEnd"]),
                        "sourceTitle": source.get("sourceTitle") or chapter["title"],
                        "sourceUrl": source.get("sourceUrl"),
                        "sourceLicense": source.get("license"),
                        "attributionText": source.get("attributionText"),
                        "sourceDistributionScope": source.get("distributionScope") or "private",
                        "subject": plan["subject"],
                        "audience": plan["audience"], "languagePolicy": plan["languagePolicy"],
                        "title": session["title"],
                        "durationMinutes": session["durationMinutes"],
                        "sections": sections,
                        "pages": [{
                            "pageId": outline["pageId"], "sectionId": outline["sectionId"],
                            "pageKind": outline["pageKind"], "layout": outline["layout"],
                            "title": outline["title"], "objectiveIds": outline.get("objectiveIds", []),
                            "sourceRefs": outline["sourceRefs"], "attributionText": source.get("attributionText"),
                            "blocks": [], "teacherNotes": "", "revealSteps": [],
                            "estimatedMinutes": outline["estimatedMinutes"], "generationStatus": "queued",
                            "reviewStatus": "pending", "version": 1,
                        } for outline in outline_pages],
                        "assets": assets,
                        "reviewIssues": [],
                        "recordVersion": 1,
                        "status": "draft",
                        "generationStatus": "queued",
                        "sourceReadiness": "ready",
                        "reviewStatus": "pending",
                        "publicationId": None,
                        "generationJobId": None,
                        "reviewRecords": [],
                        "createdAt": now,
                        "updatedAt": now,
                    }).model_dump()
                    job_id = uuid.uuid4().hex
                    doc["generationJobId"] = job_id
                    saved_doc = self.store.save_record(
                        record_id=f"document:{courseware_id}", record_type="document",
                        chapter_id=chapter_id, courseware_id=courseware_id,
                        source_revision_id=source["sourceRevisionId"], payload=doc,
                        status="draft", expected_version=0, connection=connection,
                    )
                    job_payload = {
                        "chapterId": chapter_id, "chapterVersion": int(chapter["recordVersion"]),
                        "sourceRevisionId": source["sourceRevisionId"], "planVersion": plan_version,
                        "coursewareId": courseware_id, "documentVersion": int(saved_doc["recordVersion"]),
                        "pageIds": [item["pageId"] for item in outline_pages],
                        "pageVersions": {
                            item["pageId"]: int(item["version"])
                            for item in saved_doc["pages"]
                        },
                        "sourcePages": self._source_view(source, pages)["pages"],
                        "sourceAssets": {
                            item["pageId"]: self._assets_for_page(
                                assets,
                                {int(ref["page"]) for ref in item.get("sourceRefs", [])},
                            )
                            for item in outline_pages
                        },
                        "objectives": plan["objectives"], "audience": plan["audience"],
                        "languagePolicy": plan["languagePolicy"], "frozenTemplate": template_data,
                        "runtimeSnapshot": current_job_runtime_snapshot(),
                    }
                    job = self.jobs.create_job(
                        "courseware.document.generate", job_payload, job_id=job_id,
                        idempotency_key=f"courseware-document:{courseware_id}:plan:{plan_version}",
                        max_attempts=2, connection=connection, reject_payload_mismatch=True,
                    )
                    documents.append(saved_doc)
                    summary = self._job_summary(job)
                    if summary is None:
                        raise RuntimeError("课件页面生成任务创建失败")
                    jobs.append(summary)
                plan["status"] = "approved"
                plan["approvalCommandId"] = command_id
                plan["coursewareIds"] = list(dict.fromkeys([*plan.get("coursewareIds", []), *[item["coursewareId"] for item in documents]]))
                plan["updatedAt"] = now
                saved_plan = self.store.save_record(
                    record_id=f"plan:{chapter_id}", record_type="plan", chapter_id=chapter_id,
                    courseware_id=None, source_revision_id=source["sourceRevisionId"],
                    payload=plan, status="approved", expected_version=expected_record_version,
                    connection=connection,
                )
                return {"plan": saved_plan, "documents": documents, "jobs": jobs}

    def _document(
        self, courseware_id: str, *, lock: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        doc = self.store.load_document(courseware_id, lock=lock)
        if not doc or doc.get("status") == "deleted" or doc.get("deletedAt"):
            raise LookupError("课件不存在")
        chapter, source = self._chapter(str(doc["chapterId"]), lock=lock)
        if doc.get("sourceRevisionId") != source.get("sourceRevisionId"):
            raise ValueError("教材来源已变化，旧课件不能继续编辑")
        plan = self.store.load_plan(str(doc["chapterId"]), lock=lock)
        if (not plan or plan.get("sourceRevisionId") != doc.get("sourceRevisionId")
                or int(plan.get("planVersion", 0)) != int(doc.get("planVersion", -1))):
            raise LookupError("课件规划已更新，此草稿已被新版本取代")
        return chapter, source, doc

    def get_document(self, courseware_id: str) -> dict[str, Any]:
        _, source, document = self._document(courseware_id)
        selected = self._selected_pages(source, int(document["pageStart"]), int(document["pageEnd"]))
        self._validate_refs(document, source, selected)
        result = CoursewareDocument.model_validate(document).model_dump()
        return self._with_asset_urls(result)

    @staticmethod
    def _with_asset_urls(document: dict[str, Any], *, publication_id: str | None = None) -> dict[str, Any]:
        result = copy.deepcopy(document)
        courseware_id = result["coursewareId"]
        for asset in result.get("assets", []):
            if asset.get("status") != "available":
                asset["src"] = None
                continue
            suffix = f"?publicationId={publication_id}" if publication_id else ""
            asset["src"] = f"/api/courseware/{courseware_id}/assets/{asset['assetId']}{suffix}"
        return result

    def get_chapter_assets(
        self,
        courseware_id: str,
        asset_id: str,
        *,
        publication_id: str | None = None,
        teacher_preview: bool = False,
    ) -> tuple[str, str]:
        """Resolve a controlled source asset from a draft or immutable publication.

        Private publications are teacher-visible for authoring and review, but the
        publication-bound asset route is also part of the student surface. The
        caller must therefore pass explicit trusted teacher scope; publication
        metadata alone never grants access to a private source.
        """
        if publication_id:
            publication = self.store.load_publication(publication_id)
            if not publication or publication["coursewareId"] != courseware_id:
                raise LookupError("课件素材不存在")
            if (publication["distributionScope"] not in {"classroom", "public"}
                    and not (teacher_preview and publication["distributionScope"] == "private")):
                raise LookupError("课件素材不存在")
            target = publication["snapshot"]
            document = self.store.load_document(courseware_id)
            if not document or document.get("deletedAt") or document.get("status") == "deleted":
                raise LookupError("课件素材不存在")
            chapter = self.chapters.store.load_chapter(str(target.get("chapterId", "")))
            if not chapter or chapter.get("deletedAt"):
                raise LookupError("课件素材不存在")
            source = next((item for item in chapter.get("sourceRevisions", [])
                           if item.get("sourceRevisionId") == target.get("sourceRevisionId")), None)
            if not source:
                raise LookupError("课件素材不存在")
        else:
            chapter, source, target = self._document(courseware_id)
        asset = next((item for item in target.get("assets", []) if item.get("assetId") == asset_id), None)
        if not asset or asset.get("status") != "available" or asset.get("kind") not in {"source_page", "source_image"}:
            raise LookupError("课件素材不存在")
        ref = next((item for item in asset.get("sourceRefs", [])
                    if item.get("assetId") == asset_id
                    and item.get("sourceRevisionId") == target.get("sourceRevisionId")
                    and int(target.get("pageStart", 0)) <= int(item.get("page", 0)) <= int(target.get("pageEnd", -1))), None)
        if not ref:
            raise ValueError("素材缺少可解析的来源定位")
        from application.services.chapter_source_preview import ChapterSourcePreview

        path = ChapterSourcePreview(self.chapters.store).get(
            chapter["chapterId"], str(ref["sourceRevisionId"]), int(ref["page"]),
        )
        if asset.get("sourceFileSha256") and asset["sourceFileSha256"] != source.get("sourceFileSha256"):
            raise ValueError("素材原文件身份与冻结来源不一致")
        if asset.get("kind") == "source_image":
            from application.services.courseware_assets import crop_source_asset

            path = crop_source_asset(path, source, int(ref["page"]), asset)
        return str(path), "image/png"

    def regenerate_page(self, courseware_id: str, page_id: str, *, expected_record_version: int,
                        reason: str, command_id: str) -> dict[str, Any]:
        initial = self.store.load_document(courseware_id)
        if not initial:
            raise LookupError("课件不存在")
        chapter_id = str(initial["chapterId"])
        idempotency_key = f"courseware-regenerate:{courseware_id}:{page_id}:{command_id}"
        request_hash = hashlib.sha256(
            f"{courseware_id}:{page_id}:{expected_record_version}:{reason.strip()}".encode(),
        ).hexdigest()
        with self.chapters.store.chapter_lock(chapter_id):
            with self.store.atomic() as connection:
                chapter, source = self._chapter(chapter_id, lock=True)
                document = self.store.load_document(courseware_id, lock=True)
                if not document or document.get("status") == "deleted":
                    raise LookupError("课件不存在")
                existing_job = self.jobs.get_by_idempotency_key(idempotency_key, connection=connection, lock=True)
                if existing_job:
                    if (existing_job.get("payload") or {}).get("requestHash") != request_hash:
                        raise ValueError("重做命令已用于其他页面内容或版本")
                    if document.get("generationJobId") != existing_job["jobId"]:
                        raise ValueError("该重做命令已被更新课件版本取代")
                    return {"document": self._with_asset_urls(document), "job": self._job_summary(existing_job)}
                if int(document["recordVersion"]) != expected_record_version:
                    raise ValueError("课件已更新，请刷新后重做页面")
                if document.get("sourceRevisionId") != source["sourceRevisionId"]:
                    raise ValueError("教材来源已变化，旧课件不能继续编辑")
                if document.get("generationStatus") in {"queued", "running"}:
                    raise ValueError("课件仍有生成任务正在运行")
                page = next((item for item in document["pages"] if item["pageId"] == page_id), None)
                if not page:
                    raise LookupError("课件页面不存在")
                profile = self.profiles.get(str(document["subject"]))
                template = self._prompt(profile.page_prompt_id)
                selected = self._selected_pages(source, int(document["pageStart"]), int(document["pageEnd"]))
                page_source_numbers = {ref.page for ref in page.get("sourceRefs", [])}
                page_source = [item for item in selected if int(item["page"]) in page_source_numbers]
                if not page_source:
                    raise ValueError("页面来源引用不在课件选定页段内")
                old_job_id = document.get("generationJobId")
                job_id = uuid.uuid4().hex
                page["generationStatus"] = "queued"
                page["reviewStatus"] = "pending"
                page["error"] = None
                page["version"] = int(page["version"]) + 1
                document["generationStatus"] = "queued"
                document["reviewStatus"] = "pending"
                document["status"] = "draft"
                document["generationJobId"] = job_id
                document["updatedAt"] = time.time()
                saved = self.store.save_record(
                    record_id=f"document:{courseware_id}", record_type="document",
                    chapter_id=chapter["chapterId"], courseware_id=courseware_id,
                    source_revision_id=source["sourceRevisionId"], payload=document,
                    status="draft", expected_version=expected_record_version, connection=connection,
                )
                if old_job_id:
                    self.jobs.request_cancel(str(old_job_id), connection=connection)
                plan = self.store.load_plan(chapter_id, lock=True)
                payload = {
                    "chapterId": chapter_id, "chapterVersion": int(chapter["recordVersion"]),
                    "sourceRevisionId": source["sourceRevisionId"], "planVersion": int(document["planVersion"]),
                    "coursewareId": courseware_id, "documentVersion": int(saved["recordVersion"]),
                    "pageIds": [page_id], "pageVersions": {page_id: int(page["version"])},
                    "sourcePages": self._source_view(source, page_source)["pages"],
                    "sourceAssets": {
                        page_id: self._assets_for_page(
                            document.get("assets", []), page_source_numbers,
                        ),
                    },
                    "objectives": (plan or {}).get("objectives", []),
                    "audience": document.get("audience", ""), "languagePolicy": document.get("languagePolicy", ""),
                    "frozenTemplate": self._frozen_template(template),
                    "runtimeSnapshot": current_job_runtime_snapshot(), "regenerationReason": reason.strip(),
                    "requestHash": request_hash,
                }
                job = self.jobs.create_job(
                    "courseware.document.generate", payload, job_id=job_id,
                    idempotency_key=idempotency_key, max_attempts=2,
                    connection=connection, reject_payload_mismatch=True,
                )
                return {"document": saved, "job": self._job_summary(job)}

    def run_document_generation(
        self, payload: dict[str, Any], cancellation_check: Any, context: JobExecutionContext,
    ) -> dict[str, Any]:
        chapter, source, document = self._document(str(payload["coursewareId"]))
        if int(chapter["recordVersion"]) != int(payload["chapterVersion"]):
            raise TerminalJobError("教材章节已变化，旧页面生成未写入")
        if document.get("generationJobId") != context.job_id:
            raise TerminalJobError("页面生成任务已被新编辑或新任务取代")
        if int(document["planVersion"]) != int(payload["planVersion"]):
            raise TerminalJobError("课件规划已变化，旧页面生成未写入")
        selected = self._selected_pages(source, int(document["pageStart"]), int(document["pageEnd"]))
        source_pages_by_number = {int(item["page"]): item for item in payload["sourcePages"]}
        template = self._template_from_payload(payload["frozenTemplate"])
        selection = payload["runtimeSnapshot"]["generation"]
        expected_doc_version = int(document["recordVersion"])
        page_ids = list(payload["pageIds"])
        page_versions = {str(key): int(value) for key, value in (payload.get("pageVersions") or {}).items()}
        active_page_id: str | None = None
        try:
            with use_job_runtime_snapshot(payload):
                for index, page_id in enumerate(page_ids):
                    active_page_id = str(page_id)
                    if cancellation_check():
                        raise JobCancelled()
                    current_doc = self.store.load_document(str(payload["coursewareId"]))
                    if not current_doc or current_doc.get("generationJobId") != context.job_id:
                        raise TerminalJobError("课件已被新编辑或新任务取代")
                    outline = next((item for item in current_doc["pages"] if item["pageId"] == page_id), None)
                    if not outline:
                        raise TerminalJobError("目标页面不存在")
                    pinned_page_version = page_versions.get(str(page_id), int(outline["version"]))
                    if (outline.get("generationStatus") == "completed"
                            and int(outline["version"]) == pinned_page_version + 1):
                        # A previous attempt may have committed this page before
                        # losing its lease. Reuse that checkpoint instead of
                        # paying for the same model call again.
                        expected_doc_version = int(current_doc["recordVersion"])
                        continue
                    if int(outline["version"]) != pinned_page_version:
                        raise TerminalJobError("目标页面版本已被教师编辑或其他任务更新")
                    refs = [CoursewareSourceRef.model_validate(item) for item in outline.get("sourceRefs", [])]
                    page_numbers = {ref.page for ref in refs}
                    page_source = [source_pages_by_number[number] for number in sorted(page_numbers) if number in source_pages_by_number]
                    if not page_source:
                        raise ValueError("页面大纲没有可用于生成的来源页")
                    page_assets = list((payload.get("sourceAssets") or {}).get(str(page_id), []))
                    allowed_asset_ids = {
                        str(item.get("assetId")) for item in page_assets
                        if item.get("status") == "available" and item.get("assetId")
                    }
                    section = next((item for item in current_doc["sections"] if outline["pageId"] in item["pageIds"]), None)
                    objectives = [item for item in payload.get("objectives", []) if item.get("objectiveId") in outline.get("objectiveIds", [])]
                    prompt = template.render(
                        chapter=json.dumps({"title": chapter["title"], "subject": chapter["subject"]}, ensure_ascii=False),
                        section=json.dumps(section or {}, ensure_ascii=False),
                        page_outline=json.dumps({key: outline.get(key) for key in (
                            "pageId", "title", "pageKind", "layout", "objectiveIds", "sourceRefs", "estimatedMinutes",
                        )}, ensure_ascii=False),
                        objectives=json.dumps(objectives, ensure_ascii=False),
                        audience=str(payload.get("audience", document.get("audience", ""))),
                        language_policy=str(payload.get("languagePolicy", document.get("languagePolicy", ""))),
                        source=json.dumps({
                            "sourceRevisionId": source["sourceRevisionId"],
                            "pages": page_source,
                            "assets": page_assets,
                        }, ensure_ascii=False),
                    )
                    draft, run = self.runtime.generate_json(
                        prompt, generation_schema(CoursewarePageDraftOutput), max_tokens=3600,
                        task="courseware_page_generation",
                        selection=ModelSelection(selection["provider"], selection["model"]),
                    )
                    content = CoursewarePageDraftOutput.model_validate(draft)
                    self._validate_refs(content, source, page_source)
                    for block in content.blocks:
                        if isinstance(block, CoursewareImageBlock) and block.assetId not in allowed_asset_ids:
                            raise ValueError("生成页面引用了未提供或未登记的受控教材素材")
                    if not content.blocks:
                        raise ValueError(f"页面 {page_id} 生成内容为空")
                    activity_ids = {item.activityId for item in content.blocks if isinstance(item, CoursewareActivityBlock)}
                    if any(not set(item.objectiveIds).issubset(set(outline.get("objectiveIds", []))) for item in content.blocks if isinstance(item, CoursewareActivityBlock)):
                        raise ValueError("页面互动活动引用了未声明的教学目标")
                    page_value = {
                        **outline,
                        "blocks": [item.model_dump() for item in content.blocks],
                        "teacherNotes": content.teacherNotes,
                        "revealSteps": [
                            {**item.model_dump(), "reviewStatus": "pending"} for item in content.revealSteps
                        ],
                        "attributionText": source.get("attributionText"),
                        "generationAudit": self._runtime_audit(run, payload["frozenTemplate"]),
                        "generationStatus": "completed", "reviewStatus": "pending",
                        "version": int(outline["version"]), "error": None,
                    }
                    _ = activity_ids
                    candidate = copy.deepcopy(current_doc)
                    replace_index = next(i for i, item in enumerate(candidate["pages"]) if item["pageId"] == page_id)
                    candidate["pages"][replace_index] = {**page_value, "version": int(outline["version"]) + 1}
                    candidate["generationStatus"] = "running"
                    self._validate_refs(candidate, source, selected)
                    CoursewareDocument.model_validate(candidate)
                    saved = self.store.save_generated_page(
                        context=context,
                        chapter_id=str(payload["chapterId"]),
                        chapter_version=int(payload["chapterVersion"]),
                        source_revision_id=str(payload["sourceRevisionId"]),
                        plan_version=int(payload["planVersion"]),
                        courseware_id=str(payload["coursewareId"]),
                        expected_document_version=expected_doc_version,
                        page_id=page_id,
                        page_version=pinned_page_version,
                        generated_page=page_value,
                        progress=20 + int(70 * (index + 1) / max(len(page_ids), 1)),
                        now=time.time(),
                    )
                    expected_doc_version = int(saved["recordVersion"])
            final = self.store.finish_generation(
                context=context,
                chapter_id=str(payload["chapterId"]), chapter_version=int(payload["chapterVersion"]),
                source_revision_id=str(payload["sourceRevisionId"]), plan_version=int(payload["planVersion"]),
                courseware_id=str(payload["coursewareId"]), expected_document_version=expected_doc_version,
                now=time.time(),
            )
            return {"coursewareId": payload["coursewareId"], "generationStatus": "completed", "recordVersion": final["recordVersion"]}
        except JobCancelled:
            raise
        except Exception as error:
            self.store.mark_generation_failed(
                context=context,
                chapter_id=str(payload["chapterId"]), chapter_version=int(payload["chapterVersion"]),
                source_revision_id=str(payload["sourceRevisionId"]), plan_version=int(payload["planVersion"]),
                courseware_id=str(payload["coursewareId"]), expected_document_version=expected_doc_version,
                error=str(error), failed_page_id=active_page_id, now=time.time(),
            )
            raise

    def edit_page(self, courseware_id: str, page_id: str, *, content: CoursewarePageContent,
                  expected_record_version: int) -> dict[str, Any]:
        initial = self.store.load_document(courseware_id)
        if not initial:
            raise LookupError("课件不存在")
        chapter_id = str(initial["chapterId"])
        with self.chapters.store.chapter_lock(chapter_id):
            with self.store.atomic():
                return self._edit_page_locked(
                    courseware_id, page_id, content=content,
                    expected_record_version=expected_record_version,
                )

    def _edit_page_locked(self, courseware_id: str, page_id: str, *, content: CoursewarePageContent,
                          expected_record_version: int) -> dict[str, Any]:
        chapter, source, document = self._document(courseware_id, lock=True)
        if int(document["recordVersion"]) != expected_record_version:
            raise ValueError("课件已更新，请刷新后重新编辑")
        if document.get("generationStatus") in {"queued", "running"}:
            raise ValueError("页面生成中，暂时不能编辑")
        page = next((item for item in document["pages"] if item["pageId"] == page_id), None)
        if not page:
            raise LookupError("课件页面不存在")
        selected = self._selected_pages(source, int(document["pageStart"]), int(document["pageEnd"]))
        self._validate_refs(content, source, selected)
        plan = self.store.load_plan(chapter["chapterId"], lock=True)
        objectives = {item["objectiveId"] for item in (plan or {}).get("objectives", [])}
        if not set(content.objectiveIds).issubset(objectives):
            raise ValueError("页面只能引用已声明的教学目标")
        asset_ids = {item["assetId"] for item in document.get("assets", []) if item.get("status") == "available"}
        for block in content.blocks:
            if isinstance(block, CoursewareActivityBlock) and not set(block.objectiveIds).issubset(objectives):
                raise ValueError("活动只能引用已声明的教学目标")
            if isinstance(block, CoursewareImageBlock) and block.assetId not in asset_ids:
                raise ValueError("图片块必须引用课件中已登记且可用的素材")
        page.update(content.model_dump())
        page["version"] = int(page["version"]) + 1
        page["reviewStatus"] = "pending"
        page["generationStatus"] = "completed"
        page["error"] = None
        section = next(item for item in document["sections"] if page_id in item["pageIds"])
        section["estimatedMinutes"] = sum(
            int(item["estimatedMinutes"]) for item in document["pages"] if item["pageId"] in section["pageIds"]
        )
        document["durationMinutes"] = sum(int(item["estimatedMinutes"]) for item in document["sections"])
        if document["durationMinutes"] < 10 or document["durationMinutes"] > 120:
            raise ValueError("课件总时长必须在 10 到 120 分钟之间")
        document["status"] = "draft"
        document["reviewStatus"] = "pending"
        document["reviewIssues"] = [item for item in document.get("reviewIssues", []) if item.get("pageId") != page_id]
        document["updatedAt"] = time.time()
        self._validate_refs(document, source, selected)
        validated = CoursewareDocument.model_validate(document).model_dump()
        saved = self.store.save_record(
            record_id=f"document:{courseware_id}", record_type="document",
            chapter_id=chapter["chapterId"], courseware_id=courseware_id,
            source_revision_id=source["sourceRevisionId"], payload=validated,
            status="draft", expected_version=expected_record_version,
        )
        return self._with_asset_urls(saved)

    def review_document(self, courseware_id: str, *, decision: str, page_ids: list[str],
                        note: str, expected_record_version: int, actor_id: str) -> dict[str, Any]:
        initial = self.store.load_document(courseware_id)
        if not initial:
            raise LookupError("课件不存在")
        chapter_id = str(initial["chapterId"])
        with self.chapters.store.chapter_lock(chapter_id):
            with self.store.atomic():
                return self._review_document_locked(
                    courseware_id, decision=decision, page_ids=page_ids,
                    note=note, expected_record_version=expected_record_version,
                    actor_id=actor_id,
                )

    def _review_document_locked(self, courseware_id: str, *, decision: str, page_ids: list[str],
                                note: str, expected_record_version: int, actor_id: str) -> dict[str, Any]:
        chapter, source, document = self._document(courseware_id, lock=True)
        if int(document["recordVersion"]) != expected_record_version:
            raise ValueError("课件已更新，请刷新后重新审核")
        selected = self._selected_pages(source, int(document["pageStart"]), int(document["pageEnd"]))
        pages = document.get("pages", [])
        targets = page_ids or [item["pageId"] for item in pages]
        if len(targets) != len(set(targets)):
            raise ValueError("审核页面不能重复")
        if set(targets) - {item["pageId"] for item in pages}:
            raise LookupError("审核目标页面不存在")
        target_pages = [item for item in pages if item["pageId"] in targets]
        if any(item["generationStatus"] != "completed" for item in target_pages):
            raise ValueError("页面生成完成后才能审核")
        self._validate_refs(target_pages, source, selected)
        if decision == "approve":
            issues = document_page_issues(
                self._with_asset_urls({**document, "pages": target_pages}), selected,
            )
            if issues:
                raise ValueError("页面未通过结构与来源质量检查：" + "; ".join(str(item["message"]) for item in issues[:8]))
        for page in target_pages:
            page["reviewStatus"] = "approved" if decision == "approve" else "changes_requested"
        now = time.time()
        records = list(document.get("reviewRecords", []))
        records.append({"actorId": actor_id, "decision": decision, "note": note[:2000], "pageIds": targets, "createdAt": now})
        document["reviewRecords"] = records[-500:]
        ready = all(item.get("reviewStatus") == "approved" for item in pages)
        document["reviewStatus"] = "ready" if ready else ("changes_requested" if decision == "request_changes" else "in_review")
        document["status"] = "ready" if ready else "in_review"
        document["updatedAt"] = now
        validated = CoursewareDocument.model_validate(document).model_dump()
        saved = self.store.save_record(
            record_id=f"document:{courseware_id}", record_type="document",
            chapter_id=chapter["chapterId"], courseware_id=courseware_id,
            source_revision_id=source["sourceRevisionId"], payload=validated,
            status=document["status"], expected_version=expected_record_version,
        )
        return self._with_asset_urls(saved)

    def publish(self, courseware_id: str, *, expected_record_version: int) -> dict[str, Any]:
        initial = self.store.load_document(courseware_id)
        if not initial:
            raise LookupError("课件不存在")
        chapter_id = str(initial["chapterId"])
        with self.chapters.store.chapter_lock(chapter_id):
            with self.store.atomic() as connection:
                chapter, source, document = self._document(courseware_id, lock=True)
                plan = self.store.load_plan(chapter_id, lock=True)
                if (not plan or plan.get("sourceRevisionId") != source["sourceRevisionId"]
                        or int(plan.get("planVersion", 0)) != int(document.get("planVersion", -1))):
                    raise ValueError("教材来源或课件规划已变化，不能发布旧草稿")
                if int(document["recordVersion"]) != expected_record_version:
                    raise ValueError("课件已更新，请刷新后重新发布")
                return self._publish_locked(
                    connection, chapter, source, document,
                    expected_record_version=expected_record_version,
                )

    def _publish_locked(self, connection: Any, chapter: dict[str, Any], source: dict[str, Any],
                        document: dict[str, Any], *, expected_record_version: int) -> dict[str, Any]:
        courseware_id = str(document["coursewareId"])
        if int(document["recordVersion"]) != expected_record_version:
            raise ValueError("课件已更新，请刷新后重新发布")
        if document.get("status") != "ready" or document.get("reviewStatus") != "ready":
            raise ValueError("课件所有页面必须通过教师审核后才能发布")
        if document.get("generationStatus") != "completed" or any(item.get("generationStatus") != "completed" for item in document.get("pages", [])):
            raise ValueError("课件所有页面必须完成生成后才能发布")
        if document.get("reviewIssues"):
            raise ValueError("课件仍有未解决的质量问题，不能发布")
        if not source.get("license") or not source.get("attributionText"):
            raise ValueError("发布前必须登记教材许可与署名文本")
        selected = self._selected_pages(source, int(document["pageStart"]), int(document["pageEnd"]))
        issues = document_page_issues(self._with_asset_urls(document), selected)
        if issues:
            raise ValueError("课件未通过结构与来源质量检查：" + "; ".join(str(item["message"]) for item in issues[:8]))
        self._validate_refs(document, source, selected)
        scope = str(source.get("distributionScope") or "private")
        order = {"private": 0, "classroom": 1, "public": 2}
        for asset in document.get("assets", []):
            if asset.get("status") != "available" or not asset.get("sourceRefs"):
                raise ValueError("所有发布素材必须有可用状态和可解析来源")
            self._validate_refs(asset["sourceRefs"], source, selected)
            if order.get(str(asset.get("distributionScope")), -1) < order[scope]:
                raise ValueError("课件素材许可范围小于本次发布范围")
            if asset.get("origin") == "textbook" and (not asset.get("license") or not asset.get("attributionText")):
                raise ValueError("发布中的教材素材必须登记许可和署名")
            if asset.get("kind") not in {"source_page", "source_image"}:
                raise ValueError("仅发布由受控来源预览服务解析的原页或冻结区域素材")
            self.get_chapter_assets(courseware_id, str(asset["assetId"]))
        snapshot = CoursewareDocument.model_validate({
            **document, "status": "published", "publicationId": None,
        }).model_dump()
        publication_id = uuid.uuid4().hex
        latest = self.store.latest_publication(str(document["coursewareId"]))
        version = int(latest["version"]) + 1 if latest else 1
        snapshot["publicationId"] = publication_id
        snapshot["assets"] = self._with_asset_urls(snapshot)["assets"]
        now = time.time()
        publication = {
            "publicationId": publication_id, "coursewareId": courseware_id,
            "version": version, "sourceRevisionId": source["sourceRevisionId"],
            "sourceTitle": source.get("sourceTitle") or chapter["title"],
            "sourceUrl": source.get("sourceUrl"), "attributionText": source["attributionText"],
            "distributionScope": scope, "snapshot": snapshot,
            "reviewRecords": document.get("reviewRecords", []), "publishedAt": now,
        }
        # This transaction still owns the chapter advisory lock and has re-read
        # the source, plan, and document; publication and current pointer commit together.
        current = self.store.load_document(str(document["coursewareId"]), lock=True)
        if not current or current.get("recordVersion") != expected_record_version:
            raise ValueError("课件已更新，请刷新后重新发布")
        self.store.save_publication(publication, connection=connection)
        current["status"] = "published"
        current["publicationId"] = publication_id
        current["version"] = version
        current["updatedAt"] = now
        self.store.save_record(
            record_id=f"document:{document['coursewareId']}", record_type="document",
            chapter_id=chapter["chapterId"], courseware_id=document["coursewareId"],
            source_revision_id=source["sourceRevisionId"], payload=current,
            status="published", expected_version=expected_record_version,
            connection=connection,
        )
        return publication

    def get_presentation(self, courseware_id: str, publication_id: str | None = None) -> dict[str, Any]:
        publication = self.store.load_publication(publication_id) if publication_id else self.store.latest_publication(courseware_id)
        if not publication or publication["coursewareId"] != courseware_id:
            raise LookupError("课件发布版本不存在")
        self._assert_publication_visible(courseware_id, publication)
        document = CoursewareDocument.model_validate(publication["snapshot"]).model_dump()
        document = self._with_asset_urls(document, publication_id=publication["publicationId"])
        return {
            "coursewareId": courseware_id,
            "publicationId": publication["publicationId"],
            "version": publication["version"],
            "sourceTitle": publication.get("sourceTitle"),
            "sourceUrl": publication.get("sourceUrl"),
            "sourceLicense": document.get("sourceLicense"),
            "attributionText": publication.get("attributionText"),
            "distributionScope": publication["distributionScope"],
            "document": document,
        }

    def get_published(self, courseware_id: str, publication_id: str | None = None) -> dict[str, Any]:
        publication = self.store.load_publication(publication_id) if publication_id else self.store.latest_publication(courseware_id)
        if (not publication or publication["coursewareId"] != courseware_id
                or publication["distributionScope"] not in {"classroom", "public"}):
            raise LookupError("课件不存在")
        self._assert_publication_visible(courseware_id, publication)
        snapshot = copy.deepcopy(publication["snapshot"])
        assets = self._with_asset_urls(snapshot, publication_id=publication["publicationId"])["assets"]
        pages = []
        for page in snapshot.get("pages", []):
            blocks = []
            for block in page.get("blocks", []):
                if block.get("kind") != "activity":
                    blocks.append(block)
                    continue
                activity = CoursewarePublicActivityBlock.model_validate({
                    key: value for key, value in block.items()
                    if key in CoursewarePublicActivityBlock.model_fields
                } | {"selectionMode": block.get("selectionMode") or (
                    "multiple" if isinstance(block.get("answerKey"), list) and len(block["answerKey"]) > 1 else "single"
                )})
                blocks.append(activity.model_dump())
            pages.append(CoursewarePublicPage.model_validate({
                "pageId": page["pageId"], "sectionId": page["sectionId"],
                "pageKind": page["pageKind"], "layout": page["layout"], "title": page["title"],
                "objectiveIds": page.get("objectiveIds", []), "sourceRefs": page.get("sourceRefs", []),
                "attributionText": publication.get("attributionText"),
                "blocks": blocks, "estimatedMinutes": page["estimatedMinutes"],
            }).model_dump())
        public = CoursewarePublicDocument.model_validate({
            "coursewareId": courseware_id, "publicationId": publication["publicationId"],
            "version": publication["version"], "subject": snapshot["subject"],
            "title": snapshot["title"], "sourceTitle": publication.get("sourceTitle"),
            "sourceUrl": publication.get("sourceUrl"), "sourceLicense": snapshot.get("sourceLicense"),
            "attributionText": publication.get("attributionText"),
            "sourceDistributionScope": snapshot.get("sourceDistributionScope", publication["distributionScope"]),
            "durationMinutes": snapshot["durationMinutes"], "sections": snapshot["sections"],
            "pages": pages, "assets": assets, "status": "published",
        }).model_dump()
        return public

    def attempt(self, courseware_id: str, *, learner_id: str, request: CoursewareAttemptRequest) -> dict[str, Any]:
        publication = self.store.load_publication(request.publicationId)
        if (not publication or publication["coursewareId"] != courseware_id
                or publication["distributionScope"] not in {"classroom", "public"}):
            raise LookupError("课件不存在")
        self._assert_publication_visible(courseware_id, publication)
        snapshot = publication["snapshot"]
        activity = next((block for page in snapshot.get("pages", []) for block in page.get("blocks", [])
                         if block.get("kind") == "activity" and block.get("activityId") == request.activityId), None)
        if not activity:
            raise LookupError("互动活动不存在")
        assessment = assess_activity(activity, request.response)
        feedback = "作答已记录。"
        attempt = self.store.save_attempt({
            "attemptId": uuid.uuid4().hex,
            "publicationId": request.publicationId,
            "coursewareId": courseware_id,
            "learnerId": learner_id,
            "activityId": request.activityId,
            "commandId": request.commandId,
            "response": request.response,
            "assessment": assessment,
            "feedback": feedback,
            "createdAt": time.time(),
        })
        return {key: attempt[key] for key in ("attemptId", "publicationId", "activityId", "assessment", "feedback", "createdAt")}

    def _assert_publication_visible(self, courseware_id: str, publication: dict[str, Any]) -> None:
        """Retain immutable history while withdrawing delivery after parent deletion."""
        document = self.store.load_document(courseware_id)
        chapter_id = publication["snapshot"].get("chapterId")
        chapter = self.chapters.store.load_chapter(str(chapter_id or ""))
        if (not document or document.get("deletedAt") or document.get("status") == "deleted"
                or not chapter or chapter.get("deletedAt")):
            raise LookupError("课件不存在")

    def delete_draft(self, courseware_id: str, *, expected_record_version: int) -> dict[str, str]:
        chapter, source, document = self._document(courseware_id)
        if int(document["recordVersion"]) != expected_record_version:
            raise ValueError("课件已更新，请刷新后重试")
        if document.get("publicationId") or document.get("status") == "published":
            raise ValueError("已发布课件不可删除")
        if any(item.get("status") == "published" for item in document.get("publications", [])):
            raise ValueError("已有不可变发布版本的课件不可删除")
        document["status"] = "deleted"
        document["generationStatus"] = "cancelled"
        document["updatedAt"] = time.time()
        self.store.save_record(
            record_id=f"document:{courseware_id}", record_type="document",
            chapter_id=chapter["chapterId"], courseware_id=courseware_id,
            source_revision_id=source["sourceRevisionId"], payload=document,
            status="deleted", expected_version=expected_record_version,
            deleted_at=document["updatedAt"],
        )
        if document.get("generationJobId"):
            self.jobs.request_cancel(document["generationJobId"])
        return {"coursewareId": courseware_id, "status": "deleted"}
