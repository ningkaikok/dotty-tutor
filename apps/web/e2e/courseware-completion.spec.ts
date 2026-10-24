import { expect, test, type Page } from "@playwright/test";

const chapterId = "completion-unit";
const sourceRevisionId = "completion-source";
const completion = {
  chapterId, sourceRevisionId, expectedRecordVersion: 1, maxPagesPerBatch: 5,
  batches: [{ pageStart: 4, pageEnd: 8, sourcePages: [4, 5, 6, 7, 8] }, { pageStart: 10, pageEnd: 12, sourcePages: [10, 11, 12] }],
  blockedPages: [9], alreadyGeneratedPages: [1, 2, 3], alreadyPlannedPages: [], pendingGenerationPages: [], pendingReviewPages: [2, 3], pendingPublicationPages: [2, 3],
  existingPlan: null, canCreatePlan: true, action: "create_plan", requiresHumanReview: true,
};
function plan(status = "awaiting_confirmation") {
  return {
    schemaVersion: 1, chapterId, sourceRevisionId, subject: "english", audience: "八年级", languagePolicy: "英语授课，中文释义", pageStart: 4, pageEnd: 8,
    objectives: [{ objectiveId: "goal", description: "用原文支持一个观点", sourceRefs: [{ sourceRevisionId, page: 4 }] }],
    sessions: [{ sessionId: "session", title: "补齐页段", durationMinutes: 40, sections: [{ sectionId: "section", title: "阅读", purpose: "理解原文", estimatedMinutes: 40, pages: [{ pageId: "page", title: "原文证据", pageKind: "reading", layout: "text_focus", objectiveIds: ["goal"], sourceRefs: [{ sourceRevisionId, page: 4 }], estimatedMinutes: 10, required: true }] }] }],
    coverageMap: { goal: [{ sourceRevisionId, page: 4 }] }, status, planVersion: 1, recordVersion: 1, generationStatus: "completed", createdAt: 1, updatedAt: 1,
  };
}
async function fixture(browser: Page, existing = false, planFailure = false, sourceEnd = 12) {
  const mutations: Array<{ path: string; body: Record<string, unknown> }> = [];
  await browser.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (!path.startsWith("/api/")) return route.continue();
    if (request.method() !== "GET") mutations.push({ path, body: request.postDataJSON() as Record<string, unknown> });
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path === "/api/auth/config") return json({ protected: false });
    if (path === "/api/courseware/profiles") return json({ items: [{ subjectId: "english", label: "英语" }] });
    if (path === "/api/library" || path === "/api/chapters" || path === `/api/chapters/${chapterId}/courseware`) return json({ items: [] });
    if (path === `/api/chapters/${chapterId}`) return json({
      chapterId, title: "完整单元补齐", subject: "english", teachingMode: "tutorial", status: "draft", version: 1, recordVersion: 1,
      lessons: [], currentLessonIds: [], reviewIssues: [], publications: [], sourceRevisions: [{ sourceRevisionId, sourceVersion: "1", fingerprint: "fixed-source", pageStart: 1, pageEnd: sourceEnd, createdAt: 1, issues: [], pages: [{ page: 4, text: "Original source", sentences: [], flags: [], regions: [], previewUrl: null }] }],
    });
    if (path === `/api/chapters/${chapterId}/courseware-coverage`) {
      const missing = Array.from({ length: sourceEnd - 3 }, (_, index) => index + 4);
      return json({ chapterId, sourceRevisionId, pageStart: 1, pageEnd: sourceEnd, complete: false, missingSourcePages: [9], unplannedPages: missing.filter((number) => number !== 9), ungeneratedPages: missing, unreviewedPages: [2, 3, ...missing], unpublishedPages: [2, 3, ...missing] });
    }
    if (path === `/api/chapters/${chapterId}/courseware-completion`) {
      expect(url.searchParams.get("maxPagesPerBatch")).toBe("5");
      return json(existing ? { ...completion, canCreatePlan: false, action: "continue_plan", existingPlan: { sourceRevisionId, planVersion: 1, recordVersion: 1, status: "approved", generationStatus: "completed", pageStart: 1, pageEnd: 3, stale: false } } : completion);
    }
    if (path === `/api/chapters/${chapterId}/courseware-plan`) {
      if (request.method() === "POST") return json({ plan: plan(), job: null }, 202);
      if (planFailure) return json({ detail: "规划服务暂不可用" }, 503);
      return existing ? json(plan("approved")) : json({ detail: "Plan not found" }, 404);
    }
    return json({ detail: `Unexpected route ${path}` }, 500);
  });
  return mutations;
}

test("user Given a unit gap When selecting a bounded range on a narrow screen Then the teacher can explicitly choose manual planning for only that range", async ({ page }) => {
  const mutations = await fixture(page);
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(`/studio/courseware?chapterId=${chapterId}`);
  await expect(page.getByRole("heading", { name: "补齐完整单元" })).toBeVisible();
  await expect(page.getByText(/原页 9.*来源缺失/)).toBeVisible();
  await page.screenshot({ path: test.info().outputPath("unit-completion-narrow.png"), fullPage: true });
  await page.getByRole("button", { name: "规划教材原页 4–8" }).click();
  await expect(page.getByLabel("起始页", { exact: true })).toBeFocused();
  await expect(page.getByLabel("起始页", { exact: true })).toHaveValue("4");
  await expect(page.getByLabel("结束页", { exact: true })).toHaveValue("8");
  expect(mutations).toEqual([]);
  await page.getByText("调整教学要求（可选）").click();
  await page.getByLabel("学习对象").fill("八年级");
  await page.getByLabel("授课语言策略").fill("英语授课，中文释义");
  await page.getByLabel("学习目标（每行一个）").fill("用原文支持一个观点");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("button", { name: "手动生成规划并编辑" }).click();
  await expect(page.getByRole("heading", { name: "确认课时与页面规划" })).toBeVisible();
  expect(mutations).toEqual([{ path: `/api/chapters/${chapterId}/courseware-plan`, body: expect.objectContaining({ pageStart: 4, pageEnd: 8, expectedRecordVersion: 1 }) }]);
  expect(mutations.some((item) => /approve|publish|regenerate/.test(item.path))).toBe(false);
});
test("user Given forty available source pages When requesting thirty-one pages Then the planning limit is explained before any model task is submitted", async ({ page }) => {
  const mutations = await fixture(page, false, false, 40);
  await page.goto(`/studio/courseware?chapterId=${chapterId}`);
  await page.getByText("调整教学要求（可选）").click();
  await page.getByLabel("学习对象").fill("八年级");
  await page.getByLabel("授课语言策略").fill("英语授课");
  await page.getByLabel("学习目标（每行一个）").fill("找到原文依据");
  await page.getByLabel("起始页", { exact: true }).fill("1");
  await page.getByLabel("结束页", { exact: true }).fill("31");
  await page.getByRole("button", { name: "手动生成规划并编辑" }).click();
  await expect(page.getByRole("alert")).toContainText("最多处理 30 页");
  expect(mutations).toEqual([]);
});
test("user Given an approved plan When viewing full-unit gaps Then suggestions cannot replace existing teacher work", async ({ page }) => {
  const mutations = await fixture(page, true);
  await page.goto(`/studio/courseware?chapterId=${chapterId}`);
  await expect(page.getByText(/已确认规划.*不会覆盖现有规划/)).toBeVisible();
  await expect(page.getByRole("button", { name: /规划教材原页/ })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "确认课时与页面规划" })).toBeVisible();
  expect(mutations).toEqual([]);
});
test("user Given an unavailable existing plan When opening suggestions Then no new-plan creation is offered until the plan can be read", async ({ page }) => {
  const mutations = await fixture(page, false, true);
  await page.goto(`/studio/courseware?chapterId=${chapterId}`);
  await expect(page.getByRole("heading", { name: "暂时无法确认现有规划" })).toBeVisible();
  await expect(page.getByRole("button", { name: "手动生成规划并编辑" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /规划教材原页/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "重新读取规划" })).toBeEnabled();
  expect(mutations).toEqual([]);
});
