"""Read current courseware drafts into the ordinary material review inbox."""
from typing import Any


def courseware_review_tasks(chapter_store: Any, courseware_store: Any, *, chapter_id: str | None, upload_id: str | None) -> list[dict[str, Any]]:
    if chapter_store is None or courseware_store is None:
        return []
    tasks = []
    for chapter in chapter_store.list_chapters():
        if chapter.get("deletedAt") or (chapter_id and chapter["chapterId"] != chapter_id):
            continue
        revisions = chapter.get("sourceRevisions") or []
        if not revisions:
            continue
        source = revisions[-1]
        if upload_id and source.get("uploadId") != upload_id:
            continue
        plan = courseware_store.load_plan(chapter["chapterId"])
        for document in courseware_store.list_documents(chapter["chapterId"]):
            if document.get("sourceRevisionId") != source["sourceRevisionId"] or document.get("status") == "published":
                continue
            if not plan or document.get("planVersion") != plan.get("planVersion"):
                continue
            pending = sum(page.get("reviewStatus") != "approved" for page in document.get("pages", []))
            tasks.append({"coursewareId": document["coursewareId"], "chapterId": chapter["chapterId"],
                "sourceRevisionId": source["sourceRevisionId"], "sourceKey": f"sha256:{source.get('sourceFileSha256')}" if source.get("sourceFileSha256") else f"chapter:{chapter['chapterId']}",
                "uploadId": source.get("uploadId"), "title": document["title"], "status": document["status"],
                "pageCount": len(document.get("pages", [])), "pendingPageCount": pending})
    return tasks
