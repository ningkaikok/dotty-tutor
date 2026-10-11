"""Use cases for source-bound, non-gold quality review cases."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from application.quality_review_sources import (
    QualityReviewSourceAssets,
    verify_locator_quote,
    verify_page_quote,
)


class QualityReviewService:
    """Validate imported review snapshots and persist human decisions."""

    def __init__(self, store: Any, *, auth_mode: str = "demo", source_root: Path | None = None, source_store: Any | None = None) -> None:
        self.store = store
        self.auth_mode = auth_mode
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
