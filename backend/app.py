"""Dotty Tutor ASGI composition root.

Run this module with ``uvicorn app:app``. Business logic belongs in route,
service, runtime, and store modules; this file only wires those boundaries.
Selected symbols are re-exported for backwards-compatible tests and scripts.
"""

from application import create_app
from learning_routes import build_learning_router
from lesson_generation import (
    attach_question_source,
    generate_lesson,
    generate_model_reply,
    lesson_store,
    question_payload,
)
from mistake_recognition import build_mistake_recognizer
from mistake_routes import build_mistake_router
from mistake_store import MistakeStore
from model_runtime import runtime
from question_contracts import HELP_SCHEMA, LESSON_SCHEMA, HelpRequest
from question_pipeline import (
    apply_question_quality_gate,
    build_question_content_blocks,
    normalize_image_choice_question,
    normalize_model_math_text,
    normalize_stacked_equation_choices,
    normalize_text_choices_from_source,
    validate_question_payload,
    write_model_prompt_artifact,
)
from question_source import (
    limited_question_sources,
    select_complete_question_source,
    split_question_sources,
)
from runtime_routes import build_runtime_router
from storage import store
from textbook_ocr import resolve_ocr_text
from textbook_routes import pdf_uploads, process_pdf_batch, router as textbook_router
from tutor_checks import build_reply, equation_conflict, equivalent_linear_equations


app = create_app()

# Cross-cutting runtime and learning APIs share the main TutorStore.
app.include_router(build_runtime_router(store=store, question_payload=question_payload))
app.include_router(build_learning_router(store=store))
app.include_router(textbook_router)

# The mistake domain shares infrastructure but keeps its own table and routes.
mistake_store = MistakeStore(engine=store.engine, data_root=store.root)
mistake_recognizer = build_mistake_recognizer(
    resolve_ocr_text=resolve_ocr_text,
    generate_lesson=generate_lesson,
    build_content_blocks=build_question_content_blocks,
)
app.include_router(build_mistake_router(store=mistake_store, recognize=mistake_recognizer))
