# 路线图与生产边界

本页只维护优先级索引和导航。文档职责与阅读入口见 [文档索引](README.md)；
当前实现以 [系统架构](architecture.md)、[代码地图](codebase-guide.md) 和 [API](api.md) 为准。
已完成状态表示仓库实现存在，不表示本次重跑了全部测试、真实部署或教学效果验证。

## 当前优先级索引（2026-10-12）

| 顺序 | 目标 | 状态 | 入口 |
| --- | --- | --- | --- |
| 当前优先 | 已生成英语/数学课件的教师内容复核与整单元覆盖验收 | v0.37.0 工程实现及英语五页、代数三页、几何五页真实模型试点已交付；本机旧英语教材产物已可恢复撤回；教师复核与整单元教学质量待完成；试卷保留原题生产 | [独立验收记录](textbook-courseware-acceptance.md) |
| 下一阶段 | 教材区域图像、课件媒体/语音适配与更多学科扩展 | 当前原图为整页预览；媒体/语音沿用章节发布契约，课件适配待实施；正式生成仅注册英语/数学，第三学科仅注册 smoke 通过 | [工程后续队列](engineering-roadmap.md#教材课件后续队列2026-10-12) |
| 界面收尾 | Bright 视觉迁移 | 首页、学生今日、做题、错题与陪练已迁移；讲解/章节学习/课件、教师工作台、教材工作室待迁移 | [界面设计规范](design-system.md#迁移状态) |
| 历史 A/B 基础 | 数学来源关联课程、英语阅读与证据定位 | 工程第一版已有；不等于本次多页课件完成，真实材料质量仍需复核 | [原 A/B 计划](chapter-ab-implementation-plan.md) |
| C/D 基础 | 教学媒体与步骤语音 | 工程实现已接入；复用既有能力，真实教材/视频审校/真人语音质量仍待人工验收 | [工程计划](engineering-roadmap.md#教材章节与跨学科实施计划2026-10-02) |
| 历史 AI 基础（已收口） | AI 工程方向：模型调用边界指标、评测语料继续扩充、陪练上下文分层 | 模型调用指标、陪练上下文切分与度量、多模态 TutorInput、受约束 ToolProposal、Tutor 评测实验室、最小画布和 PostgreSQL 全文检索第一版均已完成；Prefix Cache、跨模型评测和真实数据扩充纳入主动实验队列。新增排序以 2026-10-11 教材课件计划为准，跨模型和工具安全评测随实施批次开展。理由与范围见 [`product-roadmap.md`](product-roadmap.md) “优先级临时调整” | [`engineering-roadmap.md`](engineering-roadmap.md) |
| T0 | 知识点实体化 + 掌握度改为派生量 | 已完成（代码、迁移、验证） | [`engineering-roadmap.md`](engineering-roadmap.md) |
| P1 产品 | 作业指派（班级 + assignment）、班级掌握分布看板和班级级个性化作业 MVP；主用户明确为老师 | 第二版及个性化 MVP 已完成：脱敏证据→审阅→新试卷→确认创建（单机单库；可显式启用受保护会话） | [`product-roadmap.md`](product-roadmap.md) |
| T1 | 金标准集补维度（公式/审核/陪练）、EvaluationEvidence 判题证据接入陪练、LLM-as-Judge 和学习漏斗报告 | 第一版已完成；人工金标准扩充至 50+ 题和跨模型统计横评仍待完成 | [`engineering-roadmap.md`](engineering-roadmap.md) |
| 并行卫生 | `local-demo` 收敛、Ruff/ESLint/Pyright 门禁、超长文件拆分边界评估 | 已完成（拆分执行按需触发，不单独排期） | [`engineering-roadmap.md`](engineering-roadmap.md) |
| 数据门控 | MathText 讲解通道、图片纯位置归属、subQuestions 多小问 | MathText 与 subQuestions 已完成；图片纯位置归属等待真实 Badcase 信号 | [`engineering-roadmap.md`](engineering-roadmap.md) |
| P1 教学法 | 分类型复习间隔、定量/定性双门槛、推进由掌握度算出、错因双归因 | 策略与 API 第一版已完成；多轮重新掌握和真实教学效果仍待验证；错因双归因已完成 | [`product-roadmap.md`](product-roadmap.md) |
| T2 韧性 | 批次熔断与系统性失败识别、部分成功状态、依赖自检 preflight | 已实现系统性失败熔断、部分成功汇总与依赖自检（含页面）；不是通用分布式熔断器 | [`engineering-roadmap.md`](engineering-roadmap.md) |
| 备选池 | 仿真卷、出题增量发射、拍照单次多模态、题图视觉复审、生成前审形状+成本估算（价值已论证，各自等触发信号） | 未排期 | [`product-roadmap.md`](product-roadmap.md) |
| P2 实验 | 互动数学库二选一、WebLLM 提示兜底、知识点树派生索引、动画表现层 | 纳入 AI 前沿实验线，主动评估并保持独立可回滚 | [工程路线图](engineering-roadmap.md#前端知识表达与互动技术选型) |
| 生产化 | 多租户、个人教师账号、商业化、高可用和公网运营 | 暂缓；已有可选受保护会话与公网配额 | [产品暂缓范围](product-roadmap.md#明确暂缓) |


## 执行边界

2026-10-11 起本次新增开发按旧教材产物清理 → 英语课件 → 数学课件 → 学科扩展推进，
技术设计与专项验收见 [教材互动课件实施计划](textbook-courseware-plan.md)。该顺序取代历史 A/B 的新增排期；
历史章节、英语阅读与 C/D 设计继续作为基础与后续参考。全局产品范围仍见 [产品路线图](product-roadmap.md)，
全局工程与实验边界见 [工程路线图](engineering-roadmap.md)。
已有错题、作业、掌握度和复习能力作为回归基线；前沿工具围绕当前批次实验，不能绕过确定性状态机和证据链。

个人教师账号、多租户、商业化、高可用与匿名公网运营继续暂缓；已有受保护模式和可选配额的当前接口见 API 文档。
现有班级/作业是本地单机单库能力。A Level 多学科题库、英语作文总评分、发音评分和跨学科掌握度合并未排期。
详细范围以 [产品路线图的暂缓项](product-roadmap.md#明确暂缓) 为准；部署要求见 [部署与运维](deployment.md)。

## 专题设计入口

- [运行快照、事件和后台任务](runtime-governance-plan.md)：已实现边界及 G5 契约。
- [原题级试卷处理链路](exam-pipeline-refactor-plan.md)：分阶段 IR、缓存和质量门禁。
- [可编程课程与学习闭环](programmable-learning.md)：内容块、播放器和学习状态边界。
- [教材互动课件实施计划](textbook-courseware-plan.md)：本地 OpenMAIC 参考、英语/数学课件与旧教材产物清理；当前按 [执行计划](textbook-courseware-execution-plan.md) 实施，结果见 [独立验收记录](textbook-courseware-acceptance.md)。
- [错题陪练设计](mistake-coach-plan.md)：单题状态机与用户闭环。
- [模型与系统测试报告](model-evaluation-report.md)：带日期的历史证据，不能当作当前验证结果。

## 下一产品阶段：自主多模态智能导师

保留此锚点以兼容已有链接。Tutor Turn Plan、多模态观察、shadow 工具提案和画像的设计见
[错题陪练设计](mistake-coach-plan.md#第七阶段自主多模态智能导师)，实现与实验状态见
[工程路线图](engineering-roadmap.md#当前-ai-能力实验路线2026-09-14)。此处不再维护第二套 A/B/C/D 任务列表；
本次教材课件阶段编号以 [2026-10-11 实施计划](textbook-courseware-plan.md) 为准。

2026-10-11：新增系统内原页复核、不可变案例修订、受限模型盲评、教学媒体和步骤语音；
18 个真实教材案例仍待用户复核，独立双审 50+ 人工金标准与正式跨模型排名未完成。
本轮工程门禁和发布状态以[验收记录](quality-media-voice-acceptance.md)为准。

来源复核融入「我的教材 / 章节复核」，视频绑定发布快照，模型盲评位于高级教学工具；整合与验收见[记录](material-review-integration.md)。教材互动课件生成已随 v0.37.0 交付，教师内容复核与扩展范围按上述入口继续推进。

## 本次代码与运行核对（2026-10-12）

main 已到 `4ab30e98`（[PR #289](https://github.com/ningkaikok/dotty-tutor/pull/289)）；该提交的 [CI](https://github.com/ningkaikok/dotty-tutor/actions/runs/38232093507) 与 [CodeQL](https://github.com/ningkaikok/dotty-tutor/actions/runs/38232093548) 均成功。
发布准备前的最新 Release 为 [v0.37.0](https://github.com/ningkaikok/dotty-tutor/releases/tag/v0.37.0)，旧教材计数修复、Bright 视觉及本轮组件/教材质量扩展合并整理为 v0.38.0。
`deploy/render-supabase` 当前为 `dc1e18f2`，尚未包含这两项变化；本机 8010 服务的运行 checkout 为 `039ba196`，也未包含 Bright 改版。更新审查工作区不等于更新运行服务。

本轮工程扩展和材料恢复已经完成独立验收，见[执行计划](frontend-quality-expansion-execution-plan.md)与[验收报告](frontend-quality-expansion-acceptance.md)。本机原库已恢复 18 待审案例及英语/代数/几何 3 份原始课件草稿，均未经人工批准；完整单元仍有生成和审核缺口。组件系统、区域裁图、课件媒体/语音适配、science profile、人工双审与正式盲评门槛已实现于本工作区。

本机原运行代码未替换，数据库仍为 0024；新前端和 0025 仅在隔离克隆验收。真实扫描 OCR、教师内容质量、第三学科真实教材、50+ 双审金标准和正式跨模型结果继续待完成；没有新增真实模型调用。用户随后授权发布 v0.38.0 并同步部署分支；GitHub 标签/Release 与部署分支需分别核验，原本机运行切换不属于此次发布。
