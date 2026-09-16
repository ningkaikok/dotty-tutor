"""Focused tests for the reusable textbook PDF application service."""

from __future__ import annotations

import hashlib
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException

from application.services.textbook_processing import (
    TextbookProcessingService,
    _systemic_failure_signal,
)
from domain.questions.source import MAX_QUESTIONS_PER_BATCH
from tests.postgres_test_support import postgres_tests_enabled

_POSTGRES_TEST_SKIP_REASON = "需要 DOTTY_TEST_POSTGRES_ADMIN_URL 指向隔离 PostgreSQL admin 库"


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

    @unittest.skipUnless(postgres_tests_enabled(), _POSTGRES_TEST_SKIP_REASON)
    def test_queued_batch_uses_its_page_range_and_becomes_switchable(self) -> None:
        """A later batch includes the previous page to recover split questions."""
        from routers.textbook_routes import pdf_uploads, processing_service

        with TemporaryDirectory() as directory:
            payload = {
                "question": {"id": "q2"},
                "lessonSteps": [],
                "architecture": {},
                "modelRun": {"provider": "mock", "model": "test", "fallback": False},
            }
            pdf_uploads["test-upload"] = {
                "status": "complete",
                "directory": Path(directory),
                "result": {
                    "ocrRun": {"provider": "mineru"},
                    "sourceFingerprint": "0" * 64,
                    "extraction": {"questionCount": 1},
                    "batches": [
                        {"id": "batch-001", "startPage": 1, "endPage": 5, "status": "processed"},
                        {"id": "batch-002", "startPage": 6, "endPage": 10, "status": "queued"},
                    ],
                },
                "batchPayloads": {},
            }
            try:
                with (
                    patch("routers.textbook_routes.ocr_runtime.should_use_mineru", return_value=True),
                    # Patch where the service resolves the dependency, not at
                    # the HTTP facade that merely delegates the call.
                    patch(
                        "application.services.textbook_processing.resolve_routed_ocr_source",
                        return_value=("page text", {"provider": "mineru"}),
                    ) as resolve,
                    patch(
                        "application.services.question_processing.generate_lesson",
                        return_value=(payload, [], payload["modelRun"]),
                    ),
                    patch(
                        "application.services.question_processing.review_lesson_payload",
                        side_effect=lambda item, _source, _images, _cards: (
                            item,
                            {"provider": "test"},
                        ),
                    ),
                    patch(
                        "application.services.question_processing.apply_question_quality_gate",
                        side_effect=lambda item, _source, _images: (
                            item.update({
                                "quality": {
                                    "status": "ready",
                                    "errors": [],
                                    "warnings": [],
                                    "validatorVersion": "test-v1",
                                },
                            })
                            or item["quality"]
                        ),
                    ),
                    patch("routers.textbook_routes.store.save_questions"),
                    patch("routers.textbook_routes.store.save_lesson"),
                    patch("routers.textbook_routes.store.save_job"),
                    patch("routers.textbook_routes.store.append_revisions_and_save_questions", return_value=[]),
                    patch.object(processing_service.audit, "start", return_value={"runId": "test-run"}),
                    patch.object(processing_service.audit, "finish", return_value={"runId": "test-run", "status": "succeeded"}),
                ):
                    response = processing_service.process_batch("test-upload", "batch-002")
                self.assertEqual(response["questionPayload"]["question"]["id"], "q2")
                self.assertEqual(response["batch"]["status"], "processed")
                self.assertEqual(resolve.call_args.kwargs["start_page"], 4)
                self.assertEqual(resolve.call_args.kwargs["end_page"], 9)
            finally:
                pdf_uploads.pop("test-upload", None)

    @unittest.skipUnless(postgres_tests_enabled(), _POSTGRES_TEST_SKIP_REASON)
    def test_question_regeneration_replaces_only_selected_question(self) -> None:
        """Single-question repair must keep the other questions and stable source key."""
        from routers.textbook_routes import pdf_uploads, processing_service

        with TemporaryDirectory() as directory:
            source_key = "batch-001-q-2"
            first = {
                "question": {
                    "id": "old-1",
                    "sourceBatchId": "batch-001",
                    "sourceQuestionKey": "batch-001-q-1",
                },
                "modelRun": {"provider": "mock", "model": "test"},
            }
            old_target = {
                "question": {
                    "id": "old-2",
                    "sourceBatchId": "batch-001",
                    "sourceQuestionKey": source_key,
                },
                "modelRun": {"provider": "mock", "model": "test"},
            }
            job = {
                "uploadId": "single-question-upload",
                "status": "complete",
                "filename": "source.pdf",
                "directory": Path(directory),
                "batchPayloads": {
                    "batch-001-q-1": first,
                    source_key: old_target,
                },
                "batchGuideCards": {"batch-001-q-1": [], source_key: []},
                "batchQuestionKeys": {"batch-001": ["batch-001-q-1", source_key]},
                "result": {
                    "sourceFingerprint": "0" * 64,
                    "batches": [{"id": "batch-001", "startPage": 1, "endPage": 5, "status": "processed"}],
                    "questionPayloads": [first, old_target],
                    "questionPayload": first,
                },
            }
            pdf_uploads[job["uploadId"]] = job
            new_payload = {
                "question": {
                    "id": "new-2",
                    "sourceBatchId": "batch-001",
                    "sourceQuestionKey": "batch-001-q-2",
                },
                "modelRun": {"provider": "test", "model": "test"},
            }
            try:
                with (
                    patch.object(
                        processing_service,
                        "_load_batch_sources",
                        return_value=("2、题目", {"provider": "mineru"}, Path(directory), [("2", "2、题目", [])]),
                    ),
                    patch(
                        "application.services.textbook_processing._generate_validated_question",
                        return_value=(new_payload, [], new_payload["modelRun"], {"provider": "test"}),
                    ),
                    patch("application.services.textbook_processing.TextbookProcessingService._persist_lessons"),
                    patch("routers.textbook_routes.store.save_job"),
                    patch.object(processing_service.audit, "start", return_value={"runId": "test-run"}),
                    patch.object(processing_service.audit, "finish", return_value={"runId": "test-run", "status": "succeeded"}),
                ):
                    response = processing_service.regenerate_question(job["uploadId"], source_key)
                self.assertEqual(response["questionPayload"]["question"]["id"], "new-2")
                self.assertEqual(response["questionPayload"]["question"]["sourceQuestionKey"], source_key)
                self.assertIs(job["batchPayloads"]["batch-001-q-1"], first)
                self.assertEqual(job["batchQuestionKeys"]["batch-001"], ["batch-001-q-1", source_key])
                self.assertEqual(job["result"]["questionPayloads"], [first, new_payload])
            finally:
                pdf_uploads.pop(job["uploadId"], None)

    @unittest.skipUnless(postgres_tests_enabled(), _POSTGRES_TEST_SKIP_REASON)
    def test_question_regeneration_reaches_questions_beyond_the_preview_limit(self) -> None:
        """整卷批次里第 6 题之后的题目也必须能单独修复。

        _load_batch_sources 默认只切前 MAX_QUESTIONS_PER_BATCH（5）题，而整卷生成
        一个批次最多有 MAX_FULL_PAPER_QUESTIONS_PER_BATCH（20）题。沿用默认值会让
        "修复本题" 对第 6 题之后的题目一律返回 "OCR 结果中已找不到这道题"。
        """
        from routers.textbook_routes import pdf_uploads, processing_service

        with TemporaryDirectory() as directory:
            source_key = "batch-001-q-7"
            old_target = {
                "question": {
                    "id": "old-7",
                    "sourceBatchId": "batch-001",
                    "sourceQuestionKey": source_key,
                },
                "modelRun": {"provider": "mock", "model": "test"},
            }
            job = {
                "uploadId": "beyond-limit-upload",
                "status": "complete",
                "filename": "source.pdf",
                "directory": Path(directory),
                "batchPayloads": {source_key: old_target},
                "batchGuideCards": {source_key: []},
                "batchQuestionKeys": {"batch-001": [source_key]},
                "result": {
                    "sourceFingerprint": "0" * 64,
                    "batches": [{"id": "batch-001", "startPage": 1, "endPage": 5, "status": "processed"}],
                    "questionPayloads": [old_target],
                    "questionPayload": old_target,
                },
            }
            pdf_uploads[job["uploadId"]] = job
            new_payload = {
                "question": {
                    "id": "new-7",
                    "sourceBatchId": "batch-001",
                    "sourceQuestionKey": source_key,
                },
                "modelRun": {"provider": "test", "model": "test"},
            }
            # 模拟真实的截断行为：只返回 question_limit 指定条数的题源。
            all_sources = [(str(number), f"{number}、题目", []) for number in range(1, 20)]

            def load_sources(*, question_limit=MAX_QUESTIONS_PER_BATCH, **_kwargs):
                return ("源文本", {"provider": "mineru"}, Path(directory), all_sources[:question_limit])

            try:
                with (
                    patch.object(processing_service, "_load_batch_sources", side_effect=load_sources),
                    patch(
                        "application.services.textbook_processing._generate_validated_question",
                        return_value=(new_payload, [], new_payload["modelRun"], {"provider": "test"}),
                    ),
                    patch("application.services.textbook_processing.TextbookProcessingService._persist_lessons"),
                    patch("routers.textbook_routes.store.save_job"),
                    patch.object(processing_service.audit, "start", return_value={"runId": "test-run"}),
                    patch.object(processing_service.audit, "finish", return_value={"runId": "test-run", "status": "succeeded"}),
                ):
                    response = processing_service.regenerate_question(job["uploadId"], source_key)
                self.assertEqual(response["questionPayload"]["question"]["id"], "new-7")
            finally:
                pdf_uploads.pop(job["uploadId"], None)


if __name__ == "__main__":
    unittest.main()
