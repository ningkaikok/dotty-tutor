from __future__ import annotations

import copy
import unittest

from publication_revision import PublicationRevisionService


def question_payload(question_id: str = "question-1") -> dict:
    return {
        "question": {
            "id": question_id,
            "chapter": "有理数",
            "knowledgePoint": "数轴",
            "prompt": "下列数中最大的是（ ）",
            "options": ["2", "0", "1", "-3"],
            "sourceBatchId": "batch-001",
            "sourceQuestionKey": "batch-001-q-1",
            "questionNumber": "1",
            "publicationStatus": "ready",
        },
        "lessonSteps": [],
        "modelRun": {"provider": "codex", "model": "gpt-5.6-sol"},
        "quality": {"status": "ready"},
    }


class PublicationRevisionServiceTests(unittest.TestCase):
    def test_creates_new_lesson_ids_and_preserves_old_publication(self) -> None:
        original_payload = question_payload()

        class Store:
            def __init__(self) -> None:
                self.saved: list[dict] = []
                self.created: dict | None = None

            def load_publication(self, publication_id: str) -> dict:
                self.assert_publication_id = publication_id
                return {
                    "publicationId": "paper-1",
                    "title": "第一单元 · 互动试卷",
                    "sourceUploadId": "upload-1",
                    "status": "published",
                    "version": 1,
                    "lessonIds": ["question-1"],
                    "lessons": [{
                        "lessonId": "question-1",
                        "questionPayload": original_payload,
                    }],
                }

            def save_lesson(self, document: dict) -> dict:
                self.saved.append(copy.deepcopy(document))
                return document

            def create_publication(self, **values: object) -> dict:
                self.created = dict(values)
                return {
                    "publicationId": values["publication_id"],
                    "title": values["title"],
                    "sourceUploadId": values["source_upload_id"],
                    "status": values["status"],
                    "version": values["version"],
                    "revisionOf": values["revision_of"],
                    "lessonIds": values["lesson_ids"],
                    "lessons": self.saved,
                }

        class Processor:
            def __init__(self) -> None:
                self.calls: list[tuple] = []

            def process_batch(self, *args: object, **kwargs: object) -> dict:
                self.calls.append((args, kwargs))
                return {
                    "questionPayloads": [question_payload("generated-temporary")],
                    "guideCards": [[{"hint": "先比较正负"}]],
                }

        store = Store()
        processor = Processor()
        result = PublicationRevisionService(
            store=store,
            processing_service=processor,
        ).create("paper-1")

        self.assertEqual(processor.calls[0][0], ("upload-1", "batch-001", True))
        self.assertEqual(processor.calls[0][1], {"persist": False})
        self.assertEqual(original_payload["question"]["id"], "question-1")
        self.assertEqual(store.saved[0]["version"], 2)
        self.assertNotEqual(store.saved[0]["lessonId"], "question-1")
        self.assertEqual(store.created["revision_of"], "paper-1")
        self.assertEqual(result["publication"]["version"], 2)
        self.assertEqual(
            result["questionPayloads"][0]["question"]["revisionOf"],
            "question-1",
        )

    def test_requires_source_batch_before_regeneration(self) -> None:
        class Store:
            def load_publication(self, _publication_id: str) -> dict:
                payload = question_payload()
                payload["question"].pop("sourceBatchId")
                return {
                    "sourceUploadId": "upload-1",
                    "version": 1,
                    "lessons": [{"lessonId": "question-1", "questionPayload": payload}],
                }

        service = PublicationRevisionService(store=Store(), processing_service=object())
        with self.assertRaisesRegex(ValueError, "重新上传原 PDF"):
            service.create("paper-1")


if __name__ == "__main__":
    unittest.main()
