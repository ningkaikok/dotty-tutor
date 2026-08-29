# Dotty Tutor 技术路线图

> 本文只回答“怎样可靠地实现产品路线”。它是技术任务的唯一优先级入口，详细运行治理见
> [`runtime-governance-plan.md`](runtime-governance-plan.md)，产品目标见
> [`product-roadmap.md`](product-roadmap.md)。

## 技术优先级规则

- **T0 正确性**：直接阻塞演示或会产生错误学习记录的问题必须先修复。
- **T1 可复现性**：让一次模型/OCR运行可以定位、评测和回放。
- **T2 可靠性与性能**：让长任务可恢复、模型调用可控、陪练延迟可测量。
- **T3 实验能力**：只有产品验证需要时才引入新库、新模型或新基础设施。

## 当前执行队列（2026-08）

本轮 P0（任务状态、Worker、错误契约和 Runtime 配置）已经完成并通过后端、前端和 Docker 验收。

**T0 收口（2026-08-29）· 知识点实体与 mastery-v2**：代码、迁移脚本、并发锁和测试已完成。
`knowledge_points` 以 `publication_id + normalized_name` 建立发布版本作用域内的实体；服务端从发布题目解析
知识点，作答保存 `knowledge_point_id`；`mastery_states` 按每道题最新证据重建，并记录证据置信度、算法版本
和计算时间。详见 [`ADR-001`](adr/001-knowledge-point-identity-and-mastery-v2.md)。

实际开发 PostgreSQL 已完成 `dry-run/apply/verify`：6 条作答记录已回填、5 条掌握度投影已重建，旧表保留且
无空知识点 ID。空的新数据库不需要手动迁移；已有旧表的数据库必须显式迁移。跨教材知识点是否需要升级到
学科/教材作用域，留到老师视图前基于真实使用场景决定，不在本次 T0 中提前建模。

实施要求：新引入的枚举（知识点类型、错因分类）必须自带**旧值映射**——在 `Enum._missing_` 里把
已删除或改名的取值映射到最近的现存值。本项目用 PostgreSQL JSONB 存学习证据，枚举一改，
历史行就会反序列化失败；几行代码可以避免一次数据迁移。

**优先级重排（2026-08-21）**：这一轮用真实教材反复复验渲染/OCR 切分修复时，走的是“人工翻页发现坏样本 →
定位根因 → 修 → 验证”的手工循环，成本很高，且每次只覆盖当次偶然点开的那道题。这个循环本身就是
`test/offline-ai-evaluation`（脱敏金标准集）和 `feature/badcase-replay-loop`（Badcase 回放）要解决的
问题，而这一轮恰好沉淀了一批**已经核实过根因、可以直接当固定用例**的真实坏样本（题号识别导致的题目
覆盖/消失、题干图文错位、图片选择题占位文字残留与重复选项误判、答案规范与题型错配、显式图注被忽略），
不需要再另外准备语料。因此把这两项从队列提前到最优先，先用现成坏样本把金标准集和回放循环建起来，
再决定下一步修哪个：

1. **T1 / `test/offline-ai-evaluation`**：建立脱敏金标准集，覆盖 OCR、公式、题图、题型、审核和陪练；
   种子数据直接复用本轮已经写成单测夹具的真实坏样本（`apps/api/tests/test_question_processing.py`、
   `test_question_segmentation.py`、`test_question_content_residue.py`、`test_stem_image_placement.py`
   里的真实 OCR 片段），不必重新采集。
2. **T1 / `feature/badcase-replay-loop`**：统一 Badcase 标签，支持失败样本重放、前后版本比较和回归入集。
   建成后，T0 里剩下的几项开放问题（见下）应该先进金标准集验证影响范围，再决定是否值得单独立项修复，
   不要再凭感觉判断优先级。
3. **T2 / `feat/ocr-preflight-report`**：增加页面预检和脏页报告，复用现有页面级 OCR 路由、质量门禁和局部重试。
   **已完成**（`apps/api/ocr_preflight.py` + `ocrRun.preflight`）：预检只参与路由（空白页跳过 MinerU
   升级）和报告，不删除页面；扫描页安全边界有专项测试。
4. **T2 / `feat/model-capability-registry`**：建立模型能力目录，按任务能力筛选候选模型，并保持 RunSnapshot 不变。
   **已完成**（`infrastructure/runtime/capabilities.py` + `providers()` 的 `modelDetails` +
   调用路径健康挂钩）；剩余的"切换前后评测集比较"随 T1 模型维度解锁。
5. **T0 / `feature/sub-questions`（已立项排期）**：多小问结构三处联动，按既有 T0 设计块拆分为
   三个独立 PR——①契约层 `subQuestions` schema + 逐小问 answerSpec + 质量门禁适配；
   ②前端分小问作答渲染；③判题循环逐小问确定性判定与"不可判小问"显式标记。
   触发条件已满足（本地题库占比 17%）；在每个 PR 改动落在对应文件时顺路执行。
6. **P1 生产准备（按需）**：真实 PostgreSQL 集成测试、CodeQL、Actions SHA 固定、API 错误脱敏
   与管理员调试入口、备份恢复、限流和资源生命周期；只有准备公网测试时才进入开发。

每一项都必须单独有测试、文档和回滚边界；完成当前项后再进入下一项，不把模型接入、UI 重构和数据库迁移混在
同一个 PR 中。

**并行工程卫生项**（不占用上面队列的顺序位，见 T0/T1/架构边界对应条目）：三项已全部完成——
`local-demo` 身份集中配置（`domain/constants.py:DEMO_LEARNER_ID` + `apps/web/src/api/client.ts`，
生产代码内不再有字面量）、Ruff/ESLint/Pyright 静态检查门禁（见 T1 第 7 条）、超长文件审查
（见“架构边界”的逐文件拆分边界）。超长文件的**执行**仍待触发：拆分按“下次需要行为改动时顺路执行”，
不做纯搬家式重构，因此这里不会出现一个可勾选的完成时间点。

## T0：正确性与回归保护

- [x] 统一题目主链路的公式、图片 URL 和内容块渲染：题干、选项、条件在内容生产端与学生端共用同一套
  `contentBlocks` 契约，Playwright 回归覆盖公式与题图顺序。题干图片引用清理已提升到所有题型的通用路径
  （此前只对 A-D 图片选择题生效），统计表由后端解析成结构化 `table` 块；质量门禁增加残留检查，
  未结构化的图片引用或表格标签会让题目进入人工复核，不再依赖人工在页面上发现。
  > 这条曾被标记为完整覆盖“讲解、历史消息和错误回退”，但实际只覆盖了题目主链路。
  > 过度声称覆盖范围导致同一类缺陷被误判为已修复，换一种题型就复发一次；剩余通道见下面两条。
- [ ] 课程 `markdown` 讲解块、陪练历史消息和错误回退仍把整段文本直接交给 `MathText`
  （`apps/web/src/lesson/rendererRegistry.tsx`），非公式内容会退化成纯文字。这些通道要么改为后端下发
  结构化内容块，要么明确约定“只允许纯文本 + `$...$`”并在写入侧校验，不要在前端补一层解析。
  > 优先级：中。目前没有确认的真实坏样本（题目主链路上的同类问题已经修完），但结构上和这一轮反复
  > 复发的“某个渲染通道被漏掉”是同一类缺陷，大概率会在 Badcase 语料跑起来后自然暴露——先建语料，
  > 让真实坏样本决定这条什么时候动手，不要凭猜测排期。
- [x] 题号识别根治（`QUESTION_START_PATTERN`，`apps/api/domain/questions/source.py`）。
  两个子问题均已修复并进入金标准语料回归：
  **子问题 A**（续举例编号 "3、4." 被误判为新题号，question-segmentation-v3）：'、'分隔的候选题号
  只有在前文最后一个非空白字符是枚举标记（、，,）时才判为续行举例；对本地全部真实教材 OCR 重放验证，
  唯一变化即坏样本所在批次（伪 '3' 消失、第 9 题取回题图），其余批次不变。roadmap 曾考虑的
  "句末标点前置"宽方案被否决——真实语料里合法题目常以公式或数字收尾，且存在合法的"、"风格题号，
  误伤面大；最终规则以"前文停在枚举标记上"这一缺陷签名为界。
  **子问题 B**（题号粘连无换行导致整块消失）：已随 `fix/reconstruct-line-breaks-from-mineru-layout`
  修复，用 middle.json 行级坐标重建换行，未改动正则本身。
  两者的回归证据固化为语料条目 `subproblem-a-enumeration-not-a-boundary` 与
  `line-break-reconstruction-recovers-9-and-10`；安全网行为由合成样本单测继续覆盖。

- [ ] 图片与题目的版面级对应关系：图片归属曾经完全由 MinerU 的线性阅读顺序决定
  （`apps/api/domain/questions/source.py` 按纯文本位置分配），多图页面可能把图错绑到相邻题。
  题干里“主视图/图1/图2”这类内联图注已经能就地对齐（图片会解析到题干文字中对应的位置，见
  `extract_image_placements`），OCR 原文里明确写着“第N题图/第N题”的显式标注也已经优先于纯文本位置
  （见 `_apply_caption_image_attribution`）。`feat/caption-attribution-from-structured-json` 落地后，
  这条显式标注信号已经改为优先读取 `content_list.json` 里 MinerU 自己识别好的
  `image_caption`/`chart_caption`/`table_caption` 结构化字段，不再只靠在扁平文本里用正则猜"图片后
  紧跟第N题图"；没有结构化数据时完全回退到原来的正则逻辑。用本地 5 本真实教材验证过，这条改动在
  当前语料里和原来的正则结果完全一致（真实教材的扁平文本里图注文字本身没有丢失，正则已经够用），
  价值在于对"扁平化过程丢失图注文字、但结构化字段仍保留"的场景更稳健——已有单元测试用构造场景验证。
  仍未覆盖的是“既没有内联图注、也没有显式题号标注（不管是文本还是结构化字段）”的纯位置场景，需要
  版面坐标或语义校验才能根治，属于独立工程，不要与渲染契约混在一起做。
  > 优先级：低。两条高置信度信号（内联图注、显式题号标注，且后者现在有结构化数据兜底）已经覆盖
  > 大多数真实案例；剩下的纯位置场景目前没有独立于子问题 B（题号识别）之外的确认坏样本——先看
  > Badcase 语料积累后这类场景的真实占比。
- [ ] 支持多小问结构（`subQuestions`）。当前 `questionType`、`correctAnswer` 和 `answerSpec` 都假设
  一道题只有一个答案，而真实中考卷约 17% 的题目是多小问（本地 64 道题库中 11 道，其中 6 道含证明）。
  典型形态是「(1) 求证… + (2) 求数值…」：证明部分无法确定性判题，数值部分完全可以，但现在整题被归成
  `short-answer`，连可判的那一问也拿不到自动反馈。方案是每个小问带自己的题型与判题规范；没有规范的
  小问（证明、说理）显式标记为“由陪练反馈、不自动判分”并在界面说明，而不是假装能判。
  过渡措施已生效：质量门禁会拒绝“不可判题型携带 answerSpec”的错配数据，并对多小问发出提示。
  设计时先决定 `subQuestions` 与填空题已有的 `blanks` 分项结构是否合并，避免出现两套并行的分项模型。
  不要为证明题单独新增题型——证明只是“不可确定性判题”的一种，会被这个结构自然吸收。
  > 优先级：低。过渡门禁已生效，错配数据不会再静默进入发布结果，当前只是“多小问的可判部分拿不到
  > 自动反馈”的功能缺口，不是正在产生错误学习记录；`subQuestions` 涉及契约、前端渲染和判题循环
  > 三处联动改动，值得等 Badcase 语料确认真实占比和影响后再单独立项，不要抢跑。
- [x] 从 FastAPI 的 OpenAPI Schema 自动生成前端 API 边界类型；API 适配层再与页面所需的领域类型
  交叉校验，保留清晰的前端领域模型，同时避免 Pydantic `response_model` 与请求代码发生契约漂移。
- [x] 固化学生答案状态、重新提交、下一题、完成态和验证题重试的 Playwright 流程。
- [x] 学生做题阶段禁止自动 TTS；切题、离开页面或重复请求时取消过期请求。
- [x] 区分单题修复、重新 OCR、重新生成和整套重新审核，记录新旧版本和实际运行配置。
- [x] 归档错题时清理旧陪练线程和消息，同时保留错题、作答与学习证据；恢复后创建新线程。
- [x] 修复内容生产端模型/OCR 选择器的回归：已就绪的模型和解析方式必须可以打开下拉并切换；未安装的
  MinerU 可以保持禁用，但必须明确显示“未安装”原因。切换后界面状态、后端实际运行配置和运行快照必须一致，
  避免用户看到可选项却无法选择，或选择后仍使用旧 Provider。选择请求按控件隔离并取消过期响应；本机
  MinerU 自动探测仓库根目录 `.mineru-venv`，Docker 则明确提示必须提供 Linux MinerU 服务。
- [x] 防止考试“注意事项”编号被切成题目：题目切分容忍 OCR 空格/换行；章节标题缺失时使用考试说明语义黑名单
  跳过编号说明，并在生成前后用 `p0-v4` 质量门禁拦截。切分规则版本写入 OCR/提示词审计，历史坏产物必须显式重新 OCR/生成。
- [x] 固化多图题的图片角色和顺序：后端以 OCR 原始顺序生成 `imageManifest`/`contentBlocks`，质量门禁拒绝
  图片数量、顺序或 A-D 绑定不一致；前端只渲染当前结构化内容块，不在页面重复推断题图。
- [x] OCR 默认请求 MinerU；Docker 缺少 MinerU 时显示并审计 pypdf 回退。
- [x] 文字与视觉审核共用一个可切换的审核模型；视觉审核结果进入同一质量门禁。
- [x] 来源明确为 A-D 而模型产生额外选项时，自动隐藏多余项并隔离题目，等待修复或重生成。
- [x] 固定演示用户 `local-demo` 已收敛为集中配置：后端 `domain/constants.py` 的
  `DEMO_LEARNER_ID`，前端 `api/client.ts` 的同名导出；原先散落在路由/Store/契约层的
  默认参数全部改为引用常量。接入登录时删除常量并把调用点改为服务端身份即可。
## T1：质量、评测与可观测性

本轮已完成：单题修订版本与运行配置通过不可变审计表记录；OpenAPI Schema 生成前端类型并由脚本校验。

**已升级为最优先事项**（见“当前执行队列”）：这一轮修渲染/OCR 切分 bug 时反复用真实教材手动翻页
找坏样本，成本高且覆盖面随机——下面两条正是要解决这个问题，而且已经有现成的真实坏样本可以直接
当种子数据，不需要另外采集。

1. [x] 建立脱敏金标准集，覆盖 OCR、公式、题图、题型和陪练意图（9 条语料，
   `apps/api/evaluation/`，`python -m evaluation.replay`）。种子直接复用单测夹具的真实坏样本，
   含一个固化 T0 子问题 A 的特征化条目（已随 v0.23.0 修复转正为回归证据）。重放器支持多入口：
   切分 / 公式规范化（幂等断言）/ 审核质量门禁（状态+错误摘要）/ 陪练意图识别。
   **剩余**：讲解样本与期望质量锚点维度依赖 LLM-as-Judge 落地后补齐。验收时同步收敛版本化
   Prompt 模板：
   模板进入普通 Python 模块或小目录，运行快照记录 `templateId`/`templateVersion`/`templateHash`/
   `schemaVersion`，动态输入只保存题目修订、OCR 产物和线程摘要 ID 的引用；不保存渲染后的完整
   Prompt（含教材原文和学生输入），也不建设 Prompt 管理平台或巨型常量表。快照同时记录采样参数
   （temperature 等 options）；回放目标是结构化重放与指标比较，不是逐字节复现——本地 LLM 输出
   本身非确定，同模板同参数也可能因运行时版本产生差异。
2. [ ] 统一 Badcase 标签，支持从失败样本重放并比较结构、评分、耗时和调用次数。**进行中**：
   统一标签体系（`evaluation/labels.py`）、坏样本登记簿（`evaluation/badcases.json` + `badcase.py`，
   状态机约束"fixed 必须带修复说明和版本"）、前后对比工具（`python -m evaluation.compare`）已落地，
   结构维度的对比可用；模型调用边界指标表（#166）已就绪，评分/耗时/调用次数的对比
   只差评测集接入模型调用后串联。
3. [x] 创建不可变 `RunSnapshot`，记录模型、Prompt、Schema、OCR Provider 和校验器版本。
4. [ ] 将内容生产和后台任务已经具备的运行快照继续扩展到陪练的全部结构化日志。
5. [ ] 使用确定性指标评估答案/结构，使用独立审核模型评估讲解质量，并记录评分依据和置信度。
   **基建已落地**（`evaluation/judge.py` + `judge_cli.py`）：固定 rubric（clarity/targeting/factual，
   1-5 分）+ 版本化提示词 + 输出校验门禁（分值越界/缺依据/置信度越界一律拒绝）+ 按需 CLI
   （`python -m evaluation.judge_cli`，报告落 output/eval-reports/judge/）。judge 需真实模型调用，
   不进入确定性重放链路；内置三条讲解样本语料（分层引导卡模板产物）。**真实运行已完成**（2026-08-25，ollama/qwen2.5:7b ×3 样本全成功）：
clarity 均分 3.67、targeting 4.00、factual 5.00，置信度 0.9-0.95；报告落
output/eval-reports/judge/。后续可定期运行对比不同讲解版本的分数漂移。
6. [ ] 建立学习效果和模型成本的 PostgreSQL 聚合报告，不提前引入独立数据平台。
   **进行中**：业务漏斗已上线（`GET /api/funnel`），成本/token 维度的数据源也已就绪——
   模型调用边界指标表（`model_call_metrics`）随每次调用记录 runtime/task/provider/model/
   耗时/token/失败，聚合经 `GET /api/metrics/model-calls?days=N` 查询。漏斗与成本的
   联合展示待前端页面；"同知识点再次出错率"需要尝试与知识点的跨会话关联，
   待 subQuestions/画像工作后补充。
7. [x] 引入 Python Ruff 与前端 ESLint 门禁（`pyproject.toml` + `apps/web/eslint.config.js`，
   CI 中在 `apps/api` 下跑 `uv run ruff check .`、在 `apps/web` 下跑 `pnpm run lint`）。
   规则集刻意克制：Ruff 只开 E4/E7/E9/F/I，
   ESLint 补 tsc 抓不到的 hooks 依赖与未使用变量。两条 React Compiler 时代的保守规则
   （set-state-in-effect/purity）暂关闭并记录理由，对应的"key 重挂载/派生状态"重构列入
   超长文件审查的同一重构窗口。Prettier/格式化统一不进本门禁。

**Pyright 门禁已接入（2026-08-23，错误级）**——基线 100 项中的生产代码 24 项已全部清零
（含一处真实潜在缺陷：Store 在 engine 与 database_url 同时缺失时仍会调用 create_engine）；
门禁范围为生产代码（排除 tests 目录与 qwen_tts 独立子进程服务），版本固定 1.1.411。
**收紧决策（2026-08-23）**：测试目录维持排除。理由：(1) 存量 64 处 Optional 下标需逐一
改造为显式断言，churn 集中在历史测试文件且运行时断言已覆盖同类失败；(2) 仓库主配置纳入
tests 后 pyright 组合分析存在挂起问题（>10min 两次复现），独立配置秒级完成——未来若纳入，
用独立配置（pyrightconfig.tests.json 形态，已提交备用）而非合并主配置。
新增测试文件已示范门禁友好写法（先断言非 None 再下标）。qwen_tts 桩注解随其独立环境
工作单独处理。
8. [x] 确定性判题返回结构化、客观的 `EvaluationEvidence`（normalizedResponse、expectedMatched、
   unitMatched、failedBlankIds、tolerance 和 evaluatorVersion），已随回复 guideContext 与消息动作
   持久化（#155）。Turn Plan 使用该证据选择诊断动作——数值题空提交在任意阶段返回
   extract-conditions；具体误区仍由模型提出带证据和置信度的假设并按需确认，判据器不得直接输出
   `error_type`/`concept` 结论。Evidence 只追加保存，Tutor 不能覆盖原始作答与评分结果。
   泄漏断言保证标准答案不出现在任何返回结构。

## T2：模型、OCR 与上下文优化

### 模型能力目录

- [x] 注册 provider、model、显示名称、角色（生成/审核/陪练/视觉）、能力标签
  （json-schema/vision/math/long-context）、上下文上限、延迟与成本级别和回退模型
  （`infrastructure/runtime/capabilities.py`，Ollama 动态 tag 用前缀通配匹配，
  未登记模型走保守默认值——不编造能力和规格）。
- [x] 进程内轻量健康记录：连续失败计数 + 最近一次失败原因（阈值 3 次）；只影响
  `eligible_for_role` 候选筛选，绝不覆盖已开始运行的 `RunSnapshot`，任何成功调用即复位。
  已挂接 generate_json / generate_json_as 两条路径（覆盖生成、审核文字+视觉+修复、陪练）。
- [ ] 按任务能力筛选模型：服务端筛选函数已就绪，学生端只暴露产品允许的陪练选项的
  界面裁剪待接；**模型切换前后的固定评测集比较依赖评测集的模型维度**（见 T1 第 1 条
  进行中事项），语料补齐后自动解锁。

### 模型调用边界指标

- [ ] 在模型调用边界记录 provider、model、task（生成/审核/陪练/OCR）、耗时、token 用量、失败和
  回退信息；先以 PostgreSQL 聚合表落地，只有跨服务追踪需求出现后再评估完整 OpenTelemetry 栈。
  该指标同时是"陪练上下文分层"和模型切换评测的数据来源。

### 开发期只读工具

- [x] 开发期只读状态报告已落地（`python -m evaluation.report`）：聚合 Badcase 登记簿状态、
  最近确定性重放计数与 LLM-as-Judge 评审均分为一页摘要；严格只读，不触碰学生数据写入路径。
  运行状态统计的 HTTP 查询已由 `GET /api/metrics/model-calls` 覆盖。生产运行时不引入 MCP 或任何 Agent 工具协议。

### OCR 预检

- [x] 正式 OCR 前识别空白页、出版信息页、疑似题目页、无图纯文字页、公式密集页和图文混排页
  （`ocr_preflight.classify_page`，纯函数、毫秒级，只依赖 pypdf 文字层和图片计数）。
- [x] 每批次生成脏页摘要并写入 `ocrRun.preflight`（总页数/可处理页数/疑似脏页/需要视觉 OCR 的页数）；
  预检只决定路由——空白页在 auto 模式跳过 MinerU 升级——不删除原始页面，页面级质量门禁和局部重试不变。
- [x] 预检误判样本可按既有 Badcase 登记簿流程入库（每页分类携带命中原因列表）；暂无真实误判样本，
  出现后直接入集回放。

### 陪练上下文分层

- [ ] 将系统规则、工具定义、题目上下文、Schema 组成稳定前缀；学生输入和最近消息组成动态后缀。
- [ ] 先记录稳定/动态 token、耗时、调用次数和回退率，再判断 Prefix Cache 是否有实际收益。
- [ ] 只有 Provider 明确支持时才启用缓存；缓存键包含模型、Prompt、Schema、题目版本和知识点版本。

- [ ] **批次熔断与系统性失败识别**：当前一个批次里的题各自失败各自记录，但没有区分
  “这一题没做出来”和“在你去修配置之前什么都做不出来”。API key 过期、配额耗尽、上游超时这三类
  属于后者，连续命中若干次应当**暂停整个批次并给出可展示的具体原因**（如 `rate_limit: 429 …`），
  而不是把剩下的题逐一磨成失败记录。同时把“部分成功”确立为正经状态：20 页 OCR 成功 18 页就
  展示 18 页，而不是整批算失败。
  > 优先级：中。本地开发同样会遇到（换机器、key 过期），不依赖是否部署。
  > 但要等 Badcase 语料跑起来后确认这三类失败在真实运行里的占比，再决定阈值怎么设。

- [ ] **依赖自检（preflight）**：本项目依赖 MinerU、pypdf、Ollama、Codex CLI、Azure Speech、
  Qwen3-TTS 和 PostgreSQL，任何一个没配好都要等到运行时才炸。做一个“现在这条链路能不能跑”的
  自检页：每项一条 `{key, label, ok, detail, optional}`，**任何检查都不抛异常**（失败的导入或
  缺失的配置变成一条失败/可选记录，不是异常），整体 ok = 所有必需项通过。
  > 优先级：中。价值不只在部署——换机器、换环境、新人上手时同样省往返。
  > 与 `feat/ocr-preflight-report`（页面级脏页预检）不是同一件事：那条检查的是**内容**，
  > 这条检查的是**环境**，两者可以复用同一套报告结构但不要合并。

## T2：可恢复的长任务

- [x] 使用 PostgreSQL `background_jobs` 和单 Worker，支持任务 ID、独立进度、快速 `202` 响应和有限并发。
- [x] 增加取消、有限自动重试、人工重试、租约恢复和幂等键；失去租约的旧 Worker 不得提交结果。
- [x] 为整卷生成提供成功/失败/隔离/跳过汇总；服务端限制最多 50 页、100 道题，重试会跳过已成功批次。
- [ ] 只有吞吐基准证明单 Worker 不足时，才评估 Redis 或其他队列。

## T3：技术实验

- [ ] 交互数学只选择一个库实验（Mafs 或 JSXGraph），由 `InteractiveMathCanvas` 隔离。
- [ ] 批量视频需求出现后再评估 Manim；实时参数动画再评估 Motion Canvas。
- [ ] 反馈动效稳定后再评估 Lottie；不让动画库承载学习状态。
- [ ] RAG 先使用 PostgreSQL 元数据/全文检索，只有评测证明不足时再评估向量数据库。
- [ ] TTS 音频缓存、对象存储和 CDN 只有在真实延迟或流量指标达到阈值后实现。
- [ ] 实验性评估浏览器内推理（WebLLM/WebGPU）作为分层 Help 的离线兜底层；只覆盖提示生成这类
  低风险输出，不用于判题、出题和审校。沿用 TTS 三级回退的模式：云端模型 → 本地 Ollama →
  浏览器内小模型 → 固定模板提示，实验失败可整体移除且不影响其他层。
- [ ] 版本化轻量知识点树（`id`/`name`/`parent_id`/`taxonomy_version`/`status`），作为 P2 数据治理实验。
  知识标签从已审核内容派生（Source Artifact → Document Blocks → Learning Objects → Knowledge Tags），
  当前已有发布版本作用域内的稳定知识点实体，尚未建立父子关系、课标版本和跨教材映射。
  只有跨教材概念检索、先修关系推荐需求真实出现后再评估升级为正式图谱；不引入图数据库、embedding
  或向量基础设施——OCR 错误会被图谱关系放大，派生索引先行可以把这个风险限制在可重建的层。

## 前端数据请求与校验工具决策

- 暂不引入 TanStack Query：现有手写 `fetch` + `parse()` 规模尚可维护；只有在组件里出现明显重复的
  loading/retry/缓存失效代码，或接口数量继续显著增加时，再评估引入并限定在新接口上试点。
- 暂不引入 Zod：请求体校验已由后端 Pydantic 在 API 边界完成，前端重复校验大多是重复劳动；只有
  出现需要提交前做复杂交互式校验（例如分步表单）、且现状确实导致错误提交时，才评估局部引入。

## 架构边界

- 生产流程由路由、领域服务、Runtime/Store、Worker 和质量门禁执行。
- Agent 只用于开发期读报告、运行脚本、汇总进度和生成修复建议，不能直接修改学生掌握度或发布状态。
- 不为单一调用创建空 Repository/Manager/Factory；出现第二种实现或测试替身时再抽象。
- 暂不引入全局 LangChain/LangGraph、Multi-Agent、独立 Control Plane、Redis/Kafka 或 Tool Gateway。
- 模型结构化输出遵循"约束解码优先、兼容降级、确定性校验兜底"的分层：Ollama 路径传 `format: schema`
  （运行时无法转换 grammar 时降级为普通 JSON 模式并把 Schema 写入系统提示），Codex 路径使用
  `--output-schema`。语义质量门禁（答案正确性、题图归属、数学语义）是独立于结构化输出的一层，
  不因约束解码的存在而弱化，两者不可互相替代。
- **超长文件审查已完成**（Badcase 语料建成后的重构窗口已开启），拆分边界评估如下；
  每个拆分都是独立 PR、可单独回滚，在对应文件下次需要行为改动时顺路执行，不做纯搬家式重构：
  - `application/services/textbook_processing.py`（987 行）：四个公开工作流各自成模块——
    `complete_upload`（226 行）/`generate_full_paper`（165 行）/`process_batch`（169 行）/
    `regenerate_question`（143 行）；共享的批次源加载（`_load/_ordered/_reconcile`）和课程持久化
    （`_persist_lessons`）提取为协作对象，Service 保留薄门面供 HTTP/Worker 注册表使用。
  - `domain/questions/pipeline.py`（786 行）：按职责三分为 prompt 构建（~60 行）、模型输出规范化
    （normalize_* 系列，~250 行）、内容块构建与校验（rich_text/table/blocks/validate，~400 行）；
    `build_question_content_blocks` 作为公共 API 从原路径 re-export，调用方无感。
  - `apps/web/src/apps/textbook/TextbookApp.tsx`（530 行）：预览作答状态机（input/options/blanks/
    numeric/draw/hint/reply 及提交处理）提取为 `usePreviewAnswering`，整卷任务轮询与汇总提取为
    `useFullPaperJob`；组件保留组合与布局，延续既有 useTextbookImport/usePaperPublication 的模式。
  - 触发条件：任一文件需要行为改动且改动落在上述边界内时，先拆后改；纯行数下降不作为目标。

## 推荐 PR 顺序

历史正确性修复、`RunSnapshot`、事件模型、可恢复后台任务和整卷任务汇总已经完成；新的 PR 顺序为：

`test/offline-ai-evaluation` → `feature/badcase-replay-loop` → `feat/ocr-preflight-report` →
`feat/model-capability-registry` → `test/postgres-integration` → `chore/public-test-hardening`。

前两项种子数据直接复用本轮已核实的真实坏样本（见“当前执行队列”），不必重新采集；建成后再回头评估
T0 里剩下的几条开放项（题号识别子问题 A——子问题 B 已随 `fix/reconstruct-line-breaks-from-mineru-layout`
修复、图片版面纯位置场景、`MathText` 讲解通道、`subQuestions`）分别值不值得单独立项，用语料里的
真实占比决定顺序，不要凭感觉排期。

每个 PR 只处理一个主题，必须有测试、文档和回滚边界；不要把新模型接入、UI 重构和数据库迁移混在一起。
