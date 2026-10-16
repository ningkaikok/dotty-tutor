import { useCallback, useEffect, useRef, useState } from "react";
import { loadCoursewareAiReview, startCoursewareAiReview, type CoursewareAiReviewStatus } from "../../api/aiReview";
import { coursewareErrorMessage } from "./coursewareErrors";

/** Load AI pre-review status for one document version and poll while a review job runs. */
export function useCoursewareAiReview(coursewareId: string, recordVersion: number | undefined) {
  const [status, setStatus] = useState<CoursewareAiReviewStatus | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const requestSequence = useRef(0);

  const reload = useCallback(async (signal?: AbortSignal) => {
    const sequence = ++requestSequence.current;
    try {
      const next = await loadCoursewareAiReview(coursewareId, signal);
      if (!signal?.aborted && sequence === requestSequence.current) { setStatus(next); setError(""); }
    } catch (requestError) {
      if (!signal?.aborted && sequence === requestSequence.current) setError(coursewareErrorMessage(requestError, "AI 初审结果暂时无法读取。"));
    }
  }, [coursewareId]);

  // A new document version (edit, review, regeneration) can change routing; re-read it.
  useEffect(() => {
    if (recordVersion === undefined) return;
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, [recordVersion, reload]);

  useEffect(() => {
    if (!status?.jobId) return;
    const timer = window.setTimeout(() => void reload(), 2000);
    return () => window.clearTimeout(timer);
  }, [status, reload]);

  const start = async () => {
    if (recordVersion === undefined) return;
    setStarting(true);
    setError("");
    try {
      await startCoursewareAiReview(coursewareId, recordVersion);
      await reload();
    } catch (requestError) {
      setError(coursewareErrorMessage(requestError, "AI 初审未能启动。"));
    } finally {
      setStarting(false);
    }
  };

  // A document edit invalidates the actionable result immediately, before its
  // replacement request completes. Bulk confirmation must never use old routing.
  const currentStatus = status?.coursewareId === coursewareId && status.recordVersion === recordVersion ? status : null;
  return { status: currentStatus, error, starting, start, reload };
}
