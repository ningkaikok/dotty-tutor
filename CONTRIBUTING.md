# 参与贡献

感谢你帮助改进 Dotty Tutor。项目欢迎 Bug 修复、文档、测试、无障碍改进、模型适配和教育
交互建议。较大的功能请先创建 Issue，确认方向后再投入实现。

参与项目即表示你同意遵守 [行为准则](CODE_OF_CONDUCT.md)。安全漏洞不要提交公开 Issue，
请按照 [安全策略](SECURITY.md) 私下报告。

## 开始之前

1. 搜索已有 Issue 和 Pull Request，避免重复工作。
2. Bug 报告提供复现步骤、预期行为、实际行为、日志和环境信息。
3. 功能建议说明使用场景、目标用户、替代方案和验收标准。
4. 涉及接口、数据库、题目结构或模型输出的改动，请先在 Issue 中确认兼容方案。

## 开发环境

快速体验可以使用 Docker：

```bash
cp .env.docker.example .env
# 设置 POSTGRES_PASSWORD
docker compose up --build --detach
```

需要热更新或调试时，请按照[本地开发指南](docs/development.md)配置 Python、Node.js 和
PostgreSQL。

## 分支命名

从最新 `main` 创建分支：

```bash
git switch main
git pull --ff-only
git switch -c feature/short-description
```

使用以下前缀：

- `feature/`：新功能。
- `fix/`：缺陷修复。
- `docs/`：仅文档变更。
- `test/`：测试补充。
- `refactor/`：不改变行为的重构。
- `chore/`：依赖、CI 和工程维护。
- `hotfix/`：线上紧急修复。
- `release/`：版本发布准备。

## 提交信息

提交信息遵循 Conventional Commits 风格：

```text
feat: add drawing question validator
fix: preserve option order after OCR review
docs: clarify Docker model configuration
test: cover PostgreSQL job restoration
```

一次提交只处理一个清晰主题。不要提交 `.env`、密钥、教材原文件、学生数据、模型权重、
虚拟环境、`data/` 或构建产物。

## 质量检查

提交 Pull Request 前至少运行：

```bash
.venv/bin/python -m unittest discover -s backend -p 'test_*.py'
cd frontend && npm ci && npm run build
```

Docker 相关改动还需要运行：

```bash
docker compose config
docker compose up --build --detach
curl -fsS http://127.0.0.1:8080/api/health
docker compose down
```

新增或修复行为应附带测试。修改代码、配置、接口或用户可见行为时，应同步更新
[`CHANGELOG.md`](CHANGELOG.md) 和相关文档。

## Pull Request

- 标题说明改动类型和目标。
- 描述问题、实现方法、影响范围和验证结果。
- UI 改动提供截图或短视频。
- 数据库或接口改动说明迁移和兼容策略。
- 保持 PR 小而聚焦，不混入无关格式化或重构。
- 确认 GitHub Actions 全部通过并解决评审对话。

维护者可能要求调整实现、补充测试、拆分 PR，或在产品方向不一致时关闭提案。

## 许可证

除非明确另行声明，提交到本仓库并被接受的贡献将按照
[Apache License 2.0](LICENSE) 授权。
