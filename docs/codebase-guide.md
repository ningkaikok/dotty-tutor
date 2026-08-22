# 代码结构、复用决策与扩展指南

本文面向第一次阅读或继续维护 Dotty Tutor 的开发者，回答四个问题：代码放在哪里、一次请求如何流动、
哪些能力直接复用开源实现，以及新增功能时应在哪个边界修改。

## 设计目标

Dotty Tutor 是个人技术 Demo，不追求微服务数量或企业框架完整度。架构选择按以下顺序判断：

1. 演示路径是否稳定、可解释。
2. 一名维护者能否在十分钟内找到相关代码。
3. 相同能力是否已有成熟依赖或仓库内实现。
4. 模型、OCR、数据库或浏览器能否被 Mock 后独立测试。
5. 只有真实出现第二种实现时，才增加新的抽象层。

因此，本项目采用模块化单体：一个 React 前端、一个 FastAPI 后端、一个 PostgreSQL 数据库，
可选模型/OCR/TTS 通过适配器连接。内容生产、学生学习和错题陪练共享基础设施，但保持页面职责与业务
数据边界清晰。

## 顶层目录

```text
dotty-tutor/
├── backend/                    # FastAPI、领域编排、适配器和测试
│   ├── app.py                  # ASGI 组合根；只装配，不写业务逻辑
│   ├── app_factory.py          # 中间件、安全头、CORS、请求日志
│   ├── api/routers/             # HTTP 协议边界；按产品域拆分 APIRouter
│   │   ├── textbook_routes.py  # 教材 HTTP、分块接收和文件响应
│   │   ├── tutoring_routes.py  # 错题陪练线程 API
│   │   └── ...                 # 学习、发布、运行时和错题路由
│   ├── application/services/   # 可由 HTTP 或 Worker 调用的业务编排
│   │   ├── textbook_processing.py # PDF 合并、OCR、生成和批次编排
│   │   ├── question_processing.py  # 批次生成、审校和质量门禁
│   │   └── stateful_tutor.py      # 有状态陪练编排
│   ├── textbook_ocr_pipeline.py # 页面级 OCR 路由、局部升级和缓存编排
│   ├── ocr_pipeline.py          # 页面探测、路由和内容寻址缓存纯函数
│   ├── ocr_quality.py           # 页面/题块质量门禁和有限重试策略
│   ├── textbook_ocr.py         # 手工文本/MinerU/pypdf 的回退策略
│   ├── domain/contracts/       # 跨业务域的稳定请求/响应契约
│   ├── domain/questions/       # 题目来源、规范化、Schema 和质量纯函数
│   ├── domain/tutoring/        # 判题、陪练策略和状态机纯函数
│   ├── mistake_recognition.py  # 复用教材流水线的错题识别适配
│   ├── variation_service.py    # 错题变式验证题生成与确定性题型门禁
│   ├── answer_evaluator.py     # 选择/多选/填空/数值的确定性答案判定
│   ├── publication_revision.py # 不可变试卷新版编排
│   ├── run_audit.py            # 运行快照与题目修订审计
│   ├── worker.py               # 独立后台 Worker 入口（PostgreSQL Job Store）
│   ├── infrastructure/runtime/ # 模型、OCR、审校和 TTS Provider 适配器
│   ├── infrastructure/files/   # 上传注册和文件边界
│   ├── persistence/            # 数据库基础设施和按领域拆分的 Store
│   │   ├── base.py             # 引擎、初始化、健康检查和通用 Upsert
│   │   ├── textbook_store.py   # 教材导入、题目批次和教材库
│   │   ├── learning_store.py   # 课程、学习会话、作答和掌握度
│   │   └── schema.py           # 教材/学习 schema；其他领域表声明在各自 Store
├── frontend/src/
│   ├── App.tsx                 # React Router 顶层路由和懒加载
│   ├── apps/home/              # 角色入口选择
│   ├── apps/student/           # 学生学习空间，不包含生产配置
│   │   ├── PaperLearningProgress.tsx # 互动试卷掌握证据展示
│   │   └── usePublishedLearningSession.ts # 会话恢复、离线队列和掌握度投影
│   ├── apps/textbook/          # 内容生产、互动预览与发布子模块
│   │   └── import/             # 导入状态机、校验和展示组件
│   ├── apps/mistake/           # 错题本、录入、裁切和确认
│   ├── components/             # 跨教材题型复用的作答组件
│   ├── lesson/                 # 课程文档和内容块渲染器
│   ├── api/                    # 按教材、错题、辅导和运行时拆分的 API
│   ├── types/                  # 按领域拆分的稳定类型
├── frontend/e2e/               # Playwright 用户路径
├── docs/                       # 面向维护者和使用者的文档
└── compose.yaml                # 可重复演示环境
```

API 和类型按领域分别位于 `api/` 与 `types/` 目录。规范代码必须进入
`api/routers`、`application/services`、`domain`、`infrastructure` 或 `persistence`；跨领域组合只在明确的应用入口完成。

### P0～P3 后端分层边界

本轮已完成一次可回滚的模块化单体分层：

| 优先级 | 范围 | 验收标准 |
| --- | --- | --- |
| P0 | `api/routers`、`application/services`、`domain/*`、`infrastructure/*`、`persistence/` 包边界 | `app.py` 只装配；规范新代码不依赖旧根路径 |
| P1 | 路由与协议层 | 路由只做 HTTP 校验、依赖注入和响应映射，长流程委托 Service |
| P2 | 应用服务与领域规则 | 题目、陪练、教材处理可脱离 FastAPI 复用和单元测试 |
| P3 | Runtime、文件和 Store 基础设施 | 外部 Provider、文件系统和数据库边界可替换，当前入口清晰可测试 |

后端根目录仅保留真实的 ASGI、Worker、OCR 编排和领域服务入口；模块导入必须使用规范包路径。

如果希望按完整用户路径学习前端状态归属、可恢复上传、题型复用和 TTS 竞态处理，参见
[前端架构学习指南](frontend-learning-guide.md)。

## 后端依赖方向

```mermaid
flowchart LR
  App["app.py 组合根"] --> Routes["APIRouter 路由"]
  Routes --> Services["application/services"]
  Services --> Domain["domain 规则与契约"]
  Routes --> Stores["persistence Store"]
  Services --> Runtime["infrastructure/runtime"]
  Services --> Contracts["domain/contracts"]
  Stores --> PostgreSQL[(PostgreSQL)]
  Runtime --> External["MinerU / Ollama / Codex / Azure / Qwen TTS"]
```

依赖只能向右。Runtime、Store 和领域函数不得导入 `app.py`，否则会产生循环依赖并让单元测试必须启动
整个 Web 应用。

### 本机与 Docker 的 Runtime 边界

运行时下拉框展示的是后端进程的能力，不是浏览器本身的能力。开发脚本会优先探测仓库根目录
`.mineru-venv/bin/mineru`；因此本机后端（`8010`）能选择 MinerU 时，浏览器应使用
`scripts/dev-local.sh` 启动的 API。Docker API 运行在 Linux 容器中，不能执行宿主机 macOS
虚拟环境，也不会自动继承宿主机安装的模型。容器没有 Linux MinerU 或独立 OCR 服务时，
`/api/ocr` 必须把 MinerU 标记为不可用，前端保留“自动选择”和“PDF 文字层”，避免选择后静默
回退导致用户误以为扫描图已经被识别。若要在 Docker 中启用 MinerU，应新增 Linux OCR 镜像或
独立服务，并在 Runtime 适配器中显式注册其健康检查、版本和资源边界。

### `app.py` 为什么保持很小

`app.py` 是 Uvicorn 的固定入口。它负责：

- 调用 `create_app()`。
- 注册 Runtime、学习、教材和错题路由。
- 将同一个数据库引擎及 OCR/生成函数注入错题域。
- 为旧测试和脚本保留少量导出符号。

上传、模型调用、SQL 或响应拼装都不应重新写入该文件。

### 教材链路

```text
api/routers/textbook_routes.py（HTTP、上传状态）
  → application/services/textbook_processing.py（PDF 合并、首批/后续批次编排）
  → textbook_ocr_pipeline.py（页面探测 → pypdf/MinerU → 局部升级 → 缓存）
  → ocr_pipeline.py / ocr_quality.py（无副作用路由与质量决策）
  → domain/questions/source.py（按题号切分 Markdown）
  → application/services/question_processing.py（生成、审校、确定性修复和质量门禁）
  → persistence/textbook_store.py（题目和上传任务）
  → persistence/learning_store.py（生成后的课程文档）
```

`TextbookProcessingService` 和 `question_processing.py` 已把长流程与 APIRouter 分离。Route 只创建
`background_jobs` 并返回 `202`；`application/textbook_jobs.py` 从 payload 调用 `complete_upload()` 或
`process_batch()`。Worker 不复制 OCR、生成、审校和持久化逻辑，也不应按每个 HTTP 端点创建一个类。

`persistence/job_store.py` 只管理任务生命周期、幂等、租约和错误，不管理教材批次；教材领域进度仍由
`upload_registry.py` 与教材 Store 负责。保持这两层分离可以避免一次任务重试篡改教材当前视图。

### 错题链路

```text
api/routers/mistake_routes.py
  → mistake_recognition.py（复用 OCR 与课程生成）
  → persistence/mistake_store.py（独立 mistake_items 表）
  → api/routers/tutoring_routes.py（线程 HTTP 边界）
  → application/services/stateful_tutor.py（判题、提示和状态转换）
  → persistence/tutoring_store.py（线程与消息）
```

错题域不复制 OCR 或题目生成代码。`mistake_recognition.py` 通过函数注入复用教材能力，因此测试时能
直接替换为确定性识别器，也避免导入 ASGI 应用。

## 前端依赖方向

```mermaid
flowchart LR
  Router["App.tsx / React Router"] --> Page["apps/* 页面编排"]
  Page --> Hook["状态机 Hook"]
  Page --> UI["展示组件"]
  Hook --> API["api/"]
  UI --> Shared["共享题型、公式和课程组件"]
  API --> Backend["/api"]
```

教材导入是参考实现：

- `TextbookImport.tsx` 只负责页面组合。
- `useTextbookImport.ts` 负责多文件队列、每项断点上传、独立轮询、模型切换和错误状态；页面只负责组合列表与右侧结果。
- `fileValidation.ts` 只包含纯校验和常量。
- `RuntimeSettings`、`TextbookLibrary`、`UploadPanel`、`PipelinePanel` 各自负责一个视觉区域。

互动试卷沿用相同分层：`TextbookApp.tsx` 组合内容预览，`usePaperPublication.ts` 负责显式发布状态流；
`PublishedPaperApp.tsx` 组合学生作答，`usePublishedLearningSession.ts` 负责刷新恢复、数据库重建后的旧会话
替换、离线批量补传和掌握度投影；`PaperLearningProgress.tsx` 只展示确定性学习证据。
内容生产端使用 `PracticeWorkspace` 展示重新生成、审核和诊断信息，学生端使用
`StudentQuestionWorkspace` 表达作答、求助和反馈；两者只复用无副作用的 `QuestionAnswer` 与课程渲染器。
这种边界避免为了复用视觉外壳而把作者权限和内部术语带入学生任务流。

学生的非正确作答由 `api/routers/learning_routes.py` 编排写入 `MistakeStore`。稳定错题 ID 使用学生、试卷和题目
共同生成，因此在线提交、离线补传和重复请求都只更新同一条记录；纸质错题仍走 OCR 与人工确认链路。

错题页面新增复杂状态机时也遵循同样边界，不要把 API 请求重新塞回列表或表单组件。

## 开源能力复用清单

| 能力 | 当前复用 | 本项目只负责 | 不应自建 |
| --- | --- | --- | --- |
| SPA 路由 | [React Router](https://reactrouter.com/) | 页面标题、产品路由配置 | `pushState` 监听和动态参数解析 |
| Web API | [FastAPI](https://fastapi.tiangolo.com/)、[Pydantic](https://docs.pydantic.dev/) | 业务契约和依赖装配 | 请求解析、OpenAPI 生成、Schema 校验 |
| 数据访问 | [SQLAlchemy](https://www.sqlalchemy.org/)、[psycopg](https://www.psycopg.org/) | 表结构、查询和领域状态 | 连接池、SQL 转义、PostgreSQL 驱动 |
| PDF 文字层 | [pypdf](https://pypdf.readthedocs.io/) | 页数/字符边界和回退策略 | PDF 文件格式解析器 |
| 扫描 OCR | [MinerU](https://github.com/opendatalab/MinerU) | 子进程适配、产物路径和失败回退 | 版面分析、公式 OCR、图片提取模型 |
| 数学渲染 | [KaTeX](https://katex.org/) | 文本规范化和 React 包装 | LaTeX 排版引擎 |
| 浏览器回归 | [Playwright](https://playwright.dev/) | 关键用户流程和 API Mock | 浏览器控制、Trace、截图录像框架 |
| 语音 | Web Speech、Azure Speech、Qwen3-TTS | Provider 选择、缓存和回退 | 浏览器 TTS 或语音模型训练 |
| 容器编排 | [Docker Compose](https://docs.docker.com/compose/)、[Nginx](https://nginx.org/) | 服务配置和健康检查 | 自定义进程守护与反向代理 |

新增依赖时记录以下信息：官方仓库或文档、许可证、当前维护状态、前端体积或后端运行成本、替换掉的
自维护代码。仅为了少量格式化或一个简单状态值，不引入新依赖。

## 代码拆分判断

出现以下任一信号时检查拆分：

- 页面同时负责 API 请求、状态机和三块以上独立视觉区域。
- 路由函数同时负责协议校验、模型编排、数据库写入和文件操作。
- 同一段解析、错误回退或路径安全逻辑出现第二次。
- 测试必须 Patch ASGI 入口内部变量才能验证纯业务规则。
- 修改一个题型导致无关产品入口一起重新构建或回归。

不要只按行数切文件。拆出的模块必须能用一句话描述责任，并具有较少、稳定的输入输出。

## 注释规范

应写注释的地方：

- Provider 回退顺序及选择理由。
- 文件路径、MIME、大小、缓存等安全边界。
- PDF 分块和恢复状态机的不变量。
- 模型输出被截断、规范化或确定性修复的原因。
- 内存缓存与 PostgreSQL 真相来源的区别。
- 看似多余、但用于处理浏览器或第三方工具边界情况的代码。

不应写注释的地方：

- `setLoading(true)` 上方写“设置加载状态”。
- 为每个 JSX 标签重复中文说明。
- 与代码不一致的阶段计划或历史实现。

Python 公共模块和复杂函数使用 docstring；TypeScript 状态机 Hook、纯校验函数和存在隐含约束的组件
使用 JSDoc。注释变化应和行为变化在同一个提交中完成。

## 常见扩展路径

### 增加一种题型

1. 在 `domain/questions/contracts.py` 和前端 `types/` 扩展稳定契约。
2. 在 `domain/questions/pipeline.py` 添加模型输出规范化和质量检查。
3. 在 `QuestionAnswer.tsx` 或独立题型组件增加输入。
4. 在 `answer_evaluator.py` 添加确定性判题；无法确定性处理时再调用模型。
5. 增加后端单元测试和 Playwright 用户流程。

### 增加一个模型 Provider

1. 在对应 `*_runtime.py` 内增加适配，不修改页面业务组件。
2. 输出现有统一的 provider/model/fallback/error 记录。
3. 配置通过环境变量提供，不把凭据放入 API 或数据库。
4. 为失败、超时和回退增加测试及日志。

### 扩展错题复习任务

阶段四已经提供 `review_tasks`、复习 API、进度页和 1/3/7 天排期。后续扩展时继续遵循同样边界：

1. 在错题域扩展任务契约和表，不复用教材上传任务表。
2. 复用 `QuestionPayload`、确定性判题和掌握度证据，不让模型直接修改状态。
3. 前端在 `apps/mistake/` 下增加页面与 Hook，通过 React Router 注册子路径。
4. 将跨请求的复习状态保存在 PostgreSQL，不依赖进程内字典。

## 测试边界

- 纯函数：直接单元测试，不启动 FastAPI。
- Runtime/Store：使用替身或临时数据库验证边界。
- API：FastAPI `TestClient` 验证协议、状态码和持久化调用。
- 用户路径：Playwright Mock API，验证路由、录入、确认和作答交互。
- Docker：只验证镜像、网络、健康检查和启动配置，不在其中重复所有业务测试。

提交前命令以 `AGENTS.md` 和 `docs/development.md` 为准。

## 30 分钟代码阅读路线

先用 3 分钟阅读 `backend/app.py` 和 `frontend/src/App.tsx`，认识组合根、角色入口与懒加载边界。剩余时间只选
下面一条路径跟踪，避免同时展开所有 import：

| 学习目标 | 建议阅读顺序 | 重点观察 |
| --- | --- | --- |
| PDF 如何变成题目 | `useTextbookImport.ts` → `api/routers/textbook_routes.py` → `application/services/textbook_processing.py` → `application/services/question_processing.py` → `domain/questions/pipeline.py` | 可恢复上传、服务编排、模型输出门禁 |
| 长任务将如何后台化 | `runtime-governance-plan.md` → `infrastructure/files/upload_registry.py` → `persistence/schema.py` → `application/services/textbook_processing.py` | 运行快照、Job Store、租约、幂等与 Worker 边界 |
| 试卷如何安全发布新版 | `usePaperPublication.ts` → `api/routers/publication_routes.py` → `publication_revision.py` → `persistence/learning_store.py` | 显式状态机、不可变版本、事务写入顺序 |
| 学生作答如何离线同步 | `PublishedPaperApp.tsx` → `usePublishedLearningSession.ts` → `api/routers/learning_routes.py` → `persistence/learning_store.py` | 受控组件、幂等 attemptId、掌握度投影 |
| 错题如何多轮陪练 | `useMistakeTutor.ts` → `api/routers/tutoring_routes.py` → `application/services/stateful_tutor.py` → `persistence/tutoring_store.py` | 有限上下文、确定性判题、状态转换权限 |

最后运行对应测试，把一个断言临时改坏再恢复，观察哪条业务约束在保护流程。推荐只跟踪一条请求，不要从最长
文件开始通读整个仓库。

## 已知架构债务

- PDF 完成和批次处理已有独立应用服务，并由 PostgreSQL Job Store 与单 Worker 异步执行；HTTP 只返回
  `202 + jobId`。后续长任务应注册同类 handler，不复制任务领取、续租和重试循环。
- `persistence/app_store.py` 只负责应用需要的教材/学习 Store 组合；错题、陪练和复习仓储继续独立。
- 模型/OCR 的运行时选择是进程级全局状态，不适合多用户公网服务。
- 文件资源仍保存在本地目录，横向扩容前需要对象存储。

这些项目属于生产化边界，不阻塞个人 Demo。产品优先级见[路线图](roadmap.md)，运行快照、事件、后台任务
和离线评测的学习顺序见[AI 运行治理与后台任务演进计划](runtime-governance-plan.md)。
