# 人工金标准候选审校包

`candidates.jsonl` 含 50 条按六个评测维度整理的**合成候选案例**；逐条可读、可勾选和填写意见的审核清单见 [`HUMAN_REVIEW.md`](HUMAN_REVIEW.md)。，配套 SVG 也由脚本外手工绘制为合成图示。所有记录都标记为 `sourceKind=synthetic`、`counted=false`、`reviewStatus=pending_human_review`，并且没有 `annotatorId`、`reviewerId` 或 `approvedAt`。它们是供领域人员审题、修订 rubric、验证工作流的草案，**不能计入 50 条人工金标准，也不能用于声称模型横评具有统计代表性**。

从 `apps/api` 运行草案结构与覆盖检查：

```bash
python -m evaluation.benchmark.drafts evaluation/benchmark/review_queue/candidates.jsonl
```

正式集仍须由人工提供/编写案例、独立标注并复核、记录真实许可与脱敏依据和审批时间，再按
[`benchmark/contract.py`](../contract.py) 的 JSONL 契约写入正式语料。不能把这些草案改成 `sourceKind=human`
来绕过审校门槛。图片案例中的 `imageAsset` 路径相对于本目录。
