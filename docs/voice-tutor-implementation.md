# 语音 Tutor 实现说明

日期：2026-10-11。语音 Tutor 位于已发布章节的当前非测验步骤。学生录音后先回放，转写或手动输入内容保存在独立会话草稿中；只有学生明确确认文字，系统才会请求导师回应。语音会话不写入作答、评分或掌握记录。

## 交互和持久化边界

- 学生可使用麦克风录制最多 60 秒音频，也可上传已有录音。录音可在提交转写前回放。浏览器不支持录音时，界面提供上传录音和手动输入转写的回退。
- 转写可由受支持的浏览器 `SpeechRecognition`、可选服务端 STT，或学生手动输入提供。识别文字显示为可编辑草稿；识别置信度只作为复核提示。Whisper 不提供置信分数时会明确显示“置信度不可用”，并保留人工确认步骤。
- 上传录音限 16 MB、60 秒；服务端先验证容器签名、音轨和时长，再运行有界解码。录音仅短暂写入临时文件用于处理，处理后删除，不保存进语音会话。浏览器中的回放对象在替换录音或组件卸载时释放。
- PostgreSQL 的 `voice_sessions` 保存 learner、发布版本、lesson、block、草稿、确认文本、代际和对话记录，保留 30 天。浏览器只保存会话恢复凭据；凭据以哈希持久化，恢复时还需匹配同一 `learnerId`。过期或无权访问时 API 不返回会话内容。
- 每轮回应通过数据库事务认领 generation。取消或修改草稿会推进 generation，使迟到的模型回应无法写入对话。刷新后恢复中的活动 generation 可被取消；模型响应完成后仅在仍拥有 generation 时提交。
- 只有 `/api/chapters` 提供的确切 publication、lesson 和 block 可用于上下文；quiz block 不开放语音追问。TutorRuntime 收到经过字段筛选的只读课程上下文，并通过既有 JSON 模型契约生成简短回应。它不调用判题状态机，不读取标准答案或写入学习证据。回答语音复用现有 `playSpeech`/TTS 路径。

## 转写配置

默认无需配置 STT 凭据：学生仍可录音、回放并手动确认输入的文字。可选转写按以下顺序选择：

1. 配置本地 Whisper tiny 运行环境：

   ```text
   VOICE_STT_LOCAL_PYTHON=/absolute/path/to/python
   VOICE_STT_LOCAL_MODEL_PATH=/absolute/path/to/whisper-tiny
   VOICE_STT_LOCAL_DEVICE=cpu
   VOICE_STT_LOCAL_LANGUAGE=en
   ```

   Python 环境需提供 PyTorch、Transformers、NumPy 和 FFmpeg。模型从本地目录加载，启用 `local_files_only`，不会在请求期间联网下载。运行器最多等待 150 秒；服务默认 45 秒超时。CPU 是默认设备；`mps`/`cuda` 仅在宿主运行环境确实支持时使用。`VOICE_STT_LOCAL_LANGUAGE` 可设 `en` 或 `zh`，留空时让模型检测语言。

2. 如未配置可用的本地模型，可选配置 OpenAI 兼容的转写服务：

   ```text
   VOICE_STT_API_KEY=<server-side secret>
   VOICE_STT_BASE_URL=https://api.openai.com/v1
   VOICE_STT_MODEL=gpt-4o-mini-transcribe
   ```

   API key 只保存在服务端环境，不返回给浏览器。兼容服务支持可选 token logprob 诊断；缺少诊断时同样提示置信度不可用并要求确认。若两种服务都不可用，状态端点会给出原因，录音上传、回放和手动文字确认仍可使用。

独立运行器为 `apps/api/infrastructure/runtime/local_whisper_stt.py`，接收 `--audio-file`、`--model-path`、`--language` 和 `--device` 参数。进程不经过 shell，文件和模型路径由服务端配置；解码仅接受明确的音频容器白名单、限长输入和 `file,pipe` 协议，并给子进程提供最小环境变量集合。

## API 和代码入口

- `GET /api/voice/status`：报告可选 STT 是否就绪及其提供方。
- `POST /api/voice/sessions`、`GET /api/voice/sessions/{id}`：建立或恢复绑定 learner 与已发布步骤的持久会话。
- `PUT /api/voice/sessions/{id}/transcript`、`POST /api/voice/sessions/{id}/transcribe`：保存文字草稿，或将经过验证的录音送至已配置的转写器。
- `POST /api/voice/sessions/{id}/confirm-transcript`：确认当前草稿 revision 和最终文字。
- `POST /api/voice/sessions/{id}/responses`、`POST /api/voice/sessions/{id}/cancel`：生成或取消当前 generation 的导师回应。
- 语音路由由 `build_voice_router` 构造，数据库结构和 CAS 存储位于 `persistence/voice_schema.py`、`persistence/voice_store.py`；`0019_voice_sessions` 接在 `0018_lesson_media` 后。
- 学生 UI 是 `VoiceTutorPanel` 和 `useVoiceTutor`，在 `PublishedChapterApp` 中随 LessonPlayer 的活动 block 切换。会话恢复 key 包含 learner、publication、lesson 和 block，避免跨课程或跨 learner 重用浏览器凭据。

所有带会话凭据的请求都要求 `X-Voice-Session-Token` 与 `X-Learner-ID`。受保护部署还需让已认证 session learner 与该 header 一致；跨域时两个 header 都需出现在 CORS allowlist。

## 浏览器和质量边界

麦克风采集要求 HTTPS 或浏览器认可的安全上下文，并由用户授权。`MediaRecorder` 广泛可用，但实际编码由浏览器决定；服务端验证真实媒体容器而不信任上传的 MIME 标记。回放转写调用 `SpeechRecognition.start(audioTrack)`，MDN 标为 Limited availability；部分浏览器会把音频发送至服务端识别。这条路径可失败或不可用，界面检测录音轨道支持并保留服务端/手动输入回退，任何识别结果都不能绕过人工确认。详情见 [MDN getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia)、[MDN MediaRecorder](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder) 与 [MDN SpeechRecognition.start](https://developer.mozilla.org/en-US/docs/Web/API/SpeechRecognition/start)。

Whisper tiny 使用公开的多语言模型卡和离线 Transformers 加载方式，模型卡标注 Apache-2.0；它不能仅凭运行成功证明真人语音效果。项目所做的合成音频输入仅作为技术链路验证材料，需与真人语音准确率评估区分。服务端 STT 兼容边界采用 OpenAI Audio Transcriptions 请求形状；可用模型、响应字段和置信诊断以官方 [Speech to text guide](https://developers.openai.com/api/docs/guides/speech-to-text) 为准。

本实现是单轮请求/回应式语音互动：录音、识别、用户确认、TutorRuntime 回应、既有 TTS 播放。当前不提供边录边识别、双工流式对话或真人语音质量结论。

## 验收场景和验证

`apps/api/tests/test_voice_tutor_acceptance.py` 使用 PostgreSQL 与 Given/When/Then 场景覆盖：未确认转写不能发起回应或写学习证据；确认前保存相同 STT 文本不会丢失置信诊断；取消后迟到的回应不能提交；更正转写会使旧 generation 失效；重连恢复只对绑定 learner 生效；两个并发请求不能同时认领同一 generation。测试使用真实 store 和数据库，不替换内部服务或存储。

本次工程验证中，后端语音验收场景 6 项通过，测试数据库完成从 `0017_quality_reviews` 到 `0019_voice_sessions` 的迁移；语音模块 `ruff` 和针对语音文件的 `pyright` 通过。前端 lint、Vitest 39 个文件/146 项测试和 Playwright Chromium 23 项现有回归通过。全量 E2E 是既有页面回归，不作为真实语音质量证据。

额外离线 Whisper 技术探测通过 `local_whisper_stt.py` 处理一段系统语音合成的数学音频，返回预期英文问题，运行器报告 `confidence: null`。该合成音频只证明配置、格式校验、离线模型加载和结果契约可运行，不代表真人识别准确率。真人语音效果仍需由真实录音单独评估。
