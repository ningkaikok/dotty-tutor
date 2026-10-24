import { Link } from "react-router";
import { Badge, Button } from "../../components/ui";
import type { ChapterSummary } from "../../types/chapter";
import type { MaterialCourseware } from "./useMaterials";
import { coursewareListStatus } from "../courseware/coursewareWorkspaceView";

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
    {lessons.map((item) => {
      const status = coursewareListStatus(item);
      const playableDocument = item.status === "published" && Boolean(item.publicationId);
      const tone = status.kind === "published" ? "success" : status.kind === "needs-attention" ? "warning" : status.kind === "making" ? "info" : "neutral";
      return <div key={item.coursewareId}>
        <p>{item.title} · <Badge tone={tone}>{status.label}</Badge></p>
        <Link to={`/studio/courseware/${encodeURIComponent(item.coursewareId)}`} aria-label={`管理课件 · ${item.title}`}>管理课件</Link>
        {playableDocument && <Link className="material-main-action" aria-label={`直接授课 · ${item.title}`} to={`/teacher/courseware/${encodeURIComponent(item.coursewareId)}?publicationId=${encodeURIComponent(item.publicationId!)}`}>直接授课 →</Link>}
      </div>;
    })}
  </section>;
}
