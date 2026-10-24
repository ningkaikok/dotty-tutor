# 课件详情页独立验收（2026-10-23）

> 以下保留各阶段验收的原始范围；当前 v0.41.0 候选的最终检查与发布边界见 [发布验收](release-v0.41.0-acceptance.md)。旧端口/PID和未提交状态为当时记录。

用户反馈：已发布课件仍提示最后一次确认，返回导航、管理标题与完整播放器叠加多套头部。

## 交付与运行位置

- 实现由 Luna（极高）Orca Worker `luna-integration` 完成，Lead 独立检查并收敛重复发布动作、长页预览翻页与原来源异常恢复入口。
- 完整开发改动保留于 `quiet-learning`；本次前端修复独立同步到基于 `8818217e` 的 `courseware-preview`，只包含课件详情、共享品牌换行及相关测试和文档。
- 本机 `http://localhost:59174` 已改由 `courseware-preview/apps/web` 的 Vite 服务提供；仍代理原 API `http://127.0.0.1:8010`。原 API、Worker、PostgreSQL 与配置未更换。
- 原 Vite PID 37149 已停止，新 Vite PID 34728；原启动脚本 PID 37098 和 API PID 37128/37133 保持运行。当前 Vite 终端会话 97924；临时验收端口 59304 已停止。
- 仅本机更新；未提交、推送、创建 PR 或部署生产。

## 页面行为

- 所有详情加载、错误和正常状态都使用 MaterialsHeader/AppHeader。
- 服务端 `status=published` 且无 automation 记录时，显示已发布状态和直接授课入口，不再要求确认发布。
- 展开可选编辑也只有管理页的一处发布动作；发布仍使用既有版本校验、confirmReview、权限和恢复流程。
- 内嵌预览省略完整授课工具栏，保留内容、来源、讲稿与翻页；唯一翻页导航位于内容上方，长页无需滚到底。
- 独立 teacher/learn 播放器仍保持原完整控制；来源异常和失败页面的恢复入口保留。

## 实际执行的检查

工作目录：`courseware-preview/apps/web`。

- `pnpm lint`：通过。
- `pnpm vitest run`：60 文件、244 项通过。
- `PYTHON=/Users/kiki/work/tutor-demo/.cindy-worktrees/quiet-learning/output/review-py312/bin/python pnpm check:api`：通过，无类型漂移。首次因独立检出没有 Python 依赖失败，指定已有 Python 环境后通过，未安装或改动原环境。
- `pnpm exec tsc --noEmit`：通过。
- `pnpm run build`：通过。
- `DOTTY_WEB_PORT=59305 pnpm run test:e2e`：50 项全部通过。首轮 48/50，两处定位歧义暴露了重复状态文本及重复发布按钮；收敛页面动作并限定状态区域后重新全量通过。
- `git diff --check`：通过。
- 后端测试、数据库迁移和 Docker 重建未运行：本次没有后端、存储或容器改动，沿用原 API 和数据。

## 真实数据验收

使用截图课件 `9d3eadb4415a408da1ea98693e7544a7`，原 API 返回 published/completed/ready、7 页、recordVersion 16、automation=null。

真实页面在 320px 和桌面宽度验证：单一共享品牌页头；无内嵌完整教学工具栏；无旧确认提示；可选编辑默认关闭；预览能翻页；直接授课进入原 publicationId 固定版本。

切换前后教师课件 GET 内容 SHA256 相同（411cad1c62a1bb6fbc38e1d2fb8ab068f1b635c1d4f990a3b80b3288dbbaa743），原文档、发布快照与版本未被修改。

## 重启与回退

当前前端启动：在 courseware-preview 下执行 `DOTTY_WEB_PORT=59174 DOTTY_API_URL=http://127.0.0.1:8010 pnpm --dir apps/web dev --host 127.0.0.1`。

如需恢复原前端：只停止当前 59174 的 preview Vite，在 `/Users/kiki/work/tutor-demo/apps/web` 运行 `pnpm dev --host 127.0.0.1`。不要重新启动整套 dev-local.sh，以免与现有 API/Worker/数据库重复。
