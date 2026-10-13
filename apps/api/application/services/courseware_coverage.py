"""Complete-unit structural coverage over the current immutable source revision."""
from __future__ import annotations

from typing import Any


def coverage_report(chapter: dict[str, Any], source: dict[str, Any], store: Any) -> dict[str, Any]:
    """Separate generated/reviewed/published coverage from teacher quality acceptance."""
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
        for page in doc.get("pages", []):
            if page.get("generationStatus") == "completed":
                generated.update(refs(page))
                if page.get("reviewStatus") == "approved":
                    reviewed.update(refs(page))
        publication = store.latest_publication(doc["coursewareId"])
        if publication and publication.get("sourceRevisionId") == revision:
            for page in publication["snapshot"].get("pages", []):
                published.update(refs(page))
    return {"chapterId": chapter["chapterId"], "sourceRevisionId": revision,
            "pageStart": start, "pageEnd": end, "sourcePages": sorted(source_pages),
            "plannedPages": sorted(planned), "generatedPages": sorted(generated),
            "reviewedPages": sorted(reviewed), "publishedPages": sorted(published),
            "missingSourcePages": sorted(required - source_pages), "unplannedPages": sorted(required - planned),
            "ungeneratedPages": sorted(required - generated), "unreviewedPages": sorted(required - reviewed),
            "unpublishedPages": sorted(required - published), "complete": required <= source_pages & planned & generated & reviewed & published,
            "qualityAcceptance": "requires_teacher_validation"}
