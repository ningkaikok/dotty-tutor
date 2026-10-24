import { test, expect } from "@playwright/test";

function contentBlocks(id: string, prompt: string, options: string[]) {
  return [
    { id: `${id}-prompt`, type: "text", text: prompt, sourceOrder: 0 },
    {
      id: `${id}-options`,
      type: "options",
      sourceOrder: 1,
      items: options.map((option, index) => ({
        label: `(${String.fromCharCode(65 + index)})`,
        contentBlocks: [{ id: `${id}-option-${index}`, type: "text", text: option, sourceOrder: 0 }],
      })),
    },
  ];
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/config", async (route) => route.fulfill({ json: { protected: false } }));
  await page.route("**/api/learners", async (route) => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/courseware-units?*", async (route) => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/assignments?*", async (route) => route.fulfill({ json: {
    learnerId: "local-demo",
    items: [{
      assignmentId: "assignment-continuous",
      publicationId: "paper-continuous",
      title: "继续中的作业",
      learnerStatus: "in_progress",
      attemptedCount: 1,
      questionCount: 2,
      sessionId: "server-session-continuous",
    }],
  } }));
  await page.route("**/api/publications?status=published", async (route) => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/mistakes?*", async (route) => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/progress?*", async (route) => route.fulfill({ json: { dueReviewCount: 0 } }));
  await page.addInitScript(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });
});

test("user resumes a server-saved paper, reads the correct feedback, and can pause automatic continuation", async ({ page }) => {
  let startRequests = 0;
  const savedAttempts: Array<Record<string, unknown>> = [{
    attemptId: "already-saved-attempt",
    questionId: "continuous-question-1",
    response: { text: "已完成第一题" },
    assessment: "correct",
    hintLevel: 0,
    durationMs: 800,
    createdAt: 2,
  }];
  const publication = {
    publicationId: "paper-continuous",
    title: "两道题的练习",
    status: "published",
    lessonIds: ["continuous-question-1", "continuous-question-2"],
    lessonCount: 2,
    lessons: [
      {
        lessonId: "continuous-question-1",
        title: "数轴比较（一）",
        knowledgePointId: "kp-1",
        questionPayload: { question: { id: "continuous-question-1", questionType: "choice", prompt: "第一题。", givens: [], options: ["(A) 1", "(B) 2"], contentBlocks: contentBlocks("continuous-question-1", "第一题。", ["1", "2"]) } },
      },
      {
        lessonId: "continuous-question-2",
        title: "数轴比较（二）",
        knowledgePointId: "kp-2",
        questionPayload: { question: { id: "continuous-question-2", questionType: "choice", prompt: "第二题。", givens: [], options: ["(A) 3", "(B) 4"], contentBlocks: contentBlocks("continuous-question-2", "第二题。", ["3", "4"]) } },
      },
    ],
  };
  const session = () => ({
    sessionId: "server-session-continuous",
    learnerId: "local-demo",
    publicationId: "paper-continuous",
    assignmentId: "assignment-continuous",
    startedAt: 1,
    attempts: savedAttempts,
  });

  await page.route("**/api/learning/next?*", async (route) => route.fulfill({ json: {
    learnerId: "local-demo",
    serverTime: 10,
    next: { kind: "assignment", assignmentId: "assignment-continuous", publicationId: "paper-continuous", title: "继续中的作业", learnerStatus: "in_progress", sessionId: "server-session-continuous" },
    summary: { unfinishedAssignments: 1, pendingConfirmationCount: 0, unmasteredCount: 0, dueReviewCount: 0 },
  } }));
  await page.route("**/api/learning/next/start", async (route) => {
    startRequests += 1;
    await route.fulfill({ json: {
      learnerId: "local-demo",
      serverTime: 10,
      next: { kind: "assignment", assignmentId: "assignment-continuous", publicationId: "paper-continuous", title: "继续中的作业", learnerStatus: "in_progress", sessionId: "server-session-continuous" },
      summary: { unfinishedAssignments: 1, pendingConfirmationCount: 0, unmasteredCount: 0, dueReviewCount: 0 },
    } });
  });
  await page.route("**/api/learning/sessions/server-session-continuous", async (route) => route.fulfill({ json: session() }));
  await page.route("**/api/learning/mastery/local-demo", async (route) => route.fulfill({ json: { learnerId: "local-demo", items: [] } }));
  await page.route("**/api/publications/paper-continuous", async (route) => route.fulfill({ json: publication }));
  await page.route("**/api/learning/sessions", async (route) => {
    const request = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { ...session(), learnerId: request.learnerId, publicationId: request.publicationId } });
  });
  await page.route("**/api/help", async (route) => route.fulfill({ json: {
    reply: "回答正确。",
    guideContext: { assessment: "correct", assessmentAuthority: "deterministic" },
    nextHintLevel: 0,
    canvasAction: "none",
    source: "answer-check",
    modelRun: { provider: "mock", model: "playwright" },
  } }));
  await page.route("**/api/learning/sessions/server-session-continuous/attempts", async (route) => {
    const attempt = route.request().postDataJSON() as Record<string, unknown>;
    savedAttempts.push(attempt);
    await route.fulfill({ json: {
      attemptId: attempt.attemptId,
      mastery: { learnerId: "local-demo", knowledgePoint: "数轴比较", score: 0.8, attemptCount: 1, correctCount: 1, lastPracticedAt: 3 },
      autoMistake: null,
    } });
  });

  await page.goto("/learn");
  const start = page.getByRole("button", { name: "继续学习：继续中的作业" });
  await expect(start).toBeVisible();
  await expect.poll(() => startRequests).toBe(0);

  await start.click();
  await expect(page).toHaveURL(/sessionId=server-session-continuous/);
  await expect(page.getByText("第二题。", { exact: true })).toBeVisible();
  await expect(page.getByText("第一题。", { exact: true })).not.toBeVisible();

  await page.getByRole("button", { name: "(B) 4" }).click();
  await page.getByRole("button", { name: "提交答案" }).click();
  await expect(page.getByText("回答正确。", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "暂停自动继续" })).toBeVisible();
  await page.getByRole("button", { name: "暂停自动继续" }).click();
  await expect(page.getByText("自动继续已暂停，这道题的反馈会保留。", { exact: true })).toBeVisible();
  await page.waitForTimeout(2800);

  await expect(page).toHaveURL(/learn\/papers\/paper-continuous/);
  await expect(page.getByText("第二题。", { exact: true })).toBeVisible();
  expect(startRequests).toBe(1);
  expect(savedAttempts).toHaveLength(2);
});

test("user gets an automatic mistake correction, passes mastery verification, and completes its due review", async ({ page }) => {
  const now = Math.floor(Date.now() / 1000);
  let attemptCount = 0;
  let mistakeStatus: "unmastered" | "mastered" = "unmastered";
  let reviewStatus: "scheduled" | "ready" | "completed" = "scheduled";
  let tutorStage: "diagnose" | "practice" = "diagnose";
  let variationCreated = false;
  let variationAnswered = false;
  const paperId = "paper-error-flow";
  const assignmentId = "assignment-error-flow";
  const sessionId = "server-session-error-flow";
  const mistakeId = "mistake-from-paper";
  const reviewId = "review-after-mastery";

  const payload = (question: Record<string, unknown>) => ({
    question,
    lessonSteps: [],
    architecture: {},
    modelRun: { provider: "mock", model: "playwright" },
  });

  const paperQuestion = {
    id: "paper-error-question",
    questionType: "choice",
    prompt: "已知 2 + 3 = ?",
    answer: "5",
    givens: [],
    knowledgePoint: "基础加法",
    options: ["(A) 4", "(B) 5"],
    contentBlocks: contentBlocks("paper-error-question", "已知 2 + 3 = ?", ["4", "5"]),
  };
  const paper = {
    publicationId: paperId,
    title: "加法练习",
    status: "published",
    lessonIds: [paperQuestion.id],
    lessonCount: 1,
    lessons: [{
      lessonId: paperQuestion.id,
      title: "加法",
      knowledgePointId: "addition",
      questionPayload: payload(paperQuestion),
    }],
  };
  const mistake = {
    mistakeId,
    learnerId: "local-demo",
    sourceFilename: "加法练习",
    contentType: "application/vnd.dotty.publication+json",
    sourceImageUrl: "",
    questionPayload: payload(paperQuestion),
    guideCards: [],
    originalAnswer: "我选择(A)：4",
    subject: "数学",
    gradeBand: "小学",
    chapter: "加法",
    knowledgePoint: "基础加法",
    notes: "",
    status: mistakeStatus,
    createdAt: now,
    updatedAt: now,
  };
  const variationQuestion = {
    id: "variation-question-1",
    questionType: "choice",
    prompt: "验证题：7 + 2 = ?",
    answer: "9",
    givens: [],
    knowledgePoint: "基础加法",
    options: ["(A) 8", "(B) 9"],
    contentBlocks: contentBlocks("variation-question-1", "验证题：7 + 2 = ?", ["8", "9"]),
  };
  const reviewQuestion = {
    id: "review-question-1",
    questionType: "choice",
    prompt: "复习题：6 + 3 = ?",
    answer: "9",
    givens: [],
    knowledgePoint: "基础加法",
    options: ["(A) 8", "(B) 9"],
    contentBlocks: contentBlocks("review-question-1", "复习题：6 + 3 = ?", ["8", "9"]),
  };
  const currentAction = () => {
    if (attemptCount === 0) return {
      kind: "assignment", assignmentId, publicationId: paperId, title: "加法练习",
      learnerStatus: "in_progress", sessionId,
    };
    if (mistakeStatus === "unmastered") return {
      kind: "correct_mistake", mistakeId, title: "基础加法",
    };
    if (reviewStatus !== "completed") return {
      kind: "review", taskId: reviewId, mistakeId, title: "基础加法复习",
      status: reviewStatus === "ready" ? "ready" : "scheduled", dueAt: now - 1,
    };
    return { kind: "none" };
  };
  const snapshot = () => ({
    learnerId: "local-demo",
    serverTime: now,
    next: currentAction(),
    summary: {
      unfinishedAssignments: attemptCount === 0 ? 1 : 0,
      pendingConfirmationCount: 0,
      unmasteredCount: mistakeStatus === "unmastered" && attemptCount > 0 ? 1 : 0,
      dueReviewCount: mistakeStatus === "mastered" && reviewStatus !== "completed" ? 1 : 0,
    },
  });
  const session = () => ({
    sessionId, learnerId: "local-demo", publicationId: paperId, assignmentId,
    startedAt: now - 60,
    attempts: attemptCount ? [{
      attemptId: "wrong-paper-attempt", questionId: paperQuestion.id,
      response: { text: "我选择(A)：4" }, assessment: "incorrect", hintLevel: 0,
      durationMs: 800, createdAt: now,
    }] : [],
  });
  const readyReview = () => ({
    taskId: reviewId,
    mistakeId,
    learnerId: "local-demo",
    intervalDays: 1,
    dueAt: now - 1,
    status: reviewStatus,
    questionPayload: payload(reviewQuestion),
    response: reviewStatus === "completed" ? { content: "我选择(B)：9", interactionResult: { selectedOptions: ["(B)"] } } : {},
    assessment: reviewStatus === "completed" ? "correct" : undefined,
    feedback: reviewStatus === "completed" ? "复习正确。" : "",
    createdAt: now - 2,
    mistake: { chapter: "加法", knowledgePoint: "基础加法", prompt: paperQuestion.prompt },
  });

  await page.route("**/api/assignments?*", async (route) => route.fulfill({ json: {
    learnerId: "local-demo",
    items: attemptCount === 0 ? [{
      assignmentId, publicationId: paperId, title: "加法练习", learnerStatus: "in_progress",
      attemptedCount: 0, questionCount: 1, sessionId,
    }] : [],
  } }));
  await page.route("**/api/learning/next?*", async (route) => route.fulfill({ json: snapshot() }));
  await page.route("**/api/learning/next/start", async (route) => {
    const action = currentAction();
    if (action.kind === "review" && reviewStatus === "scheduled") reviewStatus = "ready";
    const state = snapshot();
    await route.fulfill({ json: state.next.kind === "review" ? {
      ...state,
      next: {
        ...state.next,
        intervalDays: 1,
        questionPayload: payload(reviewQuestion),
        response: {},
        feedback: "",
        createdAt: now - 2,
        mistake: { chapter: "加法", knowledgePoint: "基础加法", prompt: paperQuestion.prompt },
      },
    } : state });
  });
  await page.route(`**/api/learning/sessions/${sessionId}`, async (route) => route.fulfill({ json: session() }));
  await page.route("**/api/learning/mastery/local-demo", async (route) => route.fulfill({ json: { learnerId: "local-demo", items: [] } }));
  await page.route(`**/api/publications/${paperId}`, async (route) => route.fulfill({ json: paper }));
  await page.route("**/api/learning/sessions", async (route) => {
    const request = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { ...session(), learnerId: request.learnerId, publicationId: request.publicationId } });
  });
  await page.route("**/api/help", async (route) => route.fulfill({ json: {
    reply: "这次还没有答对，错题已保存。",
    guideContext: { assessment: "incorrect", assessmentAuthority: "deterministic" },
    nextHintLevel: 0, canvasAction: "none", source: "answer-check",
    modelRun: { provider: "mock", model: "playwright" },
  } }));
  await page.route(`**/api/learning/sessions/${sessionId}/attempts`, async (route) => {
    attemptCount += 1;
    await route.fulfill({ json: {
      attemptId: "wrong-paper-attempt",
      mastery: { learnerId: "local-demo", knowledgePoint: "基础加法", score: 0, attemptCount: 1, correctCount: 0, lastPracticedAt: now },
      autoMistake: { ...mistake, status: mistakeStatus },
    } });
  });
  await page.route(`**/api/mistakes/${mistakeId}`, async (route) => route.fulfill({ json: { ...mistake, status: mistakeStatus } }));
  await page.route(`**/api/mistakes/${mistakeId}/thread?*`, async (route) => route.fulfill({ json: {
    threadId: "tutor-thread-error-flow", mistakeId, learnerId: "local-demo", stage: tutorStage,
    summary: "", hintLevel: 0, messageCount: 0, messages: [], createdAt: now, updatedAt: now,
  } }));
  await page.route("**/api/tutor/threads/tutor-thread-error-flow/messages", async (route) => {
    tutorStage = "practice";
    const thread = {
      threadId: "tutor-thread-error-flow", mistakeId, learnerId: "local-demo", stage: tutorStage,
      summary: "学生重新理解了加法过程。", hintLevel: 0, messageCount: 2, messages: [], createdAt: now, updatedAt: now,
    };
    await route.fulfill({ json: { thread, reply: "我们来做一道巩固题。", action: {} } });
  });
  await page.route(`**/api/mistakes/${mistakeId}/variations?*`, async (route) => {
    if (route.request().method() === "GET") {
      await route.fulfill({ json: { items: variationAnswered ? [{
        variationId: "variation-1", mistakeId, learnerId: "local-demo", strategy: "parallel-calculation",
        level: "foundation", sequence: 1, questionPayload: payload(variationQuestion), status: "answered",
        assessment: "correct", feedback: "验证题回答正确。", response: { content: "9" }, createdAt: now,
        answeredAt: now, attemptId: "variation-attempt-1", nextAction: "advance",
        mastery: { correctStreak: 1, requiredCorrect: 1, mastered: true, answeredCount: 1, nextAction: "advance" },
      }] : [] } });
      return;
    }
    variationCreated = true;
    await route.fulfill({ json: {
      variationId: "variation-1", mistakeId, learnerId: "local-demo", strategy: "parallel-calculation",
      level: "foundation", sequence: 1, questionPayload: payload(variationQuestion), status: "ready",
      response: {}, feedback: "", createdAt: now,
    } });
  });
  await page.route("**/api/variations/variation-1/answer", async (route) => {
    variationAnswered = true;
    mistakeStatus = "mastered";
    reviewStatus = "scheduled";
    await route.fulfill({ json: {
      variationId: "variation-1", mistakeId, learnerId: "local-demo", strategy: "parallel-calculation",
      level: "foundation", sequence: 1, questionPayload: payload(variationQuestion), status: "answered",
      assessment: "correct", feedback: "验证题回答正确。", response: { content: "我选择(B)：9" },
      createdAt: now, answeredAt: now, attemptId: "variation-attempt-1", tutorStage: "verify", nextAction: "advance",
      mastery: { correctStreak: 1, requiredCorrect: 1, mastered: true, answeredCount: 1, nextAction: "advance" },
    } });
  });
  await page.route(`**/api/reviews/${reviewId}/answer`, async (route) => {
    reviewStatus = "completed";
    await route.fulfill({ json: readyReview() });
  });

  await page.goto("/learn");
  await page.getByRole("button", { name: "继续学习：加法练习" }).click();
  await expect(page.getByText(paperQuestion.prompt, { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /A/ }).click();
  await page.getByRole("button", { name: "提交答案" }).click();
  await expect(page.getByText("这次还没有答对，错题已保存。", { exact: true })).toBeVisible();
  await expect.poll(() => attemptCount).toBe(1);

  await expect(page).toHaveURL(/mistakes\/mistake-from-paper\/tutor\?continuous=1/, { timeout: 10_000 });
  await expect(page.locator(".ui-brand")).toContainText("错题辅导", { timeout: 10_000 });
  await page.getByRole("button", { name: "我说不好，直接开始" }).click();
  await page.getByRole("textbox").fill("我把加法算错了，现在明白要逐步计算。");
  await page.getByRole("button", { name: "提交这一轮" }).click();
  await expect.poll(() => variationCreated).toBe(true);
  await expect(page.getByText(variationQuestion.prompt, { exact: true })).toBeVisible();
  await page.locator(".variation-practice").getByRole("button", { name: "(B) 9" }).click();
  await page.getByRole("button", { name: "提交验证答案" }).click();
  await expect(page.getByText("已通过掌握门槛", { exact: true })).toBeVisible();

  await expect(page).toHaveURL(/learn\/continue/, { timeout: 10_000 });
  await expect(page.getByText(reviewQuestion.prompt, { exact: true })).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("今日待复习", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "(B) 9" }).click();
  await page.getByRole("button", { name: "提交复习答案" }).click();
  await expect(page.getByText("复习正确", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/learn\/continue/);
  await page.waitForTimeout(2800);
  await expect(page).toHaveURL(/\/learn$/);
  await expect(page.getByText("今天没有待办任务", { exact: true })).toBeVisible();
  expect(attemptCount).toBe(1);
  expect(variationAnswered).toBe(true);
  expect(mistakeStatus).toBe("mastered");
  expect(reviewStatus).toBe("completed");
});
