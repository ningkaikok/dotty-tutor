# 日志与运行监控

Dotty Tutor 的后端使用结构化 JSON 日志。每行日志都是一个独立 JSON 对象，适合 Docker、
systemd、Loki、ELK 或云日志服务采集。

## 已记录的事件

### 服务稳定性

- `http.request`：请求方法、路径、状态码、耗时和 `request_id`。
- `http.request.failed`：未处理异常类型、请求耗时和堆栈。
- `service.health.ok` / `service.health.failed`：API 与 PostgreSQL 健康状态。
- `database.ping.failed`：数据库连接检查失败。
- `tts.health.failed`、`tts.request.failed`：Qwen3-TTS 不可用或合成失败。

### 教材处理链路

- `textbook.import.started` / `textbook.import.completed`：轻量导入耗时和使用的 OCR/模型。
- `upload.initialized`、`upload.chunk.received`：分块上传进度。
- `upload.status.changed`：合并、校验、OCR、生成和完成等状态迁移。
- `upload.processing.failed`、`upload.batch.failed`：PDF 或批次处理失败。
- `ocr.started` / `ocr.completed` / `ocr.failed`：OCR 提供商、页范围和回退状态。
- `model.request.*`、`model.review.*`：模型提供商、模型、耗时、图像数量和错误类型。
- `question.batch.*`、`question.*`：题目批次、题型、审校提供商和处理结果。
- `help.completed`：提示请求的模式、判定结果、来源和耗时。

## 日志格式

示例：

```json
{"timestamp":"2026-08-02T13:30:00+00:00","level":"INFO","logger":"dotty","event":"http.request","request_id":"...","method":"GET","path":"/api/health","status_code":200,"duration_ms":4.2}
```

请求会优先使用客户端传入的 `X-Request-ID`，否则由 API 生成；响应会原样返回该 ID，便于从
飞书告警跳转后检索完整链路。

通过 `LOG_LEVEL` 控制级别，默认是 `INFO`：

```bash
LOG_LEVEL=INFO  # 生产建议 INFO
LOG_LEVEL=DEBUG # 本地排查上传分块等细节
```

## 隐私边界

日志不会记录教材原文、Prompt、学生答案全文、模型完整响应、API Key、Webhook 或数据库密码。
错误信息只保留有限长度；异常堆栈只写入服务日志，不发送到飞书消息。

## 飞书告警建议

当前飞书 Action 用于 CI 汇总。生产环境建议由日志平台或定时健康检查聚合以下条件后再告警：

- 连续两次 `/api/health` 失败；
- 5 分钟内 `http.request` 的 5xx 比例超过 5%；
- OCR、模型、TTS 或批次处理失败率超过阈值；
- PostgreSQL 连接失败或慢查询持续出现；
- 磁盘使用率超过 85%，或容器反复重启。

飞书消息只发送服务名、事件、次数、时间窗口、`request_id` 或日志查询链接，不发送原始日志。

## 部署服务异常通知

仓库包含可选的 `.github/workflows/service-health.yml`，每 10 分钟检查一次部署环境的健康
接口。配置以下 GitHub Actions Secrets 后，检查失败会复用同一个飞书机器人发送告警：

- `SERVICE_HEALTH_URL`：完整健康检查地址，例如 `https://tutor.example.com/api/health`；
- `SERVICE_HEALTH_TOKEN`：可选的 Bearer Token，不需要鉴权时留空；
- `FEISHU_WEBHOOK_URL`、`FEISHU_WEBHOOK_SECRET`：飞书机器人凭据。

CLI 配置示例：

```bash
gh secret set SERVICE_HEALTH_URL --repo ningkaikok/dotty-tutor
gh secret set SERVICE_HEALTH_TOKEN --repo ningkaikok/dotty-tutor  # 可选
```

未设置 `SERVICE_HEALTH_URL` 时，工作流会跳过，不会发送空告警。当前工作流只在失败时通知；
恢复通知和多实例/指标告警应交给 Sentry、Prometheus 或 Grafana 等持续监控系统。
