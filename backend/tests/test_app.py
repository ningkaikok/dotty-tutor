from __future__ import annotations

import unittest
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app import (
    HELP_SCHEMA,
    LESSON_SCHEMA,
    HelpRequest,
    apply_question_quality_gate,
    attach_question_source,
    build_question_content_blocks,
    build_reply,
    equation_conflict,
    equivalent_linear_equations,
    generate_lesson,
    generate_model_reply,
    lesson_store,
    limited_question_sources,
    normalize_image_choice_question,
    normalize_model_math_text,
    normalize_stacked_equation_choices,
    normalize_text_choices_from_source,
    runtime,
    select_complete_question_source,
    split_question_sources,
    validate_question_payload,
    write_model_prompt_artifact,
)
from infrastructure.runtime.model_runtime import ModelSelection
from domain.questions.contracts import CANVAS_ACTIONS
from domain.questions.pipeline import strip_choice_text_from_prompt
from infrastructure.runtime.review_runtime import formula_anomaly_score, normalize_ocr_question
from storage import TutorStore


STEPS = [
    {"text": "方程两边同时减去 3。", "speechText": "先做移项。"},
    {"text": "化简得到 2x = 8。", "speechText": "现在得到 2x 等于 8。"},
    {"text": "两边同时除以 2。", "speechText": "求出未知数。"},
    {"text": "最终得到 x = 4。", "speechText": "检查答案。"},
]


class EquationConflictTests(unittest.TestCase):
    def test_detects_same_left_side_with_wrong_result(self) -> None:
        self.assertEqual(equation_conflict("我得到 2x = 14", STEPS), ("2x=14", "2x=8"))

    def test_accepts_matching_intermediate_equation(self) -> None:
        self.assertIsNone(equation_conflict("我得到 2x = 8", STEPS))

    def test_ignores_answer_without_comparable_equation(self) -> None:
        self.assertIsNone(equation_conflict("我不知道下一步", STEPS))

    def test_detects_non_equivalent_step_from_original_question(self) -> None:
        generic_steps = [{"text": "先移项。", "speechText": "继续计算。"}]
        self.assertEqual(
            equation_conflict("我得到 2x = 14", generic_steps, "解方程 2x + 3 = 11"),
            ("2x=14", "2x+3=11"),
        )

    def test_accepts_equivalent_transformation(self) -> None:
        self.assertTrue(equivalent_linear_equations("2x + 3 = 11", "2x = 8"))
        self.assertTrue(equivalent_linear_equations("2x + 3 = 11", "x = 4"))


class TutorResponseTests(unittest.TestCase):
    def test_lesson_schema_includes_priority_question_types(self) -> None:
        self.assertEqual(
            set(LESSON_SCHEMA["properties"]["questionType"]["enum"]),
            {"choice", "multi-select", "true-false", "short-answer", "fill-blank", "numeric", "draw-line"},
        )
        self.assertIn("assessment", HELP_SCHEMA["properties"])

    def test_lesson_schema_requires_every_declared_root_property(self) -> None:
        self.assertEqual(
            set(LESSON_SCHEMA["properties"]),
            set(LESSON_SCHEMA["required"]),
        )

    def test_answer_mode_marks_known_correct_conclusion(self) -> None:
        reply = build_reply(HelpRequest(
            questionId="geometry-perpendicular-bisector",
            studentInput="点 P 在 AB 的垂直平分线上",
            mode="answer",
        ))
        self.assertEqual(reply.source, "answer-check")
        self.assertEqual(reply.guideContext["assessment"], "correct")

    def test_help_mode_advances_one_hint_level(self) -> None:
        reply = build_reply(HelpRequest(
            questionId="geometry-perpendicular-bisector",
            studentInput="我不知道怎么开始",
            mode="help",
        ))
        self.assertEqual(reply.source, "stored-guide-card")
        self.assertEqual(reply.nextHintLevel, 1)

    def test_numeric_answer_uses_tolerance_without_model_call(self) -> None:
        lesson_store["numeric-test"] = {
            "payload": {"question": {
                "id": "numeric-test",
                "questionType": "numeric",
                "knowledgePoint": "近似值",
                "answerSpec": {"answerType": "numeric", "expected": "3.14", "tolerance": 0.01},
            }},
            "guideCards": [],
        }
        try:
            reply = generate_model_reply(HelpRequest(
                questionId="numeric-test",
                studentInput="3.145",
                mode="answer",
                interactionResult={"numericAnswer": "3.145"},
            ))
            self.assertEqual(reply.source, "answer-check")
            self.assertEqual(reply.guideContext["assessment"], "correct")
        finally:
            lesson_store.pop("numeric-test", None)

    def test_fill_blank_checks_each_answer(self) -> None:
        lesson_store["blank-test"] = {
            "payload": {"question": {
                "id": "blank-test",
                "questionType": "fill-blank",
                "knowledgePoint": "基础运算",
                "blanks": [
                    {"id": "a", "answerType": "numeric", "correctAnswers": ["4"]},
                    {"id": "b", "answerType": "text", "correctAnswers": ["平方"]},
                ],
            }},
            "guideCards": [],
        }
        try:
            reply = generate_model_reply(HelpRequest(
                questionId="blank-test",
                mode="answer",
                interactionResult={"blankAnswers": {"a": "4.0", "b": "平方"}},
            ))
            self.assertEqual(reply.guideContext["assessment"], "correct")
        finally:
            lesson_store.pop("blank-test", None)

    def test_multi_select_requires_the_complete_set(self) -> None:
        lesson_store["multi-test"] = {
            "payload": {"question": {
                "id": "multi-test",
                "questionType": "multi-select",
                "knowledgePoint": "集合",
                "correctAnswers": ["(A)", "(C)"],
            }},
            "guideCards": [],
        }
        try:
            reply = generate_model_reply(HelpRequest(
                questionId="multi-test",
                mode="answer",
                interactionResult={"selectedOptions": ["(A)", "(C)"]},
            ))
            self.assertEqual(reply.guideContext["assessment"], "correct")
        finally:
            lesson_store.pop("multi-test", None)


class LessonGenerationTests(unittest.TestCase):
    def test_normalizes_a_successful_real_model_response(self) -> None:
        """Regression test: generate_lesson used CANVAS_ACTIONS without importing it,
        so any real (non-mock) model call that succeeded raised NameError."""
        original_selection = runtime.selection
        runtime.selection = ModelSelection("codex", "default")
        generated = {
            "questionType": "short-answer",
            "prompt": "解方程 2x + 3 = 11",
            "lessonSteps": [{"title": "移项", "text": "两边同时减 3", "speechText": "先移项"}],
        }
        payload = None
        try:
            with patch("app.runtime.generate_json", return_value=(generated, {"provider": "codex", "model": "default", "fallback": False})):
                payload, guide_cards, run = generate_lesson("解方程 2x + 3 = 11")
        finally:
            runtime.selection = original_selection
            if payload:
                lesson_store.pop(payload["question"]["id"], None)

        self.assertEqual(run["provider"], "codex")
        self.assertEqual(
            [step["action"] for step in payload["lessonSteps"]],
            ["show-base"] * 4,
        )
        self.assertEqual(
            [card["canvasAction"] for card in guide_cards],
            ["show-base"] * 3,
        )


class PersistentStoreTests(unittest.TestCase):
    def test_completed_pdf_and_questions_survive_store_recreation(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DOTTY_DATA_DIR": directory}):
            first_store = TutorStore()
            upload_directory = first_store.upload_root / "persisted-upload"
            upload_directory.mkdir()
            (upload_directory / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
            payload = {
                "question": {"id": "persisted-question"},
                "lessonSteps": [],
                "architecture": {},
                "modelRun": {"provider": "mock", "model": "test", "fallback": False},
            }
            result = {
                "importId": "pdf-persisted",
                "filename": "book.pdf",
                "extraction": {"questionCount": 1, "chapter": "测试章节"},
                "questionPayload": payload,
            }
            job = {
                "uploadId": "persisted-upload",
                "filename": "book.pdf",
                "contentType": "application/pdf",
                "size": 18,
                "chunkSize": 1024,
                "totalChunks": 1,
                "sourceText": "",
                "directory": upload_directory,
                "status": "complete",
                "progress": 100,
                "message": "完成",
                "startedAt": 1.0,
                "updatedAt": 2.0,
                "completedAt": 2.0,
                "result": result,
            }
            first_store.save_job(job)
            guide_cards = [{"hint": "持久化提示"}]
            first_store.save_question("persisted-upload", "batch-001", payload, guide_cards)

            restored = TutorStore().load_job("persisted-upload")
            self.assertIsNotNone(restored)
            self.assertEqual(restored["result"]["importId"], "pdf-persisted")
            self.assertEqual(restored["batchPayloads"]["batch-001"]["question"]["id"], "persisted-question")
            self.assertEqual(restored["batchGuideCards"]["batch-001"][0]["hint"], "持久化提示")

    def test_soft_deleted_import_drops_from_library_but_keeps_data(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DOTTY_DATA_DIR": directory}):
            store = TutorStore()
            upload_directory = store.upload_root / "delete-upload"
            upload_directory.mkdir()
            payload = {"question": {"id": "delete-question"}, "lessonSteps": []}
            job = {
                "uploadId": "delete-upload",
                "filename": "book.pdf",
                "contentType": "application/pdf",
                "size": 18,
                "chunkSize": 1024,
                "totalChunks": 1,
                "sourceText": "",
                "directory": upload_directory,
                "status": "complete",
                "progress": 100,
                "message": "完成",
                "startedAt": 1.0,
                "updatedAt": 2.0,
                "completedAt": 2.0,
                "result": {"importId": "pdf-delete", "filename": "book.pdf", "extraction": {"questionCount": 1}, "questionPayload": payload},
            }
            store.save_job(job)
            store.save_question("delete-upload", "batch-001", payload, [])
            self.assertEqual(len(store.list_imports()), 1)

            self.assertTrue(store.soft_delete_import("delete-upload"))
            # Dropped from the library, but the row and questions stay recoverable.
            self.assertEqual(store.list_imports(), [])
            restored = store.load_job("delete-upload")
            self.assertEqual(restored["status"], "deleted")
            self.assertIn("batch-001", restored["batchPayloads"])
            # Deleting an already-deleted import is a no-op.
            self.assertFalse(store.soft_delete_import("delete-upload"))
            self.assertFalse(store.soft_delete_import("missing-upload"))

    def test_find_completed_import_matches_content_hash(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DOTTY_DATA_DIR": directory}):
            store = TutorStore()

            def make_job(upload_id: str, status: str) -> dict:
                updir = store.upload_root / upload_id
                updir.mkdir(parents=True, exist_ok=True)
                return {
                    "uploadId": upload_id, "filename": "same.pdf", "contentType": "application/pdf",
                    "size": 10, "chunkSize": 1024, "totalChunks": 1, "sourceText": "",
                    "directory": updir, "status": status, "progress": 100, "message": "",
                    "startedAt": 1.0, "updatedAt": 2.0, "completedAt": 2.0,
                    "result": {"importId": "pdf-deadbeef1234", "filename": "same.pdf", "extraction": {}, "questionPayload": {}},
                }

            store.save_job(make_job("first-upload", "complete"))
            # A completed import with the same content hash is found (excluding self).
            match = store.find_completed_import("pdf-deadbeef1234", exclude_upload_id="second-upload")
            self.assertEqual(match["uploadId"], "first-upload")
            # It excludes the in-progress upload itself and unknown hashes.
            self.assertIsNone(store.find_completed_import("pdf-deadbeef1234", exclude_upload_id="first-upload"))
            self.assertIsNone(store.find_completed_import("pdf-other00000"))
            # A soft-deleted original no longer blocks re-uploading the same file.
            store.soft_delete_import("first-upload")
            self.assertIsNone(store.find_completed_import("pdf-deadbeef1234"))

    def test_resolves_database_directory_after_project_move(self) -> None:
        with TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            with patch.dict(os.environ, {"DOTTY_DATA_DIR": str(data_root)}):
                store = TutorStore()
                current_upload = data_root / "uploads" / "moved-upload"
                current_upload.mkdir(parents=True)
                legacy_path = Path("/Users/example/legacy/tutor-demo/data/uploads/moved-upload")
                self.assertEqual(store._resolve_directory(str(legacy_path)), current_upload.resolve())

    def test_saves_multiple_questions_in_one_batch_transaction(self) -> None:
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"DOTTY_DATA_DIR": directory}):
            store = TutorStore()
            upload_directory = store.upload_root / "atomic-upload"
            upload_directory.mkdir()
            job = {
                "uploadId": "atomic-upload", "filename": "book.pdf", "contentType": "application/pdf",
                "size": 1, "chunkSize": 1, "totalChunks": 1, "directory": upload_directory,
                "status": "complete", "progress": 100, "message": "完成", "startedAt": 1.0,
                "updatedAt": 1.0, "completedAt": 1.0, "result": {"importId": "atomic"},
            }
            store.save_job(job)
            first = {"question": {"id": "q1"}}
            second = {"question": {"id": "q2"}}
            store.save_questions("atomic-upload", [("batch-q1", first, []), ("batch-q2", second, [])])
            restored = store.load_job("atomic-upload")
            self.assertEqual(set(restored["batchPayloads"]), {"batch-q1", "batch-q2"})


class QuestionExtractionTests(unittest.TestCase):
    def test_content_blocks_preserve_stem_image_before_options(self) -> None:
        payload = {"question": {
            "prompt": r"比较 $a$ 与 $b$ 的大小",
            "options": [r"$a>b$", r"$a=b$", r"$a<b$", "无法确定"],
            "imageUrls": ["/api/uploads/u/assets/batch/q2.jpg"],
        }}
        source = "2.比较大小\n![](images/q2.jpg)\n(A)甲 (B)乙 (C)丙 (D)丁"
        blocks = build_question_content_blocks(payload, source, ["images/q2.jpg"])
        self.assertEqual([block["type"] for block in blocks], ["text", "math", "text", "math", "text", "image", "options"])
        self.assertEqual(blocks[-2]["role"], "stem")

    def test_content_blocks_bind_option_images_inside_options(self) -> None:
        urls = [f"/api/uploads/u/assets/batch/{letter}.jpg" for letter in "abcd"]
        payload = {"question": {
            "prompt": "选择圆柱",
            "options": ["(A)", "(B)", "(C)", "(D)"],
            "imageUrls": urls,
            "optionImageUrls": urls,
        }}
        source_images = [f"images/{letter}.jpg" for letter in "abcd"]
        blocks = build_question_content_blocks(payload, "选择圆柱\n(A)\n(B)\n(C)\n(D)", source_images)
        self.assertEqual([block["type"] for block in blocks], ["text", "options"])
        self.assertEqual([item["imageUrl"] for item in blocks[-1]["items"]], urls)

    def test_quality_gate_rejects_cross_question_image(self) -> None:
        payload = {"question": {
            "prompt": "无图题",
            "options": [],
            "imageUrls": ["/api/uploads/u/assets/batch/wrong.jpg"],
        }}
        apply_question_quality_gate(payload, "3.无图题", [])
        self.assertEqual(payload["quality"]["status"], "needs_review")
        self.assertEqual(payload["question"]["publicationStatus"], "needs_review")
        self.assertTrue(any("图片归属" in error for error in payload["quality"]["errors"]))

    def test_quality_gate_accepts_valid_math_choice_question(self) -> None:
        payload = {"question": {
            "prompt": r"方程 $x+1=2$ 的解是",
            "options": [r"$x=1$", r"$x=2$", r"$x=3$", r"$x=4$"],
            "imageUrls": [],
        }}
        source = r"1.方程 $x+1=2$ 的解是 (A)$x=1$ (B)$x=2$ (C)$x=3$ (D)$x=4$"
        quality = apply_question_quality_gate(payload, source, [])
        self.assertEqual(quality["status"], "ready")
        self.assertEqual(payload["question"]["publicationStatus"], "ready")

    def test_quality_gate_hides_extra_choice_without_publishing_it(self) -> None:
        payload = {
            "question": {
                "questionNumber": "3",
                "prompt": "下列运算正确的是（ ）",
                "options": ["A", "B", "C", "D", "E"],
                "imageUrls": ["images/stem.png", "images/a.png", "images/b.png", "images/c.png", "images/d.png"],
                "optionImageUrls": ["images/a.png", "images/b.png", "images/c.png", "images/d.png", "images/e.png"],
                "givens": [],
            },
            "lessonSteps": [{"title": "", "text": "", "speechText": ""}] * 4,
        }
        quality = apply_question_quality_gate(
            payload,
            "3. 下列运算正确的是（ ）\nA\nimages/a.png\nB\nimages/b.png\nC\nimages/c.png\nD\nimages/d.png",
            payload["question"]["imageUrls"],
        )
        self.assertEqual(payload["question"]["options"], ["A", "B", "C", "D"])
        self.assertEqual(payload["question"]["optionImageUrls"], ["images/a.png", "images/b.png", "images/c.png", "images/d.png"])
        self.assertEqual(quality["status"], "needs_review")
        self.assertTrue(any("多余选项" in error for error in quality["errors"]))

    def test_limits_each_ocr_batch_to_five_questions(self) -> None:
        markdown = "\n".join(f"{number}.这是第{number}道完整测试题。" for number in range(1, 9))
        self.assertEqual(
            [number for number, _, _ in limited_question_sources(markdown)],
            ["1", "2", "3", "4", "5"],
        )

    def test_writes_exact_post_ocr_model_prompt_artifact(self) -> None:
        sources = [("1", "1.测试题。", [])]
        with TemporaryDirectory() as directory:
            path = write_model_prompt_artifact(Path(directory), sources)
            content = path.read_text(encoding="utf-8")
        self.assertIn("MinerU 不使用自然语言提示词", content)
        self.assertIn("1.测试题。", content)
        self.assertIn("imageReferences", content)

    def test_binds_four_source_images_to_abcd_options(self) -> None:
        payload = {
            "question": {
                "prompt": "下列几何体中，是圆柱的为\n\n(A)\n\n(B)\n\n(C)\n\n(D)",
                "options": ["(A)", "(B)", "(C)", "(D)"],
                "imageUrls": [f"/assets/{letter}.jpg" for letter in "abcd"],
            }
        }
        source = "\n".join(
            f"![](images/{letter}.jpg)\n({letter.upper()})" for letter in "abcd"
        )
        normalize_image_choice_question(
            payload,
            source,
            [f"images/{letter}.jpg" for letter in "abcd"],
        )
        self.assertEqual(payload["question"]["optionImageUrls"], payload["question"]["imageUrls"])
        self.assertNotIn("(A)", payload["question"]["prompt"])

    def test_binds_stem_and_four_option_images_without_leaking_paths(self) -> None:
        urls = [f"/api/uploads/u/assets/batch/{name}.jpg" for name in ["stem", "a", "b", "c", "d"]]
        payload = {"question": {
            "prompt": "3. 左视图：images/stem.jpg\nA. images/a.jpg\nB. images/b.jpg\nC. images/c.jpg\nD. images/d.jpg",
            "options": ["images/a.jpg", "images/b.jpg", "images/c.jpg", "images/d.jpg"],
            "imageUrls": urls,
        }}
        source = "3. 左视图\n![](images/stem.jpg)\nA.\n![](images/a.jpg)\nB.\n![](images/b.jpg)\nC.\n![](images/c.jpg)\nD.\n![](images/d.jpg)"
        normalize_image_choice_question(
            payload,
            source,
            [f"images/{name}.jpg" for name in ["stem", "a", "b", "c", "d"]],
        )
        blocks = build_question_content_blocks(
            payload,
            source,
            [f"images/{name}.jpg" for name in ["stem", "a", "b", "c", "d"]],
        )
        self.assertEqual(payload["question"]["optionImageUrls"], urls[1:])
        self.assertNotIn("images/", payload["question"]["prompt"])
        self.assertEqual([block["type"] for block in blocks], ["text", "image", "options"])
        self.assertEqual([item["imageUrl"] for item in blocks[-1]["items"]], urls[1:])

    def test_binds_bare_image_option_labels_in_source_order(self) -> None:
        names = ["stem", "a", "b", "c", "d"]
        urls = [f"/api/uploads/u/assets/batch/{name}.jpg" for name in names]
        source = "3. 观察下图选择正确答案\n" + "\n".join(
            f"{'' if name == 'stem' else chr(64 + index)}\n![](images/{name}.jpg)"
            for index, name in enumerate(names)
        )
        payload = {"question": {
            "prompt": "观察下图选择正确答案",
            "options": ["A", "B", "C", "D"],
            "imageUrls": urls,
        }}
        normalize_image_choice_question(payload, source, [f"images/{name}.jpg" for name in names])
        self.assertEqual(payload["question"]["optionImageUrls"], urls[1:])
        self.assertEqual(
            [item["optionLabel"] for item in payload["question"]["imageManifest"]],
            [None, "A", "B", "C", "D"],
        )
        blocks = build_question_content_blocks(payload, source, [f"images/{name}.jpg" for name in names])
        self.assertEqual([item["imageUrl"] for item in blocks[-1]["items"]], urls[1:])

    def test_splits_all_numbered_questions_before_answers(self) -> None:
        markdown = """
1.第一道选择题，题干和选项都在这里。
(A)甲 (B)乙 (C)丙 (D)丁
2.第二道题，包含一个小问。
(1)求出答案。
3.第三道题。
# 参考答案
1.(A)
2.略
"""
        blocks = split_question_sources(markdown)
        self.assertEqual([number for number, _, _ in blocks], ["1", "2", "3"])
        self.assertIn("(1)求出答案", blocks[1][1])
        self.assertNotIn("参考答案", "\n".join(block for _, block, _ in blocks))

    def test_assigns_rendered_page_image_to_visual_question_without_local_reference(self) -> None:
        markdown = "<!-- page 1 -->\n3.某几何体的左视图如图所示。\nA． B． C． D．\n![](images/rendered-page-0001.png)"
        blocks = split_question_sources(markdown)
        self.assertEqual(blocks[0][2], ["images/rendered-page-0001.png"])

    def test_selects_one_complete_illustrated_question_before_answers(self) -> None:
        markdown = """
上一页残留的(2)小问
18.计算 1+1。
19.如图，证明四边形 ABCD 是菱形。
(1)证明第一步；
(2)求线段 OE。
![](images/geometry.jpg)
20.解方程 x+1=2。
# 参考答案
19.【答案】略。
"""
        number, block, images = select_complete_question_source(markdown)
        self.assertEqual(number, "19")
        self.assertIn("(2)求线段 OE", block)
        self.assertNotIn("20.解方程", block)
        self.assertEqual(images, ["images/geometry.jpg"])

    def test_question_images_follow_markdown_reference_order(self) -> None:
        payload = {"question": {"imageReferences": ["images/a.jpg", "images/b.jpg"]}}
        batch = {"id": "batch-001", "startPage": 1, "endPage": 5}
        ocr_run = {
            "imageUrls": [
                "/api/uploads/u/assets/batch-001/b.jpg",
                "/api/uploads/u/assets/batch-001/a.jpg",
            ]
        }
        attach_question_source(payload, batch, ocr_run)
        self.assertEqual(
            payload["question"]["imageUrls"],
            [
                "/api/uploads/u/assets/batch-001/a.jpg",
                "/api/uploads/u/assets/batch-001/b.jpg",
            ],
        )

    def test_source_image_references_override_model_order(self) -> None:
        payload = {"question": {"imageReferences": ["images/b.jpg", "images/a.jpg"]}}
        batch = {"id": "batch-001", "startPage": 1, "endPage": 5}
        ocr_run = {"imageUrls": ["/api/uploads/u/assets/batch-001/a.jpg", "/api/uploads/u/assets/batch-001/b.jpg"]}
        attach_question_source(payload, batch, ocr_run, ["images/a.jpg", "images/b.jpg"])
        self.assertEqual(
            [url.rsplit("/", 1)[-1] for url in payload["question"]["imageUrls"]],
            ["a.jpg", "b.jpg"],
        )

    def test_question_without_image_reference_does_not_inherit_batch_images(self) -> None:
        payload = {"question": {"imageReferences": []}}
        batch = {"id": "batch-001", "startPage": 1, "endPage": 5}
        ocr_run = {"imageUrls": ["/api/uploads/u/assets/batch-001/q1-a.jpg"]}
        attach_question_source(payload, batch, ocr_run)
        self.assertEqual(payload["question"]["imageUrls"], [])

    def test_explicit_empty_source_images_override_model_references(self) -> None:
        """OCR 的空引用必须阻止模型字段把相邻题目的图片带回来。"""
        payload = {"question": {"imageReferences": ["images/wrong-question.jpg"]}}
        batch = {"id": "batch-001", "startPage": 1, "endPage": 5}
        ocr_run = {"imageUrls": ["/api/uploads/u/assets/batch-001/wrong-question.jpg"]}
        attach_question_source(payload, batch, ocr_run, [])
        self.assertEqual(payload["question"]["imageUrls"], [])
        self.assertNotIn("imageReferences", payload["question"])

    def test_recovers_stacked_equation_solution_choices(self) -> None:
        payload = {"question": {"prompt": "broken", "options": ["(A)", "(B)", "(C)", "(D)"]}}
        source = (
            r"3. 方程式 $\begin{array}{c}x-y=3\\3x-8y=14\end{array}$ 的解为 "
            r"$x=-1$ $x=1$ $x=-2$ $x=2$ (A) (B) (C) (D) y=2 y=-2 y=1 y=-1"
        )
        normalize_stacked_equation_choices(payload, source)
        self.assertEqual(
            payload["question"]["options"],
            [r"$x=-1,\;y=2$", r"$x=1,\;y=-2$", r"$x=-2,\;y=1$", r"$x=2,\;y=-1$"],
        )
        self.assertNotIn("(A)", payload["question"]["prompt"])

    def test_restores_text_choices_when_reviewer_returns_only_labels(self) -> None:
        payload = {"question": {"options": ["A", "B", "C", "D"]}}
        source = (
            r"5. 若一个外角是 $6 0 ^ { \circ }$，内角和为"
            r"(A) $3 6 0 ^ { \circ }$ (B) $5 4 0 ^ { \circ }$ "
            r"(C) $7 2 0 ^ { \circ }$ (D) $9 0 0 ^ { \circ }$"
        )
        normalize_text_choices_from_source(payload, source)
        self.assertIn("360", payload["question"]["options"][0])
        self.assertIn("900", payload["question"]["options"][3])

    def test_restores_dot_labeled_choices_and_removes_them_from_stem(self) -> None:
        payload = {"question": {"options": ["A", "B", "C", "D"]}}
        source = "（3分）下列各数中比1大的数是（ ）A. 2 B. 0 C. 1 D. 3"
        normalize_text_choices_from_source(payload, source)
        self.assertEqual(payload["question"]["options"], ["2", "0", "1", "3"])
        self.assertEqual(
            strip_choice_text_from_prompt(source, payload["question"]["options"]),
            "（3分）下列各数中比1大的数是（ ）",
        )

    def test_normalizes_legacy_percent_and_temperature_latex(self) -> None:
        self.assertEqual(
            normalize_model_math_text(r"$7\textbackslash\text{%}$"),
            r"$7\%$",
        )
        self.assertEqual(
            normalize_model_math_text(r"$-3 \textdegree C$"),
            r"$-3 ^{\circ}\mathrm{C}$",
        )

    def test_quality_gate_rejects_temperature_percent_unit_conflict(self) -> None:
        payload = {"question": {
            "prompt": r"温度由 -4℃ 上升 $7\%$ 是（ ）",
            "options": ["3℃", "-3℃", "11℃", "-11℃"],
            "imageUrls": [],
        }}
        quality = apply_question_quality_gate(
            payload,
            r"1. 温度由 -4℃ 上升 $7\%$ 是（ ）A. 3℃ B. -3℃ C. 11℃ D. -11℃",
            [],
        )
        self.assertEqual(quality["status"], "needs_review")
        self.assertTrue(any("单位语义冲突" in error for error in quality["errors"]))

    def test_splits_concatenated_text_choices_from_reviewer(self) -> None:
        payload = {
            "question": {
                "options": [
                    r"(A) |a| > 4 (B) c-b > 0 (C) ac > 0 (D) a+c > 0"
                ]
            }
        }
        source = r"2. 实数 a、b、c 的位置如图所示，正确的结论是 (A) ... (B) ... (C) ... (D) ..."
        normalize_text_choices_from_source(payload, source)
        self.assertEqual(
            payload["question"]["options"],
            [
                r"(A) |a| > 4",
                r"(B) c-b > 0",
                r"(C) ac > 0",
                r"(D) a+c > 0",
            ],
        )

    def test_repairs_json_control_escape_inside_latex_command(self) -> None:
        self.assertEqual(normalize_model_math_text("$60^\text{°}$"), r"$60^\text{°}$")

    def test_repairs_legacy_textcirc_temperature_formula(self) -> None:
        self.assertEqual(
            normalize_model_math_text(r"$7\textbackslash \textcirc C$"),
            r"$7^{\circ}\mathrm{C}$",
        )


class OcrReviewNormalizationTests(unittest.TestCase):
    def test_normalizes_mineru_formula_wrappers(self) -> None:
        source = (
            r"21.如图，$\mathsf { A B / / D C }$ R $\mathsf { A B } { = } \mathsf { A D }$。"
            "\n\n"
            r"(2)若 ${ \mathsf { A B } } { = } \sqrt { 5 } , ~ { \mathsf { B D } } { = } 2$。"
        )
        normalized = normalize_ocr_question(source)
        self.assertIn(r"$AB \parallel DC$", normalized)
        self.assertIn(r"$AB = \sqrt{5}, BD = 2$", normalized)
        self.assertEqual(formula_anomaly_score(normalized), 0)

    def test_detects_json_escape_control_characters_inside_formula(self) -> None:
        broken = "$AB \nparallel DC$ and $CE \x08ot AB$ and $\\root 5 \x0crom{5}$"
        self.assertGreater(formula_anomaly_score(broken), 0)


if __name__ == "__main__":
    unittest.main()
