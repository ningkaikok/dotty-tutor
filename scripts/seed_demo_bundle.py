"""Create or verify a deterministic, fully synthetic teacher demo bundle.

The bundle is deliberately database-first: it uses the same publication,
assignment, learning, mistake and review stores as the application.  It never
invokes OCR, a model runtime or a network service, and it never deletes rows.
"""

# The script is executable from the repository root, so its API path bootstrap
# intentionally precedes application imports.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPOSITORY_ROOT / "apps" / "api"
MANIFEST_PATH = REPOSITORY_ROOT / "examples" / "demo-pack" / "manifest.json"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from application.services.assignment_planning import AssignmentPlanningService
from application.services.lecture_checklist import LectureChecklistService
from persistence.app_store import AppStore
from persistence.assignment_planning_store import AssignmentPlanningStore
from persistence.database import resolve_database_url
from persistence.mistake_store import MistakeStore
from persistence.review_store import ReviewStore

SEED_TIME = 1_720_000_000.0


class DemoBundleError(RuntimeError):
    """Raised when the fixed demo IDs already contain incompatible data."""


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """Load and validate the checked-in demo contract without touching a store."""
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("sourceKind") != "synthetic-fixture":
        raise DemoBundleError("demo manifest 必须声明 synthetic-fixture")
    if manifest.get("privacy", {}).get("containsStudentData"):
        raise DemoBundleError("demo manifest 不能包含学生隐私数据")
    return manifest


def mistake_id(learner_id: str, publication_id: str, question_id: str) -> str:
    """Match the stable identity used by published-paper mistake capture."""
    identity = f"{learner_id}:{publication_id}:{question_id}".encode("utf-8")
    return f"paper-{hashlib.sha256(identity).hexdigest()[:32]}"


def _question_specs(manifest: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "lessonId": "demo-pack-lesson-1",
            "questionId": "demo-pack-question-1",
            "title": "合成练习一 · 分数比较",
            "knowledgePoint": "分数比较",
            "prompt": "比较 1/2 与 2/3 的大小，并写出理由。",
        },
        {
            "lessonId": "demo-pack-lesson-2",
            "questionId": "demo-pack-question-2",
            "title": "合成练习二 · 分数运算",
            "knowledgePoint": "分数运算",
            "prompt": "计算 1/3 + 1/6，并写出通分步骤。",
        },
        {
            "lessonId": "demo-pack-lesson-3",
            "questionId": "demo-pack-question-3",
            "title": "合成练习三 · 一次方程",
            "knowledgePoint": "一次方程",
            "prompt": "解方程 x + 3 = 8，并说明移项结果。",
        },
    ]


def _ensure_publication(store: AppStore, manifest: dict[str, Any]) -> dict[str, Any]:
    publication_spec = manifest["publication"]
    publication_id = publication_spec["publicationId"]
    publication = store.load_publication(publication_id)
    question_specs = _question_specs(manifest)
    for spec in question_specs:
        existing_lesson = store.load_lesson(spec["lessonId"])
        if (
            publication is not None
            and publication.get("status") == "published"
        ) or (
            existing_lesson is not None and existing_lesson.get("status") == "published"
        ):
            continue
        store.save_lesson({
            "lessonId": spec["lessonId"],
            "title": spec["title"],
            "version": 1,
            "status": "draft",
            "sourceUploadId": None,
            "knowledgePoints": [spec["knowledgePoint"]],
            "blocks": [],
            "questionPayload": {
                "question": {
                    "id": spec["questionId"],
                    "title": spec["title"],
                    "knowledgePoint": spec["knowledgePoint"],
                    "prompt": spec["prompt"],
                },
                "quality": {"status": "ready", "validatorVersion": "demo-pack-v1"},
            },
            "guideCards": [],
        })
    expected_lessons = publication_spec["lessonIds"]
    if publication is None:
        publication = store.create_publication(
            publication_id=publication_id,
            title=publication_spec["title"],
            source_upload_id=None,
            lesson_ids=expected_lessons,
            status="draft",
            created_at=SEED_TIME,
        )
    elif publication.get("lessonIds") != expected_lessons:
        raise DemoBundleError(f"publication {publication_id} 已存在但题目集合不匹配")
    if publication["status"] == "draft":
        store.update_publication_status(publication_id, "in_review")
    if store.load_publication(publication_id)["status"] == "in_review":  # type: ignore[index]
        store.update_publication_status(publication_id, "published")
    publication = store.load_publication(publication_id)
    if not publication or publication.get("status") != "published":
        raise DemoBundleError("合成互动试卷未能发布")
    return publication


def _ensure_class(store: AppStore, manifest: dict[str, Any]) -> dict[str, Any]:
    class_spec = manifest["class"]
    class_id = class_spec["classId"]
    classroom = store.get_class(class_id)
    if classroom is None:
        classroom = store.create_class(
            class_id=class_id,
            name=class_spec["name"],
            subject=class_spec["subject"],
            grade_band=class_spec["gradeBand"],
            created_at=SEED_TIME,
        )
    if classroom["subject"] != class_spec["subject"] or classroom["gradeBand"] != class_spec["gradeBand"]:
        raise DemoBundleError(f"class {class_id} 已存在但元数据不匹配")
    existing = {member["learnerId"] for member in classroom["members"]}
    for learner in class_spec["learners"]:
        if learner["learnerId"] not in existing:
            store.add_member(
                class_id=class_id,
                learner_id=learner["learnerId"],
                display_name=learner["displayName"],
                joined_at=SEED_TIME,
            )
    return store.get_class(class_id)  # type: ignore[return-value]


def _ensure_assignment(store: AppStore, manifest: dict[str, Any], publication: dict[str, Any]) -> dict[str, Any]:
    assignment_spec = manifest["assignment"]
    assignment_id = assignment_spec["assignmentId"]
    existing = store.get_assignment(assignment_id)
    if existing:
        if existing["publicationId"] != publication["publicationId"]:
            raise DemoBundleError(f"assignment {assignment_id} 已存在但试卷不匹配")
        return existing
    class_id = manifest["class"]["classId"]
    planner = AssignmentPlanningService(
        store=store,
        planning_store=AssignmentPlanningStore(engine=store.engine),
    )
    plan = planner.create_plan(class_id=class_id, publication_id=publication["publicationId"], now=SEED_TIME)
    assignment = planner.planning_store.confirm_and_create_assignment(
        plan_id=plan["planId"],
        class_id=class_id,
        publication_id=publication["publicationId"],
        title=assignment_spec["title"],
        due_at=SEED_TIME + 7 * 86_400,
        source_fingerprint=plan["sourceFingerprint"],
        warning_confirmed=True,
        assignment_id=assignment_id,
        created_at=SEED_TIME,
    )
    return assignment


def _ensure_sessions_and_attempts(store: AppStore, manifest: dict[str, Any], assignment: dict[str, Any]) -> None:
    publication_id = manifest["publication"]["publicationId"]
    question_ids = set(manifest["assignment"]["questionIds"])
    for learner in manifest["class"]["learners"]:
        session_id = learner["sessionId"]
        session = store.get_learning_session(session_id)
        if session is None:
            store.create_learning_session(
                session_id=session_id,
                learner_id=learner["learnerId"],
                publication_id=publication_id,
                assignment_id=assignment["assignmentId"],
                started_at=SEED_TIME,
            )
        else:
            if session["learnerId"] != learner["learnerId"] or session["assignmentId"] != assignment["assignmentId"]:
                raise DemoBundleError(f"session {session_id} 已存在但归属不匹配")
    for index, attempt in enumerate(manifest["attempts"]):
        session_id = next(item["sessionId"] for item in manifest["class"]["learners"] if item["learnerId"] == attempt["learnerId"])
        if attempt["questionId"] not in question_ids:
            raise DemoBundleError(f"attempt {attempt['attemptId']} 引用了未知题目")
        session = store.get_learning_session(session_id)
        if not any(item["attemptId"] == attempt["attemptId"] for item in session["attempts"]):  # type: ignore[index]
            store.record_exercise_attempt(
                attempt_id=attempt["attemptId"],
                session_id=session_id,
                question_id=attempt["questionId"],
                response={"text": "synthetic answer"},
                assessment=attempt["assessment"],
                hint_level=index % 2,
                duration_ms=1000 + index * 100,
                created_at=SEED_TIME + index + 1,
            )


def _ensure_mistakes(store: AppStore, manifest: dict[str, Any], publication: dict[str, Any]) -> list[str]:
    mistake_store = MistakeStore(engine=store.engine, data_root=store.root)
    question_by_id = {item["question"]["id"]: item for item in (
        lesson.get("questionPayload") or {} for lesson in publication["lessons"]
    ) if item.get("question", {}).get("id")}
    result: list[str] = []
    for index, attempt in enumerate(manifest["attempts"]):
        if attempt["assessment"] == "correct":
            continue
        question = question_by_id[attempt["questionId"]]["question"]
        stable_mistake_id = mistake_id(
            attempt["learnerId"], publication["publicationId"], attempt["questionId"]
        )
        result.append(stable_mistake_id)
        existing = mistake_store.get(stable_mistake_id)
        if existing:
            if existing["learnerId"] != attempt["learnerId"]:
                raise DemoBundleError(f"mistake {stable_mistake_id} 已存在但学生不匹配")
            continue
        mistake_store.create({
            "mistakeId": stable_mistake_id,
            "learnerId": attempt["learnerId"],
            "sourceFilename": "demo-pack-synthetic.json",
            "contentType": "application/vnd.dotty.publication+json",
            "sourceImagePath": "",
            "sourceImageUrl": "",
            "questionPayload": {"question": question},
            "guideCards": [],
            "ocrRun": {"provider": "none", "status": "not_used"},
            "modelRun": {"provider": "synthetic-fixture", "model": "none"},
            "originalAnswer": "synthetic answer",
            "subject": "数学",
            "gradeBand": "初中",
            "chapter": "合成演示章节",
            "knowledgePoint": question["knowledgePoint"],
            "errorReason": attempt["errorReason"],
            "aiErrorReason": None,
            "notes": "synthetic demo pack; no OCR or model was used",
            "status": "unmastered",
            "createdAt": SEED_TIME + index,
            "updatedAt": SEED_TIME + index,
            "confirmedAt": SEED_TIME + index,
        })
    expected = [mistake_id(a["learnerId"], publication["publicationId"], a["questionId"])
                for a in manifest["attempts"] if a["assessment"] != "correct"]
    if result != expected or result != manifest.get("mistakeIds"):
        raise DemoBundleError("manifest 错题顺序不一致")
    return result


def _ensure_reviews(store: AppStore, manifest: dict[str, Any], mistake_ids: list[str]) -> None:
    review_store = ReviewStore(engine=store.engine)
    review_spec = manifest["review"]
    for mistake in mistake_ids:
        learner_id = mistake_store_learner(manifest, mistake)
        review_store.schedule(
            mistake_id=mistake,
            learner_id=learner_id,
            base_time=review_spec["baseTime"],
            intervals=tuple(review_spec["intervalDays"]),
            trigger_evidence_ref=f"mistake:{mistake}",
        )


def mistake_store_learner(manifest: dict[str, Any], stable_mistake_id: str) -> str:
    for attempt in manifest["attempts"]:
        if attempt["assessment"] == "correct" or not attempt.get("errorReason"):
            continue
        expected = mistake_id(attempt["learnerId"], manifest["publication"]["publicationId"], attempt["questionId"])
        if expected == stable_mistake_id:
            return attempt["learnerId"]
    raise DemoBundleError(f"manifest 中没有错题 {stable_mistake_id} 的归属")


def _ensure_teacher_review(store: AppStore, manifest: dict[str, Any], assignment: dict[str, Any]) -> None:
    review_spec = manifest["teacherReview"]
    dashboard = store.class_dashboard(manifest["class"]["classId"], assignment_id=assignment["assignmentId"], now=SEED_TIME)
    for point in dashboard["knowledgePoints"]:
        for evidence in point["evidence"]:
            if evidence["learnerId"] == review_spec["learnerId"] and evidence["questionId"] == review_spec["questionId"] and evidence["reviewStatus"] == "overturned":
                return
    point_id = next(
        point["knowledgePointId"] for point in dashboard["knowledgePoints"]
        if any(evidence["questionId"] == review_spec["questionId"] for evidence in point["evidence"])
    )
    store.record_teacher_review(
        event_id=review_spec["eventId"],
        class_id=manifest["class"]["classId"],
        assignment_id=assignment["assignmentId"],
        learner_id=review_spec["learnerId"],
        question_id=review_spec["questionId"],
        knowledge_point_id=point_id,
        action=review_spec["action"],
        mastery_score=None,
        corrected_assessment=review_spec["correctedAssessment"],
        note="合成演示数据：教师复核后推翻原判定",
        created_at=SEED_TIME + 100,
    )


def verify_bundle(store: AppStore, manifest: dict[str, Any]) -> dict[str, Any]:
    """Verify the observable teacher-facing contract without mutating data."""
    class_id = manifest["class"]["classId"]
    assignment_id = manifest["assignment"]["assignmentId"]
    classroom = store.get_class(class_id)
    if not classroom:
        raise DemoBundleError("demo class 不存在")
    member_ids = {member["learnerId"] for member in classroom["members"]}
    expected_members = {learner["learnerId"] for learner in manifest["class"]["learners"]}
    if not expected_members.issubset(member_ids):
        raise DemoBundleError("demo class 缺少固定三名合成学生")
    publication = store.load_publication(manifest["publication"]["publicationId"])
    assignment = store.get_assignment(assignment_id)
    if not publication or publication["status"] != "published" or not assignment:
        raise DemoBundleError("demo publication/assignment 未就绪")
    for learner in manifest["class"]["learners"]:
        session = store.get_learning_session(learner["sessionId"])
        if not session or len(session["attempts"]) != 3:
            raise DemoBundleError(f"session {learner['sessionId']} 作答不完整")
    checklist = LectureChecklistService(store=store).get_checklist(class_id=class_id, assignment_id=assignment_id, limit=5)
    if not checklist["commonMistakes"] or not checklist["evidenceRefs"]:
        raise DemoBundleError("讲评清单没有共性错题或证据引用")
    if not any(item["errorReason"] == "unknown" for row in checklist["commonMistakes"] for item in row["students"]):
        raise DemoBundleError("讲评清单丢失 unknown 错因")
    if not any(
        evidence["reviewStatus"] == "overturned"
        for point in store.class_dashboard(class_id, assignment_id=assignment_id, now=SEED_TIME)["knowledgePoints"]
        for evidence in point["evidence"]
    ):
        raise DemoBundleError("教师看板没有教师推翻证据")
    return {
        "packId": manifest["packId"],
        "classId": class_id,
        "assignmentId": assignment_id,
        "commonMistakeCount": len(checklist["commonMistakes"]),
        "evidenceRefCount": len(checklist["evidenceRefs"]),
        "verified": True,
    }


def seed_bundle(database_url: str, *, data_root: str | Path | None = None, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Seed the pack idempotently and return a compact verification summary."""
    loaded = manifest or load_manifest()
    store = AppStore(database_url=database_url, data_root=data_root)
    try:
        publication = _ensure_publication(store, loaded)
        _ensure_class(store, loaded)
        assignment = _ensure_assignment(store, loaded, publication)
        _ensure_sessions_and_attempts(store, loaded, assignment)
        mistake_ids = _ensure_mistakes(store, loaded, publication)
        _ensure_reviews(store, loaded, mistake_ids)
        _ensure_teacher_review(store, loaded, assignment)
        return verify_bundle(store, loaded)
    finally:
        store.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument("--verify", action="store_true", help="只验证固定 demo 包，不写入数据库")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.manifest)
    database_url = resolve_database_url(args.database_url)
    store = AppStore(database_url=database_url, data_root=args.data_root)
    try:
        if args.verify:
            result = verify_bundle(store, manifest)
        else:
            publication = _ensure_publication(store, manifest)
            _ensure_class(store, manifest)
            assignment = _ensure_assignment(store, manifest, publication)
            _ensure_sessions_and_attempts(store, manifest, assignment)
            mistakes = _ensure_mistakes(store, manifest, publication)
            _ensure_reviews(store, manifest, mistakes)
            _ensure_teacher_review(store, manifest, assignment)
            result = verify_bundle(store, manifest)
    finally:
        store.close()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
