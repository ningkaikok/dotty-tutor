import { useCallback, useEffect, useState } from "react";
import { loadCoursewareCompletion, loadCoursewareCoverage, type CoursewareCompletion, type CoursewareCoverage } from "../../api/courseware";

interface CoverageState {
  chapterId: string;
  coverage?: CoursewareCoverage;
  completion?: CoursewareCompletion;
  coverageError?: string;
  completionError?: string;
}
/** Keep source coverage useful when suggestions fail; abort route changes so old responses cannot become actions. */
export function useCoursewareCoverage(chapterId: string, revisionKey?: number, sourceRevisionId?: string) {
  const [state, setState] = useState<CoverageState>({ chapterId: "" });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({ chapterId });
    void loadCoursewareCoverage(chapterId, controller.signal).then((coverage) => {
      if (!controller.signal.aborted) setState((previous) => ({ ...previous, coverage }));
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState((previous) => ({ ...previous, coverageError: error instanceof Error ? error.message : "无法读取单元覆盖" }));
    });
    void loadCoursewareCompletion(chapterId, 5, controller.signal).then((completion) => {
      if (!controller.signal.aborted) setState((previous) => ({ ...previous, completion }));
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setState((previous) => ({ ...previous, completionError: error instanceof Error ? error.message : "无法读取补齐建议" }));
    });
    return () => controller.abort();
  }, [chapterId, revisionKey, sourceRevisionId, retry]);
  const refresh = useCallback(() => setRetry((value) => value + 1), []);
  return { ...(state.chapterId === chapterId ? state : { chapterId }), refresh };
}
