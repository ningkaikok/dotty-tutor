import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  // 本地 E2E 通过固定端口复用 Vite dev server，且每个测试用 page.route 注入独立 API
  // fixture。并发冷启动会让拦截请求排队数秒，产生与业务无关的加载超时；CI 可由流水线
  // 显式覆盖 workers，开发机默认串行保证结果可重复。
  workers: process.env.CI ? 2 : 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:59174",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: {
    command: "npm run dev -- --host 127.0.0.1",
    url: "http://127.0.0.1:59174",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [{
    name: "chromium",
    use: { ...devices["Desktop Chrome"], channel: "chromium" },
  }],
});
