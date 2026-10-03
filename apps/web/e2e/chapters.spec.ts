import { expect, test, type Page } from "@playwright/test";

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

test("user Given a math chapter source When a teacher reviews and publishes it Then the student can answer with evidence and restore the attempt after refresh", async ({ page }) => {
  await routeChapterApi(page, "math");
  await page.goto("/");
  await page.getByRole("button", { name: "章节课程与英语阅读" }).click();
  await expect(page.getByRole("heading", { name: "从来源页制作互动章节" })).toBeVisible();
  await page.getByLabel("章节名称").fill("函数代入");
  await page.getByLabel("起始页").fill("12");
  await page.getByLabel("结束页").fill("12");
  await page.getByLabel("来源许可说明").fill("校内授权");
  await page.getByRole("textbox", { name: "OCR 原文" }).fill("函数 y=2x+1 中，当 x=1 时，y=3。");
  await page.getByRole("button", { name: "创建章节草稿" }).click();
  await page.getByRole("button", { name: "生成课程草稿" }).click();
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
