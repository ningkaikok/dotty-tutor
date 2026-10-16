# Windows 文件写入与运行时边界核验

更新日期：2026-10-15。本页说明 Windows 本地核验的适用条件，不改变教材清理所需的精确 manifest、环境指纹和人工确认。

## Manifest 与恢复快照

写文件仍通过同目录临时文件、内容同步和不覆盖的硬链接发布。内容相同可以重复写入；内容不同拒绝覆盖并清理临时文件。权限设置失败时同样关闭临时文件句柄，避免 Windows 无法删除文件而遮蔽原错误。

POSIX 保留 `0600` 权限和目录 `fsync`。Windows 不支持通过 `os.open` 打开目录，不执行该项额外同步；文件内容仍执行 `fsync`。Windows 的访问权限取决于输出目录继承的 ACL，不能把 `chmod(0600)` 当成用户独占访问保证。包含环境与来源指纹的文件应写入仅本人可访问的目录；此改动不添加或修改 Windows ACL。

## 原页预览和模型对比

原页预览验收继续使用现有 Poppler `pdftoppm`，不以 OCR 文本或模拟图片替代真实 PDF 渲染。运行前需让其所在目录进入 `PATH`。替换原 PDF 后，历史修订预览必须拒绝读取。

目录逃逸测试需要创建符号链接。仅遇到 Windows `WinError 1314`（当前用户没有创建符号链接权限）时明确跳过；其他错误仍使测试失败。可在启用开发者模式或具有该权限的环境补跑。该跳过不关闭服务端路径边界校验，也不能视为此安全场景已在本机通过。

质量比较验收只替换真实 Runtime 的模型发现与生成边界，固定可用目录和响应，不依赖本机安装 Codex、Ollama 或 API 密钥，不调用付费模型。生产仍按真实发现结果拒绝不可用模型，回归测试验证拒绝后不会创建运行记录。

定向命令在 `apps/api` 下执行，预先配置独立测试库 `DOTTY_TEST_POSTGRES_ADMIN_URL` 和 Poppler `PATH`：

```powershell
uv run ruff check application/services/legacy_textbook_cleanup.py tests/test_legacy_textbook_cleanup_postgres.py tests/test_chapter_source_preview.py tests/test_quality_review_runs_acceptance.py
uv run python -m tests.postgres_test_runner tests.test_legacy_textbook_cleanup_postgres tests.test_chapter_source_preview tests.test_quality_review_runs_acceptance
uv run pyright
```

对应用户变化说明：教材制作失败后可从原任务或问题页继续；修改页面后不会继续使用旧 AI 初审结论；Windows 下旧教材清理的 manifest/恢复快照写入不再因缺少 `os.fchmod` 或目录同步能力而中断。
