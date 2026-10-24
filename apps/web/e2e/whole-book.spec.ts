import { test, expect } from "@playwright/test";

test("user starts whole-book production, pauses it, and resumes the saved run", async ({ page }) => {
  let startRequests = 0;
  let pauseRequests = 0;
  let resumeRequests = 0;
  let run: Record<string, unknown> | null = null;
  const batches = [{
    batchIndex: 0,
    title: "Science · Unit 1（第 1–30 页）",
    pageStart: 1,
    pageEnd: 30,
    status: "published",
    message: "本批课时已完成检查并发布，可直接使用。",
    chapterId: "chapter-1",
    coursewareIds: ["courseware-1"],
    publicationIds: ["publication-1"],
    skippedPages: [],
    error: null,
  }, {
    batchIndex: 1,
    title: "Science · Unit 2（第 31–60 页）",
    pageStart: 31,
    pageEnd: 60,
    status: "source_preparation",
    message: "正在准备教材原页。",
    chapterId: null,
    coursewareIds: [],
    publicationIds: [],
    skippedPages: [],
    error: null,
  }, {
    batchIndex: 2,
    title: "Science · Unit 3（第 61–61 页）",
    pageStart: 61,
    pageEnd: 61,
    status: "queued",
    message: "等待前一批完成后开始准备原页。",
    chapterId: null,
    coursewareIds: [],
    publicationIds: [],
    skippedPages: [],
    error: null,
  }];
  const state = (status: "queued" | "paused" | "running") => ({
    runId: "run-61-page-book",
    uploadId: "book-61-page",
    filename: "Science.pdf",
    status,
    message: status === "paused" ? "已暂停续做；当前已完成课时可直接使用，继续后会从未完成批次接着制作。" : "整本制作已排队，Worker 会从第一个原页批次开始。",
    totalPages: 61,
    completedPages: status === "paused" || status === "running" ? 30 : 0,
    completedBatches: status === "paused" || status === "running" ? 1 : 0,
    batchCount: 3,
    currentBatchIndex: status === "paused" || status === "running" ? 1 : 0,
    batches,
    canPause: status === "queued" || status === "running",
    canResume: status === "paused",
    createdAt: 1,
    updatedAt: 1,
  });

  await page.route("**/api/auth/config", async (route) => route.fulfill({ json: { protected: false } }));
  await page.route("**/api/courseware/profiles", async (route) => route.fulfill({ json: { items: [{ subjectId: "science", label: "科学" }] } }));
  await page.route("**/api/library", async (route) => route.fulfill({ json: { items: [{
    uploadId: "book-61-page", importId: "import-61-page", filename: "Science.pdf", size: 100,
    status: "complete", questionCount: 0, pageCount: 61, chapter: "Science", updatedAt: 1, materialKind: "textbook",
  }] } }));
  await page.route("**/api/chapters", async (route) => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/uploads/book-61-page/courseware/whole-book", async (route) => {
    if (route.request().method() === "POST") {
      startRequests += 1;
      const request = route.request().postDataJSON() as Record<string, unknown>;
      expect(request.mode).toBe("whole_book");
      expect(request.subject).toBe("science");
      expect(request.pageStart).toBeUndefined();
      expect(request.pageEnd).toBeUndefined();
      run = state("running");
      await route.fulfill({ status: 202, json: run });
      return;
    }
    if (!run) {
      await route.fulfill({ status: 404, json: { detail: "尚未开始整本教材制作" } });
      return;
    }
    await route.fulfill({ json: run });
  });
  await page.route(/\/api\/courseware\/whole-book-runs\/run-61-page-book(?:\/.*)?$/, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: run });
      return;
    }
    if (route.request().url().endsWith("/pause")) {
      pauseRequests += 1;
      run = state("paused");
    } else if (route.request().url().endsWith("/resume")) {
      resumeRequests += 1;
      run = state("running");
    }
    await route.fulfill({ status: 202, json: run });
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/studio/courseware");
  await expect(page.getByText("选择一本教材继续制作。课件完成后可直接授课；只有未解决的问题需要介入，手动制作时可编辑。试卷继续使用原有练习流程。")).toBeVisible();
  await page.goto("/studio/courseware?uploadId=book-61-page");
  await expect(page.getByRole("heading", { name: "Science.pdf", level: 1 })).toBeVisible();
  await expect(page.getByText(/最多 1000 个原 PDF 物理页.*每批最多 30 个物理页.*最多 34 批.*最多制作 45 张课件页/)).toBeVisible();
  await page.getByLabel("学科").selectOption("science");
  await page.getByRole("radio", { name: "整本自动续做（61 页）" }).check();
  await page.getByRole("button", { name: "开始整本自动制作" }).click();

  await expect(page.getByRole("heading", { name: "Science.pdf", level: 1 })).toBeVisible();
  await expect(page.getByText(/整本最多 1000 个物理页.*不超过 34 批.*45 张课件页/)).toBeVisible();
  const settings = page.locator("details.courseware-whole-book-settings");
  await expect(settings.locator(":scope > summary")).toHaveText("开始新制作（整本或指定页段）");
  await expect(page.getByRole("button", { name: "开始整本自动制作" })).toBeHidden();
  await expect(page.locator(".whole-book-progress").locator("xpath=following-sibling::details[contains(@class, 'courseware-whole-book-settings')]")).toHaveCount(1);
  await expect(page.getByRole("region", { name: "整本自动制作进度" }).getByRole("heading", { name: "Science.pdf" })).toBeInViewport();
  await settings.locator(":scope > summary").click();
  await expect(page.getByLabel("学科")).toBeVisible();
  await settings.locator(":scope > summary").click();
  await expect(page.getByText("第 1 批 · 原 PDF 第 1–30 页")).toBeVisible();
  await expect(page.getByRole("link", { name: "打开已发布课件 1 →" })).toHaveAttribute("href", "/teacher/courseware/courseware-1?publicationId=publication-1");
  await page.getByRole("button", { name: "暂停整本制作" }).click();
  await expect(page.getByText("已暂停续做；当前已完成课时可直接使用，继续后会从未完成批次接着制作。")).toBeVisible();
  await page.getByRole("button", { name: "继续整本制作" }).click();
  await expect(page.getByRole("button", { name: "暂停整本制作" })).toBeVisible();

  expect(startRequests).toBe(1);
  expect(pauseRequests).toBe(1);
  expect(resumeRequests).toBe(1);
});
