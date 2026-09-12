# 原题级试卷处理链路改造路线图

目标是让系统稳定完成：`OCR/DocumentArtifact → ExamIR → QuestionIR → SolutionIR → VerificationIR → TutorScript → 旧 lesson payload`。
这条链路服务于“忠实复刻原题”，不采用根据试卷内容重新出题的策略。

## 当前状态（2026-09-12）

| 阶段 | 状态 | 当前实现 | 下一步验收 |
| --- | --- | --- | --- |
| DocumentArtifact/OCRArtifact | 已完成核心 | 复用逐页 OCR、Provider、缓存、`source.md` 和 OCR 审计，并写入 provider/version/content hash 与页范围 | 后续按真实 MinerU 版本补充更多块类型 |
| ExamIR | 已完成核心 | `domain/questions/exam_ir.py` 构建章节、题目、页码、图片、答案引用和诊断，并原子持久化 `exam-ir.json` | 后续补充更多跨页/表格 golden 样本 |
| QuestionIR | 已完成核心 | `domain/questions/ir.py` 保存题号、原文、选项、图片 ID、真实 block ref、置信度和答案引用 | 继续扩展学科 profile 的小问核验 |
| 原题 extraction | 已完成核心 | `lesson_generation.py` 使用独立 `QUESTION_EXTRACTION_SCHEMA`，输入为 QuestionIR | 按真实业务 badcase 扩充局部修复策略 |
| SolutionIR | 已接入初版 | 独立 `SOLUTION_SCHEMA` 调用，只接收 QuestionIR | 增加依据、置信度和来源答案分离 |
| VerificationIR | 已接入初版 | 独立 `VERIFICATION_SCHEMA` 调用；非 `verified` 自动进入 `needs_review` | 增加更强的 solver/verifier agreement 与数学等价核验 |
| TutorScript | 已完成核心 | 独立 `TUTOR_SCRIPT_SCHEMA`，继续投影为 4 步/3 卡；verification 非 verified 时跳过模型调用 | 后续补充更细的学科脚本约束 |
| 旧 lesson payload adapter | 已完成 | 保留 `questionPayload`、`lessonSteps`、`guideCards` 和前端字段 | 补齐新 provenance/stage 字段的 API 回归测试 |
| 阶段缓存 | 已完成核心 | `assets/{batch}/stage-cache` 内容寻址、原子写入、损坏安全 miss 和大小/条目上限；按目标阶段复用上游 | 后续可替换为共享缓存后端 |
| 质量门禁 | 已完成核心 | 来源、边界、图片、答案核验和发布状态均有机器可读诊断；冲突阻止 ready | 补充更多数学等价性/单位规则 |
| 测试与评测 | 已完成核心 | ExamIR/staged-pipeline golden evaluator、缓存/门禁回归测试和既有 OCR replay 均纳入 unittest | 持续增加脱敏版式 fixtures |
| 审核工作台及局部重跑 | 已完成核心 | review queue API 与前端面板支持四个阶段指定重跑 | 后续增强逐字段冲突对比 |

## P0 实施顺序

1. 固定 IR 字段、版本和序列化契约，兼容旧题目投影。
2. 将现有正则明确限制为候选定位；ExamIR 负责聚合候选和来源证据。
3. 将生成拆为 extraction、solving、verification、tutoring；通过阶段契约、持久缓存和目标阶段重跑避免未变化阶段重复计算。
4. 为每阶段加入 source hash + provider/version + prompt/schema version 的缓存边界。
5. 增加来源文本、块、图片、答案和脚本的分层质量门禁。
6. 用脱敏 OCR excerpt 和合成版式样本做 golden regression，再运行全量 CI 门禁。

## 兼容策略

- HTTP、数据库和前端继续使用 `questionPayload`；IR 只作为内部生产快照和题目来源证据。
- `split_question_sources()` 保留为降级路径，但主编排先构建 ExamIR。
- 旧的 `generate_lesson()` 保留入口，内部使用阶段编排；模型不可用时只返回明确带 fallback/隔离证据的兼容候选。
- 不删除现有题型、发布门禁和批次断点续跑；新增字段均为向后兼容字段。

## 本轮验收命令

```bash
cd apps/api
UV_CACHE_DIR=/tmp/tutor-demo-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/tutor-demo-uv-cache uv run pyright
UV_CACHE_DIR=/tmp/tutor-demo-uv-cache uv run python -m unittest discover -s tests -p 'test_*.py'
```
