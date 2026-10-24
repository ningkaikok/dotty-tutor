import { Link } from "react-router";
import { WHOLE_BOOK_LIMITS, type WholeBookRunView } from "../../api/wholeBook";

const runStatusLabel: Record<WholeBookRunView["status"], string> = {
  queued: "等待开始",
  running: "制作中",
  waiting_for_source: "等待原件恢复",
  paused: "已暂停",
  needs_attention: "需要检查",
  source_changed: "来源已变化",
  completed: "整本完成",
};

const batchStatusLabel: Record<WholeBookRunView["batches"][number]["status"], string> = {
  queued: "等待上一批完成",
  source_preparation: "准备教材原页",
  planning: "规划教学内容",
  generating: "生成课件页面",
  checking: "自动检查",
  repairing: "自动修复",
  published: "已发布",
  completed: "非教学页已记录",
  needs_attention: "需要检查",
  failed: "制作失败",
  source_changed: "来源已变化",
};

const skippedPageReason: Record<string, string> = {
  cover_or_title: "封面或标题页",
  table_of_contents: "目录页",
  copyright_or_legal: "版权或法律说明",
  blank_page: "空白页",
  index_or_glossary: "索引或术语表",
};

export function WholeBookProgressPanel({
  run,
  loading,
  busy,
  error,
  onPause,
  onResume,
  onRefresh,
}: {
  run: WholeBookRunView | null;
  loading: boolean;
  busy: boolean;
  error: string;
  onPause: () => Promise<void>;
  onResume: () => Promise<void>;
  onRefresh: () => Promise<unknown>;
}) {
  if (!run && !loading && !error) return null;
  if (!run) return <section className="whole-book-progress" aria-label="整本自动制作进度">
    {loading && <p role="status">正在读取整本自动制作进度…</p>}
    {error && <p role="alert">整本自动制作进度暂时无法读取：{error}</p>}
  </section>;

  const percent = Math.round(run.completedPages / run.totalPages * 100);
  return <section className="whole-book-progress" aria-label="整本自动制作进度">
    <header className="whole-book-progress-heading">
      <div><span className="courseware-eyebrow">整本自动制作 · 可暂停与继续</span><h2>{run.filename}</h2><p>{run.message}</p></div>
      <strong className={`whole-book-status whole-book-status-${run.status}`}>{runStatusLabel[run.status]}</strong>
    </header>
    <div className="whole-book-progress-summary">
      <progress aria-label="整本制作进度" max={run.totalPages} value={run.completedPages} />
      <span>已完成 {run.completedPages} / {run.totalPages} 个原 PDF 物理页 · {run.completedBatches} / {run.batchCount} 批 · {percent}%</span>
    </div>
    {error && <p role="alert">进度刷新失败：{error}；已显示最近一次保存的状态。</p>}
    <div className="whole-book-progress-actions">
      {run.canPause && <button type="button" disabled={busy} onClick={() => void onPause()}>{busy ? "正在保存…" : "暂停整本制作"}</button>}
      {run.canResume && <button type="button" className="is-primary" disabled={busy} onClick={() => void onResume()}>{busy ? "正在继续…" : "继续整本制作"}</button>}
      <button type="button" disabled={busy || loading} onClick={() => void onRefresh()}>重新读取进度</button>
    </div>
    <ol className="whole-book-batch-list" aria-label="整本制作批次">
      {run.batches.map((batch) => <li key={batch.batchIndex}>
        <div className="whole-book-batch-heading"><strong>第 {batch.batchIndex + 1} 批 · 原 PDF 第 {batch.pageStart}–{batch.pageEnd} 页</strong><span>{batchStatusLabel[batch.status]}</span></div>
        <p>{batch.title}</p>
        {batch.message && <small>{batch.message}</small>}
        {batch.skippedPages.length > 0 && <details><summary>查看已核验的非教学页（{batch.skippedPages.length}）</summary><ul>{batch.skippedPages.map((page) => <li key={page.page}>第 {page.page} 页 · {skippedPageReason[page.reason ?? ""] ?? "已记录"}：{page.note ?? ""}</li>)}</ul></details>}
        {batch.status === "published" && batch.publicationIds.map((publicationId, index) => {
          const coursewareId = batch.coursewareIds[index];
          if (!coursewareId) return null;
          return <Link className="whole-book-courseware-link" key={publicationId} to={`/teacher/courseware/${encodeURIComponent(coursewareId)}?publicationId=${encodeURIComponent(publicationId)}`}>打开已发布课件 {index + 1} →</Link>;
        })}
        {batch.error && <p className="whole-book-batch-error" role="status">{batch.error.message}</p>}
      </li>)}
    </ol>
    <p className="whole-book-limit-note">整本最多 {WHOLE_BOOK_LIMITS.maxPages} 个物理页，按每批最多 {WHOLE_BOOK_LIMITS.pagesPerBatch} 页分成不超过 {WHOLE_BOOK_LIMITS.maxBatches} 批；每批最多生成 {WHOLE_BOOK_LIMITS.maxGeneratedPagesPerBatch} 张课件页。制作完成的课时可先使用，异常批次会保留原因供检查。</p>
  </section>;
}
