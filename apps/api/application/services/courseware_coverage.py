"""Complete-unit structural coverage over the current immutable source revision."""
from __future__ import annotations

from typing import Any

from domain.courseware.batches import owns_document


def coverage_report(chapter: dict[str, Any], source: dict[str, Any], store: Any) -> dict[str, Any]:
    """Separate generated/reviewed/published coverage from teacher quality acceptance.

    Unpublished drafts of a replaced plan are unreachable for editing, so they no
    longer count as planned/generated/reviewed; their pages become gaps again.
    Publications stay visible to students regardless of plan version and count as
    generated, reviewed and published.
    """
    start, end = int(source["pageStart"]), int(source["pageEnd"])
    required = set(range(start, end + 1))
    revision = source["sourceRevisionId"]
    source_pages = {int(page["page"]) for page in source.get("pages", [])} & required
    def refs(page: dict[str, Any]) -> set[int]:
        return {ref["page"] for ref in page.get("sourceRefs", [])
                if ref.get("sourceRevisionId") == revision and type(ref.get("page")) is int} & source_pages
    plan = store.load_plan(chapter["chapterId"])
    planned = set()
    if plan and plan.get("sourceRevisionId") == revision:
        planned = {number for session in plan.get("sessions", []) for section in session.get("sections", [])
                   for page in section.get("pages", []) for number in refs(page)}
    generated: set[int] = set()
    reviewed: set[int] = set()
    published: set[int] = set()
    for doc in store.list_documents(chapter["chapterId"]):
        if doc.get("deletedAt") or doc.get("status") == "deleted" or doc.get("sourceRevisionId") != revision:
            continue
        if owns_document(plan, doc):
            for page in doc.get("pages", []):
                # Retained batches no longer appear in the open plan's sessions.
                planned.update(refs(page))
                if page.get("generationStatus") == "completed":
                    generated.update(refs(page))
                    if page.get("reviewStatus") == "approved":
                        reviewed.update(refs(page))
        publication = store.latest_publication(doc["coursewareId"])
        if publication and publication.get("sourceRevisionId") == revision:
            # A publication was generated and teacher-approved before release, even
            # when its draft belongs to a plan that has since been replaced.
            for page in publication["snapshot"].get("pages", []):
                live = refs(page)
                published.update(live)
                generated.update(live)
                reviewed.update(live)
    return {"chapterId": chapter["chapterId"], "sourceRevisionId": revision,
            "pageStart": start, "pageEnd": end, "sourcePages": sorted(source_pages),
            "plannedPages": sorted(planned), "generatedPages": sorted(generated),
            "reviewedPages": sorted(reviewed), "publishedPages": sorted(published),
            "missingSourcePages": sorted(required - source_pages), "unplannedPages": sorted(required - planned),
            "ungeneratedPages": sorted(required - generated), "unreviewedPages": sorted(required - reviewed),
            "unpublishedPages": sorted(required - published), "complete": required <= source_pages & planned & generated & reviewed & published,
            "qualityAcceptance": "requires_teacher_validation"}
