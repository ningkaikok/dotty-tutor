# Dotty Tutor 开发代理规范

本文档适用于在本仓库中工作的 Codex、其他 AI 编程代理和自动化开发工具。
与 GitHub 协作相关的通用要求同时参见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

## 基本要求

- 使用中文沟通和编写用户可见文档，代码、API 名称和提交信息使用英文。
- 修改前先检查相关代码、测试、文档和当前 Git 状态。
- 不提交密钥、`.env`、教材原文件、学生数据、模型权重、虚拟环境或构建产物。
- 完成代码、配置或用户可见行为修改后，运行相关测试并更新文档。

## 分支命名

从最新的 `main` 创建分支，禁止直接在 `main` 上开发：

```bash
git switch main
git pull --ff-only
git switch -c feature/short-description
```

分支名称使用小写英文和连字符，并采用以下前缀：

- `feature/`：新功能。
- `fix/`：缺陷修复。
- `docs/`：仅文档变更。
- `test/`：测试补充或调整。
- `refactor/`：不改变行为的重构。
- `chore/`：依赖、CI 和工程维护。
- `hotfix/`：线上紧急修复。
- `release/`：版本发布准备。

不要使用 `codex/` 作为分支前缀。例如：

```text
feature/question-drawing-interaction
fix/question-structure-validation
docs/deployment-guide
chore/update-dependencies
```

## Git 提交信息

所有提交使用 Conventional Commits：

```text
<type>(<scope>): <description>
```

允许的 `type` 只有：

- `feat`：新增用户可见功能。
- `fix`：修复用户可见缺陷。
- `perf`：性能改进。
- `refactor`：不改变行为的代码重构。
- `docs`：文档变更。
- `test`：测试变更。
- `chore`：工程、依赖或 CI 维护。

示例：

```text
feat(auth): add login page
fix(api): handle timeout error
perf(rag): reduce vector search latency
docs(deploy): clarify Docker setup
```

每个提交只处理一个清晰主题。提交描述使用祈使语气、英文、小写开头，避免在描述中加入句号。

## CHANGELOG 维护

`CHANGELOG.md` 遵循 [Keep a Changelog](https://keepachangelog.com/) 格式，条目从用户视角描述影响，
不要直接复制内部实现细节。

只将以下提交类型写入变更日志：

| 提交类型 | CHANGELOG 分类 |
| --- | --- |
| `feat` | `Added` |
| `fix` | `Fixed` |
| `perf`、`refactor` | `Changed` |

以下类型不写入 CHANGELOG：`docs`、`style`、`chore`、`test`。

发布版本或完成一组用户可见改动时：

1. 从提交记录整理对应版本的 `Added`、`Changed`、`Fixed`。
2. 使用用户能理解的描述，必要时合并重复条目。
3. 将版本、发布日期和条目写入 `CHANGELOG.md`，保留 `Unreleased` 区域。
4. 在 PR 描述中说明 CHANGELOG 是否已更新。

只要提交信息遵循上述格式，就可以使用 `git-cliff`、Release Please 或类似工具自动生成初稿；
自动生成后仍需人工检查措辞、重复项和对用户的实际影响。

## 验证与交付

提交 PR 前至少运行：

```bash
.venv/bin/python -m unittest discover -s backend -p 'test_*.py'
cd frontend && npm ci && npm run build
```

Docker 相关改动还应运行：

```bash
docker compose config
docker compose up --build --detach
curl -fsS http://127.0.0.1:8080/api/health
docker compose down
```

PR 应保持小而聚焦，说明问题、实现方法、影响范围和验证结果，并确认 GitHub Actions 全部通过。
