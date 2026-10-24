import { expect, test } from "@playwright/test";

const chapterId = "chapter-acceptance";
const coursewareId = "courseware-acceptance";
const publicationId = "publication-acceptance";
const revisionId = "source-revision-acceptance";
const pageRef = { sourceRevisionId: revisionId, page: 23 };

function page(title = "Unit 1 · Cover", reviewStatus = "pending") {
  return {
    pageId: "page-cover", sectionId: "section-1", pageKind: "cover", layout: "hero_split", title,
    objectiveIds: ["objective-1"], sourceRefs: [pageRef],
    blocks: [
      { kind: "text", blockId: "block-intro", authorship: "teacher_authored", text: "A source-grounded introduction.", sourceRefs: [pageRef] },
      { kind: "activity", blockId: "block-practice", activityId: "activity-1", activityKind: "choice", instructions: "Choose the supported statement.", assessmentMode: "objective", authorship: "teacher_authored", choices: ["Supported", "Not supported"], answerKey: "Supported", acceptedAnswers: ["Supported"], teacherRubric: "Check the source before responding.", sourceRefs: [pageRef], objectiveIds: ["objective-1"] },
    ],
    teacherNotes: "Teacher-only note: compare responses after the discussion.",
    revealSteps: [{ revealId: "reveal-1", label: "Example", content: "Teacher-only model answer.", reviewStatus: "pending", sourceRefs: [pageRef] }],
    estimatedMinutes: 4, generationStatus: "completed", reviewStatus, version: 1,
  };
}

function document(status = "draft", reviewStatus = "pending") {
  return {
    coursewareId, chapterId, planVersion: 1, sourceRevisionId: revisionId, subject: "english", title: "Unit 1: Writing with Evidence",
    audience: "Grade 8", languagePolicy: "English with short Chinese glosses", durationMinutes: 40, pageStart: 23, pageEnd: 25,
    sections: [{ sectionId: "section-1", title: "Opening", purpose: "Connect to the source", estimatedMinutes: 4, pageIds: ["page-cover"] }],
    pages: [page("Unit 1 · Cover", reviewStatus)], assets: [], reviewIssues: [], recordVersion: 1, status,
    generationStatus: "completed", sourceReadiness: "ready", reviewStatus, publicationId: status === "published" ? publicationId : undefined,
    sourceTitle: "English textbook", sourceUrl: "https://example.test/book", sourceLicense: "Classroom permission",
    attributionText: "Selected textbook source", sourceDistributionScope: "classroom", createdAt: 1, updatedAt: 1,
  };
}

function plan(status = "awaiting_confirmation") {
  return {
    schemaVersion: 1, chapterId, sourceRevisionId: revisionId, subject: "english", audience: "Grade 8",
    languagePolicy: "English with short Chinese glosses", pageStart: 23, pageEnd: 25,
    objectives: [{ objectiveId: "objective-1", description: "Support a claim with a source detail.", sourceRefs: [pageRef] }],
    sessions: [{ sessionId: "session-1", title: "Writing with Evidence", durationMinutes: 40, sections: [{
      sectionId: "section-1", title: "Opening", purpose: "Connect to the source", estimatedMinutes: 4,
      pages: [{ pageId: "page-cover", title: "Unit 1 · Cover", pageKind: "cover", layout: "hero_split", objectiveIds: ["objective-1"], sourceRefs: [pageRef], estimatedMinutes: 4, required: true }],
    }] }],
    coverageMap: { "objective-1": [pageRef] }, status, planVersion: 1, recordVersion: 1,
    generationStatus: "completed", createdAt: 1, updatedAt: 1,
  };
}

function chapter() {
  return {
    chapterId, subject: "english", title: "Unit 1: Writing with Evidence", teachingMode: "tutorial", status: "draft",
    recordVersion: 1, version: 1, lessons: [], currentLessonIds: [], reviewIssues: [], publications: [], publicationId: null,
    sourceRevisions: [{ sourceRevisionId: revisionId, sourceVersion: "1", fingerprint: "sha256:sample", pageStart: 23, pageEnd: 52,
      createdAt: 1, issues: [], pages: [{ page: 23, text: "Source text excerpt", sentences: [], flags: [], regions: [], previewUrl: null }], uploadId: "upload-acceptance" }],
  };
}

test("user Given unattended production When reopening the lesson during checking Then the published lesson is directly teachable without another publish action", async ({ page: browser }) => {
  let finished = false;
  const mutations: string[] = [];
  await browser.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    if (request.method() !== "GET") mutations.push(path);
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path === "/api/auth/config") return json({ protected: false });
    if (path === `/api/courseware/${coursewareId}`) return json({
      ...document(finished ? "published" : "draft"), sourceDistributionScope: "private",
      automation: finished ? null : { status: "checking", repairs: {}, message: "正在自动检查内容、答案与教材原页。" },
    });
    return json({ detail: "Optional details are unavailable" }, 404);
  });
  await browser.goto(`/studio/courseware/${coursewareId}`);
  await expect(browser.getByText("正在自动检查内容、答案与教材原页。")).toBeVisible();
  await expect(browser.getByRole("button", { name: /确认并/ })).toHaveCount(0);
  await browser.reload();
  await expect(browser.getByText("正在自动检查内容、答案与教材原页。")).toBeVisible();
  finished = true;
  await expect(browser.getByRole("link", { name: "直接授课", exact: true })).toBeVisible();
  await expect(browser.getByRole("region", { name: "课件状态" }).getByText("已发布", { exact: true })).toBeVisible();
  await expect(browser.getByText("课件已发布，可直接授课。")).toBeVisible();
  await expect(browser.getByRole("button", { name: /确认并/ })).toHaveCount(0);
  await expect(browser.getByRole("button", { name: "发布课件", exact: true })).toHaveCount(0);
  expect(mutations).toEqual([]);
  await browser.setViewportSize({ width: 390, height: 844 });
  expect(await browser.evaluate(() => window.document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await browser.screenshot({ path: test.info().outputPath("automatic-ready-mobile.png"), fullPage: true });
});

for (const lostResponse of [false, true]) test(`user Given automatic source preparation When refreshing and previewing ${lostResponse ? "despite a lost publish response" : "the result"} Then the existing publish action works without intermediate reviews`, async ({ page: browser }) => {
  let sourceReady = false;
  let currentDocument = document();
  let publishBody: Record<string, unknown> | null = null;
  const mutations: string[] = [];
  await browser.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (request.method() !== "GET") mutations.push(path);
    if (path === "/api/auth/config") return json({ protected: false });
    if (path === "/api/courseware/profiles") return json({ items: [{ subjectId: "english", label: "英语" }] });
    if (path === "/api/library") return json({ items: [{ uploadId: "upload-acceptance", filename: "English textbook.pdf", status: "complete", materialKind: "textbook", pageCount: 52 }] });
    if (path === "/api/chapters") return json({ items: [] });
    if (path === "/api/jobs/source-job") return json({ jobId: "source-job", status: sourceReady ? "succeeded" : "running", progress: sourceReady ? 100 : 25, message: "Preparing source pages", result: sourceReady ? { planJobId: "plan-job", chapters: [{ chapterId, title: "Writing with Evidence", subject: "english", sourceRevisionId: revisionId, recordVersion: 1, pageStart: 23, pageEnd: 25 }] } : null });
    if (path === `/api/chapters/${chapterId}`) return json(chapter());
    if (path === `/api/chapters/${chapterId}/courseware-plan`) return json({ ...plan("approved"), generationAudit: { automaticGeneration: true } });
    if (path === `/api/chapters/${chapterId}/courseware`) return json({ items: [{ coursewareId, title: currentDocument.title, pageCount: 1, status: "draft", generationStatus: "completed" }] });
    if (path === `/api/courseware/${coursewareId}`) return json(currentDocument);
    if (path === `/api/courseware/${coursewareId}/publish`) {
      publishBody = request.postDataJSON();
      currentDocument = { ...currentDocument, status: "published", publicationId, recordVersion: 3 };
      if (lostResponse) return json({ detail: "Response lost after saving" }, 500);
      return json({ publicationId, version: 1, snapshot: currentDocument });
    }
    return json({ detail: "Optional diagnostics unavailable in this scenario" }, 404);
  });

  await browser.goto("/studio/courseware?uploadId=upload-acceptance&sourceJobId=source-job&mode=automatic");
  await expect(browser.getByText("教材来源准备：处理中")).toBeVisible();
  await browser.reload();
  await expect(browser.getByText("教材来源准备：处理中")).toBeVisible();
  sourceReady = true;
  await expect(browser.getByRole("link", { name: "查看课件与进度" })).toBeVisible();
  await browser.getByRole("link", { name: "查看课件与进度" }).click();
  await expect(browser.getByRole("region", { name: "课件预览" })).toBeVisible();
  await expect(browser.locator(".courseware-toolbar")).toHaveCount(0);
  await expect(browser.getByRole("button", { name: "Next ›" })).toBeVisible();
  const navigation = browser.locator(".courseware-pagination");
  const content = browser.locator(".courseware-stage");
  expect((await navigation.boundingBox())!.y).toBeLessThan((await content.boundingBox())!.y);
  await expect(browser.getByLabel("讲解内容")).not.toBeVisible();
  await browser.getByRole("button", { name: "发布课件", exact: true }).first().click();
  await expect(browser.getByText(/课件已发布/).first()).toBeVisible();
  expect(publishBody).toMatchObject({ expectedRecordVersion: 1, confirmReview: true });
  expect(mutations).toEqual([`/api/courseware/${coursewareId}/publish`]);
});

test("user prepares one textbook section, confirms its plan, reviews a page, and publishes a private-safe classroom player", async ({ page: browser }) => {
  let currentPlan: ReturnType<typeof plan> | null = null;
  let currentDocument = document();
  let sourcePrepBody: Record<string, unknown> | null = null;
  let savedPage: Record<string, unknown> | null = null;
  const apiReads: string[] = [];

  await browser.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (!path.startsWith("/api/")) return route.continue();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    apiReads.push(`${method} ${path}`);

    if (path === "/api/auth/config" && method === "GET") return json({ protected: false });
    if (path === "/api/courseware/profiles" && method === "GET") return json({ items: [{ subjectId: "english", label: "英语" }] });
    if (path === "/api/library" && method === "GET") return json({ items: [{ uploadId: "upload-acceptance", importId: "import-1", filename: "English textbook.pdf", size: 12, status: "complete", questionCount: 0, pageCount: 52, chapter: "Unit 1", updatedAt: 1, materialKind: "textbook" }] });
    if (path === "/api/chapters" && method === "GET") return json({ items: [] });
    if (path === "/api/chapters/from-upload/upload-acceptance" && method === "POST") {
      sourcePrepBody = request.postDataJSON() as Record<string, unknown>;
      return json({ jobId: "source-job", status: "queued", progress: 0, message: "Preparing source pages" }, 202);
    }
    if (path === "/api/jobs/source-job" && method === "GET") return json({ jobId: "source-job", status: "succeeded", progress: 100, message: "Source pages are ready", result: { chapters: [{ chapterId, title: "Unit 1: Writing with Evidence", subject: "english", sourceRevisionId: revisionId, recordVersion: 1, pageStart: 23, pageEnd: 52 }] } });
    if (path === `/api/chapters/${chapterId}` && method === "GET") return json(chapter());
    if (path === `/api/chapters/${chapterId}/courseware` && method === "GET") return json({ items: [] });
    if (path === `/api/chapters/${chapterId}/courseware-plan` && method === "GET") return currentPlan ? json(currentPlan) : json({ detail: "Plan not found" }, 404);
    if (path === `/api/chapters/${chapterId}/courseware-plan` && method === "POST") {
      const body = request.postDataJSON() as Record<string, unknown>;
      expect(body.pageStart).toBe(23);
      expect(body.pageEnd).toBe(25);
      currentPlan = { ...plan(), audience: String(body.audience), languagePolicy: String(body.languagePolicy), pageStart: Number(body.pageStart), pageEnd: Number(body.pageEnd) };
      return json({ plan: currentPlan, job: null }, 202);
    }
    if (path === `/api/chapters/${chapterId}/courseware-plan/approve` && method === "POST") {
      currentPlan = plan("approved");
      currentDocument = document();
      return json({ plan: currentPlan, documents: [currentDocument], jobs: [] }, 202);
    }
    if (path === `/api/courseware/${coursewareId}` && method === "GET") return json(currentDocument);
    if (path === `/api/courseware/${coursewareId}/pages/page-cover` && method === "PUT") {
      const body = request.postDataJSON() as Record<string, unknown>;
      savedPage = body.page as Record<string, unknown>;
      currentDocument = { ...currentDocument, pages: [{ ...page(), ...(savedPage ?? {}), generationStatus: "completed", reviewStatus: "pending", version: 2 }], recordVersion: currentDocument.recordVersion + 1 };
      return json(currentDocument);
    }
    if (path === `/api/courseware/${coursewareId}/review` && method === "POST") {
      currentDocument = { ...currentDocument, status: "ready", reviewStatus: "ready", recordVersion: currentDocument.recordVersion + 1, pages: [page("Unit 1 · Cover", "approved")] };
      return json(currentDocument);
    }
    if (path === `/api/courseware/${coursewareId}/publish` && method === "POST") {
      currentDocument = { ...currentDocument, status: "published", publicationId, recordVersion: currentDocument.recordVersion + 1 };
      return json({ coursewareId, publicationId, version: 1, publishedAt: 2, distributionScope: "classroom", sourceRevisionId: revisionId, sourceTitle: "English textbook", sourceUrl: "https://example.test/book", attributionText: "Selected textbook source", snapshot: currentDocument });
    }
    if (path === `/api/courseware/${coursewareId}/presentation` && method === "GET") return json({ coursewareId, publicationId, version: 1, distributionScope: "classroom", sourceTitle: "English textbook", sourceUrl: "https://example.test/book", attributionText: "Selected textbook source", sourceLicense: "Classroom permission", document: currentDocument });
    if (path === `/api/courseware/${coursewareId}/teaching-unit` && method === "GET") return json({ chapterId, title: "Unit 1: Writing with Evidence", subject: "english", lessons: [], resume: null });
    if (path === `/api/courseware/${coursewareId}/published` && method === "GET") {
      const publishedPage = page("Unit 1 · Cover");
      const { teacherNotes: _teacherNotes, revealSteps: _revealSteps, ...safePage } = publishedPage;
      const safeActivity = { kind: "activity", blockId: "block-practice", activityId: "activity-1", activityKind: "choice", instructions: "Choose the supported statement.", assessmentMode: "objective", authorship: "teacher_authored", choices: ["Supported", "Not supported"], sourceRefs: [pageRef], objectiveIds: ["objective-1"] };
      safePage.blocks = [publishedPage.blocks[0], safeActivity];
      return json({ coursewareId, publicationId, version: 1, title: "Unit 1: Writing with Evidence", subject: "english", durationMinutes: 40, status: "published", sourceTitle: "English textbook", sourceUrl: "https://example.test/book", sourceLicense: "Classroom permission", attributionText: "Selected textbook source", sourceDistributionScope: "classroom", sections: currentDocument.sections, pages: [safePage], assets: [] });
    }

    return json({ detail: `Unhandled test API route: ${method} ${path}` }, 500);
  });

  await browser.goto("/studio/courseware?uploadId=upload-acceptance");
  await expect(browser.getByRole("heading", { name: "English textbook.pdf" })).toBeVisible();
  await expect(browser.getByLabel("学科")).toHaveValue("");
  await expect(browser.getByRole("button", { name: "开始整本自动制作" })).toBeVisible();
  expect(apiReads.filter((entry) => !entry.startsWith("GET "))).toEqual([]);
  await browser.getByLabel("制作指定页段（每次最多 30 页）").check();
  await browser.getByLabel("学科").selectOption("english");
  await browser.getByLabel("起始页").fill("23");
  await browser.getByLabel("结束页").fill("52");
  await browser.locator(".courseware-source-license summary").click();
  await browser.getByLabel("允许的分发范围").selectOption("classroom");
  await browser.getByText("调整主题与制作方式（可选）").click();
  await browser.getByRole("checkbox", { name: /自动完成教程/ }).uncheck();
  await browser.getByRole("button", { name: "确认并准备教材来源" }).click();
  await expect(browser.getByText("Unit 1: Writing with Evidence")).toBeVisible();
  await expect.poll(() => sourcePrepBody).toMatchObject({ subject: "english", pageStart: 23, pageEnd: 52, distributionScope: "classroom" });
  await browser.getByRole("button", { name: "继续制作课件" }).click();

  await expect(browser.getByRole("heading", { name: "Unit 1: Writing with Evidence" })).toBeVisible();
  await expect(browser.getByText("英语 · 教材第 23–52 页")).toBeVisible();
  await browser.getByText("调整教学要求（可选）").click();
  await browser.getByLabel("学习对象").fill("Grade 8");
  await browser.getByLabel("授课语言策略").fill("English with short Chinese glosses");
  await browser.getByLabel("起始页").fill("23");
  await browser.getByLabel("结束页").fill("25");
  await browser.getByLabel("学习目标（每行一个）").fill("Support a claim with a source detail.");
  await browser.getByRole("button", { name: "手动生成规划并编辑" }).click();
  await expect(browser.getByRole("heading", { name: "确认课时与页面规划" })).toBeVisible();
  await expect(browser.getByText("规划来源起始页")).toBeVisible();
  await browser.getByRole("button", { name: "确认规划并生成课件" }).click();
  await expect(browser.getByRole("link", { name: /查看课件与进度/ })).toBeVisible();
  await browser.getByRole("link", { name: /查看课件与进度/ }).click();
  await browser.getByText("编辑页面与详细审核（可选）").click();

  await expect(browser.locator(".courseware-workflow-header").getByRole("heading", { level: 1, name: "Unit 1: Writing with Evidence" })).toBeVisible();
  await expect(browser.getByText("教师讲稿")).toBeVisible();
  await browser.getByLabel("讲解内容").fill("Revised from the selected page of the textbook.");
  await browser.getByRole("button", { name: "保存页面修改" }).click();
  await expect.poll(() => savedPage).toMatchObject({ title: "Unit 1 · Cover" });
  expect(savedPage?.blocks).toEqual(expect.arrayContaining([expect.objectContaining({ blockId: "block-intro", sourceRefs: [pageRef] })]));
  await browser.getByLabel("选择审核 Unit 1 · Cover").check();
  await browser.getByRole("button", { name: "审核所选页" }).click();
  await expect(browser.getByRole("button", { name: "发布课件" })).toBeEnabled();
  await browser.getByRole("button", { name: "发布课件" }).click();
  await expect(browser.getByText(/课件已发布/).first()).toBeVisible();

  await browser.getByRole("link", { name: "直接授课" }).click();
  await expect(browser).toHaveURL(new RegExp(`/teacher/courseware/${coursewareId}`));
  const returnToEditor = browser.getByRole("link", { name: "课件管理" });
  await expect(returnToEditor).toHaveAttribute("href", `/studio/courseware/${coursewareId}`);
  await expect(browser.getByRole("heading", { name: "Unit 1: Writing with Evidence" })).toBeVisible();
  await browser.getByRole("tab", { name: "Teacher notes" }).click();
  await expect(browser.getByText("Teacher-only note: compare responses after the discussion.")).toBeVisible();
  await browser.getByRole("tab", { name: "Page" }).click();
  await browser.getByRole("button", { name: "Rail" }).click();
  await expect(browser.getByRole("navigation", { name: "Page rail" })).toBeVisible();
  await browser.getByRole("button", { name: "Deck" }).click();
  await expect(browser.getByRole("navigation", { name: "Page rail" })).toHaveCount(0);
  await returnToEditor.click();
  await expect(browser.getByRole("link", { name: "学生版", exact: true })).toBeVisible();
  const studentRouteStart = apiReads.length;
  await browser.getByRole("link", { name: "学生版", exact: true }).click();
  await expect(browser.getByRole("link", { name: /学习空间/ })).toBeVisible();
  await expect(browser.getByRole("heading", { name: "Unit 1: Writing with Evidence" })).toBeVisible();
  await expect(browser.getByRole("tab", { name: "Teacher notes" })).toHaveCount(0);
  await expect(browser.getByText("Teacher-only note: compare responses after the discussion.")).toHaveCount(0);
  await expect(browser.getByText("Teacher-only model answer.")).toHaveCount(0);
  const studentRouteReads = apiReads.slice(studentRouteStart);
  expect(studentRouteReads).toContain(`GET /api/courseware/${coursewareId}/published`);
  expect(studentRouteReads).not.toContain(`GET /api/courseware/${coursewareId}/presentation`);
  expect(studentRouteReads).not.toContain(`GET /api/courseware/${coursewareId}`);
  expect(apiReads.filter((entry) => entry === `GET /api/courseware/${coursewareId}/presentation`).length).toBeGreaterThanOrEqual(1);
});

test("user Given a long published cover When selecting several options and filling a blank Then the browser submits the right responses and keeps navigation visible", async ({ page: browser }) => {
  const submitted: Record<string, unknown>[] = [];
  const safePage = {
    ...page(),
    blocks: [
      { kind: "text", blockId: "long-intro", text: "Read the source and explain your evidence. ".repeat(45), authorship: "ai_supplement", sourceRefs: [pageRef] },
      { kind: "activity", blockId: "multi", activityId: "multi", activityKind: "choice", instructions: "Choose both agents.", assessmentMode: "objective", selectionMode: "multiple", choices: ["Originator", "Audience", "Context"], sourceRefs: [pageRef], objectiveIds: ["objective-1"] },
      { kind: "activity", blockId: "blank", activityId: "blank", activityKind: "fill_blank", instructions: "Name the audience.", assessmentMode: "objective", selectionMode: "single", choices: [], sourceRefs: [pageRef], objectiveIds: ["objective-1"] },
    ],
    teacherNotes: undefined, revealSteps: undefined,
  };
  await browser.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    if (path === "/api/auth/config") return route.fulfill({ json: { protected: false } });
    if (path === `/api/courseware/${coursewareId}/published`) return route.fulfill({ json: {
      ...document("published"), publicationId, version: 1,
      sections: [{ ...document().sections[0], pageIds: [safePage.pageId, "long-body"] }],
      pages: [safePage, { ...safePage, pageId: "long-body", title: "Long explanation", pageKind: "explanation", layout: "text_focus", blocks: [
        { kind: "text", blockId: "long-body-copy", text: "A long explanation preserves the original conditions and asks learners to compare their reasoning with the source. ".repeat(180), authorship: "ai_supplement", sourceRefs: [pageRef] },
      ] }], assets: [],
    } });
    if (path === `/api/courseware/${coursewareId}/attempts`) {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      submitted.push(body);
      return route.fulfill({ json: { attemptId: `attempt-${submitted.length}`, publicationId, activityId: body.activityId, assessment: "correct", feedback: "Saved.", response: body.response } });
    }
    return route.fulfill({ status: 500, json: { detail: `Unexpected route ${path}` } });
  });
  await browser.setViewportSize({ width: 2038, height: 1064 });
  await browser.goto(`/learn/courseware/${coursewareId}?publicationId=${publicationId}`);
  await expect(browser.getByRole("heading", { name: "Unit 1: Writing with Evidence" })).toBeVisible();
  await browser.getByRole("button", { name: "Originator", exact: true }).click();
  await browser.getByRole("button", { name: "Audience", exact: true }).click();
  await browser.getByRole("button", { name: "Context", exact: true }).click();
  await browser.getByRole("button", { name: "Context", exact: true }).click();
  await browser.getByRole("button", { name: "Submit practice: Choose both agents." }).click();
  await expect.poll(() => submitted[0]).toMatchObject({ publicationId, activityId: "multi", response: { selectedOptions: ["Originator", "Audience"] } });
  await browser.getByRole("textbox", { name: "Activity response" }).fill("audience");
  await browser.getByRole("button", { name: "Submit practice: Name the audience." }).click();
  await expect.poll(() => submitted[1]).toMatchObject({ publicationId, activityId: "blank", response: { text: "audience" } });
  expect(await browser.locator(".courseware-pagination").evaluate((element) => element.getBoundingClientRect().bottom <= innerHeight)).toBe(true);
  expect(await browser.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await browser.reload();
  await expect(browser.getByRole("textbox", { name: "Activity response" })).toHaveValue("audience");
  await expect(browser.getByRole("button", { name: "Audience", exact: true })).toHaveAttribute("aria-pressed", "true");
  await browser.getByRole("button", { name: "Next ›", exact: true }).click();
  await expect(browser.getByRole("heading", { name: "Long explanation" })).toBeVisible();
  expect(await browser.evaluate(() => {
    const footer = document.querySelector(".courseware-source-footer")!;
    const lastBlock = document.querySelector(".courseware-blocks > :last-child")!;
    return footer.getBoundingClientRect().top >= lastBlock.getBoundingClientRect().bottom;
  })).toBe(true);

});
