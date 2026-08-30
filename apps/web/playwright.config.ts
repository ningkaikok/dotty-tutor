import { defineConfig, devices } from "@playwright/test";

// 与 vite.config.ts 共用同一个环境变量，两边必须指向同一个端口。
// 覆盖端口后可以在 dev server 继续运行的情况下跑 E2E，也让多个 worktree 互不干扰。
const port = Number(process.env.DOTTY_WEB_PORT ?? 59174);
const baseURL = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  // 每个测试用 page.route 注入独立 API fixture。并发冷启动会让拦截请求排队数秒，
  // 产生与业务无关的加载超时；CI 可由流水线显式覆盖 workers，开发机默认串行保证
  // 结果可重复。
  workers: process.env.CI ? 2 : 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  webServer: {
    // 端口通过 DOTTY_WEB_PORT 传给 vite（子进程继承环境变量），不在命令行里重复一遍。
    command: "npm run dev -- --host 127.0.0.1",
    url: baseURL,
    // 不复用已有服务器：复用只按端口判断，不校验它服务的是哪个目录。本仓库常态使用
    // git worktree，曾出现过 E2E 静默连到另一个检出的 dev server、把那份代码测成全绿
    // 的情况——改动根本没被执行到却报通过。宁可在端口被占时直接失败，也不要静默地
    // 测错代码。需要与 dev server 并存时用 DOTTY_WEB_PORT 换一个端口。
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [{
    name: "chromium",
    use: { ...devices["Desktop Chrome"], channel: "chromium" },
  }],
});
