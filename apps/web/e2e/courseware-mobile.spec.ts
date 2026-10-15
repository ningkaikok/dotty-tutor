import { expect, test } from "@playwright/test";

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

function publication() {
  const sourceRefs = [{ sourceRevisionId: "mobile-source", page: 3 }];
  return {
    coursewareId: "mobile-lesson", publicationId: "mobile-pub", chapterId: "mobile-unit", sourceRevisionId: "mobile-source",
    title: "手机学习回归", subject: "math", version: 1, durationMinutes: 10, sourceDistributionScope: "classroom",
    assets: [], sections: [{ sectionId: "section", title: "本课内容", purpose: "复核", estimatedMinutes: 10, pageIds: ["opening", "summary"] }],
    pages: [
      { pageId: "opening", sectionId: "section", title: "观察与作答", pageKind: "explanation", layout: "text_focus", sourceRefs, objectiveIds: [], estimatedMinutes: 5,
        blocks: [
          { kind: "text", blockId: "source-text", text: "根据教材原页解释你的想法。", sourceRefs },
          { kind: "input", blockId: "observation", prompt: "写下观察", minResponses: 1, placeholder: "记录你的线索" },
          { kind: "activity", blockId: "discussion-block", activityId: "discussion", activityKind: "discussion", instructions: "解释你的依据", assessmentMode: "teacher_review", choices: [] },
        ] },
      { pageId: "summary", sectionId: "section", title: "本课小结", pageKind: "summary", layout: "text_focus", sourceRefs, objectiveIds: [], estimatedMinutes: 5,
        blocks: [{ kind: "text", blockId: "summary-text", text: "回到原页核对自己的结论。", sourceRefs }] },
    ],
  };
}

test("user Given a phone and an interrupted submission When refreshing and switching learners Then local responses recover only for their owner and the retry can complete", async ({ page }) => {
  let submissionAttempts = 0;
  const savedResponses: unknown[] = [];
  await page.addInitScript(() => { if (!localStorage.getItem("dotty-learner-id")) localStorage.setItem("dotty-learner-id", "phone-one"); });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path === "/api/auth/config") return json({ protected: false });
    if (path === "/api/courseware/mobile-lesson/published") return json(publication());
    if (path === "/api/courseware/mobile-lesson/media/published") return json({ items: [] });
    if (path === "/api/courseware/mobile-lesson/attempts") {
      submissionAttempts += 1;
      if (submissionAttempts === 1) return route.abort("failed");
      savedResponses.push(request.postDataJSON().response);
      return json({ assessment: "needs_review", feedback: "已保存，等待教师复核" });
    }
    return json({ detail: "本场景仅核验本机恢复" }, 404);
  });
  await page.goto("/learn/courseware/mobile-lesson?publicationId=mobile-pub");
  const observation = page.getByRole("textbox", { name: "Observation 1" });
  await observation.fill("这是第一位学习者的线索");
  await page.getByRole("textbox", { name: "Activity response" }).fill("从教材原页找到的条件");
  const submit = page.getByRole("button", { name: "Submit practice: 解释你的依据" });
  await submit.click();
  await expect(page.getByText("Failed to fetch", { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "Activity response" })).toHaveValue("从教材原页找到的条件");
  await submit.click();
  await expect(page.getByText("needs_review · 已保存，等待教师复核", { exact: true })).toBeVisible();
  expect(savedResponses).toEqual([{ text: "从教材原页找到的条件" }]);
  await page.getByRole("button", { name: "Next ›" }).click();
  await expect(page.getByRole("heading", { name: "本课小结" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "本课小结" })).toBeVisible();
  await page.getByRole("button", { name: "‹ Prev" }).click();
  await expect(observation).toHaveValue("这是第一位学习者的线索");
  await page.evaluate(() => localStorage.setItem("dotty-learner-id", "phone-two"));
  await page.reload();
  await expect(observation).toHaveValue("");
  await expect(page.getByRole("textbox", { name: "Activity response" })).toHaveValue("");
  await observation.fill("第二位学习者的独立输入");
  await page.evaluate(() => localStorage.setItem("dotty-learner-id", "phone-one"));
  await page.reload();
  await expect(observation).toHaveValue("这是第一位学习者的线索");
  await expect(page.getByRole("textbox", { name: "Activity response" })).toHaveValue("从教材原页找到的条件");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("user Given withdrawn video and voice sources When opening them on a phone Then unavailable media has no playable fallback and a withdrawn publication cannot restore content", async ({ page }) => {
  let withdrawn = false;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path === "/api/auth/config") return json({ protected: false });
    if (path === "/api/courseware/mobile-lesson/published") return withdrawn ? json({ detail: "教材来源已撤回" }, 410) : json(publication());
    if (path === "/api/courseware/mobile-lesson/media/published") return json({ detail: "视频来源已撤回" }, 410);
    if (path === "/api/courseware/mobile-lesson/voice-sessions") return json({ detail: "语音来源已撤回" }, 410);
    return json({ detail: "本场景不提供单元同步" }, 404);
  });
  await page.goto("/learn/courseware/mobile-lesson?publicationId=mobile-pub");
  await expect(page.getByText(/发布课件视频暂不可用：视频来源已撤回/)).toBeVisible();
  expect(await page.locator("video, audio").count()).toBe(0);
  await page.getByRole("button", { name: "围绕本页讲解语音追问" }).click();
  const voice = page.getByRole("region", { name: "当前步骤语音追问" });
  await expect(voice.getByText("语音来源已撤回", { exact: true })).toBeVisible();
  await expect(voice.getByRole("button", { name: "确认转写并追问" })).toBeDisabled();
  withdrawn = true;
  await page.reload();
  await expect(page.getByRole("heading", { name: "学生发布版暂不可用" })).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("教材来源已撤回");
  expect(await page.locator("video, audio, .courseware-player").count()).toBe(0);
});
