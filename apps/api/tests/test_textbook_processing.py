"""Focused tests for the reusable textbook PDF application service."""

from __future__ import annotations

import hashlib
import subprocess
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from fastapi import HTTPException
from pypdf import PdfWriter

from application.services.textbook_processing import (
    TextbookProcessingService,
    _systemic_failure_signal,
)
from domain.questions.contracts import GUIDE_CARDS
from infrastructure.files.upload_registry import UploadRegistry
from infrastructure.runtime.model_runtime import runtime as model_runtime
from infrastructure.runtime.ocr_runtime import runtime as ocr_runtime
from infrastructure.runtime.review_runtime import runtime_reviewer
from persistence.app_store import AppStore
from tests.postgres_test_support import PostgresTestCase


class SystemicFailureSignalTests(unittest.TestCase):
    """针对批次熔断分类器的独立单测，不依赖整卷生成的其它状态。"""

    def test_detects_rate_limit_from_status_code(self) -> None:
        signal = _systemic_failure_signal(HTTPException(status_code=429, detail="太多请求"))
        self.assertIsNotNone(signal)
        category, reason = signal
        self.assertEqual(category, "rate_limit")
        self.assertIn("429", reason)

    def test_detects_quota_or_auth_from_wrapped_cause(self) -> None:
        """provider 适配层常把原始异常包成 RuntimeError，只有 __cause__ 带状态码。"""
        cause = HTTPException(status_code=401, detail="API key 已过期")
        wrapped = RuntimeError("模型调用失败：认证失败")
        wrapped.__cause__ = cause

        signal = _systemic_failure_signal(wrapped)

        self.assertIsNotNone(signal)
        self.assertEqual(signal[0], "quota_or_auth")

    def test_detects_upstream_timeout_from_exception_type(self) -> None:
        error = subprocess.TimeoutExpired(cmd="codex", timeout=240)
        signal = _systemic_failure_signal(error)
        self.assertIsNotNone(signal)
        self.assertEqual(signal[0], "upstream_timeout")

    def test_ordinary_validation_failure_is_not_systemic(self) -> None:
        """题号切分失败等业务失败不应被误判为系统性失败。"""
        signal = _systemic_failure_signal(HTTPException(status_code=422, detail="题号切分失败"))
        self.assertIsNone(signal)

    def test_question_or_page_numbers_do_not_trigger_auth_or_rate_limit_breakers(self) -> None:
        """解析异常里的 401/403/429 只是题号/页码，不能当成上游认证失败或限流。"""
        for message in (
            "第 401 题解析失败：无法识别题干",
            "第 403 页没有文字层",
            "page 429 could not be parsed",
            "题号 401 的选项切分失败",
        ):
            with self.subTest(message=message):
                self.assertIsNone(_systemic_failure_signal(RuntimeError(message)))

    def test_explicit_http_wording_still_classifies_the_upstream_failure(self) -> None:
        """Provider 把状态码写进文案时仍要识别，否则真正的限流/认证错误不会熔断。"""
        rate_limited = _systemic_failure_signal(RuntimeError("provider returned HTTP 429: too many requests"))
        self.assertIsNotNone(rate_limited)
        self.assertEqual(rate_limited[0], "rate_limit")

        forbidden = _systemic_failure_signal(RuntimeError("HTTP 403 Forbidden"))
        self.assertIsNotNone(forbidden)
        self.assertEqual(forbidden[0], "quota_or_auth")

        unauthorized = _systemic_failure_signal(RuntimeError("status_code=401"))
        self.assertIsNotNone(unauthorized)
        self.assertEqual(unauthorized[0], "quota_or_auth")


class TextbookProcessingTests(unittest.TestCase):
    def test_retry_reuses_merged_source_after_chunks_are_cleaned(self) -> None:
        """Worker retries resume from source.pdf instead of requiring upload chunks again."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            content = b"%PDF-1.4\n1 0 obj\n%%EOF\n"
            source_path = root / "source.pdf"
            source_path.write_bytes(content)
            job = {"uploadId": "retry-upload", "size": len(content), "directory": root}
            service = TextbookProcessingService(
                store=object(), upload_registry=object(), ocr_runtime=object(),
            )

            restored_path, fingerprint = service._ensure_source_pdf(
                job, [root / "chunk-000000.part"],
            )

            self.assertEqual(restored_path, source_path)
            self.assertEqual(fingerprint, hashlib.sha256(content).hexdigest())

    def test_mis_segmented_exam_paper_fails_instead_of_becoming_one_question(self) -> None:
        """题号切分失败时，整卷必须在来源边界报错，而不是回退成一道题。

        单元测试只能证明判据函数本身正确；这条覆盖的是接线：防护确实被
        ``_load_batch_sources`` 调用，并且在回退分支之前生效。
        """
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
            job = {"uploadId": "bad-ocr", "directory": root}
            batch = {"id": "batch-001", "startPage": 1, "endPage": 5}
            # 题号后面跟的是全角冒号，切分器不认；正文又长又带章节标题，
            # 因此判据成立，应当报错而不是把整页当成一道题。
            broken_source = "## 一、选择题\n1：第一题。\n2：第二题。\n" + ("补充 OCR 文本。" * 200)
            service = TextbookProcessingService(
                store=object(), upload_registry=object(), ocr_runtime=object(),
            )

            with patch(
                "application.services.textbook_processing.resolve_routed_ocr_source",
                return_value=(broken_source, {}),
            ):
                with self.assertRaises(HTTPException) as raised:
                    service._load_batch_sources(
                        upload_id="bad-ocr",
                        job=job,
                        batch=batch,
                        result={"sourceFingerprint": "deadbeef"},
                    )

            self.assertEqual(raised.exception.status_code, 422)
            self.assertIn("题号切分失败", raised.exception.detail)

    def test_full_paper_summary_is_bounded_and_resumes_processed_batches(self) -> None:
        """A retry skips persisted successes while recording later batch failures."""
        payload_one = {"question": {"id": "q1", "sourceQuestionKey": "batch-001-q-1"}}
        payload_two = {"question": {"id": "q2", "sourceQuestionKey": "batch-002-q-1"}}
        job = {
            "uploadId": "full-paper-upload",
            "status": "complete",
            "result": {
                "batches": [
                    {"id": "batch-001", "status": "processed", "fullPaperProcessed": True},
                    {"id": "batch-002", "status": "queued"},
                    {"id": "batch-003", "status": "queued"},
                ],
                "batchQuestionKeys": {"batch-001": ["batch-001-q-1"]},
                "questionPayloads": [payload_one],
                "questionPayload": payload_one,
            },
            "batchQuestionKeys": {"batch-001": ["batch-001-q-1"]},
            "batchPayloads": {"batch-001-q-1": payload_one},
        }

        class Registry:
            def get(self, _upload_id):
                return job

            def update(self, current, status, progress, message):
                current["status"] = status
                current["progress"] = progress
                current["message"] = message

        service = TextbookProcessingService(store=object(), upload_registry=Registry(), ocr_runtime=object())
        def process_batch(_upload_id, batch_id, **_kwargs):
            if batch_id == "batch-002":
                job["batchPayloads"]["batch-002-q-1"] = payload_two
                job["batchQuestionKeys"]["batch-002"] = ["batch-002-q-1"]
                return {"questionPayloads": [payload_two]}
            raise HTTPException(status_code=422, detail="OCR 失败")

        with patch.object(service, "process_batch", side_effect=process_batch):
            result = service.generate_full_paper("full-paper-upload", max_questions=100)

        summary = result["summary"]
        self.assertEqual(summary["totalBatches"], 3)
        self.assertEqual(summary["processedBatches"], 3)
        self.assertEqual(summary["succeededBatches"], 1)
        self.assertEqual(summary["failedBatches"], 1)
        self.assertEqual(summary["skippedBatches"], 1)
        self.assertEqual(summary["quarantinedQuestions"], 0)
        self.assertEqual(summary["questionCount"], 2)
        # batch-001 被跳过（已持久化）、batch-002 成功、batch-003 失败：这三个状态
        # 已经完整证明恰好两个批次真正触发了处理，不需要再断言 mock 调用次数。
        self.assertEqual([item["status"] for item in summary["batches"]], ["skipped", "succeeded", "failed"])

    def test_full_paper_halts_after_consecutive_rate_limit_failures(self) -> None:
        """连续命中 429 限流达到阈值后应暂停剩余批次，而不是逐题磨到失败。

        前两个批次成功，接下来三个批次连续命中 429（达到阈值 3），第六个
        批次本应成功——但绝不能被处理到，用来证明熔断确实提前退出了循环，
        不是只是恰好把所有批次都跑完后失败次数变多。
        """
        job = {
            "uploadId": "rate-limited-upload",
            "status": "complete",
            "result": {
                "batches": [{"id": f"batch-{index:03d}", "status": "queued"} for index in range(1, 7)],
                "batchQuestionKeys": {},
                "questionPayloads": [],
            },
            "batchQuestionKeys": {},
            "batchPayloads": {},
        }

        class Registry:
            def get(self, _upload_id):
                return job

            def update(self, current, status, progress, message):
                current.update(status=status, progress=progress, message=message)

        service = TextbookProcessingService(store=object(), upload_registry=Registry(), ocr_runtime=object())
        attempted_batches: list[str] = []

        def process_batch(_upload_id, batch_id, **_kwargs):
            attempted_batches.append(batch_id)
            if batch_id in {"batch-001", "batch-002"}:
                key = f"{batch_id}-q-1"
                generated = {"question": {"id": key, "sourceQuestionKey": key}}
                job["batchPayloads"][key] = generated
                job["batchQuestionKeys"][batch_id] = [key]
                return {"questionPayloads": [generated]}
            if batch_id in {"batch-003", "batch-004", "batch-005"}:
                raise HTTPException(status_code=429, detail="rate_limit: 429 Too Many Requests")
            raise AssertionError(f"{batch_id} 不应被处理——熔断应该已经提前停止循环")

        with patch.object(service, "process_batch", side_effect=process_batch):
            result = service.generate_full_paper("rate-limited-upload", max_questions=100)

        summary = result["summary"]
        # 只尝试到第三次连续 429 命中阈值为止；batch-006 绝不能出现在尝试列表里。
        self.assertEqual(attempted_batches, ["batch-001", "batch-002", "batch-003", "batch-004", "batch-005"])
        self.assertTrue(summary["haltedEarly"])
        self.assertIn("rate_limit", summary["haltReason"])
        self.assertEqual(summary["succeededBatches"], 2)
        self.assertEqual(summary["failedBatches"], 3)
        self.assertEqual(summary["processedBatches"], 5)
        # 已经成功的两个批次的题目不受影响，仍然按部分成功语义保留在结果里。
        self.assertEqual(summary["questionCount"], 2)
        self.assertEqual(
            [item["status"] for item in summary["batches"]],
            ["succeeded", "succeeded", "failed", "failed", "failed"],
        )

    def test_full_paper_does_not_halt_on_non_systemic_failures(self) -> None:
        """普通的单题失败（比如 422 题号切分失败）应继续逐题记录，不触发熔断。"""
        job = {
            "uploadId": "ordinary-failure-upload",
            "status": "complete",
            "result": {
                "batches": [{"id": f"batch-{index:03d}", "status": "queued"} for index in range(1, 4)],
                "batchQuestionKeys": {},
                "questionPayloads": [],
            },
            "batchQuestionKeys": {},
            "batchPayloads": {},
        }

        class Registry:
            def get(self, _upload_id):
                return job

            def update(self, current, status, progress, message):
                current.update(status=status, progress=progress, message=message)

        service = TextbookProcessingService(store=object(), upload_registry=Registry(), ocr_runtime=object())

        def process_batch(_upload_id, batch_id, **_kwargs):
            raise HTTPException(status_code=422, detail="题号切分失败")

        with patch.object(service, "process_batch", side_effect=process_batch):
            result = service.generate_full_paper("ordinary-failure-upload", max_questions=100)

        summary = result["summary"]
        self.assertFalse(summary["haltedEarly"])
        self.assertIsNone(summary["haltReason"])
        self.assertEqual(summary["failedBatches"], 3)
        self.assertEqual(summary["processedBatches"], 3)

    def test_full_paper_stops_model_work_at_question_limit(self) -> None:
        """The question cap must stop later model calls, not merely trim the response."""
        job = {
            "status": "complete",
            "result": {
                "batches": [
                    {"id": "batch-001", "status": "queued"},
                    {"id": "batch-002", "status": "queued"},
                ],
                "batchQuestionKeys": {},
                "questionPayloads": [],
            },
            "batchQuestionKeys": {},
            "batchPayloads": {},
        }

        class Registry:
            def get(self, _upload_id):
                return job

            def update(self, current, status, progress, message):
                current.update(status=status, progress=progress, message=message)

        service = TextbookProcessingService(store=object(), upload_registry=Registry(), ocr_runtime=object())

        def process_batch(_upload_id, batch_id, **kwargs):
            self.assertEqual(kwargs["question_limit"], 1)
            key = f"{batch_id}-q-1"
            generated = {"question": {"id": "q1", "sourceQuestionKey": key}}
            job["batchPayloads"][key] = generated
            job["batchQuestionKeys"][batch_id] = [key]
            return {"questionPayloads": [generated]}

        with patch.object(service, "process_batch", side_effect=process_batch):
            result = service.generate_full_paper("u1", max_questions=1)

        # 达到题量上限后循环在进入 batch-002 之前就退出，所以 batches 摘要里只会
        # 出现 batch-001 一条记录；如果 batch-002 也被处理过（哪怕结果被截断），
        # 这里就会出现第二条记录，从而暴露"只是裁剪结果而不是真的停止模型调用"。
        self.assertEqual([item["id"] for item in result["summary"]["batches"]], ["batch-001"])
        self.assertEqual(result["summary"]["questionCount"], 1)
        self.assertTrue(result["summary"]["limitReached"])

    def test_full_paper_stops_before_model_when_quality_report_is_blocked(self) -> None:
        job = {
            "status": "complete",
            "result": {
                "batches": [{"id": "batch-001", "status": "queued"}],
                "batchQuestionKeys": {},
                "questionPayloads": [],
            },
            "batchQuestionKeys": {},
            "batchPayloads": {},
        }

        class Registry:
            def get(self, _upload_id):
                return job

            def update(self, current, status, progress, message):
                current.update(status=status, progress=progress, message=message)

        service = TextbookProcessingService(store=object(), upload_registry=Registry(), ocr_runtime=object())
        blocked = {
            "status": "blocked",
            "readyForFullPaper": False,
            "expectedQuestionCount": 1,
            "questionRange": "2",
            "duplicateQuestionNumbers": [],
            "unidentifiedPages": [],
            "imageAttributionConflicts": [],
            "warnings": [],
            "blockers": ["题号过少"],
            "detectedQuestionNumbers": ["2"],
            "missingQuestionNumbers": [],
            "totalPages": 5,
            "checkedBatchCount": 1,
        }
        with (
            patch.object(service, "_build_full_paper_quality_report", return_value=blocked),
            patch.object(service, "process_batch") as process_batch,
        ):
            result = service.generate_full_paper("quality-blocked-upload", max_questions=100)
        process_batch.assert_not_called()
        self.assertTrue(result["summary"]["blockedByQualityReport"])
        self.assertEqual(result["summary"]["qualityReport"]["status"], "blocked")

    def test_full_paper_reuses_an_already_complete_first_batch(self) -> None:
        """Automatic whole-book uploads must not regenerate their full preview batch."""
        payloads = [
            {"question": {"id": f"q-{index}", "sourceQuestionKey": f"batch-001-q-{index}"}}
            for index in range(1, 21)
        ]
        keys = [item["question"]["sourceQuestionKey"] for item in payloads]
        job = {
            "uploadId": "full-paper-reuse",
            "status": "complete",
            "result": {
                "batches": [{"id": "batch-001", "status": "processed"}],
                "batchQuestionKeys": {"batch-001": keys},
                "questionPayloads": payloads,
                "questionPayload": payloads[0],
            },
            "batchQuestionKeys": {"batch-001": keys},
            "batchPayloads": dict(zip(keys, payloads)),
        }

        class Registry:
            def get(self, _upload_id):
                return job

            def update(self, current, status, progress, message):
                current.update(status=status, progress=progress, message=message)

        service = TextbookProcessingService(store=object(), upload_registry=Registry(), ocr_runtime=object())
        with patch.object(service, "process_batch"):
            result = service.generate_full_paper("full-paper-reuse", max_questions=100)

        self.assertEqual(result["summary"]["skippedBatches"], 1)
        self.assertEqual(result["summary"]["batches"][0]["status"], "skipped")
        self.assertEqual(result["summary"]["questionCount"], 20)

    def test_batch_payload_order_uses_source_order_not_lexical_key_order(self) -> None:
        payload_two = {"question": {"id": "q2"}}
        payload_ten = {"question": {"id": "q10"}}
        job = {
            "batchPayloads": {"batch-001-q-10": payload_ten, "batch-001-q-2": payload_two},
            "batchQuestionKeys": {"batch-001": ["batch-001-q-2", "batch-001-q-10"]},
        }
        result = {"batches": [{"id": "batch-001"}], "batchQuestionKeys": {}}

        ordered = TextbookProcessingService._ordered_batch_payloads(job, result)

        self.assertEqual([item["question"]["id"] for item in ordered], ["q2", "q10"])

    def test_reconciles_question_keys_missing_from_an_older_preview_snapshot(self) -> None:
        payload_one = {"question": {"id": "q1", "sourceQuestionKey": "batch-001-q-1"}}
        payload_two = {"question": {"id": "q2", "sourceQuestionKey": "batch-001-q-2"}}
        job = {
            "batchPayloads": {
                "batch-001-q-1": payload_one,
                "batch-001-q-2": payload_two,
            },
            "batchQuestionKeys": {"batch-001": ["batch-001-q-1"]},
        }
        result = {
            "batches": [{"id": "batch-001"}],
            "batchQuestionKeys": {"batch-001": ["batch-001-q-1"]},
        }

        TextbookProcessingService._reconcile_batch_question_keys(job, result)
        ordered = TextbookProcessingService._ordered_batch_payloads(job, result)

        self.assertEqual([item["question"]["id"] for item in ordered], ["q1", "q2"])


class TextbookProcessingPostgresAcceptanceTests(PostgresTestCase):
    """Behavioral acceptance for upload processing against isolated PostgreSQL."""

    def setUp(self) -> None:
        super().setUp()
        self.store = AppStore(database_url=self.database_url, data_root=self.data_root)
        self.addCleanup(self.store.close)
        self.lesson_cache: dict[str, dict[str, Any]] = {}
        self.registry = UploadRegistry(
            store=self.store,
            lesson_store=self.lesson_cache,
            default_guide_cards=GUIDE_CARDS,
            pdf_tail_check_bytes=64 * 1024,
        )
        self.service = TextbookProcessingService(
            store=self.store, upload_registry=self.registry, ocr_runtime=ocr_runtime,
        )

    @staticmethod
    def _payload(question_id: str, source_key: str, batch_id: str) -> dict[str, Any]:
        return {
            "question": {
                "id": question_id, "sourceBatchId": batch_id, "sourceQuestionKey": source_key,
                "prompt": f"Question for {source_key}", "chapter": "Acceptance chapter",
                "knowledgePoint": "Source-backed question", "questionType": "short-answer",
                "givens": [], "options": [], "answer": "review required", "imageUrls": [],
            },
            "lessonSteps": [], "architecture": {},
            "modelRun": {"provider": "acceptance-fixture", "model": "stored-v1"},
        }

    def _save_upload(
        self,
        upload_id: str,
        batches: list[dict[str, Any]],
        existing_payloads: list[dict[str, Any]] | None = None,
    ) -> None:
        directory = self.data_root / upload_id
        directory.mkdir(parents=True)
        writer = PdfWriter()
        page_count = max(int(batch["endPage"]) for batch in batches)
        for _ in range(page_count):
            writer.add_blank_page(width=200, height=300)
        source_path = directory / "source.pdf"
        writer.write(source_path)
        payloads = existing_payloads or []
        batch_question_keys: dict[str, list[str]] = {}
        for item in payloads:
            question = item["question"]
            source_key = str(question["sourceQuestionKey"])
            batch_question_keys.setdefault(str(question["sourceBatchId"]), []).append(source_key)
        now = time.time()
        job = {
            "uploadId": upload_id, "importId": f"acceptance-{upload_id}",
            "filename": "acceptance-paper.pdf", "contentType": "application/pdf",
            "size": source_path.stat().st_size, "chunkSize": source_path.stat().st_size,
            "totalChunks": 1, "directory": directory, "sourceText": "",
            "status": "complete", "progress": 100, "message": "Ready for batch processing",
            "startedAt": now, "updatedAt": now, "completedAt": now,
            "result": {
                "importId": f"acceptance-{upload_id}", "sourceFingerprint": hashlib.sha256(
                    source_path.read_bytes(),
                ).hexdigest(),
                "ocrRun": {"provider": "acceptance-ocr", "fallback": False},
                "extraction": {"pageCount": page_count, "questionCount": len(payloads)},
                "batches": batches, "batchQuestionKeys": batch_question_keys,
                "questionPayloads": payloads,
                "questionPayload": payloads[0] if payloads else None,
            },
        }
        self.store.save_job(job)
        for payload in payloads:
            question = payload["question"]
            self.store.save_question(
                upload_id, str(question["sourceQuestionKey"]), payload, [],
            )

    def test_user_processes_a_queued_batch_using_its_saved_page_range(self) -> None:
        # Given a completed upload with a persisted queued batch covering physical pages 6–10
        upload_id = "queued-range-acceptance"
        self._save_upload(upload_id, [
            {"id": "batch-001", "startPage": 1, "endPage": 5, "status": "processed"},
            {"id": "batch-002", "startPage": 6, "endPage": 10, "status": "queued"},
        ])
        observed_ranges: list[tuple[int, int]] = []

        def resolve_fixture(**kwargs: Any):
            observed_ranges.append((int(kwargs["start_page"]), int(kwargs["end_page"])))
            return "2、Compute 2 + 2.", {"provider": "acceptance-ocr", "fallback": False}

        # When the teacher processes the queued batch, OCR receives the saved range and the result persists.
        with (
            model_runtime.use_selection("mock", "source-preserving-fixture"),
            runtime_reviewer.use_selection("mock", "fixture-reviewer"),
            patch(
                "application.services.textbook_processing.resolve_routed_ocr_source",
                side_effect=resolve_fixture,
            ),
        ):
            response = self.service.process_batch(upload_id, "batch-002")

        self.assertEqual(observed_ranges, [(4, 9)])
        self.assertEqual(response["batch"]["status"], "processed")
        self.assertEqual(response["questionPayload"]["question"]["questionNumber"], "2")
        persisted = self.registry.get(upload_id)
        self.assertEqual(persisted["result"]["batches"][1]["status"], "processed")
        self.assertEqual(persisted["result"]["questionPayloads"][0]["question"]["sourceBatchId"], "batch-002")

    def test_user_repairs_one_question_without_replacing_its_batch_neighbors(self) -> None:
        # Given two persisted questions in one OCR batch and a teacher repairing only question 2
        upload_id = "single-question-repair-acceptance"
        first_key, target_key = "batch-001-q-1", "batch-001-q-2"
        first = self._payload("old-question-1", first_key, "batch-001")
        target = self._payload("old-question-2", target_key, "batch-001")
        self._save_upload(upload_id, [
            {"id": "batch-001", "startPage": 1, "endPage": 5, "status": "processed"},
        ], [first, target])

        def resolve_fixture(**_kwargs: Any):
            return "1、First question.\n2、Second question to repair.", {
                "provider": "acceptance-ocr", "fallback": False,
            }

        # When the target is regenerated, its stable source key stays the same and its neighbor keeps its saved payload.
        with (
            model_runtime.use_selection("mock", "source-preserving-fixture"),
            runtime_reviewer.use_selection("mock", "fixture-reviewer"),
            patch(
                "application.services.textbook_processing.resolve_routed_ocr_source",
                side_effect=resolve_fixture,
            ),
        ):
            response = self.service.regenerate_question(upload_id, target_key)

        self.assertEqual(response["questionPayload"]["question"]["sourceQuestionKey"], target_key)
        self.assertNotEqual(response["questionPayload"]["question"]["id"], "old-question-2")
        persisted = self.registry.get(upload_id)
        self.assertEqual(persisted["batchPayloads"][first_key]["question"]["id"], "old-question-1")
        self.assertEqual(persisted["batchPayloads"][target_key]["question"]["sourceQuestionKey"], target_key)
        self.assertNotEqual(persisted["batchPayloads"][target_key]["question"]["id"], "old-question-2")

    def test_user_can_repair_a_question_beyond_the_five_question_preview(self) -> None:
        # Given a persisted seventh question in a batch whose normal preview contains only five
        upload_id = "beyond-preview-acceptance"
        target_key = "batch-001-q-7"
        target = self._payload("old-question-7", target_key, "batch-001")
        self._save_upload(upload_id, [
            {"id": "batch-001", "startPage": 1, "endPage": 5, "status": "processed"},
        ], [target])
        source_text = "\n".join(f"{index}、Question {index} on the page." for index in range(1, 20))

        def resolve_fixture(**_kwargs: Any):
            return source_text, {"provider": "acceptance-ocr", "fallback": False}

        # When the teacher repairs question 7, OCR and question splitting use the full repair limit.
        with (
            model_runtime.use_selection("mock", "source-preserving-fixture"),
            runtime_reviewer.use_selection("mock", "fixture-reviewer"),
            patch(
                "application.services.textbook_processing.resolve_routed_ocr_source",
                side_effect=resolve_fixture,
            ),
        ):
            response = self.service.regenerate_question(upload_id, target_key)

        self.assertEqual(response["questionPayload"]["question"]["sourceQuestionKey"], target_key)
        self.assertNotEqual(response["questionPayload"]["question"]["id"], "old-question-7")
        persisted = self.registry.get(upload_id)
        self.assertEqual(persisted["batchPayloads"][target_key]["question"]["sourceQuestionKey"], target_key)


if __name__ == "__main__":
    unittest.main()
