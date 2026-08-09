"""处理上传教材 PDF 的同步应用服务。

HTTP 路由只负责请求解析和文件响应；本服务拥有两个长流程：完成首次上传、处理后续页批次。
未来接入后台 Worker 时可直接调用这些方法，不需要复制 OCR、生成、缓存和状态迁移规则。
"""

from __future__ import annotations

import hashlib
import shutil
import time
from typing import Any

from fastapi import HTTPException
from pypdf import PdfReader

from lesson_contracts import lesson_document_from_payload
from observability import log_event
from question_pipeline import write_model_prompt_artifact
from question_processing import process_question_sources
from question_source import (
    MARKDOWN_IMAGE_PATTERN,
    MAX_QUESTIONS_PER_BATCH,
    limited_question_sources,
    split_question_sources,
)
from textbook_ocr import extract_pdf_text, resolve_ocr_text


PDF_BATCH_PAGES = 5


class TextbookProcessingService:
    """协调一份 PDF 的 OCR、题目生成、批次状态和持久化。"""

    def __init__(self, *, store: Any, upload_registry: Any, ocr_runtime: Any) -> None:
        self.store = store
        self.upload_registry = upload_registry
        self.ocr_runtime = ocr_runtime

    def _persist_lessons(
        self,
        upload_id: str,
        payloads: list[dict[str, Any]],
        guide_cards_list: list[list[dict[str, Any]]],
    ) -> None:
        """同时保存生成题快照和对应可编程课程文档。"""
        question_keys = [item["question"]["sourceQuestionKey"] for item in payloads]
        self.store.save_questions(
            upload_id,
            list(zip(question_keys, payloads, guide_cards_list)),
        )
        for item, cards in zip(payloads, guide_cards_list):
            self.store.save_lesson(lesson_document_from_payload(
                item,
                source_upload_id=upload_id,
                guide_cards=cards,
            ))

    def complete_upload(self, upload_id: str) -> dict[str, Any]:
        """合并全部分块、验证 PDF，并处理首个页面批次。"""
        job = self.upload_registry.get(upload_id)
        log_event("upload.processing.started", upload_id=upload_id, filename=job.get("filename"))
        if job["status"] == "complete" and job.get("result"):
            return job["result"]

        chunk_paths = [
            job["directory"] / f"chunk-{index:06d}.part"
            for index in range(job["totalChunks"])
        ]
        missing = [index for index, path in enumerate(chunk_paths) if not path.exists()]
        if missing:
            raise HTTPException(status_code=409, detail=f"仍缺少 {len(missing)} 个分块")

        self.upload_registry.update(job, "merging", 22, "正在合并分块并计算文件校验值")
        source_path = job["directory"] / "source.pdf"
        digest = hashlib.sha256()
        written = 0
        with source_path.open("wb") as merged:
            for chunk_path in chunk_paths:
                with chunk_path.open("rb") as chunk:
                    while block := chunk.read(1024 * 1024):
                        merged.write(block)
                        digest.update(block)
                        written += len(block)
        if written != job["size"]:
            self.upload_registry.update(job, "failed", 22, "合并后的 PDF 大小校验失败")
            raise HTTPException(status_code=400, detail="合并后的 PDF 大小校验失败")

        content_import_id = f"pdf-{digest.hexdigest()[:12]}"
        existing = self.store.find_completed_import(
            content_import_id,
            exclude_upload_id=upload_id,
        )
        if existing:
            self.upload_registry.update(
                job,
                "duplicate",
                22,
                f"内容与已有教材重复：{existing['filename']}",
            )
            shutil.rmtree(job["directory"], ignore_errors=True)
            self.upload_registry.uploads.pop(upload_id, None)
            log_event(
                "upload.duplicate.rejected",
                upload_id=upload_id,
                duplicate_of=existing["uploadId"],
                filename=job.get("filename"),
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"这本教材已存在（{existing['filename']}），"
                    "请在教材库中打开，或删除后再重新上传。"
                ),
            )

        self.upload_registry.update(job, "validating", 28, "文件合并完成，正在读取 PDF 页数")
        try:
            self.upload_registry.validate_pdf_envelope(source_path)
            try:
                page_count = self.ocr_runtime.page_count(source_path)
            except Exception:
                page_count = len(PdfReader(str(source_path)).pages)
            if page_count == 0:
                raise ValueError("PDF 没有页面")
        except Exception as error:
            self.upload_registry.update(job, "failed", 28, f"PDF 校验失败：{error}")
            log_event(
                "upload.processing.failed",
                level=40,
                upload_id=upload_id,
                stage="pdf-validation",
                error_type=type(error).__name__,
                error=str(error)[:300],
                exc_info=True,
            )
            raise HTTPException(status_code=422, detail=f"PDF 无法解析：{error}") from error

        self.upload_registry.update(
            job,
            "splitting",
            35,
            f"校验完成，共 {page_count} 页；正在规划处理批次",
        )
        batches = []
        for start in range(0, page_count, PDF_BATCH_PAGES):
            end = min(start + PDF_BATCH_PAGES, page_count)
            batch_id = len(batches) + 1
            batches.append({
                "id": f"batch-{batch_id:03d}",
                "startPage": start + 1,
                "endPage": end,
                "pageCount": end - start,
                "status": "processed" if batch_id == 1 else "queued",
            })

        # 批次只保存页码范围，始终复用一份源 PDF；不按五页复制文件，扫描教材处理更快且不会重复占用磁盘。
        self.upload_registry.update(
            job,
            "splitting",
            50,
            f"已规划 {len(batches)} 个批次；无需复制整本 PDF",
        )
        for chunk_path in chunk_paths:
            chunk_path.unlink(missing_ok=True)

        first_batch = batches[0]
        extracted_text = ""
        if not job.get("sourceText", "").strip() and not self.ocr_runtime.should_use_mineru():
            try:
                extracted_text = extract_pdf_text(
                    PdfReader(str(source_path)),
                    max_pages=PDF_BATCH_PAGES,
                )
            except Exception:
                extracted_text = ""

        preview_pages = first_batch["pageCount"]
        self.upload_registry.update(
            job,
            "ocr",
            55,
            f"MinerU 正在识别首批 {preview_pages} 页；整本 {page_count} 页无需等待",
        )
        lesson_source, ocr_run = resolve_ocr_text(
            job.get("sourceText", ""),
            extracted_text,
            source_path,
            0,
            preview_pages - 1,
            job["directory"] / "assets" / first_batch["id"],
            f"/api/uploads/{upload_id}/assets/{first_batch['id']}",
        )
        self.upload_registry.update(
            job,
            "generating",
            88,
            "首批内容已提取，正在按题号拆分并生成课程",
        )
        question_sources = limited_question_sources(lesson_source)
        asset_dir = job["directory"] / "assets" / first_batch["id"]
        write_model_prompt_artifact(asset_dir, question_sources)
        ocr_run["sourceArtifactUrl"] = (
            f"/api/uploads/{upload_id}/artifacts/{first_batch['id']}/source.md"
        )
        ocr_run["promptArtifactUrl"] = (
            f"/api/uploads/{upload_id}/artifacts/{first_batch['id']}/model-prompt.md"
        )
        payloads, guide_cards_list, model_runs, review_runs = process_question_sources(
            question_sources,
            first_batch,
            ocr_run,
            asset_dir,
            job,
            self.upload_registry.update,
        )
        payload = payloads[0]
        question_keys = [item["question"]["sourceQuestionKey"] for item in payloads]
        result = {
            "uploadId": upload_id,
            "importId": content_import_id,
            "filename": job["filename"],
            "contentType": "application/pdf",
            "size": job["size"],
            "stored": True,
            "temporary": True,
            "modelRun": model_runs[0],
            "modelRuns": model_runs,
            "ocrRun": ocr_run,
            "reviewRun": review_runs[0],
            "reviewRuns": review_runs,
            "stages": [
                {"id": "upload", "label": "分块上传", "status": "done"},
                {"id": "merge", "label": "PDF 合并与校验", "status": "done"},
                {"id": "split", "label": "按页规划批次", "status": "done"},
                {"id": "ocr", "label": f"首批 {preview_pages} 页 MinerU OCR", "status": "done"},
                {"id": "guides", "label": "结构化与引导卡", "status": "done"},
            ],
            "extraction": {
                "chapter": payload["question"]["chapter"],
                "knowledgePoint": payload["question"]["knowledgePoint"],
                "questionCount": len(payloads),
                "questionLimit": MAX_QUESTIONS_PER_BATCH,
                "formulaCount": sum(block.count("$") // 2 for _, block, _ in question_sources),
                "guideCardCount": sum(len(cards) for cards in guide_cards_list),
                "pageCount": page_count,
                "batchCount": len(batches),
                "confidence": 0.96,
                "mode": f"model-from-{ocr_run['provider']}" if lesson_source else "demo-seed-no-ocr",
            },
            "batches": batches,
            "questionPayload": payload,
            "questionPayloads": payloads,
            "batchQuestionKeys": {first_batch["id"]: question_keys},
        }
        job["batchPayloads"] = dict(zip(question_keys, payloads))
        job["batchGuideCards"] = dict(zip(question_keys, guide_cards_list))
        job["batchQuestionKeys"] = {first_batch["id"]: question_keys}
        job["result"] = result
        job["completedAt"] = time.time()
        self._persist_lessons(upload_id, payloads, guide_cards_list)
        self.upload_registry.update(
            job,
            "complete",
            100,
            f"首批 {preview_pages} 页已拆分为 {len(payloads)} 道题，其余批次可按需处理",
        )
        log_event(
            "upload.processing.completed",
            upload_id=upload_id,
            page_count=page_count,
            batch_count=len(batches),
            question_count=len(payloads),
            ocr_provider=ocr_run.get("provider"),
        )
        return result

    def process_batch(
        self,
        upload_id: str,
        batch_id: str,
        force: bool = False,
        *,
        persist: bool = True,
    ) -> dict[str, Any]:
        """OCR 一个页范围，并可选择是否立即保存生成练习。

        试卷版本服务使用 ``persist=False``，先为新版分配全新 lesson ID 再保存；这样不会
        静默覆盖已发布版本引用的旧课程文档。
        """
        job = self.upload_registry.get(upload_id)
        log_event("upload.batch.started", upload_id=upload_id, batch_id=batch_id, force=force)
        result = job.get("result")
        if job.get("status") != "complete" or not result:
            raise HTTPException(status_code=409, detail="请先完成教材首批处理")

        batch = next(
            (item for item in result.get("batches", []) if item["id"] == batch_id),
            None,
        )
        if not batch:
            raise HTTPException(status_code=404, detail="没有找到这个教材批次")

        batch_question_keys = job.setdefault("batchQuestionKeys", {}).get(batch_id, [])
        stored_payloads = [
            job.setdefault("batchPayloads", {})[key]
            for key in batch_question_keys
            if key in job["batchPayloads"]
        ]
        stored_payload = (
            stored_payloads[0]
            if stored_payloads
            else job.setdefault("batchPayloads", {}).get(batch_id)
        )
        if stored_payload and not force:
            return {
                "batch": batch,
                "questionPayload": stored_payload,
                "questionPayloads": stored_payloads or [stored_payload],
                "ocrRun": result["ocrRun"],
                "modelRun": stored_payload["modelRun"],
            }

        # 进程内集合只能防止单实例重复点击。多 Worker 部署必须换成持久化任务锁或队列；
        # 因为锁位于服务编排边界，未来替换时不需要修改 HTTP 路由和题目纯函数。
        processing = job.setdefault("processingBatches", set())
        if batch_id in processing:
            raise HTTPException(status_code=409, detail="这个批次正在处理中")
        processing.add(batch_id)
        try:
            source_path = job["directory"] / "source.pdf"
            start_page = batch["startPage"] - 1
            end_page = batch["endPage"] - 1
            # 多读取前一页，因为题干可能从上个批次末尾开始；来源元数据仍记录真实目标页段，
            # 防止为提高识别完整度而污染页面归属。
            ocr_start_page = max(0, start_page - 1)
            asset_dir = job["directory"] / "assets" / batch_id
            cached_markdown = asset_dir / "source.md"
            extracted_text = ""
            if not self.ocr_runtime.should_use_mineru():
                reader = PdfReader(str(source_path))
                pages = []
                for page_index in range(ocr_start_page, end_page + 1):
                    text = (reader.pages[page_index].extract_text() or "").strip()
                    if text:
                        pages.append(text)
                extracted_text = "\n\n".join(pages)[:16_000]

            # 重试时复用已落盘的 MinerU Markdown。模型可以重新生成，但昂贵 OCR 不应重复执行。
            if cached_markdown.is_file():
                lesson_source = cached_markdown.read_text(
                    encoding="utf-8",
                    errors="replace",
                )[:40_000]
                image_urls = [
                    f"/api/uploads/{upload_id}/assets/{batch_id}/{path.name}"
                    for path in sorted(asset_dir.iterdir())
                    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
                ]
                ocr_run = {
                    "requestedProvider": self.ocr_runtime.selection.provider,
                    "provider": "mineru",
                    "mode": "persisted-markdown",
                    "fallback": False,
                    "output": "markdown",
                    "startPage": ocr_start_page + 1,
                    "endPage": end_page + 1,
                    "imageUrls": image_urls,
                }
            else:
                lesson_source, ocr_run = resolve_ocr_text(
                    "",
                    extracted_text,
                    source_path,
                    ocr_start_page,
                    end_page,
                    asset_dir,
                    f"/api/uploads/{upload_id}/assets/{batch_id}",
                )
            context_note = (
                f"\n\n[页码说明：识别内容来自第 {ocr_start_page + 1}-{end_page + 1} 页；"
                f"目标批次为第 {start_page + 1}-{end_page + 1} 页。前一页只用于补齐跨页题干。]\n"
            )
            question_sources = limited_question_sources(lesson_source)
            if not split_question_sources(lesson_source):
                question_sources = [
                    ("", context_note + lesson_source, MARKDOWN_IMAGE_PATTERN.findall(lesson_source))
                ]
            write_model_prompt_artifact(asset_dir, question_sources)
            ocr_run["sourceArtifactUrl"] = (
                f"/api/uploads/{upload_id}/artifacts/{batch_id}/source.md"
            )
            ocr_run["promptArtifactUrl"] = (
                f"/api/uploads/{upload_id}/artifacts/{batch_id}/model-prompt.md"
            )
            payloads, guide_cards_list, model_runs, review_runs = process_question_sources(
                question_sources,
                batch,
                ocr_run,
                asset_dir,
                job,
                self.upload_registry.update,
            )
            payload = payloads[0]
            question_keys = [item["question"]["sourceQuestionKey"] for item in payloads]
            response_batch = dict(batch)
            response_batch["status"] = "processed"
            if persist:
                batch["status"] = "processed"
                self._persist_lessons(upload_id, payloads, guide_cards_list)
                for key, item, cards in zip(question_keys, payloads, guide_cards_list):
                    job["batchPayloads"][key] = item
                    job.setdefault("batchGuideCards", {})[key] = cards
                job.setdefault("batchQuestionKeys", {})[batch_id] = question_keys
                result.setdefault("batchQuestionKeys", {})[batch_id] = question_keys
                result["questionPayloads"] = [
                    item
                    for key in sorted(job["batchPayloads"])
                    for item in [job["batchPayloads"][key]]
                ]
                result["questionPayload"] = result["questionPayloads"][0]
                result["extraction"]["questionCount"] = len(result["questionPayloads"])
                result["extraction"]["guideCardCount"] = sum(
                    len(cards) for cards in job.get("batchGuideCards", {}).values()
                )
            self.upload_registry.update(
                job,
                "complete",
                100,
                (
                    f"批次 {batch['startPage']}-{batch['endPage']} 页已更新 {len(payloads)} 道题"
                    if persist else
                    f"批次 {batch['startPage']}-{batch['endPage']} 页已生成审核新版"
                ),
            )
            log_event(
                "upload.batch.completed",
                upload_id=upload_id,
                batch_id=batch_id,
                question_count=len(payloads),
                ocr_provider=ocr_run.get("provider"),
            )
            return {
                "batch": response_batch,
                "questionPayload": payload,
                "questionPayloads": payloads,
                "guideCards": guide_cards_list,
                "ocrRun": ocr_run,
                "modelRun": model_runs[0],
                "modelRuns": model_runs,
                "reviewRun": review_runs[0],
                "reviewRuns": review_runs,
            }
        except HTTPException as error:
            if persist:
                self._record_batch_failure(job, batch, batch_id, str(error.detail), error)
            raise
        except Exception as error:
            if persist:
                self._record_batch_failure(job, batch, batch_id, str(error), error)
            raise HTTPException(status_code=422, detail=f"批次处理失败：{error}") from error
        finally:
            processing.discard(batch_id)

    def _record_batch_failure(
        self,
        job: dict[str, Any],
        batch: dict[str, Any],
        batch_id: str,
        detail: str,
        error: Exception,
    ) -> None:
        """Keep the previous lesson usable when a later batch fails."""
        batch["status"] = "failed"
        batch["error"] = detail
        self.upload_registry.update(
            job,
            "complete",
            100,
            f"批次处理失败，已保留原题：{detail}",
        )
        log_event(
            "upload.batch.failed",
            level=40,
            upload_id=job.get("uploadId"),
            batch_id=batch_id,
            error_type=type(error).__name__,
            error=detail[:300],
            exc_info=True,
        )
