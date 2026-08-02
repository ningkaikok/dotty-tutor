# Dotty Tutor

[![CI](https://github.com/ningkaikok/dotty-tutor/actions/workflows/ci.yml/badge.svg)](https://github.com/ningkaikok/dotty-tutor/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/ningkaikok/dotty-tutor)](https://github.com/ningkaikok/dotty-tutor/releases)
[![License](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev/)

面向中文教材的 AI 数字化与互动辅导平台。

Dotty Tutor 将 PDF 或扫描教材转换为带来源、公式、题图和审校记录的结构化题目，并通过
分步讲解、选择/多选/判断/填空/数值/简答/画线交互和中文语音，帮助学生完成练习。

> 当前项目处于 MVP 阶段，适合本地体验和受控内测，暂不建议将匿名 API 直接暴露到公网。

![Dotty Tutor 教材练习界面](demo-verified.png)

## 功能

- 大 PDF 分块上传、暂停续传、页范围批处理和历史教材恢复。
- MinerU OCR、PDF 文字层解析与公式/题图提取。
- Ollama、Codex CLI 和 Mock 三种题目生成路径。
- 文本与视觉双模型审校，以及确定性结构质量门禁。
- 选择题、多选题、判断题、填空题、数值/公式题、简答题和画线题交互。
- 填空题支持多空答案；数值题支持容差；多选题支持完整选项集合校验。
- 基于学生当前答案的分层 Help 提示。
- Azure Speech、Qwen3-TTS 和浏览器语音三级回退。
- PostgreSQL JSONB 持久化题目、审校结果和引导卡。

## 工作流程

```text
PDF / 扫描教材
  → OCR 与公式、题图提取
  → 结构化出题
  → 文本和视觉审校
  → 质量门禁与 PostgreSQL 持久化
  → React 互动练习
  → 分层提示与中文 TTS
```

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | React 18、TypeScript、Vite、KaTeX |
| API | FastAPI、Pydantic、Uvicorn |
| 数据 | PostgreSQL、SQLAlchemy、JSONB、本地文件资源 |
| OCR | MinerU、pypdf |
| 模型 | Ollama、Codex CLI、Mock 回退 |
| TTS | Azure Speech、Qwen3-TTS、Web Speech API |

## 快速开始

最简单的体验方式是使用 Docker Desktop 或 Docker Engine + Compose：

```bash
git clone https://github.com/ningkaikok/dotty-tutor.git
cd dotty-tutor

cp .env.docker.example .env
# 编辑 .env，为 POSTGRES_PASSWORD 设置一个长且只包含 URL 安全字符的随机密码
docker compose up --build --detach
```

打开 <http://localhost:8080>。默认 Compose 使用 Mock 模型，不会下载模型权重；PostgreSQL
和教材文件分别保存在命名卷中。

```bash
docker compose ps
docker compose logs --follow api
docker compose down
```

`docker compose down` 会保留数据卷。完整 Docker 配置、外部模型连接和生产部署说明见
[部署与运维](docs/deployment.md)。

不使用 Docker 时，需要 Python 3.12、Node.js 20+ 和 PostgreSQL：

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

createdb dotty_tutor
cd backend
MODEL_PROVIDER=mock REVIEW_PROVIDER=mock VISION_PROVIDER=mock \
  ../.venv/bin/python -m uvicorn app:app --reload --port 8010
```

另开终端启动前端：

```bash
cd frontend
npm ci
npm run dev
```

本地开发地址是 <http://localhost:5174>。完整环境变量、模型和 OCR 配置见
[本地开发指南](docs/development.md)。

## 文档

- [系统架构与调用流程](docs/architecture.md)
- [本地开发与模型配置](docs/development.md)
- [API 接口](docs/api.md)
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
.venv/bin/python -m unittest discover -s backend -p 'test_*.py'
cd frontend && npm run build
```

GitHub Actions 会在每次推送和 Pull Request 中运行后端测试、前端构建，以及完整 Docker
Compose 构建和健康检查。

## 项目状态

当前版本已完成教材导入、结构化出题、审校、互动练习、分层提示、TTS 和 PostgreSQL
持久化闭环。公网生产部署仍需要用户鉴权、对象存储、异步任务队列、Alembic、限流、
监控和自动备份；详情见[路线图](docs/roadmap.md)。

## 参与开发

请优先通过 Issue 讨论问题和方案，代码修改使用独立分支并通过 Pull Request 合并。提交前请
运行后端测试和前端构建，不要提交 `.env`、模型权重、教材文件、`data/` 或构建产物。

参与前请阅读[贡献指南](CONTRIBUTING.md)和[行为准则](CODE_OF_CONDUCT.md)。安全漏洞请按照
[安全策略](SECURITY.md)私下报告，不要创建公开 Issue。

## 许可证

除非文件中另有声明，本项目的代码和原创文档使用
[Apache License 2.0](LICENSE) 授权。第三方依赖、模型、教材和用户上传内容分别遵循其原始
许可证或权利声明，不因本仓库许可证而自动获得授权。
