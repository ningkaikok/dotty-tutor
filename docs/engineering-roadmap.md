# Dotty Tutor 技术路线图

> 本文只回答“怎样可靠地实现产品路线”。它是技术任务的唯一优先级入口，详细运行治理见
> [`runtime-governance-plan.md`](runtime-governance-plan.md)，产品目标见
> [`product-roadmap.md`](product-roadmap.md)。

## 技术优先级规则

- **T0 正确性**：直接阻塞演示或会产生错误学习记录的问题必须先修复。
- **T1 可复现性**：让一次模型/OCR运行可以定位、评测和回放。
- **T2 可靠性与性能**：让长任务可恢复、模型调用可控、陪练延迟可测量。
- **T3 实验能力**：只有产品验证需要时才引入新库、新模型或新基础设施。

## T0：正确性与回归保护

- [ ] 统一 `MathText`、图片 URL 和 Markdown 解析，覆盖题干、选项、条件、讲解、历史消息和错误回退。
- [ ] 固化学生答案状态、重新提交、下一题、完成态和验证题重试的 Playwright 流程。
- [ ] 学生做题阶段禁止自动 TTS；切题、离开页面或重复请求时取消过期请求。
- [ ] 区分单题修复、重新 OCR、重新生成和整套重新审核，记录新旧版本和实际运行配置。
- [ ] 清理或隔离旧线程、归档错题和演示数据，避免历史状态污染新测试。

## T1：质量、评测与可观测性

1. 建立脱敏金标准集，覆盖 OCR、公式、题图、七类题型和陪练。
2. 统一 Badcase 标签，支持从失败样本重放并比较结构、评分、耗时和调用次数。
3. 创建不可变 `RunSnapshot`，记录模型、Prompt、Schema、OCR Provider 和校验器版本。
4. 让 `run_id` 贯穿 OCR、生成、审校、发布、陪练和结构化日志。
5. 使用确定性指标评估答案/结构，使用独立审核模型评估讲解质量，并记录评分依据和置信度。
6. 建立学习效果和模型成本的 PostgreSQL 聚合报告，不提前引入独立数据平台。

## T2：模型、OCR 与上下文优化

### 模型能力目录

- [ ] 注册 provider、model、角色（生成/审核/陪练/视觉）、能力标签、上下文上限、延迟、成本和回退模型。
- [ ] 增加健康状态和失败原因，但不能覆盖已开始运行的 `RunSnapshot`。
- [ ] 按任务能力筛选模型；模型切换前后必须通过固定评测集比较效果和成本。

### OCR 预检

- [ ] 正式 OCR 前识别空白页、出版信息页、无图页、公式密集页和图文混排页。
- [ ] 为每本教材生成脏页摘要，预检只决定路由，不删除原始页面。
- [ ] 将预检失败样本进入 Badcase 回放，复用现有页面级质量门禁、局部重试和内容缓存。

### 陪练上下文分层

- [ ] 将系统规则、工具定义、题目上下文、Schema 组成稳定前缀；学生输入和最近消息组成动态后缀。
- [ ] 先记录稳定/动态 token、耗时、调用次数和回退率，再判断 Prefix Cache 是否有实际收益。
- [ ] 只有 Provider 明确支持时才启用缓存；缓存键包含模型、Prompt、Schema、题目版本和知识点版本。

## T2：可恢复的长任务

- [ ] 复用 PostgreSQL `upload_jobs` 和单 Worker，支持任务 ID、独立进度、有限并发和限流。
- [ ] 增加取消、重试、租约恢复和幂等键，Worker 重启不重复发布或丢失已完成页面。
- [ ] 为多 PDF、批量 OCR、批量生成和整套重新审核提供成功/失败/隔离汇总。
- [ ] 只有吞吐基准证明单 Worker 不足时，才评估 Redis 或其他队列。

## T3：技术实验

- [ ] 交互数学只选择一个库实验（Mafs 或 JSXGraph），由 `InteractiveMathCanvas` 隔离。
- [ ] 批量视频需求出现后再评估 Manim；实时参数动画再评估 Motion Canvas。
- [ ] 反馈动效稳定后再评估 Lottie；不让动画库承载学习状态。
- [ ] RAG 先使用 PostgreSQL 元数据/全文检索，只有评测证明不足时再评估向量数据库。
- [ ] TTS 音频缓存、对象存储和 CDN 只有在真实延迟或流量指标达到阈值后实现。

## 架构边界

- 生产流程由路由、领域服务、Runtime/Store、Worker 和质量门禁执行。
- Agent 只用于开发期读报告、运行脚本、汇总进度和生成修复建议，不能直接修改学生掌握度或发布状态。
- 不为单一调用创建空 Repository/Manager/Factory；出现第二种实现或测试替身时再抽象。
- 暂不引入全局 LangChain/LangGraph、Multi-Agent、独立 Control Plane、Redis/Kafka 或 Tool Gateway。

## 推荐 PR 顺序

`fix/student-answer-flow` → `fix/formula-image-rendering` → `fix/tutor-request-boundary` →
`fix/question-repair-flow` → `test/offline-ai-evaluation` → `feat/run-snapshot-events` →
`feat/model-capability-registry` → `feat/ocr-preflight-report` → `feat/resumable-content-jobs`。

每个 PR 只处理一个主题，必须有测试、文档和回滚边界；不要把新模型接入、UI 重构和数据库迁移混在一起。
