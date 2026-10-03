"""Build provenance-bound review packets from externally supplied real sources.

The source pack contains short case inputs and locators, never textbook files.
PDF text extraction is the existing pypdf text layer; it is not OCR evidence.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from domain.chapters.english import evaluate_english_chapter_attempt
from domain.questions.source import split_question_sources

REQUIRED_ENGLISH_TAGS = {"word_meaning", "reference", "explicit", "inference", "paraphrase", "wrong_evidence"}
ENGLISH_KINDS = {"word_meaning", "reference", "explicit", "inference", "short_answer"}
ANSWER_MODES = {"objective", "short_answer"}


def _canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _manifest_hash(manifest: dict[str, Any]) -> str:
    return _canonical_hash({key: value for key, value in manifest.items() if key != "manifestSha256"})


def _case_fingerprint(case: dict[str, Any]) -> str:
    mutable = {"caseFingerprint", "reviewStatus", "reviewer", "review", "counted"}
    return _canonical_hash({key: value for key, value in case.items() if key not in mutable})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _page(source: dict[str, Any], page: int) -> tuple[str, int]:
    path = Path(source["file"]).expanduser().resolve()
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"source file must be a PDF: {path}")
    reader = PdfReader(str(path))
    if page < 1 or page > len(reader.pages):
        raise ValueError(f"source page {page} is outside PDF page range 1..{len(reader.pages)}")
    pdf_page = reader.pages[page - 1]
    try:
        image_count = len(pdf_page.images)
    except (AttributeError, TypeError, ValueError):
        image_count = 0
    return pdf_page.extract_text() or "", image_count


def _observe(case: dict[str, Any], page_text: str, image_count: int, revision: str) -> dict[str, Any]:
    subject, expected, supplied = case["subject"], case["expected"], case["input"]
    if subject == "math":
        observed_text = supplied.get("ocrText", page_text)
        blocks = split_question_sources(observed_text)
        expected_numbers = expected.get("questionNumbers", [])
        present_fragments = [fragment for fragment in expected.get("requiredSourceFragments", []) if fragment in observed_text]
        return {
            "method": "pypdf_text_layer_plus_split_question_sources",
            "textSource": "provided_input" if "ocrText" in supplied else "pypdf_text_layer",
            "questionNumbers": [number for number, _block, _images in blocks],
            "requiredSourceFragmentsPresent": present_fragments,
            "requiredSourceFragments": expected.get("requiredSourceFragments", []),
            "imageChecks": expected.get("imagesByQuestion", {}),
            "pageImageCount": image_count,
            "checks": {
                "questionNumbersMatch": [number for number, _block, _images in blocks] == expected_numbers,
                "requiredFragmentsPresent": set(present_fragments) == set(expected.get("requiredSourceFragments", [])),
            },
            "limitations": "No OCR model was run; pypdf text extraction cannot validate scanned-image recognition or visual figure correspondence.",
        }
    supplied_passage = supplied["passage"]
    expected_text = expected["evidenceQuote"]
    answers = {" ".join(re.findall(r"[a-z0-9']+", value.casefold())) for value in expected["acceptedAnswers"]}
    answer = " ".join(re.findall(r"[a-z0-9']+", supplied["providedAnswer"].casefold()))
    observation: dict[str, Any] = {
        "method": "domain.chapters.english.evaluate_english_chapter_attempt",
    }
    # Keep the legacy declared checks visible as fixture diagnostics, but never
    # present them as semantic validation of the answer key or rubric.
    observation["declaredChecks"] = {
        "passageMatchesSourceLocator": supplied_passage in page_text,
        "answerMatchesDeclaredVariants": answer in answers,
        "providedEvidenceMatchesExpected": " ".join(re.findall(r"[a-z0-9']+", supplied["providedEvidence"].casefold()))
        == " ".join(re.findall(r"[a-z0-9']+", expected_text.casefold())),
    }
    quote = case["sourceLocator"]["quote"]
    required_quote = expected["evidenceQuote"]
    sentence_id = case["sourceLocator"].get("sentenceId", f"p{case['sourcePage']}-quoted-sentence")
    answer_quote = supplied.get("providedEvidenceQuote", "")
    answer_sentence_id = supplied.get("providedEvidenceSentenceId", sentence_id)
    region_id = case["sourceLocator"].get("regionId")
    source_ref = {"sourceRevisionId": revision, "page": case["sourcePage"], "sentenceId": sentence_id, "quote": quote}
    if region_id:
        source_ref["regionId"] = region_id
    required_ref = {**source_ref, "quote": required_quote}
    question = {
        "questionKind": expected.get("questionKind", _english_kind(case)),
        "answerMode": expected.get("answerMode", "short_answer"),
        "acceptedAnswers": expected.get("acceptedAnswers", expected.get("acceptedVariants", [])),
        "requiredEvidenceRefs": [required_ref],
        "sourceRevisionId": revision,
        "rubric": expected.get("rubric", {}),
    }
    evidence_refs = []
    if isinstance(answer_quote, str) and answer_quote.strip():
        answer_ref = {**source_ref, "sentenceId": answer_sentence_id, "quote": answer_quote}
        evidence_refs = [answer_ref]
    result = evaluate_english_chapter_attempt(
        question=question,
        answer={"text": supplied.get("providedAnswer", "")},
        evidence_refs=evidence_refs,
        source_pages=[{
            "sourceRevisionId": revision,
            "page": case["sourcePage"],
            "text": page_text,
            "sentences": [
                {"sentenceId": sentence_id, "regionId": region_id, "text": quote},
                *([{"sentenceId": answer_sentence_id, "regionId": region_id, "text": answer_quote}]
                  if answer_sentence_id != sentence_id else []),
            ],
            "regions": ([{"regionId": region_id}] if region_id else []),
        }],
    )
    observation["productionEvaluation"] = result
    observation["expectedCalibrationStatus"] = "pending_human_calibration"
    observation["limitations"] = "Production evaluator replay only; expected answers, evidence, and inference rubric still require human calibration."
    return observation


def _english_kind(case: dict[str, Any]) -> str:
    tags = set(case.get("tags", []))
    if "paraphrase" in tags:
        return "short_answer"
    for kind in ("word_meaning", "reference", "explicit", "inference"):
        if kind in tags:
            return kind
    return "unknown"


def prepare(source_pack: dict[str, Any]) -> dict[str, Any]:
    """Validate source locators, run available deterministic checks and emit pending cases."""
    if not isinstance(source_pack, dict) or source_pack.get("version") != 1 or not isinstance(source_pack.get("cases"), list):
        raise ValueError("source pack requires version 1 and a cases array")
    cases = copy.deepcopy(source_pack["cases"])
    if not cases:
        raise ValueError("source pack cases must not be empty")
    ids = [case.get("id") for case in cases if isinstance(case, dict)]
    if len(ids) != len(cases) or len(ids) != len(set(ids)) or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("every case needs a unique non-empty id")
    math_count = sum(case.get("subject") == "math" for case in cases)
    if not 10 <= math_count <= 20:
        raise ValueError("real-source packet requires 10 to 20 math cases")
    english_tags = {tag for case in cases if case.get("subject") == "english" for tag in case.get("tags", [])}
    if not REQUIRED_ENGLISH_TAGS.issubset(english_tags):
        raise ValueError(f"English coverage is missing: {', '.join(sorted(REQUIRED_ENGLISH_TAGS - english_tags))}")

    records: list[dict[str, Any]] = []
    for case in cases:
        subject, source = case.get("subject"), case.get("source")
        if subject not in {"math", "english"} or not isinstance(source, dict):
            raise ValueError(f"{case['id']}: subject and source metadata are required")
        for field in ("url", "license", "version", "file"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                raise ValueError(f"{case['id']}: source.{field} is required")
        page = case.get("sourcePage")
        locator = case.get("sourceLocator")
        if not isinstance(page, int) or isinstance(page, bool) or page < 1 or not isinstance(locator, dict):
            raise ValueError(f"{case['id']}: positive sourcePage and sourceLocator are required")
        quote = locator.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError(f"{case['id']}: sourceLocator.quote is required")
        tags = case.get("tags")
        if not isinstance(tags, list) or not tags or any(not isinstance(tag, str) for tag in tags):
            raise ValueError(f"{case['id']}: tags must be a non-empty array of strings")
        expected, supplied = case.get("expected"), case.get("input")
        if not isinstance(expected, dict) or not isinstance(supplied, dict):
            raise ValueError(f"{case['id']}: expected and input must be objects")
        if subject == "math":
            numbers = expected.get("questionNumbers")
            fragments = expected.get("requiredSourceFragments")
            if (
                not isinstance(numbers, list) or any(not isinstance(item, str) or not item for item in numbers)
                or not isinstance(fragments, list) or not fragments or any(not isinstance(item, str) or not item for item in fragments)
                or ("ocrText" in supplied and not isinstance(supplied["ocrText"], str))
            ):
                raise ValueError(f"{case['id']}: math expected requires questionNumbers and requiredSourceFragments; input.ocrText must be text when supplied")
        else:
            if expected.get("questionKind") not in ENGLISH_KINDS or expected.get("answerMode") not in ANSWER_MODES:
                raise ValueError(f"{case['id']}: English expected.questionKind and answerMode must use supported values")
            accepted = expected.get("acceptedAnswers")
            if not isinstance(accepted, list) or not accepted or len(accepted) > 20 or any(not isinstance(answer, str) or not answer.strip() for answer in accepted):
                raise ValueError(f"{case['id']}: English expected.acceptedAnswers must contain 1 to 20 non-empty strings")
            if not isinstance(expected.get("evidenceQuote"), str) or not expected["evidenceQuote"].strip():
                raise ValueError(f"{case['id']}: English expected.evidenceQuote is required")
            if not isinstance(supplied.get("passage"), str) or not isinstance(supplied.get("providedAnswer"), str):
                raise ValueError(f"{case['id']}: English input.passage and providedAnswer are required strings")
            if not isinstance(supplied.get("providedEvidenceQuote", ""), str):
                raise ValueError(f"{case['id']}: English input.providedEvidenceQuote must be a string")
            if expected["questionKind"] == "inference" and (
                not isinstance(expected.get("rubric"), dict) or expected["rubric"].get("supportStatus") != "supported"
            ):
                raise ValueError(f"{case['id']}: inference requires a human-calibrated rubric with supportStatus=supported")
        page_text, image_count = _page(source, page)
        if quote not in page_text:
            raise ValueError(f"{case['id']}: source locator quote was not found on PDF page {page}")
        content_hash = _sha256(Path(source["file"]))
        if subject == "english" and len(page_text) > 20_000:
            raise ValueError(f"{case['id']}: cited PDF page exceeds the English evaluator's 20,000-character limit")
        actual = _observe(case, page_text, image_count, content_hash)
        actual["textSource"] = "pypdf_text_layer" if subject == "math" and "ocrText" not in case.get("input", {}) else actual.get("textSource")
        records.append({
            **case,
            "sourceKind": "real_open_material",
            "source": {
                **source,
                "contentSha256": content_hash,
                "attribution": source.get("attribution", "Access for free at openstax.org"),
            },
            "actual": actual,
            "reviewStatus": "pending_human_review",
            "reviewer": None,
            "review": None,
            "counted": False,
        })
        records[-1]["caseFingerprint"] = _case_fingerprint(records[-1])
    result = {
        "schemaVersion": 1,
        "title": source_pack.get("title", "Real-source chapter review"),
        "sourceKind": "real_open_material",
        "counted": False,
        "cases": records,
        "report": {
            "cases": len(records), "mathCases": math_count,
            "englishCases": sum(case["subject"] == "english" for case in records),
            "pendingHumanReview": len(records), "realCaseReviewed": 0, "goldCount": 0, "counted": False,
            "claim": "Preparation checks only; no human review or OCR model execution is claimed.",
        },
    }
    result["manifestSha256"] = _canonical_hash(result)
    return result


def import_reviews(manifest: dict[str, Any], decisions_doc: dict[str, Any]) -> dict[str, Any]:
    """Import explicit human decisions, rejecting stale, duplicate, or unsupported locators."""
    if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 1:
        raise ValueError("review manifest schemaVersion must be 1")
    if manifest.get("manifestSha256") != _manifest_hash(manifest):
        raise ValueError("review manifest content hash mismatch")
    decisions = decisions_doc.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("decisions must be an array")
    manifest_cases = manifest.get("cases")
    if not isinstance(manifest_cases, list) or any(not isinstance(case, dict) or not isinstance(case.get("id"), str) for case in manifest_cases):
        raise ValueError("review manifest cases must be objects with IDs")
    for case in manifest_cases:
        if case.get("sourceKind") != "real_open_material" or case.get("caseFingerprint") != _case_fingerprint(case):
            raise ValueError(f"{case['id']}: case fingerprint mismatch or non-real source")
    by_id = {case["id"]: case for case in manifest_cases}
    if len(by_id) != len(manifest_cases):
        raise ValueError("manifest contains duplicate case IDs")
    decision_ids = [item.get("caseId") for item in decisions if isinstance(item, dict)]
    if len(decision_ids) != len(decisions) or len(set(decision_ids)) != len(decision_ids):
        raise ValueError("each review caseId must occur exactly once")
    if set(decision_ids) - set(by_id):
        raise ValueError("review contains unknown case IDs")
    for decision in decisions:
        case = by_id[decision["caseId"]]
        if case.get("sourceKind") != "real_open_material" or case.get("reviewStatus") != "pending_human_review":
            raise ValueError(f"{case['id']}: case is not pending real-source review")
        if case.get("caseFingerprint") != _case_fingerprint(case):
            raise ValueError(f"{case['id']}: case fingerprint mismatch")
        if not isinstance(case.get("sourceLocator"), dict) or not isinstance(case.get("sourceLocator", {}).get("quote"), str):
            raise ValueError(f"{case['id']}: source locator is missing")
        if _sha256(Path(case["source"]["file"])) != case["source"].get("contentSha256"):
            raise ValueError(f"{case['id']}: source file changed since packet preparation")
        if decision.get("sourceSha256") != case["source"]["contentSha256"]:
            raise ValueError(f"{case['id']}: source SHA-256 mismatch")
        if decision.get("manifestSha256") != manifest["manifestSha256"]:
            raise ValueError(f"{case['id']}: review packet SHA-256 mismatch")
        if decision.get("caseFingerprint") != case["caseFingerprint"]:
            raise ValueError(f"{case['id']}: case fingerprint mismatch")
        reviewer, reviewed_at = decision.get("reviewer"), decision.get("reviewedAt")
        if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reviewed_at, str):
            raise ValueError(f"{case['id']}: reviewer and reviewedAt are required")
        try:
            parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{case['id']}: reviewedAt must be ISO-8601") from error
        if parsed.tzinfo is None:
            raise ValueError(f"{case['id']}: reviewedAt must include a timezone")
        rationale, verified_quote = decision.get("rationale"), decision.get("verifiedQuote")
        if not isinstance(rationale, str) or not rationale.strip() or not isinstance(verified_quote, str):
            raise ValueError(f"{case['id']}: rationale and verifiedQuote are required")
        page_text, _image_count = _page(case["source"], case["sourcePage"])
        if not verified_quote.strip() or verified_quote not in page_text or case["sourceLocator"]["quote"] not in page_text:
            raise ValueError(f"{case['id']}: verifiedQuote is not present on the cited source page")
        outcome = decision.get("decision")
        if outcome not in {"accepted", "rejected"}:
            raise ValueError(f"{case['id']}: decision must be accepted or rejected")
        case["reviewStatus"] = "human_accepted" if outcome == "accepted" else "human_rejected"
        case["reviewer"] = reviewer.strip()
        case["review"] = {"reviewedAt": reviewed_at, "decision": outcome, "rationale": rationale.strip(), "verifiedQuote": verified_quote}
        case["counted"] = False
    manifest["report"] = {
        "cases": len(by_id),
        "realCaseReviewed": sum(case.get("reviewStatus") in {"human_accepted", "human_rejected"} for case in by_id.values()),
        "pendingHumanReview": sum(case.get("reviewStatus") == "pending_human_review" for case in by_id.values()),
        "goldCount": 0,
        "counted": False,
        "claim": "Only explicitly imported single-review decisions are reported; goldCount remains zero until the separate dual-review protocol completes.",
    }
    manifest["counted"] = False
    manifest.pop("manifestSha256", None)
    manifest["manifestSha256"] = _manifest_hash(manifest)
    return manifest


def render_markdown(manifest: dict[str, Any]) -> str:
    lines = [f"# {manifest['title']}", "", "真实开放材料待人工复核包。机检不计为人工结论；教材 PDF 不会复制到此包。", "",
             f"案例：{manifest['report']['cases']}；数学：{manifest['report'].get('mathCases', 0)}；英语：{manifest['report'].get('englishCases', 0)}；待复核：{manifest['report']['pendingHumanReview']}", ""]
    for case in manifest["cases"]:
        lines.extend([f"## {case['id']} ({case['subject']})", "",
                      f"- 来源：{case['source']['url']}；许可：{case['source']['license']}；版本：{case['source']['version']}；页脚署名：{case['source']['attribution']}",
                      f"- SHA-256：`{case['source']['contentSha256']}`；页码：{case['sourcePage']}；定位：{case['sourceLocator']['quote']}",
                      f"- 状态：{case['reviewStatus']}；counted：{str(case['counted']).lower()}",
                      f"- 预期：`{json.dumps(case['expected'], ensure_ascii=False)}`",
                      f"- 实际：`{json.dumps(case['actual'], ensure_ascii=False)}`", ""])
    lines.extend(["## 决策导入格式", "", "逐案填写 decisions 数组；单人审核不计入双审 goldCount。decision 为 accepted/rejected，verifiedQuote 必须逐字出现在所引 PDF 页。", "",
                  "```json", json.dumps({"decisions": [{"caseId": "...", "manifestSha256": manifest["manifestSha256"], "caseFingerprint": "...", "sourceSha256": "...", "reviewer": "真实复核者", "reviewedAt": "2026-10-03T12:00:00+08:00", "decision": "accepted", "rationale": "复核依据", "verifiedQuote": "来源页中的原文"}]}, ensure_ascii=False, indent=2), "```", ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare")
    prep.add_argument("--source-pack", type=Path, required=True)
    prep.add_argument("--out-dir", type=Path, required=True)
    imp = commands.add_parser("import-review")
    imp.add_argument("--manifest", type=Path, required=True)
    imp.add_argument("--decisions", type=Path, required=True)
    imp.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(json.loads(args.source_pack.read_text(encoding="utf-8")))
            args.out_dir.mkdir(parents=True, exist_ok=True)
            (args.out_dir / "manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (args.out_dir / "cases.jsonl").write_text("".join(json.dumps(case, ensure_ascii=False) + "\n" for case in result["cases"]), encoding="utf-8")
            (args.out_dir / "review-packet.md").write_text(render_markdown(result), encoding="utf-8")
            print(json.dumps(result["report"], ensure_ascii=False))
        else:
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            decisions = json.loads(args.decisions.read_text(encoding="utf-8"))
            result = import_reviews(manifest, decisions)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(result["report"], ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
