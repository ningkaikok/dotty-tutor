import { Link, useLocation, useParams, useSearchParams } from "react-router";
import { CoursewarePlayer } from "./CoursewarePlayer";
import { CoursewarePlanWorkspace } from "./CoursewarePlanWorkspace";
import { CoursewareDocumentWorkspace } from "./CoursewareDocumentWorkspace";
import { useCoursewarePresentation } from "./useCoursewarePresentation";
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
    return <CoursewarePlaybackRoute coursewareId={coursewareId} publicationId={search.get("publicationId") ?? undefined} role="teacher" />;
  }
  if (coursewareId) return <CoursewareDocumentWorkspace coursewareId={coursewareId} />;
  return <CoursewarePlanWorkspace />;
}

function CoursewarePlaybackRoute({ coursewareId, publicationId, role }: { coursewareId: string; publicationId?: string; role: "teacher" | "student" }) {
  const { document, error, busyActivity, playbackScope, submitPractice } = useCoursewarePresentation(coursewareId, publicationId, role);

  if (error) return <main className="courseware-route-state"><h1>{role === "student" ? "学生发布版暂不可用" : "教师授课预览暂不可用"}</h1><p role="alert">{error}</p><Link to={role === "student" ? "/learn" : "/studio"}>返回</Link></main>;
  if (!document) return <main className="courseware-route-state"><p role="status">正在安全读取{role === "student" ? "学生发布版" : "教师授课快照"}…</p></main>;
  return <div>
    <div className="courseware-player-return"><Link to={role === "student" ? "/learn" : `/studio/courseware/${encodeURIComponent(coursewareId)}`}>{role === "student" ? "返回学习空间" : "返回课件审核"}</Link>{role === "student" && busyActivity && <span role="status">正在保存练习…</span>}</div>
    <CoursewarePlayer document={document} playbackScope={playbackScope} onSubmitActivity={submitPractice} />
  </div>;
}
