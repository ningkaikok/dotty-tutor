"""Compare observed textbook text with proposed transcription without models or approval.

This reports local, page-pinned differences. It cannot establish real scan OCR
accuracy or turn a science source candidate into an approved teaching artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


def build_source_review_report(candidate: dict[str, Any], transcriptions: dict[str, str]) -> dict[str, Any]:
    """Preserve original observations and leave every proposed correction pending review."""
    if candidate.get("schemaVersion") != "teaching-candidate-v1":
        raise ValueError("请提供现有教材候选准备工具生成的来源包")
    document = candidate.get("document") or {}
    digest = document.get("contentSha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("候选来源缺少有效原文件指纹")
    chapter = candidate.get("chapterRequest") or {}
    source = chapter.get("source") or {}
    pages = source.get("pages") or []
    numbers = {str(item["page"]) for item in pages}
    if (not isinstance(transcriptions, dict) or any(key not in numbers for key in transcriptions)
            or any(not isinstance(text, str) or len(text) > 20_000 for text in transcriptions.values())):
        raise ValueError("转写必须按候选物理页码提供文本，每页最多 20000 字符")
    rows = []
    for page in pages:
        number = int(page["page"])
        observed = str(page.get("text") or "")
        proposed = transcriptions.get(str(number))
        differences = []
        if proposed is not None:
            differences = [{"kind": tag, "observedStart": a, "observedEnd": b,
                            "proposedStart": c, "proposedEnd": d,
                            "observed": observed[a:b], "proposed": proposed[c:d]}
                           for tag, a, b, c, d in SequenceMatcher(None, observed, proposed, autojunk=False).get_opcodes()
                           if tag != "equal"]
        rows.append({"page": number, "sourceLocator": {"sourceFileSha256": digest, "page": number},
                     "observedText": observed, "observedTextSha256": hashlib.sha256(observed.encode()).hexdigest(),
                     "sourceFlags": page.get("flags", []), "proposedText": proposed,
                     "proposedTextSha256": hashlib.sha256(proposed.encode()).hexdigest() if proposed is not None else None,
                     "differences": differences,
                     "status": "pending_human_review" if proposed is not None else "awaiting_transcription"})
    checklist = ["逐页对照原图及文本", "核对公式、上下标和缺失条件", "核对图片归属与说明", "确认来源许可和署名"]
    if chapter.get("subject") == "science":
        checklist.extend(["核对科学概念、单位及图表", "准备一个有来源图表及活动的待审课时", "记录实际生成模型后再做学科内容审核"])
    return {"schemaVersion": "source-review-report-v1", "subject": chapter.get("subject"),
            "title": chapter.get("title"), "inputKind": candidate.get("inputKind"),
            "sourceFileSha256": digest, "sourceVersion": source.get("sourceVersion"),
            "sourceUrl": source.get("sourceUrl"), "license": source.get("license"),
            "attributionText": source.get("attributionText"), "pages": rows,
            "humanReview": "pending", "counted": False, "modelExecuted": False,
            "ocrExecuted": False, "ocrAccuracy": None, "teachingArtifactReady": False,
            "reviewChecklist": checklist,
            "limitations": ["文字差异不是 OCR 准确率", "待审转写不修改原始 OCR 或来源修订", "报告不创建课件、人工批准或金标准案例"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成逐页来源校对报告，不修改原文、不调用模型、不写入应用库")
    parser.add_argument("candidate", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--transcriptions", type=Path, help='人工待审转写 JSON，例如 {"12": "校对文本"}')
    args = parser.parse_args()
    try:
        candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
        texts = json.loads(args.transcriptions.read_text(encoding="utf-8")) if args.transcriptions else {}
        if not isinstance(candidate, dict):
            raise ValueError("候选来源必须是 JSON 对象")
        report = build_source_review_report(candidate, texts)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        args.output.chmod(0o600)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(2, f"来源校对报告生成失败：{type(error).__name__}；请核对候选包、转写与输出路径\n")
    print(json.dumps({"pages": len(report["pages"]), "humanReview": "pending", "ocrAccuracy": None}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
