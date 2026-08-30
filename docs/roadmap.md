# 路线图与生产边界

Dotty Tutor 当前是本地优先的 MVP。核心教材数字化、互动辅导和 AI 错题陪练闭环已经可用，覆盖
拍照录入、多轮陪练、掌握验证和 1/3/7 天复习，但尚未达到匿名公网服务所需的安全性、隔离性和
可运维性。

## 路线图使用方式

路线图已拆成产品路线和技术路线，避免“用户要什么”和“系统怎么实现”互相覆盖：

- **产品优先级**：见 [`product-roadmap.md`](product-roadmap.md)，只记录用户价值、产品阶段和产品验收。
- **技术优先级**：见 [`engineering-roadmap.md`](engineering-roadmap.md)，只记录正确性、质量、性能和基础设施。
- **详细设计**：见 [`runtime-governance-plan.md`](runtime-governance-plan.md) 和
  [`mistake-coach-plan.md`](mistake-coach-plan.md)。

本文件保留历史阶段和已完成记录，并提供一页式优先级索引；具体执行状态以两份新路线图为准。

## 当前优先级索引（2026-08）

| 顺序 | 目标 | 状态 | 入口 |
| --- | --- | --- | --- |
| T0 | 知识点实体化 + 掌握度改为派生量 | 已完成（代码、迁移、验证） | [`engineering-roadmap.md`](engineering-roadmap.md) |
| P1 产品 | 作业指派（班级 + assignment）与班级掌握分布看板；主用户明确为老师 | 待启动 | [`product-roadmap.md`](product-roadmap.md) |
| T1 | 金标准集补维度（公式/审核/陪练）、EvaluationEvidence 判题证据接入陪练、LLM-as-Judge 和学习漏斗报告 | 进行中（离线评测、Judge/Badcase 回放和学习效果/模型成本联合报告第一版已完成） | [`engineering-roadmap.md`](engineering-roadmap.md) |
| 并行卫生 | `local-demo` 收敛、Ruff/ESLint/Pyright 门禁、超长文件拆分边界评估 | 已完成（拆分执行按需触发，不单独排期） | [`engineering-roadmap.md`](engineering-roadmap.md) |
| 数据门控 | MathText 讲解通道、图片纯位置归属、subQuestions 多小问 | MathText 与 subQuestions 已完成；图片纯位置归属等待真实 Badcase 信号 | [`engineering-roadmap.md`](engineering-roadmap.md) |
| P1 教学法 | 分类型复习间隔、定量/定性双门槛、推进由掌握度算出、错因双归因（四条一组，依赖 T0） | 待启动 | [`product-roadmap.md`](product-roadmap.md) |
| T2 韧性 | 批次熔断与系统性失败识别、部分成功状态、依赖自检 preflight | 待启动 | [`engineering-roadmap.md`](engineering-roadmap.md) |
| 备选池 | 仿真卷、出题增量发射、内容块模板、教材定位与注释模型、拍照单次多模态、题图视觉复审、生成前审形状+成本估算（价值已论证，各自等触发信号） | 未排期 | [`product-roadmap.md`](product-roadmap.md) |
| P2 实验 | 互动数学库二选一、WebLLM 提示兜底、知识点树派生索引、动画表现层 | 按信号暂缓 | 本文件“前端知识表达与互动技术选型” |
| 生产化 | 登录鉴权、多租户隔离、商业化、高可用和公网运营 | 明确暂缓 | 本文件“暂缓范围” |

> **方向 ≠ 部署（2026-08-29）**：产品方向已明确为“先服务一个学科组、主用户是老师”，
> 由此新增作业指派和老师看板；但这只是**做什么功能**的判断，不改变部署形态。
> 登录鉴权、`org_id` 租户隔离和角色权限仍然暂缓到出现第一个真实试用的学科组为止——
> 班级和作业在单机单库下同样跑得通，而提前铺多租户是为可能不存在的客户付钱。
> 详见 [`product-roadmap.md`](product-roadmap.md) 的“服务对象”与“方向 ≠ 部署”两节。

执行原则：先修复会产生错误学习记录的问题，再补可复现性和质量证据，最后做性能/表现实验；任何新模型或新
框架都不能绕过现有状态机、质量门禁和运行快照。

## 已具备，不必再论证（2026-08-29）

一次与同类开源产品（DeepTutor）的架构对照中，下面这些“最佳实践”**本项目已经实现，且部分比参考
实现更严谨**。记录在此，避免以后再被当成改进建议重新提一遍：

| 能力 | 本项目现状 | 对照 |
| --- | --- | --- |
| OCR 内容寻址缓存 | `build_ocr_cache_key(content_hash, pages, provider, provider_version)`，带 `schema` 版本字段与入参校验，Provider 升级自动产生新键 | 参考实现无 schema 版本、不校验哈希格式 |
| 不可变审计与修订链 | `question_revisions`（带 `previous_revision_id`）+ `run_snapshots` 记录实际运行配置 | 参考实现的审计更薄 |
| 后台任务幂等 | `background_jobs.idempotency_key` + `uq_background_jobs_idempotency` 唯一索引 | 同等 |
| 内容质量评测闭环 | `apps/api/evaluation/`：badcase / judge / replay / compare / corpus | **参考实现没有对应物**，这是本项目的差异化资产 |
| OCR 引擎路由与降级 | `choose_ocr_provider` 按信号自动路由（短文字+图片、公式信号 → MinerU，其余走 pypdf）；`ocr_preflight` 的扫描页信号参与路由；**质量门禁不达标时向上升级** pypdf → MinerU；运行时异常降级到 `text-layer-fallback` → `ocr-failed`，`run` 里记 `fallback`/`mode` 可追溯 | 参考实现只有全局单引擎 + 失败即跳过，**无自动探测、无降级链、无向上升级** |
| MinerU 产物定位 | `OcrRuntime.parse` 用临时目录 + `rglob("*.md")` 按文件大小降序取最大的一份 | 参考实现要靠三级候选目录兜底去捞 CLI 产物树；本项目这一招更简单，且同样不受 MinerU 版本目录结构变动影响 |
| 语音多级回退 | Azure Speech → Qwen3-TTS → 浏览器 Web Speech | 参考实现只有适配层，无回退链 |
| 角色分离 | 学生端不暴露 OCR、模型和上传配置 | 参考实现把模型/引擎选择全部摊给用户 |

其中评测闭环值得单独说：K12 内容的正确性是红线，“题目经过哪些自动校验、历史 badcase 全部回归”
是可以对外讲的信任凭证，而不只是内部工具。这条在产品层面被低估了。

## 当前限制

- API 没有登录、授权、用户、班级或租户隔离。
- PDF 完成和批次 OCR/生成已进入 PostgreSQL 后台任务；单页导入、Help 和普通模型调用仍保持同步。
- 模型/OCR 选择和部分读取缓存属于单个 FastAPI 进程。
- PDF、Markdown 和题图仍保存在本地文件系统。
- 生成阶段会对结构失败的单题自动修复一次；仍失败的题在发布时自动隔离，整套无合格题时安全阻断。
- 单页快速导入没有进入完整审校和持久化流水线。
- 选择、多选、填空和数值题已由确定性答案引擎判定（`apps/api/answer_evaluator.py`）；简答题和证明类
  内容仍无统一判题，多小问结构（`subQuestions`）已支持，但 tutor-only 小问仍不进入自动掌握判定。
- 快速预览模式最多展示 5 道题；整卷生成模式上限为 100 题。
- 数据库表由 `create_all()` 初始化；mastery-v2 已有一次性迁移脚本，但尚无通用 Alembic 迁移历史。
- 已有结构化日志和请求 ID，但尚无集中式指标、追踪、错误监控和自动备份。
- 错题章节和知识点目前由模型建议、学生确认，尚未关联版本化教材知识树。
- 知识点已通过 `knowledge_points` 建立稳定实体，当前按发布版本作用域隔离；掌握度已改为按最新不同题证据
  派生，并设置低证据置信度上限。跨教材聚合仍未建模，需等老师视图的真实使用场景再决定身份维度。
- 没有班级、作业指派和教师视图：学生自选已发布试卷，老师看不到班级层面的掌握分布。
- 错题陪练已实现对话线程、受约束状态机、按错误原因生成的变式验证、进阶本迁移和 1/3/7 天复习进度闭环。

## 工程决策摘要

- **暂不引入 LangChain、LangGraph 等代理编排框架**：现有路由/领域服务/状态机已覆盖全部流程。
  只有出现"多代理并行编排、跨请求长流程恢复、同一编排逻辑在两个以上流程重复"信号时，才局部评估；
  详细触发条件见 [engineering-roadmap](engineering-roadmap.md) 的架构边界一节。
- **暂不引入 TanStack Query / Zod**：理由和重评条件见 engineering-roadmap 的前端工具决策一节。
- **学习数据采用显式迁移**：`create_all()` 只初始化缺失表；mastery-v2 通过
  `scripts/migrate_mastery_v2.py` 进行 dry-run/apply/verify。进入公网生产前仍需补齐通用版本化迁移历史、
  备份恢复演练和回滚流程。

## 下一阶段：AI 运行治理与后台任务

运行快照、内容生产事件、Job Store 和单 Worker 已落地；详细设计与 PR 边界见
[AI 运行治理与后台任务演进计划](runtime-governance-plan.md)，剩余工作并入
[engineering-roadmap](engineering-roadmap.md) T1 执行队列：陪练结构化日志和统一
`ModelRequest` / `ModelResult`；脱敏离线评测集与 Badcase/Judge 回放第一版已落地。

Redis、OpenTelemetry、LangGraph 和 MCP 都属于按信号升级项，不是完成上述计划的前置依赖。

## 前端知识表达与互动技术选型

这部分路线服务于“知识动画 + 结构化内容 + 互动学习”，作为学习项目采用渐进式选型，不以堆叠技术名词为目标。
当前项目已经使用 React、结构化 `LessonDocument`/`LessonBlock`、`rendererRegistry`、KaTeX/`MathText`、
Canvas/SVG 和错题掌握闭环。新增能力必须先复用这些边界，不能为不同入口再复制一套题目或课程渲染器。

### 当前已采用

- [x] 使用 KaTeX 和统一 `MathText` 处理题干、选项、题目条件与讲解中的标准 LaTeX。
- [x] 使用结构化课程块连接题目、讲解、提示、公式、图形和 TTS，而不是把整节课保存为一段 HTML。
- [x] 使用 React 组件、Canvas/SVG 和 Renderer Registry 支撑选择题、判断题、画线题和分步讲解。
- [x] 使用错题本、变式题、掌握验证和 1/3/7 天复习任务实现 Khan/Duolingo 式学习闭环的基础版本。

### 第一阶段：稳定内容模型和互动渲染边界

- [ ] 在 `apps/web/src/types/lesson.ts` 和 `apps/api/domain/contracts/lesson.py` 明确 `markdown`、`formula`、`diagram`、
  `interactive-math`、`quiz`、`animation` 内容块的版本化契约。
- [ ] 新增 `InteractiveMathCanvas` 渲染边界；页面只组合它，不直接依赖具体绘图库。
- [ ] 为每种内容块补充 MathText、图片、键盘输入、画布动作和错误回退的 Playwright 回归案例。
- [ ] 在文档中记录“Canvas 负责图形，HTML/MathText 负责公式文字”的边界，避免公式重新回到 `fillText` 路径。

验收标准：同一份 LessonBlock 可以在内容生产端预览和学生端消费；新增渲染器不修改题目业务状态；公式、图片和
交互状态在刷新后仍有明确的恢复或丢弃策略。

### 第二阶段：选择一个互动数学库做小范围实验

只选择一种，不同时引入 Mafs、JSXGraph 和 Desmos：

- [ ] 函数图像、坐标系、滑块和参数探索：评估 Mafs，限定在 `interactive-math` 实验题型。
- [ ] 点、线、圆、拖拽几何构造和证明：评估 JSXGraph，限定在几何题 Renderer。
- [ ] 只有在确实需要外部计算器体验且能接受外部运行时依赖时，才评估 Desmos API。
- [ ] 无论选择哪个库：`interactionSpec` 产生的结构化作答复用现有 `answerSpec` 判题契约，
  不新建第二套评分机制；Schema 中禁止出现具体绘图库或组件名称。

评估记录至少包括：React 集成复杂度、无障碍能力、移动端触控、导出/回放、许可证、包体积、离线运行和与现有
题目答案契约的适配成本。实验成功后只保留一个库，并通过 `InteractiveMathCanvas` 隔离；实验失败则回退到现有
Canvas/SVG，不影响其他题型。

### 第三阶段：动画生产与反馈动效

当前唯一需要实施的表现能力是 CSS transition；Lottie、Manim 和 Motion Canvas 都是未引入的实验项。
分层图、职责表和不变量见[可编程课程与学习闭环](programmable-learning.md)。

- [ ] 答题反馈先用 CSS transition；动效设计稳定后再评估 Lottie；仅用于表现层，不承载学习状态，
  动画层不得回写掌握度或参与判题。
- [ ] Manim 分两阶段：实验期只做"本地离线脚本 → 预生成视频 → 静态资源 → `<video>` 加字幕和暂停点"；
  只有出现动态生成需求或同步渲染耗时影响接口时，才建设后台渲染链路。现有 Job Store 和 Worker 可以
  复用，但 Manim 渲染运行时、依赖、资源生命周期和存储链路均不存在，需要按新任务类型补齐。
- [ ] 需要浏览器内实时改变参数时，评估 Motion Canvas；不与 Manim 同时作为同一条生产链路的必选依赖。
  两者用途不同（预生成视频 vs 可编程时间轴），不适用"最终只留一个"，但当前阶段只验证其中一个真实场景，
  不同时建设两条内容生产链。
- [ ] 学生可操作的数学交互始终走 `InteractiveMathCanvas`，不混入动画系统。

### 暂不引入

- [ ] MathJax：当前 KaTeX 已统一且性能更适合题目列表；只有遇到 KaTeX 不支持的教材公式时再做局部评估。
- [ ] MDX/unified：当前模型生成内容以 JSON 契约为主；人工课程创作需求出现后再作为离线内容生产格式。
- [ ] Khan Perseus：借鉴其 Widget、答案契约和渐进提示设计，不直接替换现有题目系统。
- [ ] LangChain、LangGraph 和多智能体：继续遵循“出现复杂跨请求编排信号后局部评估”的决策。

建议拆分为独立 PR：`refactor/lesson-block-contracts`、`feature/interactive-math-canvas`、
`experiment/mafs-renderer` 或 `experiment/jsxgraph-renderer`、`chore/animation-worker`。每个实验都必须
可单独回滚，不能与登录、试卷发布或掌握度迁移混在一起。

## 学生端与内容生产端边界

- `/studio`：内容生产者上传教材、选择 OCR/模型、生成和预览互动内容。
- `/learn`：学生消费已发布内容，进入个人错题本和掌握复习，不提供整本 PDF 上传或模型配置。
- `/mistakes`：学生空间下的单题错题录入与陪练；保留拍摄单道错题，不承担教材生产。

## 错题陪练交付顺序（已全部完成）

"录入一题 → 陪练 → 验证掌握 → 1/3/7 天复习"闭环已交付，交付记录见
[AI 错题陪练产品规划](mistake-coach-plan.md)。剩余开放项：微信内浏览器与弱网体验验证。

## 暂缓范围：A Level 多学科与生产级服务

为保持个人学习项目的边界清晰，以下方向暂不纳入当前开发、验收和版本发布：

- **A Level 多学科题库**：暂不扩展 A Level 数学、物理、化学等学科，也不建设跨学科课程内容。当前只验证
  中文教材/初中数学的 OCR、结构化题目、错题陪练和掌握复习闭环。等现有闭环稳定，并且准备好真实的学科
  数据集和评测集后，再单独规划学科扩展。
- **生产级服务能力**：暂不建设多租户登录、教师/班级权限、7×24 SLA、高可用与弹性扩缩容、集中式监控、
  计费、合规审计和公网运营能力。异步任务、对象存储、配额、认证等内容仍保留在后续 P0/P1 规划中，
  但不阻塞本地 Demo 的学习和演示。

当前优先级仍是：单题体验、题目/公式/图片质量、陪练状态机、掌握验证、复习闭环和可重复测试。

## 质量闭环与工程机制指针

离线评测集、Badcase 回放、学习漏斗指标、OCR 预检、模型能力目录、陪练上下文分层等任务的
验收标准统一维护在 [engineering-roadmap](engineering-roadmap.md)，本文不再重复。
Agent 只作为开发期工具使用（读报告、跑脚本），不进入生产运行时。

## 下一产品阶段：自主多模态智能导师

现有错题陪练已经具备一题一线程、确定性判题、受约束状态机、变式验证和复习闭环，但每轮回复仍主要由
固定阶段和提示层级驱动。下一阶段不改造成开放式 Agent，也不让模型直接写学习状态，而是在现有状态机
之上增加一个可解释的 `Tutor Turn Plan`：系统先判断本轮意图、误区和教学动作，再让模型负责自然表达。

### 阶段 A / A.1：可解释的单轮教学计划（已完成）

`Tutor Turn Plan` 已落地：结构化教学计划、八类学生意图识别、带证据和置信度的误区假设、
单一教学动作约束、重复提示的确定性升级回退，均有单元测试覆盖。验收记录见 git 历史与
`apps/api/tests/test_tutor_turn_plan.py`。

### 阶段 B：统一多模态输入

- [ ] 定义统一 `TutorInput`，承载文字、结构化答案、题图裁切、画板快照和公式识别结果。
- [ ] 图片和画板先进入独立的理解适配器，产出“观察事实 + 置信度 + 证据区域”，不直接拼接为长文本。
- [ ] 低置信度结果要求学生确认；原图、识别文本和人工修正分层保存，避免覆盖原始证据。
- [ ] 为移动端上传、取消、压缩、失败重试和隐私提示补充交互与 Playwright 测试。

### 阶段 C：受约束工具与学习者画像

- [ ] 将判题、错误原因建议、提示选择、变式生成、掌握更新和复习安排暴露为显式工具契约。
- [ ] 模型只能提出工具调用建议；状态机、Schema 校验和领域服务决定是否执行。
- [ ] 建立按知识点聚合的学习者画像，仅保存错误模式、提示依赖和掌握证据，不保存无边界聊天历史。
- [ ] 回复上下文分为题目快照、线程摘要、最近必要消息和知识点画像，并分别设置长度与生命周期。

### 阶段 D：评测、观测与按信号升级

- [ ] 建立脱敏回放集，测量判题一致性、提示重复率、阶段越权率、首次有效提示率、耗时和调用次数。
- [ ] 为模型超时、回退、工具拒绝、图片理解低置信度和状态迁移记录稳定事件。
- [ ] 只有出现跨请求暂停恢复、复杂并行工具或两个以上流程重复编排时，才局部评估 LangGraph；
  LangChain、LangGraph 和多智能体都不是阶段 A 至 C 的前置依赖。

建议按 `feature/tutor-turn-planning`、`feature/multimodal-tutor-input`、
`feature/tutor-tools-profile` 和 `test/tutor-evaluation-suite` 拆分 PR，每个阶段可单独测试和回滚。

## 当前阶段：互动试卷发布与学生消费

发布状态机（`draft → in_review → published → archived`）、送审/发布门禁、失败题自动隔离、
整套重新审核的新版本链、学生端作答与离线同步均已完成并有测试覆盖。

- [ ] 在内容生产端增加撤回/归档按钮，并补充对应 Playwright 流程。

这一阶段优先复用现有教材、课程、学习会话和题型组件，不另建一套试卷渲染器。教师后台、班级管理和
复杂排课等协作能力继续放在受控公网测试之后。

## P0：受控公网测试（暂缓，准备公网时启动）

- [ ] 用户登录、角色权限和教材资源归属。
- [ ] 未审核题目禁止发布给学生。
- [ ] 对象存储或可靠持久卷，以及资源生命周期管理。
- [ ] 数据库迁移与备份恢复、低权限账号、SSL。
- [ ] 上传配额、限流、恶意文件扫描和数据保留策略。
- [ ] 错误追踪和 readiness/liveness；HTTPS 与可信 Host 配置。

## P1：协作与质量（已完成）

静态检查门禁见 engineering-roadmap T1；main 分支 Ruleset、Python 可复现依赖锁定（uv.lock）、
CodeQL 扫描、Actions SHA 固定与真实 PostgreSQL 集成测试（CI service container）
、API 错误脱敏（Problem JSON 响应不含内部信息）与
管理员调试入口（`DOTTY_DEBUG_TOKEN` 门控的环形缓冲查询）已完成。P1 收尾全部闭环。

## P2：产品能力（按需）

用户/班级模型、渲染 worker 与对象存储、整本索引检索、更强数学判题引擎、TTS 缓存与 CDN、
绘图 DSL、国际化和语义化发布——只有对应产品需求出现后才排期。

## 架构演进方向

当前布局：pnpm monorepo（`apps/web` + `apps/api`），单实例 FastAPI + PostgreSQL Job Store +
独立 Worker；以下为按指标升级的演进路径，不是现状。

```text
浏览器（React SPA）
  → apps/api（FastAPI）
       ├─ PostgreSQL（关系数据 + Job Store）
       ├─ 对象存储（暂缓）
       └─ 独立后台 Worker（已落地）
              ├─ OCR / 题目生成与审校
              └─ 按指标升级 Redis / 多 Worker / TTS 预生成（暂缓）
```

在进程内状态外部化之前，不应简单增加 Uvicorn worker 数量；否则不同进程看到的模型选择、
任务状态和缓存可能不一致。
