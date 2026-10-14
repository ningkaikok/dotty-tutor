# 人工金标准准备诊断

准备诊断复用现有 `admission` 与 `contract` 门禁，只读检查候选和已有证据。它不填写审批、
不修改来源或候选、不生成入集文件、不调用模型。材料不足也会输出完整诊断，便于继续准备。

在仓库的 `apps/api` 目录执行：

```bash
uv run python -m evaluation.benchmark.readiness evaluation/benchmark/review_queue/candidates.jsonl
```

当前仓库的这份清单是 50 条合成候选，诊断结果应为 `synthetic=50`、有效独立人工案例为 0、
人工案例缺口为 50。合成或公开来源材料只能作为开发与来源复核材料，不能改个身份标签来充当
人工金标准。

已有脱敏真实人工案例、独立身份登记和两人审批后，可以读取这些文件检查准备度：

```bash
uv run python -m evaluation.benchmark.readiness /private/tmp/human-candidates.jsonl \
  --identities /private/tmp/verified-identities.json \
  --reviews /private/tmp/existing-human-reviews.json \
  --out /private/tmp/new-readiness-report.json
```

`--identities`、`--reviews` 都可省略。省略独立身份登记时，案例自身声称身份已核验不能使其计数。
`--reviews` 是 `caseId` 到两条**已经实际完成**的审批记录的映射；不传时检查候选内已有的
`humanReviews` 和入集元数据。两个文件的契约与来源限制见
[双审入集实现](../apps/api/evaluation/benchmark/admission.py) 与
[金标准契约](../apps/api/evaluation/benchmark/contract.py)。诊断不会代写这些文件。

提供外部审批时，工具仅在内存调用 `admit_case` 验证。不合格的外部审批不会回退到候选中较旧的
通过记录。两条审批必须绑定完整当前快照；改变 `source.contentSha256`、输入、答案或 rubric
会使旧审批失效。身份登记中的两个 `actorId` 即使不同，若 `personKey` 属于同一人仍不能双审。
身份登记必须由材料维护者独立核验；本工具不能证明登记中的身份声明是真实的，也不会打开
原始教材重新计算 PDF 指纹。实际来源文件与指纹核对仍由来源准备和人工复核工作流执行。

输出仅包含总量、固定原因码、行号与覆盖统计，不包含案例编号、学生原文、来源路径或个人身份。
`candidateCount` 是可解析 JSON 对象数，坏 JSON 行或非对象行另外计入 `invalidLineCount`；
`sourceKinds` 分别列出 `synthetic`、`public`、`human`、`fixture` 和无法识别的 `unknown`。
`inputSha256` 用于核对诊断针对的候选文件版本。

| 字段 | 含义 |
| --- | --- |
| `validIndependentHumanCases` | 来源、内容契约、同一快照的独立双审及入集元数据均有效的人工案例数；全文件跨来源的重复案例也不计入 |
| `coverage` / `gaps` | 各任务维度有效计数、各分层不同取值数及缺口；不输出具体分层值 |
| `gates` | 至少 50 条、每个任务维度至少 5 条、subject/gradeBand/difficulty 各至少 2 个取值 |
| `goldAdmissionReady` | 现有人工材料满足全部门槛，且没有输入/证据错误 |
| `problems` / `problemCounts` | 固定原因码；逐行读取与外部审批错误带安全行号 |
| `nextActions` | 修复输入/证据、保留非人工开发材料、采集真实案例和双审、补维度/分层缺口或进入既有协议 |
| `formalRankingAvailable` | 始终为 false；准备诊断无法证明模型结果与盲评分完整 |

常见原因包括 `invalid_json`、`record_not_object`、`approval_snapshot_stale`、
`reviewers_not_independent`、`reviewer_identity_unverified`、`two_reviews_required`、
`source_provenance_missing`、`sensitive_input_key` 和 `admission_metadata_mismatch`。
对外报告不会透出底层验证器可能含有身份或案例编号的详细错误文字。

退出码 0 表示金标准材料门槛满足；1 表示诊断成功但尚未满足；2 表示文件读取、辅助 JSON、
编码或报告写入失败。`--out` 只创建新文件，已有文件、输入文件、符号链接均拒绝覆盖；
不指定时只向标准输出打印报告，输出目录需事先存在。

准备度满足后，由维护者使用既有 `evaluation.benchmark.admission` 创建新的入集文件、
`evaluation.benchmark.validator` 校验，再使用 `evaluation.benchmark.formal` 对完整模型臂和
两名独立盲审评分执行固定数据集比较。本诊断不会自动进入这些步骤或宣称生产模型排名。
