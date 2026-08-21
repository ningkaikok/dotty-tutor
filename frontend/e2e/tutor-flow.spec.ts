import { test, expect, type Locator, type Page } from "@playwright/test";

const TINY_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNg+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
);

const modelRun = {
  requestedProvider: "mock",
  provider: "mock",
  model: "playwright-fixture",
  fallback: false,
};

const review = {
  status: "reviewed",
  needsHumanReview: false,
  text: { verdict: "pass", corrections: [], issues: [], confidence: 99 },
  vision: { imageAssessments: [], issues: [], confidence: 99 },
  textModelRun: modelRun,
  visionModelRun: modelRun,
};

const quality = {
  status: "ready",
  errors: [],
  warnings: [],
  validatorVersion: "playwright-fixture",
  validatedAt: 0,
};

function lessonSteps(topic: string) {
  return [{
    id: `${topic}-step-1`,
    title: "先观察题目条件",
    text: "先找出题目中最重要的关系：$x-1$。",
    speechText: "方程两边同乘 $x-1$，并保持等式两边的每一项都参与相乘。",
    action: "show-base",
  }];
}

function withContentBlocks(question: Record<string, unknown>) {
  if (question.contentBlocks) return question;
  const id = String(question.id);
  const blocks: Array<Record<string, unknown>> = [
    { id: `${id}-prompt`, type: "text", text: String(question.prompt), sourceOrder: 0 },
  ];
  const imageUrls = Array.isArray(question.imageUrls) ? question.imageUrls : [];
  imageUrls.forEach((url, index) => blocks.push({
    id: `${id}-image-${index}`,
    type: "image",
    url,
    assetId: url,
    sourceReference: url,
    role: "stem",
    sourceOrder: index + 1,
  }));
  const options = Array.isArray(question.options) ? question.options : [];
  if (options.length) {
    const optionImages = Array.isArray(question.optionImageUrls) ? question.optionImageUrls : [];
    blocks.push({
      id: `${id}-options`,
      type: "options",
      sourceOrder: blocks.length,
      items: options.map((option, index) => ({
        label: `(${String.fromCharCode(65 + index)})`,
        contentBlocks: [{
          id: `${id}-option-${index}`,
          type: "text",
          text: String(option).replace(/^(?:\([A-D]\)|[A-D][.．:：、])\s*/, "").trim(),
          sourceOrder: 0,
        }],
        ...(optionImages[index] ? { imageUrl: optionImages[index], assetId: optionImages[index] } : {}),
      })),
    });
  }
  return { ...question, contentBlocks: blocks };
}

function payload(question: Record<string, unknown>) {
  const currentQuestion = withContentBlocks(question);
  const topic = String(currentQuestion.knowledgePoint);
  return {
    question: currentQuestion,
    lessonSteps: lessonSteps(topic),
    architecture: { source: "playwright-fixture" },
    modelRun,
    review,
    quality,
  };
}

const choiceQuestion = payload({
  id: "pw-choice",
  questionType: "choice",
  chapter: "第一章",
  knowledgePoint: "数轴上的大小比较",
  questionNumber: "1",
  prompt: "在数轴上，比较下列各数的大小。",
  givens: ["分式为 $\\frac{1}{x+2}$"],
  options: ["(A) -2 > 1", "(B) -2 < 1"],
});

const trueFalseQuestion = payload({
  id: "pw-true-false",
  questionType: "true-false",
  chapter: "第一章",
  knowledgePoint: "有理数判断",
  questionNumber: "2",
  prompt: "负数都小于零。",
  givens: ["判断命题真假"],
});

const drawLineQuestion = payload({
  id: "pw-draw-line",
  questionType: "draw-line",
  chapter: "第一章",
  knowledgePoint: "对应点连线",
  questionNumber: "3",
  prompt: "把相互对应的点连起来。",
  givens: ["点击两个端点完成连线"],
  interaction: {
    type: "draw-line",
    instruction: "先点击左侧端点，再点击右侧端点。",
    points: [
      { id: "left", label: "A", x: 0.2, y: 0.5 },
      { id: "right", label: "B", x: 0.8, y: 0.5 },
    ],
    requiredConnections: [["left", "right"]],
  },
});

const multiSelectQuestion = payload({
  id: "pw-multi-select",
  questionType: "multi-select",
  chapter: "第二章",
  knowledgePoint: "集合交集",
  questionNumber: "4",
  prompt: "下列选项中属于集合 A 的元素有（多选）。",
  givens: ["选择所有正确选项"],
  options: ["(A) 1", "(B) 2", "(C) 3"],
  correctAnswers: ["(A)", "(C)"],
});

const fillBlankQuestion = payload({
  id: "pw-fill-blank",
  questionType: "fill-blank",
  chapter: "第二章",
  knowledgePoint: "基础运算",
  questionNumber: "5",
  prompt: "填空：2 + 2 = ____。",
  givens: ["填写最终结果"],
  blanks: [{ id: "blank-1", label: "第 1 空", answerType: "numeric", correctAnswers: ["4"] }],
});

const numericQuestion = payload({
  id: "pw-numeric",
  questionType: "numeric",
  chapter: "第二章",
  knowledgePoint: "近似值",
  questionNumber: "6",
  prompt: "π 取两位小数约为多少？",
  givens: ["答案允许误差 0.01"],
  answerSpec: { answerType: "numeric", expected: "3.14", tolerance: 0.01, unit: "" },
});

const importResult = {
  importId: "pw-import",
  filename: "playwright-fixture.png",
  contentType: "image/png",
  size: 128,
  stored: false,
  modelRun,
  ocrRun: {
    requestedProvider: "mock",
    provider: "mock",
    mode: "fixture",
    fallback: false,
    output: "fixture",
  },
  stages: [
    { id: "validate", label: "文件校验", status: "done" },
    { id: "structure", label: "题目结构化", status: "done" },
  ],
  extraction: {
    chapter: "第一章",
    knowledgePoint: "数轴上的大小比较",
    questionCount: 3,
    formulaCount: 0,
    guideCardCount: 1,
    confidence: 99,
    mode: "fixture",
  },
  questionPayload: choiceQuestion,
  questionPayloads: [choiceQuestion, trueFalseQuestion, drawLineQuestion],
};

const priorityImportResult = {
  ...importResult,
  importId: "pw-priority-import",
  filename: "playwright-priority-fixture.png",
  extraction: { ...importResult.extraction, knowledgePoint: "第一优先级题型", questionCount: 3 },
  questionPayload: multiSelectQuestion,
  questionPayloads: [multiSelectQuestion, fillBlankQuestion, numericQuestion],
};

// Fixture for the current structured image and option rendering contract.
const renderingQuestion = payload({
  id: "pw-rendering",
  questionType: "choice",
  chapter: "图文题",
  knowledgePoint: "公式与题图",
  questionNumber: "7",
  prompt: "观察题干图，计算 $\\frac{1}{2} + 1$。",
  givens: ["条件为 $x > 0$"],
  options: ["(A) 1.5", "(B) $x=2$"],
  imageUrls: ["/fixtures/stem.png"],
  optionImageUrls: ["/fixtures/a.png", ""],
  contentBlocks: [
    { id: "pw-rendering-prompt", type: "text", text: "观察题干图，计算 $\\frac{1}{2} + 1$。", sourceOrder: 0 },
    { id: "pw-rendering-stem", type: "image", url: "/fixtures/stem.png", assetId: "/fixtures/stem.png", sourceReference: "stem.png", role: "stem", sourceOrder: 1 },
    { id: "pw-rendering-options", type: "options", sourceOrder: 2, items: [
      { label: "(A)", imageUrl: "/fixtures/a.png", assetId: "/fixtures/a.png", contentBlocks: [{ id: "pw-rendering-a", type: "text", text: "1.5", sourceOrder: 0 }] },
      { label: "(B)", contentBlocks: [{ id: "pw-rendering-b", type: "math", latex: "x=2", display: false, sourceOrder: 0 }] },
    ] },
    // 统计表必须渲染成 <table> 结构，不能退化成原始 HTML 源码文字。
    { id: "pw-rendering-table", type: "table", sourceOrder: 3, rows: [
      { cells: [
        { contentBlocks: [{ id: "pw-rendering-table-h1", type: "text", text: "阅读量/本", sourceOrder: 0 }] },
        { contentBlocks: [{ id: "pw-rendering-table-h2", type: "text", text: "人数", sourceOrder: 0 }] },
      ] },
      { cells: [
        { contentBlocks: [{ id: "pw-rendering-table-c1", type: "text", text: "0", sourceOrder: 0 }] },
        { contentBlocks: [{ id: "pw-rendering-table-c2", type: "text", text: "2", sourceOrder: 0 }] },
      ] },
    ] },
  ],
});

const renderingImportResult = {
  ...importResult,
  importId: "pw-rendering-import",
  questionPayload: renderingQuestion,
  questionPayloads: [renderingQuestion],
};

async function mockApi(page: Page, result = importResult) {
  await mockFixtureImages(page);
  await page.route("**/api/learning/sessions/*/attempts", async (route) => {
    await route.fulfill({
      json: {
        attemptId: "pw-attempt",
        mastery: {
          learnerId: "local-demo",
          knowledgePoint: "数轴上的大小比较",
          score: 0.165,
          attemptCount: 1,
          correctCount: 0,
          lastPracticedAt: 1,
        },
        autoMistake: {
          mistakeId: "paper-mistake-pw-1",
          status: "unmastered",
          contentType: "application/vnd.dotty.publication+json",
        },
      },
    });
  });
  await page.route("**/api/learning/sessions", async (route) => {
    const request = route.request().postDataJSON() as { learnerId: string; publicationId: string };
    await route.fulfill({
      json: {
        sessionId: "pw-session",
        learnerId: request.learnerId,
        publicationId: request.publicationId,
        startedAt: 1,
      },
    });
  });
  await page.route("**/api/learning/mastery/local-demo", async (route) => {
    await route.fulfill({ json: { learnerId: "local-demo", items: [] } });
  });
  await page.route("**/api/models", async (route) => {
    await route.fulfill({
      json: {
        selected: { provider: "mock", model: "playwright-fixture" },
        providers: [{
          id: "mock",
          label: "Mock",
          available: true,
          models: ["playwright-fixture"],
          detail: "Playwright 固定测试模型",
        }],
      },
    });
  });
  await page.route("**/api/ocr", async (route) => {
    await route.fulfill({
      json: {
        selected: "auto",
        effective: "mock",
        providers: [{ id: "auto", label: "自动", available: true, detail: "测试解析" }],
      },
    });
  });
  await page.route("**/api/library", async (route) => {
    await route.fulfill({ json: { items: [] } });
  });
  await page.route("**/api/publications?status=published", async (route) => {
    await route.fulfill({ json: { items: [] } });
  });
  await page.route("**/api/textbook/import", async (route) => {
    await route.fulfill({ json: result });
  });
  await page.route("**/api/help", async (route) => {
    await route.fulfill({
      json: {
        reply: "先比较两个数在数轴上的左右位置，再写出结论。",
        guideContext: { assessment: "partial", hint: "观察数轴" },
        nextHintLevel: 1,
        canvasAction: "show-point-p",
        source: "stored-guide-card",
        modelRun,
      },
    });
  });
  await page.route("**/api/tts", async (route) => {
    await route.fulfill({ status: 503, body: "disabled in e2e" });
  });
}

async function mockFixtureImages(page: Page) {
  await page.route("**/fixtures/*.png", async (route) => {
    await route.fulfill({ status: 200, contentType: "image/png", body: TINY_PNG });
  });
}

async function expectLoadedUniqueImages(images: Locator) {
  await expect.poll(async () => images.evaluateAll((elements) => elements.every((element) => {
    const image = element as HTMLImageElement;
    return image.complete && image.naturalWidth > 0;
  }))).toBe(true);
  const sources = await images.evaluateAll((elements) => elements.map((element) => {
    const image = element as HTMLImageElement;
    return image.currentSrc || image.getAttribute("src") || "";
  }));
  expect(new Set(sources).size).toBe(sources.length);
}

async function mockMistakeApi(page: Page, startConfirmed = false, startVerify = false, richHistory = false) {
  let items: Array<Record<string, unknown>> = [];
  const pendingItem = {
    mistakeId: "mistake-pw-1",
    learnerId: "local-demo",
    sourceFilename: "mistake-fixture.png",
    contentType: "image/png",
    sourceImageUrl: "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==",
    questionPayload: {
      question: {
        id: "mistake-question-pw-1",
        questionType: "short-answer",
        chapter: "一元一次方程",
        knowledgePoint: "移项",
        prompt: "解方程 $x + 1 = 3$",
        givens: [],
        options: [],
        imageUrls: [],
        contentBlocks: [{ id: "mistake-question-pw-1-prompt", type: "text", text: "解方程 $x + 1 = 3$", sourceOrder: 0 }],
      },
      lessonSteps: [],
      architecture: {},
      modelRun,
    },
    guideCards: [],
    ocrRun: { requestedProvider: "manual", provider: "manual", mode: "fixture", fallback: false, output: "text" },
    modelRun,
    originalAnswer: "x = 1",
    subject: "数学",
    gradeBand: "初中",
    chapter: "一元一次方程",
    knowledgePoint: "移项",
    notes: "",
    status: "pending_confirmation",
    createdAt: 1,
    updatedAt: 1,
  };
  const confirmedItem = {
    ...pendingItem,
    errorReason: "calculation",
    status: "unmastered",
    confirmedAt: 2,
    updatedAt: 2,
  };
  if (startConfirmed) items = [confirmedItem];

  let tutorStage = startVerify ? "verify" : "diagnose";
  let tutorMessages: Array<Record<string, unknown>> = richHistory ? [{
    messageId: "history-pw-1", threadId: "thread-pw-1", role: "assistant",
    content: "复习公式 $\\frac{1}{2}$。",
    inputMode: "text", action: {}, modelRun, createdAt: 3.5,
  }] : [];
  let variations: Array<Record<string, unknown>> = [];
  let reviewTasks: Array<Record<string, unknown>> = [];
  const tutorThread = () => ({
    threadId: "thread-pw-1",
    mistakeId: "mistake-pw-1",
    learnerId: "local-demo",
    stage: tutorStage,
    summary: "",
    hintLevel: 0,
    messageCount: tutorMessages.length,
    messages: tutorMessages,
    createdAt: 3,
    updatedAt: 3,
  });

  await page.route("**/api/mistakes?*", async (route) => {
    await route.fulfill({ json: { learnerId: "local-demo", items } });
  });
  await page.route("**/api/mistakes/import", async (route) => {
    items = [pendingItem];
    await route.fulfill({ json: pendingItem });
  });
  await page.route("**/api/mistakes/mistake-pw-1", async (route) => {
    if (route.request().method() === "PATCH") {
      const confirmation = route.request().postDataJSON() as Record<string, unknown>;
      items = [{ ...pendingItem, ...confirmation, status: "unmastered", confirmedAt: 2, updatedAt: 2 }];
      await route.fulfill({ json: items[0] });
      return;
    }
    await route.fulfill({ json: items[0] ?? pendingItem });
  });
  await page.route("**/api/mistakes/mistake-pw-1/archive", async (route) => {
    await route.fulfill({ json: { ...items[0], status: "archived" } });
  });
  await page.route("**/api/mistakes/mistake-pw-1/thread", async (route) => {
    await route.fulfill({ json: tutorThread() });
  });
  await page.route("**/api/tutor/threads/thread-pw-1", async (route) => {
    await route.fulfill({ json: tutorThread() });
  });
  await page.route("**/api/tutor/threads/thread-pw-1/messages", async (route) => {
    const request = route.request().postDataJSON() as { content: string };
    tutorStage = "explain";
    tutorMessages = [
      {
        messageId: "student-pw-1", threadId: "thread-pw-1", role: "student",
        content: request.content, inputMode: "text", action: {}, modelRun: {}, createdAt: 4,
      },
      {
        messageId: "assistant-pw-1", threadId: "thread-pw-1", role: "assistant",
        content: "先检查移项后符号是否改变，再重新算一次。", inputMode: "text",
        assessment: "incorrect", action: {}, modelRun, createdAt: 5,
      },
    ];
    await route.fulfill({
      json: {
        thread: tutorThread(),
        reply: {
          reply: "先检查移项后符号是否改变，再重新算一次。",
          guideContext: { assessment: "incorrect" }, nextHintLevel: 1,
          canvasAction: "show-base", source: "answer-check", modelRun,
        },
        action: {
          type: "advance_stage", previousStage: "diagnose", nextStage: "explain",
          assessment: "incorrect", prompt: "移项时符号如何变化？",
        },
      },
    });
  });
  await page.route("**/api/mistakes/mistake-pw-1/variations", async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { items: variations } });
      return;
    }
    if (variations.length > 0) {
      await route.fulfill({ json: variations[0] });
      return;
    }
    const variation = {
      variationId: `variation-pw-${variations.length + 1}`,
      mistakeId: "mistake-pw-1",
      learnerId: "local-demo",
      strategy: "parallel-calculation",
      level: variations.length ? "parallel" : "foundation",
      sequence: variations.length + 1,
      questionPayload: payload({
        id: `variation-question-${variations.length + 1}`,
        questionType: "choice",
        chapter: "一元一次方程",
        knowledgePoint: "移项",
        prompt: "解方程 $x + 2 = 5$，正确结果是？",
        givens: [],
        options: ["(A) x = 3", "(B) x = 7"],
        correctAnswers: ["A"],
      }),
      modelRun,
      status: "ready",
      response: {},
      feedback: "",
      createdAt: 6,
    };
    variations = [...variations, variation];
    await route.fulfill({ json: variation });
  });
  await page.route("**/api/variations/*/answer", async (route) => {
    const answer = route.request().postDataJSON() as {
      content: string;
      interactionResult: Record<string, unknown>;
    };
    const current = variations[variations.length - 1];
    const selectedOptions = Array.isArray(answer.interactionResult.selectedOptions)
      ? answer.interactionResult.selectedOptions as string[]
      : [];
    const correct = selectedOptions.includes("(A)");
    const mastery = {
      correctStreak: correct ? 1 : 0,
      requiredCorrect: 1,
      mastered: correct,
      answeredCount: 1,
    };
    const answered = {
      ...current,
      status: "answered",
      assessment: correct ? "correct" : "incorrect",
      response: answer,
      feedback: correct ? "回答正确，你已经能独立完成这类移项。" : "请重新检查移项后的结果。",
      answeredAt: 7,
      mastery,
    };
    variations = variations.map((item) => item.variationId === current.variationId ? answered : item);
    if (mastery.mastered) {
      items = items.map((item) => ({ ...item, status: "mastered", updatedAt: 7 }));
      reviewTasks = [1, 3, 7].map((intervalDays, index) => ({
        taskId: `review-pw-${intervalDays}`,
        mistakeId: "mistake-pw-1",
        learnerId: "local-demo",
        intervalDays,
        dueAt: index === 0 ? 900 : 1000 + intervalDays * 86400,
        status: "scheduled",
        modelRun: {},
        response: {},
        feedback: "",
        createdAt: 8,
      }));
    }
    await route.fulfill({ json: answered });
  });
  await page.route("**/api/reviews?*", async (route) => {
    await route.fulfill({ json: { items: reviewTasks, serverTime: 1000 } });
  });
  await page.route("**/api/progress?*", async (route) => {
    const completed = reviewTasks.filter((task) => task.status === "completed");
    const mastered = items.filter((item) => item.status === "mastered").length;
    await route.fulfill({ json: {
      learnerId: "local-demo",
      totalMistakes: items.length,
      masteredCount: mastered,
      masteryRate: items.length ? mastered / items.length : 0,
      dueReviewCount: reviewTasks.filter((task) => task.status !== "completed" && Number(task.dueAt) <= 1000).length,
      completedReviewCount: completed.length,
      reviewAccuracy: completed.length ? 1 : 0,
      knowledgePoints: items.length ? [{ knowledgePoint: "移项", total: 1, mastered }] : [],
    } });
  });
  await page.route("**/api/reviews/*/start", async (route) => {
    const taskId = /\/api\/reviews\/([^/]+)\/start/.exec(route.request().url())?.[1];
    const current = reviewTasks.find((task) => task.taskId === taskId) ?? reviewTasks[0];
    const started = {
      ...current,
      status: "ready",
      questionPayload: payload({
        id: "review-question-pw",
        questionType: "choice",
        chapter: "一元一次方程",
        knowledgePoint: "移项",
        prompt: "复习：解方程 $x + 4 = 9$。",
        givens: [],
        options: ["(A) x = 5", "(B) x = 13"],
        correctAnswers: ["A"],
      }),
      startedAt: 1000,
    };
    reviewTasks = reviewTasks.map((task) => task.taskId === current.taskId ? started : task);
    await route.fulfill({ json: started });
  });
  await page.route("**/api/reviews/*/answer", async (route) => {
    const taskId = /\/api\/reviews\/([^/]+)\/answer/.exec(route.request().url())?.[1];
    const current = reviewTasks.find((task) => task.taskId === taskId) ?? reviewTasks[0];
    const completed = {
      ...current,
      status: "completed",
      assessment: "correct",
      feedback: "复习正确，请继续保持。",
      response: route.request().postDataJSON(),
      completedAt: 1001,
    };
    reviewTasks = reviewTasks.map((task) => task.taskId === current.taskId ? completed : task);
    await route.fulfill({ json: completed });
  });
}

test.describe("产品入口", () => {
  test("学生端与内容生产端边界清晰", async ({ page }) => {
    await mockApi(page);
    await mockMistakeApi(page);
    await page.goto("/");

    await expect(page.getByRole("heading", { name: "选择你的使用入口" })).toBeVisible();
    await page.getByRole("button", { name: "进入学生学习空间" }).click();
    await expect(page).toHaveURL(/\/learn$/);
    await expect(page.getByRole("heading", { name: "直接开始学习" })).toBeVisible();
    await expect(page.locator(".paper-card")).toContainText("已可用");
    await expect(page.getByText("暂无已发布试卷，请先在内容生产端发布。")).toBeVisible();
    await expect(page.getByRole("heading", { name: "上传教材页或整本 PDF" })).toHaveCount(0);

    await page.getByRole("button", { name: "打开我的错题本" }).click();
    await expect(page).toHaveURL(/\/mistakes$/);
    await expect(page.getByRole("heading", { name: "我的错题本", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "← 返回学生空间" })).toBeVisible();

    await page.goto("/studio");
    await expect(page.getByRole("heading", { name: "上传教材页或整本 PDF" })).toBeVisible();
    await expect(page.getByText("内容生产工作台")).toBeVisible();

  });

  test("学生可以打开已发布互动试卷并同步作答", async ({ page }) => {
    await mockApi(page);
    await mockMistakeApi(page);
    const savedAttempts: Record<string, unknown>[] = [];
    await page.route("**/api/learning/sessions/pw-session", async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      await route.fulfill({ json: {
        sessionId: "pw-session",
        learnerId: "local-demo",
        publicationId: "paper-pw-1",
        startedAt: 1,
        attempts: savedAttempts,
      } });
    });
    await page.route("**/api/learning/sessions/pw-session/attempts", async (route) => {
      savedAttempts.push(route.request().postDataJSON() as Record<string, unknown>);
      await route.fulfill({ json: {
        attemptId: "pw-attempt",
        mastery: {
          learnerId: "local-demo",
          knowledgePoint: "数轴上的大小比较",
          score: 0.165,
          attemptCount: 1,
          correctCount: 0,
          lastPracticedAt: 1,
        },
        autoMistake: {
          mistakeId: "paper-mistake-pw-1",
          status: "unmastered",
          contentType: "application/vnd.dotty.publication+json",
        },
      } });
    });
    const publication = {
      publicationId: "paper-pw-1",
      title: "第一章 · 互动试卷",
      status: "published",
      lessonIds: [choiceQuestion.question.id],
      lessonCount: 1,
      createdAt: 1,
      updatedAt: 1,
      lessons: [{
        lessonId: choiceQuestion.question.id,
        title: choiceQuestion.question.knowledgePoint,
        version: 1,
        status: "published",
        questionPayload: choiceQuestion,
        guideCards: [],
      }],
    };
    await page.route("**/api/publications?status=published", async (route) => {
      await route.fulfill({ json: { items: [publication] } });
    });
    await page.route("**/api/publications/paper-pw-1", async (route) => {
      await route.fulfill({ json: publication });
    });
    await page.goto("/learn");
    await page.getByRole("button", { name: /第一章 · 互动试卷/ }).click();
    await expect(page).toHaveURL(/\/learn\/papers\/paper-pw-1$/);
    await expect(page.getByRole("button", { name: "重新生成本题" })).toHaveCount(0);
    await expect(page.getByText(/当前画布动作/)).toHaveCount(0);
    await expect(page.getByRole("region", { name: "分步讲解" })).toHaveCount(0);
    await expect(page.locator(".student-question-givens .math-inline")).toHaveCount(1);
    await expect(page.locator(".student-question-givens > span:not(.student-question-givens-heading)")).toHaveCount(1);
    await expect(page.locator(".student-question-givens .katex")).toHaveCount(1);
    await page.getByRole("button", { name: /B/ }).click();
    await page.getByRole("button", { name: "提交答案" }).click();
    await expect(page.getByText("已经接近了")).toBeVisible();
    await expect(page.getByText("这道错题已自动加入错题本，不需要再次上传。")).toBeVisible();
    await expect(page.getByRole("region", { name: "分步讲解" })).toBeVisible();
    await expect(page.getByLabel("掌握度 17%")).toBeVisible();

    // 学生刷新或返回上一题后，已提交的结构化答案应从学习会话恢复，
    // 而不是只依赖页面内存状态。
    await page.reload();
    await expect(page.getByRole("button", { name: /B/ })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("textbox", { name: "补充你的思路（可选）" })).toHaveValue(/我选择/);
    await expect(page.getByRole("button", { name: "重新提交答案" })).toBeVisible();
  });

  test("正确提交后进入下一道未完成题，最后显示完成态且作答阶段不请求 TTS", async ({ page }) => {
    await mockApi(page);
    const answerAttempts: Record<string, unknown>[] = [];
    let ttsRequests = 0;
    const first = choiceQuestion;
    const second = payload({
      id: "pw-progress-second",
      questionType: "choice",
      chapter: "第一章",
      knowledgePoint: "有理数判断",
      questionNumber: "2",
      prompt: "负数都小于零。",
      givens: [],
      options: ["(A) 正确", "(B) 错误"],
    });
    const publication = {
      publicationId: "paper-progress",
      title: "推进状态测试",
      status: "published",
      lessonIds: [first.question.id, second.question.id],
      lessonCount: 2,
      createdAt: 1,
      updatedAt: 1,
      lessons: [
        { lessonId: first.question.id, title: first.question.knowledgePoint, version: 1, status: "published", questionPayload: first, guideCards: [] },
        { lessonId: second.question.id, title: second.question.knowledgePoint, version: 1, status: "published", questionPayload: second, guideCards: [] },
      ],
    };
    const session = () => ({ sessionId: "progress-session", learnerId: "local-demo", publicationId: "paper-progress", startedAt: 1, attempts: answerAttempts });
    await page.route("**/api/publications?status=published", async (route) => await route.fulfill({ json: { items: [publication] } }));
    await page.route("**/api/publications/paper-progress", async (route) => await route.fulfill({ json: publication }));
    await page.route("**/api/learning/sessions", async (route) => await route.fulfill({ json: session() }));
    await page.route("**/api/learning/sessions/progress-session", async (route) => await route.fulfill({ json: session() }));
    await page.route("**/api/learning/sessions/progress-session/attempts", async (route) => {
      answerAttempts.push(route.request().postDataJSON() as Record<string, unknown>);
      await route.fulfill({ json: {
        attemptId: answerAttempts.at(-1)?.attemptId ?? "progress-attempt",
        mastery: { learnerId: "local-demo", knowledgePoint: "数轴上的大小比较", score: 1, attemptCount: 1, correctCount: 1, lastPracticedAt: 1 },
        autoMistake: null,
      } });
    });
    await page.route("**/api/help", async (route) => await route.fulfill({ json: {
      reply: "回答正确。",
      guideContext: { assessment: "correct", assessmentAuthority: "deterministic" },
      nextHintLevel: 0,
      canvasAction: "none",
      source: "answer-check",
      modelRun,
    } }));
    await page.route("**/api/tts", async (route) => { ttsRequests += 1; await route.fulfill({ status: 204 }); });

    await page.goto("/learn");
    await page.getByRole("button", { name: /推进状态测试/ }).click();
    await expect(page.getByRole("heading", { name: "数轴上的大小比较" })).toBeVisible();
    await page.getByRole("button", { name: /B/ }).click();
    await page.getByRole("button", { name: "提交答案" }).click();
    await expect(page.getByRole("heading", { name: "有理数判断" })).toBeVisible();
    await page.getByRole("button", { name: /A/ }).click();
    await page.getByRole("button", { name: "提交答案" }).click();
    await expect(page.getByRole("heading", { name: "这套试卷已经完成" })).toBeVisible();
    expect(ttsRequests).toBe(0);

    // 刷新完成态仍由最新 attempts 推导，不会把学生带回第一题。
    await page.reload();
    await expect(page.getByRole("heading", { name: "这套试卷已经完成" })).toBeVisible();
  });

  test("可上传裁切后的错题并确认分类与错误原因", async ({ page }) => {
    await mockMistakeApi(page);
    await page.goto("/mistakes");

  await page.getByRole("button", { name: "录入纸质错题", exact: true }).click();
    await expect(page).toHaveURL(/\/mistakes\/capture$/);
    await page.getByLabel("选择错题图片").setInputFiles({
      name: "mistake-fixture.png",
      mimeType: "image/png",
      buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNgYAAAAAMAASsJTYQAAAAASUVORK5CYII=", "base64"),
    });
    await expect(page.getByRole("region", { name: "裁切错题图片" })).toBeVisible();
    await page.getByLabel("裁去上方").fill("5");
    await page.getByLabel(/你当时写的答案/).fill("x = 1");
    await page.getByLabel(/题目文字/).fill("解方程 x + 1 = 3");
    await page.getByRole("button", { name: "识别并进入确认" }).click();

    await expect(page).toHaveURL(/\/mistakes\/mistake-pw-1\/confirm$/);
    await expect(page.getByRole("heading", { name: "确认题目与错误原因" })).toBeVisible();
    await expect(page.getByLabel("题干与公式")).toHaveValue("解方程 $x + 1 = 3$");
    await page.getByRole("radio", { name: /计算失误/ }).check();
    await page.getByRole("button", { name: "确认并保存到错题本" }).click();

    await expect(page).toHaveURL(/\/mistakes$/);
    await expect(page.getByText("计算失误", { exact: true })).toBeVisible();
    await expect(page.getByText("待掌握", { exact: true }).first()).toBeVisible();
  });

  test("可从错题本恢复单题线程并完成一轮有状态陪练", async ({ page }) => {
    await mockMistakeApi(page, true);
    await page.goto("/mistakes");

    await page.getByRole("button", { name: "开始陪练" }).click();
    await expect(page).toHaveURL(/\/mistakes\/mistake-pw-1\/tutor$/);
    await expect(page.getByText("理解错因")).toBeVisible();
    await page.getByLabel("继续回答或描述你的想法").fill("我算出 x = 1");
    await page.getByRole("button", { name: "提交这一轮" }).click();

    await expect(page.getByText("我算出 x = 1")).toBeVisible();
    await expect(page.getByText("先检查移项后符号是否改变，再重新算一次。")).toBeVisible();
    await expect(page.getByText("理解错因")).toBeVisible();
    await expect(page.getByText("需要修正")).toBeVisible();
  });

  test("错题陪练历史消息按当前文本契约渲染公式", async ({ page }) => {
    await mockMistakeApi(page, true, false, true);
    await page.goto("/mistakes/mistake-pw-1/tutor");

    await expect(page.getByText("复习公式")).toBeVisible();
    await expect(page.locator(".tutor-messages .katex")).toHaveCount(1);
  });

  test("陪练完成后可生成并提交错误原因自适应验证题", async ({ page }) => {
    await mockMistakeApi(page, true, true);
    await page.goto("/mistakes/mistake-pw-1/tutor");

    // 进入 verify 后现在会自动生成第一道验证题；按钮分支覆盖生成失败后的手动重试。
    await expect(page.getByRole("heading", { name: /用一道新题验证是否真正理解|基础验证/ })).toBeVisible();
    const firstVariationButton = page.getByRole("button", { name: "生成第一道验证题" });
    if (await firstVariationButton.isVisible().catch(() => false)) {
      await firstVariationButton.click();
    }
    await expect(page.getByRole("heading", { name: "基础验证" })).toBeVisible();
    await page.getByRole("button", { name: /\(B\).*x = 7/ }).click();
    await page.getByRole("button", { name: "提交验证答案" }).click();

    await expect(page.getByText("这次还没有答对", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "重新提交" })).toBeVisible();
    await page.getByRole("button", { name: /\(A\).*x = 3/ }).click();
    await page.getByRole("button", { name: "重新提交" }).click();
    await expect(page.getByText("回答正确", { exact: true })).toBeVisible();
    await expect(page.getByText("已完成掌握验证")).toBeVisible();
    await expect(page.getByRole("button", { name: "生成下一道" })).toHaveCount(0);

    await page.getByRole("button", { name: "← 返回我的错题本" }).click();
    await page.getByRole("button", { name: /进阶本 1/ }).click();
    await expect(page.getByText("已掌握", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "查看验证记录" })).toBeVisible();

    await page.getByRole("button", { name: "查看学习进度" }).click();
    await expect(page.getByRole("heading", { name: "掌握与复习" })).toBeVisible();
    await expect(page.getByText("100%").first()).toBeVisible();
    await page.getByRole("button", { name: "开始复习" }).click();
    await page.getByRole("button", { name: /\(A\).*x = 5/ }).click();
    await page.getByRole("button", { name: "提交复习答案" }).click();
    await expect(page.getByText("复习正确", { exact: true })).toBeVisible();
  });
});

test.describe("教材辅导核心交互", () => {
  test("内容生产端按来源顺序统一渲染公式、题干图和选项图", async ({ page }) => {
    await mockApi(page, renderingImportResult);
    await page.goto("/studio");
    await page.locator('input[type="file"]').setInputFiles({
      name: "rendering-fixture.png",
      mimeType: "image/png",
      buffer: Buffer.from("fixture"),
    });
    await page.getByRole("button", { name: /开始识别/ }).click();
    await page.getByRole("button", { name: "进入动态教材 →" }).click();

    await expect(page.getByRole("heading", { name: "公式与题图" })).toBeVisible();
    await expect(page.locator(".canonical-question-content .question-images img")).toHaveCount(1);
    await expect(page.locator(".canonical-question-content .question-options img")).toHaveCount(1);
    await expect(page.locator(".givens .katex")).toHaveCount(1);
    await expect(page.locator(".canonical-question-content")).not.toContainText("/fixtures/");
    await expect(page.locator(".canonical-question-content .katex")).toHaveCount(2);
    // 统计表要渲染成真实的 <table> 元素，不能作为原始 HTML 源码文字显示。
    await expect(page.locator(".canonical-question-content table")).toHaveCount(1);
    await expect(page.locator(".canonical-question-content table")).toContainText("阅读量/本");
    await expect(page.locator(".canonical-question-content")).not.toContainText("<table");
    await expectLoadedUniqueImages(page.locator(".canonical-question-content img"));
  });

  test("学生入口复用同一套结构化题图和公式渲染", async ({ page }) => {
    await mockApi(page, renderingImportResult);
    const publication = {
      publicationId: "paper-rendering",
      title: "图文渲染回归",
      status: "published",
      lessonIds: [renderingQuestion.question.id],
      lessonCount: 1,
      createdAt: 1,
      updatedAt: 1,
      lessons: [{
        lessonId: renderingQuestion.question.id,
        title: renderingQuestion.question.knowledgePoint,
        version: 1,
        status: "published",
        questionPayload: renderingQuestion,
        guideCards: [],
      }],
    };
    await page.route("**/api/publications?status=published", async (route) => await route.fulfill({ json: { items: [publication] } }));
    await page.route("**/api/publications/paper-rendering", async (route) => await route.fulfill({ json: publication }));
    await page.goto("/learn");
    await page.getByRole("button", { name: /图文渲染回归/ }).click();
    await expect(page.getByRole("heading", { name: "公式与题图" })).toBeVisible();
    await expect(page.locator(".canonical-question-content .question-images img")).toHaveCount(1);
    await expect(page.locator(".canonical-question-content .question-options img")).toHaveCount(1);
    await expect(page.locator(".student-question-givens .katex")).toHaveCount(1);
    await expect(page.locator(".canonical-question-content")).not.toContainText("![");
    await expect(page.locator(".canonical-question-content table")).toHaveCount(1);
    await expect(page.locator(".canonical-question-content")).not.toContainText("<table");
    await expectLoadedUniqueImages(page.locator(".canonical-question-content img"));
  });

  test("导入后可完成选择、判断、画线和 Help 流程", async ({ page }) => {
    await mockApi(page);
    await page.goto("/studio");

    await expect(page.getByRole("heading", { name: "上传教材页或整本 PDF" })).toBeVisible();
    await page.locator('input[type="file"]').setInputFiles({
      name: "playwright-fixture.png",
      mimeType: "image/png",
      buffer: Buffer.from("fixture"),
    });
    await expect(page.getByText("playwright-fixture.png")).toBeVisible();
    await page.getByRole("button", { name: /开始识别/ }).click();
    await page.getByRole("button", { name: "进入动态教材 →" }).click();

    await expect(page.getByRole("heading", { name: "数轴上的大小比较" })).toBeVisible();
    await expect(page.locator(".geometry-canvas-text .math-inline")).toHaveCount(1);
    // 讲解区会同时显示正文和 TTS 文本副本，两者都必须经过 MathText。
    await expect(page.locator(".explanation-card .math-inline")).toHaveCount(2);
    await expect(page.locator(".explanation-card")).not.toContainText("$x-1$");
    await expect(page.locator(".givens > span:not(.givens-heading)")).toHaveCount(1);
    await expect(page.locator(".givens .katex")).toHaveCount(1);
    await page.getByRole("button", { name: /\(B\).*-2 < 1/ }).click();
    await expect(page.getByText("已选择 (B)" )).toBeVisible();
    await page.getByRole("button", { name: "Help · 下一步提示" }).click();
    await expect(page.getByText("先比较两个数在数轴上的左右位置，再写出结论。")).toBeVisible();
    // Studio answers are quality-preview interactions; only the published
    // student route writes mastery telemetry.
    await expect(page.getByText("掌握度 17%")).toHaveCount(0);

    await page.getByRole("button", { name: "下一题" }).click();
    await expect(page.getByRole("heading", { name: "有理数判断" })).toBeVisible();
    await page.getByRole("button", { name: "正确" }).click();
    await expect(page.getByRole("button", { name: "正确" })).toHaveAttribute("aria-pressed", "true");

    await page.getByRole("button", { name: "下一题" }).click();
    await expect(page.getByRole("heading", { name: "对应点连线" })).toBeVisible();
    await page.getByTestId("draw-point-left").click();
    await page.getByTestId("draw-point-right").click();
    await expect(page.locator("line.draw-line-created")).toHaveCount(1);
    await expect(page.getByText("已画 1 条线")).toBeVisible();
    await page.getByRole("button", { name: "提交作图" }).click();
    await expect(page.getByText("先比较两个数在数轴上的左右位置，再写出结论。")).toBeVisible();
  });

  test("第一优先级题型支持多选、填空和数值答案", async ({ page }) => {
    await mockApi(page, priorityImportResult);
    await page.goto("/studio");
    await page.locator('input[type="file"]').setInputFiles({
      name: "playwright-priority-fixture.png",
      mimeType: "image/png",
      buffer: Buffer.from("fixture"),
    });
    await page.getByRole("button", { name: /开始识别/ }).click();
    await page.getByRole("button", { name: "进入动态教材 →" }).click();

    await expect(page.getByRole("heading", { name: "集合交集" })).toBeVisible();
    await page.getByRole("button", { name: /\(A\).*1/ }).click();
    await page.getByRole("button", { name: /\(C\).*3/ }).click();
    await expect(page.getByText("已选择 (A)、(C)")).toBeVisible();
    await page.getByRole("button", { name: "提交回答" }).click();
    await expect(page.getByText("先比较两个数在数轴上的左右位置，再写出结论。")).toBeVisible();

    await page.getByRole("button", { name: "下一题" }).click();
    await expect(page.getByRole("heading", { name: "基础运算" })).toBeVisible();
    await page.getByRole("textbox", { name: "第 1 空" }).fill("4");
    await page.getByRole("button", { name: "提交回答" }).click();
    await expect(page.getByText("先比较两个数在数轴上的左右位置，再写出结论。")).toBeVisible();

    await page.getByRole("button", { name: "下一题" }).click();
    await expect(page.getByRole("heading", { name: "近似值" })).toBeVisible();
    await page.getByRole("textbox", { name: "数值答案" }).fill("3.145");
    await page.getByRole("button", { name: "提交回答" }).click();
    await expect(page.getByText("先比较两个数在数轴上的左右位置，再写出结论。")).toBeVisible();
  });
});
