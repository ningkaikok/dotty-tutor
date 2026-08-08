# 变更记录

本文件记录项目的功能、接口、模型、交互和工程变更。格式参考 Keep a Changelog，版本号遵循
Semantic Versioning。

## [Unreleased]
### Added
- product: 增加教材互动学习与 AI 错题陪练的独立入口，并提供错题闭环的阶段规划
- practice: Show active model next to the regenerate button (#42)
- library: 教材库删除、去重与返回导航 (#39)
### Changed
- tts: Cache narration and sync lesson playback (#35)
- tts: Prefetch narration for every lesson step on load (#37)
### Fixed
- model: Require all lesson schema fields (#32)
- storage: 消除数据库配置的两个静默踩坑 (#40)
- model: Import CANVAS_ACTIONS to fix real-model lesson generation (#41)
- model: Allow overriding the Codex CLI path via CODEX_COMMAND (#43)
- ci: Add TestClient dependency (#47)

## [0.2.0] - 2026-08-02

### Added

- 增加多选题、填空题和数值/公式题的结构化生成与交互作答。
- 增加填空答案、数值容差和多选集合的确定性核对，并保留模型辅导回退。
- 增加后端 JSON 结构化日志、请求 ID、耗时记录和教材/OCR/模型/TTS/数据库关键事件日志。
- 增加基于 `POSTGRES_*` 环境变量自动组装密码连接串的本地配置方式。
- Docker PostgreSQL 默认映射到宿主机 `127.0.0.1:15432`，便于本地安全访问。
- 增加可编程课程内容块和可扩展渲染器，可组合文字、公式、图形、动画、标注、练习和提示。
- 增加学习会话、作答记录和知识点掌握度，学生提交后可看到最新掌握度反馈。

### Changed

- 将题目契约、题目规范化/质量门禁、答案核对和运行时路由从 FastAPI 入口拆分为独立模块。
- 将题型作答渲染和题目展示逻辑拆分为独立前端组件，便于继续增加题型。
- 将 FastAPI 应用初始化、上传状态、辅导检查和学习接口拆分为独立模块，降低单文件修改风险。

## [0.1.0] - 2026-08-01

### Added

- 增加选择题、判断题、简答题和画线题的统一结构与交互。
- 增加 PDF 分块上传、暂停续传、批次 OCR、来源绑定和教材恢复。
- 增加文本/视觉双模型审校、结构质量门禁和题目来源记录。
- 增加 Qwen3-TTS、Azure Speech Neural 和浏览器语音回退。
- 增加 PostgreSQL/JSONB 存储层和旧 SQLite 幂等迁移工具。
- 增加完整 Docker Compose 部署：PostgreSQL、FastAPI、React/Nginx、健康检查和持久化卷。
- 增加后端/前端 Dockerfile、Nginx 同源代理、Docker 环境模板和 `.dockerignore`。
- 增加 GitHub Actions 后端测试、前端构建和 Docker Compose 健康检查。
- 增加 Apache-2.0 许可证、贡献指南、安全策略、行为准则和支持说明。
- 增加 Issue/PR 模板、CODEOWNERS、Dependabot 和私有漏洞报告入口。
- 增加系统架构、本地开发、API、部署、路线图和模型评估文档。

### Fixed

- 修复模型将 `(A) ... (B) ... (C) ... (D) ...` 合并为单个选项的问题，并增加回归测试。
- 修复项目移动后旧持久化绝对路径导致题图无法恢复的问题。

### Changed

- 本地和生产默认数据库从 SQLite 切换为 PostgreSQL `dotty_tutor`。
- CORS 和可信 Host 改为环境变量配置，API 增加安全响应头、请求 ID 和数据库健康检查。
- README 从内部交付式长文档重构为开源项目首页，详细内容拆分到 `docs/`。
- FastAPI 标题、前端包名和页面副标题统一为 `Dotty Tutor`。
- Docker CI 改为构建前后端镜像、启动完整服务并检查 Web/API 健康状态。

### Notes

- Qwen3-TTS 模型权重保存在本地缓存，不提交到仓库。
- Azure、数据库和模型凭据只通过环境变量或密钥管理提供。
- 当前版本是面向本地体验和受控内测的 MVP，公网部署限制见 `docs/roadmap.md`。

[Unreleased]: https://github.com/ningkaikok/dotty-tutor/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/ningkaikok/dotty-tutor/releases/tag/v0.2.0
[0.1.0]: https://github.com/ningkaikok/dotty-tutor/releases/tag/v0.1.0
