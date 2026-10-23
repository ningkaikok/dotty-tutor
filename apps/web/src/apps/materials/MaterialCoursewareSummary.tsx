import { Link } from "react-router";
import { Button } from "../../components/ui";
import type { ChapterSummary } from "../../types/chapter";
import type { MaterialCourseware } from "./useMaterials";

/** Display actual courseware independently of legacy chapter lesson records. */
export function MaterialCoursewareSummary({ chapters, documents, loading, failed, onReload }: {
  chapters: ChapterSummary[];
  documents: Record<string, MaterialCourseware[]>;
  loading: boolean;
  failed: string[];
  onReload: () => void;
}) {
  const lessons = chapters.flatMap((chapter) => documents[chapter.chapterId] ?? []);
  const playable = lessons.filter((item) => item.status === "published" && item.publicationId);
  const hasFailure = chapters.some((chapter) => failed.includes(chapter.chapterId));
  return <section className="material-courseware-summary" aria-label="互动课件">
    {loading ? <p role="status">正在读取课件…</p> : <>
      {lessons.length > 0 && <p>{lessons.length} 份课件 · {lessons.reduce((sum, item) => sum + item.pageCount, 0)} 页 · {playable.length} 份可授课</p>}
      {hasFailure && <div role="alert"><p>课件列表暂时无法读取，已有课件不会因此丢失。</p><Button onClick={onReload}>重新读取课件</Button></div>}
      {!hasFailure && !lessons.length && <p>还没有制作课件</p>}
    </>}
    {playable.map((item) => <div key={item.coursewareId}><p>{item.title}</p><Link className="material-main-action" aria-label={`打开授课 · ${item.title}`} to={`/teacher/courseware/${encodeURIComponent(item.coursewareId)}?publicationId=${encodeURIComponent(item.publicationId!)}`}>打开授课 →</Link></div>)}
    {lessons.filter((item) => !playable.includes(item)).map((item) => <div key={item.coursewareId}><p>{item.title} · {item.automation?.status === "needs_attention" ? "需要处理" : ["generating", "checking", "repairing"].includes(item.automation?.status ?? "") || ["queued", "running"].includes(item.generationStatus) ? "正在自动制作" : "已有草稿"}</p><Link to={`/studio/courseware/${encodeURIComponent(item.coursewareId)}`}>查看制作进度 →</Link></div>)}
    {!loading && !hasFailure && !lessons.length && chapters[0] && <Link className="material-main-action" to={`/studio/courseware?chapterId=${encodeURIComponent(chapters[0].chapterId)}`}>自动制作课件 →</Link>}
  </section>;
}
