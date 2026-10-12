import { expect, test } from "@playwright/test";
test("user Given the development component catalog When opening and closing a dialog with keyboard Then focus returns to its trigger", async ({ page }) => {
  await page.route("**/api/auth/config", (route) => route.fulfill({ json: { protected: false } }));
  await page.goto("/dev/components");
  const trigger = page.getByRole("button", { name: "查看复核说明" });
  await trigger.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "复核说明" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "关闭" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();
  await expect(trigger).toBeFocused();
});
test("user Given a narrow screen When browsing components and following the student route Then controls fit and navigation works", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (!path.startsWith("/api/")) return route.continue();
    return route.fulfill({ json: path === "/api/auth/config" ? { protected: false } : { items: [] } });
  });
  await page.goto("/dev/components");
  await expect(page.getByRole("heading", { name: "Bright 组件目录" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await page.getByRole("link", { name: "学生今日" }).click();
  await expect(page).toHaveURL(/\/learn$/);
  await expect(page.getByRole("navigation", { name: "学习导航" })).toBeVisible();
});
