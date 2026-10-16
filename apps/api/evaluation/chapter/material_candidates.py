"""Prepare bounded, source-pinned teaching candidates without models or approvals.

The PDF remains external to Git. Text-layer extraction is an observation, never
evidence of scanned OCR accuracy or of a valid teaching answer key.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from domain.chapters.source import fingerprint, sentences
from domain.contracts.chapter import ChapterCreate
from domain.courseware.profiles import courseware_profiles
from ocr_quality import evaluate_page_quality


def prepare_candidate(
    pdf: Path, *, subject: str, title: str, source_url: str, source_version: str,
    license_name: str, attribution: str, page_start: int, page_end: int,
    input_kind: str = "digital_pdf",
) -> dict[str, Any]:
    """Preserve raw text and report missing/uncertain evidence without changing it."""
    courseware_profiles.get(subject)
    if pdf.is_symlink() or not pdf.is_file():
        raise ValueError("候选教材必须是本机普通 PDF 文件，不能是符号链接")
    if pdf.suffix.lower() != ".pdf":
        raise ValueError("候选教材必须是 PDF")
    url = urlparse(source_url)
    if url.scheme != "https" or not url.netloc or url.username or url.password:
        raise ValueError("来源链接必须是无凭据的 HTTPS 链接")
    if not all(value.strip() for value in (title, source_version, license_name, attribution)):
        raise ValueError("必须提供教材标题、来源版本、许可和署名")
    if input_kind not in {"digital_pdf", "paper_scan"}:
        raise ValueError("未知材料类型")
    if not 1 <= page_start <= page_end or page_end - page_start + 1 > 80:
        raise ValueError("候选页段必须连续且不超过 80 页")
    raw = pdf.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        reader = PdfReader(BytesIO(raw))
    except PdfReadError as error:
        raise ValueError("PDF 无法解析，请核对原文件") from error
    if reader.is_encrypted or page_end > len(reader.pages):
        raise ValueError("PDF 已加密或候选页段超出原文件")
    pages, diagnostics = [], []
    for number in range(page_start, page_end + 1):
        text = reader.pages[number - 1].extract_text() or ""
        warnings = []
        if not text.strip():
            warnings.append("empty_text_layer")
        if re.search(r"\b[A-Za-z]{35,}\b", text):
            warnings.append("joined_words_suspected")
        quality = evaluate_page_quality(text, page_number=number, provider="none", retry_count=2)
        if quality.get("formulaReview"):
            warnings.append("formula_requires_review")
        pages.append({"page": number, "text": text, "flags": ["unreadable"] if warnings else []})
        diagnostics.append({"page": number, "textCharacters": len(text), "warnings": warnings})
    source = {
        "sourceVersion": source_version, "sourceTitle": title, "sourceUrl": source_url,
        "license": license_name, "attributionText": attribution, "distributionScope": "private",
        "pageStart": page_start, "pageEnd": page_end, "pages": pages,
    }
    revision = fingerprint({"source": source, "fileSha256": digest})
    for page in pages:
        page["sentences"] = sentences(page["text"], page["page"], revision)
    request = ChapterCreate.model_validate({"teachingMode": "tutorial", "subject": subject, "title": title, "source": source})
    return {
        "schemaVersion": "teaching-candidate-v1", "sourceKind": "public", "counted": False,
        "humanReview": "pending", "modelExecuted": False, "ocrExecuted": False,
        "inputKind": input_kind, "extractionMethod": "pypdf_text_layer",
        "sourceSnapshotSha256": fingerprint({"source": request.source.model_dump(mode="json"), "fileSha256": digest}),
        "document": {"file": str(pdf.resolve()), "contentSha256": digest, "totalPages": len(reader.pages)},
        "chapterRequest": request.model_dump(mode="json"), "diagnostics": diagnostics,
        "nextActions": ["上传原 PDF 后绑定受管 uploadId", "逐页核对原图及提取文本，修订疑似问题", "明确教学目标后生成待确认规划"],
        "limitations": ["候选材料不是已生成课件", "没有教师或双人批准，不能计入人工金标准", "未执行 OCR；数字 PDF 原页不是真实纸质扫描验收"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="准备待教师确认的来源教材候选，不调用模型或写入应用库")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-version", required=True)
    parser.add_argument("--license", required=True, dest="license_name")
    parser.add_argument("--attribution", required=True)
    parser.add_argument("--page-start", type=int, required=True)
    parser.add_argument("--page-end", type=int, required=True)
    parser.add_argument("--input-kind", choices=("digital_pdf", "paper_scan"), default="digital_pdf")
    args = parser.parse_args()
    try:
        candidate = prepare_candidate(
            args.pdf, subject=args.subject, title=args.title, source_url=args.source_url,
            source_version=args.source_version, license_name=args.license_name,
            attribution=args.attribution, page_start=args.page_start, page_end=args.page_end,
            input_kind=args.input_kind,
        )
        # Exclusive creation protects both source files and prior human edits.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n")
        args.output.chmod(0o600)
    except (ValueError, OSError) as error:
        parser.exit(2, f"候选准备失败：{error}\n")
    print(json.dumps({"pages": len(candidate["diagnostics"]), "humanReview": "pending", "counted": False,
                      "warningPages": [row["page"] for row in candidate["diagnostics"] if row["warnings"]]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
