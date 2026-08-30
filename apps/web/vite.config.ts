import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 端口可以用 DOTTY_WEB_PORT 覆盖。默认值保持 59174，文档和脚本里的链接因此不变；
// 需要覆盖的是多个 git worktree 并行开发的场景——它们共享同一份配置，端口写死时
// 第二个检出根本起不来，而且更糟的是会让 Playwright 误连到另一个检出的服务器。
const DEFAULT_PORT = 59174;
const port = Number(process.env.DOTTY_WEB_PORT ?? DEFAULT_PORT);

export default defineConfig({
  plugins: [react()],
  server: {
    port,
    // 端口被占时直接失败，而不是静默改用相邻端口：静默换端口会让代理、
    // 书签和端到端测试都指向一个并不存在的地址。
    strictPort: true,
    proxy: {
      "/api": "http://127.0.0.1:8010",
    },
  },
});
