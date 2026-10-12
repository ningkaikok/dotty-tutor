# 教材课件设计验收

日期：2026-10-12。本轮由主代理在本工作区实际浏览器中检查；内容与工程证据见
[独立验收记录](textbook-courseware-acceptance.md)。用户提供的两张 Night Sky 截图是视觉与授课交互参考，截图中的教学文字不是本轮教材事实来源。

## 参考与实际结果

| 检查项 | 参考目标 | 实际检查结果 |
| --- | --- | --- |
| 封面 | 深色图文双栏、教学主题与课堂路径 | 英语真实试点采用深色左文右图、教学目标与环节；图来自选定教材原页 |
| 授课导航 | Deck/Rail、页码、环节和翻页 | 两种布局、跳页及环节导航可用；桌面工具栏、课堂路径和翻页保持可见 |
| 课堂活动 | 先写、检查表、讨论和答案揭晓 | 输入、检查表和开放作答可用；封面讨论真实提交返回待复核；教师揭晓与学生状态隔离 |
| 教师工具 | 讲稿、计时、全屏、对比度 | 播放器提供对应控制；实际验证讲稿、逐步揭晓、倒计时与刷新恢复，组件/E2E覆盖状态行为 |
| 长页 | 正文与署名不重叠，仍能翻页 | 修复封面交互未接入与长页署名重叠；正文内部滚动，署名位于正文之后 |
| 数学 | 公式、步骤和原图可读 | 代数和几何真实课件 KaTeX 无错误，几何原页图片加载成功；补全底高垂直条件与跨页引用 |
| 窄屏 | 输入与导航可用、无水平溢出 | 390×844 视口检查几何课件；有效内容宽 375 像素，图片加载，署名位于正文之后 |

英语来源是 Writing Guide with Handbook 的修辞阅读开篇，实际教学内容围绕 originator、audience、context，不能机械改写成参考截图的天文词汇课。
当前图片为受控教材整页预览，保留来源和署名；未实现 OCR 区域裁图或生成主题插画，因此与参考的插画视觉密度不同，不报告像素级复刻。

## 本地截图

截图与教材、模型输出一并保存在忽略目录，不纳入 Git。下列链接仅在本验收工作区可用；其他 checkout 应按验收记录重新运行。

- [英语封面，2038×1064](../output/courseware-acceptance/screenshots/real-english-cover-full.jpg)
- [英语 Rail 概念页，2038×1064](../output/courseware-acceptance/screenshots/real-english-rail-full.jpg)
- [代数公式与步骤](../output/courseware-acceptance/screenshots/real-math-algebra.jpg)
- [几何公式与原图](../output/courseware-acceptance/screenshots/real-math-geometry.jpg)
- [几何窄屏完整页面](../output/courseware-acceptance/screenshots/real-math-mobile.jpg)
- [英语学生真实提交后的待复核反馈](../output/courseware-acceptance/screenshots/real-english-student-attempt.jpg)

桌面完整英语截图已实际打开核对；代数、几何与学生提交截图采用窗口截图，不能用其裁切边界推断完整页面宽度。窄屏截图为完整滚动页面。

## 验收范围

本地设计与工程验收通过，完整浏览器门禁 25 项通过。真实内容由主代理对照原页复核并修正后发布到隔离本地课堂，尚未完成认证教师审核、双审金标准或实际课堂教学效果验证。
原业务服务尚未切换到本轮代码，以上截图属于端口 59262/59261 的验收环境；不能据此推断原环境已更新或旧数据已经撤回。
