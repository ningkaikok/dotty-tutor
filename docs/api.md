# API 接口

开发环境默认地址为 <http://127.0.0.1:8010>，前端通过同源 `/api` 路径调用。

`/`、`/textbooks` 和 `/mistakes` 是前端页面路径，不是 API。错题拍照确认使用独立的
`/api/mistakes` 命名空间。

FastAPI 交互文档启动后可从以下地址查看：

- Swagger UI：<http://127.0.0.1:8010/docs>
- OpenAPI JSON：<http://127.0.0.1:8010/openapi.json>

## 健康与运行时

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 检查 API 和数据库连接 |
| `GET` | `/api/models` | 返回可用 Ollama、Codex 和 Mock 模型 |
| `POST` | `/api/models/select` | 切换当前进程使用的生成模型 |
| `GET` | `/api/ocr` | 返回 OCR provider 和自动探测结果 |
| `POST` | `/api/ocr/select` | 切换 `auto`、`mineru` 或 `pypdf` |
| `GET` | `/api/tts/status` | 返回当前 TTS provider 和可用状态 |

模型和 OCR 选择目前是 FastAPI 进程级状态，不按用户或教材隔离。

## 题目与教材导入

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/question` | 返回内置示例题和讲解 |
| `POST` | `/api/textbook/import` | 上传单页图片或小 PDF，返回一道结构化题目 |
| `POST` | `/api/uploads/init` | 初始化最大 500 MB 的 PDF 分块任务 |
| `PUT` | `/api/uploads/{uploadId}/chunks/{index}` | 幂等上传一个 5 MB 分块 |
| `GET` | `/api/uploads/{uploadId}/status` | 查询上传、OCR 和生成进度 |
| `POST` | `/api/uploads/{uploadId}/complete` | 合并 PDF、规划批次并处理首批 |
| `POST` | `/api/uploads/{uploadId}/batches/{batchId}/process` | 按需处理后续批次或重新生成 |

PDF 会在浏览器上传前和后端合并后检查 `%PDF-` 文件头与 `%%EOF` 结束标记。文件缺少
`%%EOF` 通常表示源 PDF 本身被截断，需要重新下载或重新导出。

## 学习与语音

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/api/lessons` | 校验并保存带版本的可编程课程文档 |
| `GET` | `/api/lessons/{lessonId}` | 读取课程内容块和发布状态 |
| `POST` | `/api/learning/sessions` | 创建学习者与课程关联的学习会话 |
| `POST` | `/api/learning/sessions/{sessionId}/attempts` | 保存作答并更新知识点掌握度 |
| `GET` | `/api/learning/mastery/{learnerId}` | 查询学习者的知识点掌握度 |
| `POST` | `/api/help` | 判定学生答案或返回下一层提示 |
| `POST` | `/api/tts` | 调用 Azure Speech 或代理 Qwen3-TTS |

课程块 Schema、渲染器扩展方式和掌握度计算见[可编程课程与学习闭环](programmable-learning.md)。

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
- 没有明确答案规格的题目继续交给模型和分层引导卡处理。

## 资源与教材库

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/uploads/{uploadId}/assets/{batchId}/{filename}` | 读取持久化题图 |
| `GET` | `/api/uploads/{uploadId}/artifacts/{batchId}/{filename}` | 读取 OCR Markdown 或模型提示词 |
| `GET` | `/api/library` | 列出已持久化教材 |
| `GET` | `/api/library/{uploadId}` | 恢复教材和已生成题目 |

## 错误与安全边界

- 不支持的文件类型返回 `415`。
- 文件、分块或 PDF 结构校验失败返回 `4xx`。
- 数据库、TTS 或模型服务不可用通常返回 `503`。
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
| `POST` | `/api/mistakes/{mistakeId}/variations` | 陪练进入 `verify` 后，按错误原因生成下一道验证题 |
| `POST` | `/api/variations/{variationId}/answer` | 提交一次结构化答案并完成确定性判题 |

答案请求沿用 `{ "content": "...", "interactionResult": {...} }` 契约。每道验证题只能提交一次，重复提交
返回 `409`；生成结果不是选择、多选、填空或数值题，或者直接复制原题时返回 `422`。模型决定新题内容，
正确性仍由答案结构和确定性判题器决定。

作答响应额外包含 `mastery.correctStreak`、`requiredCorrect`、`answeredCount` 和 `mastered`。连续两次
判定为 `correct` 后，服务把错题状态从 `unmastered` 更新为 `mastered`；任意非正确结果都会自然中断
连续记录。次数由已保存的验证题推导，不接受客户端传入。

## 间隔复习与学习进度

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/reviews?learnerId=local-demo` | 读取按到期时间排序的 1/3/7 天复习任务和服务器时间 |
| `POST` | `/api/reviews/{taskId}/start` | 生成或恢复该任务的同知识点迁移题，允许提前复习 |
| `POST` | `/api/reviews/{taskId}/answer` | 提交一次结构化复习答案并保存确定性判题结果 |
| `GET` | `/api/progress?learnerId=local-demo` | 返回掌握率、待复习数、完成数、复习正确率和知识点聚合 |

错题首次变为 `mastered` 时，以第二次正确作答时间为基准，幂等创建三个任务。相同错题和间隔有唯一
约束，网络重试不会重复排期。任务状态依次为 `scheduled → ready → completed`；已完成任务不能重复
提交。当前 MVP 允许提前开始未来任务，方便个人演示和主动复习。

本地运行后可访问 <http://127.0.0.1:8010/docs> 查看 FastAPI 自动生成的完整 OpenAPI 页面。

具体数据模型和交付顺序见[AI 错题陪练产品规划](mistake-coach-plan.md)。
