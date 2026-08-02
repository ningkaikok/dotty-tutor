# 路线图与生产边界

Dotty Tutor 当前是本地优先的 MVP。核心教材数字化和互动辅导闭环已经可用，但尚未达到
匿名公网服务所需的安全性、隔离性和可运维性。

## 已完成

- 图片和大 PDF 分块上传、暂停续传与批次处理。
- MinerU 和 pypdf OCR 路径。
- 结构化题目、四步讲解和三层引导卡生成。
- 文本与视觉双模型审校、来源绑定和结构质量门禁。
- 选择、判断、简答和画线题交互。
- 学生答案检查、分层 Help 和画布动作。
- Azure、Qwen3-TTS 与浏览器语音回退。
- PostgreSQL JSONB 题目、审校和提示持久化。
- 可编程 `LessonDocument`、内容块渲染器注册表和课程播放器。
- 学习会话、作答记录和知识点掌握度的本地演示闭环。
- GitHub Actions 后端测试、前端构建和 Docker 构建。

## 当前限制

- API 没有登录、授权、用户、班级或租户隔离。
- OCR、PDF 完成和模型调用仍在 HTTP 请求内同步执行。
- 模型/OCR 选择和部分读取缓存属于单个 FastAPI 进程。
- PDF、Markdown 和题图仍保存在本地文件系统。
- `needs_review` 只是可见告警，尚未成为发布阻断条件。
- 单页快速导入没有进入完整审校和持久化流水线。
- 选择题和简答题没有统一的确定性答案引擎。
- 前端当前最多展示 5 道题。
- 数据库表由 `create_all()` 初始化，尚无 Alembic 迁移历史。
- 已有结构化日志和请求 ID，但尚无集中式指标、追踪、错误监控和自动备份。

## P0：受控公网测试

- [ ] 用户登录、角色权限和教材资源归属。
- [ ] 未审核题目禁止发布给学生。
- [ ] Redis 任务队列、独立 worker、任务重试、取消和幂等状态。
- [ ] 对象存储或可靠持久卷，以及资源生命周期管理。
- [ ] Alembic 数据库迁移、低权限账号、SSL 和备份恢复。
- [ ] 上传配额、限流、恶意文件扫描和数据保留策略。
- [ ] 结构化日志、请求 ID、错误追踪和 readiness/liveness。
- [ ] HTTPS、生产域名、CORS 和可信 Host 配置。

## P1：协作与质量

- [x] `LICENSE`、`CONTRIBUTING.md`、`CODE_OF_CONDUCT.md` 和 `SECURITY.md`。
- [x] Issue/PR 模板和 CODEOWNERS。
- [ ] main 分支 Ruleset。
- [ ] Python Ruff/类型检查和前端 ESLint/Prettier。
- [x] Playwright 端到端冒烟测试（导入、选择题、判断题、画线题和 Help 交互）。
- [x] 第一优先级题型：多选、填空、数值/公式题及确定性答案核对。
- [ ] CI 中增加真实 PostgreSQL 集成测试。
- [x] Dependabot 自动依赖更新。
- [ ] 依赖审查、CodeQL 和 Actions SHA 固定。
- [ ] Python 可复现依赖锁定。
- [ ] API 错误脱敏和管理员调试入口。

## P2：产品能力

- [x] 课程、学习会话、作答记录和知识点掌握度基础模型。
- [ ] 用户登录、班级、课程归属和教师管理模型。
- [ ] Manim/Canvas/WebGL 渲染 worker、对象存储和任务状态。
- [ ] 整本教材后台索引、分页题库和检索。
- [ ] 更完整的确定性数学判题引擎。
- [ ] TTS 音频缓存、异步预生成和 CDN。
- [ ] 可扩展并带 Schema 校验的绘图 DSL。
- [ ] 多语言 README 和国际化界面。
- [ ] 语义版本、GitHub Release 和自动发布说明。

## 架构演进方向

```text
React / CDN
  → API Gateway / FastAPI
       ├─ PostgreSQL
       ├─ Redis
       ├─ 对象存储
       └─ 后台任务队列
              ├─ OCR Worker
              ├─ 题目生成与审校 Worker
              └─ TTS Worker
```

在进程内状态外部化之前，不应简单增加 Uvicorn worker 数量；否则不同进程看到的模型选择、
任务状态和缓存可能不一致。
