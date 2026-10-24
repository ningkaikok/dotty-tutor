# 本机完整流程接入（2026-10-24）

当前完整代码位于 `quiet-learning`，本机 Web 59174、API 8010 和后台 Worker 已从该检出运行，沿用 `/Users/kiki/work/tutor-demo/.env.local`、原 PostgreSQL 15432 与原 `data/`。未提交、推送或发布线上。

备份保存在本机私有 `output/local-upgrade-20261024/`，包含升级前 pg_dump。已恢复到临时数据库验证，再升级真实本机 schema 至 0028；57 张原业务表的逐表行数及内容摘要保持一致，仅增加空的 managed_file_objects 表。

进程与终端会话：API PID 51168 / session 37486；Worker PID 51169 / session 8266；Vite PID 46909 / session 77882。原 Qwen TTS 保持运行。原本机旧 Worker 并未在运行，本次已启动完整 Worker。

实际验证：schema verify ready=true；health 正常；统一学习入口按原数据显示下一项，点击进入原错题订正页，320px 无横向溢出、单一共享页头，任务中无 StudentNav。原已发布 7 页课件 GET 内容摘要不变，原页资产 GET 200。

Luna（极高）Orca Worker 本轮补练收尾：4 道自动补练上限后可在原页明确再练一道；失败保留反馈并重试；不伪造掌握、不无限生成。Worker 全量前端 65 文件260项、53 E2E 和全部前端门禁通过；Lead独立定向12项通过。

容量扩展已由 Lead 从 `courseware-preview` 独立检查并同步回 `quiet-learning`，API/Worker 已重启到新规则：最多 1000 页、34 批、每批30个物理页及45张课件页。真实 OpenAPI 端点也返回1000/34限制。

Lead 使用备份恢复的临时数据库、原教材记录和原773页PDF完成真实分批演练：26批连续覆盖1–773，不遗漏或重叠；同一请求重复启动复用原run；暂停状态由新服务实例读取，恢复成功。没有启动临时Worker或调用模型，原业务库整本任务数仍为0。演练完成后临时数据库已删除，私有备份继续保留。

真实本机773页教材页面在320px下显示整本选项可用；选择English和整本后“开始整本自动制作”按钮可用，无横向溢出或用户可见错误。只确认操作入口，没有点击原业务库的整本生成。手机截图在`output/playwright/whole-book-773-mobile-ready.png`。

容量修复的 Orca Worker 全量验证：Python3.12真实隔离PG共1017项通过；前端65文件262项、53项E2E及全部前端门禁通过。Lead同步后独立重新执行 `pnpm lint`、`pnpm vitest run`（262项）、`pnpm exec tsc --noEmit`、`pnpm check:api`，并在真实59174/8010核对UI、API契约、素材、原发布内容和Worker进程。容量修复没有迁移变化，因此无需再次迁移或重复Docker重建。

文件存储目前仍为 local；私有云桶尚未配置，不能声称云端文件已持久化。

## 当前运行位置与重启

当前源码唯一交付位置是 `/Users/kiki/work/tutor-demo/.cindy-worktrees/quiet-learning`；`courseware-preview` 仅用于本次容量修复的隔离开发和验证，当前不服务真实端口。原主检出 `main` 没有写入这些源码改动。使用主检出的旧启动脚本会加载旧源码，重启时应继续指向quiet-learning。

API与Worker都在 `quiet-learning/apps/api` 运行，启动前在当前终端加载原 `/Users/kiki/work/tutor-demo/.env.local`（不要输出内容），设置 `DOTTY_DATA_DIR=/Users/kiki/work/tutor-demo/data`，以及 `UV_PROJECT_ENVIRONMENT=/Users/kiki/work/tutor-demo/.cindy-worktrees/quiet-learning/output/review-py312`。沿用原配置中MinerU绝对路径；未配置时可使用 `/Users/kiki/work/tutor-demo/.mineru-venv/bin/mineru`。使用 `uv run --no-sync python -m uvicorn app:app --host 127.0.0.1 --port 8010` 与 `uv run --no-sync python -m worker --registry app:job_registry` 分别启动两个进程。不要对原库重复启动另一个Worker或另一套空数据目录。

前端在 `quiet-learning/apps/web` 使用 `DOTTY_WEB_PORT=59174 DOTTY_API_URL=http://127.0.0.1:8010 pnpm dev --host 127.0.0.1`。原Qwen服务仍在8020运行，无需重新启动。此次接入没有配置开机启动或更改个人环境文件。


## v0.41.0 界面接入（2026-10-24）

课件制作与样式收尾后，将已审查的21份前端源码/元数据同步到原 quiet-learning Vite；59174 的原进程继续服务，API 8010、Worker、原数据库及个人配置保留。实际打开59174核对教材库、773页教材管理和7页已发布课件，原发布ID、recordVersion 16与课件GET响应SHA256在同步前后完全相同。原页不重做、不重新发布；没有启动真实整本生成任务。

新界面优先显示已有课件与直接授课，教材管理可继续整本制作；上传、管理、预览与授课共用导航样式。此处为本机界面更新，GitHub版本发布及验证见[发布验收](release-v0.41.0-acceptance.md)，不代表Render部署。
