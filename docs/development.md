# 本地开发指南

本文说明如何启动 Dotty Tutor 的基础开发环境，以及如何按需接入 Ollama、MinerU、
Qwen3-TTS 和 Azure Speech。

## 环境要求

- Python 3.12
- Node.js 20.19+（20.x）或 22.12+
- PostgreSQL 16+
- 可选：Ollama、MinerU、Qwen3-TTS、Codex CLI

## 推荐开发模式：本机服务 + Docker PostgreSQL

日常开发建议让 FastAPI、React、MinerU、Qwen3-TTS 和 Codex CLI 都运行在本机，
只使用 Docker 提供 PostgreSQL。这样可以直接复用本机登录态、模型缓存和 Apple Silicon
MPS，不需要把凭据或虚拟环境复制进 API 容器。

```bash
cp .env.docker.example .env
# 确保 .env 中 POSTGRES_PASSWORD 已填写
cp .env.local.example .env.local
# 将 .env.local 的 POSTGRES_PASSWORD 改成 .env 中相同的值

scripts/dev-local.sh
```

脚本会先检查 Node.js 是否满足 Vite 8 的运行基线；检查失败时不会启动 Docker 或本机服务。也可以单独运行
`scripts/check-node-version.sh`，或使用仓库根目录的 `.nvmrc`：

```bash
nvm install
nvm use
scripts/check-node-version.sh
```

检查通过后，脚本会启动 Docker PostgreSQL、本机 FastAPI、本机 `background_jobs` Worker、本机 Vite 和 Qwen3-TTS。打开
<http://localhost:5174>；按 `Ctrl-C` 会停止本机进程，但保留 PostgreSQL 数据卷。

本机 Codex、MinerU 和 Qwen3-TTS 的状态可以分别检查：

```bash
codex --version
.mineru-venv/bin/mineru --help
curl -fsS http://127.0.0.1:8020/health
curl -fsS http://127.0.0.1:8010/api/health
```

### Codex 连接慢或反复重连

先确认页面访问的是本机开发入口 `http://localhost:5174`。`http://localhost:8080` 是 Docker
Compose 入口，默认使用 `.env` 中的运行时配置，通常不会继承本机 Codex 登录态。

```bash
curl -fsS http://127.0.0.1:8010/api/models
codex login status
nc -vz 127.0.0.1 7897   # 按代理软件实际端口替换
curl -v --proxy http://127.0.0.1:7897 https://api.openai.com
```

代理请求返回 `api.openai.com` 的 `421` 或 `chatgpt.com` 的 `403` 并不等于链路断开：这两个地址的根路径
本来就不是已登录的模型请求入口，重点看 TLS 是否完成、耗时是否稳定，以及 Codex 是否能完成一次最小
`codex exec --ephemeral`。如果最小调用成功而页面仍显示“重连”，优先检查是否启动了多个服务入口或批量
任务正在等待前一个模型子进程，而不是重复登录。

后端的 Codex 适配器为每一次结构化生成、文字审核和视觉审核启动一个隔离的
`codex exec --ephemeral` 子进程；一题可能连续触发多次调用，因此浏览器网络面板会看到多次请求，
这不等于每次都发生了网络断线。批量处理由独立 Worker 执行，HTTP 请求只创建任务并返回 `202`，前端通过任务状态接口观察进度。若 CLI 日志出现
`state db discrepancy ... falling_back`，它是本机 Codex 状态库的回退警告，不是 OpenAI TLS 失败。
真正的网络错误通常会在后端日志的 `model.request.failed` / `model.review.failed` 中包含超时、代理或
连接拒绝信息。切换网络或代理后需要重启 `scripts/dev-local.sh`，让 FastAPI 继承新的代理环境变量。

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
createuser --pwprompt dotty_app
createdb -O dotty_app dotty_tutor
```

如果不使用上面的启动脚本，本地开发也可以使用显式密码连接。复制模板并填写同一组数据库凭据：

```bash
cp .env.example .env
# 编辑 .env，替换 replace-with-long-random-password
set -a
source .env
set +a
```

也可以只设置 `POSTGRES_HOST`、`POSTGRES_PORT`、`POSTGRES_USER`、`POSTGRES_PASSWORD` 和
`POSTGRES_DB`，后端会自动组装 `DATABASE_URL`。`DATABASE_URL` 优先级更高。不要提交真实密钥：

`.env` 已被 `.gitignore` 忽略；当前应用不会自动读取 `.env`，上面的 `source` 用于把变量导入当前 shell。

> 图片看起来与刚生成的结果不一致时，先确认服务使用的是同一份环境和全新测试数据库：
> `source .env.local` 后再启动后端。未加载环境文件时，PostgreSQL 可能回退到本机 socket 或另一套数据目录；
> 当前基线不读取旧数据库题目，需清空测试库并按当前导入流程重新生成。

### 独立切换陪练模型

内容生产端的运行时设置包含三个互不影响的选择：题目生成模型、统一审核模型（文字与图片共用）和错题陪练模型。
陪练模型通过 `GET /api/tutor-models` 查询、`POST /api/tutor-models/select` 切换，默认读取
`TUTOR_MODEL_PROVIDER` 与 `TUTOR_MODEL_NAME`。例如：

```env
TUTOR_MODEL_PROVIDER=codex
TUTOR_MODEL_NAME=gpt-5.6-sol
```

每轮陪练响应的 `modelRun` 会记录实际 provider、model 和是否回退；看到 `provider=mock` 或
`source=stored-guide-card` 说明本轮没有调用大模型。

题目生成和审核共用同一份 Codex 订阅模型目录，但审核仍是独立 Runtime；图片审核不会再额外占用一套
模型选择。Codex 目录默认包含 `default`、`gpt-5.6-sol`、`gpt-5.6-luna`、`gpt-5.6-terra`、
`gpt-5.5` 和 `gpt-5.4`，可通过 `CODEX_MODELS` 用逗号覆盖。目录只代表本机 Codex CLI 已登录且可执行，
不代表套餐一定授予每一个模型；真正的模型权限要到请求时由 Codex 返回。若下拉框中的 Codex 整组变灰，
通常是后端进程找不到 CLI（桌面版默认路径为 `/Applications/ChatGPT.app/Contents/Resources/codex`），
请重启后端或设置 `CODEX_COMMAND`，不要只刷新浏览器页面。

启动 FastAPI：

```bash
cd backend
../.venv/bin/python -m uvicorn app:app --reload --port 8010
```

无需本地模型的界面开发可以使用 Mock：

```bash
cd backend
MODEL_PROVIDER=mock REVIEW_PROVIDER=mock \
  ../.venv/bin/python -m uvicorn app:app --reload --port 8010
```

## 安装前端

另开终端：

```bash
cd frontend
npm ci
npm run dev
```

修改 FastAPI 的 `response_model` 或路径契约后，同步刷新前端生成类型并检查工作树：

```bash
npm run generate:api
npm run check:api
```

`check:api` 会在临时目录生成 OpenAPI 类型并与已提交文件逐字比较；它不会用生成结果静默覆盖工作树。

`frontend/package.json` 的 `engines` 与 Vite 8/Playwright 的实际要求一致；Node.js 18 或 Node.js 21 会在启动前
收到可操作的切换提示。

打开 <http://localhost:5174>。Vite 会把 `/api` 代理到 <http://127.0.0.1:8010>。

前端入口：

| 地址 | 用途 |
| --- | --- |
| <http://localhost:5174/> | 产品选择首页 |
| <http://localhost:5174/learn> | 学生学习空间，不显示教材上传和模型配置 |
| `http://localhost:5174/learn/papers/{id}` | 学生继续作答已发布互动试卷；错答自动进入错题本，讲解按需出现 |
| <http://localhost:5174/studio> | 教材导入、OCR、内容生成与互动预览 |
| <http://localhost:5174/mistakes> | AI 错题本、图片录入和确认 |
| <http://localhost:5174/mistakes/capture> | 手机拍照/相册上传与识别范围裁切 |
| `http://localhost:5174/mistakes/{id}/confirm` | 修正题干、知识点和错误原因 |
| `http://localhost:5174/mistakes/{id}/tutor` | 恢复该错题的有状态多轮陪练 |

入口使用 React Router；Vite 和 `docker/nginx.conf` 均已配置 SPA 回退，因此可直接打开子路径。

## 修改代码时从哪里开始

- 修改顶层页面或 URL：`frontend/src/App.tsx` 与对应 `frontend/src/apps/*`。
- 修改教材上传交互：`frontend/src/apps/textbook/import/`，不要把状态机重新写回页面组件。上传区支持一次加入多个
  PDF/图片；每个条目独立显示分块上传、OCR 处理和失败状态，最多三个任务并行，点击条目查看右侧结果。
- 修改教材 API/PDF 批次：`backend/api/routers/textbook_routes.py`；长流程在 `backend/application/services/textbook_processing.py`。
- 内容生产端“修复本题”调用 `POST /api/uploads/{uploadId}/questions/{sourceQuestionKey}/regenerate`，默认只重跑当前题并复用 OCR 缓存；需要重新识别页面时使用批次接口的 `refreshOcr=true`。
- 修改教材页面路由/缓存：`backend/textbook_ocr_pipeline.py`；调整启发式和门禁分别查看
  `domain/questions/` 下的 OCR 纯函数；MinerU 子进程和矢量 PDF 页面渲染细节仍在 `infrastructure/runtime/ocr_runtime.py`。
  Docker 后端镜像通过 `poppler-utils` 提供 `pdftoppm`，本机开发也需要 Poppler 才能启用矢量页渲染兜底。
- 修改模型题目结构：`application/services/lesson_generation.py`、`domain/questions/contracts.py` 和 `domain/questions/pipeline.py`。
- 修改错题功能：`backend/mistake_*.py` 与 `frontend/src/apps/mistake/`。
- 修改多轮状态：`backend/application/services/stateful_tutor.py`、`api/routers/tutoring_routes.py`、`persistence/tutoring_store.py` 和
  `frontend/src/apps/mistake/useMistakeTutor.ts`。

完整依赖方向、开源复用清单和扩展步骤见[代码结构与扩展指南](codebase-guide.md)。

数据库只支持全新空库启动。首次访问各领域 Store 时会根据
`backend/persistence/schema.py` 及领域 Store 中的当前 SQLAlchemy metadata 创建 PostgreSQL 或 SQLite schema；
项目不提供原地数据库升级，也不再维护编号 SQL 迁移链。已有本地测试库和 `data/` 资源应在切换当前基线前清空，
生产环境需要通过备份后重建空库并重新导入当前数据。

## 常用环境变量

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DATABASE_URL` | 由 `POSTGRES_*` 组装 | PostgreSQL 连接地址；优先使用 |
| `POSTGRES_HOST` | `127.0.0.1` | PostgreSQL 主机 |
| `POSTGRES_PORT` | `5432` | PostgreSQL 端口 |
| `POSTGRES_USER` | `dotty_app` | 应用数据库用户 |
| `POSTGRES_PASSWORD` | 空 | 应用数据库密码；设置后启用显式密码连接 |
| `POSTGRES_DB` | `dotty_tutor` | 数据库名称 |
| `POSTGRES_SSLMODE` | 空 | 云数据库可设为 `require` |
| `DOTTY_DATA_DIR` | 项目下 `data/` | PDF、Markdown 和题图目录 |
| `CORS_ORIGINS` | 本地 Vite 地址 | 允许访问 API 的来源列表 |
| `TRUSTED_HOSTS` | 空 | 可选可信 Host 列表 |
| `MODEL_PROVIDER` | `codex` | `ollama`、`codex` 或 `mock` |
| `MODEL_NAME` | `default` | 生成模型名称 |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama 地址 |
| `MINERU_COMMAND` | 自动探测 | MinerU 可执行文件路径；本机脚本会优先注入仓库根目录 `.mineru-venv/bin/mineru` |
| `REVIEW_PROVIDER` | `codex` | 统一文字与图片审校 provider |
| `REVIEW_MODEL` | `gpt-5.6-sol` | 统一文字与图片审校模型 |
| `CODEX_MODELS` | 内置常用订阅模型 | 可选；控制 Codex 下拉框模型列表 |
| `TTS_PROVIDER` | `auto` | `auto`、`azure` 或 `qwen` |
| `QWEN_TTS_URL` | `http://127.0.0.1:8020` | Qwen3-TTS 服务地址 |

完整示例见项目根目录的 [`.env.example`](../.env.example)。本机开发专用配置见
[`.env.local.example`](../.env.local.example)；完整 Docker 配置见
[`.env.docker.example`](../.env.docker.example)。

## Ollama

安装并启动 [Ollama](https://ollama.com)，然后至少下载一个文本模型：

```bash
ollama pull qwen2.5:3b
ollama serve
```

上传页会从 Ollama API 自动读取本地模型列表。模型不可用时，后端会按运行路径返回错误或
回退到 Mock。

## MinerU OCR

上传页的 `auto` 模式按页选择 Provider：文字层完整的电子页使用 pypdf，扫描页、公式密集页或质量门禁
失败页使用 MinerU；未安装 MinerU 时只能保留 PDF 文字层并把空白/损坏来源交给发布门禁。纯扫描 PDF
仍需要 MinerU 或其他 OCR 服务。

MinerU 建议使用独立 Python 3.12 环境，避免和主后端依赖冲突：

```bash
python3.12 -m venv .mineru-venv
.mineru-venv/bin/pip install -U pip uv
.mineru-venv/bin/uv pip install -U "mineru[all]"
export MINERU_COMMAND="$PWD/.mineru-venv/bin/mineru"
```

如果上传页仍显示“MinerU OCR · 未安装”，先确认浏览器连接的是本机后端 `8010`，而不是
Docker 的 `8080` 网关：`curl http://127.0.0.1:8010/api/ocr` 返回的 `providers` 中，`id=mineru`
的 `available` 应为 `true`。
Docker 基础镜像只包含 FastAPI 和 pypdf，不会自动看到宿主机的 `.mineru-venv`（尤其不能把
macOS 虚拟环境挂进 Linux 容器）。此时下拉框禁用 MinerU 是正确的安全行为，避免选择后任务
悄悄回退到 PDF 文字层；需要 Docker 使用 MinerU 时，应提供 Linux MinerU 镜像或独立 OCR
服务，再增加对应 Runtime 适配器。

整本 PDF 每 5 页规划一个批次，首批优先生成 5 道预览题，其余批次按需处理。内容生产预览也可以点击
“生成整套试卷”创建 `textbook.paper.generate` 后台任务；整卷模式每批最多处理 20 题，服务端硬限制最多
50 页、100 道题。可以用 `DOTTY_MAX_FULL_PAPER_PAGES` 和 `DOTTY_MAX_FULL_PAPER_QUESTIONS` 降低本机上限，
但环境变量不能突破代码硬限制。相邻且路由相同的页面合并调用，
减少 MinerU 进程启动次数；结果以 PDF SHA-256、页范围、Provider 和流水线版本写入 `ocr-cache`。
MinerU 输出的 Markdown、模型提示词和题图保存在对应上传任务的资源目录中。

整卷任务通过 `GET /api/jobs/{jobId}` 轮询，批次汇总也可从
`GET /api/uploads/{uploadId}/full-paper/summary` 恢复。汇总中的成功批次不会因 Worker 重试重复生成；
单批 OCR 或模型异常会进入 `failedBatches` 和 `summary.batches`，其它批次继续处理。取消只在批次之间和
OCR/题目循环安全点生效；长任务运行时仍可查看和编辑首批预览题。达到页数或题数上限时汇总返回
`limitReached=true`，不会继续产生 OCR 或模型调用。

## Qwen3-TTS

Qwen3-TTS 使用独立 Python 环境：

```bash
python3.12 -m venv .qwen3-tts-venv
.qwen3-tts-venv/bin/pip install -U qwen-tts
cd backend
../.qwen3-tts-venv/bin/python infrastructure/runtime/qwen_tts_service.py
```

首次启动可能会下载 `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice`。默认音色为 `Serena`，可以通过
`QWEN_TTS_MODEL`、`QWEN_TTS_SPEAKER` 和 `QWEN_TTS_DEVICE` 修改。

服务启动时默认先加载模型，再合成一段不会返回给用户的短音频。这个推理级预热会让服务晚几秒进入
ready 状态，但能避免学生第一次播放承担设备内核初始化开销。`GET /health` 的 `warmup` 字段会显示
是否完成和耗时；资源紧张时可设置 `QWEN_TTS_WARMUP_ENABLED=0`，也可用
`QWEN_TTS_WARMUP_TEXT` 修改预热文本。课程播放器只预取当前/首个步骤，切换题目时会取消旧的浏览器 TTS 请求，
避免多个本地合成任务排队阻塞。

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
- `data/mistakes/<mistakeId>/` 保存错题原图和 MinerU 提取的题图；元数据存于 `mistake_items`。
- 完成合并后会删除上传分块，仅保留原 PDF。
- 后端重启后可以从 PostgreSQL 恢复教材和已生成题目。
## 测试

```bash
cd backend && ../.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
cd frontend
npm run build
npx playwright install chromium   # 首次运行或浏览器版本更新时执行
npm run test:e2e
```

Playwright 测试会启动独立的 Vite 开发服务器，并通过固定 API mock 覆盖双产品入口、直接子路径、
导入、选择/多选题、判断题、
填空题、数值题、画线题和 Help 交互；不会调用本地模型、OCR 或数据库。失败时 CI 会保留 HTML 报告、trace、
截图和视频，便于下载排查。浏览器探索可以使用 Computer Use，稳定回归统一使用 Playwright。

当前 CI 会并行运行后端单元测试、前端 TypeScript/生产构建、Playwright 浏览器冒烟测试和后端
Docker 镜像构建。所有检查结束后，`feishu-notify-action` 会把各项结果推送到飞书群；未配置
仓库 Secrets 时会自动跳过，不影响 CI。Fork 发起的 Pull Request 不会发送通知，以避免暴露
飞书 Webhook。

后端默认以 JSON 输出结构化运行日志，使用 `LOG_LEVEL=DEBUG` 可以临时查看分块上传等细节；
事件字段和生产健康检查工作流见[日志与运行监控](observability.md)。

### 配置飞书 CI 通知

在 GitHub 仓库的 **Settings → Secrets and variables → Actions** 中添加：

- `FEISHU_WEBHOOK_URL`：飞书群自定义机器人的 Webhook 地址；
- `FEISHU_WEBHOOK_SECRET`：机器人启用签名校验时填写，否则可不添加。

也可以使用 GitHub CLI：

```bash
gh secret set FEISHU_WEBHOOK_URL
gh secret set FEISHU_WEBHOOK_SECRET
```

工作流引用 [ningkaikok/feishu-notify-action](https://github.com/ningkaikok/feishu-notify-action)，
每个仓库和飞书群建议使用独立机器人，不要在多个项目之间共享 Webhook。

## 分支与提交

```bash
git switch -c feat/your-change
git add .
git commit -m "feat: describe the change"
git push -u origin feat/your-change
```

请通过 Pull Request 合并到 `main`，并在修改用户可见行为、配置或文档时同步更新
[`CHANGELOG.md`](../CHANGELOG.md)。

## 手动生成 CHANGELOG 初稿

项目保留 [git-cliff](https://git-cliff.org/) 作为发布准备工具，根据 Conventional Commits 生成
`CHANGELOG.md` 的 `Unreleased` 初稿，不会覆盖已有版本记录。分类规则与项目开发规范一致：

- `feat` → `Added`
- `fix` → `Fixed`
- `perf`、`refactor` → `Changed`
- `docs`、`style`、`chore`、`test` 不写入 CHANGELOG

本地安装 `git-cliff` 后运行：

```bash
scripts/generate-changelog.sh
```

默认会先同步远程版本 Tag，并只扫描“最新 Tag 到当前提交”的范围，避免旧版本条目再次进入 `Unreleased`。
离线或需要指定范围时，可运行 `scripts/generate-changelog.sh v0.3.0..HEAD`。

请只在准备发布或整理一组用户可见改动时运行。脚本会替换 `Unreleased` 区域，因此运行后必须检查：

- 将英文提交摘要改写为中文用户视角描述。
- 合并重复条目，并删除只有内部实现意义的内容。
- 确认没有把上一个正式版本已经发布的内容重新加入。
- 在 `release/*` 分支或对应功能 PR 中提交审核后的结果。

仓库不再在 `main` 推送后自动创建 CHANGELOG PR。自动流程无法判断用户影响，会重复扫描最新 Tag 后的
提交，并可能覆盖已经人工整理的文案。
