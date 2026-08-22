// ESLint 平面配置（eslint 9）。
// 范围刻意克制：只启用"真实缺陷"类规则——TypeScript 编译器已覆盖类型错误，
// 这里补的是 tsc 抓不到的 hooks 依赖、未使用变量和常见逻辑陷阱。
// 风格统一（prettier/格式化）是独立的重构窗口任务，不进本门禁。
import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "playwright-report", "test-results", "node_modules", "src/types/generated"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    plugins: { "react-hooks": reactHooks },
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser },
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // 以下两条是 react-hooks v6 为 React Compiler 准备的保守规则，在未启用
      // Compiler 的代码库上会把既有合法惯用法判为错误：
      // - set-state-in-effect：本项目的"输入变化时重置状态"模式（切题/切课程）；
      //   正确解法是 key 重挂载或派生状态，属于独立重构窗口，见 roadmap。
      // - purity：把组件作用域内定义的异步回调里的 Date.now()/crypto.randomUUID()
      //   也当作渲染期调用；这些调用实际发生在事件/网络回调中，已人工核实。
      "react-hooks/set-state-in-effect": "off",
      "react-hooks/purity": "off",
      // 项目大量使用非空断言处理 OpenAPI 生成的可选字段；交给人工审查而不是门禁。
      "@typescript-eslint/no-non-null-assertion": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  }
);
