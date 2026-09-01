"""Seed a deterministic local classroom demo from an existing publication.

This script is opt-in and never runs during application startup. It creates a
small roster, assigns one published paper, and records different progress for
the first two learners so the teacher dashboard has meaningful local data.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPOSITORY_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from persistence.app_store import AppStore
from persistence.assignment_planning_store import AssignmentPlanningStore
from persistence.database import resolve_database_url
from application.services.assignment_planning import AssignmentPlanningService


DEMO_CLASS_ID = "demo-classroom"
DEMO_ASSIGNMENT_ID = "demo-assignment"
DEMO_MEMBERS = [
    ("local-demo", "小安"),
    ("local-demo-b", "小北"),
    ("local-demo-c", "小陈"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publication-id", required=True, help="已发布互动试卷 ID")
    parser.add_argument("--database-url", default=None)
    return parser.parse_args()


def database_url(args: argparse.Namespace) -> str:
    return resolve_database_url(args.database_url)


def main() -> int:
    args = parse_args()
    store = AppStore(database_url=database_url(args))
    try:
        publication = store.load_publication(args.publication_id)
        if not publication or publication["status"] != "published":
            raise SystemExit("publication-id 必须指向已发布互动试卷")
        now = time.time()
        classroom = store.get_class(DEMO_CLASS_ID)
        if classroom is None:
            classroom = store.create_class(
                class_id=DEMO_CLASS_ID,
                name="初二数学演示班",
                subject="数学",
                grade_band="初中",
                created_at=now,
            )
        for learner_id, display_name in DEMO_MEMBERS:
            store.add_member(class_id=DEMO_CLASS_ID, learner_id=learner_id, display_name=display_name, joined_at=now)
        classroom = store.get_class(DEMO_CLASS_ID)
        assignment = next((item for item in classroom["assignments"] if item["publicationId"] == args.publication_id), None) if classroom else None
        if assignment is None:
            planner = AssignmentPlanningService(
                store=store,
                planning_store=AssignmentPlanningStore(engine=store.engine),
            )
            plan = planner.create_plan(
                class_id=DEMO_CLASS_ID,
                publication_id=args.publication_id,
            )
            planner.planning_store.confirm_and_create_assignment(
                plan_id=plan["planId"],
                class_id=DEMO_CLASS_ID,
                publication_id=args.publication_id,
                title=f"演示作业 · {publication['title']}",
                due_at=now + 7 * 86400,
                source_fingerprint=plan["sourceFingerprint"],
                warning_confirmed=True,
                assignment_id=DEMO_ASSIGNMENT_ID,
                created_at=now,
            )
            assignment = store.get_assignment(DEMO_ASSIGNMENT_ID)
        first_question = next(
            ((lesson.get("questionPayload") or {}).get("question") or {}).get("id")
            for lesson in publication.get("lessons", [])
            if ((lesson.get("questionPayload") or {}).get("question") or {}).get("id")
        )
        for index, (learner_id, _) in enumerate(DEMO_MEMBERS[:2]):
            session_id = f"demo-session-{learner_id}"
            if store.get_learning_session(session_id) is None:
                store.create_learning_session(
                    session_id=session_id,
                    learner_id=learner_id,
                    publication_id=args.publication_id,
                    assignment_id=assignment["assignmentId"],
                    started_at=now,
                )
            session = store.get_learning_session(session_id)
            if not any(attempt["questionId"] == first_question for attempt in session["attempts"]):
                store.record_exercise_attempt(
                    attempt_id=f"demo-attempt-{learner_id}",
                    session_id=session_id,
                    question_id=first_question,
                    response={},
                    assessment="correct" if index == 0 else "incorrect",
                    hint_level=0,
                    duration_ms=0,
                    created_at=now,
                )
        print(f"seeded class={DEMO_CLASS_ID} assignment={assignment['assignmentId']}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
