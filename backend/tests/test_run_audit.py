from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from domain.contracts.audit import BatchProcessResponse
from persistence.textbook_store import TextbookStore
from infrastructure.runtime.model_runtime import ModelSelection, runtime as model_runtime
from infrastructure.runtime.ocr_runtime import runtime as ocr_runtime
from infrastructure.runtime.review_runtime import runtime_reviewer
from run_audit import RunAudit, build_run_config


class RunAuditStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.store = TextbookStore(
            database_url=f"sqlite+pysqlite:///{self.directory.name}/audit.sqlite3",
            data_root=self.directory.name,
        )
        # question_revisions intentionally references an upload row.
        self.store.save_job({
            "uploadId": "upload-audit", "filename": "book.pdf", "contentType": "application/pdf",
            "size": 1, "chunkSize": 1, "totalChunks": 1, "sourceText": "", "directory": self.store.upload_root,
            "status": "complete", "progress": 100, "message": "done", "startedAt": 1.0,
            "updatedAt": 1.0, "completedAt": 1.0, "result": {"uploadId": "upload-audit"},
        })

    def tearDown(self) -> None:
        self.store.close()
        self.directory.cleanup()

    def test_run_config_is_frozen_and_can_finish_only_once(self) -> None:
        run = RunAudit(self.store).start(
            "question_repair", "question", upload_id="upload-audit",
            question_key="q-1", config={"model": {"provider": "mock"}, "promptVersion": "p1"},
        )
        done = RunAudit(self.store).finish(run["runId"], result={"revisionId": "r1"})
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(done["config"]["promptVersion"], "p1")
        with self.assertRaises(ValueError):
            self.store.finish_run_snapshot(run["runId"], status="failed", error={"message": "late"})

    def test_batch_contract_accepts_nested_guide_card_groups(self) -> None:
        response = BatchProcessResponse.model_validate({
            "run": None,
            "batch": {"id": "batch-001"},
            "guideCards": [[{"id": "guide-1", "title": "第一题"}]],
        })
        self.assertEqual(response.guideCards[0][0]["id"], "guide-1")

    def test_revisions_are_append_only_and_chain_previous(self) -> None:
        audit = RunAudit(self.store)
        run = audit.start("question_repair", "question", upload_id="upload-audit", question_key="q-1")
        first = self.store.append_question_revision(
            upload_id="upload-audit", source_question_key="q-1", operation="question_repair",
            payload={"question": {"prompt": "old"}}, guide_cards=[], run_id=run["runId"],
        )
        second = self.store.append_question_revision(
            upload_id="upload-audit", source_question_key="q-1", operation="question_reocr",
            payload={"question": {"prompt": "new"}}, guide_cards=[], run_id=run["runId"],
        )
        revisions = self.store.list_question_revisions("upload-audit", "q-1")
        self.assertEqual([item["revisionNumber"] for item in revisions], [1, 2])
        self.assertEqual(second["previousRevisionId"], first["revisionId"])
        self.assertEqual(revisions[0]["payload"]["question"]["prompt"], "old")

    def test_failed_run_keeps_failure_evidence(self) -> None:
        audit = RunAudit(self.store)
        run = audit.start("batch_regenerate", "batch", upload_id="upload-audit")
        failed = audit.fail(run["runId"], ValueError("provider unavailable"), stage="ocr")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"]["stage"], "ocr")

    def test_snapshot_uses_live_runtime_selections_and_operation_flags(self) -> None:
        old_model = model_runtime.selection
        old_ocr = ocr_runtime.selection.provider
        old_review = (runtime_reviewer.text_provider, runtime_reviewer.text_model)
        try:
            model_runtime.selection = ModelSelection("mock", "static-demo")
            ocr_runtime.selection.provider = "pypdf"
            runtime_reviewer.text_provider = "codex"
            runtime_reviewer.text_model = "default"
            config = build_run_config(operation_details={"force": True, "refreshOcr": True})
            self.assertEqual(config["model"]["provider"], "mock")
            self.assertEqual(config["model"]["model"], "static-demo")
            self.assertEqual(config["ocr"]["provider"], "pypdf")
            self.assertEqual(config["review"]["text"]["provider"], "codex")
            self.assertEqual(config["operation"], {"force": True, "refreshOcr": True})
            self.assertEqual(config["validatorVersion"], "p0-v3")
        finally:
            model_runtime.selection = old_model
            ocr_runtime.selection.provider = old_ocr
            runtime_reviewer.text_provider, runtime_reviewer.text_model = old_review

    def test_atomic_revision_and_current_question_write_roll_back_together(self) -> None:
        run = RunAudit(self.store).start("question_repair", "question", upload_id="upload-audit", question_key="q-atomic")
        self.store.append_revision_and_save_question(
            upload_id="upload-audit", source_question_key="q-atomic", operation="question_repair",
            payload={"question": {"id": "old-question"}}, guide_cards=[], run_id=run["runId"],
        )
        original_upsert = self.store._upsert
        upsert_count = 0

        def fail_second_upsert(*args: object, **kwargs: object) -> None:
            nonlocal upsert_count
            upsert_count += 1
            if upsert_count == 2:
                raise RuntimeError("fixture write failure")
            original_upsert(*args, **kwargs)

        with patch.object(self.store, "_upsert", side_effect=fail_second_upsert):
            with self.assertRaises(RuntimeError):
                self.store.append_revisions_and_save_questions(
                    upload_id="upload-audit",
                    questions=[
                        ("q-atomic", {"question": {"id": "new-question"}}, []),
                        ("q-atomic-2", {"question": {"id": "second-question"}}, []),
                    ],
                    operation="question_repair",
                    run_id=run["runId"],
                )
        revisions = self.store.list_question_revisions("upload-audit", "q-atomic")
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0]["payload"]["question"]["id"], "old-question")
        self.assertEqual(self.store.load_job("upload-audit")["batchPayloads"]["q-atomic"]["question"]["id"], "old-question")
        self.assertEqual(self.store.list_question_revisions("upload-audit", "q-atomic-2"), [])

    def test_all_audited_operations_keep_target_identity(self) -> None:
        audit = RunAudit(self.store)
        targets = (
            ("question_repair", "question", {"question_key": "q-repair"}),
            ("question_reocr", "question", {"question_key": "q-reocr"}),
            ("batch_regenerate", "batch", {}),
            ("publication_rereview", "publication", {"publication_id": "paper-1"}),
        )
        for operation, scope, target in targets:
            run = audit.start(
                operation,
                scope,
                upload_id="upload-audit",
                config=build_run_config(operation_details={"force": operation != "question_repair", "refreshOcr": operation == "question_reocr"}),
                **target,
            )
            self.assertEqual(run["operation"], operation)
            self.assertEqual(run["scope"], scope)
            self.assertEqual(run["targetUploadId"], "upload-audit")
            self.assertEqual(run["targetQuestionKey"], target.get("question_key"))
            self.assertEqual(run["targetPublicationId"], target.get("publication_id"))
            self.assertEqual(run["config"]["validatorVersion"], "p0-v3")
            audit.fail(run["runId"], "fixture failure", stage="test")


if __name__ == "__main__":
    unittest.main()
