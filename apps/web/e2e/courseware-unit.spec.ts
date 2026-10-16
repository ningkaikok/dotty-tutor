import { expect, test, type Page } from "@playwright/test";

const unitId = "unit-fixture";
const secondId = "unit-lesson-two";

function published(coursewareId: string, publicationId: string) {
  return {
    coursewareId, publicationId, version: publicationId.endsWith("old") ? 1 : 2,
    subject: "math", title: coursewareId === secondId ? "第二课时" : "第一课时",
    sourceTitle: "开放许可函数材料", sourceUrl: "https://example.org/open-functions", sourceLicense: "CC BY 4.0",
    attributionText: "原创测试材料署名", sourceDistributionScope: "classroom", durationMinutes: 20,
    sections: [{ sectionId: "s1", title: "函数探索", purpose: "比较参数", estimatedMinutes: 20, pageIds: ["p1", "p2"] }],
    pages: [
      { pageId: "p1", sectionId: "s1", pageKind: "explanation", layout: "text_focus", title: "函数的定义",
        objectiveIds: [], sourceRefs: [{ sourceRevisionId: "rev1", page: 1 }], estimatedMinutes: 10,
        blocks: [{ kind: "formula", blockId: "f1", latex: "y=kx+b", sourceRefs: [] }], teacherNotes: "SECRET_TEACHER_NOTE" },
      { pageId: "p2", sectionId: "s1", pageKind: "practice", layout: "text_focus", title: "参数探索与作答",
        objectiveIds: [], sourceRefs: [{ sourceRevisionId: "rev1", page: 2 }], estimatedMinutes: 10,
        blocks: [{ kind: "activity", activityId: "function-value", blockId: "activity1", activityKind: "fill_blank",
          instructions: "设置 k=2、b=1，求 x=2 时的 y。", assessmentMode: "objective", choices: [],
          responseSchema: { interaction: "linear_function", slope: 1, intercept: 0, sampleX: 2 },
          answerKey: "SECRET_ANSWER", teacherRubric: "SECRET_RUBRIC" }] },
    ], assets: [], status: "published",
  };
}

async function installUnitFixture(browser: Page) {
  let version = 1;
  let withdrawn = false;
  const saved = { coursewareId: secondId, publicationId: "pub-two-old", pageId: "p2", version: 1, updatedAt: 100, available: true };
  const answers: Array<{ learnerId: string | null; body: Record<string, unknown> }> = [];
  const positions: Array<Record<string, unknown>> = [];
  const unit = () => ({ chapterId: unitId, title: "一次函数单元", subject: "math", resume: saved,
    lessons: ["unit-lesson-one", secondId].map((coursewareId, index) => ({
      coursewareId, title: index ? "第二课时" : "第一课时", order: index + 1, pageStart: index * 2 + 1,
      pageEnd: index * 2 + 2, durationMinutes: 20, status: "published", currentBatch: true,
      sourceRevisionId: "rev1", publicationId: index ? "pub-two-new" : "pub-one-new", publicationVersion: 2,
      activityCount: 1, answeredActivityCount: 0, needsCorrectionCount: 0, activityStatus: "not_started",
      progress: index ? { ...saved, version } : null,
    })),
  });
  await browser.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (!path.startsWith("/api/")) return route.continue();
    const json = (body: unknown, status = 200) => route.fulfill({ status, json: body });
    if (path === "/api/auth/config") return json({ protected: false });
    if (path === "/api/courseware-units") return json({ items: [unit()] });
    const match = path.match(/^\/api\/courseware\/([^/]+)\/(published|unit|progress|attempts)$/);
    if (match) {
      const [, coursewareId, action] = match;
      if (action === "unit") return json(unit());
      if (action === "published") return withdrawn ? json({ detail: "课件已撤回" }, 404)
        : json(published(coursewareId, url.searchParams.get("publicationId") ?? "pub-two-new"));
      if (action === "progress") {
        const body = request.postDataJSON() as Record<string, unknown>;
        positions.push(body);
        return json({ coursewareId, publicationId: body.publicationId, pageId: body.pageId,
          version: ++version, updatedAt: 101, available: true });
      }
      if (action === "attempts") {
        const body = request.postDataJSON() as Record<string, unknown>;
        answers.push({ learnerId: url.searchParams.get("learnerId"), body });
        return json({ attemptId: "answer1", publicationId: body.publicationId, activityId: body.activityId,
          assessment: "correct", feedback: "已记录当前发布版", createdAt: 101 });
      }
    }
    return json({ detail: "此场景未启用附加功能" }, 404);
  });
  return { answers, positions, withdraw: () => { withdrawn = true; } };
}

test("user Given a saved second lesson When continuing and switching lessons Then restores the fixed old publication and exact page", async ({ page }) => {
  const fixture = await installUnitFixture(page);
  await page.goto("/learn");
  await page.getByRole("link", { name: "继续上次学习" }).first().click();
  await expect(page).toHaveURL(/publicationId=pub-two-old.*pageId=p2/);
  await expect(page.getByRole("heading", { level: 1, name: "参数探索与作答" })).toBeVisible();
  await expect(page.getByText("数学 · 第 1 版", { exact: true })).toBeVisible();
  await page.getByText("单元目录与切换课时", { exact: true }).click();
  await page.getByRole("link", { name: "打开第 2 版" }).first().click();
  await expect(page).toHaveURL(/unit-lesson-one.*publicationId=pub-one-new/);
  await expect(page.getByRole("heading", { level: 1, name: "函数的定义" })).toBeVisible();
  await page.getByText("单元目录与切换课时", { exact: true }).click();
  await page.getByRole("link", { name: "打开第 2 版" }).nth(1).click();
  await expect(page).toHaveURL(/unit-lesson-two.*publicationId=pub-two-new/);
  await expect(page.getByRole("heading", { level: 1, name: "函数的定义" })).toBeVisible();
  expect(fixture.answers).toEqual([]);
});

test("user Given a function activity on a phone When changing parameters and submitting Then only the explicit answer is saved", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const fixture = await installUnitFixture(page);
  await page.goto(`/learn/courseware/${secondId}?publicationId=pub-two-old`);
  await expect(page.getByLabel("斜率 k")).toBeVisible();
  await page.getByLabel("斜率 k").focus();
  await page.keyboard.press("ArrowRight");
  await page.getByLabel("截距 b").focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByText("x = 2 时，y = 5", { exact: true })).toBeVisible();
  expect(fixture.answers).toEqual([]);
  await page.getByRole("button", { name: "把当前函数值填入作答" }).click();
  await page.getByRole("button", { name: /Submit practice/ }).click();
  await expect(page.getByText("correct · 已记录当前发布版")).toBeVisible();
  expect(fixture.answers).toMatchObject([{ learnerId: "local-demo", body: {
    publicationId: "pub-two-old", activityId: "function-value", response: { text: "5" },
  } }]);
  await page.reload();
  await expect(page.getByLabel("斜率 k")).toHaveValue("2");
  await expect(page.getByRole("textbox", { name: "Activity response" })).toHaveValue("5");
  await page.getByRole("button", { name: "只看静态说明" }).click();
  await expect(page.getByRole("table", { name: "函数取值表" })).toBeVisible();
  await expect(page.getByRole("application", { name: "交互数学画布" })).toHaveCount(0);
});

test("user Given a frozen publication When exporting student PDF Then all pages and attribution print without private answers and withdrawal blocks a new export", async ({ page }) => {
  const fixture = await installUnitFixture(page);
  await page.goto(`/learn/courseware/${secondId}?publicationId=pub-two-old`);
  await page.getByRole("button", { name: "导出学生版 / PDF" }).click();
  const preview = page.getByRole("region", { name: "课件打印预览" });
  await expect(preview).toBeVisible();
  await expect(preview.getByRole("article", { name: /打印页/ })).toHaveCount(2);
  await expect(preview.getByText(/原创测试材料署名/)).toHaveCount(2);
  await expect(preview.getByText(/SECRET_/)).toHaveCount(0);
  await expect(preview.getByText(/发布第 1 版/)).toHaveCount(3);
  const formula = preview.locator(".courseware-formula-block");
  const activity = preview.locator(".courseware-activity-block");
  await expect(formula).toHaveCSS("background-color", "rgb(255, 255, 255)");
  await expect(formula).toHaveCSS("color", "rgb(23, 23, 23)");
  await expect(activity).toHaveCSS("background-color", "rgb(255, 255, 255)");
  await page.emulateMedia({ media: "print" });
  await expect(formula).toHaveCSS("background-color", "rgb(255, 255, 255)");
  await expect(activity).toHaveCSS("color", "rgb(23, 23, 23)");
  const pdf = await page.pdf({ format: "A4" });
  expect(pdf.subarray(0, 4).toString()).toBe("%PDF");
  await page.emulateMedia({ media: "screen" });
  fixture.withdraw();
  await preview.getByRole("button", { name: "打印 / 保存 PDF" }).click();
  await expect(page.getByRole("alert")).toHaveText("课件已撤回");
  await expect(preview).toHaveCount(0);
});
