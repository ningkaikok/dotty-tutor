# 教材主线工程与候选准备验收

日期：2026-10-13；基线 main / release/v0.38.0 `d93c6f7a`。用户选择“暂时只做工程与候选材料准备”。
执行按[开发计划](roadmap-development-execution-plan.md)开展；三个既有子代理分工实现，主代理检查边界、修复与集成验收。
前端代理最后因用量限制中断，主代理接续完成规划 30 页限制、表单视觉与完整回归。实现与本地验收后先留在工作区；随后用户授权提交、推送和合并，进入 `feature/courseware-completion-readiness` → main PR 交付流程。

## 本轮交付

| 批次 | 实际结果 | 保留边界 |
| --- | --- | --- |
| 路线图与运行盘点 | 更新过期视觉/媒体/线上迁移状态；本机 8010 实际 OpenAPI 0.37.0、健康 PostgreSQL/current；复核收件箱 18 pending、0 reviewed、6 unlinked | 没有切换原本机运行代码或写入其数据库 |
| 完整单元补齐准备 | 新增教师只读 completion 接口与覆盖面板建议；按连续、可用、未规划、未生成原页分批；已有规划、任务、待审和待发布分开显示 | 不创建审批/发布或真实模型任务；当前单章节仍只有一个规划，已有规划不被建议替换，多规划/完整单元成品并未交付 |
| 人工评测准备 | readiness 复用原入集和覆盖校验，安全原因码不输出学生文本/身份；实际清单 50 synthetic、0 有效人工金标准、缺口 50 | 退出 1 表示门槛未满足；无模型调用、无正式排名、无新增人工批准 |
| 扫描与来源质量准备 | 通用教材候选命令保留 PDF 与来源哈希、原始文字层和待核对警告；空文字层/词语粘连/公式风险不得自动通过 | 没有真实纸质扫描；没有新 OCR、TTS 或真实模型运行 |
| 第三学科候选 | 实际下载 Wikibooks Cell Biology 97 页 PDF，准备物理页 7–10、原页 PNG、source-pinned ChapterCreate 候选；四页提取文本均有粘连警告 | 真实公开数字资料，旧版 2013-12-29；不是合成教材，也不是纸质扫描或教学成品；保持 private/pending/count=false，未导入原库 |
| 技术实验 | 按来源检索→互动数学→媒体缓存→本地提示/Prefix→知识点树→模型/安全实验写明输入、依赖和失败判据 | 本轮未实施这些实验，计划不计为功能完成 |

科学候选的来源与许可详见[说明](material-candidate-preparation.md)；文件页标注 CC BY-SA 3.0，原 PDF 保留贡献者和逐图许可。
未把旧版资料的教学表述当成已核验科学事实；原页视觉可读，文字层质量仍须人工校对。
英语/代数/几何的原 18 来源案例和 3 课件草稿没有重新审批或重新生成，完整单元仍待后续内容工作。

## 独立审查与修正

1. 存储含 generationJobId、服务却未装配 Jobs 的恢复/离线场景不能崩溃；回退已保存 generationStatus，保持只读。
2. 已删除草稿即使旧行没有 deleted_at，也不能继续计入生成覆盖或阻止补齐。
3. 同一个 caseId 同时出现在 human/public/synthetic 中时，全文件原 validator 统一去重；冲突人工案例不计数，50 条加一条同 ID public 会降为 49。
4. 规划 GET 返回 503 不能被当作 404；读取失败/未完成时不提供新建规划入口，重试后再明确判定。
5. 建议读取之后来源修订，旧章节 expectedRecordVersion 无法创建新规划；来源文件哈希变化、章节撤回、学生读教师接口均有拒绝回归。
6. 375px 页面无横向溢出，选页后焦点进入起始页；页码控件采用共享 TextField。31 页请求在客户端说明 30 页上限，不触发模型任务。

## 实际执行的门禁

| 命令 | 结果 |
| --- | --- |
| apps/api: uv run ruff check . | 通过 |
| apps/api: uv run pyright | 0 errors / 0 warnings；候选 DTO 类型问题已修正后重跑 |
| apps/api: uv run python -m unittest discover -s tests -p 'test_*.py' | 显式创建/迁移的一次性 PostgreSQL，868 项通过，133.679s |
| apps/api: uv run python -m unittest tests.test_material_candidates tests.test_benchmark_readiness | 最终候选解析/类型修正后 11 项通过 |
| apps/web: pnpm lint | 通过 |
| apps/web: pnpm vitest run | 52 文件、196 项通过 |
| apps/web: pnpm generate:api / pnpm check:api | 生成类型已由脚本更新；检查无漂移 |
| apps/web: pnpm exec tsc --noEmit / pnpm run build | 通过 |
| apps/web: DOTTY_WEB_PORT=59449 pnpm run test:e2e | 37 项通过，58.1s；独立端口、API fixture，未连接原 8010 |
| python3 scripts/check_test_discipline.py / git diff --check | 115 文件无违规；差异检查通过 |
| pdftoppm 原 PDF 物理页 7–10 → PNG | 四页实际查看，原页清晰；没有据此宣称文字层或教学质量通过 |

完整 unittest 末尾两处 psycopg 连接 ResourceWarning 不影响退出码，与此前回归表现一致。
未改 Docker/部署配置，本地验收不执行 Docker up/down，避免影响原运行容器。随后交付 PR 重跑仓库完整 CI，远程结果以该 PR 检查为准；上述本地结果不代替 CI 或线上验证。

## 本机可检查产物

- `output/roadmap-execution/science-cell-biology-candidate.json`：包含原文的待核对候选，0600，Git 忽略。
- `output/pdf/science-candidate/Cell_Biology.pdf` 和 `page-07.png` 至 `page-10.png`：原 PDF 与实际原页预览，Git 忽略。
- `output/roadmap-execution/benchmark-readiness.json`：安全计数及缺口报告。
- `output/roadmap-execution/local-readonly-inventory.json`：不含教材原文或学生信息的运行盘点。
- `output/roadmap-execution/screenshots/unit-completion-narrow.png`：实际 fixture 浏览器窄屏预览，已独立查看。

PR 的固定测试数据窄屏截图保存在 [docs/assets/courseware-completion-narrow.png](assets/courseware-completion-narrow.png)，不含真实教材或学生数据。

当前实现只负责候选与工程准备。教师逐页/整单元审批、真实扫描质量、真人视频/语音听评、第三学科教学成品、50+ 双审和正式横评继续待执行；不以工程测试替代这些验收。

## 产品规划补充（2026-10-13）

按用户要求加入 PP1–PP7 产品任务与 M1–M4 里程碑，详见[产品路线图](product-roadmap.md#当前产品交付规划2026-10-13)。
执行计划只维护产品到工程批次的对应，路线图索引同步入口。加入产品规划不代表增量生成、单元目录、学生衔接、内容导出或真人试用已实现。
本次补充只修改规划文档，未改变上述代码、材料、运行数据和“仅工程与候选准备”范围；验证本地链接、锚点与差异格式，不重复运行应用门禁。
