"""Offline human-gold admission pinned to a snapshot and a separate identity register.

The register is supplied by the dataset owner after identity verification. A
reviewer's display name or a row's own identityVerified claim is not evidence.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

_APPROVAL_FIELDS = {"annotatorId", "reviewerId", "approvedAt", "counted", "humanReviews", "admission"}


def snapshot_hash(row: Mapping[str, Any]) -> str:
    """Hash the complete case content excluding only admission metadata."""
    snapshot = {key: value for key, value in row.items() if key not in _APPROVAL_FIELDS}
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def verified_reviews(row: Mapping[str, Any], reviews: Any, identities: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Require two distinct verified individuals to approve the exact same case."""
    if row.get("sourceKind") != "human":
        raise ValueError("only human source cases can enter gold")
    source = row.get("source")
    if (not isinstance(source, Mapping) or not re.fullmatch(r"[a-f0-9]{64}", str(source.get("contentSha256", "")))
            or not str(source.get("reference", "")).strip()):
        raise ValueError("human source requires a pinned hash and reference")
    if not isinstance(reviews, list) or len(reviews) != 2:
        raise ValueError("two independent human reviews are required")
    digest = snapshot_hash(row)
    people: set[str] = set()
    actors: set[str] = set()
    result = []
    for review in reviews:
        if not isinstance(review, Mapping):
            raise ValueError("review must be an object")
        actor = str(review.get("actorId", ""))
        identity = identities.get(actor)
        if (not isinstance(identity, Mapping) or identity.get("identityVerified") is not True
                or identity.get("actorKind") != "individual" or not identity.get("personKey")
                or not str(identity.get("evidenceRef", "")).strip()):
            raise ValueError("human reviewer identity is not independently verified")
        person = str(identity["personKey"])
        if person in people or actor in actors:
            raise ValueError("two independent people are required")
        people.add(person)
        actors.add(actor)
        if review.get("snapshotSha256") != digest:
            raise ValueError("review snapshot does not match current case")
        if review.get("decision") != "approve" or not str(review.get("rationale", "")).strip():
            raise ValueError("both humans must approve with a rationale")
        try:
            timestamp = datetime.fromisoformat(str(review.get("reviewedAt", "")).replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("review timestamp is invalid") from error
        if timestamp.tzinfo is None:
            raise ValueError("review timestamp must include timezone")
        result.append(dict(review))
    return result


def admit_case(row: dict[str, Any], reviews: list[dict[str, Any]], identities: Mapping[str, Any]) -> dict[str, Any]:
    checked = verified_reviews(row, reviews, identities)
    return {**copy.deepcopy(row), "annotatorId": checked[0]["actorId"], "reviewerId": checked[1]["actorId"],
            "approvedAt": max(review["reviewedAt"] for review in checked), "counted": True,
            "humanReviews": checked, "admission": {"snapshotSha256": snapshot_hash(row), "version": "human-gold-v1"}}


def main() -> int:
    """Prepare a new output; never mutate candidate/source files or invent approvals."""
    from evaluation.benchmark.contract import load_jsonl, validate_records
    parser = argparse.ArgumentParser(description="导入已核验身份的双审记录，生成待校验人工金标准")
    parser.add_argument("candidates", type=Path)
    parser.add_argument("reviews", type=Path, help="JSON object mapping caseId to two review records")
    parser.add_argument("identities", type=Path, help="由数据集维护者独立核验的身份登记")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("输出文件已存在，不覆盖历史审计")
    try:
        identities = json.loads(args.identities.read_text())
        reviews = json.loads(args.reviews.read_text())
        rows = [admit_case(row, reviews.get(row["caseId"], []), identities) for row in load_jsonl(args.candidates)]
        validation = validate_records(rows, trusted_identities=identities)
        # Store partial admitted sets for further intake; formal ranking remains gated.
        args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        print(json.dumps(validation.as_dict(), ensure_ascii=False))
        return 0 if validation.ok else 1
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "problems": [str(error)]}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
