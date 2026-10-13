import { defineConfig } from "vitest/config";

// 纯函数单测跑在 node 环境；组件测试按文件声明 `@vitest-environment jsdom`。
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    // 只补 jsdom 缺失的原生 API；node 环境下是空操作。
    setupFiles: ["src/test/setupDom.ts"],
    coverage: {
      // 目前只出报告，不设门槛：先看真实分布，再决定要不要按模块定基线。
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/types/generated/**", "src/main.tsx"],
    },
  },
});
