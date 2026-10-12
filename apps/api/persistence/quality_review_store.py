"""PostgreSQL persistence for version-pinned source review cases."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Engine,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    select,
)

from persistence.base import DatabaseStore
from persistence.database import decode_json
from persistence.schema import json_document

quality_review_metadata = MetaData()

quality_review_packages = Table(
    "quality_review_packages",
    quality_review_metadata,
    Column("package_id", String(64), primary_key=True),
    Column("title", String(240), nullable=False),
    Column("source_kind", String(40), nullable=False),
    Column("source_json", json_document, nullable=False),
    Column("created_by", String(128), nullable=False),
    Column("created_by_identity_verified", Boolean, nullable=False, default=False),
    Column("created_at", Float, nullable=False),
    CheckConstraint("source_kind IN ('real_open_material', 'user_authored', 'fixture')", name="ck_quality_review_package_source_kind"),
)

quality_review_cases = Table(
    "quality_review_cases",
    quality_review_metadata,
    Column("case_id", String(64), primary_key=True),
    Column("package_id", String(64), ForeignKey("quality_review_packages.package_id", ondelete="CASCADE"), nullable=False),
    Column("external_case_id", String(128), nullable=False),
    Column("ordinal", Integer, nullable=False),
    Column("subject", String(32), nullable=False),
    Column("source_page", Integer, nullable=False),
    Column("source_kind", String(40), nullable=False),
    Column("source_fingerprint", String(64), nullable=False),
    Column("input_fingerprint", String(64), nullable=False),
    Column("output_fingerprint", String(64), nullable=False),
    Column("source_json", json_document, nullable=False),
    Column("locator_json", json_document, nullable=False),
    Column("input_json", json_document, nullable=False),
    Column("expected_json", json_document, nullable=False),
    Column("actual_json", json_document, nullable=False),
    Column("record_version", Integer, nullable=False, default=1),
    Column("review_status", String(40), nullable=False, default="pending_source_verification"),
    Column("source_verified_at", Float, nullable=True),
    Column("source_verified_hash", String(64), nullable=True),
    Column("source_verified_quote", Text, nullable=True),
    Column("counted", Boolean, nullable=False, default=False),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("package_id", "external_case_id", name="uq_quality_review_external_case"),
    CheckConstraint("source_page > 0", name="ck_quality_review_source_page_positive"),
    CheckConstraint("record_version > 0", name="ck_quality_review_record_version_positive"),
    CheckConstraint("counted = false", name="ck_quality_review_public_cases_not_counted"),
    CheckConstraint("review_status IN ('pending_source_verification', 'pending_human_review', 'decision_recorded_pending_source', 'human_accepted', 'human_rejected', 'needs_changes')", name="ck_quality_review_case_status"),
)
Index("ix_quality_review_cases_package_ordinal", quality_review_cases.c.package_id, quality_review_cases.c.ordinal)
Index("ix_quality_review_cases_status", quality_review_cases.c.review_status, quality_review_cases.c.created_at)

quality_review_case_revisions = Table(
    "quality_review_case_revisions",
    quality_review_metadata,
    Column("case_id", String(64), ForeignKey("quality_review_cases.case_id", ondelete="CASCADE"), primary_key=True),
    Column("record_version", Integer, primary_key=True),
    Column("source_fingerprint", String(64), nullable=False),
    Column("input_fingerprint", String(64), nullable=False),
    Column("output_fingerprint", String(64), nullable=False),
    Column("source_json", json_document, nullable=False),
    Column("locator_json", json_document, nullable=False),
    Column("input_json", json_document, nullable=False),
    Column("expected_json", json_document, nullable=False),
    Column("actual_json", json_document, nullable=False),
    Column("created_by", String(128), nullable=False),
    Column("created_by_identity_verified", Boolean, nullable=False, default=False),
    Column("created_at", Float, nullable=False),
    CheckConstraint("record_version > 0", name="ck_quality_review_revision_version_positive"),
)
Index("ix_quality_review_case_revisions_case", quality_review_case_revisions.c.case_id, quality_review_case_revisions.c.record_version)

quality_review_decisions = Table(
    "quality_review_decisions",
    quality_review_metadata,
    Column("review_id", String(64), primary_key=True),
    Column("case_id", String(64), ForeignKey("quality_review_cases.case_id", ondelete="CASCADE"), nullable=False),
    Column("reviewed_record_version", Integer, nullable=False),
    Column("decision_stage", String(24), nullable=False),
    Column("source_fingerprint", String(64), nullable=False),
    Column("input_fingerprint", String(64), nullable=False),
    Column("output_fingerprint", String(64), nullable=False),
    Column("actor_id", String(128), nullable=False),
    Column("actor_kind", String(40), nullable=False),
    Column("identity_verified", Boolean, nullable=False),
    Column("display_name", String(128), nullable=False, default=""),
    Column("decision", String(24), nullable=False),
    Column("rationale", Text, nullable=False),
    Column("verified_quote", Text, nullable=False, default=""),
    Column("claimed_quote", Text, nullable=False, default=""),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("case_id", "actor_id", "reviewed_record_version", name="uq_quality_review_case_actor_version"),
    CheckConstraint("reviewed_record_version > 0", name="ck_quality_review_decision_version_positive"),
    CheckConstraint("decision IN ('accepted', 'rejected', 'needs_changes')", name="ck_quality_review_decision_value"),
    CheckConstraint("decision_stage IN ('provisional', 'final')", name="ck_quality_review_decision_stage"),
)
Index("ix_quality_review_decisions_case", quality_review_decisions.c.case_id, quality_review_decisions.c.created_at)


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class QualityReviewStore(DatabaseStore):
    """Persist immutable case snapshots and append-only reviewer decisions."""

    def __init__(self, database_url: str | None = None, data_root: str | None = None, *, engine: Engine | None = None) -> None:
        super().__init__(database_url=database_url, data_root=data_root)
        if engine is not None:
            self.engine.dispose()
            self.engine = engine
            self.database_url = str(engine.url)
            self._owns_engine = False
        else:
            self._owns_engine = True

    def close(self) -> None:
        if self._owns_engine:
            super().close()

    def create_package(self, *, title: str, source_kind: str, source: dict[str, Any], cases: list[dict[str, Any]], actor_id: str, actor_identity_verified: bool = False, now: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if now is None else now
        package_id = uuid.uuid4().hex
        prepared: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for ordinal, item in enumerate(cases, start=1):
            external_id = str(item["id"])
            if external_id in seen_ids:
                raise ValueError("案例 ID 不能重复")
            seen_ids.add(external_id)
            case_id = uuid.uuid4().hex
            source_part = item["source"]
            locator = item["sourceLocator"]
            inputs = item.get("input", {})
            expected = item["expected"]
            actual = item.get("actual", {})
            immutable_source = {"source": source_part, "sourcePage": item["sourcePage"], "sourceLocator": locator}
            prepared.append({
                "case_id": case_id,
                "external_case_id": external_id,
                "ordinal": ordinal,
                "subject": str(item.get("subject", "unknown")),
                "source_page": int(item["sourcePage"]),
                "source_kind": source_kind,
                "source_fingerprint": _canonical_hash(immutable_source),
                "input_fingerprint": _canonical_hash(inputs),
                "output_fingerprint": _canonical_hash(actual),
                "source_json": source_part,
                "locator_json": locator,
                "input_json": inputs,
                "expected_json": expected,
                "actual_json": actual,
                "record_version": 1,
                "review_status": (
                    "pending_human_review"
                    if source_part.get("assetVerification") == "server_hash_verified"
                    else "pending_source_verification"
                ),
                "counted": False,
                "created_at": timestamp,
            })
        with self.atomic() as connection:
            connection.execute(quality_review_packages.insert().values(
                package_id=package_id, title=title, source_kind=source_kind,
                source_json=source, created_by=actor_id,
                created_by_identity_verified=actor_identity_verified, created_at=timestamp,
            ))
            for values in prepared:
                values["package_id"] = package_id
                connection.execute(quality_review_cases.insert().values(**values))
                revision_values = {key: values[key] for key in (
                    "case_id", "record_version", "source_fingerprint", "input_fingerprint", "output_fingerprint",
                    "source_json", "locator_json", "input_json", "expected_json", "actual_json", "created_at",
                )}
                revision_values.update({
                    "created_by": actor_id,
                    "created_by_identity_verified": actor_identity_verified,
                })
                connection.execute(quality_review_case_revisions.insert().values(**revision_values))
        package = self.get_package(package_id)
        if package is None:
            raise RuntimeError("持久化的复核包未能重新读取")
        return package

    def list_packages(self) -> list[dict[str, Any]]:
        with self.read_connection() as connection:
            rows = connection.execute(
                select(quality_review_packages).order_by(quality_review_packages.c.created_at.desc())
            ).mappings().all()
            counts = connection.execute(select(
                quality_review_cases.c.package_id,
                quality_review_cases.c.review_status,
            )).all()
        by_package: dict[str, dict[str, int]] = {}
        for package_id, status in counts:
            record = by_package.setdefault(package_id, {"cases": 0, "pending": 0, "reviewed": 0})
            record["cases"] += 1
            if status in {"pending_source_verification", "pending_human_review", "decision_recorded_pending_source"}:
                record["pending"] += 1
            else:
                record["reviewed"] += 1
        return [{
            "packageId": row["package_id"], "title": row["title"], "sourceKind": row["source_kind"],
            "source": decode_json(row["source_json"]), "createdBy": row["created_by"],
            "createdByIdentityVerified": row["created_by_identity_verified"], "createdAt": row["created_at"],
            **by_package.get(row["package_id"], {"cases": 0, "pending": 0, "reviewed": 0}),
        } for row in rows]

    def list_cases(self, package_id: str) -> dict[str, Any]:
        package = self.get_package(package_id)
        if package is None:
            raise LookupError("复核包不存在")
        with self.read_connection() as connection:
            rows = connection.execute(select(quality_review_cases).where(
                quality_review_cases.c.package_id == package_id,
            ).order_by(quality_review_cases.c.ordinal)).mappings().all()
        return {"package": package, "items": [self._case_summary(row) for row in rows]}

    def list_inbox_cases(self) -> list[dict[str, Any]]:
        """Read case summaries and immutable source snapshots for server-side linking."""
        with self.read_connection() as connection:
            rows = connection.execute(
                select(
                    quality_review_cases,
                    quality_review_packages.c.title.label("package_title"),
                )
                .join(
                    quality_review_packages,
                    quality_review_packages.c.package_id == quality_review_cases.c.package_id,
                )
                .order_by(quality_review_cases.c.created_at, quality_review_cases.c.ordinal)
            ).mappings().all()
        return [
            {
                **self._case_summary(row),
                "source": decode_json(row["source_json"]),
                "packageTitle": row["package_title"],
            }
            for row in rows
        ]

    def get_package(self, package_id: str) -> dict[str, Any] | None:
        with self.read_connection() as connection:
            row = connection.execute(select(quality_review_packages).where(
                quality_review_packages.c.package_id == package_id,
            )).mappings().first()
        if row is None:
            return None
        return {"packageId": row["package_id"], "title": row["title"], "sourceKind": row["source_kind"],
                "source": decode_json(row["source_json"]), "createdBy": row["created_by"],
                "createdByIdentityVerified": row["created_by_identity_verified"], "createdAt": row["created_at"]}

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self.read_connection() as connection:
            row = connection.execute(select(quality_review_cases).where(
                quality_review_cases.c.case_id == case_id,
            )).mappings().first()
            reviews = connection.execute(select(quality_review_decisions).where(
                quality_review_decisions.c.case_id == case_id,
            ).order_by(quality_review_decisions.c.created_at)).mappings().all()
        if row is None:
            return None
        package = self.get_package(row["package_id"])
        if package is None:
            return None
        with self.read_connection() as connection:
            revisions = connection.execute(select(quality_review_case_revisions).where(
                quality_review_case_revisions.c.case_id == case_id,
            ).order_by(quality_review_case_revisions.c.record_version)).mappings().all()
        return {**self._case_summary(row),
                "source": decode_json(row["source_json"]), "sourceLocator": decode_json(row["locator_json"]),
                "input": decode_json(row["input_json"]), "expected": decode_json(row["expected_json"]),
                "actual": decode_json(row["actual_json"]),
                "createdBy": package["createdBy"],
                "createdByIdentityVerified": package["createdByIdentityVerified"],
                "revisions": [self._revision_summary(revision) for revision in revisions],
                "decisions": [self._decision_summary(review) for review in reviews]}

    def decide(self, *, case_id: str, expected_record_version: int, actor: dict[str, Any], decision: str, rationale: str, verified_quote: str, claimed_quote: str = "", source_verification: dict[str, Any] | None = None, now: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if now is None else now
        with self.atomic() as connection:
            row = connection.execute(select(quality_review_cases).where(
                quality_review_cases.c.case_id == case_id,
            ).with_for_update()).mappings().first()
            if row is None:
                raise LookupError("复核案例不存在")
            if row["record_version"] != expected_record_version:
                raise ValueError("案例版本已变化，请重新打开后再提交决定")
            source_verified = source_verification is not None or row["source_verified_at"] is not None
            stage = "final" if source_verified else "provisional"
            values = {
                "review_id": uuid.uuid4().hex, "case_id": case_id,
                "reviewed_record_version": expected_record_version,
                "decision_stage": stage,
                "source_fingerprint": row["source_fingerprint"],
                "input_fingerprint": row["input_fingerprint"],
                "output_fingerprint": row["output_fingerprint"],
                "actor_id": actor["actorId"], "actor_kind": actor["actorKind"],
                "identity_verified": actor["identityVerified"], "display_name": actor["displayName"],
                "decision": decision, "rationale": rationale, "verified_quote": verified_quote,
                "claimed_quote": claimed_quote,
                "created_at": timestamp,
            }
            connection.execute(quality_review_decisions.insert().values(**values))
            next_status = (
                {"accepted": "human_accepted", "rejected": "human_rejected", "needs_changes": "needs_changes"}[decision]
                if stage == "final" else "decision_recorded_pending_source"
            )
            # Multiple independent actors can review the same frozen version.
            # The unique actor/version constraint blocks duplicate decisions from
            # one actor; only an explicit revision advances record_version.
            case_updates: dict[str, Any] = {"review_status": next_status}
            if source_verification is not None:
                case_updates.update({
                    "source_verified_at": timestamp,
                    "source_verified_hash": source_verification["sourceFingerprint"],
                    "source_verified_quote": source_verification["quote"],
                })
            connection.execute(quality_review_cases.update().where(
                quality_review_cases.c.case_id == case_id,
                quality_review_cases.c.record_version == expected_record_version,
            ).values(**case_updates))
        return self.get_case(case_id)  # type: ignore[return-value]

    def revise_case(self, *, case_id: str, expected_record_version: int, input_value: dict[str, Any], expected: dict[str, Any], actual: dict[str, Any], actor: dict[str, Any], now: float | None = None) -> dict[str, Any]:
        """Append a new immutable output/input snapshot and reopen that case version."""
        timestamp = time.time() if now is None else now
        with self.atomic() as connection:
            row = connection.execute(select(quality_review_cases).where(
                quality_review_cases.c.case_id == case_id,
            ).with_for_update()).mappings().first()
            if row is None:
                raise LookupError("复核案例不存在")
            if row["record_version"] != expected_record_version:
                raise ValueError("案例版本已变化，请重新打开后再修订")
            next_version = expected_record_version + 1
            input_fingerprint = _canonical_hash(input_value)
            output_fingerprint = _canonical_hash(actual)
            expected_fingerprint = _canonical_hash(expected)
            if (
                input_fingerprint == row["input_fingerprint"]
                and output_fingerprint == row["output_fingerprint"]
                and expected_fingerprint == _canonical_hash(decode_json(row["expected_json"]))
            ):
                raise ValueError("修订内容与当前版本相同")
            review_status = "pending_human_review" if row["source_verified_at"] is not None else "pending_source_verification"
            revision = {
                "case_id": case_id,
                "record_version": next_version,
                "source_fingerprint": row["source_fingerprint"],
                "input_fingerprint": input_fingerprint,
                "output_fingerprint": output_fingerprint,
                "source_json": decode_json(row["source_json"]),
                "locator_json": decode_json(row["locator_json"]),
                "input_json": input_value,
                "expected_json": expected,
                "actual_json": actual,
                "created_by": actor["actorId"],
                "created_by_identity_verified": actor["identityVerified"],
                "created_at": timestamp,
            }
            connection.execute(quality_review_case_revisions.insert().values(**revision))
            connection.execute(quality_review_cases.update().where(
                quality_review_cases.c.case_id == case_id,
                quality_review_cases.c.record_version == expected_record_version,
            ).values(
                input_fingerprint=input_fingerprint,
                output_fingerprint=output_fingerprint,
                input_json=input_value,
                expected_json=expected,
                actual_json=actual,
                record_version=next_version,
                review_status=review_status,
            ))
        return self.get_case(case_id)  # type: ignore[return-value]

    @staticmethod
    def _case_summary(row: Any) -> dict[str, Any]:
        source = decode_json(row["source_json"])
        asset_verified = source.get("assetVerification") == "server_hash_verified"
        review_status = row["review_status"]
        if review_status == "pending_source_verification" and asset_verified:
            review_status = "pending_human_review"
        return {"caseId": row["case_id"], "externalCaseId": row["external_case_id"],
                "packageId": row["package_id"], "ordinal": row["ordinal"], "subject": row["subject"],
                "sourcePage": row["source_page"], "sourceKind": row["source_kind"],
                "sourceFingerprint": row["source_fingerprint"], "inputFingerprint": row["input_fingerprint"],
                "outputFingerprint": row["output_fingerprint"], "recordVersion": row["record_version"],
                "reviewStatus": review_status,
                "sourceVerification": (
                    "human_quote_verified" if row["source_verified_at"] is not None
                    else "managed_asset_verified" if asset_verified
                    else "pending_managed_asset"
                ),
                "assetVerification": "server_hash_verified" if asset_verified else "pending_managed_asset",
                "sourceVerifiedAt": row["source_verified_at"], "sourceVerifiedHash": row["source_verified_hash"],
                "counted": row["counted"]}

    @staticmethod
    def _revision_summary(row: Any) -> dict[str, Any]:
        return {"recordVersion": row["record_version"],
                "sourceFingerprint": row["source_fingerprint"], "inputFingerprint": row["input_fingerprint"],
                "outputFingerprint": row["output_fingerprint"], "input": decode_json(row["input_json"]),
                "expected": decode_json(row["expected_json"]), "actual": decode_json(row["actual_json"]),
                "createdBy": row["created_by"],
                "createdByIdentityVerified": row["created_by_identity_verified"],
                "createdAt": row["created_at"]}

    @staticmethod
    def _decision_summary(row: Any) -> dict[str, Any]:
        return {"reviewId": row["review_id"], "reviewedRecordVersion": row["reviewed_record_version"],
                "stage": row["decision_stage"],
                "sourceFingerprint": row["source_fingerprint"], "inputFingerprint": row["input_fingerprint"],
                "outputFingerprint": row["output_fingerprint"], "actorId": row["actor_id"],
                "actorKind": row["actor_kind"], "identityVerified": row["identity_verified"],
                "displayName": row["display_name"], "decision": row["decision"],
                "rationale": row["rationale"], "verifiedQuote": row["verified_quote"],
                "claimedQuote": row["claimed_quote"],
                "createdAt": row["created_at"]}


__all__ = ["QualityReviewStore", "quality_review_metadata", "quality_review_packages", "quality_review_cases", "quality_review_case_revisions", "quality_review_decisions"]
