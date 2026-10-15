import { useEffect, useState } from "react";
import { Link } from "react-router";
import { listCoursewareUnits, loadChapterCoursewareUnit, loadCoursewareUnit, type CoursewareReadingPosition, type CoursewareUnit } from "../../api/coursewareUnit";
import { useLearnerId } from "../../api/identity";
import "./courseware.css";

export function coursewareLessonUrl(coursewareId: string, publicationId: string, role: "student" | "teacher", pageId?: string) {
  const query = new URLSearchParams({ publicationId });
  if (pageId) query.set("pageId", pageId);
  return `/${role === "student" ? "learn" : "teacher"}/courseware/${encodeURIComponent(coursewareId)}?${query}`;
}

function ResumeLink({ position, role }: { position: CoursewareReadingPosition; role: "student" | "teacher" }) {
  return position.available
    ? <Link to={coursewareLessonUrl(position.coursewareId, position.publicationId, role, position.pageId)}>继续上次学习</Link>
    : <span role="status">上次发布版本已不可用，请打开当前课时。</span>;
}

/** One chapter is one unit; published versions keep their own response evidence. */
export function CoursewareUnitNavigation({ unit, role = "student", currentCoursewareId }: {
  unit: CoursewareUnit; role?: "student" | "teacher"; currentCoursewareId?: string;
}) {
  return <section className="courseware-unit" aria-label="单元课时目录">
    <h2>{unit.title} · 单元目录</h2>
    <p>阅读位置用于继续学习；是否掌握以作答与复习证据为准。</p>
    {unit.resume && role === "student" && <ResumeLink position={unit.resume} role={role} />}
    <ol>{unit.lessons.map((lesson) => <li key={lesson.coursewareId} aria-current={lesson.coursewareId === currentCoursewareId ? "step" : undefined}>
      <strong>第 {lesson.order} 课时 · {lesson.title}</strong>
      <span>原页 {lesson.pageStart}–{lesson.pageEnd} · {lesson.durationMinutes} 分钟</span>
      {lesson.publicationId ? <Link to={coursewareLessonUrl(lesson.coursewareId, lesson.publicationId, role)}>打开第 {lesson.publicationVersion} 版</Link>
        : <Link to={`/studio/courseware/${encodeURIComponent(lesson.coursewareId)}`}>审核草稿 · {lesson.status}</Link>}
      {!lesson.currentBatch && <span>历史批次</span>}
      {role === "student" && <span>{lesson.needsCorrectionCount ? `${lesson.needsCorrectionCount} 个活动待订正`
        : lesson.answeredActivityCount ? `已作答 ${lesson.answeredActivityCount}/${lesson.activityCount} 个活动` : "活动尚未作答"}</span>}
      {role === "student" && lesson.progress && <ResumeLink position={lesson.progress} role={role} />}
    </li>)}</ol>
  </section>;
}

/** This task entry reads server history, so a second device can resume a fixed publication. */
export function CoursewareUnitLibrary() {
  const learnerId = useLearnerId();
  const [state, setState] = useState<{ learnerId: string; units: CoursewareUnit[]; error: string } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    listCoursewareUnits(learnerId, controller.signal).then((value) => {
      if (!controller.signal.aborted) setState({ learnerId, units: value.items, error: "" });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ learnerId, units: [], error: error instanceof Error ? error.message : "单元目录暂不可用" });
    });
    return () => controller.abort();
  }, [learnerId]);
  if (state?.learnerId !== learnerId) return <p role="status">正在读取单元学习记录…</p>;
  if (state.error) return <p role="status">单元目录暂不可用：{state.error}</p>;
  if (!state.units.length) return null;
  return <section aria-label="单元学习"><h2>单元学习与继续</h2>{state.units.map((unit) => <CoursewareUnitNavigation key={unit.chapterId} unit={unit} />)}</section>;
}

export function CoursewareTeacherUnit({ chapterId, coursewareId }: { chapterId?: string; coursewareId?: string }) {
  const [unit, setUnit] = useState<CoursewareUnit | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setUnit(null);
    const request = chapterId ? loadChapterCoursewareUnit(chapterId, controller.signal) : loadCoursewareUnit(coursewareId!, "teacher", "", controller.signal);
    request.then((value) => {
      if (!controller.signal.aborted) setUnit(value);
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "单元目录暂不可用");
    });
    return () => controller.abort();
  }, [chapterId, coursewareId]);
  return <main className="courseware-route-state"><Link to={coursewareId ? `/studio/courseware/${encodeURIComponent(coursewareId)}` : `/studio/courseware?chapterId=${encodeURIComponent(chapterId ?? "")}`}>返回教材制作</Link>
    {error && <p role="alert">{error}</p>}{unit ? <CoursewareUnitNavigation unit={unit} role="teacher" /> : !error && <p role="status">正在读取单元目录…</p>}
  </main>;
}
