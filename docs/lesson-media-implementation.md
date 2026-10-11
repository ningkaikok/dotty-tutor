# 教学短视频实现说明

教学短视频从已经人工批准并发布的教程章节生成。系统为每个视频版本保留来源修订、原讲解片段、分镜文字、TTS 提供方和模型、模板版本、渲染参数、视频与 VTT 哈希，以及服务端绑定的教师审校记录。只有通过自动一致性审校、人工批准并单独发布的版本，才会进入学生接口。

## 生成与发布边界

第一版接受数学和英语的 `tutorial` 章节，每个已发布教程必须有 3–8 个带来源引用的片段。每个引用必须解析到同一 `sourceRevisionId`、页码和原文 `sentenceId`；内容不完整、发布版本过期、URL 非 HTTPS 或许可缺失时，不创建视频。分镜快照记录章节发布 ID、源文件哈希、页码、原讲解 Markdown 和来源定位。修订创建新版本，不改写已渲染产物或既有审校记录。

发布范围根据来源许可保守确定。`CC BY 4.0` 且不含 NC/SA 条款的产物可进入学生发布列表；含非商业或相同方式共享条款的产物保留为 `local_only`，不会由学生 API 暴露。每帧显示来源标题、页码、链接、许可和完整归属文字；来源说明不能在布局中容纳时，创建或修订会失败，要求教师缩短内容，而不会截掉归属。OpenStax 归属文字保留 `Access for free at openstax.org.`。

视频状态顺序为：

```text
draft → rendering → consistency_review_required → approved → published
                   ↘ consistency_failed
                         └─ 修订新版本后重新渲染
consistency_review_required → changes_requested → 修订新版本
```

教师批准和发布都由服务端从当前请求身份生成审核者 ID。当前共享教师账户标记为 `identityVerified=false`，不会伪装成独立的第二审核人；Demo 模式统一记录 `demo:unverified`。人工审校绑定精确媒体版本和视频哈希，发布动作还会再次检查来源仍属于当前发布章节，并校验视频和字幕哈希。

渲染由共享 JobStore/Worker 执行：最多 2 次尝试、总时限 600 秒、单个 TTS 片段最多 90 秒和 20 MB、单段旁白最多 2,000 字符、全部旁白合计最多 8,000 字符、视频最多 100 MB。Job 支持取消；片段编辑创建新版本。固定的 1280×720 SVG 画面在 TTS 前先做换行和安全区预检，避免发现超长文字时已经产生模型费用。SVG 只由服务端模板和经过 XML 转义的纯文字生成；不接受上传 SVG、脚本、`foreignObject`、实体或外链资源。

自动一致性检查确认总时长处于 60–120 秒、视频和音频轨道存在、VTT 字幕已生成、来源引用存在。自动检查不是教学质量判断：教师必须在工作台播放完整视频、核对旁白和来源，再批准与发布。WebVTT 是独立侧车；学生播放器用 `<track kind="captions" default>` 显示字幕，并提供 VTT 下载，不把侧车称为内嵌字幕。观看不创建作答记录，也不更新掌握状态。

## 媒体与语音实现

SVG 帧由 CairoSVG 转为 PNG，FFmpeg 将每段静帧做缓慢 Ken Burns 缩放并与 TTS 音频合成，最后拼接为 MP4；字幕由同一分镜旁白生成 WebVTT。所有生成过程使用单条固定工具链，不运行模型生成代码，也不接受模型返回的可执行命令。macOS 选用 Hiragino 中文字体，Linux 容器安装 Noto CJK；数学符号单独使用 STIX Two Math 或 DejaVu Sans 字体段。若有不支持的公式命令、上下标或数学符号，系统明确拒绝该分镜，要求改写成教师可复核的文字，不静默删除符号。

依赖依据与边界：

- [CairoSVG 官方仓库](https://github.com/Kozea/CairoSVG)将其描述为基于 Cairo 的 SVG 转换器，支持 Python 3.10+，使用 LGPL-3.0。项目锁定 CairoSVG 2.9.1；官方 [2.9.0 发布说明](https://github.com/Kozea/CairoSVG/releases)说明递归 `<use>` 渲染上限的安全修复。本功能只渲染自身构造的 SVG，不允许递归引用内容。
- [FFmpeg 官方许可说明](https://ffmpeg.org/legal.html)指出大部分代码是 LGPL，但启用 GPL 部件后 FFmpeg 按 GPL 条款许可。本机验收使用的 FFmpeg 9.0.1 开启了 GPL 与 libx264；Docker 实际构建使用 FFmpeg 7.1.5-0+deb13u1，同样开启 `--enable-gpl --enable-libx264`，渲染命令输出 H.264/AAC MP4，便于常见浏览器播放。镜像保留 `/usr/share/doc/ffmpeg/copyright` 与 `/usr/share/doc/libx264-164/copyright` 包版权信息。此记录描述实际构建配置，不代表应用整体已完成法律审查。
- [Qwen3-TTS 官方仓库](https://github.com/QwenLM/Qwen3-TTS)列出中文和英语支持及 Serena（中文）/Ryan（英语）说话人。媒体复用 `TtsRuntime` 现有 Azure/Qwen 边界；调用会记录 health 返回的真实模型名称和 provider。英语只在服务 health 声明 `supportedLanguages` 含 `English` 时使用 Qwen。运行时由 `QWEN_TTS_URL` 配置；本地 8021 新服务可供英语验收，既有服务保持原样。

锁文件将 CairoSVG 固定为 2.9.1，并锁定其 Python 依赖。Docker 与 CI 安装 `ffmpeg`、`libcairo2`、`fonts-noto-cjk` 和 `fonts-dejavu-core`；依赖预检会单独报告 CairoSVG、FFmpeg、中文字体或数学符号字体缺失。主代理完成的容器实际渲染检查确认 CairoSVG 2.9.1 与容器字体能正确绘制中文及 `− × ÷ ≤ ≥ √`，画面证据记录于 `output/quality-review/evidence/docker-media-frame.png`。若渲染环境未就绪，Job 会失败并给出受控错误，不会返回无配音的静态幻灯片冒充完整视频。

## API 与前端集成

教师接口由 `build_lesson_media_router(service)` 提供，Worker 通过 `build_lesson_media_registry(service)` 注册 `lesson_media.render`：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/lesson-media/drafts` | 从当前已发布教程创建来源分镜快照 |
| `GET` | `/api/lesson-media/lessons/{chapter_id}/{publication_id}/{lesson_id}` | 列出该课的所有版本 |
| `GET` | `/api/lesson-media/{media_id}` | 查询渲染、自动审校、人工审校和发布状态 |
| `POST` | `/api/lesson-media/{media_id}/revisions` | 创建分镜旁白修订的新版本 |
| `POST` / `DELETE` | `/api/lesson-media/{media_id}/render` | 开始或取消有界渲染 Job |
| `GET` | `/api/lesson-media/{media_id}/asset` | 教师审片视频 |
| `GET` | `/api/lesson-media/{media_id}/captions` | 教师审片 VTT |
| `GET` | `/api/lesson-media/{media_id}/captions/download` | 下载教师审片 VTT |
| `POST` | `/api/lesson-media/{media_id}/review` | 批准或要求修改 |
| `POST` | `/api/lesson-media/{media_id}/publish` | 发布已批准的不可变版本 |

学生端只提供与当前章节发布版本、教程和来源修订一致的已发布产物：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/chapters/{chapter_id}/media/published?publicationId=…&lessonId=…` | 已发布视频列表，NC/SA 与未批准视频不返回 |
| `GET` | `/api/chapters/{chapter_id}/media/published/{media_id}/asset` | 学生视频文件 |
| `GET` | `/api/chapters/{chapter_id}/media/published/{media_id}/captions` | 学生 VTT 字幕侧车 |
| `GET` | `/api/chapters/{chapter_id}/media/published/{media_id}/captions/download` | 下载学生 VTT 字幕 |

`apps/web/src/api/lessonMedia.ts` 集中处理媒体 API。学生 `MediaPlayer` 接收 `{chapterId, publicationId, lessonId, sourceRevisionId}`；没有当前来源版本的已发布视频时返回 `null`。教师 `MediaStudioPanel` 接收 `{chapterId, publicationId, lessons, selectedLessonId?}`，由 `useLessonMediaStudio` 负责 API 与异步状态；章节、发布和课程切换会清空旧状态，迟到的列表/操作响应不能选回旧视频版本。播放器不调用练习或掌握接口。

持久化由 `LessonMediaStore` 管理 `lesson_media_revisions` 与 append-only `lesson_media_reviews`。修订迁移为 `0018_lesson_media`，down revision 是 `0017_quality_reviews`；后续语音会话迁移依赖 0018。视频与 VTT 使用内容 SHA-256 文件名，写入后改为只读；重复哈希产物校验内容一致后复用。重试或取消产生的孤立文件不会被学生列表引用。

## 验证

媒体模块通过的针对性验证：

- 后端从 `apps/api` 执行 `UV_CACHE_DIR=/private/tmp/dotty-f0d2-uv uv run ruff check application/lesson_media_renderer.py application/services/lesson_media.py application/lesson_media_jobs.py application/services/chapter_courses.py dependency_preflight.py domain/contracts/lesson_media.py domain/contracts/chapter.py domain/questions/contracts.py domain/questions/source.py infrastructure/runtime/tts_runtime.py infrastructure/runtime/qwen_tts_service.py persistence/lesson_media_store.py routers/lesson_media_routes.py routers/runtime_routes.py tests/test_lesson_media_postgres.py tests/test_lesson_media_renderer.py` 与相同文件列表的 `uv run pyright`：通过，Pyright 为 0 errors、0 warnings。
- `UV_CACHE_DIR=/private/tmp/dotty-f0d2-uv uv run python -m unittest tests.test_lesson_media_renderer`：3 个旁白符号保留、不支持公式拒绝和 TTS 前布局预检场景通过。
- `DOTTY_TEST_POSTGRES_ADMIN_URL=postgresql+psycopg://dotty_f0d2@127.0.0.1:59241/postgres UV_CACHE_DIR=/private/tmp/dotty-f0d2-uv uv run python -m unittest tests.test_lesson_media_postgres`：3 个真实 PostgreSQL 场景通过；渲染使用真实 CairoSVG 和 FFmpeg，TTS/章节模型仅替身于明确的外部 Runtime 边界。这些音频是测试夹具，不作为真实语音或教学质量证据。
- 从仓库根目录执行 `UV_CACHE_DIR=/private/tmp/dotty-f0d2-uv uv run python scripts/check_test_discipline.py`：101 个测试文件，0 处未登记 mock/sleep。
- 从 `apps/web` 执行 `pnpm exec eslint src/apps/media src/api/lessonMedia.ts --max-warnings 0` 与 `pnpm exec vitest run src/apps/media`：通过，7 个媒体 React 测试通过，其中 3 个覆盖 lesson/publication/active version 切换时的迟到响应。

主代理实际调用 Qwen TTS 与 FFmpeg 合成数学 75.76 秒、英语 83.04 秒的两段技术渲染原型；帧检查确认中文/数学符号与来源归属可读。证据为 `output/quality-review/evidence/real-media-math.json`、`real-media-english.json` 及对应 `*-frame.png`。两段记录均为 `technical_renderer_prototypes`、`humanReviewed=false`、`studentPublished=false`，只能证明真实运行时和渲染链可工作，不代表教师已确认旁白与教学内容质量。

最终全仓验证由集成代理在共享 API/OpenAPI 类型和工作台装配完成后执行；教师仍需在工作台完整审校视频、旁白和来源，再批准并发布。
