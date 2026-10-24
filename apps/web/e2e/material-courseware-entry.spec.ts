import { expect, test } from "@playwright/test";

test("user Given a published courseware with zero legacy lessons and eighteen source samples When opening the library Then teaching starts directly without source approvals", async ({ page }) => {
  const mutations: string[] = [];
  const sourceRef = { sourceRevisionId: "source", page: 123 };
  const pages = Array.from({ length: 7 }, (_, index) => ({
    pageId: `page-${index}`, sectionId: "section", pageKind: "explanation", layout: "text_focus",
    title: `Evidence ${index + 1}`, objectiveIds: [], sourceRefs: [sourceRef],
    blocks: [{ kind: "text", blockId: `text-${index}`, authorship: "ai_supplement", text: `Evidence from page ${index + 1}.`, sourceRefs: [sourceRef] }],
    teacherNotes: "", revealSteps: [], estimatedMinutes: 3, generationStatus: "completed", reviewStatus: "approved", version: 1,
  }));
  const document = {
    coursewareId: "lesson", chapterId: "chapter", publicationId: "publication", planVersion: 1, sourceRevisionId: "source",
    title: "Memoir and evidence", subject: "english", durationMinutes: 21, pageStart: 123, pageEnd: 125,
    sections: [{ sectionId: "section", title: "Textual evidence", estimatedMinutes: 21, pageIds: pages.map((item) => item.pageId) }],
    pages, assets: [], reviewIssues: [], recordVersion: 3, status: "published", generationStatus: "completed", sourceReadiness: "ready", reviewStatus: "ready", sourceDistributionScope: "private", createdAt: 1, updatedAt: 1,
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    if (request.method() !== "GET") mutations.push(path);
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path === "/api/auth/config") return json({ protected: false });
    if (path === "/api/library") return json({ items: [{ uploadId: "book", filename: "Writing Guide.pdf", materialKind: "textbook", pageCount: 773, questionCount: 0, status: "complete" }] });
    if (path === "/api/chapters") return json({ items: [{ chapterId: "chapter", uploadId: "book", title: "Memoir", teachingMode: "tutorial", subject: "english", status: "draft", currentLessonIds: [], recordVersion: 1, version: 1 }] });
    if (path === "/api/quality-reviews/inbox") return json({ items: [], total: 18, pendingCount: 18, reviewedCount: 0, unlinkedCount: 12, coursewarePendingCount: 0, coursewareTasks: [], mediaPendingCount: 0, mediaTasks: [] });
    if (path === "/api/chapters/chapter/courseware") return json({ items: [{ coursewareId: "lesson", title: document.title, pageCount: 7, publicationId: "publication", status: "published", generationStatus: "completed", recordVersion: 3 }] });
    if (path === "/api/courseware/lesson/presentation") return json({ coursewareId: "lesson", publicationId: "publication", version: 1, distributionScope: "private", document });
    return json({ detail: "Optional diagnostics unavailable" }, 404);
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/studio");
  await expect(page.getByText("1 份课件 · 7 页 · 1 份可授课")).toBeVisible();
  await expect(page.getByRole("button", { name: "待处理（0）" })).toBeVisible();
  await expect(page.getByText(/0 节旧版/)).toHaveCount(0);
  expect(await page.evaluate(() => window.document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("actual-courseware-card-mobile.png"), fullPage: true });
  await page.getByRole("link", { name: "直接授课 · Memoir and evidence" }).click();
  await expect(page).toHaveURL(/\/teacher\/courseware\/lesson\?publicationId=publication/);
  await expect(page.getByText("Evidence from page 1.")).toBeVisible();
  expect(mutations).toEqual([]);
});

test("user Given a 773-page textbook with one published courseware and a draft chapter When managing it Then the existing lesson is teachable and new whole-book production stays explicit", async ({ page }) => {
  const mutations: string[] = [];
  const sourceRevision = {
    sourceRevisionId: "source", sourceVersion: "1", fingerprint: "sha256:sample", pageStart: 1, pageEnd: 7,
    createdAt: 1, issues: [], pages: [], uploadId: "book",
  };
  const document = {
    coursewareId: "lesson", chapterId: "chapter", publicationId: "publication", planVersion: 1, sourceRevisionId: "source",
    title: "第一章：证据与写作", subject: "english", durationMinutes: 21, pageStart: 1, pageEnd: 7,
    sections: [], pages: [], assets: [], reviewIssues: [], recordVersion: 16, status: "published", generationStatus: "completed",
    sourceReadiness: "ready", reviewStatus: "ready", sourceDistributionScope: "private", createdAt: 1, updatedAt: 1,
  };
  const chapter = {
    chapterId: "chapter", uploadId: "book", title: "第一章：证据与写作", subject: "english", teachingMode: "tutorial", status: "draft",
    recordVersion: 16, version: 1, lessons: [], currentLessonIds: [], reviewIssues: [], publications: [], publicationId: null,
    sourceRevisions: [sourceRevision],
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    if (request.method() !== "GET") mutations.push(path);
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path === "/api/auth/config") return json({ protected: false });
    if (path === "/api/library") return json({ items: [{ uploadId: "book", filename: "Writing Guide.pdf", materialKind: "textbook", pageCount: 773, questionCount: 0, status: "complete" }] });
    if (path === "/api/chapters") return json({ items: [{ ...chapter, sourceRevisions: undefined, sourceRevisionId: "source", pageStart: 1, pageEnd: 7 }] });
    if (path === "/api/courseware/profiles") return json({ items: [{ subjectId: "english", label: "英语" }] });
    if (path === "/api/quality-reviews/inbox") return json({ items: [], total: 0, pendingCount: 0, reviewedCount: 0, unlinkedCount: 0, coursewarePendingCount: 0, coursewareTasks: [], mediaPendingCount: 0, mediaTasks: [] });
    if (path === "/api/uploads/book/courseware/whole-book") return json({ detail: "No whole-book run" }, 404);
    if (path === "/api/chapters/chapter/courseware") return json({ items: [{ coursewareId: "lesson", title: document.title, pageCount: 7, publicationId: "publication", status: "published", generationStatus: "completed", recordVersion: 16, automation: null }] });
    if (path === "/api/chapters/chapter") return json(chapter);
    if (path === "/api/chapters/chapter/courseware-plan") return json({ detail: "No legacy plan" }, 404);
    return json({ detail: "Optional diagnostics unavailable" }, 404);
  });

  await page.goto("/studio");
  await expect(page.getByRole("link", { name: "直接授课 · 第一章：证据与写作" })).toBeVisible();
  await page.getByRole("link", { name: "管理教材 · Writing Guide.pdf" }).click();
  await expect(page).toHaveURL(/\/studio\/courseware\?uploadId=book/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Writing Guide.pdf");
  await expect(page.getByRole("link", { name: "直接授课" })).toBeVisible();
  await expect(page.getByRole("link", { name: "管理章节课件 · 第一章：证据与写作" })).toBeVisible();
  await page.getByText("开始新制作（整本或指定页段）").click();
  await expect(page.getByRole("radio", { name: "整本自动续做（773 页）" })).toBeChecked();
  expect(mutations).toEqual([]);

  await page.getByRole("link", { name: "管理章节课件 · 第一章：证据与写作" }).click();
  await expect(page).toHaveURL(/\/studio\/courseware\?chapterId=chapter/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("第一章：证据与写作");
  await expect(page.getByText("已发布", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "直接授课" })).toBeVisible();
  await expect(page.getByText(/STEP 2|自动制作中|等待生成/)).toHaveCount(0);
  expect(mutations).toEqual([]);
});
