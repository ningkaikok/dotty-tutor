"""Organize retained courseware batches as one chapter-backed learning unit."""

import time
from typing import Any

from domain.contracts.courseware_unit import CoursewarePositionSave
from domain.courseware.batches import owns_document
from persistence.courseware_unit_store import CoursewareUnitStore


class CoursewareUnitService:
    """Resolve delivery through the existing publication boundary on every read."""

    def __init__(self, *, courseware: Any, store: CoursewareUnitStore) -> None:
        self.courseware = courseware
        self.store = store

    def _chapter(self, chapter_id: str) -> dict[str, Any]:
        chapter = self.courseware.chapters.store.load_chapter(chapter_id)
        if not chapter or chapter.get("deletedAt"):
            raise LookupError("单元不存在或已撤回")
        return chapter

    def _visible_publication(self, document: dict[str, Any], publication_id: str | None = None,
                             *, teacher: bool = False) -> dict[str, Any] | None:
        try:
            if teacher:
                return self.courseware.get_presentation(document["coursewareId"], publication_id)
            return self.courseware.get_published(document["coursewareId"], publication_id)
        except (LookupError, ValueError, PermissionError):
            return None

    def directory(self, chapter_id: str, *, learner_id: str | None = None,
                  teacher: bool = False) -> dict[str, Any]:
        chapter = self._chapter(chapter_id)
        plan = self.courseware.store.load_plan(chapter_id)
        documents = self.courseware.store.list_documents(chapter_id)
        positions = self.store.positions(learner_id, chapter_id) if learner_id else {}
        lessons = []
        batches = [*(plan or {}).get("retainedBatches", []), plan or {}]
        session_order = {courseware_id: index for batch in batches
                         for index, courseware_id in enumerate(batch.get("coursewareIds", []))}
        documents.sort(key=lambda document: (
            int(document.get("pageStart", 0)), int(document.get("planVersion", 0)),
            session_order.get(document["coursewareId"], 1_000),
            float(document.get("createdAt", 0)), str(document["coursewareId"]),
        ))
        for document in documents:
            if document.get("deletedAt") or document.get("status") == "deleted":
                continue
            source = (chapter.get("sourceRevisions") or [{}])[-1]
            current = (owns_document(plan, document) if plan else True) and document.get("sourceRevisionId") == source.get("sourceRevisionId")
            if not teacher and not current:
                continue
            publication = self._visible_publication(document, teacher=teacher)
            if not teacher and not publication:
                continue
            snapshot = publication.get("document", publication) if publication else document
            source_pages = [int(ref["page"]) for page in snapshot.get("pages", []) for ref in page.get("sourceRefs", [])]
            position = positions.get(document["coursewareId"])
            if position:
                position = {**position, "available": bool(self._visible_publication(document, position["publicationId"]))}
            activities = {block["activityId"] for page in snapshot.get("pages", [])
                          for block in page.get("blocks", []) if block.get("kind") == "activity"}
            # Only the frozen version displayed by the lesson contributes to its summary.
            latest = {attempt["activityId"]: attempt["assessment"] for attempt in self.store.attempts(
                learner_id, publication["publicationId"],
            )} if learner_id and publication else {}
            answered = activities.intersection(latest)
            corrections = sum(latest[activity] in {"incorrect", "needs_review"} for activity in answered)
            lessons.append({
                "coursewareId": document["coursewareId"], "title": snapshot["title"],
                "order": len(lessons) + 1, "pageStart": min(source_pages, default=document["pageStart"]),
                "pageEnd": max(source_pages, default=document["pageEnd"]),
                "durationMinutes": snapshot["durationMinutes"], "status": document["status"],
                "currentBatch": current, "sourceRevisionId": document["sourceRevisionId"],
                "publicationId": publication["publicationId"] if publication else None,
                "publicationVersion": publication["version"] if publication else None,
                "activityCount": len(activities), "answeredActivityCount": len(answered),
                "needsCorrectionCount": corrections,
                "activityStatus": "needs_correction" if corrections else "answered" if answered else "not_started",
                "progress": position,
            })
        resume = max((lesson["progress"] for lesson in lessons if lesson["progress"]),
                     key=lambda position: position["updatedAt"], default=None)
        return {"chapterId": chapter_id, "title": chapter["title"], "subject": chapter["subject"],
                "lessons": lessons, "resume": resume}

    def directory_for_courseware(self, courseware_id: str, *, learner_id: str,
                                teacher: bool = False) -> dict[str, Any]:
        document = self.courseware.store.load_document(courseware_id)
        if not document or (not teacher and not self._visible_publication(document)):
            raise LookupError("课件不存在或不可访问")
        return self.directory(document["chapterId"], learner_id=learner_id, teacher=teacher)

    def list_units(self, *, learner_id: str) -> dict[str, Any]:
        units = []
        for chapter in self.courseware.chapters.store.list_chapters():
            if chapter.get("deletedAt"):
                continue
            directory = self.directory(chapter["chapterId"], learner_id=learner_id)
            if directory["lessons"]:
                units.append(directory)
        units.sort(key=lambda unit: -(unit["resume"]["updatedAt"] if unit["resume"] else 0))
        return {"items": units}

    def save_position(self, courseware_id: str, *, learner_id: str, request: CoursewarePositionSave,
                      now: float | None = None) -> dict[str, Any]:
        publication = self.courseware.get_published(courseware_id, request.publicationId)
        if not any(page["pageId"] == request.pageId for page in publication["pages"]):
            raise LookupError("页面不属于指定发布版本")
        document = self.courseware.store.load_document(courseware_id)
        if not document:
            raise LookupError("课件不存在")
        return self.store.save_position(
            learner_id=learner_id, chapter_id=document["chapterId"], courseware_id=courseware_id,
            publication_id=request.publicationId, page_id=request.pageId, expected_version=request.expectedVersion,
            command_id=request.commandId, now=time.time() if now is None else now,
        )
