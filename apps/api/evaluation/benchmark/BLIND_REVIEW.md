# 合成案例的匿名配对人工审核

本流程使用本地已完成的 42 对 Ollama 输出。它只准备审核材料和汇总**真人实际填写**的结论，不会自动代填，也不把合成案例变成正式人工金标准。34 条陪练文本任务与 8 条系统路由/成本任务分别统计。

从 `apps/api` 运行：

```powershell
$env:UV_CACHE_DIR = Join-Path (Resolve-Path ..\..) '.uv-cache'
uv run python -m evaluation.benchmark.blind_review prepare `
  --report ..\..\output\eval-reports\reviewed-ollama-3b-vs-7b.json `
  --dataset evaluation\benchmark\review_queue\reviewed_synthetic.jsonl `
  --out-dir ..\..\output\blind-review `
  --key-out ..\..\output\blind-review-key.json
```

打开 `output/blind-review/blind-review.html`。每题查看输入、参考期望、rubric 和两份匿名回答；分别选“通过/不通过”，再选更好的回答或“同等/均不合格”，填写具体理由。可以随时导出审核 JSON，之后再导入继续填写。**只把 `output/blind-review/` 交给审核者**；`blind-review-key.json` 保存在该目录外，审核完成前不要查看或分享。审核页面不显示模型名称、延迟或 Token 用量。

完成后，将导出的 JSON 放在本机，例如 `output/blind-ratings.json`，执行：

```powershell
uv run python -m evaluation.benchmark.blind_review summarize `
  --packet ..\..\output\blind-review\blind-packet.json `
  --key ..\..\output\blind-review-key.json `
  --ratings ..\..\output\blind-ratings.json `
  --out ..\..\output\blind-review-summary.json
```

汇总会校验审核包 ID、全部 caseId、审核者标识、时间、两份通过判定、偏好与理由；缺一项便拒绝生成结论。统计包括配对通过率差、95% bootstrap 区间与精确符号检验。审核者标识由填写人声明，程序不能验证真人身份或审核独立性。结果只描述这批合成案例的本地输出，不等同于正式金标准或生产模型排名。
