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
from question_processing import _generate_validated_question, process_question_sources
from question_source import (
    MARKDOWN_IMAGE_PATTERN,
    MAX_QUESTIONS_PER_BATCH,
    limited_question_sources,
    question_key,
    split_question_sources,
)
from textbook_ocr_pipeline import resolve_routed_ocr_source


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

    def _load_batch_sources(
        self,
        *,
        upload_id: str,
        job: dict[str, Any],
        batch: dict[str, Any],
        result: dict[str, Any],
        refresh_ocr: bool = False,
    ) -> tuple[str, dict[str, Any], Any, list[tuple[str, str, list[str]]]]:
        """读取批次 OCR 来源，并切成稳定题块。

        单题修复和整批重生成共享这段准备逻辑。默认复用内容寻址缓存，只有明确刷新 OCR
        时才重新启动 Provider，避免一次修复无谓地重复 MinerU。
        """
        source_path = job["directory"] / "source.pdf"
        start_page = batch["startPage"] - 1
        end_page = batch["endPage"] - 1
        ocr_start_page = max(0, start_page - 1)
        asset_dir = job["directory"] / "assets" / batch["id"]
        content_hash = result.get("sourceFingerprint")
        if not content_hash:
            digest = hashlib.sha256()
            with source_path.open("rb") as source_file:
                while block := source_file.read(1024 * 1024):
                    digest.update(block)
            content_hash = digest.hexdigest()
        lesson_source, ocr_run = resolve_routed_ocr_source(
            runtime=self.ocr_runtime,
            source_text="",
            source_path=source_path,
            start_page=ocr_start_page,
            end_page=end_page,
            asset_dir=asset_dir,
            asset_url_prefix=f"/api/uploads/{upload_id}/assets/{batch['id']}",
            cache_dir=job["directory"] / "ocr-cache",
            content_hash=content_hash,
            refresh=refresh_ocr,
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
        ocr_run["sourceArtifactUrl"] = f"/api/uploads/{upload_id}/artifacts/{batch['id']}/source.md"
        ocr_run["promptArtifactUrl"] = f"/api/uploads/{upload_id}/artifacts/{batch['id']}/model-prompt.md"
        return lesson_source, ocr_run, asset_dir, question_sources

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
        preview_pages = first_batch["pageCount"]
        self.upload_registry.update(
            job,
            "ocr",
            55,
            f"正在按页面特征识别首批 {preview_pages} 页；整本 {page_count} 页无需等待",
        )
        lesson_source, ocr_run = resolve_routed_ocr_source(
            runtime=self.ocr_runtime,
            source_text=job.get("sourceText", ""),
            source_path=source_path,
            start_page=0,
            end_page=preview_pages - 1,
            asset_dir=job["directory"] / "assets" / first_batch["id"],
            asset_url_prefix=f"/api/uploads/{upload_id}/assets/{first_batch['id']}",
            cache_dir=job["directory"] / "ocr-cache",
            content_hash=digest.hexdigest(),
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
            "sourceFingerprint": digest.hexdigest(),
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
                {"id": "ocr", "label": f"首批 {preview_pages} 页自适应 OCR", "status": "done"},
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
        refresh_ocr: bool = False,
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
            lesson_source, ocr_run, asset_dir, question_sources = self._load_batch_sources(
                upload_id=upload_id,
                job=job,
                batch=batch,
                result=result,
                refresh_ocr=refresh_ocr,
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

    def regenerate_question(
        self,
        upload_id: str,
        source_question_key: str,
        *,
        refresh_ocr: bool = False,
    ) -> dict[str, Any]:
        """只重新生成一题，并保留同批次其它题目的快照。

        题目修复默认复用 OCR 缓存：模型或审核规则有问题时，不应因为修复一题而重新
        识别整页。若页面本身疑似识别错，调用方可显式传 ``refresh_ocr=True``，此时
        仍只处理题目所在批次，并绕过该批次的内容寻址 OCR 缓存。
        """
        job = self.upload_registry.get(upload_id)
        result = job.get("result")
        if job.get("status") != "complete" or not result:
            raise HTTPException(status_code=409, detail="请先完成教材首批处理")

        old_payload = job.setdefault("batchPayloads", {}).get(source_question_key)
        if not old_payload:
            raise HTTPException(status_code=404, detail="没有找到要修复的题目")
        batch_id = old_payload.get("question", {}).get("sourceBatchId")
        batch = next((item for item in result.get("batches", []) if item["id"] == batch_id), None)
        if not batch:
            raise HTTPException(status_code=404, detail="没有找到题目所属批次")

        processing = job.setdefault("processingBatches", set())
        lock_key = f"question:{source_question_key}"
        if lock_key in processing:
            raise HTTPException(status_code=409, detail="这个题目正在修复中")
        processing.add(lock_key)
        try:
            _lesson_source, ocr_run, asset_dir, question_sources = self._load_batch_sources(
                upload_id=upload_id,
                job=job,
                batch=batch,
                result=result,
                refresh_ocr=refresh_ocr,
            )
            target_index = -1
            target_number = ""
            target_block = ""
            target_images: list[str] = []
            for index, (number, block, images) in enumerate(question_sources):
                if question_key(batch_id, number, index) == source_question_key:
                    target_index = index
                    target_number, target_block, target_images = number, block, images
                    break
            if target_index < 0:
                raise HTTPException(status_code=409, detail="OCR 结果中已找不到这道题，请刷新 OCR 后重试")

            payload, guide_cards, model_run, review_run = _generate_validated_question(
                number=target_number,
                block=target_block,
                images=target_images,
                index=target_index,
                batch=batch,
                ocr_run=ocr_run,
                asset_dir=asset_dir,
            )
            # 业务引用使用稳定的来源键，生成题目的内部 id 可以变化；这样只替换当前题，
            # 不会让学生端已有的其它题目、排序和发布快照失效。
            payload["question"]["sourceQuestionKey"] = source_question_key
            self._persist_lessons(upload_id, [payload], [guide_cards])
            job["batchPayloads"][source_question_key] = payload
            job.setdefault("batchGuideCards", {})[source_question_key] = guide_cards
            result["questionPayload"] = payload
            ordered_keys = [
                key
                for current_batch in result.get("batches", [])
                for key in job.setdefault("batchQuestionKeys", {}).get(current_batch["id"], [])
            ]
            result["questionPayloads"] = [
                job["batchPayloads"][key]
                for key in ordered_keys
                if key in job["batchPayloads"]
            ]
            self.upload_registry.update(job, "complete", 100, f"已修复题目 {source_question_key}")
            log_event(
                "upload.question.regenerated",
                upload_id=upload_id,
                source_question_key=source_question_key,
                batch_id=batch_id,
                refresh_ocr=refresh_ocr,
                ocr_provider=ocr_run.get("provider"),
            )
            return {
                "batch": batch,
                "questionPayload": payload,
                "guideCards": guide_cards,
                "ocrRun": ocr_run,
                "modelRun": model_run,
                "reviewRun": review_run,
                "regeneration": {"scope": "question", "refreshOcr": refresh_ocr},
            }
        except HTTPException:
            raise
        except Exception as error:
            log_event(
                "upload.question.regeneration.failed",
                level=40,
                upload_id=upload_id,
                source_question_key=source_question_key,
                error_type=type(error).__name__,
                error=str(error)[:300],
                exc_info=True,
            )
            raise HTTPException(status_code=422, detail=f"题目修复失败：{error}") from error
        finally:
            processing.discard(lock_key)

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
