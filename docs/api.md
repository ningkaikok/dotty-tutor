# API 接口

开发环境默认地址为 <http://127.0.0.1:8010>，前端通过同源 `/api` 路径调用。

`/`、`/learn`、`/studio`、`/mistakes` 是前端页面路径，不是 API。错题拍照确认使用独立的
`/api/mistakes` 命名空间。

FastAPI 交互文档启动后可从以下地址查看：

- Swagger UI：<http://127.0.0.1:8010/docs>
- OpenAPI JSON：<http://127.0.0.1:8010/openapi.json>

前端 API 类型由应用自身的 OpenAPI 文档生成，不要手工修改
`apps/web/src/types/generated/api.ts`。接口响应模型变更后，在 `frontend` 目录执行：

```bash
npm run generate:api  # 重新生成
npm run check:api     # 只校验，过期时返回非零状态
```

生成器使用与 API 应用相同的 `app.openapi()`；生成类型只作为 API 层的契约，页面领域类型仍可通过适配器保留。

## 健康与运行时

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 检查 API 和数据库连接 |
| `GET` | `/api/models` | 返回可用 Ollama、Codex 和 Mock 模型；每个模型附带 `modelDetails`（角色、能力标签、上下文上限、延迟/成本级别、回退建议、健康状态） |
| `POST` | `/api/models/select` | 切换当前进程使用的生成模型 |
| `GET` | `/api/review-models` | 返回当前统一审核模型和可用模型目录 |
| `POST` | `/api/review-models/select` | 切换后续题目使用的统一审核模型（文字与图片共用） |
| `GET` | `/api/tutor-models` | 返回错题陪练独立使用的模型目录 |
| `POST` | `/api/tutor-models/select` | 切换后续陪练轮次使用的模型，不影响生成/审核/OCR 选择 |
| `GET` | `/api/ocr` | 返回 OCR provider 和自动探测结果 |
| `POST` | `/api/ocr/select` | 切换 `auto`、`mineru` 或 `pypdf` |
| `GET` | `/api/tts/status` | 返回当前 TTS provider 和可用状态 |
| `GET` | `/api/metrics/model-calls?days=7` | 模型调用边界指标聚合（只读）：按 runtime/task/provider/model 分组的调用数、失败数、平均耗时与输出 token 合计 |
| `GET` | `/api/reports/learning-cost?learnerId=local-demo&days=7` | 学习效果与模型成本代理指标联合报告；学习为学生累计快照，模型为全局最近窗口，成本不表示货币金额或因果关系 |

生成模型、统一审核模型和 OCR 选择目前是 FastAPI 进程级状态，不按用户或教材隔离。
`modelDetails.health` 是进程内连续失败计数（阈值 3 次），只用于候选筛选提示；它不会改写任何
已开始运行的审计快照，成功调用会立即复位。

## 题目与教材导入

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/question` | 返回内置示例题和讲解 |
| `POST` | `/api/textbook/import` | 上传单页图片或小 PDF，返回一道结构化题目 |
| `POST` | `/api/uploads/init` | 初始化最大 500 MB 的 PDF 分块任务 |
| `PUT` | `/api/uploads/{uploadId}/chunks/{index}` | 幂等上传一个 5 MB 分块 |
| `GET` | `/api/uploads/{uploadId}/status` | 查询上传、OCR 和生成进度 |
| `POST` | `/api/uploads/{uploadId}/complete` | 创建 PDF 合并、OCR 与整本生成任务，返回 `202 + jobId`；支持 `Idempotency-Key` |
| `POST` | `/api/uploads/{uploadId}/batches/{batchId}/process` | 创建后续批次处理或重生成任务，返回 `202 + jobId` |
| `POST` | `/api/uploads/{uploadId}/full-paper` | 快速预览后排队整卷生成任务（默认上限 100 题）；Worker 会先生成 `summary.qualityReport`，阻断项存在时暂停模型调用；返回 `202 + jobId`；支持 `Idempotency-Key` |
| `GET` | `/api/uploads/{uploadId}/full-paper/summary` | 读取整卷任务按批次持久化的成功/失败/隔离/跳过汇总、`summary.qualityReport` 和题目载荷 |
| `GET` | `/api/jobs/{jobId}` | 查询后台任务状态、进度、尝试次数、结果或结构化失败详情 |
| `POST` | `/api/jobs/{jobId}/cancel` | 取消排队任务，或请求运行中的 Worker 在安全点停止 |
| `POST` | `/api/jobs/{jobId}/retry` | 对已失败任务增加一次明确预算并重新排队；保留历史尝试次数和最后错误 |
| `POST` | `/api/uploads/{uploadId}/questions/{sourceQuestionKey}/regenerate` | 修复单题；传 `refreshOcr=true` 时先重新 OCR |
| `GET` | `/api/runs/{runId}` | 查询冻结的运行配置、状态和结果/失败证据 |
| `GET` | `/api/uploads/{uploadId}/questions/{sourceQuestionKey}/revisions` | 按来源题键读取不可变题目修订链 |

PDF 会在浏览器上传前和后端合并后检查 `%PDF-` 文件头与 `%%EOF` 结束标记。文件缺少
`%%EOF` 通常表示源 PDF 本身被截断，需要重新下载或重新导出。

后台任务状态为 `queued`、`running`、`succeeded`、`failed` 或 `cancelled`。任务创建请求可以携带稳定
`Idempotency-Key`；相同任务和幂等键不会重复入队。前端应以 `jobId` 轮询，不要持续占用创建任务的 HTTP
连接。失败详情中的 `code` 与 `retryable` 用于决定展示“重试”还是“重新上传”，不要解析 Python 异常字符串。

整本导入的 `qualityReport` 是确定性 OCR 检查结果：`ready` 表示可继续生成，`warning` 允许继续但需人工留意，
`blocked` 表示检测到题号/图片归属等阻断项，Worker 不会继续调用模型。报告同时返回预计题数、题号范围、重复
题号、缺失页、图片归属冲突以及坐标归属审计 `imageAttributionAudit`，便于定位后重新上传或修复 OCR。

## 学习与语音

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/lessons` | 校验并保存带版本的可编程课程文档 |
| `GET` | `/api/lessons/{lessonId}` | 读取课程内容块和发布状态 |
| `POST` | `/api/publications` | 创建由多个课程组成的互动试卷草稿 |
| `GET` | `/api/publications?status=published` | 列出学生可见的已发布互动试卷 |
| `GET` | `/api/publications/{publicationId}` | 读取一份已发布试卷及其题目 |
| `PATCH` | `/api/publications/{publicationId}/status` | 将试卷送审、发布或归档 |
| `POST` | `/api/publications/{publicationId}/revisions` | 从原 PDF 整套重新生成并创建审核新版 |
| `GET` | `/api/publications/source/{sourceUploadId}` | 内容生产页刷新后恢复该教材最新试卷版本和工作区题目 |
| `POST` | `/api/learning/sessions` | 使用 `learnerId`、`publicationId` 创建互动试卷学习会话 |
| `GET` | `/api/learning/sessions/{sessionId}` | 恢复学习会话和已同步作答 |
| `POST` | `/api/learning/sessions/{sessionId}/attempts` | 校验题目属于当前发布试卷，服务端解析知识点并保存作答、更新 mastery-v2 |
| `POST` | `/api/learning/sessions/{sessionId}/sync` | 批量补传离线期间排队的作答记录；按 `attemptId` 幂等 |
| `GET` | `/api/learning/mastery/{learnerId}` | 查询包含 `knowledgePointId`、`score`、`rawScore`、`evidenceCount`、`evidenceConfidence`、`algorithmVersion` 和 `computedAt` 的掌握度 |
| `POST` | `/api/help` | 判定学生答案或返回下一层提示 |
| `POST` | `/api/tts` | 调用 Azure Speech 或代理 Qwen3-TTS |

发布状态只有 `draft`、`in_review`、`published` 和 `archived`。允许的主路径是
`draft → in_review → published → archived`，归档内容可恢复为草稿；接口拒绝跳过审核直接发布。
发布时若只有部分题目未通过自动修复，响应包含 `qualityRecovery`，异常题被隔离，其余题目正常发布；
若全部题目均不合格，接口返回 `409`，`detail.code` 为 `publication_quality_blocked`，并包含脱敏后的
题目 ID 与校验错误，便于日志聚合和开发排查。
发布时要求试卷内每道题的质量状态明确为 `ready`，学生接口只返回 `published` 试卷。课程块 Schema、
渲染器扩展方式和掌握度计算见
[可编程课程与学习闭环](programmable-learning.md)。

作答同步请求中的每个 `attemptId` 应由客户端稳定生成。请求只提交 `questionId`、答案和判定，不提交可信的
知识点字符串；服务端使用当前会话绑定的 `publicationId` 查找发布题目并解析知识点。题目不属于试卷时返回
`404`，客户端伪造标签不会改变知识点实体。服务端按该 ID 幂等写入，网络重试不会重复
增加掌握度证据；同一 ID 不能跨学习会话复用。`createdAt` 使用 Unix 秒时间戳并记录真实作答时间，
离线补传不会把旧作答记成刚刚完成；浏览器暂时离线时，学生端会将记录放入本地待同步队列。
会话查询响应中的 `attempts` 是按作答时间排序的答案快照，包含 `questionId`、`knowledgePointId`、`response`、判定和提示层级；
学生端用它恢复已提交题目的选择、填空、数值或画线状态。模型讲解文本不作为恢复数据，避免旧反馈串题。
学习会话请求使用 `publicationId` 指向已发布试卷；题目 `lessonId` 不作为会话输入。

mastery-v2 对每个 `(publicationId, questionId)` 只取最新作答：正确为 `1`、部分正确为 `0.55`、错误为 `0`；
不同题证据数的置信度上限依次为 1/2/3/4/5 道题的 `0.6/0.7/0.8/0.9/1.0`。`rawScore` 是最新题证据平均分，
`score = rawScore × evidenceConfidence`。旧数据库使用
`python scripts/migrate_mastery_v2.py --dry-run|--apply|--verify` 迁移；迁移会保留旧掌握度表、补齐实体和作答归属，
再从可用作答日志重建 mastery-v2 投影；没有作答证据的旧投影会明确标记为 legacy，重复执行为 no-op。

试卷新版不会覆盖原课程文档。接口为每道题创建新的 `lessonId`，将试卷 `version` 加一并记录
`revisionOf`；新版本从 `in_review` 开始，仍须通过质量门禁后发布。若原 PDF、来源批次或 OCR 所需文件
已经丢失，接口返回 `409`，需要重新上传原 PDF，而不是使用已污染的题干继续猜测。

内容生产操作会返回审计摘要：`question_repair` 复用 OCR，`question_reocr` 显式刷新 OCR，
`batch_regenerate` 重新生成批次，`publication_rereview` 创建整套审核新版。摘要包含 `runId`、题目
`revisionNumber`、实际模型/审核/OCR provider 及 Prompt/Schema/validator 版本或摘要；不会返回完整 Prompt、密钥或学生数据。
运行配置创建后冻结，只允许从 `running` 终结为 `succeeded` 或 `failed`。

Help 示例：

```bash
curl -X POST http://127.0.0.1:8010/api/help \
  -H 'Content-Type: application/json' \
  -d '{
    "questionId": "geometry-perpendicular-bisector",
    "studentInput": "我不知道怎么开始",
    "hintLevel": 0,
    "language": "zh",
    "mode": "help"
  }'
```

`/api/help` 会返回回复文本、判定结果、引导上下文、下一提示层级、画布动作和实际模型运行信息。

结构化题型的作答字段：

```json
{
  "questionType": "fill-blank",
  "blanks": [
    {"id": "blank-1", "answerType": "numeric", "correctAnswers": ["4"], "tolerance": 0, "unit": ""}
  ]
}
```

- `multi-select`：前端提交 `interactionResult.selectedOptions`，后端比较完整的 `correctAnswers` 集合。
- `fill-blank`：前端提交 `interactionResult.blankAnswers`，逐空比较文本或数值。
- `numeric`：前端提交 `interactionResult.numericAnswer`，按 `answerSpec.tolerance` 判定数值误差。
- `subQuestions`：多小问各自声明 `id`、`questionType` 和 `evaluation.mode`；前端提交
  `interactionResult.subQuestionAnswers`，键为小问 `id`，值沿用选择、填空、数值或文本字段。
  `deterministic` 小问复用对应题型判题，`tutor` 小问只进入陪练反馈，不自动判分。
- 没有明确答案规格的题目继续交给模型和分层引导卡处理。

多小问响应的 `guideContext.evaluationSummary` 会列出每个小问的 `status`、可判分数量、完成情况和
`masteryEligible`。该字段只用于解释本轮结果，不能作为学习证据授权；掌握度投影会根据已发布题目契约在服务端
重新判断是否含 tutor-only 小问。确定性小问的正确和错误都可形成学习证据；含 tutor-only 小问的作答保留审计，
但整题不进入 mastery。

示例：

```json
{
  "questionType": "short-answer",
  "subQuestions": [
    {
      "id": "part-1",
      "label": "（1）",
      "prompt": "说明理由。",
      "questionType": "short-answer",
      "evaluation": {"mode": "tutor", "reason": "开放性证明"},
      "correctAnswer": null,
      "correctAnswers": null,
      "options": null,
      "blanks": null,
      "answerSpec": null,
      "interaction": null,
      "contentBlocks": []
    }
  ]
}
```

## 资源与教材库

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/uploads/{uploadId}/assets/{batchId}/{filename}` | 读取持久化题图 |
| `GET` | `/api/uploads/{uploadId}/artifacts/{batchId}/{filename}` | 读取 OCR Markdown 或模型提示词 |
| `GET` | `/api/library` | 列出已持久化教材 |
| `GET` | `/api/library/{uploadId}` | 恢复教材和已生成题目 |
| `DELETE` | `/api/library/{uploadId}` | 软删除教材（保留源文件和数据库记录，可恢复） |

## 错误与安全边界

应用错误统一返回 Problem JSON 风格字段：`errorCode`、`message`、`requestId`、`retryable` 和可选
`details`；FastAPI 校验错误可能同时携带框架生成的 `detail`。未知异常不会向浏览器暴露堆栈、文件路径、
密钥或完整模型响应；详细证据只进入脱敏日志和任务的内部错误记录。

- 不支持的文件类型返回 `415`。
- 文件、分块或 PDF 结构校验失败返回 `4xx`。
- 数据库、TTS 或模型服务不可用通常返回 `503`。
- 管理员调试入口：`GET /api/debug/errors?request_id=` 返回最近失败请求的脱敏摘要
  （错误类型 + 截断消息，无完整堆栈）。三重门控：环境变量 `DOTTY_DEBUG_TOKEN` 未配置时
  端点返回 404；token 不匹配返回 403；匹配才返回数据。生产默认关闭（不配置即隐藏）。
- 当前 API 尚未实现登录、权限、租户隔离和限流，只应运行在受控环境。
- OCR Markdown 和模型提示词属于调试资源，生产环境应限制为管理员访问。

## 错题录入与确认

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/mistakes/import` | 上传最大 10 MB 的单张图片，OCR 并创建待确认错题 |
| `GET` | `/api/mistakes?learnerId=local-demo` | 列出个人错题本，默认不含已归档记录 |
| `GET` | `/api/mistakes/{mistakeId}` | 读取题目快照、原答案、归类和运行信息 |
| `PATCH` | `/api/mistakes/{mistakeId}` | 确认题干、学段、学科、章节、知识点和错误原因 |
| `PATCH` | `/api/mistakes/{mistakeId}/archive` | 归档或恢复错题 |
| `GET` | `/api/mistakes/{mistakeId}/source` | 读取持久化错题原图 |
| `GET` | `/api/mistakes/{mistakeId}/assets/{filename}` | 读取 OCR 提取题图 |

互动试卷自动记录的错题先进入 `pending_confirmation`，不会根据一次错答自动填写 `errorReason`；学生确认错误原因后才进入
`unmastered` 并允许开始陪练。纸质错题沿用相同确认契约。

导入使用 `multipart/form-data`：`file` 必填；`sourceText`、`originalAnswer` 和 `learnerId` 可选。
浏览器会先完成裁切，再上传裁切后的文件。确认请求中的 `errorReason` 必须是：

```text
concept | reading | calculation | missing_step | unknown | careless
```

`pending_confirmation` 表示 AI 结果尚未由学生确认；确认后进入 `unmastered`。当前匿名演示仍使用
`local-demo`，不能据此实现多用户隔离。

## 有状态单题陪练

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/mistakes/{mistakeId}/thread` | 为已确认错题创建或恢复唯一线程 |
| `GET` | `/api/tutor/threads/{threadId}` | 获取当前阶段、摘要和最近最多 40 条消息 |
| `POST` | `/api/tutor/threads/{threadId}/messages` | 提交文字或结构化答案并完成一轮辅导 |

选择题可以同时携带用户可读文字和结构化答案：

```json
{
  "content": "我选择 B",
  "mode": "answer",
  "hintLevel": 0,
  "interactionResult": { "selectedOptions": ["B"] }
}
```

请求提示时允许没有答案：

```json
{
  "content": "",
  "mode": "help",
  "hintLevel": 1,
  "interactionResult": {}
}
```

响应中的 `stage` 是 `diagnose`、`explain`、`practice` 或 `verify`；`assessment` 是确定性判题产生的
`correct`、`partial` 或 `incorrect`；`action` 描述本轮是否推进阶段。模型只负责解释、提示和追问，
不能自行修改正确性或把错题标记为已掌握。

待确认或已归档错题返回 `409`；线程不存在返回 `404`；`answer` 模式没有任何有效文字或结构化内容时
返回 `422`。当前匿名 Demo 使用 `local-demo`，它不是可靠鉴权。

## 变式掌握验证

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/mistakes/{mistakeId}/variations` | 按生成顺序读取该错题的验证题和作答结果 |
| `GET` | `/api/mistakes/{mistakeId}/evidence` | 读取单道错题的错误原因、变式策略、每次验证作答证据和 1/3/7 天复习任务 |
| `POST` | `/api/mistakes/{mistakeId}/variations` | 陪练进入 `practice`/`verify` 后生成或复用该错题唯一的验证题 |
| `POST` | `/api/variations/{variationId}/answer` | 提交结构化答案并完成确定性判题；答错可对同一道题重新提交 |

答案请求沿用 `{ "attemptId": "...", "content": "...", "interactionResult": {...} }` 契约，`attemptId` 用于网络重试幂等。
每次新提交都会追加一条不可变验证证据；答错后改对不会覆盖此前答案。答对后的验证题不能再次提交，新的请求返回 `409`；
同一 `attemptId` 重试会返回原次结果。答错或部分正确的题目可以修改后再次提交。生成结果不是选择、多选、填空或数值题，或者直接复制原题时返回 `422`。模型决定新题内容，
正确性仍由答案结构和确定性判题器决定。

每道变式题会固化 `variationStrategyVersion`、目标、教学目标和难度。作答响应额外包含 `mastery.correctStreak`、`requiredCorrect`、`answeredCount` 和 `mastered`，以及本次 `evaluationEvidence`。
唯一验证题
判定为 `correct` 后，服务把错题状态从 `unmastered` 更新为 `mastered`；在此之前的错误结果保留在同一题上
并允许修正。Evidence API 返回 `unmastered → mastered` 状态变化和每次验证证据，不接受客户端传入掌握结论。

## 间隔复习与学习进度

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/reviews?learnerId=local-demo` | 读取按到期时间排序的 1/3/7 天复习任务和服务器时间 |
| `POST` | `/api/reviews/{taskId}/start` | 生成或恢复该任务的同知识点迁移题，允许提前复习 |
| `POST` | `/api/reviews/{taskId}/answer` | 提交一次结构化复习答案并保存确定性判题结果 |
| `GET` | `/api/progress?learnerId=local-demo` | 返回掌握率、待复习数、完成数、复习正确率、变式验证正确率、复习完成率、同知识点再错率和知识点聚合 |
| `GET` | `/api/funnel?learnerId=local-demo` | 学习效果漏斗快照：导入→确认→陪练→验证→复习各阶段计数与比率（分母为零时比率为 null） |

错题首次变为 `mastered` 时，以第二次正确作答时间为基准，幂等创建三个任务。相同错题和间隔有唯一
约束，网络重试不会重复排期。任务状态依次为 `scheduled → ready → completed`；已完成任务不能重复
提交。当前 MVP 允许提前开始未来任务，方便个人演示和主动复习。

本地运行后可访问 <http://127.0.0.1:8010/docs> 查看 FastAPI 自动生成的完整 OpenAPI 页面。

具体数据模型和交付顺序见[AI 错题陪练产品规划](mistake-coach-plan.md)。
