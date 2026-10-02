# 从用户任务审视结构与消融结果

> **最新验收状态（2026-10-03）**：原有用户任务消融改动均纳入最终实现；后续补充了身份边界、不可变性、并发与迁移修复。本文中标为“初轮历史”的 PostgreSQL/Docker 跳过和未启动记录已经过时；当前最终结果、命令与证据见[优化验收报告](optimization-acceptance.md)。

审视日期：2026-10-02。基线：`2d90ee9`。本轮追踪三类角色入口，深入检查学生今日、作答完成、
教师班级与内容生产页面，并抽查后台编排、学习路由和测试。属于任务链定向审视，不是全仓逐行审计。

## 用户要完成的任务

| 用户 | 输入与行动 | 可观察的完成条件 | 失败后需要保留什么 |
| --- | --- | --- | --- |
| 学生 | 接到作业或发现一道错题，作答、订正、验证、复习 | 待办减少；能看见判定依据、反馈和下一步；完成后仍能回看 | 答案、学习记录、当前身份归属 |
| 教师 | 选择班级，查看卡点，审阅计划并确认指派 | 作业发给正确学生；讲评能追溯到作答和复核 | 原始判定、人工修正、指派版本 |
| 内容生产者 | 上传教材，核对题干、题图、答案并发布 | 学生得到可作答且来源可查的练习 | 来源、修订记录、失败批次和可重试状态 |

第一性原理的判断顺序是：这一步帮助谁完成什么？完成能否被用户辨认？失败后能否恢复？
之后才讨论组件、Hook、服务或测试数量。AI 工程学习仍可以作为实验目标；实验功能进入主流程时，
必须单独说明它改变了哪个用户结果，不能拿合成评测成功替代教学效果。

## 结构取舍

保留三类任务入口及模块化单体。它们共享已发布题目、判题、学习证据和后台任务基础设施，
无需为相同题型建立学生与内容生产两套实现，也无需新增角色服务、通用 Repository 或另一套状态框架。

```text
用户任务页面 → 请求与状态 Hook → 既有 API
                                  ↓
                          领域编排 → Runtime / Store
                                  ↓
                     来源、发布版本、作答及复核证据
```

页面负责可见任务和动作；Hook 负责请求生命周期与身份范围；领域负责判题和状态转移；
Store 保存事实，Runtime 隔离外部系统。`TutorEngine` 与 `StatefulTutor` 分开有判题权限和
跨请求教学状态的理由，不因类名或层数直接判为冗余。

## 本轮已修改

| 观察到的问题 | 对用户的影响 | 修改 |
| --- | --- | --- |
| 已完成作业和未完成作业一起累计今日计数 | 做完作业也清不掉待办 | 已完成作业保留独立回看区 |
| 四路读取分散维护八份 React 状态，切换身份未清除失败路的旧值 | 新学生可能看到上一人的任务 | 单一身份绑定快照，失败路为空，保留迟到响应保护 |
| 部分请求失败仍宣称没有待办 | 学生把读不到理解为做完了 | 明确提示列表不完整，不给出全量空任务结论 |
| 本机会话存在就宣称“上次没做完” | 已完成的自由练习被描述为未完成 | 本机记录仅用于开始/继续提示，不推断完成状态 |
| 注释仍说班级和作业不存在 | 维护者基于错误前提扩展队列 | 删除过时设计说明 |
| “过期就失去间隔重复的效果” | 把未经验证的教学结论作为催促文案 | 改为到期复习及后续安排的事实描述 |
| 待办与练习重复列表 JSX | 修正文案、读屏名称和布局要改两处 | 同文件内复用 QueueItem，供三类列表使用 |
| 进度 Hook 暴露没有调用者的 isQuestionCompleted | 无实际消费者的额外契约 | 删除该成员，保留内部完成判定 |
| 两条 E2E 重复建立完成态 API 夹具 | 同一回归维护两套大段准备代码 | 合并反馈顺序断言到推进与刷新恢复流程 |

“AI Slop”在此按可验证症状判断：错误前提、无依据断言、无调用者接口、重复状态与夹具。
不按代码是否由 AI 生成判断，也不为了净删行数删除业务能力。

## 实际消融与反证

1. 修改前先写四个 Given/When/Then 场景：混合完成状态、全部完成、部分读取失败、身份切换后读取失败。
   原实现四条全失败，修改后四条通过；随后补充四路全部失败场景。
2. 将八份状态及逐路 catch/setState 删除，改为一次 allSettled 快照；用户场景通过，已有浏览器流程通过。
   本轮未测量性能收益，不声称减少请求数量。
3. 临时移除快照的 learnerId 检查：四条中身份切换场景失败，其余三条通过。已恢复保护。
4. 删除独立的最后一题反馈 E2E，把反馈可见、完成页尚未出现、完成按钮可见断言移入两题推进流程。
   临时移除 PublishedPaperApp 的完成反馈保护后，合并测试在“回答正确”不可见处失败；恢复后通过。
   因此删掉的是重复夹具，反馈回归仍有验收。

反证实验的失败是预期结果；实验修改已恢复，没有提交失败实现。上述证据只证明软件行为，
不证明模型教学质量或真实学生效果。

## 后续结构工作及保留边界

- `TeacherClassroomApp` 仍组合班级读取、看板刷新、写入表单和复核状态；本轮为班级详情与看板读取增加请求序号，
  切换班级时清空旧详情并丢弃迟到响应。`useAssignmentPlanning` 用 generation 与当前班级绑定分析/恢复结果，
  不新增通用请求管理器。页面其余写入状态未拆分，后续只有出现独立任务边界时才抽取。
- `TextbookApp` 混合后台任务轮询、互动预览和发布组合；`textbook_processing.py` 同时承接上传、
  OCR、批次生成、修订。后续按这些已存在的生命周期逐段归位，保持 Worker 取消、修订并发和来源绑定。
  仅凭体积尚不能认定其中某条业务能力应删除。
- 陪练模型比较需要约 100 次模型调用，且报告只在 API 进程内保存。它有显式实验边界和成本说明，
  本轮保留；是否变成常规切换前置条件，应以对真实坏样本的增益、成本和维护负担另做对照。
- 保留幂等作答、离线归属隔离、确定性判题、原始证据、发布门禁、教师复核和 schema 迁移测试。
  它们保护恢复和正确性，不能因为快乐路径测试已通过就删掉。
- 组件 DOM 行为放 Vitest + Testing Library；跨页面、浏览器交互和反馈顺序保留 E2E。
  不把端到端场景搬成仅断言内部调用次数的单测。

## 初轮风险与状态更新（历史快照，2026-10-02）

本节记录数据库和 Docker 验收启动前的工作区状态。其“仍需隔离 PG”及“尚未连接容器”描述仅适用于当时；最终验证结果以[优化验收报告](optimization-acceptance.md)为准。

| 问题 | 处理及可观察边界 | 证据 |
| --- | --- | --- |
| 学生 numericAnswer 可进入 SymPy 动态表达式解析 | 改为长度/节点/深度/常量/指数受限的 AST 白名单构造；SymPy 构造前估算整数位数、符号项数和次数，超预算时 undecidable；函数仅允许单参 `sqrt`，不允许任意标识符、属性或调用。嵌套幂用例在预算阶段拒绝，未执行危险计算。 | `apps/api/tests/test_answer_solver.py`、`test_answer_evaluator_symbolic_fallback.py` |
| 本机身份选择被误认为联网授权及角色漏路由 | Compose 默认 loopback + demo；protected 模式服务端 session 决定角色/learnerId，opaque token 哈希存储，邀请一次性、24 小时失效，会话 12 小时过期并可撤销；陌生 API 路由默认教师角色。Prompt 仍需教师会话与独立内容凭据；学生只可访问本人错题图片导入任务，状态响应剥离内部错误；学习漏斗和有界文本朗读按学生开放，TTS 状态探测仍教师可见。 | `apps/api/tests/test_auth_sessions.py`、`test_protected_ownership.py` |
| 发布课件覆盖与确认输入替换 | 已发布/历史引用 lesson 相同内容幂等返回、不同内容冲突；带 inputId 的消息只能使用已确认服务端快照。并发 PostgreSQL 锁与不可变写入仍需隔离 PG 集成环境验证。 | `apps/api/tests/test_learning_runtime.py`、`test_stateful_tutoring.py` |
| 任务使用执行时而非入队时模型配置 | 教材/错题任务保存非密钥 generation/review provider-model 与 OCR provider 快照；Worker 用 task-local ContextVar 应用，旧任务无快照会有兼容告警。 | `apps/api/tests/test_runtime_job_snapshot.py`、`test_textbook_jobs.py`、`test_mistake_jobs.py` |
| 看板迟到响应和辅导并发提交 | 教师异步状态按请求序号与班级绑定；辅导消息用 message_count 条件提交，并在提供 `Idempotency-Key` 时事务保存请求摘要/响应，前端失败重试复用 key。 | `apps/api/tests/test_tutoring_store_concurrency.py`、`apps/web/src/apps/teacher/`、`apps/web/src/auth/ProtectedAccess.test.tsx` |

初轮检查确认上述变更没有引入新依赖。源码中原有可恢复上传任务、题目修订和离线队列均保留；新增的幂等表只承接辅导网络重试，不替代线程消息事实。初轮时尚未连接隔离 PostgreSQL 或 Docker daemon；后续完整验证已记录在验收报告。

初轮本机验证补充：当时同步 main 后的后端 `ruff check .`、`pyright` 通过；`unittest discover` 496 项通过、39 项跳过（尚未设置隔离 PostgreSQL URL）。
前端 lint、Vitest（26 文件/98 测试）、API 类型漂移检查、tsc、build 全部通过；独立端口 Playwright E2E 16/16 通过。
`alembic heads` 指向 `0015_tutor_turn_idempotency`；带隔离 project name 的 Compose `config` 解析通过。数据库与容器未能启动，故此记录不表示 PostgreSQL 或 Docker 端到端验收完成。

## 初轮本机验证（历史快照，已由最终验收报告更新）

后端在 `apps/api` 执行（UV_CACHE_DIR 指向临时目录以满足本机沙箱）：

| 命令 | 结果 |
| --- | --- |
| `uv run ruff check .` | 通过 |
| `uv run pyright` | 0 errors；提示根目录 .venv 不存在，实际依赖由 uv 使用 apps/api/.venv |
| `uv run python -m unittest discover -s tests -p 'test_*.py'` | 474 条，37 条跳过，其余通过 |
| 根目录 `python3 scripts/check_test_discipline.py` | 74 个文件，无未登记 mock/sleep |

前端在 `apps/web` 执行：`pnpm lint`、`pnpm vitest run`、`pnpm check:api`、
`pnpm exec tsc --noEmit`、`pnpm run build`、`DOTTY_WEB_PORT=59286 pnpm run test:e2e`。
浏览器全套 16 条通过，使用 API fixture；未连接真实模型、OCR 或正式数据库。
前端单元测试 24 个文件、95 条通过。

未配置 `DOTTY_TEST_POSTGRES_ADMIN_URL`，数据库测试按仓库约定跳过，不能据此声称 PostgreSQL 完整验收。
未修改 Docker 配置，不执行 Docker 构建/启动门禁。未创建分支、提交或推送。
