# 数据库设计与治理演进

本文记录 Dotty Tutor 数据库从快速验证到正式运行治理的演进。内容以仓库 Git 历史、当前
`apps/api/persistence/` 代码和测试为依据，快照日期为 **2026-08-31**。它解释为什么当前
PostgreSQL 是正式运行时的唯一数据库、为什么 SQLite 暂时仍在仓库中，以及后续完全退出 SQLite
需要满足什么条件。

## 一、演进时间线

### 1. 本地文件与 SQLite 的快速开发阶段

项目最初把持久化集中在 `backend/storage.py`。教材 PDF、Markdown、题图等大文件放在本地
`data/` 目录，数据库只保存上传任务、批次题目、状态、JSON 结果和文件目录等元数据。早期
schema 由一个 `MetaData` 描述，主要表是 `upload_jobs` 和 `batch_questions`，应用启动时通过
`metadata.create_all()` 确保表存在。

这个阶段的目标是快速跑通“上传 → OCR/生成 → 保存 → 恢复”的闭环，而不是建立独立的数据库
发布流程。`DOTTY_DATA_DIR` 可以选择隔离的 `sqlite+pysqlite:///.../dotty.sqlite3`，因此单机
开发和测试不需要先准备服务；同时代码已经保留 PostgreSQL URL 和 JSON/JSONB、Upsert 的方言
适配，为后续正式数据库迁移留下入口。早期还存在
`backend/migrate_sqlite_to_postgres.py`，它按行读取旧 SQLite 的上传任务和题目批次，再写入
PostgreSQL，文件资产仍留在本地目录。

### 2. 领域 Store 拆分与双数据库方言

2026-08-08 的 `95c009c refactor(persistence): split domain stores` 将一个不断膨胀的存储模块
拆为教材、学习等领域 Store，并抽出共享的 `DatabaseStore`。拆分解决了查询职责混杂的问题，
但也暴露出一个关键约束：多个领域必须共用同一个 Engine 和同一条 schema 初始化路径，否则
每个 Store 都可能形成自己的表创建逻辑。

当前代码延续了这个边界：领域查询留在各自 Store，`persistence/base.py` 负责 Engine、连接
策略、健康检查和 PostgreSQL/SQLite Upsert 差异，`schema_registry.py` 只负责把多个领域
metadata 汇总给 SQLite 隔离初始化、Alembic 和 readiness 检查。JSON 文档在 PostgreSQL 使用
JSONB，在 SQLite 使用 JSON；有行锁、`SKIP LOCKED` 等 PostgreSQL 能力的地方，SQLite 只保留
明确标注的单进程测试回退。

### 3. 从核心学习记录到错题与复习闭环

在核心教材和学习会话之上，数据库按产品能力逐步扩展：

| 历史节点 | 主要结构 | 设计变化 |
| --- | --- | --- |
| `7fad9f3`，错题录入 | `mistake_items` | 保存错题原始题面、识别/模型运行记录、学生状态和确认时间；旧阶段使用 `002_mistake_capture.sql`。 |
| `100f9ed`，变式练习 | `variation_exercises` | 保存由错题生成的验证题、策略、难度、最新作答状态；旧阶段使用 `004_variation_practice.sql`。 |
| `1d80c36`，间隔复习 | `review_tasks` | 将 1/3/7 天复习排期变成持久记录；旧阶段使用 `005_spaced_review.sql`。 |
| `b7e9fbd`，复习判题证据 | `review_tasks.evaluation_evidence_json` | 复习答案旁保存确定性判题证据，避免只保留结论。 |
| `d89bba1`，双归因 | `mistake_items` 的自评/AI 兼容列 | 学生自评和 AI 判断分开保存，陪练状态机消费带证据的 AI 归因。 |
| `d91a4b7`，个性化归因 | `variation_exercises.attribution_source`、`variation_attempts` | 变式练习的采信来源和每次尝试追加保存，支持 AI/自评/教师路径的可追溯性。 |

错题域当前保留旧的 `error_reason`、`ai_error_reason` 和
`ai_error_reason_confidence` 作为兼容投影，同时用 `mistake_attributions` 保存 append-only
历史；旧列不能因为一次新的 AI 或教师判断而覆盖历史证据。

### 4. mastery-v2、发布版本与作业治理

学习数据的第一次重要重构是 `338ab5b feat(learning): add knowledge point entities and mastery
projections`：引入 `knowledge_points`，让知识点身份由发布版本作用域内的稳定 ID 表示，
并将 `mastery_states` 改为按 `(learner_id, knowledge_point_id)` 的 v2 投影。旧的按名称记录
不能直接删除，迁移脚本会保留旧表并从可用作答日志重建新投影。当前 `exercise_attempts` 同时
保留旧展示字段，并记录稳定的 `publication_id` 和 `knowledge_point_id`。

随后，互动试卷与学习会话形成发布版本边界：`lesson_documents`、`lesson_publications`、
`question_revisions` 和 `run_snapshots` 分别承担内容、发布、修订和运行审计职责；新版试卷
追加新版本，不覆盖已发布内容。

班级和作业能力在 `4880cf2 feat(classroom): add assignments and teacher mastery dashboard`
中加入 `learning_classes`、`class_memberships`、`assignments` 和
`teacher_review_events`，作业实例与学习会话关联，教师复核以追加事件影响有效掌握度。
`b517812 feat: add assignment planning workflow` 又加入 `assignment_plans`：计划输入快照、
结果、警告和确认状态独立于最终 assignment，并以 `source_fingerprint` 和确认事务建立幂等边界。
`d91a4b7` 在此基础上增加个性化作业，复用只读班级证据生成脱敏的个性化结果，不把模型输出
直接当作持久化状态。

### 5. 本分支的统一迁移治理

此前的 schema 由 `create_all()`、多个领域 `MetaData`、运行时懒建表和若干一次性脚本共同推动。
它们在功能快速增加时都能工作，却会让“当前代码想要什么”和“已有数据库实际有什么”逐渐
分离。2026-08-31 的 `74b43aa feat: govern database schema migrations` 将这些变化收束到
一条 Alembic 版本链；之后的 `c389b30`、`bef8475` 和 `7e4a66e` 分别补强外键/归因幂等性、
遗留缺口和 preflight 分类。

## 二、当前 schema 的组织方式

`schema_registry.py` 当前汇总 6 个领域、24 张业务表。各领域的职责如下：

| 领域 | 当前表 | 主要职责 |
| --- | --- | --- |
| core | `upload_jobs`、`background_jobs`、`batch_questions`、`lesson_documents`、`lesson_publications`、`learning_classes`、`class_memberships`、`assignments`、`assignment_plans`、`knowledge_points`、`run_snapshots`、`question_revisions`、`learning_sessions`、`exercise_attempts`、`mastery_states`、`teacher_review_events` | 教材、发布、学习、掌握度、班级和作业核心记录 |
| mistake | `mistake_items`、`mistake_attributions` | 错题兼容投影和追加式错因历史 |
| tutoring | `tutor_threads`、`tutor_messages` | 陪练线程、有限上下文消息和判题证据 |
| variation | `variation_exercises`、`variation_attempts` | 变式题当前投影与每次尝试历史 |
| review | `review_tasks` | 间隔复习排期、答案和 `evaluation_evidence_json` |
| metrics | `model_call_metrics` | 模型调用边界指标和成本代理数据 |

当前代码中的 SQLAlchemy metadata 是“模型描述”，不是生产变更命令。各领域 metadata 通过
registry 被 Alembic 检查和被隔离 SQLite 测试复用，避免再次出现同名表由导入顺序决定事实来源。

## 三、从 schema drift 得到的教训

### `create_all()` 只能补表，不能演进已有表

SQLAlchemy `MetaData.create_all(checkfirst=True)` 会在表不存在时创建表；它不会把已有表自动
补上新列、索引、约束、外键或数据回填。因此“代码声明已经有字段”不等于“运行中的数据库已经
有字段”。当前生产 PostgreSQL Store 已不再调用 `create_all()`，只由显式 Alembic 迁移修改
schema。

### 多个 MetaData 会隐藏完整 schema

教材/学习、错题、陪练、变式、复习和指标曾各自拥有 metadata。若迁移只看到其中一部分，或者
某个 Store 在自己的首次请求中懒建表，数据库就会出现业务可访问但迁移工具看不到的表。当前
registry 明确列出六个 metadata，并在导入时拒绝重复表名。

### 运行时懒建表适合隔离测试，不适合正式运行时

各 Store 仍保留 SQLite 隔离初始化，是为了让单元测试不依赖外部服务；同一入口对 PostgreSQL
直接跳过 DDL。这样测试便利性不会转化为生产请求隐式改表，也能让 `/api/health` 在业务查询
前报告 schema 未就绪。

### 一次性脚本必须退回兼容包装层

`scripts/migrate_mastery_v2.py`、`migrate_class_assignments.py`、`migrate_assignment_plans.py`、
`migrate_teacher_review_events.py` 和 `migrate_variation_attribution.py` 曾分别承担 schema
或数据改造。它们现在只保留参数兼容，事实来源是 Alembic 版本链和
`persistence/migration_support.py`；否则同一个数据库可能被不同脚本以不同顺序重复修改。

## 四、权威边界与本分支治理方案

当前数据库权威边界固定为：

1. **PostgreSQL 是正式运行时唯一数据库。** 正式 API、Worker 和部署环境都应通过
   `DATABASE_URL` 或 `POSTGRES_*` 指向 PostgreSQL；本地文件仍保存 PDF、Markdown、题图等资产，
   不替代业务数据库。
2. **Alembic 是 schema 版本权威。** 当前唯一正式版本链是
   `apps/api/migrations/versions/0001_adopt_current_schema.py` 到
   `0005_mistake_attributions.py`；生产变更必须进入有序 revision。
3. **SQLAlchemy metadata 描述当前模型。** `schema.py` 及各领域 Store 的表声明用于查询、
   registry、检查和新库建表语义；它们不授权业务进程直接改变 PostgreSQL。
4. **业务进程不得 DDL。** PostgreSQL 运行时不执行 `create_all()`、`ALTER TABLE` 或隐式回填；
   schema 不在 head 时，健康检查返回 `503 + SCHEMA_OUT_OF_DATE`，把缺失表/列/索引/外键和
   orphan 数据暴露为脱敏诊断。
5. **迁移必须可重复且保守。** 已有表只做已登记的 additive 补齐；mastery 旧投影保留；归因
   历史 append-only；添加 assignment 外键前先检查非空 orphan，发现风险就拒绝，而不是删除或
   改写数据。preflight 还区分 `autoFixable` 与 `manualActionRequired`。

### Alembic 0001–0005 的职责

| Revision | 职责 |
| --- | --- |
| `0001_adopt_current_schema` | adoption 现有 v0.27 schema 或部分遗留库，补齐 registry 可创建的表、已登记 additive columns 和索引；不覆盖已有数据。 |
| `0002_mastery_v2` | 必要时保留旧 `mastery_states` 为 `mastery_states_legacy`，补齐知识点身份并重建 mastery-v2 投影。 |
| `0003_assignment_governance` | 补齐班级、作业、assignment plan 及其索引；为 assignment 关联外键准备安全检查。 |
| `0004_teacher_variation` | 补齐教师复核事件、变式归因来源和变式尝试历史。 |
| `0005_mistake_attributions` | 创建 append-only 错因归因历史，并从旧错题兼容列做幂等双写/回填。 |

`env.py` 在 PostgreSQL 事务内取得 advisory lock；`migration_cli.py` 提供
`current`、`head`、`preflight`、`upgrade`、`verify` 五个统一入口；`schema_registry.py` 保证
六个领域的 metadata 在迁移、检查和隔离 SQLite 初始化中保持同一份注册事实。

## 五、为什么 SQLite 暂时保留

SQLite 当前只保留两个准确用途：

- 快速、隔离的后端单元测试，不占用共享服务，也不要求测试环境先创建数据库；
- 兼容遗留 SQLite 数据和迁移工具的验证路径，帮助把历史数据安全导入 PostgreSQL。

SQLite **不存放正式业务数据**，也不是生产 PostgreSQL 的隐式回退。新建 SQLite schema 仍可由
registry 初始化，是测试便利；已有 SQLite 遗留表无法原地追加 assignment 外键时，报告会保持
not-ready，不能把 SQLite 的结构限制伪装成生产约束已经存在。

本次审计的 2026-08-31 快照统计为：后端有 **16 个测试文件显式使用 SQLite**，生产代码和脚本
约有 **45 处 SQLite 引用**。这里的“引用”按实际测试/运行分支与兼容入口统计，不把文档说明和
重复注释当作新的运行能力。

## 六、完全删除 SQLite 的影响、退出条件与路线

完全删除 SQLite 会影响测试启动方式、`sqlite+pysqlite` URL 解析、JSON/JSONB 和 Upsert 方言
分支、SQLite 的 schema registry 初始化、遗留库兼容工具，以及当前依赖临时 `.sqlite3` 文件的
测试。直接删除会让单测需要数据库服务，也可能丢失对历史 SQLite 数据的可验证迁移入口。

阶段 1 已完成：正式 AppStore/DatabaseStore、Worker 和业务脚本在没有显式 PostgreSQL 配置时会直接失败；
`DOTTY_DATA_DIR` 只决定文件资产根目录，显式 SQLite URL 仅作为过渡测试能力保留。完全退出 SQLite
仍需满足以下剩余条件，不以“生产已经使用 PostgreSQL”作为唯一判断：

- 正式运行时、Worker、脚本和开发启动路径继续没有把 `DOTTY_DATA_DIR` 或 SQLite 当作数据库回退；
- 迁移工具已能在隔离 PostgreSQL 上覆盖 adoption、增量升级、幂等重跑、外键 orphan 拒绝、
  advisory lock 和 JSONB 默认值；
- 16 个 SQLite 测试文件已经迁移或明确删除，CI 不再依赖本地 `.sqlite3`；
- 历史 SQLite 导入已完成一次可审计演练并有备份、行数核对和回滚/重试方案；
- 运行手册、开发文档和健康检查不再把 SQLite 描述为正式数据库选项。

三阶段路线的当前状态如下：

1. **阶段 1（已完成）：取消正式运行时回退。** 保留 SQLite 测试和兼容工具，但生产/Worker
   若未提供 PostgreSQL 配置就明确失败；`DOTTY_DATA_DIR` 不再选择 SQLite，部署配置要求密码或
   完整的 `DATABASE_URL`，并用无凭据错误消息提示修复方向。
2. **把 PostgreSQL 特性与迁移测试迁到隔离 PG。** 为 Alembic、JSONB、外键、事务锁、并发
   Job Store 和 orphan 检查建立每个 worktree/session 独立的临时 PostgreSQL 数据库；保留
   `DOTTY_TEST_POSTGRES_URL` 这类显式隔离入口，绝不使用用户共享库。
3. **迁移余下测试并删除方言分支。** 将剩余单测从临时 SQLite 迁到隔离 PostgreSQL，确认
   所有测试不再依赖 SQLite 后，删除 SQLite URL/Upsert/初始化/`render_as_batch` 分支和遗留
   兼容脚本中的 SQLite 入口；最后再移除 SQLite 依赖与文档，而不是先删测试基础设施。

## 七、标准迁移流程

生产数据库变更固定按以下顺序执行：

```text
backup → preflight → upgrade → verify → deploy/restart
```

在 `apps/api` 下使用统一 CLI：

```bash
uv run python -m persistence.migration_cli current --database-url "$DATABASE_URL"
uv run python -m persistence.migration_cli head
uv run python -m persistence.migration_cli preflight --database-url "$DATABASE_URL"
uv run python -m persistence.migration_cli upgrade --database-url "$DATABASE_URL"
uv run python -m persistence.migration_cli verify --database-url "$DATABASE_URL"
```

`preflight` 和 `verify` 只读且不打印连接串；`upgrade` 在 PostgreSQL 事务中取得 advisory lock，
执行有序 revision，并在添加 assignment 外键前检查孤儿数据。`verify` 不通过时不能部署或重启
业务进程。迁移前必须有可恢复备份；发现手工项时先修复数据或结构，再重新 preflight。

多个 worktree/session **不得共享同一个可写数据库**。每个开发工作树应使用独立 PostgreSQL
数据库；自动化测试使用隔离数据库或临时实例；只有明确配置且与用户库隔离的
`DOTTY_TEST_POSTGRES_URL` 才能运行真实 PostgreSQL 集成测试。这样可以避免一个分支的迁移、
测试清理或 seed 数据改变另一个分支的事实状态。

相关实现入口：`apps/api/persistence/migration_support.py`、
`apps/api/persistence/migration_cli.py`、`apps/api/persistence/schema_registry.py`、
`apps/api/migrations/env.py`；架构背景见[系统架构](architecture.md)。
