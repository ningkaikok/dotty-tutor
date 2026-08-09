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
from publication_routes import build_publication_router
from practice_routes import build_practice_router
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
from review_routes import build_review_router
from review_store import ReviewStore
from runtime_routes import build_runtime_router
from storage import store
from stateful_tutor import StatefulTutor
from textbook_ocr import resolve_ocr_text
from textbook_routes import pdf_uploads, process_pdf_batch, router as textbook_router
from tutor_checks import build_reply, equation_conflict, equivalent_linear_equations
from tutoring_routes import build_tutoring_router
from tutoring_store import TutoringStore
from variation_service import VariationService
from variation_store import VariationStore


app = create_app()

# Cross-cutting runtime and learning APIs share the main TutorStore.
app.include_router(build_runtime_router(store=store, question_payload=question_payload))
app.include_router(build_learning_router(store=store))
app.include_router(build_publication_router(store=store))
app.include_router(textbook_router)

# The mistake domain shares infrastructure but keeps its own table and routes.
mistake_store = MistakeStore(engine=store.engine, data_root=store.root)
mistake_recognizer = build_mistake_recognizer(
    resolve_ocr_text=resolve_ocr_text,
    generate_lesson=generate_lesson,
    build_content_blocks=build_question_content_blocks,
)
app.include_router(build_mistake_router(store=mistake_store, recognize=mistake_recognizer))

# Stateful tutoring is a separate domain store so message history does not
# expand the capture repository or the generic textbook TutorStore.
tutoring_store = TutoringStore(engine=store.engine)
stateful_tutor = StatefulTutor(runtime=runtime)
app.include_router(build_tutoring_router(
    mistake_store=mistake_store,
    tutoring_store=tutoring_store,
    tutor=stateful_tutor,
))

# Phase-four verification exercises are persisted separately from the tutor
# conversation. This keeps a student's scored attempts immutable and makes the
# later mastery/review policy independent from free-form chat history.
variation_store = VariationStore(engine=store.engine)
variation_service = VariationService(generator=generate_lesson)
review_store = ReviewStore(engine=store.engine)
app.include_router(build_practice_router(
    mistake_store=mistake_store,
    tutoring_store=tutoring_store,
    variation_store=variation_store,
    variation_service=variation_service,
    review_store=review_store,
))
app.include_router(build_review_router(
    mistake_store=mistake_store,
    review_store=review_store,
    variation_service=variation_service,
))
