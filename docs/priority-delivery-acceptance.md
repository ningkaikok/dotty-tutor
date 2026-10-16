# 优先级执行记录（2026-10-16）

本次从 `991d257` 快进同步到 main `3813715`，在独立工作区完成以下工程与材料验收。
后续按用户授权准备 v0.40.0 发布，发布结果以 GitHub 的 PR、检查与 Release 记录为准。
用户授权寻找教材后，选择以下开放教材制作来源可追溯的私有草稿。
这里的完整单元范围是教材中一个完整小节，不表示整章、整本教材或教师教学验收通过。

## 教材与可检查产物

| 学科 | 教材、小节与物理页 | 许可与来源 |
| --- | --- | --- |
| 英语 | Writing for Success（2015），4.6 Using Context Clues，201–206；书内页码 192–197 | CC BY-NC-SA 4.0，署名按原书保留。[明尼苏达大学目录](https://open.umn.edu/opentextbooks/textbooks/writing-for-success)、[原 PDF](https://open.lib.umn.edu/writingforsuccess/open/download?type=pdf) |
| 数学 | Tyler Wallace，Beginning and Intermediate Algebra（2010），1.1 One-Step Equations，29–33；书内页码 28–32 | CC BY 3.0，保留作者署名。[Saylor 存档 PDF](https://resources.saylor.org/wwwresources/archived/site/wp-content/uploads/2011/12/SAYLOR-MA001-TEXT.pdf) |
| 科学候选 | Wikibooks Cell Biology（2013-12-29），7–10 | CC BY-SA 3.0，图片另依原书逐项许可。[文件与署名页](https://commons.wikimedia.org/wiki/File:Cell_Biology.pdf)；四页文字层均提示疑似粘连，尚未制作课件 |

PDF、页段原始文字、页图、哈希、候选清单、来源复核报告与生成快照保存在
`output/priority-delivery/`，目录受 Git 忽略。没有提交教材原件、本机配置或数据库。
英语 PDF SHA256 为 `334a9f9724a103ef4f764a5e9e3614d39fba075d3868f23b1695df4621f5a915`；
数学为 `2185a6e55b80e80859c1c39e6733acf270b8e4751544ec3b4bba47f5c4f381d8`。

本机工作台为 <http://127.0.0.1:59571/studio>，API 为 `http://127.0.0.1:59570`。
独立 PostgreSQL 仅监听本机 `59369`，不使用原业务数据库。

- [英语草稿](http://127.0.0.1:59571/studio/courseware/3842f813100b4606b712b6fd881b8196)：六页，覆盖上下文线索类型、信号词、协作练习与总结。
- [数学草稿](http://127.0.0.1:59571/studio/courseware/7465d5b9f83246918f5c5a9269d997da)：五页，覆盖代入检验、加减乘除一步方程与选做练习。

生成使用真实模型运行时；最初本机 Ollama 超时，后续使用已登录的 Codex CLI。
本次导入提取的是 PDF 原有文字层，不能作为真实纸张扫描 OCR 的验收证据。
开发代理修订内容仍标为 AI 补充，来源复核、教师审核和发布均保持待完成。

## 按优先级继续的任务

| 顺序 | 任务 | 当前结果与下一步 |
| --- | --- | --- |
| 1 | 同步代码 | 已同步 main；本次修复与版本准备一并进入 v0.40.0 发布 PR |
| 2 | 运行、部署与数据库 | 独立本机 API 和 schema 检查通过，完成数据库备份恢复演练；线上版本仍未核验。部署 PR #302 尚未合并，健康工作流因缺少 SERVICE_HEALTH_URL 实际跳过，不能视为线上健康通过 |
| 3 | 英语完整小节 | 已找到教材并制作六页待审草稿，需教师逐页核对来源与教学内容 |
| 4 | 数学完整小节 | 已制作五页待审草稿，修订明显运算讲解、公式转义和练习排版；需教师复核答案和教学难度 |
| 5 | 教师授课与学生续学 | 现有浏览器流程测试通过；本次真实草稿尚未由教师确认发布，真人完整流程仍待验收 |
| 6 | 真实扫描 OCR | 需实际扫描/拍照材料及独立人工转写；不能用 PDF 文字层代替。沿用现有来源复核与差异报告工具 |
| 7 | 检索与互动质量 | 既有自动化流程通过；真实已发布小节的引用正确性、答案泄露和触控体验待教师发布后检查 |
| 8 | 科学课件 | 已准备四页候选和风险报告，先修正粘连并核对插图许可，再生成与审阅 |
| 9 | 50+ 独立双审金标准 | 诊断显示 50 条为合成候选、有效独立双审为 0，仍缺 50 条；按现有人工规则收集，不能将模型意见记为真人批准 |
| 10 | 真机与媒体 | 缺少实际设备测试、OCR/语音相关服务与语音配置，后续按设备和服务逐项检查 |
| 11 | 教师和学生试用 | 待实际参与者完成；先使用上述私有草稿进行教师审阅 |
| 12 | 根据试用改进 | 本轮已修复实际生成发现的问题；后续按真实试用记录安排 |

人工准备规则见 [评测候选复核说明](../apps/api/evaluation/benchmark/review_queue/HUMAN_REVIEW.md)，
完整产品验收范围见 [产品路线图](product-roadmap.md)。

## 本轮修复与验证

修复单页重做错误读取来源引用、模型误报教师署名，以及 Windows 下 API 类型检查误报换行差异。
英语、数学和科学逐页提示词升级到 v2，要求页面 ID 前缀与真实 AI 署名；数学提示词明确 LaTeX 转义。
两项新增 PostgreSQL 验收用例验证署名归一与明确教师编辑、重做单页保留其他页和来源引用。

实际执行命令与结果如下；详细日志保存在 `output/priority-delivery/logs/`。

| 检查 | 命令与结果 |
| --- | --- |
| 受影响后端 | `uv run python -m tests.postgres_test_runner tests.test_courseware_postgres_acceptance tests.test_prompt_catalog`：18 项通过 |
| 后端完整门禁 | `uv run ruff check .`、`uv run pyright` 通过；`uv run python -m tests.postgres_test_runner` 执行 943 项：941 通过、2 跳过（Windows POSIX 进程清理与符号链接权限）；使用独立 PostgreSQL，未跳过数据库测试 |
| 前端完整门禁 | `pnpm lint`、`pnpm vitest run`（58 文件 / 226 项）、`pnpm check:api`、`pnpm exec tsc --noEmit`、`pnpm run build`、`pnpm run test:e2e --output ../../output/priority-delivery/e2e`（48 项）均通过 |
| 本次真实草稿 | Playwright 逐页打开英语六页、数学五页；所有展示原图均成功加载，无 KaTeX 渲染错误或页面警报。结果为 `browser-draft-check.json`，另保留截图；不等于教学内容审核 |
| 测试纪律与差异 | `python scripts/check_test_discipline.py`：123 文件、0 违规；`git diff --check` 通过 |
| API 漂移负例 | 仅 CRLF 变化通过；临时加入真实类型差异时按预期失败，随后恢复生成文件 |
| 数据库 | 新建独立数据库迁移至 0027，`migration_cli verify` 通过；`pg_dump` / `pg_restore` 后在独立恢复库再次 `uv run python -m persistence.migration_cli verify` 通过；最终备份为 `materials-final.dump` |
| 基准集准备度 | readiness 按预期返回不满足准入；不生成排名、不增加人工计数 |
| Docker | 本机没有 Docker，未运行 Compose 门禁；本次没有 Docker 配置改动 |

页面生成或依赖缺失的历史失败日志予以保留，不用后续通过结果抹去真实失败记录。
预览进程仅供本机本轮检查；重启电脑后可运行忽略目录中的 `start-preview.ps1` 恢复。
