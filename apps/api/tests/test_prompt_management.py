"""内容平台提示词的持久化、权限和任务版本验收。"""

from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from application import create_app
from persistence.prompt_store import PromptConflict, PromptStore
from prompts import (
    CATALOG,
    configure_prompt_source,
    freeze_prompts,
    prompt_identity,
    render_prompt,
)
from routers.prompt_routes import build_prompt_router, content_role
from tests.postgres_test_support import PostgresTestCase


class PromptManagementTests(PostgresTestCase):
    def setUp(self):
        super().setUp()
        self.store = PromptStore(engine=self.engine)
        self.detail = self.store.detail("generation.solution")
        self.base = self.detail["activeRevisionId"]
        self.text = CATALOG["generation.solution"].text + "\n请使用简短中文。"

    def draft(self):
        return self.store.save_draft("generation.solution", self.text, self.base, actor="content-editor", now=1)

    def test_user_saves_draft_previews_publishes_and_rolls_back(self):
        """Given 原版本；When 保存并预览草稿后发布再回滚；Then 正文和发布历史均可恢复。"""
        revision = self.draft()
        self.assertEqual(self.store.active_templates()["generation.solution"].text, CATALOG["generation.solution"].text)
        with self.assertRaises(PromptConflict):
            self.store.activate("generation.solution", revision["revisionId"], self.base, actor="content-publisher")
        preview = self.store.preview("generation.solution", revision["revisionId"], {"question_ir": "合成样例 ${repair}", "repair": ""})
        self.assertIn("合成样例 ${repair}", preview)
        self.store.activate("generation.solution", revision["revisionId"], self.base, actor="content-publisher", now=2)
        other_process = PromptStore(engine=self.engine)
        self.assertEqual(other_process.active_templates()["generation.solution"].text, self.text)
        self.store.activate("generation.solution", self.base, revision["revisionId"], actor="content-publisher", rollback=True, now=3)
        detail = self.store.detail("generation.solution")
        self.assertEqual(detail["activeRevisionId"], self.base)
        self.assertEqual([event["action"] for event in detail["events"]], ["rollback", "publish"])
        self.assertEqual(next(item for item in detail["revisions"] if item["revisionId"] == revision["revisionId"])["text"], self.text)
        self.assertNotIn("合成样例", str(detail))

    def test_user_cannot_publish_unknown_or_unpreviewed_draft_via_rollback(self):
        """Given 未预览草稿；When 请求回滚到它；Then 拒绝绕过发布检查。"""
        revision = self.draft()
        with self.assertRaises(PromptConflict):
            self.store.activate("generation.solution", revision["revisionId"], self.base, actor="content-publisher", rollback=True)
        with self.assertRaises(ValueError):
            self.store.save_draft("generation.solution", "删除了上下文", self.base, actor="content-editor")
        rubric = self.store.detail("evaluation.judge")
        with self.assertRaises(ValueError):
            self.store.save_draft("evaluation.judge", CATALOG["evaluation.judge"].text, rubric["activeRevisionId"], actor="content-editor")

    def test_user_concurrent_publications_have_one_winner(self):
        """Given 两份已预览草稿；When 同时基于同一生效版本发布；Then 仅一个成功。"""
        drafts = [self.draft(), self.draft()]
        for revision in drafts:
            self.store.preview("generation.solution", revision["revisionId"], {"question_ir": "样例", "repair": ""})
        def publish(revision):
            try:
                self.store.activate("generation.solution", revision["revisionId"], self.base, actor="content-publisher")
                return "published"
            except PromptConflict:
                return "conflict"
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(publish, drafts))
        self.assertCountEqual(results, ["published", "conflict"])
        self.assertEqual(len(self.store.detail("generation.solution")["events"]), 1)

    def test_user_running_task_keeps_prompt_snapshot_after_publication(self):
        """Given 任务已经读取版本；When 内容负责人发布；Then 该任务输入和身份不变化。"""
        templates = self.store.active_templates()
        revision = self.draft()
        self.store.preview("generation.solution", revision["revisionId"], {"question_ir": "样例", "repair": ""})
        configure_prompt_source(self.store.active_templates)
        self.addCleanup(configure_prompt_source, None)

        @freeze_prompts
        def running_task():
            before = render_prompt("generation.solution", question_ir="样例", repair="")
            self.store.activate("generation.solution", revision["revisionId"], self.base, actor="content-publisher")
            self.assertEqual(render_prompt("generation.solution", question_ir="样例", repair=""), before)
            self.assertEqual(prompt_identity("generation.solution"), templates["generation.solution"].identity())

        @freeze_prompts
        def next_task():
            return render_prompt("generation.solution", question_ir="样例", repair="")

        running_task()
        self.assertIn("请使用简短中文", next_task())

    def test_user_editor_cannot_publish_but_can_save_and_preview(self):
        """Given 编辑权限；When 直接调用发布接口；Then 服务端拒绝，草稿可保存预览。"""
        app = create_app()
        app.include_router(build_prompt_router(store=self.store))
        app.dependency_overrides[content_role] = lambda: "content-editor"
        with TestClient(app) as client:
            saved = client.post("/api/content/prompts/generation.solution/drafts", json={"text": self.text, "baseRevisionId": self.base})
            self.assertEqual(saved.status_code, 200)
            revision_id = saved.json()["revisionId"]
            self.assertEqual(client.post("/api/content/prompts/generation.solution/preview", json={"revisionId": revision_id, "variables": {"question_ir": "样例", "repair": ""}}).status_code, 200)
            response = client.post("/api/content/prompts/generation.solution/activate", json={"revisionId": revision_id, "expectedActiveRevisionId": self.base, "action": "publish"})
            self.assertEqual(response.status_code, 403)
            self.assertEqual(self.store.detail("generation.solution")["activeRevisionId"], self.base)
