# 质量复核模块实施说明

## 业务验收场景

- `user` 在我的教材中查找待审任务时，`Given` 已有课程、来源案例和媒体历史，`When` 选择「待我复核」，`Then` 来源按真实文件身份分组，未关联旧案例保留，待审课程及当前发布版本的最新媒体均可打开；请求失败不显示确定为零的数量。
- `user` 在章节复核时，`Given` 来源与当前修订匹配，`When` 接受来源案例并返回教材列表，`Then` 来源计数刷新，但课程和媒体仍须分别审核；案例切换、返回及刷新保留正确上下文。
- `user` 审查已发布课程的视频时，`Given` 已存在更新的未发布教材修订，`When` 从媒体待办打开章节，`Then` 媒体课时与原句仍来自对应发布快照，旧媒体待审不会因新版本已批准而重新出现。

- `user` 导入包含 18 个真实开放教材案例的来源包时，`Given` 来源类型、许可、版本、PDF 指纹和每案页码/引用齐全，`When` 教师提交导入，`Then` 系统持久化原页来源、预期结果、当前实际结果及不可变版本，并将全部案例标为待复核且不计入人工集。
- `user` 复核一个真实来源案例时，`Given` 案例、教材页和实际输出来自同一来源及输出版本，`When` 教师作出接受或拒绝并填写理由，`Then` 系统记录登录用户、决定、理由、时间和所审版本；若该版本已变化，则拒绝旧页面提交并要求重新查看。
- `user` 独立双审一个人工金标准案例时，`Given` 第一审已持久化，`When` 同一用户尝试完成第二审，`Then` 系统拒绝并保持双审未完成；`When` 另一位登录用户审阅同一冻结案例版本，`Then` 系统保留两个独立决定，并仅在达到审核契约时允许进入人工候选集。
- `user` 复核模型 A/B 输出时，`Given` 两个输出版本随机匿名且来源、提示词和 schema 版本可追溯，`When` 评分者对匿名输出提交评分，`Then` 系统保存评分与案例版本；评分提交前不揭示模型身份，评分臂缺失或覆盖门槛未满足时不生成正式排名。
- `user` 导出人工金标准或查看质量统计时，`Given` 数据中混有真实开放来源、合成案例和未复核案例，`When` 执行导出或统计，`Then` 只有满足现有 benchmark 的 50+ 人工案例及维度/分层覆盖门槛的数据可计入；合成与公开教材来源永不因重命名或决定而计为人工金标准。
- `user` 对真实来源启动一次 OCR/模型质量评测时，`Given` 案例数量、并发、尝试数和时间预算有上限，模型运行参数及来源/提示词/schema 版本已固定，`When` Worker 执行，`Then` 系统持久化实际运行方式、Provider/Model、耗时、错误以及可得的 Token/成本数据；PDF 文字层重放始终标为文字层检查，不能冒充 OCR 或人工结论。
- `user` 查看开放来源导入案例时，`Given` 原始材料保存在受控的数据目录并通过来源指纹校验，`When` 工作台展示该案例，`Then` 教师可核对原始页/引用、预期与实际输出；文件缺失、指纹变化或引用不存在时案例保持阻断状态并不得导出或发布。
- `user` 快速切换来源包时，`Given` 较早的包请求尚未返回，`When` 用户选择另一个包并打开其中案例，`Then` 较早响应不得覆盖当前包、案例详情或意见草稿，且加载期间不得提交旧案例。
- `user` 快速切换案例时，`Given` 旧案例详情尚未返回且用户已填写旧意见，`When` 用户选择另一个案例，`Then` 工作台立即清空旧详情和草稿、禁用提交，并在乱序响应完成后仍只显示新案例。

## 实现记录

已实现：

- PostgreSQL 持久保存来源包、不可变案例快照/修订和追加式决定；公开教材案例的来源类型与 `counted=false` 由服务端固定，来源、输入、输出快照均保存 SHA-256 指纹。0022 兼容迁移会为已运行旧版 0020/0021 的数据库补齐来源复核字段并保留既有案例与决定。
- `POST /api/quality-reviews/packages`、包/案例列表、详情、决定、修订及原页预览端点已接入应用。决定 actor 从服务端认证上下文生成；当前共享教师凭据和 demo 身份都明确为未验证。
- 原 PDF 可来自现有教材上传记录，或服务端配置的 `DOTTY_QUALITY_REVIEW_SOURCE_DIR` 下两个固定 OpenStax 素材编号。请求不接受绝对路径；服务端每次校验文件哈希、PDF 页码和来源定位，并使用既有 Poppler 页面渲染器提供受控预览。工作台已展示真实 PDF 原页和引用，而非以 OCR 文本代替图像；页码标签明确指 PDF 页序，不等同于教材印刷页码。
- 对已绑定受管 PDF 的决定，服务端要求复核者填写能在指定原页找到的摘录，随后保存最终单人教材决定；没有受管原件或摘录不匹配时，只能保存明确标为待核验的临时意见。旧版本提交会被拒绝；显式修订会追加冻结版本，旧决定仍绑定原版本。当前身份未验证，因此最终单人决定仍不是独立双审或正式人工金标准。
- 工作台以可读方式展示题目/段落、预期结果、实际观察、失败项及 MinerU 原始输出和限制说明，并把扫描页 MinerU OCR 与 PDF 文字层题号回放标成两种独立检查。已导入的 18 个真实开放教材案例都仍待用户复核；数学 OCR 记录是由原 PDF 渲染出的扫描页，含已发现的识别错误，成本记录为 unknown。工作台本身不启动 OCR 作业；OCR 仍使用系统已有教材 OCR 主流程。
- 有界模型 A/B 评测由共享 JobStore/Worker 执行，最多 6 案、每次调用最多 60 秒、总预算最多 300 秒，创建任务固定一次尝试。`POST /api/quality-review-runs` 是此模块唯一占用昂贵模型配额的端点；其他列表、刷新、评分、取消和导出检查沿用普通请求限制。匿名臂仅在本次案例全部完成评分后揭盲，单人结果只是探索性统计。
- 金标准导出要求受信来源、已验证作者、同一冻结版本上两位不同的已验证最终复核者，且满足现有 50+ 案和覆盖门槛。公开教材、合成来源、demo/共享账号身份及未完成评分均 fail closed；当前导出检查会说明未就绪原因，不会生成虚假人工结果。
- `useQualityReview` 管理导航和网络状态机。包或案例切换会立刻清空旧详情与草稿；选择代数丢弃乱序响应，避免迟到的请求覆盖当前选择或把意见提交给旧案例。
- 主入口为「我的教材 → 待我复核」，关联案例复用 `CaseReviewPanel` 嵌入章节第 2 步，未关联案例使用兼容详情页；`useQualityReviewInbox` 负责筛选与刷新，来源、课程、视频分别显示数量。收件箱只读取已有快照，接受来源意见不会批准课程、视频或产生学习证据。
- 模型盲评与既有运行历史移至 `/studio/metrics?tab=evaluation`。普通来源复核不要求配置模型或导入 JSON；旧案例链接保留包/案例及返回上下文。进入页面、刷新和查看历史不会创建收费任务。
- OpenStax EXAMPLE / TRY IT / 练习切分已修正 `-13.` 等正文数字误识别及练习区段遗漏，并以真实页面回放案例覆盖章节尾部截断；中文教材和既有题号语料保留为回归基线。
- Vite API proxy 可通过 `DOTTY_API_URL` 指向隔离验收 API。

工程验收不等于教学效果验收。用户尚未提交 18 个案例的真实人工决定；这些记录仍然全部 pending，公开来源永不计入正式金标准。系统当前不提供 OCR 启动按钮，也没有经过身份验证的独立第二位复核者；不得据此报告 OCR 语义通过、正式模型排名、双审完成或金标准可导出。

### OCR 公式发布保护（2026-10-12）

工作区：Windows `C:\Users\KaiNing\work\dotty-tutor`；最初基线 `039ba19`（`fix/ocr-formula-quality`），2026-10-13 合并到 main `4017f30`。
本节记录工作区验证，不代表已部署；历史已交付版本的验收与本次工作区验证分别记录。

章节来源设置新增“公式识别与原页不一致”，保存为来源页 `formula_mismatch`。已知错识阻止章节发布以及课件规划、审核和发布；批准生成讲解不能清除来源标记，应修订来源并重新审核。
共享 OCR 门禁检查 `$…$`、`$$…$$`、`\(…\)`、`\[…\]` 的配对、公式内花括号结构，以及分式/等式中被空格断开的数字（`formula_digits_fragmented`，仅作疑似标记，不推断原值），保留原始文本，按既有重试预算进入重试或隔离。题目、章节和课件发布边界复用该检查，包括没有旧 issues 的来源快照。

本次在 Windows 下载同一公开教材，SHA-256 与历史 `08553ad4…680805` 一致，并查看 PDF 第 214 页（印刷页 206），建立原页参考。原文件和渲染图只保存于忽略的 `output/ocr-formula-quality/`。
历史 MinerU 原始输出未随代码分发，本机也没有可用 MinerU 配置；没有重跑真实 OCR。回归中的公式截断和符号替换均为明确的合成场景，不冒充历史错误或人工复核结果。
这次修复证明错误来源的发布保护，不能据此报告 OCR 识别模型的语义准确率已修复。

首轮（基线 `039ba19`）实际验证（日志位于忽略目录）：

- `uv run ruff check .` 通过；`uv run pyright` 在 Windows 因既有 `legacy_textbook_cleanup.py` 的 `os.fchmod` 报 1 项类型错误。按配置补齐根虚拟环境依赖后，`uv run pyright --pythonplatform Linux` 通过。
- 完整后端 `uv run python -m tests.postgres_test_runner` 在本次独立 PostgreSQL 59421 实例创建一次性数据库并执行全部 unittest 发现：832 项，9 项失败、4 项错误；失败涉及旧清理的 Windows 权限/编码/锁行为、符号链接权限、FFmpeg 缺失和模型目录可用性，不报告完整门禁通过。
- 最终相关后端 `uv run python -m unittest tests.test_ocr_quality tests.test_answer_spec_gate tests.test_chapter_postgres_acceptance tests.test_courseware_postgres_acceptance tests.test_textbook_ocr_pipeline`：43 项通过。补充转义美元符号及错序花括号输入后，`uv run python -m unittest tests.test_ocr_quality`：11 项通过。
- Web `pnpm generate:api` 已重新生成类型；`pnpm lint`、`pnpm vitest run`（48 文件/185 项）、`pnpm check:api`、`pnpm exec tsc --noEmit`、`pnpm run build` 均通过。
- `DOTTY_WEB_PORT=59422 pnpm run test:e2e`：30 项通过；浏览器使用既有 API fixture，持久化和发布门禁由真实 PostgreSQL 回归验证。部分非场景代理请求仍有本机 8010 连接提示，不属于真实部署验收。
- `python scripts/check_test_discipline.py`：108 文件、0 未登记 mock/sleep；`git diff --check` 通过。没有 Docker 配置改动，未运行 Docker config/build/up/down；未执行真实 OCR、部署或业务库修改。临时 PostgreSQL 在验收后停止。

合并到 main `4017f30` 后复验（2026-10-13，同一 Windows 工作区）：

- 合并冲突：共享门禁保留单次扫描实现，并纳入 main 新增的数字断裂疑似标记与页面级 `formulaReview` 原文保留；`api.ts` 与契约同为新增 `formula_mismatch`。
- `uv run ruff check .` 通过；`uv run pyright` 仍只有上述既有 `os.fchmod` Windows 错误。
- 未配置 `DOTTY_TEST_POSTGRES_ADMIN_URL` 的 `uv run python -m unittest discover -s tests -p 'test_*.py'`：625 项，52 项跳过，1 项失败、4 项导入错误，全部因本机缺少 Cairo 动态库（`libcairo-2.dll`）无法导入课件媒体渲染器；无业务断言失败。本机无 Docker/WSL/PostgreSQL，未运行 PostgreSQL 完整套件，以 PR CI 为准。
- 同一 Cairo 缺失使 `pnpm generate:api` / `pnpm check:api` 无法导出 OpenAPI，未在本机执行；生成类型差异已人工对照契约。
- 按锁文件 `pnpm install --frozen-lockfile` 补齐 main 新增依赖后，`pnpm lint`、`pnpm vitest run`（52 文件/197 项）、`pnpm exec tsc --noEmit`、`pnpm run build` 通过；`DOTTY_WEB_PORT=59449 pnpm run test:e2e` 37 项通过。
- `check_test_discipline.py`：115 文件、0 未登记 mock/sleep；`git diff --check` 通过。仍未执行真实 OCR、部署或业务库修改。
