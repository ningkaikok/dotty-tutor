import { Link } from "react-router";
import { useAutomaticCourses } from "./useAutomaticCourses";

export function AutomaticCourseCreator({ uploadId }: { uploadId: string }) {
  const { job, error, retry, cancel } = useAutomaticCourses(uploadId);
  const active = !job || job.status === "queued" || job.status === "running";
  const failure = error || (job?.status === "failed" ? job.lastError?.message || "课程制作失败" : job?.status === "cancelled" ? "课程制作已取消" : "");
  return <main className="chapter-shell">
    <header className="chapter-topbar"><Link to="/studio">← 我的教材</Link><strong>Dotty · 自动制作课程</strong></header>
    <section className="chapter-page-heading"><h1>自动制作教材课程</h1><p>自动识别章节、学科与页码，最多制作前 5 章。草稿生成后，核对内容与来源许可再发布。</p></section>
    {active && !error && <section className="chapter-panel" role="status"><p>{job?.message || "正在识别教材章节…"}</p>{job && <button type="button" onClick={() => void cancel()}>取消制作</button>}</section>}
    {failure && <div className="chapter-error" role="alert"><span>{failure}</span><button type="button" onClick={() => void retry()}>重试制作</button></div>}
    {job?.status === "succeeded" && job.result && <section className="chapter-panel"><h2>已安排 {job.result.chapters.length} 章课程草稿</h2><p>每章独立生成，可以进入课程查看进度、复核内容或重试。</p>{job.result.notices.map((notice) => <p className="chapter-warning" key={notice}>{notice}</p>)}<ol>{job.result.chapters.map((chapter) => <li key={chapter.chapterId}><Link to={`/studio/chapters/${chapter.chapterId}`}>{chapter.title} · 第 {chapter.pageStart}–{chapter.pageEnd} 页 →</Link></li>)}</ol></section>}
  </main>;
}
