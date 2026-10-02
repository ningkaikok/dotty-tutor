# 2026-09-29 合成评测集审核记录

用户在当前 Codex 对话中确认已审核 50 条候选，并要求入库。M365 Copilot 提供的辅助复核意见为 29 条保留、13 条修改后保留、8 条因未收到 SVG 而暂不能复核。仓库实际包含这 8 张 SVG；`reviewed_synthetic.jsonl` 记录每张图的 SHA-256，便于重新核对素材版本。

原始 `candidates.jsonl` 保持不变，其 SHA-256 为 `9e02e9717ee5d219424d96e30fe1ff236a79b2d0b052fa2c65ce751c572425de`；用户提供的 M365 Copilot 复核文本 SHA-256 为 `f49b313b679510444c6cea5e8c669bc1f4153dcc205e00a1527e9c46b28991d9`，附件不复制进仓库。`reviewed_synthetic.jsonl` 是经用户确认、结合辅助意见修订的 50 条**合成**案例：保留 `sourceKind=synthetic`、`counted=false`，审核状态为 `owner_confirmed_synthetic`。这条确认记录不提供独立真人复核者身份，也不证明图示与参考答案已完成视觉核对；M365 Copilot 是 AI 工具，不能填作独立人工 reviewer。正式人工金标准校验仍应返回计数 0。

修订范围：`draft-error-004` 明确完全平方展开与允许的平方差误用诊断；`draft-socratic-005` 调整年级，`draft-socratic-008` 改成单步追问；`draft-escalation-001` 改成真正的选择式追问，`draft-escalation-005` 写明复合单位；`draft-cost-001` 至 `008` 改成系统评测分层并增加任务子类，其中 `003` 明确缺失案例，`006` 限定置信区间解释，`008` 保留重试失败记录。辅助意见对 `draft-error-004` 的原文引用与当前 JSONL 不完全一致，因此按当前文件实际内容修订。

当前模型评测只使用 42 条文本案例。8 条图像案例虽然素材存在，但现有调用只把 `imageAsset` 路径放进文本提示，没有传图片，故标记 `evaluationEligible=false`。42 条中的 8 条成本/路由任务也不是学生答题质量样本；报告应按维度展示，不能合并成学生陪练效果结论。精确参考字段匹配只衡量字面一致，不是语义评分或模型优劣排名。

下一步若要形成 50 条**人工金标准**，仍需人工撰写或实质修订的输入与参考、真实标注者和另一名真人独立复核者的逐条记录、许可及脱敏依据、审批时间，并通过 `evaluation.benchmark.validator`。图像案例还需完成素材与参考答案的视觉复核，并在模型调用中实际传入图片。
