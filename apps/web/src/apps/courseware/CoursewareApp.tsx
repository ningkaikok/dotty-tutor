import { CoursewarePublishedMedia } from "./CoursewarePublishedMedia";
import { CoursewarePageVoice } from "./CoursewarePageVoice";
import { Link, useLocation, useParams, useSearchParams } from "react-router";
import { CoursewarePlayer } from "./CoursewarePlayer";
import { CoursewarePlanWorkspace } from "./CoursewarePlanWorkspace";
import { CoursewareDocumentWorkspace } from "./CoursewareDocumentWorkspace";
import { useCoursewarePresentation } from "./useCoursewarePresentation";
import { CoursewareTeacherUnit, CoursewareUnitNavigation } from "./CoursewareUnitNavigation";
import { useCoursewareUnitProgress } from "./useCoursewareUnitProgress";
import { CoursewareExport } from "./CoursewareExport";
import { SourceSearchPanel } from "./SourceSearchPanel";
import { MaterialsHeader } from "../materials/MaterialsHeader";
import { coursewareSubjectLabel } from "./coursewareWorkspaceView";
import "./courseware.css";

/** Route boundary for source preparation, teacher authoring, and the separate published player. */
export function CoursewareApp() {
  const { coursewareId } = useParams();
  const { pathname } = useLocation();
  const [search] = useSearchParams();
  if (pathname.startsWith("/learn/courseware/") && coursewareId) {
    return <CoursewarePlaybackRoute coursewareId={coursewareId} publicationId={search.get("publicationId") ?? undefined} role="student" />;
  }
  if (pathname.startsWith("/teacher/courseware/") && coursewareId) {
    if (search.get("unit") === "1") return <CoursewareTeacherUnit coursewareId={coursewareId} />;
    return <CoursewarePlaybackRoute coursewareId={coursewareId} publicationId={search.get("publicationId") ?? undefined} role="teacher" />;
  }
  if (coursewareId) return <CoursewareDocumentWorkspace coursewareId={coursewareId} />;
  if (search.get("unit") === "1" && search.get("chapterId")) return <CoursewareTeacherUnit chapterId={search.get("chapterId")!} />;
  return <CoursewarePlanWorkspace />;
}

function CoursewarePlaybackRoute({ coursewareId, publicationId, role }: { coursewareId: string; publicationId?: string; role: "teacher" | "student" }) {
  const { document, error, busyActivity, playbackScope, submitPractice } = useCoursewarePresentation(coursewareId, publicationId, role);
  const progress = useCoursewareUnitProgress(coursewareId, document?.publicationId, role);
  const [search, setSearch] = useSearchParams();
  const header = <MaterialsHeader
    backTo={role === "student" ? "/learn" : `/studio/courseware/${encodeURIComponent(coursewareId)}`}
    backLabel={role === "student" ? "学习空间" : "课件管理"}
    subtitle={role === "student" ? "课件学习" : "教师授课"}
    actions={<>{document && <span className="courseware-playback-save">{coursewareSubjectLabel(document.subject)} · 第 {document.version} 版</span>}{role === "student" && busyActivity && <span className="courseware-playback-save" role="status">正在保存练习…</span>}</>}
  />;

  if (error) return <main className="courseware-route-state app-shell">{header}<h1>{role === "student" ? "学生发布版暂不可用" : "教师授课预览暂不可用"}</h1><p role="alert">{error}</p><Link to={role === "student" ? "/learn" : "/studio"}>返回</Link></main>;
  if (!document || !progress.ready) return <main className="courseware-route-state app-shell">{header}<p role="status">正在安全读取{role === "student" ? "学生发布版" : "教师授课快照"}…</p></main>;
  return <div className="courseware-playback-route">
    {header}
    <div className="courseware-playback-actions">
    <CoursewareExport key={`export:${playbackScope}:${document.publicationId}`} coursewareId={coursewareId} publicationId={document.publicationId} role={role} />
    {progress.error && <p role="status">{progress.error}</p>}
    {progress.unit && <details className="courseware-unit-menu"><summary>单元目录与切换课时</summary><CoursewareUnitNavigation unit={progress.unit} role={role} currentCoursewareId={coursewareId} /></details>}
    </div>
    <CoursewarePlayer key={`player:${playbackScope}:${document.publicationId}:${search.get("pageId") ?? ""}`} document={document} playbackScope={playbackScope} onSubmitActivity={submitPractice} showBrand={false}
      initialPageId={search.get("pageId") ?? progress.initialPageId} onPageChange={role === "student" ? progress.onPageChange : undefined}
      renderPageTools={role === "student" ? (page) => <><SourceSearchPanel key={`source:${playbackScope}:${document.publicationId}`} coursewareId={coursewareId} publicationId={document.publicationId} onOpenPage={(pageId) => { const next = new URLSearchParams(search); next.set("pageId", pageId); setSearch(next); }} /><CoursewarePublishedMedia coursewareId={coursewareId} publicationId={document.publicationId} /><CoursewarePageVoice key={`${document.publicationId}:${page.pageId}`} coursewareId={coursewareId} publicationId={document.publicationId} page={page} /></> : undefined} />
  </div>;
}
