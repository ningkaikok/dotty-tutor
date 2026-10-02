import { test, expect } from "@playwright/test";

test("user enters prompt management from content studio while teacher workspace has no prompt editor", async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    if (path === "/api/content/prompts") {
      return route.fulfill({ json: { canPublish: false, items: [{ id: "generation.extraction", variables: ["source", "repair"], editable: true, activeRevisionId: "baseline", events: [], revisions: [{ revisionId: "baseline", templateId: "generation.extraction", version: "v1", contentHash: "a".repeat(64), text: "原题抽取 ${source}${repair}", baseRevisionId: null, createdAt: 0, createdBy: "git", previewed: true, published: true }] }] } });
    }
    if (path.endsWith("models")) return route.fulfill({ json: { selected: { provider: "mock", model: "default" }, providers: [] } });
    if (path === "/api/ocr") return route.fulfill({ json: { selected: "mock", providers: [] } });
    return route.fulfill({ json: { items: [] } });
  });
  await page.goto("/studio");
  await page.getByRole("link", { name: "教学策略 · 提示词管理" }).click();
  await expect(page).toHaveURL(/\/studio\/prompts/);
  await page.getByLabel("内容平台凭据").fill("test-editor");
  await page.getByRole("button", { name: "进入管理" }).click();
  await expect(page.getByLabel("提示词正文")).toHaveValue("原题抽取 ${source}${repair}");
  await expect(page.getByRole("button", { name: "发布此草稿" })).toHaveCount(0);
  await page.getByRole("button", { name: "退出管理" }).click();
  await expect(page.getByLabel("内容平台凭据")).toHaveValue("");
  await page.goto("/teacher");
  await expect(page.getByRole("link", { name: /提示词管理/ })).toHaveCount(0);
  await expect(page.getByLabel("提示词正文")).toHaveCount(0);
});
