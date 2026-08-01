# 本地开发指南

本文说明如何启动 Dotty Tutor 的基础开发环境，以及如何按需接入 Ollama、MinerU、
Qwen3-TTS 和 Azure Speech。

## 环境要求

- Python 3.12
- Node.js 20+
- PostgreSQL 16+
- 可选：Ollama、MinerU、Qwen3-TTS、Codex CLI

## 安装后端

如果只想快速体验完整服务，优先使用 README 中的 Docker Compose 方法。本节用于需要热更新、
调试后端或接入本地模型的开发环境。

```bash
git clone https://github.com/ningkaikok/dotty-tutor.git
cd dotty-tutor

python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt
```

创建本地数据库：

```bash
createdb dotty_tutor
```

默认连接为 `postgresql+psycopg:///dotty_tutor`。使用密码或非本机数据库时设置：

```bash
export DATABASE_URL="postgresql+psycopg://user:password@127.0.0.1:5432/dotty_tutor"
```

可以复制环境变量模板后按需修改，但不要提交真实密钥：

```bash
cp .env.example .env
```

当前应用不会自动读取 `.env`；开发时需要在 shell 中导出变量，或使用自己选择的环境加载工具。

启动 FastAPI：

```bash
cd backend
../.venv/bin/python -m uvicorn app:app --reload --port 8010
```

无需本地模型的界面开发可以使用 Mock：

```bash
cd backend
MODEL_PROVIDER=mock REVIEW_PROVIDER=mock VISION_PROVIDER=mock \
  ../.venv/bin/python -m uvicorn app:app --reload --port 8010
```

## 安装前端

另开终端：

```bash
cd frontend
npm ci
npm run dev
```

打开 <http://localhost:5174>。Vite 会把 `/api` 代理到 <http://127.0.0.1:8010>。

## 常用环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg:///dotty_tutor` | PostgreSQL 连接地址 |
| `DOTTY_DATA_DIR` | 项目下 `data/` | PDF、Markdown 和题图目录 |
| `CORS_ORIGINS` | 本地 Vite 地址 | 允许访问 API 的来源列表 |
| `TRUSTED_HOSTS` | 空 | 可选可信 Host 列表 |
| `MODEL_PROVIDER` | `ollama` | `ollama`、`codex` 或 `mock` |
| `MODEL_NAME` | `qwen2.5:3b` | 生成模型名称 |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama 地址 |
| `MINERU_COMMAND` | 自动探测 | MinerU 可执行文件路径 |
| `REVIEW_PROVIDER` | `ollama` | 文本审校 provider |
| `REVIEW_MODEL` | `qwen2.5:7b` | 文本审校模型 |
| `VISION_PROVIDER` | `codex` | 视觉审校 provider |
| `VISION_MODEL` | `default` | 视觉审校模型 |
| `TTS_PROVIDER` | `auto` | `auto`、`azure` 或 `qwen` |
| `QWEN_TTS_URL` | `http://127.0.0.1:8020` | Qwen3-TTS 服务地址 |

完整示例见项目根目录的 [`.env.example`](../.env.example)。

## Ollama

安装并启动 [Ollama](https://ollama.com)，然后至少下载一个文本模型：

```bash
ollama pull qwen2.5:3b
ollama serve
```

上传页会从 Ollama API 自动读取本地模型列表。模型不可用时，后端会按运行路径返回错误或
回退到 Mock。

## MinerU OCR

上传页的 `auto` 模式会优先使用 MinerU；未安装时回退到 PDF 文字层。纯扫描 PDF 需要
MinerU 或其他 OCR 服务。

MinerU 建议使用独立 Python 3.12 环境，避免和主后端依赖冲突：

```bash
python3.12 -m venv .mineru-venv
.mineru-venv/bin/pip install -U pip uv
.mineru-venv/bin/uv pip install -U "mineru[all]"
export MINERU_COMMAND="$PWD/.mineru-venv/bin/mineru"
```

整本 PDF 每 5 页规划一个批次，首批优先生成，其余批次按需处理。MinerU 输出的 Markdown、
模型提示词和题图保存在对应上传任务的资源目录中。

## Qwen3-TTS

Qwen3-TTS 使用独立 Python 环境：

```bash
python3.12 -m venv .qwen3-tts-venv
.qwen3-tts-venv/bin/pip install -U qwen-tts
cd backend
../.qwen3-tts-venv/bin/python qwen_tts_service.py
```

首次请求会下载 `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice`。默认音色为 `Serena`，可以通过
`QWEN_TTS_MODEL`、`QWEN_TTS_SPEAKER` 和 `QWEN_TTS_DEVICE` 修改。

没有合适 GPU 时，可以不启动该服务，前端会回退到浏览器语音。

## Azure Speech

```bash
export TTS_PROVIDER=azure
export AZURE_SPEECH_KEY="your-key"
export AZURE_SPEECH_REGION="eastasia"
export AZURE_SPEECH_VOICE="zh-CN-XiaoxiaoNeural"
```

Azure 凭据只应存在于本地环境变量、服务器密钥管理或 GitHub Secrets 中。

## 数据持久化

- PostgreSQL 保存上传任务、结构化题目、审校结果和引导卡。
- `data/uploads/<uploadId>/` 保存 PDF、OCR Markdown 和题图。
- 完成合并后会删除上传分块，仅保留原 PDF。
- 后端重启后可以从 PostgreSQL 恢复教材和已生成题目。
- `backend/migrate_sqlite_to_postgres.py` 用于迁移旧 SQLite 数据。

迁移命令：

```bash
.venv/bin/python backend/migrate_sqlite_to_postgres.py
```

## 测试

```bash
.venv/bin/python -m unittest discover -s backend -p 'test_*.py' -v
cd frontend
npm run build
```

当前 CI 会运行后端单元测试、前端 TypeScript/生产构建和后端 Docker 镜像构建。

## 分支与提交

```bash
git switch -c feat/your-change
git add .
git commit -m "feat: describe the change"
git push -u origin feat/your-change
```

请通过 Pull Request 合并到 `main`，并在修改用户可见行为、配置或文档时同步更新
[`CHANGELOG.md`](../CHANGELOG.md)。

## 自动生成 CHANGELOG

项目使用 [git-cliff](https://git-cliff.org/) 根据 Conventional Commits 更新
`CHANGELOG.md` 的 `Unreleased` 区域，不会覆盖已有版本记录。分类规则与项目开发规范一致：

- `feat` → `Added`
- `fix` → `Fixed`
- `perf`、`refactor` → `Changed`
- `docs`、`style`、`chore`、`test` 不写入 CHANGELOG

本地安装 `git-cliff` 后运行：

```bash
scripts/generate-changelog.sh
```

GitHub Actions 会在 `main` 更新后自动生成变更记录，并创建一个
`chore/generated-changelog` Pull Request。合并前请检查用户视角措辞、重复条目和版本范围。
