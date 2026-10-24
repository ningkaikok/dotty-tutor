import { expect, test } from "@playwright/test";

test("user Given source recognition failed on one original page When refreshing and retrying Then the same persisted task resumes without creating a new source", async ({ page }) => {
  let retried = false;
  const mutations: string[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (request.method() !== "GET") mutations.push(path);
    if (path === "/api/auth/config") return json({ protected: false });
    if (path === "/api/courseware/profiles") return json({ items: [{ subjectId: "math", label: "数学" }] });
    if (path === "/api/library") return json({ items: [{ uploadId: "book", filename: "数学教材.pdf", status: "complete", materialKind: "textbook", pageCount: 20 }] });
    if (path === "/api/chapters") return json({ items: [] });
    if (path === "/api/jobs/failed-source/retry") { retried = true; return json({ jobId: "failed-source", status: "queued", progress: 0, message: "等待人工重试" }); }
    if (path === "/api/jobs/failed-source") return json({ jobId: "failed-source", status: retried ? "queued" : "failed", progress: 40,
      message: retried ? "等待人工重试" : "教材来源准备失败", lastError: retried ? null : { message: "教材第 12 页未能识别，请核对原页或重新选择范围" } });
    return json({ detail: "Not needed for source recovery" }, 404);
  });
  await page.goto("/studio/courseware?uploadId=book&sourceJobId=failed-source&mode=automatic");
  await page.reload();
  const recovery = page.getByRole("region", { name: "来源准备恢复" });
  await expect(recovery).toContainText("教材第 12 页未能识别");
  await recovery.getByRole("button", { name: "重试原来源任务" }).click();
  await expect(page.getByText("教材来源准备：排队中")).toBeVisible();
  await expect(page.getByText(/继续使用原页码范围和已识别页面/)).toBeVisible();
  await page.reload();
  await expect(page.getByText("教材来源准备：排队中")).toBeVisible();
  expect(mutations).toEqual(["/api/jobs/failed-source/retry"]);
});

test("user Given a failed manual planning task When returning to the chapter Then its failure is restored and the original task can be retried", async ({ page }) => {
  let retried = false;
  const mutations: string[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (request.method() !== "GET") mutations.push(path);
    if (path === "/api/auth/config") return json({ protected: false });
    if (path === "/api/courseware/profiles") return json({ items: [{ subjectId: "math", label: "数学" }] });
    if (path === "/api/library" || path === "/api/chapters" || path === "/api/chapters/chapter/courseware") return json({ items: [] });
    if (path === "/api/chapters/chapter") return json({ chapterId: "chapter", title: "数学单元", subject: "math", teachingMode: "tutorial", status: "draft", version: 1, recordVersion: 1,
      lessons: [], currentLessonIds: [], reviewIssues: [], publications: [],
      sourceRevisions: [{ sourceRevisionId: "source", uploadId: "book", pageStart: 11, pageEnd: 12, pages: [] }] });
    if (path === "/api/chapters/chapter/courseware-plan") return json({ chapterId: "chapter", sourceRevisionId: "source", subject: "math", audience: "初中", languagePolicy: "中文",
      schemaVersion: 1, planVersion: 1, recordVersion: 1, generationStatus: "queued", status: "draft", generationJobId: "failed-plan",
      pageStart: 11, pageEnd: 12, objectives: [], sessions: [], coverageMap: {}, createdAt: 1, updatedAt: 1 });
    if (path === "/api/jobs/failed-plan/retry") { retried = true; return json({ jobId: "failed-plan", status: "queued", progress: 0, message: "等待人工重试" }); }
    if (path === "/api/jobs/failed-plan") return json({ jobId: "failed-plan", status: retried ? "queued" : "failed", progress: 0,
      message: retried ? "等待人工重试" : "规划失败", lastError: retried ? null : { message: "审核模型暂时不可用" } });
    return json({ detail: "Optional diagnostics unavailable" }, 404);
  });
  await page.goto("/studio/courseware?chapterId=chapter");
  await page.reload();
  const recovery = page.getByRole("region", { name: "规划恢复" });
  await expect(recovery).toContainText("审核模型暂时不可用");
  await recovery.getByRole("button", { name: "重试原规划任务" }).click();
  await expect(page.getByText("教学规划：排队中")).toBeVisible();
  expect(mutations).toEqual(["/api/jobs/failed-plan/retry"]);
});

function draft(failed = true) {
  const sourceRefs = [{ sourceRevisionId: "source", page: 12 }];
  return {
    coursewareId: "cw-recovery", chapterId: "chapter", sourceRevisionId: "source", planVersion: 1,
    title: "公式课时", subject: "math", audience: "初中", languagePolicy: "中文", durationMinutes: 10,
    pageStart: 11, pageEnd: 12, recordVersion: 1, createdAt: 1, updatedAt: 1,
    status: "draft", generationStatus: failed ? "partial" : "completed", sourceReadiness: "ready", reviewStatus: "pending",
    sourceDistributionScope: "private", assets: [], reviewIssues: [],
    sections: [{ sectionId: "section", title: "概念与例题", purpose: "学习公式", estimatedMinutes: 10, pageIds: ["intro", "example"] }],
    pages: ["intro", "example"].map((pageId) => ({
      pageId, sectionId: "section", pageKind: "explanation", layout: "text_focus", title: pageId === "intro" ? "概念导入" : "公式例题",
      objectiveIds: [], sourceRefs, blocks: [{ kind: "text", blockId: `${pageId}-text`, text: "核对原页的公式与条件", authorship: "teacher_authored", sourceRefs }],
      revealSteps: [], estimatedMinutes: 5, generationStatus: failed && pageId === "example" ? "failed" : "completed", reviewStatus: "pending", version: 1,
      error: failed && pageId === "example" ? "公式条件缺失，需要核对原页" : null,
    })),
  };
}

for (const failed of [true, false]) test(`user Given ${failed ? "a failed page" : "an AI-flagged page"} When opening its recovery action Then the correct page opens with its original-page locator`, async ({ page }) => {
  const document = draft(failed);
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path === "/api/auth/config") return json({ protected: false });
    if (path === "/api/courseware/cw-recovery") return json(document);
    if (path === "/api/courseware/cw-recovery/ai-review") return json({ coursewareId: document.coursewareId, recordVersion: 1,
      samplingRate: 0.1, pagesToReview: 0, countsAsHumanReview: false, metrics: { compared: 0, agreements: 0, missed: 0, falseAlarms: 0 },
      pages: [{ pageId: "example", title: "公式例题", pageVersion: 1, reviewStatus: "pending", route: "needs_review", sampled: false,
        verdict: "fail", textOnly: true, summary: "需要核对公式", issues: [{ code: "formula_error", severity: "blocking", sourcePage: 12, message: "请核对原页" }] }] });
    return json({ detail: "Optional diagnostics unavailable" }, 404);
  });
  await page.goto("/studio/courseware/cw-recovery");
  if (failed) {
    await expect(page.getByText("部分页面未能完成。打开问题页核对教材来源，修订或重做后再预览发布。", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "打开问题页与恢复操作" }).click();
    await expect(page.getByRole("button", { name: "保存并使用", exact: true })).toHaveCount(0);
  } else {
    await page.getByText("编辑页面与详细审核（可选）", { exact: true }).click();
    await page.getByRole("region", { name: "AI 初审", exact: true }).getByRole("button", { name: "打开此页" }).click();
  }
  const editor = page.getByRole("region", { name: "编辑页面：公式例题" });
  await expect(editor).toBeVisible();
  await editor.getByText(/教材来源（1 处）/).click();
  await expect(editor).toContainText("教材第 12 页");
  await editor.getByRole("button", { name: "重做本页" }).click();
  await expect(editor.getByLabel("重做原因")).toBeVisible();
});
