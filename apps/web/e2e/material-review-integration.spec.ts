import { expect, test, type Page } from "@playwright/test";

const PDF_PAGE_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/x2cAAAAASUVORK5CYII=",
  "base64",
);

type ReviewCase = {
  caseId: string;
  externalCaseId: string;
  packageId: string;
  ordinal: number;
  subject: string;
  sourcePage: number;
  sourceKind: string;
  sourceFingerprint: string;
  inputFingerprint: string;
  outputFingerprint: string;
  recordVersion: number;
  reviewStatus: string;
  sourceVerification: string;
  assetVerification: string;
  counted: boolean;
  sourceTitle: string;
  sourceKey: string;
  uploadId: string | null;
  chapterIds: string[];
  source: Record<string, unknown>;
  sourceLocator: Record<string, unknown>;
  input: Record<string, unknown>;
  expected: Record<string, unknown>;
  actual: Record<string, unknown>;
  createdBy: string;
  createdByIdentityVerified: boolean;
  sourceVerifiedAt: number | null;
  sourceVerifiedHash: string | null;
  revisions: Array<Record<string, unknown>>;
  decisions: Array<Record<string, unknown>>;
};

function makeCase(overrides: Partial<ReviewCase> & Pick<ReviewCase, "caseId" | "externalCaseId" | "packageId" | "sourceTitle" | "sourceKey">): ReviewCase {
  const page = overrides.sourcePage ?? 12;
  return {
    caseId: overrides.caseId,
    externalCaseId: overrides.externalCaseId,
    packageId: overrides.packageId,
    ordinal: 1,
    subject: "math",
    sourcePage: page,
    sourceKind: "real_open_material",
    sourceFingerprint: "source-fingerprint-1",
    inputFingerprint: "input-fingerprint-1",
    outputFingerprint: "output-fingerprint-1",
    recordVersion: 1,
    reviewStatus: "pending_human_review",
    sourceVerification: "managed_asset_verified",
    assetVerification: "server_hash_verified",
    counted: false,
    sourceTitle: overrides.sourceTitle,
    sourceKey: overrides.sourceKey,
    uploadId: "upload-algebra",
    chapterIds: ["chapter-algebra"],
    source: {
      sourceTitle: overrides.sourceTitle,
      url: "https://openstax.org/details/books/elementary-algebra-2e",
      version: "2e",
      license: "CC BY 4.0",
      contentSha256: "a".repeat(64),
      assetVerification: "server_hash_verified",
    },
    sourceLocator: { page, quote: "Subtract 37 from both sides of the equation." },
    input: { question: "Why do we subtract 37 from both sides of the equation?", passage: "x + 37 = 52" },
    expected: { acceptedAnswers: ["Keep the equation balanced."], requiredSourceFragments: ["both sides"] },
    actual: { productionEvaluation: { resultText: "Subtract 37 from both sides to keep the equation balanced." } },
    createdBy: "fixture-author",
    createdByIdentityVerified: false,
    sourceVerifiedAt: 1_791_600_000,
    sourceVerifiedHash: "a".repeat(64),
    revisions: [],
    decisions: [],
    ...overrides,
  };
}

function makeRun() {
  return {
    runId: "run-blind-history",
    taskInstruction: "从冻结输入中提取题目和依据。",
    status: "completed",
    cancelRequested: false,
    perCallTimeoutSeconds: 30,
    totalBudgetSeconds: 240,
    maxOutputTokens: 500,
    error: null,
    blind: true,
    models: null,
    ratings: [],
    cases: [{
      caseId: "legacy-case",
      ordinal: 1,
      recordVersion: 1,
      sourceKind: "real_open_material",
      sourceFingerprint: "run-source-fp",
      inputFingerprint: "run-input-fp",
      outputFingerprint: "run-output-fp",
      snapshot: {
        source: { sourceTitle: "Legacy Workbook" },
        sourceLocator: { page: 9, quote: "Example source line" },
        sourcePage: 9,
        input: { question: "Which operation keeps both sides equal?", passage: "x + 7 = 19" },
        expected: { acceptedAnswers: ["Subtract 7 from both sides"] },
        actual: { resultText: "Subtract 7 from both sides." },
      },
      arms: {
        A: { arm: "A", status: "succeeded", output: { answer: "Subtract 7 from both sides." }, durationMs: 420, promptTokens: null, outputTokens: null, cost: null, error: null },
        B: { arm: "B", status: "succeeded", output: { answer: "Subtract 7 from both sides." }, durationMs: 510, promptTokens: null, outputTokens: null, cost: null, error: null },
      },
    }],
  };
}

function chapterFixture(published = false) {
  const sourceRevisionId = "revision-algebra-1";
  const lesson = {
    lessonId: "lesson-algebra-1",
    title: "保持等式平衡",
    version: 1,
    status: published ? "published" : "in_review",
    sourceRevisionId,
    sourceLocator: { sourceRevisionId, page: 12, regions: [] },
    reviewIssues: [],
    blocks: [{ id: "block-1", title: "关键步骤", type: "markdown", payload: { markdown: "对等式两边执行相同运算，保持相等。" } }],
    questionPayload: { question: { id: "lesson-algebra-1", prompt: "为什么要同时减去 37？", questionType: "short-answer" } },
  };
  const chapter = {
    chapterId: "chapter-algebra",
    subject: "math",
    title: "一元一次方程",
    status: published ? "published" : "in_review",
    version: published ? 4 : 1,
    recordVersion: 3,
    sourceRevisions: [{
      sourceRevisionId,
      fingerprint: "chapter-source-fingerprint",
      sourceVersion: "2e",
      license: "CC BY 4.0",
      pageStart: 12,
      pageEnd: 14,
      pages: [{ page: 12, text: "x + 37 = 52。等式两边同时减去 37。", regions: [], flags: [], previewUrl: null }],
      issues: [],
    }],
    lessons: [lesson],
    teachingMode: "tutorial",
    currentLessonIds: [lesson.lessonId],
    reviewIssues: [],
    publicationId: published ? "publication-algebra-v4" : null,
    publications: published ? [{ publicationId: "publication-algebra-v4", version: 4, sourceRevisionId, publishedAt: 1_791_600_000 }] : [],
  };
  return { chapter, lesson };
}

async function installMaterialReviewRoutes(page: Page, options: { mediaTasks?: Array<Record<string, unknown>>; publishedChapter?: boolean } = {}) {
  const { chapter } = chapterFixture(options.publishedChapter);
  const sharedSourceKey = `sha256:${"a".repeat(64)}`;
  const linked = makeCase({ caseId: "chapter-case", externalCaseId: "ALG-12-A", packageId: "book-package", sourceTitle: "Elementary Algebra 2e", sourceKey: sharedSourceKey });
  const needsChanges = makeCase({
    caseId: "chapter-case-needs-changes", externalCaseId: "ALG-13-B", packageId: "book-package", sourceTitle: "Elementary Algebra 2e", sourceKey: sharedSourceKey,
    sourcePage: 13, ordinal: 2, reviewStatus: "needs_changes", uploadId: "upload-algebra", chapterIds: ["chapter-algebra"],
    sourceLocator: { page: 13, quote: "Apply the same operation to both sides." },
  });
  const otherUploadInSameChapter = makeCase({
    caseId: "other-upload-case", externalCaseId: "OTHER-14-C", packageId: "book-package", sourceTitle: "Algebra Supplements", sourceKey: "package:other-package",
    uploadId: "upload-other", chapterIds: ["chapter-algebra"], sourcePage: 14,
    sourceVerification: "pending_source_verification", assetVerification: "pending_managed_asset", sourceVerifiedAt: null, sourceVerifiedHash: null,
    source: { sourceTitle: "Algebra Supplements", version: "1", license: "待核实", assetVerification: "pending_managed_asset" },
  });
  const sameSourceOtherChapter = makeCase({
    caseId: "other-chapter-case", externalCaseId: "ALG-22-D", packageId: "book-package", sourceTitle: "Elementary Algebra 2e", sourceKey: sharedSourceKey,
    uploadId: "upload-algebra", chapterIds: ["chapter-other"], sourcePage: 22,
  });
  const legacy = makeCase({
    caseId: "legacy-case", externalCaseId: "LEGACY-9-A", packageId: "legacy-package", sourceTitle: "Legacy Workbook", sourceKey: `sha256:${"b".repeat(64)}`,
    uploadId: null, chapterIds: [], sourcePage: 9,
    source: { sourceTitle: "Legacy Workbook", version: "2024", license: "校内授权", contentSha256: "b".repeat(64), assetVerification: "server_hash_verified" },
    input: { question: "Which operation keeps both sides equal?", passage: "x + 7 = 19" },
  });
  // Equal display titles with distinct trusted source keys must remain separate source groups.
  const sameTitleOtherSource = makeCase({
    caseId: "homonym-case", externalCaseId: "HOMONYM-4-A", packageId: "homonym-package", sourceTitle: "Legacy Workbook", sourceKey: "package:homonym-package",
    uploadId: null, chapterIds: [], sourcePage: 4,
    sourceVerification: "pending_source_verification", assetVerification: "pending_managed_asset", sourceVerifiedAt: null, sourceVerifiedHash: null,
    source: { sourceTitle: "Legacy Workbook", version: "2026 draft", license: "待核实", assetVerification: "pending_managed_asset" },
  });
  const cases = new Map([[linked.caseId, linked], [needsChanges.caseId, needsChanges], [otherUploadInSameChapter.caseId, otherUploadInSameChapter], [sameSourceOtherChapter.caseId, sameSourceOtherChapter], [legacy.caseId, legacy], [sameTitleOtherSource.caseId, sameTitleOtherSource]]);
  const uploads = [
    { uploadId: "upload-algebra", importId: "import-algebra", filename: "Elementary Algebra 2e.pdf", size: 1024, status: "complete", questionCount: 0, pageCount: 300, chapter: "Algebra", materialKind: "textbook", updatedAt: 1_791_600_000 },
  ];
  const mediaTasks = options.mediaTasks ?? [];
  const media = {
    mediaId: "media-algebra-v4",
    seriesId: "series-algebra",
    version: 3,
    chapterId: "chapter-algebra",
    publicationId: "publication-algebra-v4",
    lessonId: "lesson-algebra-1",
    sourceRevisionId: "revision-algebra-1",
    title: "保持等式平衡讲解",
    subject: "math",
    license: "CC BY 4.0",
    sourceTitle: "Elementary Algebra 2e",
    sourceUrl: "https://openstax.org/details/books/elementary-algebra-2e",
    attributionText: "OpenStax, CC BY 4.0",
    distributionScope: "local_only",
    sourceSnapshot: { storyboardTemplateVersion: "source-grounded-v1", storyboardModel: {} },
    rendererSnapshot: { renderer: "fixture" },
    scenes: [{ sceneId: "scene-1", order: 1, kind: "explanation", title: "保持等式平衡", narration: "等式两边执行相同运算。", sourceRefs: [{ sourceRevisionId: "revision-algebra-1", page: 12, quote: "两边同时减去 37" }] }],
    status: "consistency_review_required",
    durationSeconds: 12,
    artifactSha256: "c".repeat(64),
    consistencyReview: { passed: true, issues: [], checks: { sourceRefs: true } },
    reviews: [],
    renderJob: null,
    createdAt: 1_791_600_000,
    publishedAt: null,
  };
  const summary = (item: ReviewCase) => ({
    caseId: item.caseId, externalCaseId: item.externalCaseId, packageId: item.packageId, ordinal: item.ordinal, subject: item.subject,
    sourcePage: item.sourcePage, sourceKind: item.sourceKind, sourceFingerprint: item.sourceFingerprint, inputFingerprint: item.inputFingerprint,
    outputFingerprint: item.outputFingerprint, recordVersion: item.recordVersion, reviewStatus: item.reviewStatus,
    sourceVerification: item.sourceVerification, assetVerification: item.assetVerification, counted: item.counted,
    sourceTitle: item.sourceTitle, sourceKey: item.sourceKey, uploadId: item.uploadId, chapterIds: item.chapterIds,
  });
  const inboxRequests: URL[] = [];
  const getRequests: URL[] = [];
  const postRequests: Array<{ method: string; path: string }> = [];
  let publicationRequests = 0;
  const run = makeRun();

  await page.route((url) => url.pathname.startsWith("/api/"), async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (method === "GET") getRequests.push(url);
    if (method !== "GET") postRequests.push({ method, path });

    if (path === "/api/auth/config") return route.fulfill({ json: { protected: false } });
    if (path === "/api/library" && method === "GET") return route.fulfill({ json: { items: uploads } });
    if (path === "/api/chapters" && method === "GET") return route.fulfill({ json: { items: [{
      chapterId: chapter.chapterId, subject: "math", title: chapter.title, status: chapter.status, version: chapter.version, recordVersion: 3,
      currentLessonIds: ["lesson-algebra-1"], reviewIssues: [], teachingMode: "tutorial", uploadId: "upload-algebra", publicationId: chapter.publicationId, pageStart: 12,
    }] } });
    if (path === `/api/chapters/${chapter.chapterId}/published` && method === "GET") return route.fulfill({ json: {
      ...chapter, status: "published", version: 4, publicationId: "publication-algebra-v4", lessons: chapter.lessons.map((item) => ({ ...item, status: "published" })),
    } });
    if (path === `/api/chapters/${chapter.chapterId}` && method === "GET") return route.fulfill({ json: chapter });
    if (path.startsWith("/api/lesson-media/lessons/") && method === "GET") return route.fulfill({ json: { items: [media] } });
    if (path.endsWith("/publish") && method === "POST") {
      publicationRequests += 1;
      return route.fulfill({ status: 409, json: { detail: "Review is required before publishing." } });
    }
    if (path === "/api/quality-reviews/inbox" && method === "GET") {
      inboxRequests.push(url);
      const chapterId = url.searchParams.get("chapterId");
      const uploadId = url.searchParams.get("uploadId");
      const includeReviewed = url.searchParams.get("includeReviewed") === "true";
      let visible = [...cases.values()].filter((item) => !["human_accepted", "human_rejected"].includes(item.reviewStatus));
      if (includeReviewed) visible = [...cases.values()];
      if (chapterId) visible = visible.filter((item) => item.chapterIds.includes(chapterId));
      if (uploadId) visible = visible.filter((item) => item.uploadId === uploadId);
      const scoped = visible;
      const pendingCount = scoped.filter((item) => !["human_accepted", "human_rejected"].includes(item.reviewStatus)).length;
      const reviewedCount = scoped.filter((item) => ["human_accepted", "human_rejected"].includes(item.reviewStatus)).length;
      return route.fulfill({ json: {
        items: scoped.map(summary), total: scoped.length, pendingCount, reviewedCount,
        unlinkedCount: scoped.filter((item) => item.chapterIds.length === 0).length,
        mediaTasks: mediaTasks.filter((item) => !chapterId || item.chapterId === chapterId), mediaPendingCount: mediaTasks.filter((item) => !chapterId || item.chapterId === chapterId).length,
      } });
    }
    const sourcePageMatch = path.match(/^\/api\/quality-reviews\/cases\/([^/]+)\/source-page$/);
    if (sourcePageMatch && method === "GET") return route.fulfill({ status: 200, contentType: "image/png", body: PDF_PAGE_PNG });
    const caseMatch = path.match(/^\/api\/quality-reviews\/cases\/([^/]+)(?:\/(decisions|revisions))?$/);
    if (caseMatch) {
      const item = cases.get(decodeURIComponent(caseMatch[1]));
      if (!item) return route.fulfill({ status: 404, json: { detail: "Case not found" } });
      if (caseMatch[2] === "decisions" && method === "POST") {
        const body = request.postDataJSON() as { decision: "accepted" | "rejected" | "needs_changes"; rationale: string; verifiedQuote: string; displayName: string };
        item.reviewStatus = body.decision === "accepted" ? "human_accepted" : body.decision === "rejected" ? "human_rejected" : "needs_changes";
        item.recordVersion += 1;
        item.decisions.push({
          reviewId: `decision-${item.caseId}`, reviewedRecordVersion: item.recordVersion, stage: "final", actorId: "teacher", actorKind: "demo",
          identityVerified: false, displayName: body.displayName, decision: body.decision, rationale: body.rationale,
          verifiedQuote: body.verifiedQuote, claimedQuote: "", createdAt: 1_791_600_010,
        });
      }
      return route.fulfill({ json: item });
    }
    if (path === "/api/quality-reviews/packages" && method === "GET") return route.fulfill({ json: { items: [{ packageId: legacy.packageId, title: legacy.sourceTitle }] } });
    if (path === `/api/quality-reviews/packages/${legacy.packageId}/cases` && method === "GET") return route.fulfill({ json: { items: [summary(legacy)] } });
    if (path === "/api/quality-review-runs/models" && method === "GET") return route.fulfill({ json: { providers: [] } });
    if (path === "/api/quality-review-runs" && method === "GET") return route.fulfill({ json: { items: [run] } });
    if (path === `/api/quality-review-runs/${run.runId}` && method === "GET") return route.fulfill({ json: run });
    if (path === "/api/quality-review-runs" && method === "POST") return route.fulfill({ status: 202, json: { ...run, status: "queued" } });
    if (path === "/api/reports/learning-cost" && method === "GET") return route.fulfill({ json: {
      learnerId: "demo-learner", days: 7, generatedAt: 1_791_600_000,
      scope: { learning: "learner_cumulative", modelCalls: "global_rolling_window", costUnit: "proxy_only" },
      learning: {
        learnerId: "demo-learner", mistakes: { imported: 0, confirmed: 0, confirmationRate: null },
        tutoring: { confirmedMistakes: 0, threadsStarted: 0 },
        verification: { answeredVariations: 0, correctVariations: 0, passRate: null },
        review: { scheduledTasks: 0, completedTasks: 0, completionRate: null },
        learningEffect: { sameKnowledgePointReerrorCount: 0, sameKnowledgePointReerrorDenominator: 0, sameKnowledgePointReerrorRate: null },
      },
      modelCost: { summary: { logicalCalls: 0, failures: 0, failureRate: null, avgDurationMs: null, totalPromptTokens: null, totalOutputTokens: null, tokenMeasuredCalls: 0, tokenCoverageRate: null }, items: [] },
      limitations: [],
    } });
    return route.fulfill({ status: 404, json: { detail: `Unhandled route ${method} ${path}` } });
  });
  return { chapter, cases, inboxRequests, getRequests, postRequests, getPublicationRequests: () => publicationRequests };
}

test("user Given a chapter source inbox with pending, needs-changes, and cross-source cases When the teacher opens and accepts one embedded case Then only that source case leaves the queue and the lesson stays unpublished", async ({ page }) => {
  const fixture = await installMaterialReviewRoutes(page);
  await page.goto("/studio");
  await page.getByRole("button", { name: /待处理/ }).click();
  await page.getByText(/来源质量抽检（\d+ 项，可选）/).click();
  await expect(page.getByRole("region", { name: "来源复核任务" })).toContainText("6 项待抽检");
  await expect(page.getByRole("region", { name: "来源复核任务" })).toContainText("2 项尚未关联章节");
  await expect(page.getByRole("listitem").filter({ hasText: "ALG-12-A" }).getByRole("link", { name: "打开来源复核" })).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "ALG-13-B" }).getByRole("link", { name: "打开来源复核" })).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "ALG-13-B" })).toContainText("待复核");

  await page.getByRole("listitem").filter({ hasText: "ALG-12-A" }).getByRole("link", { name: "打开来源复核" }).click();
  await expect(page).toHaveURL(/\/studio\/chapters\/chapter-algebra\?.*sourceReviewCaseId=chapter-case/);
  await expect.poll(() => fixture.inboxRequests.at(-1)?.searchParams.get("chapterId")).toBe("chapter-algebra");
  await expect.poll(() => fixture.inboxRequests.at(-1)?.searchParams.get("includeReviewed")).toBe("true");
  await expect(page.getByRole("list", { name: "课程制作进度" })).toContainText("2 复核内容");
  await expect(page.getByRole("region", { name: "教材来源复核" })).toBeVisible();
  await expect(page.getByRole("img", { name: "教材 PDF 第 12 页" })).toBeVisible();
  const sourceReview = page.getByRole("region", { name: "教材来源复核" });
  await expect(sourceReview.locator(".quality-review-question")).toHaveText("Why do we subtract 37 from both sides of the equation?");
  await expect(sourceReview.locator(".quality-review-passage")).toHaveText("x + 37 = 52");
  await expect(page.getByRole("heading", { name: "预期结果" })).toBeVisible();
  await expect(page.getByText("Keep the equation balanced.", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "实际观察" })).toBeVisible();
  await expect(page.getByText("实际响应：Subtract 37 from both sides to keep the equation balanced.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "发布课程" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "复核来源案例 ALG-13-B" })).toContainText("人工要求补充");
  await page.getByLabel(/复核理由/).fill("原页、输入、预期和实际观察一致。接受这个来源案例。");
  await page.getByLabel(/原文核对摘录/).fill("Subtract 37 from both sides of the equation.");
  await page.getByRole("button", { name: "提交最终单人决定" }).click();
  await expect(page.getByRole("status").filter({ hasText: "教材决定已保存" })).toBeVisible();
  await expect(page.getByRole("button", { name: "发布课程" })).toBeDisabled();
  await page.getByRole("button", { name: /我的教材/ }).click();
  await expect(page.getByRole("heading", { name: "我的教材" })).toBeVisible();
  await page.getByRole("button", { name: /待处理/ }).click();
  await page.getByText(/来源质量抽检（\d+ 项，可选）/).click();
  await expect(page.getByRole("button", { name: "待处理（1）" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "课程：1 章待复核" })).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "ALG-13-B" }).getByRole("link", { name: "打开来源复核" })).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "ALG-12-A" }).getByRole("link", { name: "打开来源复核" })).toHaveCount(0);
  expect(fixture.getPublicationRequests()).toBe(0);
  expect(fixture.chapter.status).toBe("in_review");
});

test("user Given unlinked legacy cases share a display title but have distinct source identities When the teacher opens a case and refreshes its deep link Then the original frozen review and saved decision are restored without merging sources", async ({ page }) => {
  await installMaterialReviewRoutes(page);
  await page.goto("/studio");
  await page.getByRole("button", { name: /待处理/ }).click();
  await page.getByText(/来源质量抽检（\d+ 项，可选）/).click();
  await expect(page.getByText("未关联教材的来源包").first()).toBeVisible();
  await expect(page.getByText("未关联教材的来源包")).toHaveCount(2);
  await expect(page.getByRole("listitem").filter({ hasText: "LEGACY-9-A" }).getByRole("link", { name: "打开来源复核" })).toBeVisible();
  await expect(page.getByRole("listitem").filter({ hasText: "HOMONYM-4-A" }).getByRole("link", { name: "打开来源复核" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Legacy Workbook", exact: true })).toHaveCount(2);
  await page.getByRole("listitem").filter({ hasText: "LEGACY-9-A" }).getByRole("link", { name: "打开来源复核" }).click();
  await expect(page).toHaveURL(/\/studio\/quality-review\?.*packageId=legacy-package.*caseId=legacy-case.*returnTo=%2Fstudio/);
  await expect(page.getByRole("img", { name: "教材 PDF 第 9 页" })).toBeVisible();
  await expect(page.getByRole("region", { name: "教材来源复核" }).locator(".quality-review-question")).toHaveText("Which operation keeps both sides equal?");
  await page.getByLabel(/复核理由/).fill("原页和冻结案例一致，记录最终意见。");
  await page.getByLabel(/原文核对摘录/).fill("Example source line");
  await page.getByRole("button", { name: "提交最终单人决定" }).click();
  await expect(page.getByRole("status").filter({ hasText: "教材决定已保存" })).toBeVisible();
  await page.reload();
  await expect(page).toHaveURL(/caseId=legacy-case/);
  await expect(page.getByRole("heading", { name: "LEGACY-9-A" })).toBeVisible();
  await expect(page.getByRole("region", { name: "教材来源复核" }).locator(".quality-review-source .quality-review-status")).toContainText("人工已接受");
  await expect(page.getByLabel("来源复核 JSON")).toHaveCount(0);
});

test("user Given cases that match a chapter or a source separately When the teacher applies both inbox filters Then only cases matching both contexts remain", async ({ page }) => {
  await installMaterialReviewRoutes(page);
  await page.goto("/studio/quality-review");
  await page.getByLabel("按教材或来源筛选").selectOption(`sha256:${"a".repeat(64)}`);
  await page.getByLabel("按章节筛选").selectOption("chapter-algebra");
  await expect(page.getByRole("button", { name: "复核来源案例 ALG-12-A" })).toBeVisible();
  await expect(page.getByRole("button", { name: "复核来源案例 ALG-13-B" })).toBeVisible();
  await expect(page.getByRole("button", { name: "复核来源案例 OTHER-14-C" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "复核来源案例 ALG-22-D" })).toHaveCount(0);
});

test("user Given ordinary source review with no model execution When the teacher opens the dedicated model evaluation tab Then a blinded historical run waits for the human rating without starting another run", async ({ page }) => {
  const fixture = await installMaterialReviewRoutes(page);
  await page.goto("/studio/quality-review?packageId=legacy-package&caseId=legacy-case&returnTo=%2Fstudio");
  await expect(page.getByRole("heading", { name: "LEGACY-9-A" })).toBeVisible();
  await expect(page.getByLabel("来源复核 JSON")).toHaveCount(0);
  await expect(page.getByLabel("基线模型")).toHaveCount(0);
  await expect(page.getByLabel("候选模型")).toHaveCount(0);
  expect(fixture.getRequests.some((url) => url.pathname === "/api/quality-review-runs/models")).toBe(false);
  await expect(page).not.toHaveURL(/tab=evaluation/);

  await page.goto("/studio/metrics?tab=evaluation");
  await expect(page.getByRole("tab", { name: "模型评测" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "模型对比与人工评分" })).toBeVisible();
  await expect(page.locator(".quality-runs-frozen-case")).toContainText("Which operation keeps both sides equal?");
  await expect(page.getByRole("heading", { name: "提交你的匿名评分" })).toBeVisible();
  await expect(page.getByRole("button", { name: "启动有界运行" })).toBeDisabled();
  expect(fixture.postRequests.filter((item) => item.path === "/api/quality-review-runs")).toHaveLength(0);
});

test("user Given a published course with a video awaiting review When the teacher opens it from the review inbox and accepts the source case Then the video remains on its publication snapshot and awaits a separate media decision", async ({ page }) => {
  const mediaTask = {
    mediaId: "media-algebra-v4", chapterId: "chapter-algebra", publicationId: "publication-algebra-v4",
    lessonId: "lesson-algebra-1", version: 3, title: "保持等式平衡讲解", status: "consistency_review_required",
    uploadId: "upload-algebra", sourceRevisionId: "revision-algebra-1", sourceKey: `sha256:${"a".repeat(64)}`,
  };
  const fixture = await installMaterialReviewRoutes(page, { mediaTasks: [mediaTask], publishedChapter: true });
  await page.goto("/studio");
  await page.getByRole("button", { name: /待处理/ }).click();
  await page.getByText(/来源质量抽检（\d+ 项，可选）/).click();
  await expect(page.getByText("教学短视频待审（1）")).toBeVisible();
  await page.getByRole("link", { name: "打开视频审校" }).click();
  await expect(page).toHaveURL(/\/studio\/chapters\/chapter-algebra\?mediaReviewPublicationId=publication-algebra-v4&mediaReviewLessonId=lesson-algebra-1/);
  await expect(page.getByRole("heading", { name: "视频来源课程发布版本 4 · publication-algebra-v4" })).toBeVisible();
  await expect(page.locator("body")).toContainText("publication-algebra-v4");
  const mediaPanel = page.getByRole("region", { name: "教学短视频工作台" });
  await expect(mediaPanel).toContainText("consistency_review_required");
  await expect(mediaPanel.getByRole("button", { name: "批准此版本" })).toBeVisible();

  const sourcePanel = page.getByRole("region", { name: "教材来源复核" });
  await sourcePanel.getByLabel(/复核理由/).fill("来源原文与案例一致；这条意见只针对教材来源。");
  await sourcePanel.getByLabel(/原文核对摘录/).fill("Subtract 37 from both sides of the equation.");
  await sourcePanel.getByRole("button", { name: "提交最终单人决定" }).click();
  await expect(sourcePanel.getByRole("status").filter({ hasText: "教材决定已保存" })).toBeVisible();
  await expect(mediaPanel).toContainText("consistency_review_required");
  expect(fixture.postRequests.filter((item) => item.path.startsWith("/api/lesson-media/")).map((item) => item.path)).toEqual([]);
  expect(fixture.getPublicationRequests()).toBe(0);
});
