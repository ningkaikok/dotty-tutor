"""Dotty Tutor 的 ASGI 组合根。

使用 ``uvicorn app:app`` 启动本模块。业务逻辑分别属于 route、service、runtime 和 store；
此文件只创建对象、注入依赖并注册路由。
"""

import os

from application import create_app
from application.services.assignment_planning import AssignmentPlanningService
from application.services.lesson_generation import generate_lesson, question_payload
from application.services.personalized_assignment import PersonalizedAssignmentService
from application.services.stateful_tutor import StatefulTutor
from domain.questions.pipeline import build_question_content_blocks
from infrastructure.runtime.model_runtime import ModelRuntime
from infrastructure.runtime.model_runtime import runtime as generation_runtime
from mistake_recognition import build_mistake_recognizer
from persistence.app_store import get_application_store
from persistence.assignment_planning_store import AssignmentPlanningStore
from persistence.metrics_store import MetricsStore
from persistence.mistake_store import MistakeStore
from persistence.review_store import ReviewStore
from persistence.tutoring_store import TutoringStore
from persistence.variation_store import VariationStore
from publication_revision import PublicationRevisionService
from routers.classroom_routes import build_classroom_router
from routers.learning_routes import build_learning_router
from routers.mistake_routes import build_mistake_router
from routers.practice_routes import build_practice_router
from routers.publication_routes import build_publication_router
from routers.review_routes import build_review_router
from routers.runtime_routes import build_runtime_router
from routers.textbook_routes import processing_service
from routers.textbook_routes import router as textbook_router
from routers.tutoring_routes import build_tutoring_router
from textbook_ocr import resolve_ocr_text
from variation_service import VariationService

app = create_app()
store = get_application_store()

# 运行时配置、教材和正式学习记录共享同一数据库引擎，避免一次请求跨多个事务真相源。
# 模型调用边界指标的共享存储；生成/陪练两个 Runtime 实例都写入同一张表。
metrics_store = MetricsStore(engine=store.engine)
generation_runtime.metrics_store = metrics_store
tutor_runtime = ModelRuntime(env_prefix="TUTOR_", metrics_store=metrics_store)
app.include_router(build_runtime_router(
    store=store,
    question_payload=question_payload,
    tutor_runtime=tutor_runtime,
    metrics_store=metrics_store,
))
# 错题域复用同一引擎；学习路由通过显式依赖把试卷错答写入错题本，不让 app.py 承担业务判断。
mistake_store = MistakeStore(engine=store.engine, data_root=store.root)
assignment_planning_service = AssignmentPlanningService(
    store=store,
    planning_store=AssignmentPlanningStore(engine=store.engine),
    mistake_store=mistake_store,
    # The local demo stays deterministic; deployments can opt into the shared
    # generation runtime and the same response validator still enforces its boundary.
    runtime=generation_runtime if os.getenv("ASSIGNMENT_PLANNER_ENABLED") == "1" else None,
)
app.include_router(build_learning_router(store=store, mistake_store=mistake_store))
personalized_assignment_service = PersonalizedAssignmentService(
    store=store,
    planning_service=assignment_planning_service,
    model_runtime=generation_runtime,
)
app.include_router(build_classroom_router(
    store=store,
    planning_service=assignment_planning_service,
    personalized_service=personalized_assignment_service,
))
publication_revision_service = PublicationRevisionService(
    store=store,
    processing_service=processing_service,
)
app.include_router(build_publication_router(
    store=store,
    revision_service=publication_revision_service,
))
app.include_router(textbook_router)

# 多轮消息单独存储，且在归档错题时清理对应线程；题目记录本身仍保留，便于恢复。
tutoring_store = TutoringStore(engine=store.engine)
stateful_tutor = StatefulTutor(runtime=tutor_runtime)

# 错题域复用 OCR/生成函数，但使用独立表与路由，防止教材页面状态渗入个人错题。
mistake_recognizer = build_mistake_recognizer(
    resolve_ocr_text=resolve_ocr_text,
    generate_lesson=generate_lesson,
    build_content_blocks=build_question_content_blocks,
)
app.include_router(build_mistake_router(
    store=mistake_store,
    recognize=mistake_recognizer,
    archive_cleanup=tutoring_store.delete_for_mistake,
))

app.include_router(build_tutoring_router(
    mistake_store=mistake_store,
    tutoring_store=tutoring_store,
    tutor=stateful_tutor,
))

# 计分变式练习与自由对话分开持久化。这样作答证据保持不可变，掌握度和复习策略也不会依赖
# 难以稳定重放的聊天文本。
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
    engine=store.engine,
))
