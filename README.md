# Dotty Tutor

[![CI](https://github.com/ningkaikok/dotty-tutor/actions/workflows/ci.yml/badge.svg)](https://github.com/ningkaikok/dotty-tutor/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ningkaikok/dotty-tutor)](https://github.com/ningkaikok/dotty-tutor/releases)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev/)

面向中文教材与个人错题复习的 AI 学习平台。

Dotty Tutor 在内容生产工作台中将 PDF 或扫描教材转换为带来源、公式、题图和审校记录的结构化题目；
学生学习空间只负责互动练习、错题陪练、掌握验证和复习，不向学生暴露 OCR、模型或上传配置。

> 当前项目处于 MVP 阶段，适合本地体验和受控内测，暂不建议将匿名 API 直接暴露到公网。

![Dotty Tutor 教材练习界面](demo-verified.png)

## 功能

### 内容生产工作台（`/studio`）

把 PDF 教材变成可作答、可审校、带来源的结构化题库：大 PDF 分块上传与批次处理、MinerU/pypdf 双路 OCR、
多模型生成路径、统一审校与质量门禁、送审发布和不可变运行审计。细节见
[系统架构](docs/architecture.md)。

### 学生学习空间（`/learn`）

只暴露学习本身，不暴露 OCR、模型和上传配置：七种题型交互、分层 Help、可编程课程播放器，
以及断网可恢复的学习会话与掌握度记录。

### AI 错题陪练（`/mistakes`）

拍照录题、错误原因归因、单题多轮陪练线程、变式掌握验证和 1/3/7 天复习计划，形成完整闭环。
产品设计见 [AI 错题陪练产品规划](docs/mistake-coach-plan.md)。

### 平台

- **语音三级回退**：Azure Speech → Qwen3-TTS → 浏览器 Web Speech。
- **持久化**：PostgreSQL JSONB 存储题目、审校结果与学习证据；Job Store + 独立 Worker 执行长任务。

## 工作流程

```text
产品首页
  ├─ 学生学习空间：已发布互动试卷 / 错题陪练 / 掌握与复习
  │    └─ AI 错题陪练：拍照错题 → 确认归类 → 多轮陪练 → 变式验证 → 复习计划
  └─ 内容生产工作台：PDF / 扫描教材 → OCR → 结构化出题 → 审校 → 互动预览
```

登录鉴权、微信内体验和更完整的数学判题按[产品规划](docs/mistake-coach-plan.md)逐步完善。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | React 19、React Router、TypeScript、Vite、KaTeX |
| API | FastAPI、Pydantic、Uvicorn |
| 数据 | PostgreSQL、SQLAlchemy、JSONB、本地文件资源 |
| OCR | MinerU、pypdf |
| 模型 | Ollama、Codex CLI、Mock 回退 |
| TTS | Azure Speech、Qwen3-TTS、Web Speech API |
| 工程结构 | pnpm workspace monorepo（apps/web + apps/api）、uv 精确锁定 |
| 测试与门禁 | Vitest、unittest、Playwright E2E、Ruff、ESLint、Pyright（basic）、OpenAPI 类型漂移检查 |

## 快速开始

本地开发推荐"本机服务 + Docker PostgreSQL"，可直接复用本机的 Codex 登录、MinerU 环境和
Qwen3-TTS 缓存：

```bash
cp .env.docker.example .env
cp .env.local.example .env.local
# 将 .env.local 的 POSTGRES_PASSWORD 改成 .env 中相同的值
scripts/dev-local.sh
```

打开 <http://localhost:59174>，从首页选择入口。最简单的体验方式是 Docker Compose（默认使用 Mock 模型，
不下载模型权重）：

```bash
git clone https://github.com/ningkaikok/dotty-tutor.git
cd dotty-tutor

cp .env.docker.example .env
# 编辑 .env，为 POSTGRES_PASSWORD 设置一个长且只包含 URL 安全字符的随机密码
docker compose up --build --detach
```

打开 <http://localhost:8080>；PostgreSQL 和教材文件保存在命名卷中。

本机开发页（`59174`，走本机 FastAPI `8010` 和仓库内 `data/`）与 Docker 页面（`8080`，走容器内服务
和命名卷）是两套独立环境，不要混用同一批教材。完整环境变量、模型与 OCR 配置、手动安装步骤见
[本地开发指南](docs/development.md)；Docker 运维见[部署文档](docs/deployment.md)。

## 文档

- [系统架构与调用流程](docs/architecture.md)
- [代码结构、复用决策与扩展指南](docs/codebase-guide.md)
- [产品路线图](docs/product-roadmap.md)
- [技术路线图](docs/engineering-roadmap.md)
- [AI 运行治理与后台任务演进计划](docs/runtime-governance-plan.md)
- [后端架构学习指南](docs/backend-learning-guide.md)
- [前端架构学习指南](docs/frontend-learning-guide.md)
- [AI 错题陪练产品规划](docs/mistake-coach-plan.md)
- [本地开发与模型配置](docs/development.md)
- [API 接口](docs/api.md)
- [可编程课程与学习闭环](docs/programmable-learning.md)
- [部署与运维](docs/deployment.md)
- [日志与运行监控](docs/observability.md)
- [路线图与生产边界](docs/roadmap.md)
- [模型与系统测试报告](docs/model-evaluation-report.md)
- [参与贡献](CONTRIBUTING.md)
- [安全策略](SECURITY.md)
- [支持说明](SUPPORT.md)
- [变更记录](CHANGELOG.md)

## 测试

```bash
cd apps/api && ../.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
cd apps/web && npm run build
```

GitHub Actions 会在每次推送和 Pull Request 中运行后端测试、前端构建，以及完整 Docker
Compose 构建和健康检查。

## 参与开发

请优先通过 Issue 讨论问题和方案，代码修改使用独立分支并通过 Pull Request 合并。提交前请
运行后端测试和前端构建，不要提交 `.env`、模型权重、教材文件、`data/` 或构建产物。

参与前请阅读[贡献指南](CONTRIBUTING.md)和[行为准则](CODE_OF_CONDUCT.md)。安全漏洞请按照
[安全策略](SECURITY.md)私下报告，不要创建公开 Issue。

## 许可证

除非文件中另有声明，本项目的代码和原创文档使用
[Apache License 2.0](LICENSE) 授权。第三方依赖、模型、教材和用户上传内容分别遵循其原始
许可证或权利声明，不因本仓库许可证而自动获得授权。
