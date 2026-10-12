import { Card, Notice, Skeleton } from "../../components/ui";
import { MediaStudioPanel } from "../media/MediaStudioPanel";
import { useCoursewareMediaContext } from "./useCoursewareMediaContext";

export function CoursewareMediaPanel({ coursewareId, publicationId }: { coursewareId: string; publicationId: string }) {
  const { snapshot, selected, error, toggle } = useCoursewareMediaContext(coursewareId, publicationId);
  if (error) return <Notice tone="danger">发布课件媒体来源读取失败：{error}</Notice>;
  if (!snapshot) return <Skeleton label="正在读取发布课件媒体来源…" rows={1} />;
  return <Card className="courseware-media-panel">
    <h2>课件教学视频</h2><p>从这个已发布版本选择 3–8 页公开讲解，渲染后仍需单独审校和发布。活动答案与教师备注不进入旁白。</p>
    <fieldset><legend>视频讲解页面</legend>{(snapshot.document.pages ?? []).map((page) => <label key={page.pageId}>
      <input type="checkbox" checked={selected.includes(page.pageId)} onChange={() => toggle(page.pageId)} /> {page.title}
    </label>)}</fieldset>
    <MediaStudioPanel chapterId={snapshot.document.chapterId} publicationId={publicationId}
      lessons={[{ lessonId: `courseware:${coursewareId}`, title: snapshot.document.title, sourceRevisionId: snapshot.document.sourceRevisionId }]}
      courseware={{ coursewareId, pageIds: selected, sourceRevisionId: snapshot.document.sourceRevisionId }} />
  </Card>;
}
