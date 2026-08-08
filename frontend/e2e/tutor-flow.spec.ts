import { test, expect, type Page } from "@playwright/test";

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
    text: "先找出题目中最重要的关系。",
    speechText: "先观察题目条件。",
    action: "show-base",
  }];
}

function payload(question: Record<string, unknown>) {
  const topic = String(question.knowledgePoint);
  return {
    question,
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
  givens: ["观察数轴位置"],
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

async function mockApi(page: Page, result = importResult) {
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
      },
    });
  });
  await page.route("**/api/learning/sessions", async (route) => {
    const request = route.request().postDataJSON() as { learnerId: string; lessonId: string };
    await route.fulfill({
      json: {
        sessionId: "pw-session",
        learnerId: request.learnerId,
        lessonId: request.lessonId,
        startedAt: 1,
      },
    });
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

async function mockMistakeApi(page: Page, startConfirmed = false, startVerify = false) {
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
  let tutorMessages: Array<Record<string, unknown>> = [];
  let variations: Array<Record<string, unknown>> = [];
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
    const correctStreak = variations.filter((item) => item.status === "answered").length + 1;
    const mastery = {
      correctStreak,
      requiredCorrect: 2,
      mastered: correctStreak >= 2,
      answeredCount: correctStreak,
    };
    const answered = {
      ...current,
      status: "answered",
      assessment: "correct",
      response: answer,
      feedback: "回答正确，你已经能独立完成这类移项。",
      answeredAt: 7,
      mastery,
    };
    variations = variations.map((item) => item.variationId === current.variationId ? answered : item);
    if (mastery.mastered) {
      items = items.map((item) => ({ ...item, status: "mastered", updatedAt: 7 }));
    }
    await route.fulfill({ json: answered });
  });
}

test.describe("产品入口", () => {
  test("可在教材学习和错题陪练之间导航并直接访问子路径", async ({ page }) => {
    await mockApi(page);
    await mockMistakeApi(page);
    await page.goto("/");

    await expect(page.getByRole("heading", { name: "选择你的学习入口" })).toBeVisible();
    await page.getByRole("button", { name: "进入教材学习" }).click();
    await expect(page).toHaveURL(/\/textbooks$/);
    await expect(page.getByRole("heading", { name: "上传教材页或整本 PDF" })).toBeVisible();

    await page.getByRole("button", { name: "全部功能" }).click();
    await page.getByRole("button", { name: "查看错题陪练" }).click();
    await expect(page).toHaveURL(/\/mistakes$/);
    await expect(page.getByRole("heading", { name: "我的错题本", exact: true })).toBeVisible();

    await page.goto("/mistakes");
    await expect(page.getByRole("button", { name: "录入一道错题" })).toBeVisible();
  });

  test("可上传裁切后的错题并确认分类与错误原因", async ({ page }) => {
    await mockMistakeApi(page);
    await page.goto("/mistakes");

    await page.getByRole("button", { name: "录入一道错题" }).click();
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
    await expect(page.getByText("定位卡点")).toBeVisible();
    await page.getByLabel("继续回答或描述你的想法").fill("我算出 x = 1");
    await page.getByRole("button", { name: "提交这一轮" }).click();

    await expect(page.getByText("我算出 x = 1")).toBeVisible();
    await expect(page.getByText("先检查移项后符号是否改变，再重新算一次。")).toBeVisible();
    await expect(page.getByText("解释误区")).toBeVisible();
    await expect(page.getByText("需要修正")).toBeVisible();
  });

  test("陪练完成后可生成并提交错误原因自适应验证题", async ({ page }) => {
    await mockMistakeApi(page, true, true);
    await page.goto("/mistakes/mistake-pw-1/tutor");

    await expect(page.getByRole("heading", { name: "用一道新题验证是否真正理解" })).toBeVisible();
    await page.getByRole("button", { name: "生成第一道验证题" }).click();
    await expect(page.getByRole("heading", { name: "基础验证" })).toBeVisible();
    await page.getByRole("button", { name: /\(A\).*x = 3/ }).click();
    await page.getByRole("button", { name: "提交验证答案" }).click();

    await expect(page.getByText("回答正确", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "生成下一道" })).toBeVisible();

    await page.getByRole("button", { name: "生成下一道" }).click();
    await page.getByRole("button", { name: /\(A\).*x = 3/ }).click();
    await page.getByRole("button", { name: "提交验证答案" }).click();
    await expect(page.getByText("已通过掌握验证")).toBeVisible();
    await expect(page.getByText("连续答对 2 / 2")).toBeVisible();

    await page.getByRole("button", { name: "← 我的错题本" }).click();
    await page.getByRole("button", { name: /进阶本 1/ }).click();
    await expect(page.getByText("已掌握", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "查看验证记录" })).toBeVisible();
  });
});

test.describe("教材辅导核心交互", () => {
  test("导入后可完成选择、判断、画线和 Help 流程", async ({ page }) => {
    await mockApi(page);
    await page.goto("/textbooks");

    await expect(page.getByRole("heading", { name: "上传教材页或整本 PDF" })).toBeVisible();
    await page.locator('input[type="file"]').setInputFiles({
      name: "playwright-fixture.png",
      mimeType: "image/png",
      buffer: Buffer.from("fixture"),
    });
    await expect(page.getByText("playwright-fixture.png")).toBeVisible();
    await page.getByRole("button", { name: "开始数字化" }).click();
    await page.getByRole("button", { name: "进入动态教材 →" }).click();

    await expect(page.getByRole("heading", { name: "数轴上的大小比较" })).toBeVisible();
    await page.getByRole("button", { name: /\(B\).*-2 < 1/ }).click();
    await expect(page.getByText("已选择 (B)" )).toBeVisible();
    await page.getByRole("button", { name: "Help · 下一步提示" }).click();
    await expect(page.getByText("先比较两个数在数轴上的左右位置，再写出结论。")).toBeVisible();
    await expect(page.getByText("掌握度 17%")).toBeVisible();

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
    await page.goto("/textbooks");
    await page.locator('input[type="file"]').setInputFiles({
      name: "playwright-priority-fixture.png",
      mimeType: "image/png",
      buffer: Buffer.from("fixture"),
    });
    await page.getByRole("button", { name: "开始数字化" }).click();
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
