# 变更记录

本文件记录项目的功能、接口、模型、交互和修复变更。后续每次修改代码、配置或用户可见行为时，都应在 `Unreleased` 下追加一条记录；发布或交付时再将条目归档到对应日期或版本。

## [Unreleased]

### Added

- 新增 `CHANGELOG.md`，并在 README 顶部增加变更记录入口。
- 新增 SQLAlchemy/psycopg PostgreSQL 存储层和 `DATABASE_URL` 配置。
- 新增 `backend/migrate_sqlite_to_postgres.py`，支持幂等迁移旧 SQLite 数据。
- 健康检查增加当前数据库后端字段。

### Fixed

- 修复选择题选项被模型合并成一行的问题：当模型返回 `(A) ... (B) ... (C) ... (D) ...` 时，后端会在结构质量校验前自动拆分为四个独立选项。
- 增加对应的回归测试，避免 A-D 选项再次被渲染成单个不可正确交互的选项。

### Changed

- 重新启动本地后端服务，使选项解析修复立即生效。
- 评审当前项目架构和真实调用链，并在 README 中补充组件图、职责边界、单页/PDF 导入、生成审校、Help、TTS、持久化、回退逻辑及架构限制。
- 本地和生产默认数据库从 SQLite 切换为 PostgreSQL `dotty_tutor`，结构化 payload、审校结果和引导卡使用 JSONB。
- 已将本机 SQLite 中 6 个上传任务和 68 道题迁移至 PostgreSQL；旧 SQLite 文件保留为备份。
- 在 README 中补充单机测试部署和后续生产部署方法，包括 PostgreSQL、环境变量、systemd、Nginx、HTTPS、前端构建、模型服务、健康检查、备份恢复和上线前验收。
- 增加 `.env.example`、生产环境 `.gitignore` 规则、后端 API Dockerfile 和 GitHub Actions CI（后端测试、前端构建、容器构建）。
- CORS 改为通过 `CORS_ORIGINS` 配置，支持可选 `TRUSTED_HOSTS`；API 增加安全响应头、请求 ID 和真实数据库连通性健康检查。
- 将 README 项目描述更新为“AI 教材数字化与互动辅导平台”，保留产品名称 `Dotty Tutor`。
- 将 FastAPI 标题、前端包名和页面副标题统一为 `Dotty Tutor`，仅保留 `MVP` 作为当前阶段标识。

## [2026-08-01]

### Added

- 增加选择题、判断题、简答题和画线题的统一题目类型字段及交互支持。
- 增加 Qwen3-TTS 本地语音服务，默认使用中文 `Serena` 音色；服务不可用时回退到浏览器语音。
- 增加 Azure Speech Neural 可选 TTS provider，支持通过环境变量配置密钥、区域和音色。
- 增加 TTS 状态接口，前端可以显示当前使用的语音 provider 及回退状态。
- 增加题目来源、OCR/视觉审校记录和结构质量门禁，发现题干、图片、选项数量不一致时标记人工复核。

### Changed

- 前端朗读功能改为优先调用后端 `/api/tts`，失败时再使用浏览器 `speechSynthesis`。
- 题目生成流程支持逐题来源绑定，避免跨题复用图片或文本片段。

### Notes

- Qwen3-TTS 模型权重支持断点下载，模型文件保存在本地缓存目录，不提交到代码库。
- Azure 凭据只通过环境变量提供，不写入源码或变更记录。
