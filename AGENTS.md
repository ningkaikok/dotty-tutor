# Dotty Tutor 开发代理规范

本文档适用于在本仓库中工作的 Codex、其他 AI 编程代理和自动化开发工具。

分工：**代理每次都要照做的规则和命令写在本文件里**，人类协作流程（Issue、PR 描述、评审、
许可证、发布准备）写在 [`CONTRIBUTING.md`](CONTRIBUTING.md)。同一条规则只维护一份——
两份副本曾各自漂移，导致 CONTRIBUTING 里的验证命令引用了早已不存在的目录。

## 基本要求

- 使用中文沟通和编写用户可见文档，代码、API 名称和提交信息使用英文。
- 修改前先检查相关代码、测试、文档和当前 Git 状态。
- 不提交密钥、`.env`、教材原文件、学生数据、模型权重、虚拟环境或构建产物。
- 完成代码、配置或用户可见行为修改后，运行相关测试并更新文档。

## 架构与复用

本项目按工业界全栈工程结构组织（pnpm monorepo：`apps/web` + `apps/api`），目标是让一名
维护者能够快速理解、修改和演示；采用企业级实践（类型检查、测试金字塔、可复现锁定），
但每个实践必须保持单一维护者可理解的复杂度——不为结构而结构。

- `apps/api/app.py` 只负责创建应用和装配路由，禁止加入业务流程。
- 后端按“路由/协议 → 领域编排 → Runtime/Store”组织；跨域共享契约和适配器，不共享页面状态。
- 前端页面只负责组合；网络和状态机进入 Hook，重复视觉结构进入组件，纯校验进入工具函数。
- React 页面超过约 250 行、后端模块超过约 500 行时应检查是否职责混杂；行数是审查信号，不是机械拆分指标。
- 优先使用已有依赖和成熟开源实现。新增依赖前检查维护状态、许可证、体积、运行环境和是否真的减少自维护代码。
- 路由、公式渲染、数据库访问、PDF 解析、OCR、浏览器测试不得重复实现已有基础设施。
- 不为单一调用创建 Repository、Manager、Factory 等空包装；只有出现第二个实现或明确测试替身时再抽象接口。

### React 测试分流规则

- 纯函数、数据归一化、视图模型转换和状态机边界优先使用 Vitest；这类测试不得为了
  跑浏览器而放进 E2E。
- React 组件的 DOM 行为使用 Vitest + React Testing Library；只有在需要真实路由、多个
  页面协作或浏览器布局时才提升到 Playwright。组件测试按文件选择 `jsdom`，纯逻辑测试继续
  使用 `node`，避免全局引入不必要的 DOM 环境。
- Playwright E2E 只保留用户可见的跨组件流程、真实浏览器交互和端到端 API 契约；稳定的关键
  页面可以使用 `toHaveScreenshot` 做少量视觉回归，截图基线必须固定数据、禁用动画并经过人工审查。
- Playwright Planner、Generator、Healer 等 AI 辅助工具不是默认测试运行时；只有确实需要 AI
  辅助编写或修复测试时才临时引入，生成结果必须纳入普通代码审查和 CI 测试。

### 文档同步规则

新增顶层模块 / API 响应字段 / 端点时，**同一 PR 内**必须同步三份描述性文档，roadmap 只记优先级
状态，不承担"系统是什么样"的描述职责：

1. `docs/codebase-guide.md` 的文件树和领域链路图。
2. `docs/architecture.md` 对应组件章节。
3. 涉及接口变更时 `docs/api.md` 端点表。

背景：#147/#148 曾只更新 roadmap，导致架构文档和代码地图落后两个版本才发现。

代码注释解释“为什么、约束和回退策略”，不要逐行翻译语法。公共模块、状态机和有安全边界的函数应有
docstring/JSDoc；显而易见的展示组件不需要堆叠注释。详细边界见
[`docs/codebase-guide.md`](docs/codebase-guide.md)。

## 分支命名与提交信息

前缀清单、Conventional Commits 格式和示例见
[`CONTRIBUTING.md`](CONTRIBUTING.md#分支命名)，此处只列代理需要额外遵守的约束：

- 从最新的 `main` 创建分支，禁止直接在 `main` 上开发。
- 不要使用 `codex/` 作为分支前缀。
- 允许的提交 `type` 只有 `feat`、`fix`、`perf`、`refactor`、`docs`、`test`、`chore`；
  没有把握时不要自造新 type。
- 提交描述使用祈使语气、英文、小写开头，不加句号；一个提交只处理一个清晰主题。
- 未经明确要求，不要 `git commit`、`git push` 或创建分支——把改动留在工作区待审。
- 本仓库常态使用 git worktree，`git stash` 栈是共享的：不要使用裸 `git stash` / `git stash pop`。

## CHANGELOG

只有这三类提交进入 `CHANGELOG.md` 的 `Unreleased` 区域，条目从用户视角描述影响，
不要复制内部实现细节：

| 提交类型 | CHANGELOG 分类 |
| --- | --- |
| `feat` | `Added` |
| `fix` | `Fixed` |
| `perf`、`refactor` | `Changed` |

`docs`、`style`、`chore`、`test` 不写入。整理版本、生成初稿和发布流程见
[`CONTRIBUTING.md`](CONTRIBUTING.md#发布准备)。

## 验证与交付

下面每一条都是 [`.github/workflows/ci.yml`](.github/workflows/ci.yml) 的门禁，任意一条
失败 PR 就会挂。**交付前必须自己真的执行，不要凭印象报告通过**——只跑了单元测试就声称
"检查通过"是本仓库反复出现的问题。

后端（在 `apps/api` 下）：

```bash
uv run ruff check .
uv run pyright
uv run python -m unittest discover -s tests -p 'test_*.py'
```

用 `uv run` 而不是直接写 `.venv/bin/...`：venv 的位置随 `uv sync` 的执行目录而变
（仓库根生成 `<root>/.venv`，`apps/api` 下生成 `apps/api/.venv`），而本仓库常态使用
git worktree，硬编码路径会在其中一种布局下失败。`uv run` 自己解析，且与 CI 用法一致。

前端（在 `apps/web` 下）：

```bash
pnpm lint
pnpm vitest run
pnpm check:api      # 生成类型漂移检查；报漂移时跑 pnpm generate:api，禁止手改生成文件
pnpm exec tsc --noEmit
pnpm run build
pnpm run test:e2e   # 改动触及用户可见流程或 DOM 结构时必跑
```

Docker 相关改动追加：

```bash
docker compose config
docker compose up --build --detach
curl -fsS http://127.0.0.1:8080/api/health
docker compose down
```

交付说明中列出实际执行过的命令和结果；跳过了哪一条要写明原因。PR 应保持小而聚焦，
说明问题、实现方法、影响范围和验证结果。
