# 教材证据检索与图像评测开发记录

记录日期：2026-10-15。本轮完成 A6 的实际图片传入、B3 的发布来源检索与带引用讲解，以及 A4/B5 可离线执行的待审来源校对工具。未调用付费模型，没有把候选材料改成人工金标准，也没有生成或批准真实科学课成品。

## 已交付行为

- 模型对照为现有 50 条合成案例保留配对结果；图片先核对本地路径、文件大小与 SHA-256，再以真实 PNG 字节传入 Runtime。SVG 使用已有 CairoSVG 转换，禁止外部资源、实体及不支持的元素；保留纵横比例与源图/派生 PNG 指纹。Windows Git 转换的 SVG 换行统一为 LF 后计算来源指纹。
- 无视觉能力或缺少本机 Cairo 明确记为跳过；缺图、素材变化及模型调用失败保留失败结果。未知用量与费用保持空值。案例带 `comparisonGroupId`；现有材料没有可靠的正确/损坏转写对，不能自行倒推输入或声称已做三路质量实验。
- 教材检索复用 PostgreSQL FTS 与现有检索表，通过文档类型隔离原题与教材索引，不新增检索服务或依赖。首次查询可重建不可变发布索引，并发写入使用数据库原子 upsert。
- 只索引已审核发布页明确引用的原教材句子，正文来自冻结来源而非生成讲解；不索引未引用页、活动答案或教师备注。返回发布版本、来源修订、物理页、句子定位及经过再次授权的原页预览地址。
- 讲解复用现有模型入口，校验模型引用均来自本次证据；无证据、伪造引用和非对象输出有明确回退，原文仍可查看。引用存在不等于讲解语义一定正确，真实质量仍需人工校准。
- 前端面板支持关键词查原文、依据讲解、回到课件页及打开原页；按学习者和发布版本隔离问题/结果，取消旧请求。403/404 后移除旧证据。原图通过新页面访问，不在面板缓存图片或对象 URL。

## 历史发布与来源修订边界

同一来源修订下的多个发布版本仍可使用固定旧链接；查询始终返回链接指定的发布引用，不能串到新发布。章节保存新的来源修订后，旧修订的检索、讲解及证据预览均拒绝，即使旧索引尚未清理也不会返回旧原文。章节/课件撤回、发布变为私有或原文件指纹变化同样拒绝。现有播放器的历史回放规则不由本模块改变。

检索与原图解析在返回前再次核验发布边界；模型生成期间来源撤回时，不返回已生成的旧讲解。前端不会在已经收到拒绝响应后保留旧原图或原文；已经显示的内容在服务器侧发生变化时，需要后续读取才能获知，未实现推送撤回。

## 离线校对与科学候选

先使用 `evaluation.chapter.material_candidates` 生成候选，再运行：

```bash
cd apps/api
uv run python -m evaluation.chapter.source_review_report candidate.json report.json --transcriptions transcriptions.json
```

转写文件按物理页码提供待审文本，例如 `{"12": "待校对的整页文本"}`。报告保留原始观察、待审转写、差异文本及位置、原文件指纹和页码；不修改 OCR、教材候选或应用库，也不覆盖已有输出。科学候选增加概念、单位、图表及来源活动的核对清单。真实纸质扫描、教材图表和科学内容仍需材料及具备相应能力的人复核。

## 本轮实际验证

后端相关定向测试使用显式的一次性 PostgreSQL admin，以及本机已有的 Poppler。开发进程先把现成的 `pdftoppm` 目录加入 PATH；没有安装依赖或访问业务库。

```bash
uv run python -m unittest tests.test_courseware_search_postgres.CoursewareSearchAcceptanceTests tests.test_tutor_image_evaluation tests.test_source_review_report tests.test_benchmark_infrastructure tests.test_tutor_search
```

36 项通过，覆盖真实 PostgreSQL FTS、真实 PDF 发布与原图预览、来源修订/撤回/私有拒绝、固定旧发布、模型伪造引用与非对象失败、真实 PNG 输入、图片安全边界及待审转写保留。机器缺少 Cairo 原生库；仓库 SVG 实测结果为 `skipped / image_conversion_unavailable`，没有声称本机已成功派生 SVG 图片。

```bash
pnpm vitest run src/apps/courseware/SourceSearchPanel.test.tsx
```

6 项通过，覆盖原文与原页链接、模型不可用、发布/身份切换，以及 403/404 后移除旧证据。自有文件 Ruff、Pyright 与 ESLint 检查已通过。完整前后端和浏览器流程验证由集成阶段统一运行，不能用这些定向结果代替完整交付检查。
