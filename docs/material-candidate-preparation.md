# 教材候选材料准备

命令只读本机 PDF，用既有 pypdf 文字层、来源句子定位与 ChapterCreate 协议准备最多 80 个连续原页。
不连接应用数据库、不调用模型/OCR、不生成教师或双审批准。来源类别始终 public，counted=false，分发范围 private。

在 `apps/api` 执行：

```bash
uv run python -m evaluation.chapter.material_candidates \
  /absolute/path/source.pdf /absolute/path/new-candidate.json \
  --subject science --title '待核对科学教材页段' \
  --source-url 'https://publisher.example/book' --source-version 'edition-1' \
  --license '来源文件的实际许可' --attribution '实际作者与署名' \
  --page-start 7 --page-end 10
```

输出必须是新文件；同名候选、原 PDF 或符号链接目标均不会被覆盖。
提供许可字段不是许可审核：操作者应先核对发布者、文件中的授权、图片各自署名和用途。
`--input-kind paper_scan` 仅记录操作者声明，不代表已执行扫描 OCR；默认 digital_pdf。

候选包括原文件绝对路径/SHA-256、总页数、来源快照哈希、chapterRequest、逐页原始文本与警告。
空文本、疑似词语粘连或损坏公式标为 unreadable，保留原结果，后续必须核对原图再修订来源。
上传原 PDF 后需将候选绑定实际受管 uploadId；未绑定前不是可发布课程，不能跳过系统里的来源和规划审核。

## 本轮科学候选

真实来源为 [Wikibooks《Cell Biology》PDF 文件页](https://commons.wikimedia.org/wiki/File:Cell_Biology.pdf)，
2013-12-29 版本，共 97 页，文件页标注 CC BY-SA 3.0。原 PDF 保留贡献者和图像许可清单；引用具体图像时仍需核对对应条目。
本轮选择物理原页 7–10（印刷页码 3–6），保存原 PDF、原页 PNG 与待审候选到 output 的忽略目录。
文字层出现词语粘连，四页全部标为待核对，不自动生成教学成品；旧版资料只用于工程候选和来源验证，不代表已适配当前课标。
用户本轮选择只做工程与候选准备，教师审批、真实扫描和模型生成均未执行。
