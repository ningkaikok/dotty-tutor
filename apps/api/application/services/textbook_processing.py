"""处理上传教材 PDF 的同步应用服务。

HTTP 路由只负责请求解析和文件响应；本服务拥有两个长流程：完成首次上传、处理后续页批次。
未来接入后台 Worker 时可直接调用这些方法，不需要复制 OCR、生成、缓存和状态迁移规则。
"""

from __future__ import annotations

import copy
import hashlib
import os
import re
import shutil
import socket
import subprocess
import time
import uuid
from typing import Any

from fastapi import HTTPException
from pypdf import PdfReader

from application.job_worker import JobCancelled
from application.services.lesson_generation import (
    generate_lesson,
    generate_question_from_ir,
    write_staged_prompt_artifact,
)
from application.services.question_processing import (
    _attach_question_ir_provenance,
    _generate_validated_question,
    process_question_sources,
)
from application.services.staged_question_generation import normalize_stage
from domain.contracts.lesson import lesson_document_from_payload
from domain.questions.exam_ir import build_exam_ir
from domain.questions.pipeline import apply_question_quality_gate
from domain.questions.quality import build_import_quality_report
from domain.questions.source import (
    MARKDOWN_IMAGE_PATTERN,
    MAX_FULL_PAPER_QUESTIONS_PER_BATCH,
    MAX_QUESTIONS_PER_BATCH,
    limited_question_sources,
    looks_like_multi_question_document,
    question_key,
    split_question_sources,
)
from observability import log_event
from run_audit import RunAudit, build_run_config
from textbook_ocr_pipeline import resolve_routed_ocr_source

PDF_BATCH_PAGES = 5


def _bounded_env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Read an operator limit without allowing a client or unsafe env to widen it."""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


# These are deliberately bounded in code: env config can reduce limits for a local demo,
# but it can never turn a single request into an unbounded OCR/model workload.
MAX_FULL_PAPER_PAGES = _bounded_env_int(
    "DOTTY_MAX_FULL_PAPER_PAGES", 50, minimum=1, maximum=50,
)
MAX_FULL_PAPER_QUESTIONS = _bounded_env_int(
    "DOTTY_MAX_FULL_PAPER_QUESTIONS", 100, minimum=1, maximum=100,
)

# 批次熔断（roadmap T2）：连续命中这么多次"系统性失败"信号后暂停整批剩余部分，
# 不再把剩下的题逐一磨成失败记录。已经成功/跳过的批次不受影响。
SYSTEMIC_FAILURE_HALT_THRESHOLD = _bounded_env_int(
    "DOTTY_SYSTEMIC_FAILURE_HALT_THRESHOLD", 3, minimum=1, maximum=20,
)

# 三类"系统性失败"文案信号：API key 过期/配额耗尽、429 限流、上游超时。这些是
# roadmap 明确列出的边界；除此之外的失败（OCR 解析、题目质量不达标等）继续按
# 现状逐题记录，不参与熔断计数。Provider 适配层（Codex CLI/Ollama）大多把错误
# 包成纯文本，没有统一的结构化错误码，因此在状态码之外仍需要少量文案信号兜底。
_RATE_LIMIT_MARKERS = ("429", "rate limit", "rate_limit", "too many requests", "限流")
_QUOTA_OR_AUTH_MARKERS = (
    "401", "403", "unauthorized", "invalid_api_key", "invalid api key",
    "insufficient_quota", "quota", "api key", "过期", "配额",
)
_UPSTREAM_TIMEOUT_MARKERS = ("timed out", "timeout", "deadline exceeded", "超时")


def _systemic_failure_signal(error: BaseException) -> tuple[str, str] | None:
    """识别一次批次失败是否属于"在你去修配置之前什么都做不出来"的系统性失败。

    沿 ``__cause__``/``__context__`` 链向上查找（``RuntimeExecutionError`` 等
    包装类型会把原始 Provider 异常挂在 ``cause`` 上），优先用 HTTP 状态码判断
    （429/401/403/408/504，与 ``job_worker.RETRYABLE_HTTP_STATUS_CODES`` 同一套
    编号语义），状态码不可用时再退回到错误文案里的关键词信号。命中返回
    ``(category, 可展示原因)``；未命中返回 ``None``，调用方按现状逐题记录。
    """
    node: BaseException | None = error
    seen: set[int] = set()
    depth = 0
    while node is not None and id(node) not in seen and depth < 4:
        seen.add(id(node))
        depth += 1
        if isinstance(node, (subprocess.TimeoutExpired, socket.timeout, TimeoutError)):
            return "upstream_timeout", f"upstream_timeout: {node}"[:300]
        status_code = getattr(node, "status_code", None)
        if not isinstance(status_code, int):
            code = getattr(node, "code", None)  # urllib.error.HTTPError 用 .code
            status_code = code if isinstance(code, int) else None
        if status_code == 429:
            return "rate_limit", f"rate_limit: HTTP 429 {node}"[:300]
        if status_code in {401, 403}:
            return "quota_or_auth", f"quota_or_auth: HTTP {status_code} {node}"[:300]
        if status_code in {408, 504}:
            return "upstream_timeout", f"upstream_timeout: HTTP {status_code} {node}"[:300]
        text = str(node).lower()
        if any(marker in text for marker in _RATE_LIMIT_MARKERS):
            return "rate_limit", f"rate_limit: {node}"[:300]
        if any(marker in text for marker in _QUOTA_OR_AUTH_MARKERS):
            return "quota_or_auth", f"quota_or_auth: {node}"[:300]
        if any(marker in text for marker in _UPSTREAM_TIMEOUT_MARKERS):
            return "upstream_timeout", f"upstream_timeout: {node}"[:300]
        node = node.__cause__ or node.__context__
    return None


class TextbookProcessingService:
    """协调一份 PDF 的 OCR、题目生成、批次状态和持久化。"""

    def __init__(self, *, store: Any, upload_registry: Any, ocr_runtime: Any) -> None:
        self.store = store
        self.upload_registry = upload_registry
        self.ocr_runtime = ocr_runtime
        self.audit = RunAudit(store)

    def _persist_lessons(
        self,
        upload_id: str,
        payloads: list[dict[str, Any]],
        guide_cards_list: list[list[dict[str, Any]]],
        *,
        run_id: str | None = None,
        operation: str = "initial_batch",
        replace_keys: list[str] | None = None,
        revision_source: str = "model_generated",
    ) -> list[dict[str, Any]]:
        """保存课程文档，并在一个事务中提交题目当前视图与 revision 证据。

        ``lesson_documents`` 仍是独立存储边界；先写课程文档，再提交题目当前视图和
        revision 链，确保课程文档失败时不会覆盖上一份成功题目。极端情况下题目事务
        失败可能留下未被当前视图引用的课程文档，但不会污染学生入口。
        """
        question_keys = [item["question"]["sourceQuestionKey"] for item in payloads]
        revisions: list[dict[str, Any]] = []
        for item, cards in zip(payloads, guide_cards_list):
            self.store.save_lesson(lesson_document_from_payload(
                item,
                source_upload_id=upload_id,
                guide_cards=cards,
            ))
        if run_id:
            # 课程文档先按不可变 lessonId 保存；随后 revision 与当前题目视图在同一事务提交，
            # 因而批次失败不会部分替换学生可见题目。极端数据库失败最多留下尚未被引用的课程文档。
            revisions = self.store.append_revisions_and_save_questions(
                upload_id=upload_id,
                questions=list(zip(question_keys, payloads, guide_cards_list)),
                operation=operation,
                run_id=run_id,
                replace_keys=replace_keys,
                revision_source=revision_source,
            )
        else:
            self.store.save_questions(upload_id, list(zip(question_keys, payloads, guide_cards_list)))
        return revisions

    @staticmethod
    def _check_cancel(cancellation_check: Any) -> None:
        if cancellation_check and cancellation_check():
            raise JobCancelled()

    def _ensure_source_pdf(
        self,
        job: dict[str, Any],
        chunk_paths: list[Any],
        *,
        cancellation_check: Any = None,
    ) -> tuple[Any, str]:
        """Return a durable merged PDF and its fingerprint.

        The upload chunks are disposable once the merge succeeds. A Worker retry must
        therefore resume from ``source.pdf`` instead of treating the cleaned chunks as
        a missing-upload error.
        """
        source_path = job["directory"] / "source.pdf"
        digest = hashlib.sha256()
        if source_path.is_file() and source_path.stat().st_size == job["size"]:
            with source_path.open("rb") as source_file:
                while block := source_file.read(1024 * 1024):
                    self._check_cancel(cancellation_check)
                    digest.update(block)
            return source_path, digest.hexdigest()

        missing = [index for index, path in enumerate(chunk_paths) if not path.exists()]
        if missing:
            raise HTTPException(
                status_code=409,
                detail=f"仍缺少 {len(missing)} 个分块，且没有可恢复的已合并 PDF",
            )

        partial_path = source_path.with_suffix(".pdf.partial")
        digest = hashlib.sha256()
        written = 0
        try:
            with partial_path.open("wb") as merged:
                for chunk_path in chunk_paths:
                    self._check_cancel(cancellation_check)
                    with chunk_path.open("rb") as chunk:
                        while block := chunk.read(1024 * 1024):
                            merged.write(block)
                            digest.update(block)
                            written += len(block)
            if written != job["size"]:
                raise HTTPException(status_code=400, detail="合并后的 PDF 大小校验失败")
            os.replace(partial_path, source_path)
        except Exception:
            partial_path.unlink(missing_ok=True)
            raise
        return source_path, digest.hexdigest()

    def _load_batch_sources(
        self,
        *,
        upload_id: str,
        job: dict[str, Any],
        batch: dict[str, Any],
        result: dict[str, Any],
        refresh_ocr: bool = False,
        question_limit: int = MAX_QUESTIONS_PER_BATCH,
        exclude_question_numbers: set[str] | None = None,
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
        exam_ir = build_exam_ir(
            lesson_source,
            asset_dir=asset_dir,
            batch_id=batch["id"],
            start_page=ocr_start_page + 1,
            end_page=end_page + 1,
            provider=str(ocr_run.get("provider") or "unknown"),
        )
        result.setdefault("examIRByBatch", {})[batch["id"]] = exam_ir.as_dict()
        result.setdefault("questionIRsByBatch", {})[batch["id"]] = [
            question.as_dict() for question in exam_ir.questions[:question_limit]
        ]
        blocks = split_question_sources(lesson_source, asset_dir=asset_dir)
        if not blocks and looks_like_multi_question_document(lesson_source):
            # 切分失败时下面会回退成“整页当作一道题”。对真正的单题文本这是合理兜底，
            # 但对一整张试卷会把说明、几十道题和答案拼成一道看似合法的题，且下游没有
            # 任何环节能发现。这里在来源边界直接失败，让问题停在可定位的地方。
            raise HTTPException(
                status_code=422,
                detail="OCR 题号切分失败：检测到多个题目候选，已阻止整页作为一道题生成，请刷新 OCR 后重试",
            )
        question_sources = [
            (question["number"], question["sourceText"], list(question["visualAssetIds"]))
            for question in exam_ir.as_dict()["questions"][:question_limit]
        ]
        excluded = {str(number).strip() for number in (exclude_question_numbers or set()) if str(number).strip()}
        if excluded:
            question_sources = [item for item in question_sources if item[0] not in excluded]
            result["questionIRsByBatch"][batch["id"]] = [
                item for item in result["questionIRsByBatch"][batch["id"]]
                if str(item.get("number") or "") not in excluded
            ]
        if not blocks or not question_sources:
            question_sources = [
                ("", context_note + lesson_source, MARKDOWN_IMAGE_PATTERN.findall(lesson_source))
            ]
        write_staged_prompt_artifact(asset_dir, question_sources)
        ocr_run["sourceArtifactUrl"] = f"/api/uploads/{upload_id}/artifacts/{batch['id']}/source.md"
        ocr_run["promptArtifactUrl"] = f"/api/uploads/{upload_id}/artifacts/{batch['id']}/model-prompt.md"
        return lesson_source, ocr_run, asset_dir, question_sources

    @staticmethod
    def _question_numbers(payloads: list[dict[str, Any]]) -> set[str]:
        """提取已生成题目的原始题号，用于跨批次 overlap 去重。"""
        numbers: set[str] = set()
        for payload in payloads:
            value = str(payload.get("question", {}).get("questionNumber", "")).strip()
            match = re.match(r"\d{1,3}", value)
            if match:
                numbers.add(match.group(0))
        return numbers

    @staticmethod
    def _reconcile_batch_question_keys(job: dict[str, Any], result: dict[str, Any]) -> None:
        """Recover question-key mappings when an older snapshot only persisted a preview."""
        payload_store = job.setdefault("batchPayloads", {})
        job_keys = job.setdefault("batchQuestionKeys", {})
        result_keys = result.setdefault("batchQuestionKeys", {})
        for key in payload_store:
            if "-q-" not in key:
                continue
            batch_id = key.rsplit("-q-", 1)[0]
            job_keys.setdefault(batch_id, [])
            result_keys.setdefault(batch_id, [])
            if key not in job_keys[batch_id]:
                job_keys[batch_id].append(key)
            if key not in result_keys[batch_id]:
                result_keys[batch_id].append(key)

    @staticmethod
    def _ordered_batch_payloads(
        job: dict[str, Any],
        result: dict[str, Any],
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """按页面批次和 OCR 题目顺序组装题库，禁止用字符串键排序。

        ``q-10`` 的字典序早于 ``q-2``。题库如果按 key 排序，会造成学生端题号跳跃，
        因此顺序的唯一事实来源是 batches + batchQuestionKeys。
        """
        payload_store = job.get("batchPayloads", {})
        key_store = job.get("batchQuestionKeys", {})
        ordered: list[dict[str, Any]] = []
        seen: set[str] = set()
        for batch in result.get("batches", []):
            batch_id = batch.get("id")
            keys = key_store.get(batch_id) or result.get("batchQuestionKeys", {}).get(batch_id, [])
            for key in keys:
                payload = payload_store.get(key)
                if payload is None or key in seen:
                    continue
                seen.add(key)
                ordered.append(payload)
                if limit is not None and len(ordered) >= limit:
                    return ordered
        return ordered

    def complete_upload(
        self,
        upload_id: str,
        *,
        cancellation_check: Any = None,
        question_limit: int = MAX_QUESTIONS_PER_BATCH,
    ) -> dict[str, Any]:
        """合并全部分块、验证 PDF，并处理首个页面批次。

        自动整本任务会把首批直接扩展到整批上限，后续整本阶段复用这些题目，避免先生成
        5 道预览题、随后又把同一批次的题目全部重生成一次。
        """
        first_batch_question_limit = max(
            1, min(int(question_limit), MAX_FULL_PAPER_QUESTIONS_PER_BATCH),
        )
        job = self.upload_registry.get(upload_id)
        self._check_cancel(cancellation_check)
        log_event("upload.processing.started", upload_id=upload_id, filename=job.get("filename"))
        if job["status"] == "complete" and job.get("result"):
            return job["result"]

        chunk_paths = [
            job["directory"] / f"chunk-{index:06d}.part"
            for index in range(job["totalChunks"])
        ]
        self.upload_registry.update(job, "merging", 22, "正在合并分块并计算文件校验值")
        try:
            source_path, source_fingerprint = self._ensure_source_pdf(
                job, chunk_paths, cancellation_check=cancellation_check,
            )
        except HTTPException as error:
            self.upload_registry.update(job, "failed", 22, str(error.detail))
            raise

        content_import_id = f"pdf-{source_fingerprint[:12]}"
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
        self._check_cancel(cancellation_check)
        processable_page_count = min(page_count, MAX_FULL_PAPER_PAGES)
        batches = []
        for start in range(0, processable_page_count, PDF_BATCH_PAGES):
            end = min(start + PDF_BATCH_PAGES, processable_page_count)
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
            content_hash=source_fingerprint,
        )
        self._check_cancel(cancellation_check)
        self.upload_registry.update(
            job,
            "generating",
            88,
            "首批内容已提取，正在按题号拆分并生成课程",
        )
        asset_dir = job["directory"] / "assets" / first_batch["id"]
        exam_ir = build_exam_ir(
            lesson_source,
            asset_dir=asset_dir,
            batch_id=first_batch["id"],
            start_page=first_batch["startPage"],
            end_page=first_batch["endPage"],
            provider=str(ocr_run.get("provider") or "unknown"),
        )
        question_irs = [
            question.as_dict() for question in exam_ir.questions[:first_batch_question_limit]
        ]
        question_sources = limited_question_sources(
            lesson_source, first_batch_question_limit, asset_dir=asset_dir
        )
        quality_report = build_import_quality_report(
            [{
                "id": first_batch["id"],
                "startPage": first_batch["startPage"],
                "endPage": first_batch["endPage"],
                "source": lesson_source,
                "blocks": split_question_sources(lesson_source, asset_dir=asset_dir),
            }],
            total_pages=page_count,
        )
        quality_report["scope"] = "preview"
        write_staged_prompt_artifact(asset_dir, question_sources)
        ocr_run["sourceArtifactUrl"] = (
            f"/api/uploads/{upload_id}/artifacts/{first_batch['id']}/source.md"
        )
        ocr_run["promptArtifactUrl"] = (
            f"/api/uploads/{upload_id}/artifacts/{first_batch['id']}/model-prompt.md"
        )
        payloads, guide_cards_list, model_runs, review_runs = process_question_sources(
            question_irs or question_sources,
            first_batch,
            ocr_run,
            asset_dir,
            job,
            self.upload_registry.update,
            run_id=None,
        )
        self._check_cancel(cancellation_check)
        payload = payloads[0]
        question_keys = [item["question"]["sourceQuestionKey"] for item in payloads]
        result = {
            "uploadId": upload_id,
            "importId": content_import_id,
            "sourceFingerprint": source_fingerprint,
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
                "questionLimit": first_batch_question_limit,
                "formulaCount": sum(block.count("$") // 2 for _, block, _ in question_sources),
                "guideCardCount": sum(len(cards) for cards in guide_cards_list),
                "pageCount": page_count,
                "processablePageCount": processable_page_count,
                "pageLimit": MAX_FULL_PAPER_PAGES,
                "truncated": page_count > processable_page_count,
                "batchCount": len(batches),
                "confidence": 0.96,
                "mode": f"model-from-{ocr_run['provider']}" if lesson_source else "demo-seed-no-ocr",
            },
            "batches": batches,
            "examIRByBatch": {first_batch["id"]: exam_ir.as_dict()},
            "questionIRsByBatch": {first_batch["id"]: question_irs},
            "qualityReport": quality_report,
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

    def generate_full_paper(
        self,
        upload_id: str,
        *,
        cancellation_check: Any = None,
        max_questions: int | None = None,
    ) -> dict[str, Any]:
        """Process every planned batch after the five-question preview.

        Each batch is an independent safe point. A failed batch is recorded and skipped so
        one bad OCR page cannot hide the questions produced by the remaining pages.
        """
        job = self.upload_registry.get(upload_id)
        result = job.get("result")
        if job.get("status") != "complete" or not result:
            raise HTTPException(status_code=409, detail="请先完成教材首批处理")
        quality_report = self._build_full_paper_quality_report(
            upload_id, job, result, cancellation_check=cancellation_check,
        )
        self._reconcile_batch_question_keys(job, result)
        try:
            requested_limit = int(max_questions or MAX_FULL_PAPER_QUESTIONS)
        except (TypeError, ValueError):
            requested_limit = MAX_FULL_PAPER_QUESTIONS
        limit = max(1, min(requested_limit, MAX_FULL_PAPER_QUESTIONS))
        max_batches = (MAX_FULL_PAPER_PAGES + PDF_BATCH_PAGES - 1) // PDF_BATCH_PAGES
        batches = result.get("batches", [])[:max_batches]
        # A worker retry starts with a fresh report, but it reuses every successfully
        # persisted batch below. This makes a crash between two batches safe to resume
        # without hiding the partial report from the UI.
        summary = {
            "totalBatches": len(batches),
            "processedBatches": 0,
            "succeededBatches": 0,
            "failedBatches": 0,
            "quarantinedQuestions": 0,
            "skippedBatches": 0,
            "questionCount": 0,
            "questionLimit": limit,
            "limitReached": False,
            "batches": [],
            "qualityReport": quality_report,
            "blockedByQualityReport": not quality_report["readyForFullPaper"],
            # 批次熔断：连续命中系统性失败后提前停止时，向 UI 展示具体原因；
            # 正常跑完或只遇到偶发的单题失败时保持 False/None。
            "haltedEarly": False,
            "haltReason": None,
        }
        # Persist intermediate summary so the UI can show progress while the worker runs.
        result["fullPaper"] = summary
        self.upload_registry.update(job, "complete", 100, "整套试卷任务已开始，正在逐批处理")
        if not quality_report["readyForFullPaper"]:
            result["fullPaper"] = summary
            self.upload_registry.update(job, "complete", 100, "导入质量报告未通过，已暂停整本生成")
            payloads = self._ordered_batch_payloads(job, result, limit=limit)
            return {
                "summary": summary,
                "questionPayload": payloads[0] if payloads else None,
                "questionPayloads": payloads,
                "batches": result.get("batches", []),
            }
        # 连续命中次数；任何一次成功或跳过（即"不是系统性失败"）都会重置为 0，
        # 只有连续的系统性失败才会累积到阈值并触发熔断。
        consecutive_systemic_failures = 0

        def _register_batch_failure(error: BaseException) -> None:
            """更新熔断计数；连续命中阈值时把原因记进 ``summary``。

            只更新计数与 ``summary`` 里的 ``haltedEarly``/``haltReason`` 标记，
            不在这里做任何持久化——每个批次结束后统一的落盘逻辑（无论成功、跳过
            还是失败都会执行）负责刷新 ``job``/``result`` 并写入进度，避免这里
            另开一条持久化路径导致两边不同步。
            """
            nonlocal consecutive_systemic_failures
            signal = _systemic_failure_signal(error)
            consecutive_systemic_failures = consecutive_systemic_failures + 1 if signal else 0
            if signal and consecutive_systemic_failures >= SYSTEMIC_FAILURE_HALT_THRESHOLD:
                summary["haltedEarly"] = True
                summary["haltReason"] = signal[1]

        for index, batch_snapshot in enumerate(batches):
            self._check_cancel(cancellation_check)
            # process_batch reloads and persists its own snapshot; refresh it here so a
            # later progress write cannot overwrite newly generated question mappings.
            job = self.upload_registry.get(upload_id)
            result = job.get("result") or result
            self._reconcile_batch_question_keys(job, result)
            batch_id = batch_snapshot["id"]
            batch = next(
                (item for item in result.get("batches", []) if item.get("id") == batch_id),
                batch_snapshot,
            )
            current_payloads = self._ordered_batch_payloads(job, result, limit=limit)
            if len(current_payloads) >= limit:
                summary["limitReached"] = True
                break
            keys = (
                job.setdefault("batchQuestionKeys", {}).get(batch_id)
                or result.setdefault("batchQuestionKeys", {}).get(batch_id, [])
            )
            payload_store = job.setdefault("batchPayloads", {})
            existing = [payload_store.get(key) for key in keys]
            existing = [item for item in existing if item]
            batch_question_limit = min(MAX_FULL_PAPER_QUESTIONS_PER_BATCH, limit)
            # 自动整本上传可能已经在 complete_upload 中生成了首批完整题目。只要这一批
            # 已达到整批上限，就把它视为整本阶段的成功安全点，不再重复调用模型。
            if (
                batch.get("status") == "processed"
                and existing
                and len(existing) >= batch_question_limit
            ):
                consecutive_systemic_failures = 0
                summary["skippedBatches"] += 1
                summary["processedBatches"] += 1
                summary["questionCount"] = len(self._ordered_batch_payloads(job, result, limit=limit))
                batch["fullPaperProcessed"] = True
                summary["batches"].append({
                    "id": batch_id,
                    "status": "skipped",
                    "questionCount": len(existing),
                    "quarantinedQuestions": sum(
                        bool(item.get("qualityRecovery", {}).get("quarantined")) for item in existing
                    ),
                })
                result["fullPaper"] = summary
                self.upload_registry.update(
                    job, "complete", round((index + 1) / max(1, len(batches)) * 100),
                    f"整套试卷已处理 {index + 1}/{len(batches)} 个批次",
                )
                continue
            # 首批快速预览只生成 5 题，不能直接当成“整批已完成”。整卷任务首次经过
            # 一个批次时会复用 OCR 缓存扩展题量；Worker 重试则依靠该标记跳过成功批次。
            if batch.get("fullPaperProcessed") and existing:
                consecutive_systemic_failures = 0
                summary["skippedBatches"] += 1
                summary["processedBatches"] += 1
                quarantined = sum(
                    bool(item.get("qualityRecovery", {}).get("quarantined")) for item in existing
                )
                summary["quarantinedQuestions"] += quarantined
                summary["batches"].append({
                    "id": batch_id, "status": "skipped", "questionCount": len(existing),
                    "quarantinedQuestions": quarantined,
                })
                summary["questionCount"] = len(self._ordered_batch_payloads(job, result, limit=limit))
                result["fullPaper"] = summary
                self.upload_registry.update(
                    job, "complete", round((index + 1) / max(1, len(batches)) * 100),
                    f"整套试卷已处理 {index + 1}/{len(batches)} 个批次",
                )
                continue
            try:
                remaining = limit - len(current_payloads)
                current_batch_keys = set(job.setdefault("batchQuestionKeys", {}).get(batch_id, []))
                prior_payloads = [
                    item for item in current_payloads
                    if item.get("question", {}).get("sourceQuestionKey") not in current_batch_keys
                ]
                generated = self.process_batch(
                    upload_id,
                    batch_id,
                    force=True,
                    persist=True,
                    cancellation_check=cancellation_check,
                    question_limit=min(MAX_FULL_PAPER_QUESTIONS_PER_BATCH, remaining),
                    exclude_question_numbers=self._question_numbers(prior_payloads),
                )
                count = len(generated.get("questionPayloads") or [])
                generated_payloads = generated.get("questionPayloads") or []
                quarantined = sum(
                    bool(item.get("qualityRecovery", {}).get("quarantined"))
                    for item in generated_payloads
                )
                consecutive_systemic_failures = 0
                summary["succeededBatches"] += 1
                summary["processedBatches"] += 1
                summary["quarantinedQuestions"] += quarantined
                batch["fullPaperProcessed"] = True
                summary["batches"].append({
                    "id": batch_id,
                    "status": "succeeded",
                    "questionCount": count,
                    "quarantinedQuestions": quarantined,
                })
            except JobCancelled:
                raise
            except HTTPException as error:
                summary["failedBatches"] += 1
                summary["processedBatches"] += 1
                summary["batches"].append({
                    "id": batch_id, "status": "failed", "error": str(error.detail),
                    "questionCount": 0, "quarantinedQuestions": 0,
                })
                _register_batch_failure(error)
            except Exception as error:
                summary["failedBatches"] += 1
                summary["processedBatches"] += 1
                summary["batches"].append({
                    "id": batch_id, "status": "failed", "error": str(error)[:500],
                    "questionCount": 0, "quarantinedQuestions": 0,
                })
                _register_batch_failure(error)
            job = self.upload_registry.get(upload_id)
            result = job.get("result") or result
            self._reconcile_batch_question_keys(job, result)
            summary["questionCount"] = len(self._ordered_batch_payloads(job, result, limit=limit))
            result["fullPaper"] = summary
            if summary["haltedEarly"]:
                # 熔断命中：仍然走上面同一条落盘路径，只是进度提示换成具体原因；
                # 随后立即退出循环，不再尝试剩余批次。
                self.upload_registry.update(
                    job, "complete", round((index + 1) / max(1, len(batches)) * 100),
                    f"整套试卷检测到连续系统性失败，已暂停剩余批次：{summary['haltReason']}",
                )
                break
            self.upload_registry.update(
                job, "complete", round((index + 1) / max(1, len(batches)) * 100),
                f"整套试卷已处理 {index + 1}/{len(batches)} 个批次",
            )
        payloads = self._ordered_batch_payloads(job, result, limit=limit)
        if payloads:
            result["questionPayload"] = payloads[0]
            result["questionPayloads"] = payloads
            result.setdefault("extraction", {})["questionCount"] = len(payloads)
        summary["questionCount"] = len(payloads)
        result["fullPaper"] = summary
        self.upload_registry.update(job, "complete", 100, f"整套试卷完成，共 {len(payloads)} 道题")
        return {
            "summary": summary,
            "questionPayload": result.get("questionPayload"),
            "questionPayloads": payloads,
            "batches": result.get("batches", []),
        }

    def _build_full_paper_quality_report(
        self,
        upload_id: str,
        job: dict[str, Any],
        result: dict[str, Any],
        *,
        cancellation_check: Any = None,
    ) -> dict[str, Any]:
        """在整本模型调用前读取各批次 OCR，生成一次确定性的质量报告。"""
        # 旧的内存测试夹具和已迁移的轻量快照可能没有目录，无法重新读取 OCR；保留
        # 已持久化的预览报告，避免质量报告本身破坏断点续跑兼容性。
        if "directory" not in job:
            report = dict(result.get("qualityReport") or {
                "status": "ready",
                "readyForFullPaper": True,
                "totalPages": int((result.get("extraction") or {}).get("pageCount") or 0),
                "expectedQuestionCount": len(result.get("questionPayloads") or []),
                "detectedQuestionNumbers": [],
                "questionRange": "—",
                "duplicateQuestionNumbers": [],
                "missingQuestionNumbers": [],
                "unidentifiedPages": [],
                "imageAttributionConflicts": [],
                "imageAttributionAudit": [],
                "warnings": [],
                "blockers": [],
                "checkedBatchCount": len(result.get("batches") or []),
            })
            report["scope"] = "full-paper"
            result["qualityReport"] = report
            return report
        reports: list[dict[str, Any]] = []
        batches = result.get("batches") or []
        for batch in batches:
            self._check_cancel(cancellation_check)
            source, _ocr_run, asset_dir, _question_sources = self._load_batch_sources(
                upload_id=upload_id,
                job=job,
                batch=batch,
                result=result,
                question_limit=MAX_FULL_PAPER_QUESTIONS_PER_BATCH,
            )
            attribution_audit: list[dict[str, Any]] = []
            reports.append({
                "id": batch["id"],
                "startPage": batch["startPage"],
                "endPage": batch["endPage"],
                "source": source,
                "blocks": split_question_sources(
                    source,
                    asset_dir=asset_dir,
                    attribution_audit=attribution_audit,
                ),
                "imageAttributionAudit": attribution_audit,
            })
        report = build_import_quality_report(
            reports,
            total_pages=int((result.get("extraction") or {}).get("pageCount") or 0),
        )
        report["scope"] = "full-paper"
        result["qualityReport"] = report
        self.upload_registry.update(job, "complete", 100, "导入质量报告已生成，准备整本生成")
        return report

    def process_batch(
        self,
        upload_id: str,
        batch_id: str,
        force: bool = False,
        *,
        persist: bool = True,
        refresh_ocr: bool = False,
        run_id: str | None = None,
        cancellation_check: Any = None,
        question_limit: int = MAX_QUESTIONS_PER_BATCH,
        exclude_question_numbers: set[str] | None = None,
    ) -> dict[str, Any]:
        """OCR 一个页范围，并可选择是否立即保存生成练习。

        试卷版本服务使用 ``persist=False``，先为新版分配全新 lesson ID 再保存；这样不会
        静默覆盖已发布版本引用的旧课程文档。
        """
        job = self.upload_registry.get(upload_id)
        self._check_cancel(cancellation_check)
        log_event("upload.batch.started", upload_id=upload_id, batch_id=batch_id, force=force)
        result = job.get("result")
        if job.get("status") != "complete" or not result:
            raise HTTPException(status_code=409, detail="请先完成教材首批处理")
        self._reconcile_batch_question_keys(job, result)

        batch = next(
            (item for item in result.get("batches", []) if item["id"] == batch_id),
            None,
        )
        if not batch:
            raise HTTPException(status_code=404, detail="没有找到这个教材批次")

        # 强制重跑意味着来源/分段规则可能已经变化。清除整卷安全点，避免后续
        # full-paper 任务把这批仍标记为已处理而跳过新 OCR 结果。
        if force:
            batch["fullPaperProcessed"] = False

        batch_question_keys = job.setdefault("batchQuestionKeys", {}).get(batch_id, [])
        stored_payloads = [
            job.setdefault("batchPayloads", {})[key]
            for key in batch_question_keys
            if key in job["batchPayloads"]
        ]
        stored_payload = stored_payloads[0] if stored_payloads else None
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
        operation = "batch_regenerate" if force else "initial_batch"
        own_run = run_id is None
        run = self.audit.start(
            operation,
            "batch",
            upload_id=upload_id,
            config=build_run_config(
                ocr_run={"provider": self.ocr_runtime.selection.provider, "fallback": False},
                operation_details={"force": force, "refreshOcr": refresh_ocr},
            ),
            run_id=run_id,
        ) if own_run else self.store.get_run_snapshot(run_id)
        active_run_id = (run or {}).get("runId") or run_id
        try:
            lesson_source, ocr_run, asset_dir, question_sources = self._load_batch_sources(
                upload_id=upload_id,
                job=job,
                batch=batch,
                result=result,
                refresh_ocr=refresh_ocr,
                question_limit=question_limit,
                exclude_question_numbers=exclude_question_numbers,
            )
            self._check_cancel(cancellation_check)
            payloads, guide_cards_list, model_runs, review_runs = process_question_sources(
                result.get("questionIRsByBatch", {}).get(batch_id) or question_sources,
                batch,
                ocr_run,
                asset_dir,
                job,
                self.upload_registry.update,
                active_run_id,
            )
            self._check_cancel(cancellation_check)
            payload = payloads[0]
            question_keys = [item["question"]["sourceQuestionKey"] for item in payloads]
            response_batch = dict(batch)
            response_batch["status"] = "processed"
            revisions: list[dict[str, Any]] = []
            if persist:
                batch["status"] = "processed"
                revisions = self._persist_lessons(
                    upload_id, payloads, guide_cards_list,
                    run_id=active_run_id,
                    operation=operation,
                    replace_keys=batch_question_keys,
                )
                previous_keys = list(job.setdefault("batchQuestionKeys", {}).get(batch_id, []))
                for old_key in previous_keys:
                    job["batchPayloads"].pop(old_key, None)
                    job.setdefault("batchGuideCards", {}).pop(old_key, None)
                for key, item, cards in zip(question_keys, payloads, guide_cards_list):
                    job["batchPayloads"][key] = item
                    job.setdefault("batchGuideCards", {})[key] = cards
                job.setdefault("batchQuestionKeys", {})[batch_id] = question_keys
                result.setdefault("batchQuestionKeys", {})[batch_id] = question_keys
                result["questionPayloads"] = self._ordered_batch_payloads(job, result)
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
                run_id=active_run_id,
                question_count=len(payloads),
                ocr_provider=ocr_run.get("provider"),
            )
            if own_run and active_run_id:
                run = self.audit.finish(active_run_id, result={
                    "batchId": batch_id,
                    "questionCount": len(payloads),
                    "modelRun": model_runs[0],
                    "reviewRun": review_runs[0],
                    "ocrRun": ocr_run,
                })
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
                "run": run or self.store.get_run_snapshot(active_run_id),
                "revisions": revisions,
            }
        except HTTPException as error:
            if own_run and active_run_id:
                self.audit.fail(active_run_id, error, stage="batch")
            if persist:
                self._record_batch_failure(job, batch, batch_id, str(error.detail), error)
            raise
        except Exception as error:
            if own_run and active_run_id:
                self.audit.fail(active_run_id, error, stage="batch")
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
        operation = "question_reocr" if refresh_ocr else "question_repair"
        run = self.audit.start(
            operation,
            "question",
            upload_id=upload_id,
            question_key=source_question_key,
            config=build_run_config(
                ocr_run={"provider": self.ocr_runtime.selection.provider, "fallback": False},
                operation_details={"refreshOcr": refresh_ocr},
            ),
        )
        run_id = run["runId"]
        try:
            # 必须按整卷上限重新切题源。批次可能是整卷生成产生的（最多
            # MAX_FULL_PAPER_QUESTIONS_PER_BATCH 题），而 _load_batch_sources 默认只切
            # 前 MAX_QUESTIONS_PER_BATCH 题；沿用默认值会让批次里第 6 题之后的题目
            # 永远匹配不到来源，"修复本题" 直接报 "OCR 结果中已找不到这道题"。
            _lesson_source, ocr_run, asset_dir, question_sources = self._load_batch_sources(
                upload_id=upload_id,
                job=job,
                batch=batch,
                result=result,
                refresh_ocr=refresh_ocr,
                question_limit=MAX_FULL_PAPER_QUESTIONS_PER_BATCH,
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
                run_id=run_id,
            )
            # 业务引用使用稳定的来源键，生成题目的内部 id 可以变化；这样只替换当前题，
            # 不会让学生端已有的其它题目、排序和发布快照失效。
            payload["question"]["sourceQuestionKey"] = source_question_key
            revisions = self._persist_lessons(
                upload_id, [payload], [guide_cards], run_id=run_id, operation=operation,
            )
            revision = revisions[0] if revisions else None
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
                run_id=run_id,
                refresh_ocr=refresh_ocr,
                ocr_provider=ocr_run.get("provider"),
            )
            run = self.audit.finish(run_id, result={
                "revisionId": revision["revisionId"] if revision else None,
                "modelRun": model_run,
                "reviewRun": review_run,
                "ocrRun": ocr_run,
            })
            return {
                "batch": batch,
                "questionPayload": payload,
                "guideCards": guide_cards,
                "ocrRun": ocr_run,
                "modelRun": model_run,
                "reviewRun": review_run,
                "regeneration": {"scope": "question", "operation": operation, "refreshOcr": refresh_ocr},
                "run": run,
                "revision": revision,
            }
        except HTTPException as error:
            self.audit.fail(run_id, error, stage="question")
            raise
        except Exception as error:
            self.audit.fail(run_id, error, stage="question")
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

    def review_queue(self, upload_id: str, source_question_key: str) -> dict[str, Any]:
        """返回单题审核所需的来源证据、问题和阶段运行摘要。"""
        job = self.upload_registry.get(upload_id)
        payloads = job.get("batchPayloads") or {
            str(item.get("question", {}).get("sourceQuestionKey")): item
            for item in (job.get("result") or {}).get("questionPayloads", [])
            if isinstance(item, dict) and item.get("question", {}).get("sourceQuestionKey")
        }
        payload = payloads.get(source_question_key)
        if not payload:
            raise HTTPException(status_code=404, detail="没有找到要审核的题目")
        question = payload.get("question", {})
        provenance = question.get("sourceProvenance") or {
            "sourceQuestionKey": source_question_key,
            "sourceOrigin": "legacy-payload",
        }
        issues: list[dict[str, Any]] = []
        quality = payload.get("quality") or {}
        for error in quality.get("errors", []) if isinstance(quality, dict) else []:
            issues.append({"code": "QUALITY_GATE", "message": str(error)})
        for warning in provenance.get("warnings", []) if isinstance(provenance, dict) else []:
            issues.append({"code": str(warning), "message": str(warning)})
        verification = question.get("verification") or {}
        if verification.get("status") != "verified":
            issues.append({"code": "ANSWER_NOT_VERIFIED", "message": "答案尚未通过独立核验"})
        if isinstance(verification.get("conflicts"), list):
            issues.extend({"code": "ANSWER_CONFLICT", "message": str(item)} for item in verification["conflicts"])
        stages = (payload.get("modelRun") or {}).get("stages", [])
        return {
            "uploadId": upload_id,
            "sourceQuestionKey": source_question_key,
            "questionPayload": payload,
            "provenance": provenance,
            "issues": issues,
            "stageRuns": stages if isinstance(stages, list) else [],
            "currentRevisionId": job.get("batchCurrentRevisionIds", {}).get(source_question_key),
        }

    def rerun_stage(
        self,
        upload_id: str,
        source_question_key: str,
        stage: str,
    ) -> dict[str, Any]:
        """只重跑目标阶段和下游阶段；上游从已持久化阶段产物读取。"""
        target_stage = normalize_stage(stage)
        job = self.upload_registry.get(upload_id)
        result = job.get("result")
        old_payload = job.get("batchPayloads", {}).get(source_question_key)
        if job.get("status") != "complete" or not result or not old_payload:
            raise HTTPException(status_code=404, detail="没有找到可重跑的题目")
        batch_id = old_payload.get("question", {}).get("sourceBatchId")
        batch = next((item for item in result.get("batches", []) if item.get("id") == batch_id), None)
        if not batch:
            raise HTTPException(status_code=404, detail="没有找到题目所属批次")
        run = self.audit.start(
            "stage_rerun",
            "question",
            upload_id=upload_id,
            question_key=source_question_key,
            config=build_run_config(operation_details={"stage": target_stage}),
        )
        processing = job.setdefault("processingBatches", set())
        lock_key = f"stage:{source_question_key}:{target_stage}"
        if lock_key in processing:
            raise HTTPException(status_code=409, detail="这个阶段正在重跑")
        processing.add(lock_key)
        try:
            _source, ocr_run, asset_dir, question_sources = self._load_batch_sources(
                upload_id=upload_id,
                job=job,
                batch=batch,
                result=result,
                question_limit=MAX_FULL_PAPER_QUESTIONS_PER_BATCH,
            )
            target = next(
                ((index, number, block, images) for index, (number, block, images) in enumerate(question_sources)
                 if question_key(batch_id, number, index) == source_question_key),
                None,
            )
            if target is None:
                raise HTTPException(status_code=409, detail="OCR 结果中已找不到这道题")
            index, number, block, images = target
            question_ir = next(
                (
                    item
                    for item in result.get("questionIRsByBatch", {}).get(batch_id, [])
                    if isinstance(item, dict)
                    and item.get("sourceQuestionKey") == source_question_key
                ),
                None,
            )
            if question_ir is not None:
                payload, cards, model_run = generate_question_from_ir(
                    question_ir,
                    asset_dir=asset_dir,
                    target_stage=target_stage,
                    prior_stage_artifacts=old_payload.get("stageArtifacts") or {},
                    rerun_token=uuid.uuid4().hex,
                )
            else:
                payload, cards, model_run = generate_lesson(
                    block,
                    asset_dir=asset_dir,
                    target_stage=target_stage,
                    prior_stage_artifacts=old_payload.get("stageArtifacts") or {},
                    rerun_token=uuid.uuid4().hex,
                )
            payload["question"]["sourceQuestionKey"] = source_question_key
            payload["question"]["sourceBatchId"] = batch_id
            _attach_question_ir_provenance(
                payload,
                number=number,
                block=block,
                images=images,
                batch=batch,
                ocr_run=ocr_run,
                asset_dir=asset_dir,
            )
            self._persist_lessons(upload_id, [payload], [cards], run_id=run["runId"], operation="stage_rerun")
            job.setdefault("batchPayloads", {})[source_question_key] = payload
            job.setdefault("batchGuideCards", {})[source_question_key] = cards
            self.upload_registry.update(job, "complete", 100, f"已重跑 {target_stage} 阶段")
            completed = self.audit.finish(run["runId"], result={"stage": target_stage, "modelRun": model_run})
            return {
                "run": completed,
                "batch": batch,
                "questionPayload": payload,
                "guideCards": cards,
                "modelRun": model_run,
                "reviewRun": None,
                "stage": target_stage,
                "stages": model_run.get("stages", []),
                "regeneration": {"scope": "stage", "operation": "stage_rerun", "stage": target_stage},
            }
        except HTTPException as error:
            self.audit.fail(run["runId"], error, stage=target_stage)
            raise
        except Exception as error:
            self.audit.fail(run["runId"], error, stage=target_stage)
            log_event(
                "question.stage_rerun.failed",
                level=40,
                upload_id=upload_id,
                question_key=source_question_key,
                stage=target_stage,
                error_type=type(error).__name__,
                error=str(error)[:300],
                exc_info=True,
            )
            raise HTTPException(status_code=422, detail="阶段重跑失败，请稍后重试") from error
        finally:
            processing.discard(lock_key)

    _EDITABLE_QUESTION_FIELDS = ("prompt", "options", "correctAnswer", "correctAnswers")

    def edit_question(
        self,
        upload_id: str,
        source_question_key: str,
        *,
        base_revision_id: str | None,
        question_patch: dict[str, Any],
        guide_cards_patch: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """人工编辑题目内容字段（题干/选项/标准答案/引导卡文本）。

        只允许改写 ``_EDITABLE_QUESTION_FIELDS`` 里列出的题目字段；来源溯源、模型
        运行记录、答案核验结果等审计字段永远原样保留。写入前必须绑定
        ``base_revision_id`` 做乐观并发校验，并让编辑结果重新过一次与模型生成路径
        完全相同的 ``apply_question_quality_gate``——门禁不通过就整体拒绝，不落盘。
        """
        job = self.upload_registry.get(upload_id)
        result = job.get("result")
        if job.get("status") != "complete" or not result:
            raise HTTPException(status_code=409, detail="请先完成教材首批处理")

        old_payload = job.setdefault("batchPayloads", {}).get(source_question_key)
        if not old_payload:
            raise HTTPException(status_code=404, detail="没有找到要编辑的题目")
        batch_id = old_payload.get("question", {}).get("sourceBatchId")
        batch = next((item for item in result.get("batches", []) if item["id"] == batch_id), None)
        if not batch:
            raise HTTPException(status_code=404, detail="没有找到题目所属批次")

        current_revision_id = job.get("batchCurrentRevisionIds", {}).get(source_question_key)
        if (base_revision_id or None) != (current_revision_id or None):
            old_guide_cards = job.get("batchGuideCards", {}).get(source_question_key) or []
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "题目已被其他修改更新，请基于最新版本重新编辑",
                    "currentRevisionId": current_revision_id,
                    "questionPayload": old_payload,
                    "guideCards": old_guide_cards,
                },
            )

        unknown_fields = sorted(set(question_patch) - set(self._EDITABLE_QUESTION_FIELDS))
        if unknown_fields:
            raise HTTPException(status_code=422, detail=f"不支持编辑这些字段：{', '.join(unknown_fields)}")

        processing = job.setdefault("processingBatches", set())
        lock_key = f"question:{source_question_key}"
        if lock_key in processing:
            raise HTTPException(status_code=409, detail="这个题目正在修复中")
        processing.add(lock_key)
        run = self.audit.start(
            "question_manual_edit",
            "question",
            upload_id=upload_id,
            question_key=source_question_key,
            config=build_run_config(operation_details={"fields": sorted(question_patch)}),
        )
        run_id = run["runId"]
        try:
            # 门禁需要重建 contentBlocks，必须用当次仍然有效的 OCR 来源块，否则无法
            # 区分"人工编辑引入的问题"和"来源本身就有问题"。
            _lesson_source, _ocr_run, _asset_dir, question_sources = self._load_batch_sources(
                upload_id=upload_id,
                job=job,
                batch=batch,
                result=result,
                question_limit=MAX_FULL_PAPER_QUESTIONS_PER_BATCH,
            )
            target = next(
                (
                    (block, images)
                    for index, (number, block, images) in enumerate(question_sources)
                    if question_key(batch_id, number, index) == source_question_key
                ),
                None,
            )
            if target is None:
                raise HTTPException(status_code=409, detail="OCR 结果中已找不到这道题，无法重新校验质量门禁")
            source_block, source_images = target

            payload = copy.deepcopy(old_payload)
            question = payload["question"]
            for field in self._EDITABLE_QUESTION_FIELDS:
                if field in question_patch:
                    question[field] = question_patch[field]
            guide_cards = (
                copy.deepcopy(guide_cards_patch)
                if guide_cards_patch is not None
                else copy.deepcopy(job.get("batchGuideCards", {}).get(source_question_key) or [])
            )

            quality = apply_question_quality_gate(payload, source_block, source_images)
            if quality.get("status") != "ready":
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "编辑后的题目未通过质量门禁，已拒绝保存",
                        "errors": quality.get("errors", []),
                    },
                )

            revisions = self._persist_lessons(
                upload_id, [payload], [guide_cards],
                run_id=run_id, operation="question_manual_edit", revision_source="manual_edit",
            )
            revision = revisions[0] if revisions else None
            job["batchPayloads"][source_question_key] = payload
            job.setdefault("batchGuideCards", {})[source_question_key] = guide_cards
            job.setdefault("batchCurrentRevisionIds", {})[source_question_key] = (
                revision["revisionId"] if revision else current_revision_id
            )
            result["questionPayload"] = payload
            ordered_keys = [
                key
                for current_batch in result.get("batches", [])
                for key in job.setdefault("batchQuestionKeys", {}).get(current_batch["id"], [])
            ]
            result["questionPayloads"] = [
                job["batchPayloads"][key] for key in ordered_keys if key in job["batchPayloads"]
            ]
            self.upload_registry.update(job, "complete", 100, f"已人工编辑题目 {source_question_key}")
            log_event(
                "upload.question.manual_edit.completed",
                upload_id=upload_id,
                source_question_key=source_question_key,
                batch_id=batch_id,
                run_id=run_id,
                fields=sorted(question_patch),
            )
            run = self.audit.finish(run_id, result={
                "revisionId": revision["revisionId"] if revision else None,
            })
            return {
                "run": run,
                "batch": batch,
                "questionPayload": payload,
                "guideCards": guide_cards,
                "edit": {"scope": "question", "operation": "question_manual_edit", "fields": sorted(question_patch)},
                "revision": revision,
            }
        except HTTPException as error:
            self.audit.fail(run_id, error, stage="question")
            raise
        except Exception as error:
            self.audit.fail(run_id, error, stage="question")
            log_event(
                "upload.question.manual_edit.failed",
                level=40,
                upload_id=upload_id,
                source_question_key=source_question_key,
                error_type=type(error).__name__,
                error=str(error)[:300],
                exc_info=True,
            )
            raise HTTPException(status_code=422, detail=f"题目编辑失败：{error}") from error
        finally:
            processing.discard(lock_key)

    def activate_question_revision(
        self,
        upload_id: str,
        source_question_key: str,
        revision_id: str,
    ) -> dict[str, Any]:
        """把题目当前展示版本回滚/指向一条历史 revision，不追加新的 revision。

        这是"审校老师改主意选回更早的版本"的实现：只移动
        ``batch_questions.current_revision_id`` 这个指针并把当前视图换成目标 revision
        的内容，``question_revisions`` 追加写入链不受影响，历史证据永远不会被覆盖。
        """
        job = self.upload_registry.get(upload_id)
        result = job.get("result")
        if job.get("status") != "complete" or not result:
            raise HTTPException(status_code=409, detail="请先完成教材首批处理")
        old_payload = job.setdefault("batchPayloads", {}).get(source_question_key)
        if not old_payload:
            raise HTTPException(status_code=404, detail="没有找到要回滚的题目")

        processing = job.setdefault("processingBatches", set())
        lock_key = f"question:{source_question_key}"
        if lock_key in processing:
            raise HTTPException(status_code=409, detail="这个题目正在修复中")
        processing.add(lock_key)
        run = self.audit.start(
            "question_revision_activate",
            "question",
            upload_id=upload_id,
            question_key=source_question_key,
            config=build_run_config(operation_details={"revisionId": revision_id}),
        )
        run_id = run["runId"]
        try:
            activated = self.store.activate_question_revision(
                upload_id=upload_id,
                source_question_key=source_question_key,
                revision_id=revision_id,
            )
        except LookupError as error:
            self.audit.fail(run_id, error, stage="question")
            processing.discard(lock_key)
            raise HTTPException(status_code=404, detail=str(error)) from error
        except Exception as error:
            self.audit.fail(run_id, error, stage="question")
            processing.discard(lock_key)
            raise
        try:
            payload = activated["payload"]
            guide_cards = activated["guideCards"]
            batch_id = payload.get("question", {}).get("sourceBatchId")
            batch = next((item for item in result.get("batches", []) if item.get("id") == batch_id), None)
            job["batchPayloads"][source_question_key] = payload
            job.setdefault("batchGuideCards", {})[source_question_key] = guide_cards
            job.setdefault("batchCurrentRevisionIds", {})[source_question_key] = activated["revisionId"]
            # lesson_documents 是学生入口读取的独立边界，回滚必须同步覆盖，否则已发布
            # 课程仍会展示回滚之前的内容。这里刻意直接写 lesson，不追加新 revision。
            self.store.save_lesson(lesson_document_from_payload(
                payload, source_upload_id=upload_id, guide_cards=guide_cards,
            ))
            result["questionPayload"] = payload
            ordered_keys = [
                key
                for current_batch in result.get("batches", [])
                for key in job.setdefault("batchQuestionKeys", {}).get(current_batch["id"], [])
            ]
            result["questionPayloads"] = [
                job["batchPayloads"][key] for key in ordered_keys if key in job["batchPayloads"]
            ]
            self.upload_registry.update(
                job, "complete", 100,
                f"已回滚题目 {source_question_key} 到第 {activated['revisionNumber']} 版",
            )
            log_event(
                "upload.question.revision_activated",
                upload_id=upload_id,
                source_question_key=source_question_key,
                revision_id=revision_id,
                revision_number=activated["revisionNumber"],
                run_id=run_id,
            )
            run = self.audit.finish(run_id, result={
                "activatedRevisionId": activated["revisionId"],
                "revisionNumber": activated["revisionNumber"],
            })
            return {
                "run": run,
                "batch": batch,
                "questionPayload": payload,
                "guideCards": guide_cards,
                "activation": {
                    "scope": "question", "operation": "question_revision_activate",
                    "activatedRevisionId": activated["revisionId"],
                },
                "activatedRevision": activated,
            }
        except Exception as error:
            self.audit.fail(run_id, error, stage="question")
            raise HTTPException(status_code=422, detail=f"回滚题目版本失败：{error}") from error
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
