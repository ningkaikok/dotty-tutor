import { CoursewareCoveragePanel } from "./CoursewareCoveragePanel";
import { CoursewareMediaPanel } from "./CoursewareMediaPanel";
import { useNavigate } from "react-router";
import { CoursewareDraftEditor } from "./CoursewareDraftEditor";
import { useCoursewareDocumentWorkflow } from "./useCoursewareDocumentWorkflow";

/** Compose the teacher editor from the version-bound document workflow. */
export function CoursewareDocumentWorkspace({ coursewareId }: { coursewareId: string }) {
  const navigate = useNavigate();
  const { document, loading, busy, error, notice, reload, savePage, regenerate, review, publish } = useCoursewareDocumentWorkflow(coursewareId);
  if (loading) return <main className="courseware-route-state"><p role="status">正在读取教师课件草稿…</p></main>;
  if (!document) return <main className="courseware-route-state"><h1>课件草稿暂不可用</h1><p role="alert">{error || "找不到这份课件。"}</p><button type="button" onClick={() => navigate("/studio/courseware")}>返回教材课件</button></main>;
  return <div className="courseware-authoring-route">
    {error && <p role="alert" className="courseware-editor-error">{error}</p>}
    <CoursewareCoveragePanel chapterId={document.chapterId} revisionKey={document.recordVersion} />
    <CoursewareDraftEditor
      document={document}
      busy={busy}
      notice={notice}
      onSavePage={savePage}
      onRegeneratePage={regenerate}
      onReviewPage={(pageId, decision, note) => review(decision, [pageId], note)}
      onReviewDocument={review}
      onPublish={publish}
      onReload={reload}
      onPresent={() => document.publicationId && navigate(`/teacher/courseware/${encodeURIComponent(coursewareId)}?publicationId=${encodeURIComponent(document.publicationId)}`)}
      onLearn={() => document.publicationId && navigate(`/learn/courseware/${encodeURIComponent(coursewareId)}?publicationId=${encodeURIComponent(document.publicationId)}`)}
    />
    {document.publicationId && <CoursewareMediaPanel coursewareId={coursewareId} publicationId={document.publicationId} />}
  </div>;
}
