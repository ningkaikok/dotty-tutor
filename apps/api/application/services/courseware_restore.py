"""Append archived source-bound courseware as pending drafts without restoring approvals."""
from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text

from application.services.chapter_source_preview import file_hash
from domain.chapters.source import sentences
from domain.contracts.courseware import CoursewareDocument, CoursewarePlan


def restore_courseware_draft(service: Any, source_packet: dict[str, Any], plan: dict[str, Any], document: dict[str, Any], *, upload_id: str) -> dict[str, Any]:
    """Keep saved sentence identity, validate all references, and never replace target edits."""
    if document.get("status") != "draft" or document.get("publicationId") or document.get("reviewRecords"):
        raise ValueError("只能恢复未经人工批准的原始草稿")
    if any(page.get("reviewStatus") != "pending" for page in document["pages"]):
        raise ValueError("原始页面含审核决定，不能重建审计")
    payload_hash = hashlib.sha256(json.dumps([source_packet, plan, document], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    chapter_id = hashlib.sha256(("restored-chapter:" + payload_hash).encode()).hexdigest()[:32]
    courseware_id = hashlib.sha256(("restored-courseware:" + payload_hash).encode()).hexdigest()[:32]
    store = service.chapters.store
    with store.atomic() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": payload_hash})
        existing = service.store.load_document(courseware_id)
        if existing:
            return {"created": False, "chapterId": chapter_id, "coursewareId": courseware_id}
        job = store.load_job(upload_id)
        if not job:
            raise LookupError("恢复素材上传记录不存在")
        path = Path(job["directory"]) / "source.pdf"
        if path.is_symlink() or file_hash(path) != source_packet["sourceFileSha256"]:
            raise ValueError("原 PDF 哈希不匹配")
        raw = {"uploadId": upload_id, "sourceVersion": "restored-archive-1", "pages": source_packet["pages"],
               "pageStart": source_packet["pageStart"], "pageEnd": source_packet["pageEnd"],
               "license": document["sourceLicense"], "sourceTitle": source_packet["title"],
               "sourceUrl": document["sourceUrl"], "attributionText": document["attributionText"],
               "distributionScope": "private"}
        source = service.chapters._normalize_source(raw)
        revision_id = document["sourceRevisionId"]
        if revision_id != plan["sourceRevisionId"]:
            raise ValueError("规划和页面来源修订不一致")
        for page in source["pages"]:
            page["sentences"] = sentences(page["text"], page["page"], revision_id)
        source.update(sourceRevisionId=revision_id, issues=service.chapters._source_issues(source), createdAt=time.time())
        chapter = {"chapterId": chapter_id, "subject": document["subject"], "title": "待复核 · " + document["title"],
                   "teachingMode": "tutorial", "status": "needs_review" if source["issues"] else "draft",
                   "version": 1, "recordVersion": 1, "sourceRevisions": [source], "lessons": [],
                   "currentLessonIds": [], "reviewIssues": source["issues"], "publicationId": None,
                   "publications": [], "createdAt": time.time(),
                   "restoreProvenance": {"snapshotSha256": payload_hash, "originalChapterId": document["chapterId"],
                                         "originalCoursewareId": document["coursewareId"], "humanReview": "pending"}}
        draft = copy.deepcopy(document)
        draft.update(chapterId=chapter_id, coursewareId=courseware_id, recordVersion=1,
                     generationJobId=None, reviewRecords=[], publicationId=None, sourceDistributionScope="private")
        draft["assets"] = service._source_assets(source, service._selected_pages(source, draft["pageStart"], draft["pageEnd"]))
        draft = CoursewareDocument.model_validate(draft).model_dump()
        restored_plan = copy.deepcopy(plan)
        restored_plan.update(chapterId=chapter_id, coursewareIds=[courseware_id], recordVersion=1,
                             status="awaiting_confirmation", approvalCommandId=None, generationJobId=None)
        restored_plan = CoursewarePlan.model_validate(restored_plan).model_dump()
        service._validate_refs(draft, source, service._selected_pages(source, draft["pageStart"], draft["pageEnd"]))
        service._validate_refs(restored_plan, source, source["pages"])
        store.create_chapter(chapter)
        for record, kind, record_id, cid in ((restored_plan, "plan", f"plan:{chapter_id}", None), (draft, "document", f"document:{courseware_id}", courseware_id)):
            service.store.save_record(record_id=record_id, record_type=kind, chapter_id=chapter_id,
                courseware_id=cid, source_revision_id=revision_id, payload=record, status=record["status"],
                expected_version=0, connection=connection)
    return {"created": True, "chapterId": chapter_id, "coursewareId": courseware_id}
