"""在不修改旧版的前提下创建可审核的试卷新版本。"""

from __future__ import annotations

import copy
import re
import time
import uuid
from typing import Any

from lesson_contracts import lesson_document_from_payload
from run_audit import RunAudit, build_run_config


class PublicationRevisionService:
    """重新生成来源批次，再保存全新的课程和试卷标识。

    处理服务以 ``persist=False`` 临时模式运行；只有所有原题都能映射到新结果后才写入新版课程。
    因此中途失败不会污染原教材批次，已发布试卷也始终指向原来的不可变数据。
    """

    def __init__(self, *, store: Any, processing_service: Any) -> None:
        self.store = store
        self.processing_service = processing_service
        self.audit = RunAudit(store)

    @staticmethod
    def _revision_lesson_id(original_id: str, version: int, token: str) -> str:
        suffix = f"-v{version}-{token}"
        return f"{original_id[:128 - len(suffix)]}{suffix}"

    def create(self, publication_id: str) -> dict[str, Any]:
        """从指定版本创建下一个 ``in_review`` 版本并返回新版题目。"""
        publication = self.store.load_publication(publication_id)
        if not publication:
            raise LookupError("互动试卷不存在")
        upload_id = publication.get("sourceUploadId")
        if not upload_id:
            raise ValueError("这份试卷缺少原教材来源，无法整套重新生成")
        lessons = publication.get("lessons") or []
        if not lessons:
            raise ValueError("这份试卷没有可重新生成的题目")

        processing_ocr_runtime = getattr(self.processing_service, "ocr_runtime", None)
        processing_ocr_selection = getattr(processing_ocr_runtime, "selection", None)
        run_config_kwargs: dict[str, Any] = {
            "operation_details": {"force": True, "refreshOcr": False},
        }
        if processing_ocr_selection is not None:
            run_config_kwargs["ocr_run"] = {
                "provider": processing_ocr_selection.provider,
                "fallback": False,
            }
        run = self.audit.start(
            "publication_rereview",
            "publication",
            upload_id=upload_id,
            publication_id=publication_id,
            config=build_run_config(**run_config_kwargs),
        )
        run_id = run["runId"]

        batch_ids: list[str] = []
        try:
            for lesson in lessons:
                question = (lesson.get("questionPayload") or {}).get("question") or {}
                batch_id = str(question.get("sourceBatchId") or "")
                if not batch_id:
                    raise ValueError("旧题缺少来源批次，请重新上传原 PDF 后生成")
                if batch_id not in batch_ids:
                    batch_ids.append(batch_id)
        except Exception as error:
            self.audit.fail(run_id, error, stage="publication-source")
            raise

        # sourceQuestionKey 是跨重新生成稳定的首选关联键；questionNumber 只作为旧数据兼容回退。
        generated_by_key: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        generated_by_number: dict[tuple[str, str], tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        try:
            for batch_id in batch_ids:
                batch_kwargs = {"persist": False}
                if hasattr(self.processing_service, "audit"):
                    batch_kwargs["run_id"] = run_id
                generated = self.processing_service.process_batch(upload_id, batch_id, True, **batch_kwargs)
                payloads = list(generated.get("questionPayloads") or [])
                cards_list = list(generated.get("guideCards") or [[] for _ in payloads])
                for payload, cards in zip(payloads, cards_list):
                    question = payload.get("question") or {}
                    source_key = str(question.get("sourceQuestionKey") or "")
                    number = str(question.get("questionNumber") or "")
                    if source_key:
                        generated_by_key[source_key] = (payload, cards)
                    generated_by_number[(batch_id, number)] = (payload, cards)

            version = int(publication.get("version") or 1) + 1
            token = uuid.uuid4().hex[:8]
            documents: list[dict[str, Any]] = []
            revised_payloads: list[dict[str, Any]] = []
            revisions: list[dict[str, Any]] = []
            for lesson in lessons:
                old_payload = lesson.get("questionPayload") or {}
                old_question = old_payload.get("question") or {}
                source_key = str(old_question.get("sourceQuestionKey") or "")
                batch_id = str(old_question.get("sourceBatchId") or "")
                number = str(old_question.get("questionNumber") or "")
                candidate = generated_by_key.get(source_key) or generated_by_number.get((batch_id, number))
                if not candidate:
                    raise ValueError(f"无法在新识别结果中定位原题 {number or lesson['lessonId']}")
                payload, guide_cards = copy.deepcopy(candidate)
                question = payload["question"]
                original_question_id = str(old_question.get("id") or lesson["lessonId"])
                new_lesson_id = self._revision_lesson_id(original_question_id, version, token)
                question["id"] = new_lesson_id
                question["revisionOf"] = original_question_id
                question["publicationVersion"] = version
                document = lesson_document_from_payload(
                    payload,
                    source_upload_id=upload_id,
                    guide_cards=guide_cards,
                )
                document["version"] = version
                documents.append(document)
                revised_payloads.append(payload)
                source_key = str(question.get("sourceQuestionKey") or old_question.get("sourceQuestionKey") or "")
                if source_key and hasattr(self.store, "append_question_revision"):
                    revisions.append(self.store.append_question_revision(
                        upload_id=upload_id,
                        source_question_key=source_key,
                        operation="publication_rereview",
                        payload=payload,
                        guide_cards=guide_cards,
                        run_id=run_id,
                    ))

        # 映射全部成功后再开始写库，避免只保存半套新版。publication 最后创建，因此即使
        # 极端情况下中途写库失败，孤立 lesson 也不会进入学生入口。
            for document in documents:
                self.store.save_lesson(document)

            root_title = re.sub(r"\s*·\s*v\d+$", "", publication["title"])
            revised = self.store.create_publication(
                publication_id=uuid.uuid4().hex,
                title=f"{root_title} · v{version}",
                source_upload_id=upload_id,
                lesson_ids=[document["lessonId"] for document in documents],
                status="in_review",
                created_at=time.time(),
                version=version,
                revision_of=publication_id,
            )
            run = self.audit.finish(run_id, result={"publicationId": revised["publicationId"], "revisionCount": len(revisions)})
            return {
                "publication": revised,
                "questionPayloads": revised_payloads,
                "run": run,
                "revisions": revisions,
            }
        except Exception as error:
            self.audit.fail(run_id, error, stage="publication")
            raise
