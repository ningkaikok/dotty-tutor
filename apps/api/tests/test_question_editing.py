"""覆盖题目人工编辑/回滚的乐观并发、质量门禁接线和回滚效果。

``TextbookProcessingService`` 的 ``_load_batch_sources`` 按现有测试文件的先例（见
``tests/test_textbook_processing.py`` 里同一个方法的 mock 用法）替换为假实现——它
读的是 OCR Runtime/磁盘缓存，属于外部边界。``apply_question_quality_gate`` 是纯函数
（题目载荷 + 来源文本 → 门禁结果），不 mock，直接调真实实现：门禁到底判 ready 还是
needs_review 是这些测试要验证的行为本身，mock 掉它只会验证"mock 返回了我写的值"这种
空话，还可能让断言的错误文案和真实门禁文案脱节（本文件早期版本正是这样：mock 编的
"题干混入答案证据"其实和真实门禁输出的"题干混入答案/解析证据"不是同一句）。
"""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi import HTTPException

from application.services.textbook_processing import TextbookProcessingService


def _question_payload(source_key: str, *, prompt: str) -> dict[str, Any]:
    return {
        "question": {
            "id": f"question-{source_key}",
            "sourceQuestionKey": source_key,
            "sourceBatchId": "batch-001",
            "prompt": prompt,
            "options": ["A. 1", "B. 2", "C. 3", "D. 4"],
            "correctAnswer": "A",
            "correctAnswers": [],
            "questionType": "choice",
            "imageUrls": [],
            "optionImageUrls": [],
            "questionNumber": "1",
            "chapter": "章节",
            "knowledgePoint": "知识点",
        },
        "quality": {"status": "ready", "errors": []},
        "modelRun": {},
    }


class _FakeTextbookStore:
    """只实现人工编辑/回滚路径依赖的方法；行为对齐 ``TextbookStore`` 的真实语义。"""

    def __init__(self) -> None:
        self.lessons: dict[str, dict[str, Any]] = {}
        self.revisions: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.append_calls = 0

    def save_lesson(self, document: dict[str, Any]) -> None:
        self.lessons[document["lessonId"]] = document

    def create_run_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        record = dict(snapshot)
        self.runs[record["runId"]] = record
        return record

    def finish_run_snapshot(self, run_id: str, *, status: str, result: Any = None, error: Any = None) -> dict[str, Any]:
        record = self.runs[run_id]
        record["status"] = status
        record["result"] = result
        record["error"] = error
        return record

    def get_run_snapshot(self, run_id: str) -> dict[str, Any] | None:
        return self.runs.get(run_id)

    def append_revisions_and_save_questions(
        self,
        *,
        upload_id: str,
        questions: list[tuple[str, dict[str, Any], list[dict[str, Any]]]],
        operation: str,
        run_id: str,
        replace_keys: list[str] | None = None,
        revision_source: str = "model_generated",
    ) -> list[dict[str, Any]]:
        self.append_calls += 1
        results = []
        for source_question_key, payload, guide_cards in questions:
            chain = self.revisions.setdefault((upload_id, source_question_key), [])
            number = len(chain) + 1
            revision_id = f"rev-{source_question_key}-{number}"
            record = {
                "revisionId": revision_id, "uploadId": upload_id, "sourceQuestionKey": source_question_key,
                "revisionNumber": number, "operation": operation,
                "previousRevisionId": chain[-1]["revisionId"] if chain else None,
                "runId": run_id, "createdAt": float(number), "revisionSource": revision_source,
                "payload": payload, "guideCards": guide_cards,
            }
            chain.append(record)
            results.append({key: value for key, value in record.items() if key not in {"payload", "guideCards"}})
        return results

    def activate_question_revision(
        self, *, upload_id: str, source_question_key: str, revision_id: str,
    ) -> dict[str, Any]:
        chain = self.revisions.get((upload_id, source_question_key), [])
        target = next((item for item in chain if item["revisionId"] == revision_id), None)
        if not target:
            raise LookupError("这条历史修订不存在，或不属于这道题")
        return dict(target)


class _FakeUploadRegistry:
    def __init__(self, job: dict[str, Any]) -> None:
        self.job = job

    def get(self, _upload_id: str) -> dict[str, Any]:
        return self.job

    def update(self, job: dict[str, Any], status: str, progress: int, message: str) -> None:
        job["status"] = status
        job["progress"] = progress
        job["message"] = message


def _build_service_and_job(*, prompt: str = "原始题干") -> tuple[TextbookProcessingService, dict[str, Any], _FakeTextbookStore]:
    source_key = "batch-001-q-1"
    payload = _question_payload(source_key, prompt=prompt)
    job: dict[str, Any] = {
        "uploadId": "upload-1",
        "status": "complete",
        "result": {
            "batches": [{"id": "batch-001", "startPage": 1, "endPage": 5, "status": "processed"}],
            "questionPayloads": [payload],
            "batchQuestionKeys": {"batch-001": [source_key]},
        },
        "batchPayloads": {source_key: payload},
        "batchGuideCards": {source_key: []},
        "batchQuestionKeys": {"batch-001": [source_key]},
        "batchCurrentRevisionIds": {source_key: None},
        "processingBatches": set(),
    }
    store = _FakeTextbookStore()
    service = TextbookProcessingService(store=store, upload_registry=_FakeUploadRegistry(job), ocr_runtime=object())
    return service, job, store


_SOURCE_KEY = "batch-001-q-1"


def _patched_load_batch_sources(service: TextbookProcessingService):
    return patch.object(
        service,
        "_load_batch_sources",
        return_value=("来源原文", {}, Path("."), [("1", "来源原文", [])]),
    )


class QuestionEditOptimisticConcurrencyTests(unittest.TestCase):
    def test_stale_base_revision_is_rejected_with_409_and_current_snapshot(self) -> None:
        """先成功编辑一次产生 revision 1，再用过期的 baseRevisionId 重试必须被拒绝。"""
        service, job, store = _build_service_and_job()
        with _patched_load_batch_sources(service):
            first = service.edit_question(
                "upload-1", _SOURCE_KEY,
                base_revision_id=None,
                question_patch={"prompt": "第一次编辑"},
                guide_cards_patch=None,
            )
            stale_revision_id = None  # 客户端仍拿着编辑前的旧版本号（None）
            with self.assertRaises(HTTPException) as raised:
                service.edit_question(
                    "upload-1", _SOURCE_KEY,
                    base_revision_id=stale_revision_id,
                    question_patch={"prompt": "基于过期版本的第二次编辑"},
                    guide_cards_patch=None,
                )
        self.assertEqual(raised.exception.status_code, 409)
        detail = raised.exception.detail
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail["currentRevisionId"], first["revision"]["revisionId"])
        self.assertEqual(detail["questionPayload"]["question"]["prompt"], "第一次编辑")
        # 冲突请求必须整体拒绝，不能留下第二条 revision 或修改锁。
        self.assertEqual(store.append_calls, 1)
        self.assertEqual(job["processingBatches"], set())

    def test_matching_base_revision_succeeds(self) -> None:
        service, job, store = _build_service_and_job()
        with _patched_load_batch_sources(service):
            first = service.edit_question(
                "upload-1", _SOURCE_KEY,
                base_revision_id=None,
                question_patch={"prompt": "第一次编辑"},
                guide_cards_patch=None,
            )
            second = service.edit_question(
                "upload-1", _SOURCE_KEY,
                base_revision_id=first["revision"]["revisionId"],
                question_patch={"correctAnswer": "B"},
                guide_cards_patch=None,
            )
        self.assertEqual(second["revision"]["revisionNumber"], 2)
        self.assertEqual(second["revision"]["revisionSource"], "manual_edit")
        self.assertEqual(second["questionPayload"]["question"]["correctAnswer"], "B")
        self.assertEqual(job["batchCurrentRevisionIds"][_SOURCE_KEY], second["revision"]["revisionId"])
        self.assertEqual(store.append_calls, 2)


class QuestionEditQualityGateTests(unittest.TestCase):
    def test_quality_gate_rejection_blocks_the_edit(self) -> None:
        """门禁不通过必须拒绝这次编辑，且绝不写入任何新 revision。

        用真实门禁能拒绝的输入（题干开头混入"答案"字样，触发
        ``domain.questions.pipeline.ANSWER_LEAK_PATTERN`` 的答案泄漏检查），而不是
        mock 一个编出来的错误信息——这样断言的错误文案和线上真实会返回的一致。
        """
        service, job, store = _build_service_and_job()
        with _patched_load_batch_sources(service):
            with self.assertRaises(HTTPException) as raised:
                service.edit_question(
                    "upload-1", _SOURCE_KEY,
                    base_revision_id=None,
                    question_patch={"prompt": "答案是A，原始题干"},
                    guide_cards_patch=None,
                )
        self.assertEqual(raised.exception.status_code, 422)
        self.assertTrue(
            any("答案" in error and "证据" in error for error in raised.exception.detail["errors"]),
            raised.exception.detail["errors"],
        )
        self.assertEqual(store.append_calls, 0)
        # 题目当前视图必须保持编辑前的原样，不能被半途写入的候选内容污染。
        self.assertEqual(job["batchPayloads"][_SOURCE_KEY]["question"]["prompt"], "原始题干")
        self.assertEqual(job["processingBatches"], set())

    def test_unsupported_field_is_rejected(self) -> None:
        """禁止通过这个入口改写来源溯源等审计字段。"""
        service, job, _store = _build_service_and_job()
        with self.assertRaises(HTTPException) as raised:
            service.edit_question(
                "upload-1", _SOURCE_KEY,
                base_revision_id=None,
                question_patch={"sourceProvenance": {"sourceOrigin": "hacked"}},
                guide_cards_patch=None,
            )
        self.assertEqual(raised.exception.status_code, 422)
        self.assertNotIn("sourceProvenance", job["batchPayloads"][_SOURCE_KEY]["question"])


class QuestionRevisionActivateTests(unittest.TestCase):
    def test_activate_rolls_back_current_view_without_new_revision(self) -> None:
        """回滚到历史版本只移动指针，不追加新 revision，也会同步已发布课程文档。"""
        service, job, store = _build_service_and_job()
        with _patched_load_batch_sources(service):
            first = service.edit_question(
                "upload-1", _SOURCE_KEY,
                base_revision_id=None,
                question_patch={"prompt": "第一次编辑"},
                guide_cards_patch=None,
            )
            service.edit_question(
                "upload-1", _SOURCE_KEY,
                base_revision_id=first["revision"]["revisionId"],
                question_patch={"prompt": "第二次编辑"},
                guide_cards_patch=None,
            )
        self.assertEqual(job["batchPayloads"][_SOURCE_KEY]["question"]["prompt"], "第二次编辑")
        revision_count_before_rollback = store.append_calls

        activated = service.activate_question_revision(
            "upload-1", _SOURCE_KEY, first["revision"]["revisionId"],
        )

        self.assertEqual(activated["questionPayload"]["question"]["prompt"], "第一次编辑")
        self.assertEqual(job["batchPayloads"][_SOURCE_KEY]["question"]["prompt"], "第一次编辑")
        self.assertEqual(job["batchCurrentRevisionIds"][_SOURCE_KEY], first["revision"]["revisionId"])
        # 回滚绝不追加新的 revision：append 调用次数必须保持不变。
        self.assertEqual(store.append_calls, revision_count_before_rollback)
        # 已发布课程文档也要同步回滚后的内容，否则学生端仍会看到回滚前的题目。
        lesson = store.lessons[f"question-{_SOURCE_KEY}"]
        self.assertEqual(lesson["questionPayload"]["question"]["prompt"], "第一次编辑")

    def test_activate_unknown_revision_returns_404(self) -> None:
        service, _job, _store = _build_service_and_job()
        with self.assertRaises(HTTPException) as raised:
            service.activate_question_revision("upload-1", _SOURCE_KEY, "rev-does-not-exist")
        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
