# API 接口

开发环境默认地址为 <http://127.0.0.1:8010>，前端通过同源 `/api` 路径调用。

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
| `POST` | `/api/help` | 判定学生答案或返回下一层提示 |
| `POST` | `/api/tts` | 调用 Azure Speech 或代理 Qwen3-TTS |

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
