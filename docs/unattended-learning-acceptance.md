# 无感制作与连续学习验收记录

> 以下保留各阶段验收的原始范围；当前 v0.41.0 候选的最终检查与发布边界见 [发布验收](release-v0.41.0-acceptance.md)。旧端口/PID和未提交状态为当时记录。

日期：2026-10-23。基线：main `8818217e`。实现由三位 Luna（极高推理）Worker 完成，主代理独立审查与执行以下验收。开发计划见 [开发计划](unattended-learning-execution-plan.md)。

## 2026-10-24 补练自动上限出口

- 自动连续补练在第四道正确但未达到掌握门槛时停止；反馈留在原页，并提供“再练一道”。每次明确点击只发起一道题的生成，不重置自动上限或自动恢复连续生成。
- 生成失败时保留作答反馈和错误提示，可在同一页重试。请求处理中按钮禁用，Hook 也同步阻止重复创建。
- 达到掌握门槛后仍由现有流程衔接下一项，不触发补练生成。错题辅导仍以 `learnerId:mistakeId` 为 React key，身份和错题上下文切换时不复用旧辅导状态。
- 本次仅改前端补练入口、样式、行为测试和说明文档；尚未接入或重建原本机运行时，也未修改 API、数据库或本机个人配置。

| 命令 / 验收 | 结果 |
| --- | --- |
| `pnpm vitest run src/apps/mistake/components/VariationPractice.test.tsx src/apps/mistake/MistakeCoachApp.test.tsx` | 2 个文件、7 项通过 |
| `pnpm lint` | 通过 |
| `pnpm vitest run` | 65 个文件、260 项通过 |
| `pnpm check:api` | 通过，生成类型无漂移 |
| `pnpm exec tsc --noEmit` | 通过 |
| `pnpm run build` | 通过 |
| `DOTTY_WEB_PORT=59357 pnpm run test:e2e` | 53 项完整浏览器回归通过，含连续学习流程；使用独立端口 |

> 2026-10-24 更新：完整流程及1000页容量修复现已接入真实本机 59174/8010 与原数据；下面有关原端口版本与仅在 staging 的描述是当时的历史状态。当前运行、真实773页教材的隔离分批演练见 [本机接入记录](local-runtime-acceptance-20261024.md)。

## 本轮交付

- 学生从 `/learn` 的统一入口恢复服务端进度，自动衔接作答、错题订正、有界巩固和到期复习；反馈可暂停，不靠点击伪造掌握状态。
- 已发布题的可靠错答直接进入订正，旧状态不会继续虚报人工确认；拍照识别歧义仍保留必要核对。身份切换后的旧请求、旧计时器不回写新学生界面。
- 整本教材明确启动后按最多 30 个物理页分批，Worker 持久续做；暂停和继续幂等，保留已发布及人工编辑课件。当前容量规则为最多 1000 页、`ceil(pageCount / 30)` 且最多 34 批、每批最多 45 张课件页；773 页对应 26 批。
- 非教学页有逐页处理记录，空白页核验读取真实 PDF 文字层与原分辨率墨迹；OCR 漏掉的小公式不会被静默跳过。冷缓存状态读取不下载整本原件。
- 文件持久化覆盖原 PDF、OCR/题图及错题照片，远端分块写入并回读校验后才登记清单；缺缓存按需恢复，权限和路径边界继续生效。
- 本轮学习任务页与教材规划页复用现有 `AppHeader`；已启动的整本任务优先展示进度，设置折叠，移除暗示重复确认的旧文案。

## 主代理实际执行的门禁

| 命令 / 验收 | 结果 |
| --- | --- |
| `uv run ruff check .`（`apps/api`） | 通过 |
| `uv run pyright`（`apps/api`） | 0 错误 |
| `uv run python -m tests.postgres_test_runner --workers 3` | Python 3.12.14，1,012 项全部通过，无跳过；真实 PostgreSQL |
| 修复后的连续学习、整本续做及相关 PostgreSQL 定向回归 | 48 项通过，含公式页、冷缓存、并发及普通页段边界 |
| 文件存储及相关 PostgreSQL 定向回归 | 65 项通过，含最新 10 项存储验收 |
| `pnpm lint`（`apps/web`） | 通过 |
| `pnpm vitest run` | 64 个文件，251 项全部通过 |
| `pnpm check:api` | 通过，生成类型无漂移 |
| `pnpm exec tsc --noEmit` | 通过 |
| `pnpm run build` | 通过 |
| `DOTTY_WEB_PORT=59294 pnpm run test:e2e` | 53 项完整浏览器回归通过；含订正→验证→复习、暂停、服务端会话恢复及整本控制 |
| `python3 scripts/check_test_discipline.py` | 129 个后端测试文件检查通过，无未登记 mock/sleep |
| `git diff --check` | 通过 |
| `docker compose config --quiet`、`docker compose up --build --detach` | 独立项目构建及启动通过；API/Web 健康，Worker 正常运行，迁移任务完成 |
| `curl -fsS http://127.0.0.1:59295/api/health` | `status=ok`、`database=postgresql`、`schema=current` |

本机通过私有 `run_pg_checks.py` 包装器解析既有本机 PostgreSQL 维护库，再注入 `DOTTY_TEST_POSTGRES_ADMIN_URL` 创建隔离测试数据库；未在业务库或线上库运行测试。个人审核模型选择从测试子进程移除，使用与 CI 一致的默认值；实际开发配置未修改。

容量上限的 773/1000/1001 页边界在 `courseware-preview` 的合成 PDF 与纯业务分页测试中验证：773 页会建 26 个完整覆盖批次，1000 页接受为 34 批，1001 页拒绝。没有启动或生成真实 773 页教材的整本课程；既有真实页面、服务及数据库验收记录仍按当时执行范围保留。

### 1000 页容量扩展验收（2026-10-24）

以下是本次仅在未服务真实端口的 `courseware-preview` 工作区执行的容量扩展门禁；上方 1,012 项、64 个前端测试文件等记录是扩展前的历史验收结果，仍保留其原始范围。后端完整 PostgreSQL 套件通过隔离测试库包装器运行，1,017 项全部通过；其中以合成 PDF 覆盖 773 页/26 批、1000 页/34 批、1001 页拒绝与连续无重叠页账本，不启动真实 OpenStax 全书任务，也不调用真实模型。

| 本次实际执行 | 结果 |
| --- | --- |
| `UV_PROJECT_ENVIRONMENT=... uv run --no-sync --project . ruff check .`（`apps/api`） | 通过 |
| `UV_PROJECT_ENVIRONMENT=... uv run --no-sync --project . pyright`（`apps/api`） | 0 错误 |
| `run_stage_checks.py` 包装 `uv run --no-sync python -m tests.postgres_test_runner --workers 3` | Python 3.12，1,017 项通过；日志 `output/whole-book-1000-checks/backend-postgres.log` |
| `pnpm lint` | 通过 |
| `pnpm vitest run` | 65 个文件、262 项通过 |
| `pnpm check:api`（含 `pnpm generate:api` 后复核） | 通过，生成 API 类型无漂移 |
| `pnpm exec tsc --noEmit` | 通过 |
| `pnpm run build` | 通过 |
| `DOTTY_WEB_PORT=59361 DOTTY_API_URL=http://127.0.0.1:59362 pnpm exec playwright test` | 53 项通过；整本启动/暂停/继续 E2E 通过，使用独立空闲端口 |
| `git diff --check` | 通过 |

前端日志保存在 `output/whole-book-1000-checks/`。E2E 中未匹配 fixture 的非目标 API 请求曾输出对独立空闲 API 端口 `59362` 的连接拒绝日志，但 Playwright 全部 53 项通过；本轮未连接真实 API 8010。

首轮检查发现的旧图片路径夹具、缺少可判题答案的夹具、旧浏览器计时控制及指标页面测试的异步等待问题均已修正并复验。没有通过放宽生产路径校验、掌握门槛或删除失败断言来获得通过。

## 真实容器与手机页面

主代理使用 cindy-playwright 在独立 Compose 项目中以合成数据检查真实 API 和页面：

1. 从统一入口进入两题练习，正确作答后真实保存到服务端，再自动进入第二题。
2. 清空浏览器本地存储，从统一入口重新开始，仍恢复同一个服务端会话和第二题，第一题已保存答案保留。
3. 读取已暂停的 35 页整本任务，显示 1–30、31–35 两批；进度置于设置前，刷新保留状态。
4. 在 320 像素视口检查学生页和教材页，内容宽度为 320，无横向溢出；核对共享品牌、返回栏和长标题布局。

本机截图保存在 `output/playwright/quiet-student-mobile.png` 与 `output/playwright/quiet-book-mobile.png`，测试日志位于本机 `lively-thompson/output/quiet-acceptance/`；这些验收产物和合成数据不提交。

独立 Compose 项目名为 `dotty-quiet-acceptance-20261023`，Web 使用 59295，PostgreSQL 使用 59296。原本机 API 8010、Web 59174 和 `dotty-tutor-db-1` 保留。

最终页面检查后已执行 `docker compose down --volumes`：本轮容器、网络和两个临时卷均已清理，独立浏览器已关闭。原本机数据库容器仍健康，API 8010 与 Web 59174 可用。最后两条冗余页面 CSS 移除后，再次在真实容器核对了 320 像素布局，品牌保持在标志右侧且没有横向溢出。

## 未启用的生产配置与验证边界

本轮完成开发与本机验收，代码保留在独立工作区待审；未提交、推送、合并或发布这些新功能，原本机端口仍提供原版本。

远端文件适配的验收使用真实 HTTP Storage 边界测试服务器与真实 PostgreSQL，证明分块、清单、完整性和恢复行为；不代表 Supabase 云端已启用。默认仍为 `DOTTY_FILE_STORAGE_BACKEND=local`；生产启用需要私有桶及 API/Worker 一致的服务端配置，见 [部署说明](deployment.md#可选私有-supabase-文件存储)。未读取隐藏密钥、购买套餐或修改生产部署。

模型/OCR 外部边界的确定性替身和浏览器 API fixture 证明流程与状态转换；真实容器检查使用合成题目，不能据此声称真实模型的整本教学质量已通过内容验收。遇到未解决的来源或内容异常仍会停止并明确显示原因。
