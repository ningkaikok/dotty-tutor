import { useCallback, useEffect, useRef, useState } from "react";
import { loadCoursewareUnit, saveCoursewarePosition, type CoursewareUnit } from "../../api/coursewareUnit";
import { useLearnerId } from "../../api/identity";

/** Serialize writes with optimistic versions; a stale device must reload instead of overwriting. */
export function useCoursewareUnitProgress(coursewareId: string, publicationId: string | undefined, role: "teacher" | "student") {
  const learnerId = useLearnerId();
  const scope = `${learnerId}:${coursewareId}:${publicationId}:${role}`;
  const [state, setState] = useState<{ scope: string; unit: CoursewareUnit | null; ready: boolean; error: string }>({ scope, unit: null, ready: false, error: "" });
  const writes = useRef<{ scope: string; version: number; lastPage: string; chain: Promise<void>; blocked: boolean }>({ scope, version: 0, lastPage: "", chain: Promise.resolve(), blocked: false });

  useEffect(() => {
    const controller = new AbortController();
    loadCoursewareUnit(coursewareId, role, learnerId, controller.signal).then((unit) => {
      if (controller.signal.aborted) return;
      const position = unit.lessons.find((lesson) => lesson.coursewareId === coursewareId)?.progress;
      writes.current = { scope, version: position?.version ?? 0,
        lastPage: position && position.publicationId === publicationId ? position.pageId : "", chain: Promise.resolve(), blocked: false };
      setState({ scope, unit, ready: true, error: "" });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ scope, unit: null, ready: true,
        error: error instanceof Error ? error.message : "学习位置暂时无法同步" });
    });
    return () => { controller.abort(); };
  }, [scope, coursewareId, publicationId, role, learnerId]);

  const onPageChange = useCallback((pageId: string) => {
    if (role !== "student" || !publicationId || writes.current.scope !== scope) return;
    const queue = writes.current;
    if (queue.blocked || queue.lastPage === pageId) return;
    queue.lastPage = pageId;
    queue.chain = queue.chain.then(async () => {
      if (queue.blocked || writes.current !== queue) return;
      try {
        const result = await saveCoursewarePosition(coursewareId, { publicationId, pageId,
          expectedVersion: queue.version, commandId: crypto.randomUUID() }, learnerId);
        queue.version = result.version;
      } catch (error) {
        queue.blocked = true;
        if (writes.current === queue) setState((previous) => ({ ...previous,
          error: `学习位置未同步：${error instanceof Error ? error.message : "网络暂不可用"}。请刷新目录后继续。` }));
      }
    });
  }, [coursewareId, learnerId, publicationId, role, scope]);
  const active = state.scope === scope;
  const position = active ? state.unit?.lessons.find((lesson) => lesson.coursewareId === coursewareId)?.progress : undefined;
  return { unit: active ? state.unit : null, ready: active && state.ready, error: active ? state.error : "",
    initialPageId: position?.available && position.publicationId === publicationId ? position.pageId : undefined, onPageChange };
}
