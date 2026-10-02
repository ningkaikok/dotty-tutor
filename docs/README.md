# 文档索引与维护职责

先从 [项目 README](../README.md) 了解可演示能力，再按任务选择下列入口。
文档分为当前实现、未来规划、历史证据和操作指南；规划中的能力不代表已实现，历史测试通过不代表当前工作区验证通过。

## 阅读入口

| 任务 | 入口 | 本文档负责维护的内容 |
| --- | --- | --- |
| 启动与调试 | [本地开发](development.md) | 环境、配置、启动命令与排错 |
| 找代码与扩展边界 | [代码地图](codebase-guide.md) | 文件树、依赖方向、复用与扩展位置 |
| 理解当前系统 | [系统架构](architecture.md) | 组件职责、实际调用链与持久化边界 |
| 调用接口 | [API](api.md) | 端点表、请求响应语义与错误边界；精确类型以应用 OpenAPI 为准 |
| 看下一步 | [优先级索引](roadmap.md) | 简短状态索引与专题导航，不维护完整设计或第二套待办 |
| 定义用户价值 | [产品路线图](product-roadmap.md) | 产品范围、优先级、用户验收与暂缓项 |
| 安排开发与实验 | [工程路线图](engineering-roadmap.md) | 实施批次、技术选型、依赖、实验与交付门禁 |
| 理解课程播放 | [可编程课程](programmable-learning.md) | LessonDocument、渲染与学习状态边界 |
| 理解单题辅导 | [错题陪练设计](mistake-coach-plan.md) | 单题状态机、用户闭环与专题设计 |
| 理解内容生产 | [原题处理链路](exam-pipeline-refactor-plan.md) | IR 分阶段改造、兼容设计与实施状态 |
| 理解任务治理 | [运行治理设计](runtime-governance-plan.md) | 快照、事件、Job/Worker、恢复与网关设计 |
| 学习后端 | [后端学习指南](backend-learning-guide.md) | 按代码路径解释设计与学习练习，不维护独立能力清单 |
| 学习前端 | [前端学习指南](frontend-learning-guide.md) | 页面、Hook、组件、渲染与测试的教学解释 |
| 管理数据库 | [数据库演进](database-evolution.md) | 带日期的演进依据、schema 治理原则与迁移背景 |
| 理解知识点身份 | [ADR-001](adr/001-knowledge-point-identity-and-mastery-v2.md) | 已采用的知识点与 mastery-v2 决策及后果 |
| 部署与恢复 | [部署运维](deployment.md) | Docker/systemd/Nginx、迁移、备份与部署步骤 |
| 查运行问题 | [可观测性](observability.md) | 日志事件、隐私边界与告警配置 |
| 准备真实试用 | [学科组试用](pilot-plan.md) | 访谈、观察、指标口径与失败判据，不表示已开展试用 |
| 查实验依据 | [模型测试报告](model-evaluation-report.md) | 带日期、样本和限制的历史实测与未完成评测 |

开发代理规则只在 [AGENTS.md](../AGENTS.md) 维护；人类贡献、分支/提交格式与发布步骤见
[CONTRIBUTING.md](../CONTRIBUTING.md)。用户可见版本变化见 [CHANGELOG](../CHANGELOG.md)。
人工标注操作见 [审题队列](../apps/api/evaluation/benchmark/review_queue/README.md)，后端测试夹具与纪律见
[测试说明](../apps/api/tests/README.md)。

## 同步规则

- 当前组件/调用链变化更新 architecture；文件或领域边界变化更新 codebase-guide；接口变化更新 api。
  新增顶层模块、响应字段或端点时遵守 AGENTS.md 的同批文档同步要求。
- 产品验收只在 product-roadmap 维护，技术执行与选型只在 engineering-roadmap 维护；roadmap 用短摘要链接它们。
  专题文档解释设计与兼容性，避免复写全局优先级。
- 学习指南保留解释与练习，具体契约链接 API/架构；重复说明只有帮助理解时才保留。
- 历史实验保留日期、环境和结果限制，不用旧环境覆盖当前配置。完成状态变化更新相关索引，不能只追加新章节。
- 只改文档时检查差异、链接/锚点和引用路径；代码行为变化按 AGENTS.md 执行相关门禁。
