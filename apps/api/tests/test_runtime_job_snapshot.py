from __future__ import annotations

import unittest

from infrastructure.runtime.job_snapshot import use_job_runtime_snapshot
from infrastructure.runtime.model_runtime import runtime as model_runtime
from infrastructure.runtime.ocr_runtime import runtime as ocr_runtime
from infrastructure.runtime.review_runtime import runtime_reviewer


class RuntimeJobSnapshotTests(unittest.TestCase):
    def test_queued_selection_is_task_local_and_restored_after_failure(self) -> None:
        model_before = model_runtime.selection
        ocr_before = ocr_runtime.selection
        review_before = (runtime_reviewer.text_provider, runtime_reviewer.text_model)
        payload = {
            "runtimeSnapshot": {
                "version": 1,
                "generation": {"provider": "mock", "model": "static-demo"},
                "ocr": {"provider": "pypdf"},
                "review": {"provider": "mock", "model": "static-demo"},
            },
        }
        with self.assertRaisesRegex(RuntimeError, "simulated task failure"):
            with use_job_runtime_snapshot(payload):
                self.assertEqual((model_runtime.selection.provider, model_runtime.selection.model), ("mock", "static-demo"))
                self.assertEqual(ocr_runtime.selection.provider, "pypdf")
                self.assertEqual((runtime_reviewer.text_provider, runtime_reviewer.text_model), ("mock", "static-demo"))
                raise RuntimeError("simulated task failure")
        self.assertIs(model_runtime.selection, model_before)
        self.assertIs(ocr_runtime.selection, ocr_before)
        self.assertEqual((runtime_reviewer.text_provider, runtime_reviewer.text_model), review_before)


if __name__ == "__main__":
    unittest.main()
