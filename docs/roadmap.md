# 路线图与生产边界

本页只维护优先级索引和导航。文档职责与阅读入口见 [文档索引](README.md)；
当前实现以 [系统架构](architecture.md)、[代码地图](codebase-guide.md) 和 [API](api.md) 为准。
已完成状态表示仓库实现存在，不表示本次重跑了全部测试、真实部署或教学效果验证。

## 当前优先级索引（2026-10-02）

| 顺序 | 目标 | 状态 | 入口 |
| --- | --- | --- | --- |
| A 当前优先 | 数学教材章节 → 来源关联互动课程 | 待实施；复用上传/OCR/课程块，先章节基线与人工复核 | [产品路线图](product-roadmap.md)、[工程计划](engineering-roadmap.md#教材章节与跨学科实施计划2026-10-02) |
| B 下一阶段 | 英语教材阅读与证据定位 | 待实施；依赖 A，保留学科判定差异与数学回归 | [工程计划](engineering-roadmap.md#教材章节与跨学科实施计划2026-10-02) |
| C/D 后续 | 教学短视频 → 可打断语音 | 待实施；A/B 验收后启动 | [工程计划](engineering-roadmap.md#教材章节与跨学科实施计划2026-10-02) |
| 历史 AI 基础（已收口） | AI 工程方向：模型调用边界指标、评测语料继续扩充、陪练上下文分层 | 模型调用指标、陪练上下文切分与度量、多模态 TutorInput、受约束 ToolProposal、Tutor 评测实验室、最小画布和 PostgreSQL 全文检索第一版均已完成；Prefix Cache、跨模型评测和真实数据扩充纳入主动实验队列。AI 前沿实验线已建立，新增排序由 2026-10-02 章节与英语计划取代，跨模型和工具安全评测随实施批次开展。理由与范围见 [`product-roadmap.md`](product-roadmap.md) “优先级临时调整” | [`engineering-roadmap.md`](engineering-roadmap.md) |
| T0 | 知识点实体化 + 掌握度改为派生量 | 已完成（代码、迁移、验证） | [`engineering-roadmap.md`](engineering-roadmap.md) |
| P1 产品 | 作业指派（班级 + assignment）、班级掌握分布看板和班级级个性化作业 MVP；主用户明确为老师 | 第二版及个性化 MVP 已完成：脱敏证据→审阅→新试卷→确认创建（单机单库，无登录权限） | [`product-roadmap.md`](product-roadmap.md) |
| T1 | 金标准集补维度（公式/审核/陪练）、EvaluationEvidence 判题证据接入陪练、LLM-as-Judge 和学习漏斗报告 | 第一版已完成；人工金标准扩充至 50+ 题和跨模型统计横评仍待完成 | [`engineering-roadmap.md`](engineering-roadmap.md) |
| 并行卫生 | `local-demo` 收敛、Ruff/ESLint/Pyright 门禁、超长文件拆分边界评估 | 已完成（拆分执行按需触发，不单独排期） | [`engineering-roadmap.md`](engineering-roadmap.md) |
| 数据门控 | MathText 讲解通道、图片纯位置归属、subQuestions 多小问 | MathText 与 subQuestions 已完成；图片纯位置归属等待真实 Badcase 信号 | [`engineering-roadmap.md`](engineering-roadmap.md) |
| P1 教学法 | 分类型复习间隔、定量/定性双门槛、推进由掌握度算出、错因双归因 | 策略与 API 第一版已完成；多轮重新掌握和真实教学效果仍待验证；错因双归因已完成 | [`product-roadmap.md`](product-roadmap.md) |
| T2 韧性 | 批次熔断与系统性失败识别、部分成功状态、依赖自检 preflight | 已实现系统性失败熔断、部分成功汇总与依赖自检（含页面）；不是通用分布式熔断器 | [`engineering-roadmap.md`](engineering-roadmap.md) |
| 备选池 | 仿真卷、出题增量发射、拍照单次多模态、题图视觉复审、生成前审形状+成本估算（价值已论证，各自等触发信号） | 未排期 | [`product-roadmap.md`](product-roadmap.md) |
| P2 实验 | 互动数学库二选一、WebLLM 提示兜底、知识点树派生索引、动画表现层 | 纳入 AI 前沿实验线，主动评估并保持独立可回滚 | [工程路线图](engineering-roadmap.md#前端知识表达与互动技术选型) |
| 生产化 | 登录鉴权、多租户隔离、商业化、高可用和公网运营 | 明确暂缓 | [产品暂缓范围](product-roadmap.md#明确暂缓) |


## 执行边界

2026-10-02 起新增开发按教材章节互动课程 → 英语阅读与证据定位 → 教学短视频 → 可打断语音推进。
具体用户验收只在 [产品路线图](product-roadmap.md) 维护，实施批次与评测只在 [工程路线图](engineering-roadmap.md) 维护。
已有错题、作业、掌握度和复习能力作为回归基线；前沿工具围绕当前批次实验，不能绕过确定性状态机和证据链。

登录鉴权、多租户、生产级教师权限、商业化、高可用与匿名公网运营继续暂缓；
现有班级/作业是本地单机单库能力。A Level 多学科题库、英语作文总评分、发音评分和跨学科掌握度合并未排期。
详细范围以 [产品路线图的暂缓项](product-roadmap.md#明确暂缓) 为准；部署要求见 [部署与运维](deployment.md)。

## 专题设计入口

- [运行快照、事件和后台任务](runtime-governance-plan.md)：已实现边界及 G5 规划。
- [原题级试卷处理链路](exam-pipeline-refactor-plan.md)：分阶段 IR、缓存和质量门禁。
- [可编程课程与学习闭环](programmable-learning.md)：内容块、播放器和学习状态边界。
- [错题陪练设计](mistake-coach-plan.md)：单题状态机与用户闭环。
- [模型与系统测试报告](model-evaluation-report.md)：带日期的历史证据，不能当作当前验证结果。

## 下一产品阶段：自主多模态智能导师

保留此锚点以兼容已有链接。Tutor Turn Plan、多模态观察、shadow 工具提案和画像的设计见
[错题陪练设计](mistake-coach-plan.md#第七阶段自主多模态智能导师)，实现与实验状态见
[工程路线图](engineering-roadmap.md#当前-ai-能力实验路线2026-09-14)。此处不再维护第二套 A/B/C/D 任务列表；
新的阶段编号以 2026-10-02 教材章节与跨学科计划为准。
