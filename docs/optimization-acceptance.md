# 用户任务与安全边界优化验收

验收日期：2026-10-03。代码基线：同步后的 `origin/main` `468ea1002f59e4b02356f207bd4b07ee2fdc7c43`。本报告汇总本地隔离环境中的最终实现与验收证据；不代表已完成远端提交、合并、版本发布或生产部署。

## 验收结论

计划的七类问题均已完成实现，并通过后端、前端、隔离 PostgreSQL、受保护 HTTPS 和独立 Docker 流程验收。最新后端完整套件为 639 项通过、0 项跳过。数据库迁移从真实 main 的 `0013_prompt_management` 状态及既有 lesson 数据开始，验证升级至 head、重复升级、非破坏性降级和重新升级，数据与会话审计记录均保留。

最终验证使用本机隔离数据库、容器、合成 PDF 和合成账号；没有读取或写入真实学生数据，也没有调用付费模型、OCR 或 TTS 服务。实际外部 Provider 的教学质量、生产数据库迁移、生产渗透测试和压力测试不在本次证据范围内。

## 问题处理结果

| 优先级 | 问题 | 实现与行为结果 | 关键证据 |
| --- | --- | --- | --- |
| P0 | 用户答案进入危险表达式解析 | 改为 AST 白名单解析，在构造符号表达式前限制输入和中间整数位数、符号项数等运算预算；超出可证明边界时返回不确定。副作用输入与嵌套运算边界有回归覆盖，未执行危险表达式。 | `apps/api/domain/questions/answer_solver.py`、`apps/api/tests/test_answer_solver.py`、`apps/api/tests/test_answer_evaluator_symbolic_fallback.py` |
| P0 | 演示身份边界被误当作联网授权 | Compose 默认只绑定本机回环地址；受保护模式通过 opaque 服务端会话确定教师/学生身份与资源归属，采用一次性教师邀请、CSRF 防护和可撤销会话。未分类路由默认教师权限；仅明确允许的学习者错题任务、漏斗与有界朗读路径开放给学生。 | `apps/api/tests/test_auth_sessions.py`、`apps/api/tests/test_protected_ownership.py`、HTTPS 证据日志 |
| P1 | 已发布内容与确认输入可被同 ID 覆盖 | 已发布或被历史修订引用的课程内容只能幂等保存相同内容；不同内容需创建新修订。TutorInput 判题与辅导输入只使用服务端已确认快照。并发通过数据库条件提交与约束保护。 | `apps/api/tests/test_learning_runtime.py`、`apps/api/tests/test_stateful_tutoring.py`、真实 PostgreSQL 并发验收 |
| P1 | 后台任务配置可能在入队后漂移 | 教材、错题任务保存非密钥 Runtime 配置快照；独立 Worker 按任务快照执行，凭证仍由受控环境提供。真实 Docker API→队列→独立 Worker 流程已验证。 | `apps/api/tests/test_runtime_job_snapshot.py`、`apps/api/tests/test_textbook_jobs.py`、`apps/api/tests/test_mistake_jobs.py`、Docker 验收日志 |
| P2 | 页面异步结果可能覆盖新选择 | 班级/作业计划请求结果按请求序号与当前班级绑定，迟到响应不会覆盖较新的用户选择；用户今日视图的消融改动保留身份快照和失败状态。 | `apps/web/src/apps/teacher/`、`apps/web/src/apps/student/StudentLearningApp.test.tsx`、独立 E2E |
| P2 | 辅导并发提交与重试不一致 | 辅导回合以预期版本条件提交；请求幂等键在事务中保存请求摘要和响应，重放返回已有结果，冲突提交返回明确冲突。 | `apps/api/tests/test_tutoring_store_concurrency.py`、真实 PostgreSQL 并发验收 |
| P2 | 重复状态/测试夹具增加维护成本 | 只删除有行为证据支持的重复状态和夹具；保留恢复、越权、来源、不可变性和并发失败路径。浏览器完成反馈顺序仍由现存跨页流程覆盖。 | `docs/user-task-ablation.md`、前端 Vitest 与 E2E |

默认 `MODEL_PROVIDER=mock` 下的新 QuestionIR 生成链会保留 OCR 来源题干与 lineage，生成明确标记的 synthetic 来源预览，不伪造答案；候选固定进入 `needs_review`，不能直接发布。非 mock Provider 错误仍按失败/隔离处理。Docker 中合成 PDF 的真实任务最终 succeeded，未审核 synthetic 题目没有进入发布状态。该行为只说明任务链可控演示，不证明合成预览具有真实教学质量。

迁移编号与 main 已合并序列保持连续：`0013_prompt_management` → `0014_protected_sessions` → `0015_tutor_turn_idempotency`。已有 schema 创建逻辑可能提前建出未来表，因此新迁移对已存在对象逐项校验，并在事务中重建本迁移自有检查约束，不以 `checkfirst` 掩盖不一致。

## 实际验证

下列门禁由仓库要求的命令实际执行。详细原始日志位于本任务的本机临时目录，路径列在表内。

| 验证 | 结果与边界 | 证据 |
| --- | --- | --- |
| `uv run ruff check .`（`apps/api`） | 通过 | 最终后端验收记录；`/private/tmp/dotty-8610-final-backend.log` |
| `uv run pyright`（`apps/api`） | 检查无错误；输出包含根目录 `.venv` 定位提示 | 同上。不要将该提示描述为无 warning |
| `uv run python -m unittest discover -s tests -p 'test_*.py'`（`apps/api`） | 639 项通过、0 项跳过；退出时有个别连接资源 `ResourceWarning`，没有测试失败 | `/private/tmp/dotty-8610-final-backend.log` |
| `python scripts/check_test_discipline.py` | 83 个测试文件，0 项未登记 mock/sleep | 最终后端验收记录 |
| `pnpm lint`、`pnpm vitest run`、`pnpm check:api`、`pnpm exec tsc --noEmit`、`pnpm run build`（`apps/web`） | 全部通过；Vitest 27 文件、102 项测试 | 最终前端验收记录 |
| `pnpm run test:e2e`（独立端口） | 17/17 通过；浏览器端使用 API fixture | 最终 E2E 验收记录 |
| PostgreSQL 18.6 完整后端套件 | 639 项通过、0 项跳过；另含真实并发回合 CAS、邀请双消费、发布/保存竞态测试 | `/private/tmp/dotty-8610-final-backend.log` 及根代理验收记录 |
| PostgreSQL 17 容器套件 | 638 项通过、0 项跳过；在最后 mock 生成修复前执行。该修复另由 PostgreSQL 18.6 全套覆盖 | 根代理验收记录 |
| 从归档 main schema 与既有 lesson 执行迁移 | 先备份并 preflight；升级到 `0015`、重复升级、指针回退至 `0013` 后再升级均通过。lesson 和会话审计数据保持 | 根代理迁移验收记录；`/private/tmp/dotty-8610-pg-suite.log`、`migration.log` |
| 真实 HTTPS 受保护流程 | Cookie 属性、邀请仅消费一次、跨学生资源访问拒绝、角色边界、CSRF、退出撤销及已发布内容不可变均通过 | `/private/tmp/dotty-8610-https.log` |
| `docker compose config`、隔离项目 `up --build`、健康检查、`down --volumes` | 全流程通过；使用独立项目与端口、合成凭证和本机数据，环境已清理 | Docker 验收记录 |
| Web→API→队列→独立 Worker 的合成教材任务 | 任务成功完成；source lineage 保留，synthetic 题目仍在审核隔离状态 | `/private/tmp/dotty-8610-docker-smoke.log` |
| Nginx API 容器重建 | 修复容器重建后 Docker DNS 地址变化造成的代理失效；API 地址更新后 Web/API 健康检查通过 | `/private/tmp/dotty-8610-proxy-recreation.log` |

Docker 验收使用本机隔离 PostgreSQL 与 Web/API 端口；没有停止或替换其他 Web 容器。测试数据库、容器、网络和临时凭证/TLS 文件已清理。

## 回滚与未覆盖范围

`0015` 降级只回退 schema 版本指针并保留新增审计数据，因此旧应用代码若需回退，仍须先停止公网/联网访问，使用与身份边界兼容的旧版本和经过审查的数据恢复方案；不能通过切换到匿名模式暴露网络服务。生产回滚步骤必须结合目标环境的备份与安全配置制定，本次未执行生产回滚。

没有执行远端部署，也没有验证真实学生身份/数据、外部生产数据库迁移、实际模型/OCR/TTS Provider 的质量与可用性、渗透测试或压力测试。浏览器 E2E 使用 API fixture；真实 HTTPS 和 Docker Worker 流程使用隔离本机服务与合成数据。上述限制不影响列出的本地门禁结果，但不能外推为生产质量或发布状态。

历史工作区初轮记录曾因缺少 PostgreSQL/Docker 而跳过相关验证；它们保留在计划与消融文档中作为时间记录，已由本报告的最终证据更新。
