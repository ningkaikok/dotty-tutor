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

test.describe("产品入口", () => {
  test("可在教材学习和错题陪练之间导航并直接访问子路径", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");

    await expect(page.getByRole("heading", { name: "选择你的学习入口" })).toBeVisible();
    await page.getByRole("button", { name: "进入教材学习" }).click();
    await expect(page).toHaveURL(/\/textbooks$/);
    await expect(page.getByRole("heading", { name: "上传教材页或整本 PDF" })).toBeVisible();

    await page.getByRole("button", { name: "全部功能" }).click();
    await page.getByRole("button", { name: "查看错题陪练" }).click();
    await expect(page).toHaveURL(/\/mistakes$/);
    await expect(page.getByRole("heading", { name: "AI 错题陪练", exact: true })).toBeVisible();

    await page.goto("/mistakes");
    await expect(page.getByText("下一阶段：错题拍照与确认")).toBeVisible();
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
