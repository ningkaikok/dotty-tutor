"""Use cases for source-bound, non-gold quality review cases."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from application.quality_review_sources import (
    MANAGED_SOURCE_ASSETS,
    QualityReviewSourceAssets,
    verify_locator_quote,
    verify_page_quote,
)
from application.services.chapter_source_preview import source_file

_PENDING_REVIEW_STATUSES = {
    "pending_source_verification",
    "pending_human_review",
    "decision_recorded_pending_source",
    "needs_changes",
}
_FINAL_REVIEW_STATUSES = {"human_accepted", "human_rejected"}
_MEDIA_REVIEW_STATUSES = {
    "draft",
    "consistency_review_required",
    "consistency_failed",
    "changes_requested",
}
_MEDIA_VISIBLE_STATUSES = _MEDIA_REVIEW_STATUSES | {"rendering"}
_STATIC_SOURCE_TITLES = {
    "openstax-elementary-algebra-2e": "OpenStax Elementary Algebra 2e",
    "openstax-writing-guide": "OpenStax Writing Guide",
}


class QualityReviewService:
    """Validate imported review snapshots and persist human decisions."""

    def __init__(
        self,
        store: Any,
        *,
        auth_mode: str = "demo",
        source_root: Path | None = None,
        source_store: Any | None = None,
        chapter_store: Any | None = None,
        media_store: Any | None = None,
        courseware_store: Any | None = None,
    ) -> None:
        self.store = store
        self.auth_mode = auth_mode
        self.source_store = source_store
        self.chapter_store = chapter_store
        self.media_store = media_store
        self.courseware_store = courseware_store
        self.source_assets = QualityReviewSourceAssets(
            source_root=source_root or (Path(store.root) / "quality-review" / "sources"),
            data_root=Path(store.root),
            source_store=source_store,
        )

    def import_package(self, payload: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
        cases = payload["cases"]
        prepared_cases = []
        verified_sources = 0
        for case in cases:
            source = case["source"]
            parsed_url = urlparse(source["url"])
            host = (parsed_url.hostname or "").lower()
            if parsed_url.scheme != "https" or parsed_url.username or parsed_url.password or host not in {"openstax.org", "www.openstax.org", "assets.openstax.org"}:
                raise ValueError("当前导入仅接受 OpenStax 开放教材来源")
            if any(key in source for key in ("file", "path", "sourceFilePath")):
                raise ValueError("不接受客户端文件路径；来源文件必须先作为受管素材导入")
            source = {key: value for key, value in source.items() if key not in {"sourceKind", "counted", "assetVerification"}}
            if source.get("sourceAssetId") or source.get("uploadId"):
                source_page = self.source_assets.resolve(source, int(case["sourcePage"]))
                verify_locator_quote(source_page, case["sourceLocator"])
                source["contentSha256"] = source_page["fingerprint"]
                source["assetVerification"] = "server_hash_verified"
                verified_sources += 1
            else:
                # Preserve a caller's checksum only as an untrusted claim. It must never
                # be presented as the pinned source hash or unlock final decisions.
                source["declaredContentSha256"] = source.pop("contentSha256", "")
                source["assetVerification"] = "pending_managed_asset"
            prepared_cases.append({**case, "source": source})
        # The client can never select sourceKind or counted. The provenance class is
        # derived from the allowed source host; open-material decisions can never
        # become benchmark gold, regardless of the reviewer or decision.
        source_kind = "real_open_material"
        source_meta = {
            **{key: value for key, value in payload.get("source", {}).items() if key not in {"sourceKind", "counted", "assetVerification"}},
            "assetVerification": (
                "server_hash_verified" if verified_sources == len(cases)
                else "partially_verified" if verified_sources
                else "pending_managed_asset"
            ),
            "counted": False,
            "humanReviewStatus": "pending",
        }
        return self.store.create_package(
            title=payload["title"], source_kind=source_kind, source=source_meta,
            cases=prepared_cases, actor_id=actor["actorId"],
            actor_identity_verified=bool(actor["identityVerified"]),
        )

    def list_packages(self) -> dict[str, Any]:
        return {"items": self.store.list_packages()}

    def list_cases(self, package_id: str) -> dict[str, Any]:
        return self.store.list_cases(package_id)

    def list_inbox(
        self,
        *,
        chapter_id: str | None = None,
        upload_id: str | None = None,
        include_reviewed: bool = False,
    ) -> dict[str, Any]:
        """List source cases and published-course media without merging their review states.

        Case counts are scoped by chapter/upload filters but intentionally ignore
        ``include_reviewed``. Media tasks are taken from the latest version in each
        series, then restricted to the chapter's current published snapshot. A
        chapter's newer draft source therefore cannot rebind published media.
        Rendering work is visible as progress but is excluded from mediaPendingCount.
        """
        current_sources = self._current_chapter_sources()
        source_cases: list[dict[str, Any]] = []
        for raw_case in self.store.list_inbox_cases():
            source = raw_case.get("source")
            source = source if isinstance(source, dict) else {}
            fingerprint = self._server_source_fingerprint(source)
            matches = current_sources.get((fingerprint, int(raw_case["sourcePage"])), []) if fingerprint else []
            chapter_ids = sorted({match["chapterId"] for match in matches})
            source_upload_id = source.get("uploadId")
            managed_filename = (
                self._managed_upload_filename(source_upload_id)
                if isinstance(source_upload_id, str) and source_upload_id.strip()
                else None
            )
            case_upload_id = (
                source_upload_id
                if fingerprint and isinstance(source_upload_id, str) and managed_filename is not None
                else None
            )
            if not case_upload_id:
                upload_ids = {match["uploadId"] for match in matches if match.get("uploadId")}
                if len(upload_ids) == 1:
                    case_upload_id = next(iter(upload_ids))
            item = {
                key: value for key, value in raw_case.items()
                if key not in {"source", "packageTitle"}
            }
            item.update({
                "sourceKey": f"sha256:{fingerprint}" if fingerprint else f"package:{raw_case['packageId']}",
                "sourceTitle": self._source_title(source, managed_filename),
                "uploadId": case_upload_id,
                "chapterIds": chapter_ids,
            })
            if chapter_id and chapter_id not in chapter_ids:
                continue
            if upload_id and case_upload_id != upload_id:
                continue
            source_cases.append(item)

        pending_count = sum(item["reviewStatus"] in _PENDING_REVIEW_STATUSES for item in source_cases)
        reviewed_count = sum(item["reviewStatus"] in _FINAL_REVIEW_STATUSES for item in source_cases)
        unlinked_count = sum(not item["chapterIds"] for item in source_cases)
        visible_cases = [
            item for item in source_cases
            if include_reviewed or item["reviewStatus"] not in _FINAL_REVIEW_STATUSES
        ]
        media_tasks = self._current_publication_media(chapter_id=chapter_id, upload_id=upload_id)
        from application.services.quality_review_courseware import (
            courseware_review_tasks,
        )

        courseware_tasks = courseware_review_tasks(self.chapter_store, self.courseware_store, chapter_id=chapter_id, upload_id=upload_id)
        return {
            "items": visible_cases,
            "total": len(visible_cases),
            "pendingCount": pending_count,
            "reviewedCount": reviewed_count,
            "unlinkedCount": unlinked_count,
            "mediaTasks": media_tasks,
            "mediaPendingCount": sum(task["status"] in _MEDIA_REVIEW_STATUSES for task in media_tasks),
            "coursewareTasks": courseware_tasks,
            "coursewarePendingCount": len(courseware_tasks),
        }

    def _current_chapter_sources(self) -> dict[tuple[str, int], list[dict[str, Any]]]:
        """Index latest chapter source revisions by their server-frozen SHA and page."""
        list_chapters = getattr(self.chapter_store, "list_chapters", None)
        if not callable(list_chapters):
            return {}
        matches: dict[tuple[str, int], list[dict[str, Any]]] = {}
        chapters = list_chapters()
        if not isinstance(chapters, list):
            return matches
        for chapter in chapters:
            if not isinstance(chapter, dict) or chapter.get("deletedAt"):
                continue
            revisions = chapter.get("sourceRevisions")
            if not isinstance(revisions, list) or not revisions:
                continue
            revision = revisions[-1]
            if not isinstance(revision, dict):
                continue
            fingerprint = self._valid_sha256(revision.get("sourceFileSha256"))
            start = revision.get("pageStart")
            end = revision.get("pageEnd")
            if not fingerprint or not isinstance(start, int) or not isinstance(end, int):
                continue
            page_start = start
            page_end = end
            if page_start < 1 or page_end < page_start:
                continue
            page_list = revision.get("pages")
            if not isinstance(page_list, list):
                continue
            declared_pages: set[int] = set()
            for page in page_list:
                if isinstance(page, dict) and isinstance(page.get("page"), int):
                    declared_pages.add(page["page"])
            chapter_id = chapter.get("chapterId")
            if not isinstance(chapter_id, str) or not chapter_id:
                continue
            upload_id = revision.get("uploadId") if isinstance(revision.get("uploadId"), str) else None
            for page_number in declared_pages:
                if page_start <= page_number <= page_end:
                    matches.setdefault((fingerprint, page_number), []).append({
                        "chapterId": chapter_id,
                        "uploadId": upload_id,
                    })
        return matches

    def _current_publication_media(self, *, chapter_id: str | None, upload_id: str | None) -> list[dict[str, Any]]:
        """Read latest series versions under each active, immutable publication snapshot."""
        if self.chapter_store is None or self.media_store is None:
            return []
        list_chapters = getattr(self.chapter_store, "list_chapters", None)
        load_publication = getattr(self.chapter_store, "load_publication", None)
        list_for_lesson = getattr(self.media_store, "list_for_lesson", None)
        get_series = getattr(self.media_store, "get_series", None)
        if not callable(list_chapters) or not callable(load_publication):
            return []
        if not callable(list_for_lesson) or not callable(get_series):
            return []

        tasks: dict[str, dict[str, Any]] = {}
        chapters = list_chapters()
        if not isinstance(chapters, list):
            return []
        for chapter in chapters:
            if not isinstance(chapter, dict) or chapter.get("deletedAt"):
                continue
            item_chapter_id = chapter.get("chapterId")
            current_publication_id = chapter.get("publicationId")
            if not isinstance(item_chapter_id, str) or not isinstance(current_publication_id, str):
                continue
            if chapter_id and item_chapter_id != chapter_id:
                continue
            publication_history = chapter.get("publications")
            if not isinstance(publication_history, list):
                continue
            publication_ref = next((
                item for item in publication_history
                if isinstance(item, dict) and item.get("publicationId") == current_publication_id
            ), None)
            if not publication_ref:
                continue
            source_revision_id = publication_ref.get("sourceRevisionId")
            if not isinstance(source_revision_id, str):
                continue
            source_revisions = chapter.get("sourceRevisions")
            if not isinstance(source_revisions, list):
                continue
            source_revision = next((
                item for item in source_revisions
                if isinstance(item, dict) and item.get("sourceRevisionId") == source_revision_id
            ), None)
            if source_revision is None:
                continue
            snapshot_upload_id = source_revision.get("uploadId")
            if not isinstance(snapshot_upload_id, str):
                snapshot_upload_id = None
            source_fingerprint = self._valid_sha256(source_revision.get("sourceFileSha256"))
            source_key = f"sha256:{source_fingerprint}" if source_fingerprint else f"publication:{current_publication_id}"
            if upload_id and snapshot_upload_id != upload_id:
                continue

            publication = load_publication(current_publication_id)
            if not isinstance(publication, dict) or publication.get("status") != "published":
                continue
            lesson_ids = publication.get("lessonIds")
            if not isinstance(lesson_ids, list):
                continue
            for lesson_id in lesson_ids:
                if not isinstance(lesson_id, str) or not lesson_id:
                    continue
                raw_revisions = list_for_lesson(item_chapter_id, current_publication_id, lesson_id)
                if not isinstance(raw_revisions, list):
                    continue
                revisions = [row for row in raw_revisions if isinstance(row, dict)]
                series_ids = {
                    row["seriesId"] for row in revisions
                    if isinstance(row.get("seriesId"), str)
                }
                for series_id in series_ids:
                    series = get_series(series_id)
                    if not isinstance(series, list) or not series:
                        continue
                    versions = [row for row in series if isinstance(row, dict)]
                    if not versions:
                        continue
                    latest = max(versions, key=lambda row: int(row.get("version", 0)))
                    if (
                        latest.get("chapterId") != item_chapter_id
                        or latest.get("publicationId") != current_publication_id
                        or latest.get("lessonId") != lesson_id
                        or latest.get("sourceRevisionId") != source_revision_id
                        or latest.get("status") not in _MEDIA_VISIBLE_STATUSES
                    ):
                        continue
                    media_id = latest.get("mediaId")
                    if not isinstance(media_id, str) or not media_id:
                        continue
                    tasks[media_id] = {
                        "mediaId": media_id,
                        "chapterId": item_chapter_id,
                        "publicationId": current_publication_id,
                        "lessonId": lesson_id,
                        "version": int(latest["version"]),
                        "title": str(latest.get("title") or ""),
                        "sourceKey": source_key,
                        "status": latest["status"],
                        "uploadId": snapshot_upload_id,
                        "sourceRevisionId": source_revision_id,
                    }
        return sorted(tasks.values(), key=lambda task: (task["chapterId"], task["lessonId"], task["mediaId"]))

    def _source_title(self, source: dict[str, Any], managed_filename: str | None) -> str:
        """Mirror source resolution priority while avoiding a PDF hash on inbox reads."""
        upload_id = source.get("uploadId")
        if isinstance(upload_id, str) and upload_id.strip() and self.source_store is not None:
            return managed_filename or "未识别教材来源"
        asset_id = source.get("sourceAssetId")
        if asset_id in MANAGED_SOURCE_ASSETS:
            return _STATIC_SOURCE_TITLES.get(str(asset_id), "未识别教材来源")
        return "未识别教材来源"

    def _managed_upload_filename(self, upload_id: str) -> str | None:
        """Resolve a persisted upload ID without rescanning its PDF fingerprint."""
        if self.source_store is None:
            return None
        job = self.source_store.load_job(upload_id)
        if not isinstance(job, dict) or job.get("status") not in {"complete", "completed", "ready"}:
            return None
        if source_file(self.source_store, job) is None:
            return None
        filename = job.get("filename")
        return str(filename) if isinstance(filename, str) and filename.strip() else "source.pdf"

    @staticmethod
    def _server_source_fingerprint(source: dict[str, Any]) -> str | None:
        if source.get("assetVerification") != "server_hash_verified":
            return None
        return QualityReviewService._valid_sha256(source.get("contentSha256"))

    @staticmethod
    def _valid_sha256(value: Any) -> str | None:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            return None
        return value.lower()

    def get_case(self, case_id: str) -> dict[str, Any]:
        case = self.store.get_case(case_id)
        if case is None:
            raise LookupError("复核案例不存在")
        return case

    def get_source_page_preview(self, case_id: str) -> Path:
        """Return a rendered original page from a configured or uploaded managed PDF."""
        case = self.get_case(case_id)
        source_page = self.source_assets.resolve(case["source"], int(case["sourcePage"]))
        verify_locator_quote(source_page, case["sourceLocator"])
        return self.source_assets.render_preview(source_page)

    def decide(self, *, case_id: str, expected_record_version: int, actor: dict[str, Any], decision: str, rationale: str, verified_quote: str) -> dict[str, Any]:
        source_verification = None
        claimed_quote = verified_quote.strip()
        persisted_quote = ""
        case = self.get_case(case_id)
        has_managed_asset = bool(case["source"].get("sourceAssetId") or case["source"].get("uploadId"))
        if claimed_quote and has_managed_asset:
            source_page = self.source_assets.resolve(case["source"], int(case["sourcePage"]))
            verify_locator_quote(source_page, case["sourceLocator"])
            checked_quote = verify_page_quote(source_page, verified_quote)
            source_verification = {"sourceFingerprint": source_page["fingerprint"], "quote": checked_quote}
            persisted_quote = checked_quote
        return self.store.decide(
            case_id=case_id,
            expected_record_version=expected_record_version,
            actor=actor,
            decision=decision,
            rationale=rationale,
            verified_quote=persisted_quote,
            claimed_quote=claimed_quote,
            source_verification=source_verification,
        )

    def revise_case(
        self,
        *,
        case_id: str,
        expected_record_version: int,
        actor: dict[str, Any],
        input_value: dict[str, Any],
        expected: dict[str, Any],
        actual: dict[str, Any],
    ) -> dict[str, Any]:
        """Append an explicitly revised snapshot; prior decisions remain bound to their version."""
        return self.store.revise_case(
            case_id=case_id,
            expected_record_version=expected_record_version,
            input_value=input_value,
            expected=expected,
            actual=actual,
            actor=actor,
        )


def quality_review_actor(request: Any, *, display_name: str = "") -> dict[str, Any]:
    """Derive a stable reviewer identity from the server auth context.

    The current protected product has one shared teacher credential, so a browser
    session is not evidence of a distinct human reviewer. Every session therefore
    maps to the same actor and remains ineligible as an independent second review.
    """
    actor = getattr(request.state, "actor", None)
    if actor and actor.get("role") == "teacher":
        return {
            "actorId": "protected:teacher:shared-account",
            "actorKind": "shared_teacher_account",
            "identityVerified": False,
            "displayName": display_name.strip(),
        }
    if getattr(request.app.state, "auth_mode", "demo") == "protected":
        raise PermissionError("请先使用教师会话登录")
    return {
        "actorId": "demo:unverified",
        "actorKind": "demo_unverified",
        "identityVerified": False,
        "displayName": display_name.strip(),
    }


__all__ = ["QualityReviewService", "quality_review_actor"]
