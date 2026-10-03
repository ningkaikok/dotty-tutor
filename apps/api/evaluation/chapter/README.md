# 合成章节场景与基线

该目录为教材章节批次 1 提供可运行的离线场景集。当前数学主题是初中一次函数，英语条目是与数学分离的少量证据阅读夹具。所有正文、OCR 文本和图形均为原创合成内容，没有保存或摘录教材 PDF，也没有执行 OCR、模型、网络或数据库操作。

从 `apps/api` 目录运行：

```bash
uv run python -m evaluation.chapter.baseline --check
```

JSON 验收报告可写到临时目录：

```bash
uv run python -m evaluation.chapter.baseline --check --json --output /tmp/chapter-baseline-report.json
```

`--check` 验证清单元数据和覆盖，并重放现有数学 `split_question_sources`。普通通过项标为 `pass`；数学生产切分器的已知失败必须继续匹配清单中的完整基线签名，标为 `known_failure` 并保留失败原因。如果失败不再出现、签名发生变化或出现未预期失败，命令返回非零，要求维护者审查并明确更新基线。预期失败可见不代表产品修复。

英语场景覆盖词义、指代、明示信息、推断、答案正确但依据错误和合理改写。它们是合成夹具自检：accepted answers 是场景内声明的变体，依据按该场景的原文证据核对。`expected_rejection` 表示夹具应拒绝该条目（例如答案对但依据错），不是英语生产判题器的缺陷或能力结果；本目录没有英语生产语义判题器。报告把 `mathProductionKnownFailures` 与 `englishFixtureChecks` / `englishExpectedRejections` 分开统计。

## 来源、许可与复核状态

清单中的每个案例包含 `sourceKind=synthetic`、来源许可和版本、预期结果、页码及页内区域、`reviewStatus=pending_human_review` 与 `counted=false`。正文与原创 SVG 使用 `CC0-1.0`；来源版本为 `synthetic-chapter-v1`。图形只表达一次函数的原创示意，不来自教材或网页。所有案例的 `reviewer` 必须为空；门禁会拒绝人工批准状态或署名，不能用合成检查冒充人工复核。

未来若把案例提交到独立人工验收集，复核者应先独立核对来源页与区域、OCR 是否保留题干和限制条件、函数公式及图表是否对应、缺页和跨页题是否正确定位、预期结果与失败判据是否能从来源支持。英语应另核对答案与证据分别是否成立、推断是否有原文依据、合理改写是否保留语义。复核者及结论只能由真实人工评审流程记录。本合成清单永远不计入人工样本量，也不替代路线图要求的独立双审金标准门槛。

## 场景范围

数学有 12 个案例，包含文本 PDF OCR 产物与扫描页 OCR 产物的合成文本、公式、原创图表、重复页题号、缺页、错图、漏条件及截断公式。已知失败保持可见：缺页导致题号缺失、图像归属错误、来源条件缺失，以及截断 OCR 缺少完整公式。每条的 `baselineSignature` 记录实际观察结果，便于区分“预期产品行为”与“重放结果变动”。

英语有 6 个案例，分别覆盖上述六类阅读场景。运行器仅验证这些声明过的答案变体和证据场景，不推断其他答案的语义，也不提供人工 rubric 判定。
