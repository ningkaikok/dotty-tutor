import { expect, test, type Page } from "@playwright/test";

test("user Given a textbook PDF When uploading from the single entry Then type detection and first-five course preparation require no options", async ({ page }) => {
  const result = { uploadId: "automatic-source", importId: "book", filename: "English textbook.pdf", contentType: "application/pdf", size: 20, stored: true,
    materialKind: "textbook", detectionReason: "识别到教材标题", stages: [{ id: "ocr", label: "识别教材原文", status: "done" }],
    ocrRun: { requestedProvider: "pypdf", provider: "pypdf", mode: "text", fallback: false, output: "Unit 1 Hello" },
    questionPayloads: [], extraction: { chapter: "教材课程", knowledgePoint: "自动识别章节", pageCount: 20, questionCount: 0, formulaCount: 0, guideCardCount: 0, confidence: 0, mode: "source-only" } };
  const chapters = Array.from({ length: 5 }, (_, index) => ({ chapterId: `course-${index + 1}`, title: `Unit ${index + 1}`, pageStart: index * 3 + 1, pageEnd: index * 3 + 3 }));
  const task = { uploadId: "automatic-source", filename: result.filename, size: 20, chunkSize: 5242880, totalChunks: 1, uploadedChunks: [], status: "uploading", progress: 0, message: "上传中", elapsedSeconds: 0 };
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (!url.pathname.startsWith("/api/")) return route.continue();
    if (url.pathname === "/api/auth/config") return route.fulfill({ json: { protected: false } });
    if (url.pathname === "/api/models" || url.pathname === "/api/tutor-models") return route.fulfill({ json: { selected: { provider: "mock", model: "fixture" }, providers: [] } });
    if (url.pathname === "/api/review-models") return route.fulfill({ json: { selected: { provider: "mock", model: "fixture" }, providers: [] } });
    if (url.pathname === "/api/ocr") return route.fulfill({ json: { selected: "pypdf", effective: "pypdf", providers: [] } });
    if (url.pathname === "/api/uploads/init") return route.fulfill({ json: task });
    if (url.pathname.endsWith("/chunks/0")) return route.fulfill({ json: { ...task, uploadedChunks: [0] } });
    if (url.pathname.endsWith("/complete")) {
      expect(url.searchParams.get("autoDetect")).toBe("true");
      return route.fulfill({ status: 202, json: { jobId: "upload", status: "succeeded", result } });
    }
    if (url.pathname === "/api/chapters/from-upload/automatic-source") return route.fulfill({ status: 202, json: { jobId: "prepare", status: "queued", message: "正在识别章节" } });
    if (url.pathname === "/api/jobs/prepare") return route.fulfill({ json: { jobId: "prepare", status: "succeeded", result: { chapters, notices: [], chapterLimit: 5 } } });
    return route.fulfill({ json: { items: [] } });
  });
  await page.goto("/studio");
  await page.getByRole("link", { name: "＋ 上传试卷或教材" }).click();
  await expect(page.getByRole("heading", { name: "上传试卷或教材" })).toBeVisible();
  await expect(page.getByRole("combobox")).toHaveCount(0);
  await page.locator('input[type="file"]').setInputFiles({ name: result.filename, mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n%%EOF\n") });
  await page.getByRole("button", { name: "开始识别 1 个文件" }).click();
  await expect(page.getByText(/自动识别：教材/)).toBeVisible();
  await page.getByRole("link", { name: "自动制作课程（最多前 5 章） →" }).click();
  await expect(page.getByRole("heading", { name: "已安排 5 章课程草稿" })).toBeVisible();
  await expect(page.getByLabel("课程名称")).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Unit 5/ })).toHaveAttribute("href", "/studio/chapters/course-5");
  await page.screenshot({ path: "/tmp/dotty-unified-upload-courses.png", fullPage: true });
});

function chapterFixture(subject: "math" | "english", chapterId = "chapter-1") {
  const sourceRevisionId = "source-revision-1";
  const page = subject === "math"
    ? { page: 12, text: "函数 y=2x+1 中，当 x=1 时，y=3。", regions: [{ regionId: "region-12-a", x: 0.1, y: 0.2, width: 0.4, height: 0.2 }], flags: [] }
    : { page: 4, text: "They went to the park.\nThey ate lunch there.", regions: [], flags: [], sentences: [{ sentenceId: "sentence-right", text: "They went to the park." }, { sentenceId: "sentence-wrong", text: "They ate lunch there." }] };
  const lesson = {
    lessonId: "lesson-1",
    title: subject === "math" ? "函数代入" : "A day outdoors",
    version: 1,
    status: "in_review",
    sourceRevisionId,
    sourceLocator: { sourceRevisionId, page: page.page, regions: page.regions },
    reviewIssues: [],
    blocks: [{ id: "concept-1", title: "核心概念", type: "markdown", payload: { markdown: "先回到来源页，确认信息后再回答。" } }],
    questionPayload: { question: {
      id: "lesson-1",
      prompt: subject === "math" ? "函数 y=2x+1，当 x=1 时 y 等于多少？" : "Where did they go?",
      questionType: subject === "math" ? "numeric" : "short-answer",
      answerSpec: subject === "math" ? { expected: "3" } : undefined,
      acceptedAnswers: subject === "english" ? ["the park"] : undefined,
      questionKind: subject === "english" ? "explicit" : undefined,
      answerMode: subject === "english" ? "objective" : undefined,
      requiredEvidenceRefs: subject === "english" ? [{ sourceRevisionId, page: page.page, sentenceId: "sentence-right", quote: "They went to the park." }] : undefined,
    } },
    evidenceOptions: subject === "english" ? [
      { sourceRevisionId, page: page.page, sentenceId: "sentence-right", quote: "They went to the park.", label: "They went to the park." },
      { sourceRevisionId, page: page.page, sentenceId: "sentence-wrong", quote: "They ate lunch there.", label: "They ate lunch there." },
    ] : [{ sourceRevisionId, page: page.page, regionId: "region-12-a", quote: "函数 y=2x+1 中，当 x=1 时，y=3。", label: "函数 y=2x+1 中，当 x=1 时，y=3。" }],
  };
  const source = {
    sourceRevisionId,
    fingerprint: "fingerprint-abc123",
    sourceVersion: "edition-1",
    license: "校内授权",
    pageStart: page.page,
    pageEnd: page.page,
    pages: [page],
    issues: [],
  };
  return {
    chapter: {
      chapterId,
      subject,
      title: subject === "math" ? "函数代入" : "A day outdoors",
      status: "draft",
      version: 1,
      recordVersion: 1,
      sourceRevisions: [source],
      lessons: [] as typeof lesson[],
      currentLessonIds: [] as string[],
      reviewIssues: [] as { code: string; message: string }[],
      publicationId: null as string | null,
      publications: [] as { publicationId: string; version: number; sourceRevisionId: string; publishedAt: number }[],
    },
    lesson,
    source,
    page,
  };
}

async function routeChapterApi(page: Page, subject: "math" | "english") {
  const fixture = chapterFixture(subject);
  let published = false;
  let attempt: Record<string, unknown> | null = null;
  await page.route("**/api/auth/config", (route) => route.fulfill({ json: { protected: false } }));
  await page.route("**/api/library", (route) => route.fulfill({ json: { items: [] } }));
  await page.route(/\/api\/chapters(?:\/[^?]*)?(?:\?.*)?$/, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const path = url.pathname;
    if (path.endsWith("/preview") && method === "GET") return route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/x2cAAAAASUVORK5CYII=", "base64") });
    if (path === "/api/chapters" && method === "GET") return route.fulfill({ json: { items: [] } });
    if (path === "/api/chapters" && method === "POST") return route.fulfill({ status: 201, json: fixture.chapter });
    if (path.endsWith("/generate") && method === "POST") {
      fixture.chapter.lessons = [fixture.lesson];
      fixture.chapter.currentLessonIds = [fixture.lesson.lessonId];
      fixture.chapter.status = "in_review";
      fixture.chapter.recordVersion += 1;
      return route.fulfill({ json: fixture.chapter });
    }
    if (path.endsWith("/review") && method === "PATCH") {
      fixture.lesson.status = "approved";
      fixture.chapter.recordVersion += 1;
      return route.fulfill({ json: fixture.chapter });
    }
    if (path.endsWith("/publish") && method === "POST") {
      published = true;
      fixture.chapter.status = "published";
      fixture.chapter.publicationId = "publication-1";
      fixture.chapter.publications = [{ publicationId: "publication-1", version: 1, sourceRevisionId: fixture.source.sourceRevisionId, publishedAt: 1 }];
      return route.fulfill({ status: 201, json: { chapterId: fixture.chapter.chapterId, publicationId: "publication-1", version: 1, status: "published" } });
    }
    if (path.endsWith("/published") && method === "GET") {
      return route.fulfill({ json: {
        chapterId: fixture.chapter.chapterId,
        subject,
        title: fixture.chapter.title,
        publicationId: url.searchParams.get("publicationId") || "publication-1",
        version: 1,
        status: "published",
        lessons: [{ ...fixture.lesson, status: "published", questionPayload: { question: {
          id: fixture.lesson.questionPayload.question.id,
          prompt: fixture.lesson.questionPayload.question.prompt,
          questionType: fixture.lesson.questionPayload.question.questionType,
        } } }],
      } });
    }
    if (path.endsWith("/attempts") && method === "POST") {
      const body = request.postDataJSON() as { attemptId: string; lessonId: string; publicationId?: string; answer: { text?: string }; evidenceRefs: Array<{ sentenceId?: string }> };
      const mismatch = subject === "english" && body.evidenceRefs[0]?.sentenceId !== "sentence-right";
      attempt = {
        attemptId: body.attemptId,
        publicationId: body.publicationId || "publication-1",
        lessonId: body.lessonId,
        learnerId: "local-demo",
        answer: body.answer,
        evidenceRefs: body.evidenceRefs,
        assessment: mismatch ? "needs_review" : "correct",
        evidenceVerdict: mismatch ? "mismatch" : "supported",
        feedback: { message: mismatch ? "答案或原文依据需要教师复核。" : "答案与所选依据一致。" },
      };
      return route.fulfill({ json: attempt });
    }
    if (/\/attempts\/[^/]+$/.test(path) && method === "GET") return route.fulfill({ json: attempt || { detail: "attempt not found" }, status: attempt ? 200 : 404 });
    if (path === `/api/chapters/${fixture.chapter.chapterId}` && method === "GET") return route.fulfill({ json: fixture.chapter });
    return route.fulfill({ status: 404, json: { detail: `Unhandled route ${method} ${path} (published=${published})` } });
  });
  return fixture;
}

test("user Given a chapter page backed by an uploaded source When a teacher opens its review Then the original-page endpoint is shown with the matching region overlay", async ({ page }) => {
  const fixture = await routeChapterApi(page, "math");
  Object.assign(fixture.source.pages[0], { previewUrl: "/api/chapters/chapter-1/sources/source-revision-1/pages/12/preview" });
  fixture.source.pages[0].text = "来源页含有需要逐段核对的公式、条件和例题文字。".repeat(80);
  await page.goto("/studio/chapters/chapter-1");
  await page.getByRole("button", { name: "查看教材原文与页码" }).click();
  await expect(page.getByRole("img", { name: "教材原页，第 12 页" })).toHaveAttribute("src", /\/preview$/);
  await expect(page.getByRole("group", { name: "第 12 页原图区域定位" })).toBeVisible();
  const region = page.getByRole("button", { name: "回看第 12 页原图区域 1" });
  await region.click();
  await page.evaluate(() => new Promise<void>((resolve) => {
    let previous = window.scrollY;
    let stableFrames = 0;
    const settle = () => {
      stableFrames = window.scrollY === previous ? stableFrames + 1 : 0;
      previous = window.scrollY;
      if (stableFrames >= 5) resolve();
      else requestAnimationFrame(settle);
    };
    requestAnimationFrame(settle);
  }));
  await expect(region).toBeInViewport();
  await page.getByRole("button", { name: "放大查看原页" }).click();
  await expect(page.getByRole("dialog", { name: "教材原页，第 12 页" })).toBeVisible();
  await page.screenshot({ path: "/tmp/dotty-chapter-source-preview.png", fullPage: true });
});

test("user Given a math chapter source When a teacher reviews and publishes it Then the student can answer with evidence and restore the attempt after refresh", async ({ page }) => {
  await routeChapterApi(page, "math");
  await page.goto("/");
  await page.getByRole("button", { name: "打开我的教材" }).click();
  await expect(page.getByRole("heading", { name: "我的教材" })).toBeVisible();
  await page.goto("/studio/chapters/new");
  await expect(page.getByRole("heading", { name: "制作一节课程" })).toBeVisible();
  await page.getByLabel("课程名称").fill("函数代入");
  await page.getByLabel("起始页").fill("12");
  await page.getByLabel("结束页").fill("12");
  await page.getByLabel("来源许可说明").fill("校内授权");
  await page.getByRole("textbox", { name: "OCR 原文" }).fill("函数 y=2x+1 中，当 x=1 时，y=3。");
  await page.getByRole("button", { name: "保存并继续" }).click();
  await page.getByText("其他制作方式与来源设置", { exact: true }).click();
  await page.getByRole("button", { name: "模板生成课程草稿" }).click();
  await expect(page.getByText("函数 y=2x+1，当 x=1 时 y 等于多少？")).toBeVisible();
  await page.screenshot({ path: "/tmp/dotty-chapter-workbench.png", fullPage: true });
  await page.getByRole("button", { name: "确认已复核" }).click();
  await expect(page.getByText("已审核", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "发布课程" }).click();
  await page.getByRole("button", { name: "学生端预览" }).click();
  await expect(page.getByRole("heading", { name: "函数 y=2x+1，当 x=1 时 y 等于多少？" })).toBeVisible();
  await page.screenshot({ path: "/tmp/dotty-chapter-student.png", fullPage: true });
  await page.getByLabel("你的答案").fill("3");
  await page.getByLabel(/第 12 页/).check();
  await page.getByRole("button", { name: "提交答案与依据" }).click();
  await expect(page.getByText("答案与依据均匹配")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("你的答案")).toHaveValue("3");
  await expect(page.getByText("答案与依据均匹配")).toBeVisible();
});

test("user Given an English answer cites the wrong sentence When it is submitted Then the feedback identifies the evidence mismatch", async ({ page }) => {
  await routeChapterApi(page, "english");
  await page.goto("/learn/chapters/chapter-1");
  await expect(page.getByRole("heading", { name: "Where did they go?" })).toBeVisible();
  await page.getByLabel("你的答案").fill("the park");
  await page.getByLabel(/They ate lunch there/).check();
  await page.getByRole("button", { name: "提交答案与依据" }).click();
  await expect(page.getByText("待教师复核")).toBeVisible();
  await expect(page.getByText("答案或原文依据需要教师复核。")).toBeVisible();
  await expect(page.getByText("与题目要求不匹配")).toBeVisible();
});


test("user Given a PDF and an English course When using the unified library Then both appear and PDF course creation carries its source without exposing advanced fields", async ({ page }) => {
  const fixture = await routeChapterApi(page, "english");
  const uploads = [{ uploadId: "pdf-1", filename: "数学教材.pdf", pageCount: 12, questionCount: 4, status: "complete" }];
  await page.route("**/api/library", (route) => route.fulfill({ json: { items: uploads } }));
  await page.route("**/api/chapters", (route) => route.fulfill({ json: { items: [{ ...fixture.chapter, currentLessonIds: ["lesson-1"], status: "in_review" }] } }));
  await page.goto("/studio");
  await expect(page.getByRole("heading", { name: "A day outdoors" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "数学教材.pdf" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { name: "A day outdoors" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "/tmp/dotty-materials-mobile.png", fullPage: true });

  await expect(page.getByRole("link", { name: "＋ 上传试卷或教材" })).toBeVisible();
  const courses = Array.from({ length: 5 }, (_, index) => ({ chapterId: `automatic-${index + 1}`, title: `Unit ${index + 1}`, pageStart: index * 2 + 1, pageEnd: index * 2 + 2 }));
  await page.route("**/api/chapters/from-upload/pdf-1", (route) => route.fulfill({ status: 202, json: { jobId: "automatic-job", status: "succeeded", result: { chapters: courses, notices: [], chapterLimit: 5 } } }));
  await page.getByRole("link", { name: "自动制作课程" }).click();
  await expect(page.getByRole("heading", { name: "自动制作教材教程" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "已安排 5 章课程草稿" })).toBeVisible();
  await expect(page.getByLabel("起始页")).toHaveCount(0);
  await expect(page.getByLabel("课程名称")).toHaveCount(0);
  await expect(page.getByRole("link", { name: /Unit 5/ })).toHaveAttribute("href", "/studio/chapters/automatic-5");
  await expect(page.getByRole("link", { name: /Unit 6/ })).toHaveCount(0);
  await page.screenshot({ path: "/tmp/dotty-automatic-courses-mobile.png", fullPage: true });
  await page.goto("/studio/chapters");
  await expect(page.getByRole("heading", { name: "我的教材" })).toBeVisible();
});

test("user Given a textbook tutorial When opening its directory and published chapter Then teaching plays without reading questions", async ({ page }) => {
  const chapter = { chapterId: "tutorial", title: "Chapter 1 Writing", uploadId: "writing-book", pageStart: 1, subject: "english", teachingMode: "tutorial", status: "published", version: 1, recordVersion: 2, currentLessonIds: ["teaching"], reviewIssues: [], publicationId: "tutorial-publication" };
  const lesson = { lessonId: "teaching", title: "Writing a story", version: 1, status: "published", knowledgePoints: ["Writing"], sourceRevisionId: "source", sourceLocator: { sourceRevisionId: "source", page: 1, regions: [] }, evidenceOptions: [], questionPayload: { question: null }, blocks: [{ id: "explanation", type: "markdown", title: "讲解", payload: { markdown: "用具体细节描述人物。" } }] };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    if (path === "/api/auth/config") return route.fulfill({ json: { protected: false } });
    if (path === "/api/library") return route.fulfill({ json: { items: [{ uploadId: "writing-book", filename: "Writing Tutorial.pdf", materialKind: "textbook", questionCount: 0, pageCount: 10 }] } });
    if (path === "/api/chapters") return route.fulfill({ json: { items: [chapter] } });
    if (path === "/api/chapters/tutorial") return route.fulfill({ json: { ...chapter, status: "in_review", sourceRevisions: [], publications: [], lessons: [{ ...lesson, status: "in_review", reviewIssues: [] }] } });
    if (path === "/api/chapters/tutorial/published") return route.fulfill({ json: { ...chapter, lessons: [lesson] } });
    return route.fulfill({ json: { items: [] } });
  });
  await page.goto("/studio");
  await expect(page.getByRole("heading", { name: "Writing Tutorial.pdf" })).toBeVisible();
  await expect(page.getByRole("article")).toHaveCount(1);
  await page.getByText("查看教程目录").click();
  await page.getByRole("link", { name: "Chapter 1 Writing", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认已复核" })).toBeEnabled();
  await expect(page.getByText("确认教程讲解与引用已经人工复核后，发布按钮才会启用。")).toBeVisible();
  await expect(page.getByRole("heading", { name: "检查题", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "← 我的教材" }).click();
  await page.getByText("查看教程目录").click();
  await page.getByRole("link", { name: "学习本章" }).click();
  await expect(page.getByRole("heading", { name: "Chapter 1 Writing" })).toBeVisible();
  await expect(page.getByRole("article").getByText("用具体细节描述人物。")).toBeVisible();
  await expect(page.getByRole("button", { name: /提交/ })).toHaveCount(0);
  await expect(page.getByText("英语阅读")).toHaveCount(0);
  await page.screenshot({ path: "/tmp/dotty-a199-tutorial-player.png", fullPage: true });
});
