import { Link } from "react-router";
import { MaterialsHeader } from "../materials/MaterialsHeader";
import { useAutomaticCourses } from "./useAutomaticCourses";

export function AutomaticCourseCreator({ uploadId }: { uploadId: string }) {
  const { job, error, retry, cancel } = useAutomaticCourses(uploadId);
  const active = !job || job.status === "queued" || job.status === "running";
  const failure = error || (job?.status === "failed" ? job.lastError?.message || "课程制作失败" : job?.status === "cancelled" ? "课程制作已取消" : "");
  return <main className="app-shell materials-shell automatic-course-shell">
    <MaterialsHeader backTo="/studio" backLabel="我的教材" subtitle="自动制作课程" />
    <section className="materials-heading"><div><span className="eyebrow">教材课程</span><h1>自动制作教材教程</h1><p>一本教材组织为一个讲解教程，自动识别目录、学科与页码，当前最多处理前 5 章。草稿生成后，核对内容与来源许可再发布。</p></div></section>
    {active && !error && <section className="chapter-panel automatic-course-task" role="status"><div><span className="material-kind">{job?.status === "running" ? "制作中" : "准备中"}</span><h2>正在制作课程</h2><p>{job?.message || "正在识别教材章节…"}</p>{job?.status === "queued" && <p>任务正在排队，开始后会显示读取页码。</p>}{job?.status === "running" && <progress aria-label="课程准备进度" value={job.progress ?? 0} max={100} />}</div>{job && <button type="button" onClick={() => void cancel()}>取消制作</button>}</section>}
    {failure && <div className="chapter-error" role="alert"><span>{failure}</span><button type="button" onClick={() => void retry()}>重试制作</button></div>}
    {job?.status === "succeeded" && job.result && <section className="chapter-panel"><h2>已安排 {job.result.chapters.length} 章课程草稿</h2><p>目录内各章生成学习目标、讲解、示例与总结，不自动出题。可以逐章复核内容或重试。</p>{job.result.notices.map((notice) => <p className="chapter-warning" key={notice}>{notice}</p>)}<ol className="automatic-course-list">{job.result.chapters.map((chapter) => <li key={chapter.chapterId}><Link to={`/studio/chapters/${chapter.chapterId}`} aria-label={`${chapter.title} · 第 ${chapter.pageStart}–${chapter.pageEnd} 页 →`}><strong>{chapter.title}</strong><span>第 {chapter.pageStart}–{chapter.pageEnd} 页 →</span></Link></li>)}</ol></section>}
  </main>;
}
