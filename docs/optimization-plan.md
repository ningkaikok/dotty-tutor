# 用户任务与安全边界优化计划

> **当前状态（2026-10-03）**：计划内七类工作已经实现，并完成后端、前端、隔离 PostgreSQL、HTTPS 与 Docker 最终验收。请以[最终验收报告](optimization-acceptance.md)为最新状态与证据索引。本文件下方 2026-10-02 的跳过项和未启动项是初轮历史快照，不代表最终结果。

## 目标与范围

保留 React、FastAPI、PostgreSQL 和独立 Worker。让用户可靠完成教材发布、学生作答、辅导和教师复核，并确保实际使用的身份、题目版本、确认输入和模型配置与记录一致。减少重复状态、空包装和重复测试，不以减少行数或测试数量为目标。

本计划落实完整评审的七类问题。实施前逐项验证现状；已不存在的问题记录证据，不重复修复。初轮实施时保留工作区已有消融改动，没有重置或覆盖。用户之后明确授权提交、推送、合并与版本发布；由主代理负责该交付链。本文件记录实施范围，远端交付状态以主代理核验为准。

## P0：关闭不可信表达式执行入口

- 追踪所有判题及题目生成校验调用，移除用户文本直接进入 SymPy `parse_expr` 的路径。
- 使用明确受限的数学语法：只允许所需常量、变量、算术操作和少量函数；拒绝属性、下标、任意函数、特殊标识符、Python 内置函数。通过节点白名单构造符号对象，不使用 `eval` 或 `sympify` 解析原始文本。
- 限制输入长度、节点数量、嵌套和指数规模。无法安全处理时返回“不确定”，不得误判正确或将解析失败包装成成功。
- 若昂贵符号化简不能可靠限制，优先保留有界确定性判题，暂停该类兜底；记录数学能力范围，不引入一套复杂沙箱。
- 验收：保留必要数学等价场景；打印、导入、属性链、过深表达式和超大指数均被拒绝，无副作用。

## P0：明确本机演示与联网使用的身份边界

- 默认部署为本机演示：Compose 发布端口绑定回环地址；文档明确可信本机操作者和合成数据前提，不承诺学生独立设备使用或身份隔离。
- 审计所有学生数据、原图、教师管理、发布和 Runtime 管理端点，形成角色与资源归属表。
- 为学生独立设备使用提供明确启用的受保护模式；服务端会话决定身份和角色，学生只能访问自己的资源，教师管理接口仅教师可用。不要把 CORS、TrustedHost、客户端 learnerId 或随机资源 ID 当成授权。
- 优先复用项目已有会话/鉴权基础设施。如确实没有，可采用最小的教师引导凭证与教师签发的学生邀请流程，服务端保存可撤销会话、过期时间，Cookie 使用 HttpOnly、适当 SameSite 和 HTTPS 下 Secure；写操作有 CSRF 防护。复用成熟安全实现，不自造密码算法或 JWT 协议；不得提交任何凭证。
- 联网模式缺少必要配置时启动失败，不能静默降级成匿名模式。显式本机演示继续可用。
- 修复 TutorInput 创建、观察确认、附件读取等路径的非默认学生身份传递；联网模式以服务端身份为准，冲突参数拒绝。
- 验收：本机演示兼容；联网匿名请求被拒绝；学生 A 无法读取/修改 B 的错题、线程、输入、附件、作答和复习，无法调用教师或模型管理接口；退出/过期会话失效；非默认学生完成辅导输入确认流程。
- 这是本阶段最大改动。先写明最小身份协议、迁移和回滚方式，再实现；不扩展多租户、组织管理、密码找回或第三方登录。

## P1：发布内容与确认输入真正不可变

- 课程草稿可编辑；已发布或历史版本引用的课程内容不允许按同一 ID 覆盖。相同内容重复保存可幂等返回，不同内容返回冲突并指导创建修订。
- 复用现有 revision 流程，确保新修订使用新内容 ID；发布与保存的并发通过事务或数据库约束防护，不仅在写入前查询一次。
- 保持发布、作答、判题和知识点投影绑定同一内容版本。提供只读历史一致性检查，不自动修写旧数据。
- 携带 inputId 的消息，判题与模型输入全部读取服务端已确认快照；请求中冲突字段拒绝或要求新输入，不能替换证据。确认后的字段不再允许修改。
- 验收：发布后覆盖失败；草稿正常编辑；新修订不改变旧发布；并发发布/保存不绕过约束；确认 A 后不能借同一 inputId 提交 B。

## P1：后台任务绑定执行配置

- 检查生成、OCR、审阅等所有后台入口；入队时保存可版本化的 Runtime 配置快照，任务执行以此为准。
- 快照只含执行需要的 provider/model/能力参数，不保存密钥或客户端任意运行参数；凭证从 Worker 的受控环境获取。
- API 与 Worker 不依赖启动时读取的本地选择文件实现同步。切换模型不改变已经入队的任务；实际运行审计与请求配置对应。
- 旧任务没有快照时采用明确兼容策略并记录来源，不静默假装使用新选择。
- 验收：API/Worker 不同实例仍执行选定配置；模型切换前后的任务分别使用各自快照；重试配置稳定；配置不可用时明确失败。

## P2：异步结果与辅导提交一致性

- 教师班级/作业请求结果绑定请求键，旧响应不得覆盖新选择；抽取必要 Hook，避免新建通用 Manager 或全局状态层。
- 辅导提交采用请求幂等 ID 和线程预期版本。保存阶段/摘要时进行条件提交；旧生成结果不得覆盖新状态。
- 同一请求重试返回既有结果；并行请求采用明确的拒绝或排队规则。不跨外部模型调用持有数据库事务锁。
- 验收：A/B 响应倒序仍显示 B；同一请求重试只有一个有效回合；两个标签页提交不能回退线程状态；外部调用失败后资源释放、状态可恢复。

## P2：消融与维护成本收口

- 审查本轮影响范围中的重复状态、空包装、无用导出、逐行翻译注释，以及只断言内部调用次数或重复场景的测试。
- 每次删除须说明其原职责由什么现有行为或测试承接；不能删除故障恢复、越权、不可变性或并发失败路径来换取通过。
- 纯逻辑留在 Vitest；DOM 交互用组件测试；E2E 保留真实跨页面流程。验收测试以 user 场景和 Given/When/Then 说明用户行为。
- 不扩大到全仓库风格重写或新增依赖升级工程。

## 验证、文档与交付

每阶段运行必要行为验证，最终真实执行仓库规定门禁：

- 后端：`uv run ruff check .`、`uv run pyright`、`uv run python -m unittest discover -s tests -p 'test_*.py'`。
- 前端：`pnpm lint`、`pnpm vitest run`、`pnpm check:api`、`pnpm exec tsc --noEmit`、`pnpm run build`、`pnpm run test:e2e`。
- 配置涉及 Docker：`docker compose config`、`docker compose up --build --detach`、健康检查、`docker compose down`。使用本任务独立 Compose 项目和端口，不停止其他运行环境。
- PostgreSQL 相关验收必须实际使用隔离数据库；数据库不可用时明确列出未验证项，不能把跳过当通过。
- 运行仓库测试纪律检查和涉及的迁移校验。不得接触真实学生数据、消耗付费模型调用或修改外部部署。
- 同步 `docs/codebase-guide.md`、`docs/architecture.md`、接口变更时的 `docs/api.md`，以及部署/试用说明和 CHANGELOG 用户影响。生成 API 类型使用生成命令。
- 交付列表：每项问题的处理结果、行为变化、删除项与保留依据、真实命令及结果、迁移与回滚、明确未完成项。禁止将计划或部分通过描述成完整验收。

## 独立验收

Luna 执行实现与自检，主代理复查危险输入、跨用户访问、证据不可变、任务快照和并发提交的实际调用链，再复核关键测试结果。当前验收结果见[最终验收报告](optimization-acceptance.md)；提交、合并及版本发布由主代理继续核验并完成。

## 初轮执行记录（历史快照，2026-10-02）

以下记录写于隔离 PostgreSQL 和 Docker 环境启动前，相关未验证项已由[最终验收报告](optimization-acceptance.md)中的后续证据取代。保留本节仅为记录验收进展，不应将旧跳过项视为当前状态。

- **P0 表达式入口**：已完成。`answer_solver.py` 用长度/节点/深度/常量/指数限额及 AST 白名单构造符号表达式；构造 SymPy 之前先估算中间整数位数、符号项数与次数，预算外表达式返回 undecidable；拒绝任意调用、属性和特殊标识符。回归验证副作用输入和嵌套幂膨胀输入均未执行。solver/evaluator 版本已提升。
- **P0 身份边界**：已完成实现。Compose 默认 loopback + demo；protected 模式以教师 bootstrap secret 和一次性邀请建立 HttpOnly Secure 服务端会话，只存随机 opaque token 哈希，校验角色、学生资源归属、Origin 和写方法 CSRF 边界。未分类新 API 默认教师权限；prompt 编辑仍需教师会话及内容凭据，学生只可操作本人错题导入任务；学习漏斗取会话身份，有界文本 TTS 可用，状态诊断仍限教师。schema migration `0014_protected_sessions`。
- **P1 不可变性**：已完成实现；课程保存使用事务行锁，发布/历史引用内容同 ID 只能幂等保存，冲突要求新修订；确认 TutorInput 的判题输入来自服务端确认快照。PostgreSQL 并发效果因本机无隔离测试库而未验证。
- **P1 后台配置**：已完成实现；教材和错题任务在入队时保存 generation/review provider-model、OCR provider 快照，Worker 使用 task-local ContextVar；密钥留在 Worker 环境。缺配置的历史任务会明确告警并走兼容默认值，未知版本/残缺快照失败。迁移使用既有 JSON payload，无需额外配置表。
- **P2 并发与幂等**：教师班级、看板和作业计划结果增加请求序号/班级绑定；Tutor 消息以 message_count 条件提交，`Idempotency-Key` 24 小时内重放已存结果，其他旧版本冲突返回 409。migration `0015_tutor_turn_idempotency`。
- **消融与文档**：保留本轮用户今日入口消融及证据；补充教师迟到响应和受保护模式行为，不删除恢复、越权、来源或失败路径。同步 architecture/codebase/API/deployment/CHANGELOG 和 API 类型。

执行证据（均为本机合成 fixture/SQLite；未接触真实学生数据或部署）：

| 命令 | 实际结果 |
| --- | --- |
| `uv run --cache-dir /tmp/tutor-demo-uv-cache ruff check .`（`apps/api`） | 通过 |
| `uv run --cache-dir /tmp/tutor-demo-uv-cache pyright`（`apps/api`） | 0 errors/warnings/informations |
| `uv run --cache-dir /tmp/tutor-demo-uv-cache python -m unittest discover -s tests -p 'test_*.py'`（`apps/api`） | 同步 main 与收口回归后 496 项通过，39 项跳过（未设置隔离 PostgreSQL URL） |
| `python3 scripts/check_test_discipline.py` | 83 个测试文件，0 处未登记 mock/sleep |
| `pnpm lint`、`pnpm vitest run`、`pnpm check:api`、`pnpm exec tsc --noEmit`、`pnpm run build`（`apps/web`） | 全部通过；Vitest 26 文件、98 测试 |
| `DOTTY_WEB_PORT=59286 pnpm run test:e2e`（`apps/web`） | 16/16 通过，API 使用浏览器 fixture |
| `uv run --cache-dir /tmp/tutor-demo-uv-cache alembic heads`（`apps/api`） | `0015_tutor_turn_idempotency (head)` |
| `POSTGRES_PASSWORD=local-validation-only-123456 WEB_PORT=59684 docker compose -p tutor-optimization-20261002 config` | 配置解析通过 |

37 个数据库相关用例因未配置 `DOTTY_TEST_POSTGRES_ADMIN_URL` 跳过；因此 migrations 实际 upgrade/downgrade、PostgreSQL 行锁、JSONB/外键和课程发布并发门禁仍待隔离 PostgreSQL 验证。Docker daemon 返回 `Cannot connect to the Docker daemon at unix:///Users/kiki/.docker/run/docker.sock`；未执行 Compose build/up/health/down。最终结果不是完整环境验收通过。工作区保留改动，未创建分支、提交或推送。
