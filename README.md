# Dotty Tutor — AI 教材数字化与互动辅导平台

变更记录见 [CHANGELOG.md](CHANGELOG.md)。

当前自动化测试、真实教材链路验证、已知风险和多模型横评方法见
[模型与系统测试报告](docs/model-evaluation-report.md)。

Dotty Tutor 是一个面向中文教材的 AI 数字化与互动辅导平台：把 PDF/扫描教材转换为带
来源、公式、题图和审校记录的结构化题目，再通过分步讲解、判断/选择/简答/画线交互和 TTS
帮助学生完成练习。当前版本仍是本地优先的 MVP，适合内测和功能验证；生产部署边界及
上线前需要补齐的能力见[部署方法](#部署方法)和[Demo 与生产版本的边界](#demo-与生产版本的边界)。

当前闭环如下：

```text
扫描教材页
  → 图片直接上传；大 PDF 按 5 MB 分块并支持暂停续传
  → 后端合并 PDF，每 5 页规划一个处理批次
  → 首屏只对第一个 5 页批次运行 MinerU，先生成可预览课程
  → 真实 OCR、公式识别和题目结构化
  → 第二文本模型逐字段复核，规范 OCR 字符、公式和四步讲解
  → 多模态模型读取题图，过滤错图并核对图形、题干和讲解冲突
  → 题目、答案和 guide cards 在教材数字化阶段存库
  → React 播放 Canvas + Text + speechText
  → 学生点击 Help
  → Python 根据当前答案选择 guide_context
  → Dotty 只给下一步提示
```

## 当前系统架构

项目采用本地优先的前后端分离结构。React 页面只通过 `/api` 调用 FastAPI；模型、OCR、审校、存储和 TTS 都由后端统一编排，浏览器不直接接触模型密钥或本地模型进程。

```mermaid
flowchart LR
  User["学生 / 教师"] --> Web["React + Vite :5174"]
  Web -->|"/api/*"| API["FastAPI :8010"]

  subgraph Backend["后端编排层"]
    API --> Pipeline["上传、题目与辅导流水线\nbackend/app.py"]
    Pipeline --> OCR["OCR Runtime"]
    Pipeline --> Model["Model Runtime"]
    Pipeline --> Review["Review Runtime"]
    Pipeline --> Store["TutorStore"]
    API --> TTS["TTS Router"]
    Review --> Model
  end

  OCR --> MinerU["MinerU 子进程"]
  OCR --> PyPDF["pypdf 文字层"]
  Model --> Ollama["Ollama :11434"]
  Model --> Codex["Codex CLI 子进程"]
  Model --> Mock["Mock 离线回退"]
  Store --> PostgreSQL["PostgreSQL 18\ndotty_tutor"]
  Store --> Files["PDF / Markdown / 题图\ndata/uploads/"]
  TTS --> Azure["Azure Speech HTTPS"]
  TTS --> Qwen["Qwen3-TTS :8020"]
  Web -. "音频接口失败" .-> BrowserTTS["浏览器 speechSynthesis"]
```

### 组件职责

| 组件 | 主要文件 | 责任边界 |
| --- | --- | --- |
| 上传与模型选择页 | `frontend/src/TextbookImport.tsx` | 文件校验、PDF 分块/暂停续传、状态轮询、模型/OCR 切换、教材库入口 |
| 学习与交互页 | `frontend/src/App.tsx` | 题库导航、四步讲解、选项/判断/画线交互、Help 请求、语音播放与浏览器回退 |
| API 客户端与契约 | `frontend/src/api.ts`、`frontend/src/types.ts` | `/api` 请求封装和前后端数据类型 |
| 内容渲染 | `QuestionContent.tsx`、`MathText.tsx`、`DrawLineCanvas.tsx`、`GeometryCanvas.tsx` | 按 `contentBlocks` 渲染文字、LaTeX、题图、选项和画线操作 |
| 主编排服务 | `backend/app.py` | FastAPI 端点、上传任务、题目切分、生成、标准化、结构门禁、Help 与 TTS 路由 |
| 生成模型适配 | `backend/model_runtime.py` | Ollama、Codex CLI、Mock 的发现、选择和 JSON Schema 约束调用 |
| OCR 适配 | `backend/ocr_runtime.py` | MinerU 检测/子进程调用、页范围识别、Markdown 和题图落盘、pypdf 回退 |
| 双模型审校 | `backend/review_runtime.py` | OCR 规范化、文本复核、题图复核、视觉冲突后的二次修复 |
| 持久化 | `backend/storage.py` | PostgreSQL JSONB 任务/题目元数据和 `data/uploads/` 文件目录的恢复 |
| 本地语音服务 | `backend/qwen_tts_service.py` | 独立加载 Qwen3-TTS，提供 `:8020/health` 和 `:8020/tts` |

Vite 开发服务器在 `5174` 端口运行，并把 `/api` 代理到 FastAPI 的 `8010` 端口。Ollama 和 Qwen3-TTS 是可选的独立本地进程；Azure Speech 是可选的外部 HTTPS 服务。

## 主要调用流程

### 1. 页面初始化与运行时选择

`TextbookImport` 首次加载时并行调用：

1. `GET /api/models`：探测 Ollama 模型，并返回 Ollama、Codex、Mock 目录。
2. `GET /api/ocr`：探测 MinerU，计算 `auto` 实际命中 MinerU 还是 pypdf。
3. `GET /api/library`：从 PostgreSQL 读取已经完成的 PDF 教材。

模型和 OCR 的选择通过 `POST /api/models/select`、`POST /api/ocr/select` 写入当前 FastAPI 进程的全局运行时；它们会影响之后的生成请求，不是按用户或教材隔离的配置。

### 2. 单页图片或小文件导入

轻量入口是 `POST /api/textbook/import`：

```text
浏览器校验文件（最大 10 MB）
  → FastAPI 读取到内存
  → 手工原文优先；否则 MinerU / PDF 文字层
  → 生成一道题、四步 lessonSteps、三张 guideCards
  → 返回 QuestionPayload
```

这条路径用于快速 Demo：原文件不持久化，只生成一道题，也不执行完整 PDF 路径中的双模型审校、来源绑定和结构质量门禁。

### 3. 整本 PDF 分块与首批生成

完整 PDF 使用可续传路径：

1. 浏览器先校验 `%PDF-` 文件头和尾部 `%%EOF`，限制 500 MB。
2. `POST /api/uploads/init` 创建上传任务和本地目录。
3. 浏览器按 5 MB 调用 `PUT /api/uploads/{uploadId}/chunks/{index}`；分块按固定编号落盘，暂停后只补传缺失块。
4. `POST /api/uploads/{uploadId}/complete` 同步执行合并、SHA-256 计算、大小校验、PDF 包络校验和页数读取。
5. 后端只记录每 5 页的批次范围，不复制整本 PDF；上传分块在合并成功后删除，保留 `source.pdf`。
6. 首批默认处理第 1–5 页：MinerU 按页范围输出 Markdown、LaTeX 和题图；MinerU 不可用时使用 pypdf 文字层。MinerU 执行失败时只有在已取得文字层的调用路径中才能回退，否则会明确记录 OCR 失败。
7. OCR Markdown 按题号切分，每批最多取 5 道完整题，并保存 `source.md` 与实际结构化提示词 `model-prompt.md`。
8. 每道题依次完成生成、来源绑定、文本/视觉审校、确定性标准化、结构质量门禁和持久化。
9. 前端在 `complete` 请求等待期间每 800 ms 调用 `GET /api/uploads/{uploadId}/status` 展示进度；当前实现不是独立后台任务队列。

### 4. 单题生成与审校流水线

每个 OCR 题块在 `process_question_sources()` 中按以下顺序处理：

```text
OCR 题块
  → 第一模型按 JSON Schema 生成题目、4 步讲解、3 层引导卡
  → 绑定题号、页码、OCR 产物和当前题引用的图片
  → 第二文本模型核对错字、公式、选项和讲解
  → 有题图时调用视觉模型核对图片归属、图中事实和冲突
  → 有视觉冲突时再次调用文本模型修复讲解
  → 确定性修复公式、方程组选项、合并的 A-D 文字选项和图片选项
  → 构建 contentBlocks
  → 结构质量门禁校验图片顺序、选项数量、公式完整性和题干来源
  → 写入 PostgreSQL 和 lesson_store
```

质量门禁发现错误时会把 `publicationStatus` 和审校状态标记为 `needs_review`，页面仍会展示该题并显示“需要人工复核”；当前门禁是可见告警，不是发布阻断器。

### 5. 下一题与重新生成

- 如果下一题已在前端题库中，“下一题”只切换本地状态，不调用后端。
- 当前题库末尾遇到未处理批次时，前端调用 `POST /api/uploads/{uploadId}/batches/{batchId}/process`。
- 后续批次会额外读取前一页以补齐跨页题干；`sourceBatchId` 仍是目标批次，`sourcePages` 则记录包含重叠页的实际 OCR 范围。
- 已有 `source.md` 时直接复用缓存；否则重新执行 OCR。
- “重新生成本题”对当前批次传入 `force=true`，重新生成并替换该批次题目。
- 当前前端题库总量由 `QUESTION_LIMIT=5` 限制。

### 6. 学生回答与 Help

前端把学生文本、提示层级、模式以及画线结果提交到 `POST /api/help`：

1. 判断题有明确 `correctAnswer` 时，后端先做确定性判题。
2. 画线题根据 `requiredConnections` 与学生连接集合做确定性判题。
3. 其他题先检查学生等式是否与题干或标准步骤冲突。
4. 选中真实模型时，将题干、标准四步、当前 guide card、学生输入和确定性检查结果交给模型生成下一步反馈。
5. Mock 模式、题目未恢复或模型调用失败时，回退到已存的三层 guide cards；提示层级每次最多推进一级。

接口返回 `reply`、`assessment`、`guideContext`、`nextHintLevel`、`canvasAction` 和实际 `modelRun`。前端更新画布后朗读回复，并允许展开查看本次 guide context。

### 7. TTS 调用与回退

```text
App.speak(text)
  → POST /api/tts
  → TTS_PROVIDER=auto 且 Azure 凭据完整：Azure Speech Neural
  → 否则：代理 Qwen3-TTS :8020/tts
  → API 请求失败：浏览器 speechSynthesis(zh-CN)
```

`TTS_PROVIDER=azure` 但凭据缺失时，后端直接返回 503；`GET /api/tts/status` 可用于查看当前可用 provider。浏览器回退发生在前端，不是后端返回的音频 provider。

### 8. 持久化与恢复

- 本地和生产默认使用 PostgreSQL；数据库名为 `dotty_tutor`，可通过 `DATABASE_URL` 覆盖连接地址。
- `upload_jobs.result_json`、`batch_questions.payload_json` 和 `guide_cards_json` 使用 JSONB 保存完整结构化结果。
- `data/uploads/{uploadId}/source.pdf` 保存合并后的原 PDF。
- `data/uploads/{uploadId}/assets/{batchId}/` 保存 MinerU Markdown、模型提示词和题图。
- 内存中的 `pdf_uploads`、`lesson_store` 是读取缓存；缓存未命中时 `TutorStore` 从 PostgreSQL 恢复任务、题目和 guide cards。
- `GET /api/library/{uploadId}` 聚合已持久化题目，供前端恢复学习。

## 架构评审结论

当前架构适合作为本地单用户 Demo：模块边界清晰，OCR/模型/TTS 都可替换，PDF 和题目已具备持久化，且模型输出之后还有确定性质量门禁。主要边界如下：

- PDF 完成和批次处理仍是长时间同步请求，没有任务队列、worker、取消和并发限流。
- 模型/OCR 选择以及内存缓存属于单个 FastAPI 进程，没有用户、会话或租户隔离。
- 单页导入是轻量内存路径，没有进入完整审校和持久化流水线。
- 质量门禁只标记人工复核，未阻止有结构错误的题目进入页面。
- 选择题和简答题没有统一的确定性答案引擎；缺少可靠答案时仍依赖模型判定。
- 服务没有登录、权限、上传隔离或密钥管理界面，只适合受信任的本机环境；Azure 密钥必须只放在环境变量中。
- 前端目前最多展示 5 道题，整本教材的全量后台索引、检索和分页题库尚未实现。

当前版本支持三种可在上传页切换的模型运行方式：

- `Ollama 本地模型`：默认优先使用，自动读取本机已经下载的模型，零 API 费用。
- `Codex 订阅`：通过本机 `codex` CLI 复用已经登录的 ChatGPT/Codex 账号。
- `Mock 固定模式`：完全不调用模型，作为离线回退和界面对照。

模型会真实生成题目、四步讲解、`speechText`、三层引导卡，并在学生点击 Help 时
结合学生输入实时生成下一步提示。每次结果都带 `modelRun`，页面会显示实际命中的
provider/model 以及是否回退。

每道入库题还包含 `review`：默认用本地 `qwen2.5:7b` 做第二遍文本审校，用 Codex
读取实际题图做视觉审校。审校无法确定或发现题干/图片/讲解冲突时，状态会明确标为
`needs_review`，不会伪装成审核通过。可在启动后端前切换审校模型：

```bash
REVIEW_PROVIDER=ollama REVIEW_MODEL=qwen2.5:7b \
VISION_PROVIDER=codex VISION_MODEL=default \
../.venv/bin/python -m uvicorn app:app --reload --port 8010
```

## 启动

后端：

```bash
cd /Users/kiki/work/tutor-demo
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

# 本机首次运行；当前项目默认通过 Unix socket 连接 dotty_tutor
brew services start postgresql@18
createdb dotty_tutor

cd backend
../.venv/bin/python -m uvicorn app:app --reload --port 8010
```

默认连接为 `postgresql+psycopg:///dotty_tutor`。使用带密码、Docker 或云数据库时，在启动前设置：

```bash
export DATABASE_URL="postgresql+psycopg://user:password@127.0.0.1:5432/dotty_tutor"
```

存储层首次连接时会创建 `upload_jobs` 和 `batch_questions`。从旧 SQLite 数据库迁移可重复执行：

```bash
cd /Users/kiki/work/tutor-demo
.venv/bin/python backend/migrate_sqlite_to_postgres.py
```

迁移只复制数据库记录；原 PDF、Markdown 和题图继续保留在 `data/uploads/`。旧的 `data/dotty.sqlite3` 会作为备份保留，不会自动删除。

前端另开一个终端：

```bash
cd /Users/kiki/work/tutor-demo/frontend
npm install
npm run dev
```

打开 <http://localhost:5174>。

### Qwen3-TTS 本地语音

项目默认优先使用 Qwen3-TTS 生成中文讲解音频；Qwen 服务不可用时自动回退到浏览器
`speechSynthesis`。Qwen 官方建议使用独立的 Python 3.12 环境，本项目可这样安装并启动：

```bash
cd /Users/kiki/work/tutor-demo
python3.12 -m venv .qwen3-tts-venv
.qwen3-tts-venv/bin/pip install -U qwen-tts
cd backend
../.qwen3-tts-venv/bin/python qwen_tts_service.py
```

首次请求会自动下载 `Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice` 权重；默认使用 `Serena`
中文音色和耐心、清晰的老师语气。可通过 `QWEN_TTS_MODEL`、`QWEN_TTS_SPEAKER`、
`QWEN_TTS_DEVICE` 调整模型、音色和设备。后端的 `/api/tts` 会代理本地语音服务，
前端在服务未启动时仍可正常使用浏览器语音。

如需测试 Azure Speech Neural，可在启动后端前配置（不要把密钥提交到代码库）：

```bash
export TTS_PROVIDER=azure
export AZURE_SPEECH_KEY="你的 Azure Speech Key"
export AZURE_SPEECH_REGION="eastasia"
export AZURE_SPEECH_VOICE="zh-CN-XiaoxiaoNeural"
cd backend
../.venv/bin/python -m uvicorn app:app --reload --port 8010
```

`TTS_PROVIDER=auto` 时，如果 Azure 凭据存在会优先使用 Azure，否则尝试 Qwen3-TTS，
最后回退浏览器语音。

## 本地模型

先安装并启动 [Ollama](https://ollama.com)，然后至少下载一个文本模型：

```bash
ollama pull qwen2.5:3b
ollama serve
```

本机 Demo 会从 `http://127.0.0.1:11434/api/tags` 自动读取模型列表。纯扫描 PDF 由
MinerU 转成 Markdown、公式和题图；`qwen2.5:3b` 负责第一遍结构化，第二文本模型与
视觉审校模型再独立复核结果。

## MinerU OCR

上传页可以切换教材解析方式。`自动选择`会优先调用 MinerU；未安装时回退到 PDF
文字层抽取，并在页面显示实际命中的解析器。项目会自动检测：

```text
/Users/kiki/work/tutor-demo/.mineru-venv/bin/mineru
```

由于 MinerU 当前要求 Python 3.10–3.13，而主后端环境可能是 Python 3.14，建议使用
独立 Python 3.12 环境安装，避免依赖冲突：

```bash
cd /Users/kiki/work/tutor-demo
python3.12 -m venv .mineru-venv
.mineru-venv/bin/pip install -U pip uv
.mineru-venv/bin/uv pip install -U "mineru[all]"
```

本地 pipeline 及模型需要较多磁盘空间；安装后 Demo 会使用中文、公式和表格解析参数，
读取 MinerU 生成的主 Markdown，再传给 Ollama/Codex。也可以用环境变量
`MINERU_COMMAND=/absolute/path/to/mineru` 指向已有安装。

整本 PDF 会先按每 5 页规划批次，MinerU 通过 `--start/--end` 直接 OCR 第一批，其余批次按需处理，避免
用户等待 pypdf 展开或重写全书以及 MinerU 串行扫描整本教材。处理过程中前端每 800 ms 查询一次任务状态，显示合并、校验、批次规划、
MinerU OCR、课程生成等真实阶段，以及当前进度和已耗时。

## 本地持久化

- 上传中的 PDF 分块和合并后的源文件保存在 `data/uploads/<uploadId>/`；完成校验后会删除分块，仅保留原 PDF。
- 上传任务、处理结果和教材列表保存在本机 PostgreSQL 的 `dotty_tutor` 数据库。
- 每个 5 页批次生成的题目、讲解步骤和引导卡保存在 `batch_questions` 表。
- 题目 payload、审校、质量门禁和引导卡使用 JSONB 字段，常用任务字段保持关系型列。
- 第二遍文字审校、逐图归属判断、视觉事实、冲突和模型运行信息随题目一并持久化。
- MinerU Markdown 与题图保存在对应批次的 `assets/` 目录；题目只引用与该题匹配的持久化图片 URL。
- 后续批次向前重叠一页补齐跨页题干，并在模型调用前按题号切分，确保一次只生成一道完整原题。
- 后端重启后会从 PostgreSQL 恢复任务；首页“已持久化教材”可直接打开历史教材并继续生成下一题。
- `DATABASE_URL` 用于切换 PostgreSQL 实例；`DOTTY_DATA_DIR` 只控制 PDF、Markdown、题图和旧 SQLite 备份所在目录。
- 生产环境可保持 API 不变，将文件层替换为 S3/OSS，并把 `DATABASE_URL` 指向托管 PostgreSQL。

## 接口

- `GET /api/question`：返回数字化后的题目、讲解步骤和 Canvas 动作。
- `GET /api/models`：返回本机可用模型、Codex 和 Mock 模式。
- `POST /api/models/select`：切换当前模型 provider/model。
- `GET /api/ocr`：返回 MinerU、PDF 文字层解析器及当前实际解析方式。
- `POST /api/ocr/select`：切换 `auto`、`mineru` 或 `pypdf`。
- `POST /api/textbook/import`：上传一页常见图片、HEIC 或 PDF，返回结构化结果。
- `POST /api/uploads/init`：创建最大 500 MB 的 PDF 分块上传任务。
- `PUT /api/uploads/:id/chunks/:index`：幂等上传一个 5 MB 分块。
- `POST /api/uploads/:id/complete`：合并、校验、按每 5 页规划批次，并生成和解析首批内容。
- `GET /api/uploads/:id/status`：查询上传与处理阶段、进度、耗时和结果，供断点续传与界面轮询。
- `POST /api/uploads/:id/batches/:batchId/process`：按需 OCR 后续 5 页批次，生成下一道可切换题目。
- `POST /api/help`：接收 `questionId`、学生输入、提示层级以及 `answer/help` 模式，返回判题或下一步提示。
- `GET /api/tts/status`：返回 Azure、Qwen3-TTS 或浏览器回退状态。
- `POST /api/tts`：调用 Azure Speech 或代理本地 Qwen3-TTS，返回音频。
- `GET /api/uploads/:id/assets/:batchId/:filename`：读取持久化题图。
- `GET /api/uploads/:id/artifacts/:batchId/:filename`：读取 OCR Markdown 或结构化模型提示词。
- `GET /api/library`：列出已经持久化的教材。
- `GET /api/library/:uploadId`：恢复教材结果及已经生成的全部题目。
- `GET /api/health`：健康检查。

PDF 会在浏览器上传前和后端合并后分别检查 `%PDF-` 文件头与 `%%EOF` 结束标记。
如果提示缺少 `%%EOF`，说明浏览器读到的原文件本身已经截断，请重新下载 PDF，或使用
系统的“打印 → 存储为 PDF”重新生成后再上传。

示例：

```bash
curl -X POST http://127.0.0.1:8010/api/help \
  -H 'Content-Type: application/json' \
  -d '{"questionId":"geometry-perpendicular-bisector","studentInput":"我不知道怎么开始","hintLevel":0,"language":"zh"}'
```

## Demo 与生产版本的边界

- 语音：Demo 支持 Azure、Qwen3-TTS 和浏览器三级路径；生产版应增加音频缓存、异步预生成、配额和失败重试。
- 教材识别：安装 MinerU 后会真实生成 Markdown/公式/结构化内容；未安装时，带文字层的
  PDF 使用 pypdf，纯扫描图片需要临时粘贴题目文字。
- 模型：Ollama/Codex 会真实生成结构化脚本并判断学生卡点；JSON Schema、Canvas 动作白名单
  和静态回退用于限制输出边界。
- Canvas：讲解动画目前只有四个白名单动作，画线题使用独立交互组件；生产版可扩展成带 Schema 校验的绘图 DSL。
- 数据：题目、审校结果和引导卡已经进入 PostgreSQL，但用户、班级、标准答案版本和学习记录尚未建模；生产版仍需把文件资源迁移到对象存储。
- 任务：长时间 OCR 和模型调用仍在 API 请求内同步执行；生产版需要任务队列、worker、幂等任务状态和并发限流。

## 部署方法

### 部署方式和当前边界

GitHub 只负责保存源代码和运行 CI，不会直接运行本项目的 FastAPI、PostgreSQL、MinerU
或 Qwen3-TTS。推荐把前端部署为静态站点，把后端部署到一台 Linux 主机或容器平台，数据库
使用托管 PostgreSQL；生产环境的文件则放到 S3、OSS 或 Azure Blob 等对象存储。

当前仓库还没有 Dockerfile、Alembic 和任务队列配置，因此下面的步骤适用于“单机、单进程、
受控用户”的测试部署。它可以用于内测，但不等同于公网生产方案。当前实现包含内存缓存，
所以暂时使用一个 Uvicorn worker；在状态迁移到 PostgreSQL/Redis、长任务改为 worker 之后，
再进行多 worker 或多实例扩容。

推荐的生产拓扑如下：

```text
浏览器
  → HTTPS / Nginx 或云负载均衡
  → 前端静态文件 + /api 反向代理
  → FastAPI（单进程 API）
       ├─ 托管 PostgreSQL
       ├─ 对象存储（PDF、Markdown、题图、音频）
       ├─ Redis + OCR/出题 worker（生产版）
       └─ Ollama / Qwen3-TTS / Azure Speech 等独立模型服务
```

### 1. 准备服务器和域名

以下示例以 Ubuntu 22.04/24.04、Python 3.12、Node.js 20+、Nginx 和 PostgreSQL 为例。
路径 `/opt/dotty-tutor`、Linux 用户 `dotty` 和域名 `tutor.example.com` 都可以替换成实际值。

```bash
sudo apt update
sudo apt install -y git nginx postgresql postgresql-client rsync python3.12 python3.12-venv
sudo adduser --system --group --home /opt/dotty-tutor dotty
sudo mkdir -p /opt/dotty-tutor /srv/dotty-tutor/data
sudo chown -R dotty:dotty /opt/dotty-tutor /srv/dotty-tutor
sudo -u dotty git clone <你的 GitHub 仓库地址> /opt/dotty-tutor
```

如果 PostgreSQL 不在本机，请在云数据库控制台创建数据库和低权限应用账号，并开启 SSL。
如果 PostgreSQL 在本机，可以使用管理员账号执行：

```bash
sudo -u postgres createuser --pwprompt dotty_app
sudo -u postgres createdb -O dotty_app dotty_tutor
```

数据库密码如果包含 `@`、`:`、`/` 等特殊字符，需要先进行 URL 编码再放入
`DATABASE_URL`。不要把真实连接串提交到 GitHub。

### 2. 安装后端并配置环境变量

```bash
sudo -u dotty python3.12 -m venv /opt/dotty-tutor/.venv
sudo -u dotty /opt/dotty-tutor/.venv/bin/pip install --upgrade pip
sudo -u dotty /opt/dotty-tutor/.venv/bin/pip install -r /opt/dotty-tutor/backend/requirements.txt
sudo install -d -o dotty -g dotty /etc/dotty-tutor
sudo touch /etc/dotty-tutor/backend.env
sudo chown dotty:dotty /etc/dotty-tutor/backend.env
sudo chmod 600 /etc/dotty-tutor/backend.env
```

`/etc/dotty-tutor/backend.env` 至少包含：

```dotenv
DATABASE_URL=postgresql+psycopg://dotty_app:替换为真实密码@db.example.com:5432/dotty_tutor?sslmode=require
DOTTY_DATA_DIR=/srv/dotty-tutor/data
CORS_ORIGINS=https://tutor.example.com
TRUSTED_HOSTS=tutor.example.com

# 生成模型：ollama、codex 或 mock
MODEL_PROVIDER=ollama
MODEL_NAME=qwen2.5:3b
OLLAMA_BASE_URL=http://127.0.0.1:11434

# OCR：auto、mineru 或 pypdf；没有 MinerU 时使用 pypdf
MINERU_COMMAND=/opt/dotty-tutor/.mineru-venv/bin/mineru

# 审校模型，可先使用 mock 或已有模型
REVIEW_PROVIDER=ollama
REVIEW_MODEL=qwen2.5:7b
VISION_PROVIDER=codex
VISION_MODEL=default

# TTS：auto、azure 或 qwen
TTS_PROVIDER=azure
AZURE_SPEECH_KEY=只放在服务器环境变量中
AZURE_SPEECH_REGION=eastasia
AZURE_SPEECH_VOICE=zh-CN-XiaoxiaoNeural
QWEN_TTS_URL=http://127.0.0.1:8020
```

配置建议：

- `DATABASE_URL`、Azure key、模型平台 token 只能放在服务器密钥管理或部署平台 Secrets 中。
- `DOTTY_DATA_DIR` 必须是持久化磁盘；容器重建后不能丢失 PDF 和题图。
- `CORS_ORIGINS` 使用逗号分隔的完整来源地址；`TRUSTED_HOSTS` 使用逗号分隔的域名。生产环境不要保留任意来源或通配符。
- 生产环境不使用 `MODEL_PROVIDER=mock`，除非明确是在做演示或健康检查。
- 当前 `backend/storage.py` 会在首次连接时使用 `create_all()` 创建表。正式上线前应引入
  Alembic；在没有迁移工具之前，不要把数据库结构变更直接混入多实例发布流程。

### 3. 先做一次后端检查

```bash
sudo -u dotty bash -lc '
  cd /opt/dotty-tutor/backend
  set -a
  . /etc/dotty-tutor/backend.env
  set +a
  ../.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8010
'
```

另开终端检查：

```bash
curl -fsS http://127.0.0.1:8010/api/health
curl -fsS http://127.0.0.1:8010/api/models
curl -fsS http://127.0.0.1:8010/api/ocr
curl -fsS http://127.0.0.1:8010/api/tts/status
```

确认健康接口正常后按 `Ctrl-C` 停止临时进程，再交给 systemd 管理。若使用云数据库，
优先确认 SSL、白名单和数据库账号权限；不要让 PostgreSQL 直接暴露到公网。

### 4. 使用 systemd 运行 FastAPI

创建 `/etc/systemd/system/dotty-tutor-api.service`：

```ini
[Unit]
Description=Dotty Tutor FastAPI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=dotty
Group=dotty
WorkingDirectory=/opt/dotty-tutor/backend
EnvironmentFile=/etc/dotty-tutor/backend.env
ExecStart=/opt/dotty-tutor/.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8010 --workers 1
Restart=on-failure
RestartSec=5
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

启用服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now dotty-tutor-api
sudo systemctl status dotty-tutor-api
sudo journalctl -u dotty-tutor-api -f
```

当前 PDF 完成、OCR 和模型调用仍可能占用数分钟，因此不要添加 `--reload`。Nginx 的
`proxy_read_timeout` 也需要覆盖最长请求；完成任务队列改造后再把超时改短。

### 5. 构建并发布前端

当前 Vite 开发服务器只负责本地 `/api` 代理；生产环境可以让前端和 API 使用同一个域名，
由 Nginx 把 `/api/` 转发到 `127.0.0.1:8010`，这样无需改动现有相对路径。

```bash
sudo -u dotty bash -lc '
  cd /opt/dotty-tutor/frontend
  npm ci
  npm run build
'
sudo mkdir -p /var/www/dotty-tutor
sudo rsync -a --delete /opt/dotty-tutor/frontend/dist/ /var/www/dotty-tutor/
sudo chown -R www-data:www-data /var/www/dotty-tutor
```

创建 `/etc/nginx/sites-available/dotty-tutor`：

```nginx
server {
    listen 80;
    server_name tutor.example.com;

    root /var/www/dotty-tutor;
    index index.html;
    client_max_body_size 550m;

    location /api/ {
        proxy_pass http://127.0.0.1:8010;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 900s;
        proxy_send_timeout 900s;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

启用并检查配置：

```bash
sudo ln -s /etc/nginx/sites-available/dotty-tutor /etc/nginx/sites-enabled/dotty-tutor
sudo nginx -t
sudo systemctl reload nginx
```

然后使用 Certbot 或云负载均衡为域名配置 HTTPS。证书终止在 Nginx/负载均衡时，仍要把
`X-Forwarded-Proto` 传给后端，并限制防火墙只开放 80/443；8010、11434、8020 和 PostgreSQL
端口不应直接暴露给公网。

### 6. 可选模型和语音服务

Ollama、MinerU 和 Qwen3-TTS 都是独立运行时，不需要和前端静态文件放在同一个目录。

```bash
# Ollama 示例；模型大小和显存要求取决于所选模型
ollama serve
ollama pull qwen2.5:3b
```

Qwen3-TTS 建议放在带 GPU 的单独主机或容器，后端通过 `QWEN_TTS_URL` 调用；没有 GPU 时，
优先使用 Azure Speech 或浏览器语音回退。若通过内网访问 Qwen 服务，应设置鉴权或网络策略，
不要把 `:8020/tts` 直接暴露到公网。

### 7. 发布前验收

每次发布至少执行以下检查：

```bash
cd /opt/dotty-tutor/frontend
npm ci
npm run build

curl -fsS https://tutor.example.com/api/health
```

浏览器手工验证：登录/访问控制（如果已接入）、上传一个小 PDF、暂停后续传、生成首题、
打开题图和 OCR 产物、完成选择/判断/画线交互、点击 Help、播放 TTS，并确认失败时能看到
可理解的错误提示。带 `needs_review` 的题目在生产版必须不能直接发布给学生。

### 8. 备份、更新和回滚

数据库和文件目录必须同时备份；只备份 PostgreSQL 会丢失 PDF、Markdown 和题图。

```bash
# 示例：导出数据库（请把密码放在临时的受保护环境中）
pg_dump "$DATABASE_URL" --format=custom --file=/srv/backup/dotty_tutor-$(date +%F).dump

# 示例：备份文件资源
tar -czf /srv/backup/dotty-data-$(date +%F).tar.gz /srv/dotty-tutor/data
```

建议在真正上线前做一次恢复演练。更新时先备份，再拉取代码、安装依赖、重新构建前端，
最后重启 API；如果将来加入 Alembic，必须先执行 `alembic upgrade head`，确认成功后再切流量。

```bash
sudo -u dotty git -C /opt/dotty-tutor pull --ff-only
sudo -u dotty /opt/dotty-tutor/.venv/bin/pip install -r /opt/dotty-tutor/backend/requirements.txt
sudo -u dotty bash -lc 'cd /opt/dotty-tutor/frontend && npm ci && npm run build'
sudo rsync -a --delete /opt/dotty-tutor/frontend/dist/ /var/www/dotty-tutor/
sudo systemctl restart dotty-tutor-api
sudo systemctl reload nginx
```

### 9. 上公网前必须补齐的能力

单机步骤完成后，还需要补齐用户鉴权、上传配额和病毒扫描、对象存储、Redis 任务队列、
Alembic 迁移、结构化日志、错误监控、数据库自动备份，以及 GitHub Actions 的测试/构建/密钥扫描。
在这些能力完成前，建议只开放给可信用户，不要把当前的匿名 API 直接暴露到公网。

### 10. GitHub 开发与 CI

仓库包含 `.github/workflows/ci.yml`，每次向 `main` 推送或提交 Pull Request 时会自动执行：

- Python 3.12 后端依赖安装和 29 项后端测试；
- Node.js 20 前端依赖安装和 `npm run build`；
- `Dockerfile.backend` 后端 API 镜像构建。

本地开发建议使用功能分支：

```bash
git switch -c feat/your-change
# 修改、测试、提交
git add .
git commit -m "feat: describe the change"
git push -u origin feat/your-change
```

GitHub 上通过 Pull Request 合并到 `main`，不要直接把生产密钥、`.venv/`、模型权重、
`data/` 或前端 `dist/` 提交到仓库。需要部署到服务器时，先让 CI 通过，再在服务器执行更新
流程；后续可以增加 GitHub Actions Secrets 和部署环境，将构建产物发布到云主机或容器平台。
