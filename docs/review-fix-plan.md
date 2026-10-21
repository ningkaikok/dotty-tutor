# Review 问题修复与验收计划

依据：2026-10-21 对 `4012838` 上 review 报告的复核。实施阶段由 DeepSeek Flash 顺序实现，主代理独立检查与验收，当时按授权范围保留工作区改动。

## 本轮修改与可观察的验收结果

1. **限制教材文件出口。** 图片和中间产物只允许读取当前上传的合法批次目录；拒绝 `..`、Windows 反斜杠、绝对路径及编码后的越界输入。校验必须锚定可信根目录，不能只对已被输入改变的根做 `parent` 比较，也不能把 `Path('..').name` 当作修复。合法素材仍能读取。覆盖 Windows 和 POSIX 路径语义，并用临时文件验证越界读取被拒绝。
2. **为教材讲解接上模型配额。** `POST /api/courseware/{id}/source-search/explain` 消耗现有模型分钟/日配额及并发槽；普通检索、预览、取消等无模型操作保持可用。复用当前保护机制，不引入新的注册框架。
3. **让认证拒绝也经过请求保护。** 受保护模式下 401/403 响应同样包含请求标识和安全头、经过请求限流与大小检查。保持登录、OPTIONS、角色和所有者检查的现有语义；模型并发槽只应被确需执行的模型请求占用，注意中间件调整后 `request.state.actor` 等依赖。
4. **统一单选作答形状。** 当答案为单元素列表时，标量 `selectedOption`/`answer` 与单元素 `selectedOptions` 的正确性一致；保留多选全等、不接受缺失或不支持形状的边界。增加有效输入及错答回归场景。
5. **避免把页码识别为上游故障。** 普通解析异常中的 401/403/429 数字不应引发认证/限流熔断；真正带结构化 HTTP 状态或明确 HTTP/限流文案的异常仍正确分类。保留异常链、超时和配置错误处理。
6. **查询模型列表时保持当前选择。** `catalog()` 不再因为 Ollama 暂时不可用而把进程选择切成其他模型或 mock；失败的显式选择也不得改变已有选择。保留启动配置与显式成功选择的合理行为，并检查现有调用方是否依赖旧副作用。测试不得联系真实模型服务。
7. **恢复短暂失败后的学习位置保存。** 网络异常和可重试服务端错误后，后续翻页可继续同步；409 版本冲突仍要求刷新，不能自动覆盖其他设备位置。重试未知结果的写入必须沿用同一 `commandId`、内容和版本，避免服务端已成功但响应丢失导致冲突；仍保证串行写入、切换课件后不串状态。前端增加行为测试，并运行相关 Playwright 流程。
8. **裁剪学生侧小问判分元数据。** `subQuestions[].evaluation` 只保留展示所需 `mode`，不把自由文本 `reason` 或未来新增答案字段直接透传给学生；验证题目显示与判分模式不受影响。

## 范围与工程约束

- 先阅读仓库 `AGENTS.md`、相关实现、已有测试和文档，再改动。优先修改现有模块，避免顺手重构或增加依赖。
- 验收场景先写，遵循 Given/When/Then 与 `user` 命名规则；业务断言优先，不用调用次数断言。后端 mock 只用于已登记的模型/OCR/TTS 外部边界。
- 同步必要的中文行为文档及 `CHANGELOG.md` 的 `Unreleased/Fixed`；本轮不新增顶层模块、端点或响应字段，若确实需要，先回报主代理。
- 不调用真实模型/OCR/TTS，不运行付费评测，不修改密钥、依赖锁、数据库或其他 worktree。不安装新依赖，验证环境由主代理协调。
- 暂不处理：英语作答被标为 math 的原报告场景（上游已分流）；PDF 哈希缓存和 N+1 的性能改造；URL 深链接优先级；并发锁的跨入口完整改造；全局 fetch 重构；提示词缓存身份加固。

## 独立验收

- 主代理逐项阅读 diff，核对边界和测试是否真实覆盖故障，不以执行者的完成声明代替验收。
- 后端运行 ruff、pyright、受影响测试及测试纪律检查；数据库依赖的验证只有在专用测试 PostgreSQL 可用时执行，跳过必须说明。
- 前端运行 lint、Vitest、API 类型漂移检查、TypeScript、build 和相关 E2E。若环境阻止执行，报告原始失败与验证缺口，不声称通过。
- 验收失败交回同一 DeepSeek Flash worker 修正，再针对失败项复验；不自动提交或推送。

## 独立验收记录（2026-10-21）

8 项修复均已实现，主代理已逐项阅读实现与测试。验收中要求执行者去掉业务层 mock、复用 PostgreSQL 测试基础设施，并加强切换课件与卸载时停止旧队列的场景；修正后重新验证。没有调用真实模型服务，没有安装依赖，没有提交或推送。

本机复用主检出已安装的依赖。后端命令在 `apps/api` 执行，设置
`UV_PROJECT_ENVIRONMENT=C:\Users\KaiNing\work\dotty-tutor\apps\api\.venv` 和
`PYTHONDONTWRITEBYTECODE=1`，使用 `uv run --no-sync`，避免自动同步依赖。
前端命令在 `apps/web` 执行；API 类型检查的 `PYTHON` 指向上述环境的 `Scripts\python.exe`。

| 实际执行的命令 | 主代理结果 |
| --- | --- |
| `uv run --no-sync ruff check .` | 通过 |
| `uv run --no-sync pyright --pythonpath C:\Users\KaiNing\work\dotty-tutor\apps\api\.venv\Scripts\python.exe` | 0 errors / 0 warnings；配置指向的本地 `.venv` 不存在，有环境路径提示，实际使用指定解释器 |
| `uv run --no-sync python -m unittest tests.test_upload_asset_paths tests.test_courseware_model_output tests.test_model_runtime tests.test_textbook_processing tests.test_student_question_projection tests.test_public_protection` | 最终复验 82 项：79 通过、3 跳过 |
| `uv run --no-sync python -m tests.postgres_test_runner --workers 2` | 677 项：615 通过、61 跳过、1 错误；不是全量通过 |
| `python scripts/check_test_discipline.py`（仓库根） | 124 个测试文件，0 处未登记 mock/sleep |
| `pnpm lint` | 通过 |
| `pnpm vitest run --maxWorkers=2` | 59 个文件、233 项通过；旧队列场景加强后又单独复跑下列测试 |
| `pnpm vitest run src/apps/courseware/useCoursewareUnitProgress.test.tsx --maxWorkers=2` | 最终 7 项通过，覆盖未知结果重试、503 恢复、409 停写、串行版本、切换与卸载 |
| `pnpm check:api` | 通过，生成类型没有漂移 |
| `pnpm exec tsc --noEmit` | 通过 |
| `pnpm run build` | 通过 |
| `pnpm run test:e2e e2e/courseware-unit.spec.ts`（`DOTTY_WEB_PORT=59323`） | 相关 Chromium 流程 3 项通过；没有执行整个 E2E 套件 |
| `git diff --check`（仓库根） | 通过，仅有工作区换行符转换提示 |

尚未完成的环境验证：

- 后端全量唯一错误在本轮未修改的 `tests.test_courseware_assets_acceptance`：导入 `cairosvg` 时系统缺少 Cairo 库，报 `OSError: no library called "cairo-2" was found`。需要在带 Cairo 的环境重跑该模块与后端全量；本轮没有安装系统依赖或改测试规避。
- 未配置 `DOTTY_TEST_POSTGRES_ADMIN_URL`，数据库相关测试按现有设施跳过；包括新增的真实会话与模型并发集成场景。需要在专用 PostgreSQL 测试环境或 PR CI 完成验证，不能把跳过记为通过。
- 浏览器测试中，复用依赖目录使字体文件触发 Vite `fs.allow` 提示。功能断言通过，字体加载与视觉表现未获完整验证。
- 本轮没有 Docker 相关改动，未执行 Docker 构建与部署检查。

结论：修复实现和本机可运行的相关检查通过；后端全量验收仍有上述环境缺口，尚不满足“全部检查通过”的声明条件。

## PR 交付阶段（2026-10-21）

用户随后明确要求提交 PR 并合并，授权创建修复分支、提交、推送及合并。远端 `main` 仍为
`4012838`，据此创建 `fix/review-findings`。本机验收缺口在 PR 中明确披露，交由配置了
PostgreSQL 17 与 `libcairo2` 的现有 CI 运行后端全量；相关前端与 E2E 全量同样由 PR CI 复验。
仅在最新 PR 提交的检查通过、冲突与活动评审意见处理完成后执行合并。
