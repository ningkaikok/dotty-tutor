import { defineConfig } from "vitest/config";

// 纯函数单测跑在 node 环境；涉及 DOM 的组件测试未来引入 jsdom 时再扩展。
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
