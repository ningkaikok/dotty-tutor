import { useEffect, useState } from "react";
import { loadCoursewarePresentation, type CoursewarePresentation } from "../../api/courseware";

/** Select from the immutable publication, never from the currently edited draft pages. */
export function useCoursewareMediaContext(coursewareId: string, publicationId: string) {
  const [state, setState] = useState<{ key: string; snapshot?: CoursewarePresentation; selected: string[]; error: string }>({ key: "", selected: [], error: "" });
  const key = JSON.stringify([coursewareId, publicationId]);
  useEffect(() => {
    const controller = new AbortController();
    void loadCoursewarePresentation(coursewareId, publicationId, controller.signal).then((snapshot) => {
      if (!controller.signal.aborted) setState({ key, snapshot, selected: (snapshot.document.pages ?? []).slice(0, 3).map((page) => page.pageId), error: "" });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState({ key, selected: [], error: error instanceof Error ? error.message : "无法读取媒体来源" });
    });
    return () => controller.abort();
  }, [coursewareId, publicationId, key]);
  const visible = state.key === key ? state : { selected: [], error: "", snapshot: undefined };
  const toggle = (pageId: string) => setState((current) => current.key !== key ? current : {
    ...current, selected: current.selected.includes(pageId) ? current.selected.filter((id) => id !== pageId) : [...current.selected, pageId],
  });
  return { ...visible, toggle };
}
