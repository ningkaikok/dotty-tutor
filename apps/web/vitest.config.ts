import { defineConfig } from "vitest/config";

// 纯函数单测跑在 node 环境；涉及 DOM 的组件测试未来引入 jsdom 时再扩展。
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    coverage: {
      // 目前只出报告，不设门槛：先看真实分布，再决定要不要按模块定基线。
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/types/generated/**", "src/main.tsx"],
    },
  },
});
